---
name: functional-verification
description: Use this skill when the user asks to plan, build, run, debug, close coverage, or sign off a SystemVerilog or UVM verification environment. It drives the complete DV campaign in the main session and delegates bounded work to the verification builder, reviewer, and runner agents (the runner also owns failure diagnosis).
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - Agent(chip-design-verification:verification-builder, chip-design-verification:verification-reviewer, chip-design-verification:verification-runner)
  - Bash(pwd)
  - Bash(git rev-parse *)
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dv_flow.py" *)
---

# Functional Verification Orchestration

你是工作流的调度者，运行在用户的主 session 中。不要创建 orchestrator 子 agent，只
派发上面列出的四种 worker agent。这样每个 worker 都保持在 subagent depth one，所有
决策在主 session 中可见。

## Session Start — 先停下来问用户

**在询问用户想要做什么之前，不要派发任何 worker，也不要用 `init` 初始化状态。**

1. 检查当前目录下是否存在 `.dv/workflow_state.json`。
2. **如果存在** — 运行 `validate` 和 `show`，总结当前 phase、revision、work item
   进度和 open blocker，然后问用户下一步想做什么（resume / 重跑某个 gate /
   查看某个 task / 重新开始）。
3. **如果不存在** — 不要自己尝试阅读当前目录下有哪些内容。要停下来并直接摆出问题。在这一问题中让用户告知全部所需信息（project root（验证项目根目录，`.dv/` 建在此）、DUT name、spec 路径、RTL filelist、RTL roots（DUT RTL 源码，受保护）、top module、priority order、simulator、clock/reset 信息），确认完毕后再运行 `init`。除非用户同意，否则不要自行假设默认值。
4. 初始化或恢复完成后，**派发第一个 worker task 之前先使用中文征得用户确认**。

## Operating Model

采用一层星型拓扑：

```text
                         verification-builder
                                  ^
                                  |
verification-reviewer <---- main session ----> verification-runner
```

The runner owns both execution and read-only failure diagnosis; the analysis
is embedded in the failing execution result (`payload.diagnosis`), so one
runner task/result covers execution and analysis — not a separate task or agent.

Worker 之间不互相调用或通信。每个 worker 接收一个不可变的 task request，返回一个
JSON result。你负责校验并记录该 result，然后再派发下一个 worker。Worker 的
`recommended_next` 仅供参考，只有你可以决定路由、创建 task、推进 phase。

你独自主管以下事项：

- `.dv/workflow_state.json`
- task 创建、phase 推进、重试预算、审批 gate
- reviewer 发现的 review finding、runner 的运行失败、runner 的诊断结果和 fix request 的路由
- 判断某项证据是否满足 gate 条件

不要自己写 V-plan、testbench、test、assertion 或 coverage model。不要自己做 code
review。不要自己去读冗长的仿真 log。把这些有明确边界的工作派发给对应的 worker。

## 参考文件

以下文件按需阅读，路径相对于本 `SKILL.md`：

- `references/machine-contract.md` — 命令面、枚举、request/result schema、gate 条件的权威查询参考；**查这个，不要读 `dv_flow.py` 源码**
- `references/task-contract.md` — 首次派发 task 或处理任何 failure 路由前
- `references/vplan-template.md` — 创建 V-plan task 前
- `references/plan-tables.md` — 创建 V-plan task 前（testpoint/testlist/covergroups 表格的列模式、`tables.json` 契约与渲染命令）
- `references/workflow-state.schema.json` — 排查 state 校验问题时
- `references/task-request.schema.json` 和 `references/task-result.schema.json` — worker 返回格式异常时

## Durable State

确定 project root 后，执行一次性初始化：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dv_flow.py" init \
  --root <project-root> \
  --dut-name <name> \
  --spec <spec-path> \
  --rtl-filelist <filelist-path> \
  --rtl-root <rtl-source-file-or-directory> \
  --top <top-module>
```

Repeat `--rtl-root` for every protected RTL source file or directory. Run the
command from the verification project root and confirm `pwd` resolves to
`<project-root>` before creating or dispatching a task.

If `.dv/workflow_state.json` already exists, run `validate` and resume it. Never
silently replace an existing run.

All task files live under `.dv/tasks/<task-id>/`. Use the state tool for every
mutation. Do not hand-edit `workflow_state.json`.

Initialization records the specification, RTL filelist, and every protected RTL
root as read-only design inputs and creates a content-addressed
`baseline_revision` over the verification assets inside the project (initially
empty). Specification and RTL paths may live outside the project root and are
excluded from the revision hash. Use `baseline_revision` as `input_revision` for
the first plan-builder task. There is no revision-less dispatch. Builder outputs
extend that baseline into composite revisions; accepted and frozen revisions
always cover every path in their recorded `revision_paths`.

## Task Dispatch Protocol

每次派发 worker 遵循以下步骤：

1. 选择稳定的 lineage 名称，如 `plan-review`、`smoke-run`、`VP-T014-debug`。
2. 用 `new-task` 创建 task。选择合适的 retry kind，以确保持久化和正确的重试预算。
3. 填写生成的 `request.json`。保持 scope 紧凑：提供准确的 `project_root`、非空的
   `input_revision`、完整的 `revision_paths`、带类型的 `inputs`、明确的 read/write
   根路径、可观测的 `acceptance` 条件、有界的 `context`（runner 命令必须包含
   `timeout_s`；**builder 代码任务在 `context` 中带上 `tool`**，目标仿真器/工具名。
   本机无仿真工具时，builder 默认用插件自带的
   `${CLAUDE_PLUGIN_ROOT}/scripts/static_check.py` 做 lint-only 静态语法检查；
   `tool` 用于描述目标语法风格，以及本机可用时补充真实 lint。smoke 用单次
   build-and-run 时，runner 的 `context` 额外带 `build_and_run: true`，命令必须是
   先重建再 simv 的 wrapper）、不可变的 `prior_result_refs`、
   以及自动生成的 `expected_result_path`。不要因为某个 builder 修改了一个 TB 文件
   就去掉 baseline 路径。
4. 运行 `seal-task`。不要派发 draft 状态或校验不通过的 request。
5. 用 `run_in_background: false` 派发一个具名 worker。传入绝对路径的 request 文件，
   提示词为：
   "Read this task request, perform only its scope, and return one JSON object
   matching the task-result contract. Do not use Markdown fences."
6. 将 worker 的 JSON 返回内容原样写入 task 的 `result.json`。
7. 运行 `record-result`。拒绝 stale revision、缺失 artifact、role 不匹配、格式错误
   的 result。
8. 根据记录到的 outcome 进行路由。Worker 无权决定下一 phase，任何 worker 的 result
   都不得直接传给另一个 worker。

Worker 的执行状态与 DV outcome 是独立的。例如：

```text
agent_status = COMPLETED
outcome      = SIMULATION_FAILURE
```

means the runner completed its job correctly and found a failing simulation.
Do not retry it as an agent failure.

## 工作流程

Phase 流转顺序：

```text
INIT -> PLAN -> PREFLIGHT -> SMOKE
```

### 0. 验证计划

向 `verification-builder` 派发 `WRITE_VPLAN`（基于 `baseline_revision`）。Builder
将编写结构化的 Markdown V-plan，包含稳定的 requirement/feature/test ID、TB 架构、
checker/reference-model 策略、assertion、coverage、依赖关系、优先级语义和验收标准。
Builder **在写任何 TB 代码之前**，还要基于 `${CLAUDE_PLUGIN_ROOT}/template/` 的三个
模板（testpoint / testlist / covergroups）生成 `verification/tables/tables.json` 与
三个 `.xlsx`（见 `references/plan-tables.md`），其稳定 ID 与 V-plan 的 Traceability
Matrix 一一对应。模板是外部只读输入，不进 revision；`tables.json` 与 `.xlsx` 是
builder 在 project root 内写出的验证资产，进入 revision。

向 `verification-reviewer` 派发 `REVIEW_VPLAN`（基于 builder 产出的 revision）。
Reviewer 除审查 V-plan 外，还读取 `verification/tables/tables.json`（reviewer 无
Bash，读文本源而非二进制 xlsx）审查三张表格的完整性、ID 唯一性与取值合法性。

- `APPROVED`：记录 reviewer 审批通过，然后**停下来征求用户（人）对 V-plan 的审批**。
- `CHANGES_REQUIRED`：仅将 blocking finding ID 发给 builder 修复，然后重新 review。
- `BLOCKED`：暂停，等待缺失的 specification 或用户输入。

Reviewer `APPROVED` 之后、**派发任何 runner（含 PREFLIGHT）或让 builder 写 TB 代码
之前**，必须先把 V-plan 文档和三个交付表格（`verification/tables/testpoint.xlsx`、
`testlist.xlsx`、`covergroups.xlsx`）交给用户人工审核，并用
`approve --gate VPLAN --decision APPROVED --approved-by <用户> --note "<理由>"
--revision <accepted plan revision>` 记录人工审批。人工审批通过前，
`transition --to PREFLIGHT` 会因缺少 `VPLAN` 人工审批而失败，所以机器会强制你停下。
若用户 `REJECTED`，回到 builder 修改 V-plan 并重新走 `REVIEW_VPLAN`。

若用户在审核中**修改了 `.xlsx` 表格**：把这些改动交给 builder 派发
`APPLY_PLAN_EDITS`（它运行 `render_tables.py extract` 把改动折回 `tables.json` 并
重新渲染三张表格，产出新的 plan revision），然后在新 revision 上重新派发
`REVIEW_VPLAN`、重新征求人工审批，且 `approve --gate VPLAN --revision` 必须绑定
**新的 revision**。直接原地改 `.xlsx` 而不走 `APPLY_PLAN_EDITS`，会使工作区与已
接受 revision 漂移，`transition` 会以 revision-drift 拦截——这正是强制走闭环的
保护。详见 `references/plan-tables.md`「人工审批时的修改闭环」。

审批通过后，提取 reviewer 返回的结构化 `plan_inventory`（包含 priority order、
directed work items、random campaigns、coverage items）。后续通过这份 inventory 来
创建和评估 work item，不要自己去解析 Markdown 来判断 gate 状态。

Builder 编写的 Markdown 文档状态保持 `PROPOSED`。审批和冻结以 reviewer result 和
durable task/approval ledger 为准，worker 不得通过修改文档状态来自我批准。

只有 `BLOCKER` 和 `MAJOR` finding 才会阻塞 gate。Review 轮数有上限。当 V-plan 变更
了需求、优先级语义或 checker 行为预期时，需要征求用户 approval。

### 1. 工具和 RTL Preflight

V-plan review 通过且 inventory 提取完毕后，向 `verification-runner` 派发 `PREFLIGHT`
（基于审批通过的 plan revision）。仅确认 TB 还不存在时即可验证的事实：受保护的 RTL
roots 和 RTL filelist 存在、DUT top 可访问、clock/reset 信息可获取、请求的
simulator/tool 可发现、license 和环境变量可用、命令入口可用、隔离的 run 目录可写。

此 gate 不要求 TB filelist、TB 源码或编译通过。Ticket 仍需指定一个命令、工具、工
作目录和正的 `timeout_s`，其目的是验证工具/RTL 就绪，而非仿真。

Gate：所需 RTL/工具输入齐全，执行路径可用。环境重试有上限。持久性环境问题应标记为
`BLOCKED`，这不是仿真 bug。

### 2. TB Foundation 和 Smoke

向 builder 派发 `BUILD_SMOKE_FOUNDATION`。Foundation 必须证明一条端到端路径，不仅
仅是能编译：

- reset 和 clock 行为
- 每个关键接口上至少一笔合法 transaction
- 活跃的 sequencer、driver、monitor 路径
- monitor 中的 transaction reconstruction
- reference-model 或 scoreboard 比对
- assertion 和 coverage collector 实例化
- watchdog 和干净的 objection 终止

先做 code review，再派发 runner 做 smoke。**优先派发单次 build-and-run smoke
任务**：一个 `RUN_CASE`，`context` 里带 `build_and_run: true`，命令按 sealed
filelist/defines/top 先重建再运行 smoke sim——编译通过就紧接着 simv，一次派发同时
证明编译+仿真（省去每次 fix 后「编译→仿真」的两次派发往返）。`context.command`
必须是会重编译的 wrapper（如 `make run`），不能复用陈旧二进制。严格链
`COMPILE_ELAB → RUN_CASE` 仍是合法路径，可继续使用。Smoke 是硬 gate：在 review
过的 revision 通过编译、elaboration 和 smoke 之前，不得开始 feature 实现。

Builder 在返回 `READY_FOR_REVIEW` 前已做 lint-only 静态语法检查——默认是插件自带
的零依赖 `static_check.py`（见 `verification-builder.md`）。因此派发 runner 时，
`COMPILE_ELAB` 失败应主要是功能/elaboration 层面问题，而非纯语法错误；若仍出现
语法级失败，说明 builder 漏做或漏报 lint，应回到 builder 而非当作正常编译迭代。

### 3. 优先级 Feature 队列

V-plan 定义了 priority order，默认 `P0, P1, P2`。除非 plan 明确声明，否则不要假设
某个字母是最高优先级（以 priority_order 列表顺序为准）。`P3`（PSV）是仅列举项：
只出现在表格与 Traceability Matrix 中，不进入 `priority_order`、不作为 work item
调度，也不编译/仿真。

每次处理一个 feature 或一个小批量：

```text
builder -> reviewer -> targeted runner -> cumulative priority regression
```

A source file being written is not completion. A work item is complete only
when its exact revision is statically approved and its targeted test passes.
After each accepted batch, rerun smoke and the already accepted tests for the
current priority. Do not move to a lower priority while a higher-priority item
has an unwaived failure or unresolved blocking issue.

### 4. 随机约束和 Coverage Closure

所有 planned directed feature 通过后，按 V-plan 定义的 seed budget 派发 random
test。Runner 记录每个 seed 并 merge coverage。

遇到 coverage gap 时，向 builder 派发 `COVERAGE_CLOSURE`（附带 uncovered bins 和已
批准的 exclusion）。任何 constraint、test、assertion 或 covergroup 变更都要先
review，然后重新跑 targeted seed set 和 cumulative regression。

Coverage exclusion 和 waiver 需要有明确的 evidence 和用户 approval。

### 5. 冻结 Regression 和 Signoff

冻结 V-plan、TB 和 RTL revision。对冻结版本派发完整 regression。然后派发 reviewer
做 `SIGNOFF_AUDIT`，检查 traceability、results、coverage、bug 状态、waiver、seed、
command 和 artifact 路径。

Signoff 必须有用户明确 approval。只有 approval 之后才能推进到 `COMPLETE`。

## 失败路由

Runner outcome 按如下方式路由：

| Runner outcome | Action |
|---|---|
| `PASS` | Evaluate the current gate. |
| `ENVIRONMENT_ERROR` | Dispatch a bounded runner retry. |
| `COMPILE_ERROR` or `ELABORATION_ERROR` | Read the embedded `payload.diagnosis` from the failing runner result; route by its classification. |
| `SIMULATION_FAILURE` or `TIMEOUT` | Read the embedded `payload.diagnosis` from the failing runner result; route by its classification. |
| `COVERAGE_GAP` | Dispatch a bounded `COVERAGE_CLOSURE` builder task for the reported unmet targets. |
| `BLOCKED` | Record the blocker and request the missing input. |

Runner diagnosis classification（失败结果中嵌入的 `payload.diagnosis.classification`）
按如下方式路由：

| Classification | Action |
|---|---|
| `TB_BUG` or `TEST_BUG` | Builder fix -> reviewer -> rerun the same test and seed -> affected regression. |
| `DUT_BUG` | Write a fix request, mark affected items `BLOCKED_DUT`, and continue only independent work. |
| `ENVIRONMENT` or `TOOLCHAIN` | Runner retry within the environment budget. |
| `SPEC_GAP` | Enter a human approval gate; do not invent behavior. |
| `UNKNOWN` | Allow one evidence-collection round, then enter a human gate. |

当 `payload.diagnosis.state = "NEEDS_MORE_EVIDENCE"` 时，用
`rerun.extra_diagnostics` 派发一个**新的 `RUN_CASE` task**（`context.extra_diagnostics`
携带这些诊断请求），其证据用于下一次归因。这不是一个新诊断 task——只有 runner
会做归因，且永远与执行在同一 result 里。

A confirmed DUT defect is neither skipped, passed, nor waived into clean
signoff. Main records a fix request and marks the affected items `BLOCKED_DUT`;
the workflow cannot reach signoff while the fix request is open. The external
RTL owner fixes the protected RTL out of band, then the operator re-runs
`dv_flow.py init` to start a fresh campaign against the corrected design. The
tool does not track RTL drift or accept an RTL revision on the fly.

## Work-item 和 DUT-fix 路由

Plan 通过后，用 `set-item` 更新每个 work item 的状态，不要手动编辑 ledger。正常路
径为：

```text
PENDING -> BUILDING -> AWAITING_REVIEW -> READY_TO_RUN
        -> RUNNING -> PASSED
```

Review findings route `AWAITING_REVIEW -> CHANGES_REQUIRED -> FIXING ->
AWAITING_REVIEW`. Run failures route `RUNNING -> DEBUGGING`; a TB/test diagnosis
then routes `DEBUGGING -> FIXING`, while a confirmed DUT diagnosis routes to
`BLOCKED_DUT`. `READY_TO_RUN` must cite the approved review task with
`--last-task-id`; `PASSED` must cite the passing runner task. `WAIVED` requires a
recorded `WORK_ITEM:<id>` human approval and cannot close an unresolved DUT fix
request.

Entering `AWAITING_REVIEW` requires `--last-task-id` naming the completed
builder task that produced the item's current revision; the ledger stores it as
`builder_task_id`. The subsequent reviewer task must name that exact builder as
its parent. Main rejects a review from another builder lineage even if paths or
IDs happen to overlap.

For a confirmed `DUT_BUG`, use this auditable route:

```text
add-fix-request --failure-task-id <runner-with-embedded-DUT_BUG-diagnosis>
  -> affected work items become BLOCKED_DUT
  -> (out of band) external RTL owner fixes the protected RTL
  -> operator re-runs `dv_flow.py init` for a fresh campaign
```

`add-fix-request` reads the `DUT_BUG` classification from the failing runner
result's embedded `payload.diagnosis`; there is no separate diagnosis task to
cite.

`add-fix-request` is the only way to record a confirmed DUT defect. It marks the
affected work items `BLOCKED_DUT` and keeps the fix request `OPEN`; the signoff
and complete gates stay closed while any fix request is open. The workflow does
not accept an on-the-fly RTL revision or resolve a fix request in place — after
the RTL owner fixes the design, the operator reinitializes.

## 重试限制

- Review：每个 lineage 最多三轮。
- Environment：每个 command/test 组合最多三次。
- TB/test fix：每个 failure signature 最多三次 builder fix。
- Unknown diagnosis：初始分析 + 一次 evidence 收集。
- Worker 格式/内部错误：最多三次 dispatch。

每次重试都创建一个新的不可变 task，不要覆盖旧的 request 或 result。重新 spawn agent
不会重置预算。达到上限后，推进到 `BLOCKED` 或 `WAITING_HUMAN`。

## 完成条件

声明完成前，运行：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dv_flow.py" validate --root <project-root>
```

Completion requires all of the following on one frozen revision:

- V-plan review approved and traceability complete
- smoke and required priority items passed
- random seed budget completed
- required coverage targets met or approved waivers recorded
- full regression passed
- no open blocking TB issue or required-priority DUT bug
- signoff audit approved
- human signoff recorded

每个 gate 通过后简要汇报进度：当前 phase、accepted revision、剩余 work item、
blocker 和下一步 dispatch。

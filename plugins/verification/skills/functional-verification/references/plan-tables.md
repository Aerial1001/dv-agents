# 计划表格（testpoint / testlist / covergroups）

`WRITE_VPLAN` builder 在编写 Markdown V-plan 的同时，还必须基于插件自带的三个
xlsx 模板生成三张计划表格，然后再开始编写任何 TB 代码。表格是 V-plan 的机器可读
组成部分：表格中的稳定 ID 与 `vplan.md` 的 Traceability Matrix 一一对应，reviewer
审批 `REVIEW_VPLAN` 时对两者一并审查。

## 为什么有两份产物

reviewer 只有 `Read`/`Glob`/`Grep`，没有 Bash，无法读取二进制的 xlsx。因此每个
表格都有两份产物，且两份之间的方向是可逆的：

| 产物 | 形态 | 读者 | 作用 |
|---|---|---|---|
| `verification/tables/tables.json` | 文本 JSON（渲染规格） | reviewer、main | 机器可读、可 Read 的表格内容源 |
| `verification/tables/<key>.xlsx` | 二进制 xlsx | 用户、下游工具 | 交付用真实表格 |

`tables.json` 同时就是 `render_tables.py render` 的输入规格。它被 builder 写入
project root 内，因此进入 revision 哈希；三个 `.xlsx` 由 builder 用
`render_tables.py` 从 `tables.json` 确定性渲染，同样进入 revision。

人工在 Excel 里审核并修改交付的 `.xlsx` 后，用 `render_tables.py extract` 把改动
**折回** `tables.json`（`extract` 是 `render` 的逆操作，按模板列头匹配数据表，非空
单元格无损往返）。之后由 `APPLY_PLAN_EDITS` builder 动作把改动纳入一个新的 plan
revision、重新 review 并重新人工审批——否则人工的修改既不会被 agents 读到，也会在
下次 `render` 时被覆盖。详见下文「人工审批时的修改闭环」。

模板 `.xlsx` 位于 `${CLAUDE_PLUGIN_ROOT}/template/`，是**外部只读输入**（与
spec/RTL 同性质）：不在 revision 哈希内，builder 绝不改写模板。

## 三个模板

`render_tables.py dump`（见下）会打印每个模板的数据表与列模式，builder 在填表前
必须先运行它。下面是当前三个模板的固定模式：

### 1. testpoint — `XXXX-UT-TestPoint.xlsx`

数据表名为 `XXXX`（渲染时替换为 DUT 名）。列（顺序固定）：

| 列 | 含义 |
|---|---|
| `ID` | 稳定测试点 ID，形如 `TP-001`。与 vplan 的 `VP-T*` 或需求 ID 可互引。 |
| `L1 Feature` | 一级功能（从 spec/RTL 提取）。 |
| `L2 Feature` | 二级功能；L1 过大时拆分，可为空。 |
| `description` | 对该测试点验证行为的简要说明。 |
| `Priority` | `P0` / `P1` / `P2` / `P3`（见模板「术语说明」页的 P0..P3 语义）。 |
| `Platform` | `simulator` / `emulator` / `FPGA` / `Formal` 之一。 |
| `Verify Level` | `unit` / `subsystem` / `chip` / `soc` 之一。 |
| `DV Owner` | 负责人（可为空）。 |
| `Note` | 补充说明（可为空）。 |

模板自带一个「术语说明」note sheet，渲染时会作为 `Note` sheet 原样保留；其中对
`Priority`、`Platform`、`Verify Level` 等列给出了合法取值，builder 填表时必须遵守。

### 2. testlist — `Bach_Testlist_template.xlsx`

数据表名为 `XXXX`（渲染时替换为 DUT 名）。列（顺序固定）：

| 列 | 含义 |
|---|---|
| `Module` | 模块名（通常等于 DUT 名）。 |
| `Unique Case Name` | 唯一 case 名（测试名）。 |
| `Unique C name` | 唯一 C 测试名；纯 UVM 测试可留空或复用 case 名。 |
| `Function Point` | 覆盖的功能点。 |
| `Build Cfg` | build 配置（filelist/config 标识）。 |
| `DV Level` | 验证层级，同 Verify Level 语义。 |
| `Platform` | `simulator` / `emulator` / `FPGA` / `Formal`。 |
| `Test Steps/Procedure` | 测试步骤/激励与检查流程。 |
| `Checking Mechanism` | 独立检查机制（scoreboard/assertion/reference model 等）。 |
| `Status` | 开发状态（如 `draft`）。 |
| `Priority` | `P0` / `P1` / `P2` / `P3`。 |
| `Comment` | 备注（可为空）。 |

> 该模板的空样式网格里带有一整片 `Column1..Column247` 占位单元格，`render_tables.py`
> 已过滤，列模式不受影响，builder 无需关心。

### 3. covergroups — `Bach_CoverGroups_XXXX.xlsx`

数据表名为 `Sheet1`。列（顺序固定）：

| 列 | 含义 |
|---|---|
| `ID` | 稳定 covergroup 行 ID，形如 `CG-001`。 |
| `CoverGroupName` | covergroup 名。 |
| `CoverPointName` | coverpoint 名。 |
| `SignalName` | 被采样的信号名。 |
| `BinsType` | bin 类型（如 `HIT` / `MISS` / `auto` 等）。 |
| `BinsValue` | bin 取值；`auto` 时可为空。 |
| `Cross` | 交叉 coverpoint 名，可为空。 |
| `Dependency` | 依赖项，可为空。 |

## `tables.json` 契约

```json
{
  "dut": "axi2apb",
  "testpoint":   {"template": "XXXX-UT-TestPoint.xlsx",    "rows": [ {..}, .. ]},
  "testlist":    {"template": "Bach_Testlist_template.xlsx", "rows": [ {..}, .. ]},
  "covergroups": {"template": "Bach_CoverGroups_XXXX.xlsx",  "rows": [ {..}, .. ]}
}
```

- `dut`：DUT 名，用于把数据表名中的 `XXXX` 替换掉。
- 三个键 `testpoint` / `testlist` / `covergroups` 都是**必填**。
- 每个 `*.template` 必须与上面的模板文件名一致；`*.rows` 是对象数组，每个对象以
  模板列名为键。缺键渲染为空单元格；未知键会报错。
- 行顺序即表格顺序，与 vplan Traceability Matrix 的 ID 顺序保持一致。

## 命令

builder 用 Bash 运行 `render_tables.py`（stdlib-only，无第三方依赖）：

```bash
# 1) 先打印模板模式（列 + note sheet 语义），builder 填表前必读
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_tables.py" dump

# 2) 写入 tables.json 后渲染三个 xlsx（输出确定性、可 revision 追踪）
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_tables.py" render \
  --spec verification/tables/tables.json \
  --out verification/tables

# 3) 人工在 Excel 改完 xlsx 后，把改动折回 tables.json（缺省原地覆盖 --spec）
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_tables.py" extract \
  --spec verification/tables/tables.json \
  --xlsx-dir verification/tables
```

`--template-dir` 缺省指向脚本同级的 `${CLAUDE_PLUGIN_ROOT}/template/`，仅在插件
安装位置非常规时才需显式传。

`extract` 的规则：

- 每个表格都按 `spec.<key>.template` 指定的模板匹配数据表：数据表的表头行必须等于
  模板列（顺序一致）；不匹配时报错而不是误解析（例如人工在表头之上插入标题行）。
- 每一数据行变成一个以模板列名为键的对象；整行全空的行被丢弃（删除行、末尾留空行
  都能干净往返）。非空单元格无损进入 `tables.json`，空单元格保持空。
- 模板自带的 note sheet 由 `render` 从模板重新生成，属于模板内容，`extract` 不读、
  人工对 note sheet 的编辑不会保留。
- 输出的 `tables.json` 行是全列形态（空列也带空串键），与 `render` 的消费形态一致。

## 人工审批时的修改闭环

三张 xlsx 是交付给用户的真实表格，因此 VPLAN 人工审批环节把 `vplan.md` 与三个
`.xlsx` 一并交给用户。用户在 Excel 中修改表格后，修改必须经下面的闭环进入 revision，
否则 agents 读不到、且下一次 `render` 会覆盖：

```text
builder WRITE_VPLAN → R1
→ reviewer REVIEW_VPLAN APPROVED（读 tables.json）
→ 把 vplan + 三个 xlsx 交给人工审核
→ 人工在 Excel 里改 xlsx
→ 若人工有改动：builder 跑 APPLY_PLAN_EDITS
     (extract 折回 tables.json → render 重新生成 xlsx) → R2
→ reviewer REVIEW_VPLAN 在 R2 上重审（plan_inventory 重新提取）
→ 人工 approve --gate VPLAN --revision R2
→ 之后的一切基于人工改过的版本
```

要点：

- `extract(render(tables.json))` 的数据格无损往返；人工加/删/改行都会被如实折回。
- 回填发生在 inventory 物化（`transition --to PREFLIGHT`）**之前**。计划一旦冻结进入
  smoke，三张表即只读；中途改表属于计划变更，应走正式 change review 而非静默改 xlsx。
- 人工直接原地改 xlsx 而不走 `APPLY_PLAN_EDITS`，会使工作区与已接受 revision 漂移，
  `transition` 会以 revision-drift 报错拦截——这正是强制走闭环的保护机制。

## Builder 产出清单（WRITE_VPLAN）

在 `scope.write` 内（通常为 `verification/`），builder 必须产出：

- `verification/vplan.md`
- `verification/tables/tables.json`
- `verification/tables/testpoint.xlsx`
- `verification/tables/testlist.xlsx`
- `verification/tables/covergroups.xlsx`

`files_created` 要逐项列出这五个路径，并各自给出小写 `sha256:<hex>` 摘要。模板
`.xlsx` 与脚本 `.py` 在 project root 之外，不写入、不列入 `files_created`。

## Reviewer 审查要点（REVIEW_VPLAN）

reviewer 读取 `verification/tables/tables.json`（必要时读本参考文件与
`vplan.md`），并核对：

- 三张表都存在，`dut` 与任务一致，`template` 与列模式匹配。
- 每个 testpoint/testlist 行的 ID 唯一，且在 vplan Traceability Matrix 中有对应
  条目（或为显式记录的 coverage-only 行）。
- `Priority` / `Platform` / `Verify Level` / `DV Level` 取值落在模板「术语说明」页
  声明的合法集合内。
- 每个 testlist 行有独立 `Checking Mechanism`；covergroup 行的
  `CoverGroupName` / `CoverPointName` / `SignalName` 非空且与 vplan coverage 模型
  一致。
- 表格 ID 与 vplan 的 requirement/feature/test/coverage ID 交叉可追溯，无悬空引用。

表格审查是 `REVIEW_VPLAN` 的一部分，不单独出 finding 类别；发现表格缺陷时按
`category: plan`（或 `coverage` / `checker` 视内容）产出带 `path` 指向
`verification/tables/tables.json` 的 finding。

## 语义边界

- 模板是外部只读输入，builder 只读不写，不进 revision。
- `tables.json` 与三个 `.xlsx` 是 builder 在 project root 内写出的验证资产，进入
  revision，随 V-plan 一并被 review gate 追踪。
- 表格生成发生在 `WRITE_VPLAN` 内、任何 TB 代码编写之前；`BUILD_SMOKE_FOUNDATION`
  及之后所有 builder action 都假设这三张表已审批冻结，不再生成表格。

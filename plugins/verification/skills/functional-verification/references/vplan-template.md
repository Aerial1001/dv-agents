# <设计名> 验证计划

## 文档控制

| 字段 | 值 |
|---|---|
| 计划版本 | `VP-REV-001` |
| 设计 | `<name>` |
| 规格参考 | `<path（可在 project root 之外）>` |
| RTL 参考 | `<filelist 与 roots（可在 project root 之外）>` |
| DUT 顶层 | `<module>` |
| 验证顶层 | `<module>` |
| 负责人 | `<name>` |
| 状态 | `PROPOSED` |

`PROPOSED` 是本 builder 所有文档中唯一写入的状态。不可变的 reviewer 结果、
工作流 ledger 以及显式的人工审批才是计划接受与冻结的权威依据；不要通过修改
此字段来表示审批。

## 优先级语义

定义显式的执行顺序，不要依赖字典序。下面四个层级遵循模板的 `术语说明` 表
（`XXXX-UT-TestPoint.xlsx`），对本文件与生成的计划表均具权威性。

| 优先级 | 含义 | 退出规则 |
|---|---|---|
| `P0` | 基础且紧急的特性 | 所有映射的强制测试通过或已获批准豁免 |
| `P1` | 必备特性 | 所有映射的强制测试通过或已获批准豁免 |
| `P2` | 锦上添花的特性 | 所有强制测试通过或已获批准豁免 |
| `P3` | PSV（硅后验证）特性 | 仅列出——在本 DV 流程中从不编译、elaborate 或仿真 |

Smoke 是独立的 bring-up 门槛，不属于特性优先级。

`P3` 行仅用于枚举：它们出现在 testpoint/testlist 表与追溯矩阵中，用于硅后
验证的可追溯性，但 DV 流程不得为其排程、编译、elaborate 或仿真。它们的验证
发生在硅上（PSV），在本仿真 campaign 之外。

## 计划表

本 Markdown 计划随附三张机器可读表，在任何 TB 代码编写之前由插件模板生成
（见 `plan-tables.md`）：

| 表 | 文本源 | 渲染产物 |
|---|---|---|
| testpoint | `verification/tables/tables.json` → `testpoint` | `verification/tables/testpoint.xlsx` |
| testlist | `verification/tables/tables.json` → `testlist` | `verification/tables/testlist.xlsx` |
| covergroups | `verification/tables/tables.json` → `covergroups` | `verification/tables/covergroups.xlsx` |

保持这些表中的 ID 与本文件同步：每个 testpoint / testlist 行 ID 都出现在追溯
矩阵中（或为显式的仅覆盖率行），每个 covergroup 行都映射到覆盖率模型条目。
builder 依据规格与 RTL 事实填表；reviewer 将这些表与本计划一并审批。

## 输入与假设

- 时钟与频率：
- 复位极性、同步性与时序：
- 接口与协议版本：
- 参数/配置：
- 期望的仿真器与 UVM 版本：
- 外部模型或 package：
- 显式假设：

## 规格缺口

| 缺口 ID | 需求/来源 | 歧义或冲突 | 受影响 ID | 负责人 | 决定/状态 |
|---|---|---|---|---|---|
| `SPEC-GAP-001` | `<section>` | `<未知行为>` | `REQ-...` | Human | OPEN |

不得为未解决的缺口臆造期望行为。

## TB 架构

描述组件层级与事务流，包含：

- DUT 与接口绑定
- active/passive agent、sequencer、driver 与 monitor
- monitor 事务重建
- reference model/predictor 与 scoreboard 的独立性
- assertion 绑定与时钟/复位域
- coverage collector 与采样事件
- configuration/factory 归属
- watchdog、objection 与确定性收尾
- filelist、编译、elaborate、仿真与覆盖率入口

### 组件映射

| 组件 ID | 类型 | 接口/域 | 职责 | 输入 | 输出 |
|---|---|---|---|---|---|
| `TB-COMP-001` | `monitor` | `<interface>` | `<职责>` | `<信号>` | `<事务>` |

## Smoke 门槛

| Smoke ID | 所需证据 | 接受标准 |
|---|---|---|
| `SMOKE-001` | 编译/elaborate 日志 | 无编译或 elaborate 错误 |
| `SMOKE-002` | 复位轨迹/断言 | 复位到达文档化的空闲态 |
| `SMOKE-003` | driver 与 monitor 证据 | 观察到一笔最小合法事务端到端通过 |
| `SMOKE-004` | scoreboard/reference-model 证据 | 至少一次真实比较完成并通过 |
| `SMOKE-005` | assertion/coverage 证据 | 绑定与 collector 已实例化并处于活跃状态 |
| `SMOKE-006` | 完成日志 | watchdog 保持安静且 objection 干净收尾 |

在可行处，加入一个有界 checker 自测或故障注入，以证明关键 checker 能检测到
错误。

## 追溯矩阵

每个可独立执行的测试目标占一行。ID 在计划批准后永不改变；被取代的行仍保留
记录。

| 需求 ID | 特性 ID | 测试 ID | 优先级 | 强制 | 依赖 | 激励/约束 | 独立 checker/oracle | 断言 | 覆盖率 ID | 接受标准 | 实现引用 | 状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `REQ-001` | `FEAT-001` | `VP-T001` | `P0` | yes | none (after smoke gate) | `<合法与边界激励>` | `<预测与比较>` | `ASRT-001` | `COV-001` | `<可观测的通过条件>` | TBD | PLANNED |

每个范围内的需求都映射到至少一个测试/checker 与覆盖率，或写明覆盖率不适用的
原因。

## 特性批次

保持批次足够小，以便在一个循环内完成构建、评审、定向执行与累积回归。

| 批次 ID | 优先级 | 特性/测试 ID | 依赖 | 定向集合 | 累积集合 |
|---|---|---|---|---|---|
| `BATCH-P0-001` | `P0` | `FEAT-001, VP-T001` | smoke | `VP-T001` | smoke + 已接受的 P0 测试 |

## 约束随机计划

| Campaign ID | 测试/配置 | 种子预算 | 强制 | 依赖 | 停止条件 | 覆盖率贡献 |
|---|---|---:|---|---|---|---|
| `RAND-001` | `<test>` | `20` | yes | `VP-T001` | 任何失败都被记录并路由；预算完成 | `COV-...` |

记录每个 seed、命令、revision、结果与覆盖率数据库。失败的 seed 被保留以便
精确复跑。

## 覆盖率模型与收敛

| 覆盖率 ID | 指标 | 需求 ID | 目标百分比 | 强制 | 依赖 | 采样/bins | 排除/豁免负责人 |
|---|---|---|---:|---|---|---|---|
| `COV-001` | functional | `REQ-001` | 100 | yes | `RAND-001` | `<定义>` | none |

为功能、断言与适用的代码覆盖率定义目标。收敛动作可以增加合法激励、约束、
测试、断言或 coverpoint；不得弱化检查或隐藏可达 bins。

## 已批准计划清单

批准时的 reviewer 返回由这些表推导出的机器可读清单。保持每个被引用的 ID 唯一、
每个依赖显式，使清单无需解读文字即可校验：

- directed items: `id`, `kind`, `priority`, `dependencies`, `mandatory`
- random campaigns: `id`, `test`, `seed_budget`, `mandatory`, `dependencies`
- coverage items: `id`, `metric`, numeric `target`, `mandatory`, `dependencies`
- priority order: 与优先级语义中自上而下的精确顺序一致

## 回归与签核

冻结的签核集合记录：

- 只读的规格与 RTL 引用（在 revision 哈希之外）
- V-plan 与 TB revision
- 仿真器/UVM 版本与精确命令
- 强制测试列表与 seed manifest
- 合并后的覆盖率报告与阈值
- 开放/关闭的 bug 与 fix-request ledger
- 已批准的排除与豁免
- 完整回归结果路径

最终接受需要一次通过的冻结回归、批准的签核审计、无未解决的强制工作，以及
显式的人工审批。

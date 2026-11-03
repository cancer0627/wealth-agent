# agent（智能体）

## Responsibility（职责）

负责：

- 使用模型生成目标导向的 `TaskPlanV2`，并持有和推进内部任务
- 针对当前任务反复调用模型，每轮选择一个计划允许的 Tool（工具）或完成任务
- 每次 Tool 调用前请求 `governance` 判定，等待确认时保存可恢复检查点
- 返回回答、待确认操作、工具轨迹、模型用量或结构化错误

不负责：

- 不制定治理规则，不直接访问业务数据库
- 不让模型或浏览器生成写入幂等键、原始来源文本或预期台账版本

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：本能力内部实现
- `implementation/planning/`：模型驱动的结构化任务规划
- `implementation/runtime/`：基于持久任务的受限执行循环
- `implementation/tools/`：只读工具、单条台账新增工具和产品资料更新工具的注册和执行
- `tests/`：本能力测试；当前跨能力集成测试位于 `apps/api/tests/integration/`

## Dependencies（依赖）

- 依赖：`portfolio.contract`、`analysis.contract`、`tasks.contract` 和 `wealth_agent.shared.model`
- 不依赖：其他能力的 `implementation/`、具体模型适配器或 `bootstrap`

## Dependents（依赖方）

- `conversation`：启动运行、传递确认、读取或取消会话关联运行
- `bootstrap`：组装模型网关、任务、治理、工具和 Agent 运行时

## State / Data Ownership（状态 / 数据所有权）

拥有：

- Agent 运行语义，以及任务中的计划、模型/工具轨迹和执行检查点

不拥有：

- `data/wealth.db`、`data/task_state.db` 和 `data/telemetry.db`
- 会话历史、治理规则和业务事实

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- `wealth-agent-tools-v2`：六个只读工具、`add_ledger_record` 和 `update_product_profile`
- 模型规划、持久任务、逐步 Tool 执行、逐次治理和最终汇总主链
- 写入参数绑定确认、稳定幂等键、重启恢复和台账版本冲突重新确认
- 计划外调用、无证据回答、无效模型输出和预算限制

待实现：

- 多进程 Agent Executor（智能体执行器）租约与分布式调度；当前仅支持单进程同步执行

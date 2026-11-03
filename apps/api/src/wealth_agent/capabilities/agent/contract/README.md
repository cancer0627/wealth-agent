# agent Contract（公开契约）

本目录定义 `agent` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- `TaskPlanV2` 只固化任务目标、完成条件和允许工具，不提前固化调用参数。
- Agent Executor（智能体执行器）每轮只能调用一个计划内 Tool，或返回当前任务完成结果。
- `add_ledger_record` 只接收产品名、日期、记录类型和金额；来源、版本和幂等键由 Agent 内部注入。
- `update_product_profile` 接收产品 ID 和需要更新的产品类型、风险等级或业绩比较基准；版本和幂等键由 Agent 内部注入，未提供的资料字段由执行端保留现值。
- `get_model_analysis` 只能基于 analysis（理财分析）能力返回的确定性报表提供逐产品方向：`increase`、`hold`、`reduce` 或 `insufficient_data`；不生成金额、配置比例或交易执行指令。
- 有副作用的 Tool 必须等待与具体操作绑定的确认；失败返回结构化错误。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `AgentService`、`AgentTaskPlannerPort` 和 `AgentToolPort`
- `RunAgentRequest`、`RunAgentResult`、计划、回答和工具轨迹语义
- `wealth-agent-tools-v2`、八个工具名称、契约哈希和结构化错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 调用方必须提供会话标识、有效模型配置和原始用户消息。
- 单产品工具需要由上层会话提供 `active_product_id`；本能力不负责生成产品标识。
- 用户确认由 `conversation` 传递；是否需要确认及确认是否有效只由 `governance` 判断。

## Verification（验证）

契约行为由 `apps/api/tests/integration/test_agent_read_only_tools.py`、
`apps/api/tests/integration/test_agent_runtime.py` 和
`apps/api/tests/unit/test_structured_capability_contracts.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

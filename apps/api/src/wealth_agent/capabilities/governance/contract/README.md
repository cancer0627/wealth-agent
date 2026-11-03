# governance Contract（公开契约）

本目录定义 `governance` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- 治理判断只返回允许、拒绝或需要确认，不执行 Tool（工具）和业务写入。
- 每次 Agent Tool 调用都必须提交 `ToolAuthorizationRequest`；写入确认必须匹配当前操作哈希。
- Agent（智能体）发起的备份恢复始终拒绝；无副作用请求只允许已知只读权限。
- 相同事件标识和相同内容可幂等写入，不同内容不得覆盖已有事件。
- 确定性评测按结构化 JSON（结构化数据格式）精确比较并返回一致的计数与通过率。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `GovernanceService`、`TelemetryRepository` 和 `EvaluationService`
- 授权决策、授予权限、规则版本、事件内容哈希和评测汇总语义
- 治理持久化错误和结构化错误详情

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 调用方必须明确任务、步骤、Tool 名称、请求权限、副作用类型和操作哈希。
- 评测请求必须提供非空用例集合；本能力不负责生成评测数据集。
- 用户确认由 `conversation` 传递，本能力只判断其是否满足规则。

## Verification（验证）

契约行为由 `apps/api/tests/integration/test_remaining_backend_capabilities.py` 和
`apps/api/tests/unit/test_structured_capability_contracts.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

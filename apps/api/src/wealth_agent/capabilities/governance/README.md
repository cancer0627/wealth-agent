# governance（治理规则）

## Responsibility（职责）

负责：

- 对每次 Tool（工具）调用判断允许、拒绝或需要用户确认
- 限制 Tool（工具）权限和备份恢复入口
- 持久化遥测与审计事件，并执行确定性评测

不负责：

- 不调用模型，不执行 Tool 或具体业务
- 不管理会话和任务，不写入资产数据库

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：默认治理规则、确定性评测和 SQLite（嵌入式关系数据库）遥测仓储
- `tests/`：本能力测试目录；当前集成测试位于 `apps/api/tests/integration/test_remaining_backend_capabilities.py`

## Dependencies（依赖）

- 依赖：无业务语义的共享错误、序列化工具和本能力拥有的 SQLite 存储
- 不依赖：Agent（智能体）、会话、任务或业务能力的内部实现

## Dependents（依赖方）

- `agent`：在每次 Tool 调用前请求判定并记录关联轨迹
- `adapters/http`：提供审计事件查询和评测入口
- `bootstrap`：组装治理服务与遥测仓储

## State / Data Ownership（状态 / 数据所有权）

拥有：

- 治理规则版本及 `data/telemetry.db` 中的遥测、审计和评测数据

不拥有：

- 资产事实、分析结果、会话内容和任务状态

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- 只读 Tool 自动允许、未知权限拒绝和台账写入逐条确认
- 确认与 Tool 名称、规范化参数、任务步骤和台账版本绑定，不能复用
- Agent 发起备份恢复的确定性拒绝
- 幂等且带内容哈希的事件存储与 `exact-json-v1` 评测

待实现：

- 动态治理规则配置、版本迁移和管理入口

# backup（备份）

## Responsibility（职责）

负责：

- 创建和列出台账备份介质及 manifest（清单）
- 校验备份文件大小、哈希、数据库结构和台账版本
- 在完整确认后编排原子恢复和恢复前备份

不负责：

- 不直接访问或拥有 `data/wealth.db`
- 不修改单条业务记录，不触发分析、Agent（智能体）或任务计划

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：本地文件系统备份、校验和恢复编排
- `tests/`：本能力测试目录；当前集成测试位于 `apps/api/tests/integration/test_backup_service.py`

## Dependencies（依赖）

- 依赖：`portfolio.contract` 的 `PortfolioBackupPort（资产备份端口）`
- 不依赖：`portfolio/implementation/`、Agent、任务或会话内部实现

## Dependents（依赖方）

- `adapters/http`：提供备份列表、创建、校验和恢复入口
- `bootstrap`：组装备份目录、时钟和资产备份端口

## State / Data Ownership（状态 / 数据所有权）

拥有：

- 独立备份目录中的备份文件和 `manifest.json`

不拥有：

- `data/wealth.db`、任务状态、会话状态和分析结果

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- 备份创建、列表、完整性校验和结构化错误
- 确认文本与台账版本校验、原子恢复和恢复前备份
- HTTP（超文本传输协议）入口，并禁止将恢复注册为 Agent Tool（智能体工具）

待实现：

- 使用脱敏业务数据库副本完成数据等价性和恢复演练验收

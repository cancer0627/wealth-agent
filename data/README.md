# data（本地运行时数据）

## Responsibility（职责）

- 保存业务事实、分析结果、会话、任务控制状态、治理数据和台账备份。

## Boundary（边界）

- 允许后端各能力的实现层读写自己拥有的 SQLite（嵌入式关系数据库）和备份数据。
- 禁止 HTTP（超文本传输协议）适配器、前端和 Agent（智能体）直接访问 SQLite，也禁止能力访问其他能力拥有的数据。
- 依赖方向为后端能力实现层 → `data/`；`data/` 不依赖应用代码。

## Navigation（导航）

- `wealth.db`：`portfolio` 拥有的业务事实。
- `analysis.db`：`analysis` 拥有的版本绑定分析结果。
- `task_state.db`：`tasks` 拥有的任务控制状态。
- `conversation.db`：`conversation` 拥有的消息、活动产品、OCR 草稿和当前 Agent 运行引用。
- `telemetry.db`：`governance` 管理的遥测、评测和审计数据。
- `backups/`：`backup` 管理的台账备份介质。

具体实现以源码为准。

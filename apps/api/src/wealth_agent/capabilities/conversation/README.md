# conversation（对话）

## Responsibility（职责）

负责：

- 管理会话、消息历史和活动产品
- 启动 Agent（智能体）运行、传递确认并展示最终结果或确认卡片
- 管理 OCR（光学字符识别）草稿及确认后的资产写入请求

不负责：

- 不判断 Tool（工具）权限或治理规则
- 不实现 Agent 决策，不调用 `TaskService`，不直接访问其他能力数据库

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：会话持久化、Agent 调用、确认和 OCR 草稿流程
- `tests/`：本能力测试

## Dependencies（依赖）

- 依赖：`agent.contract`、`governance.contract`、`portfolio.contract` 和 `OcrEngine（OCR 引擎）`端口
- 不依赖：其他能力的 `implementation/` 或数据库实现

## Dependents（依赖方）

- `adapters/http`：提供会话、消息、产品选择和 OCR 草稿入口
- `adapters/ocr`：实现本能力定义的 OCR 引擎端口
- `bootstrap`：组装 Agent、治理、资产写入和 OCR 适配器

## State / Data Ownership（状态 / 数据所有权）

拥有：

- `data/conversation.db` 中的会话、消息、活动产品、单个 OCR 草稿和当前 Agent 运行引用

不拥有：

- 资产事实、治理规则、Agent 轨迹和任务状态

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- 会话创建、查询、重置、消息追加和活动产品选择
- Agent 运行、确定性确认卡片、拒绝/确认及终态消息幂等回流
- OCR 草稿创建、单条编辑、单条删除、丢弃和明确确认后的批量原子写入
- SQLite（嵌入式关系数据库）会话持久化及待确认状态重启恢复

待实现：

- 使用脱敏业务截图完成 OCR 识别与草稿修订验收

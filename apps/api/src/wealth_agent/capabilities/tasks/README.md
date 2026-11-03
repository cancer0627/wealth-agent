# tasks（任务）

## Responsibility（职责）

负责：

- 管理任务计划、步骤、状态机、检查点和最终结果
- 保存 Agent 运行标识、`trace_id`、模型/工具轨迹、待确认操作和 Checkpoint（检查点）
- 处理 Agent 内部的确认、恢复、取消、重试和乐观并发控制

不负责：

- 不进行模型决策，不生成新计划或判断 Tool（工具）权限
- 不实现具体业务步骤，不访问其他能力的内部实现

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：状态机、进程内及 SQLite（嵌入式关系数据库）存储、Worker 和运行时
- `tests/`：本能力测试

## Dependencies（依赖）

- 依赖：`ExecutorPort（执行器端口）`和 `TaskEnvironmentPort（任务环境端口）`的外部实现，以及共享时钟、错误和序列化工具
- 不依赖：`agent`、具体业务能力的 `implementation/` 或 `bootstrap`

## Dependents（依赖方）

- `agent`：使用任务计划和结果结构表达可执行步骤
- `bootstrap`：组装内部任务服务和任务环境端口

## State / Data Ownership（状态 / 数据所有权）

拥有：

- `data/task_state.db` 中的任务、链路标识、步骤、检查点、状态版本、内容哈希和幂等回执

不拥有：

- 资产事实、分析结果、会话状态和治理数据

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- 进程内和 SQLite 共用的任务状态机及持久化重启恢复
- `TaskPlanV2`、Agent 运行关联、执行检查点和待确认操作持久化
- 写入确认、台账版本和状态版本绑定的并发控制
- V1 数据库升级前恢复副本、原始记录归档和非终态任务升级失效
- V2 → V3 升级前备份并为历史任务补充持久化 `trace_id`

`TaskService` 继续作为跨能力内部契约存在，只允许 Agent 和启动组装层调用；
HTTP（超文本传输协议）与页面没有任务查询或控制入口。

待实现：

- 跨进程任务租约；当前任务并发控制基于单进程锁和持久状态版本

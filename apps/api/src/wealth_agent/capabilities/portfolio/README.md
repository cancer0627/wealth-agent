# portfolio（资产组合）

## Responsibility（职责）

负责：

- 管理产品资料、估值、现金流和资产查询
- 在事务内执行版本校验、业务写入和幂等回执
- 向备份能力提供数据库快照、检查和原子恢复端口

不负责：

- 不负责 LLM（大语言模型）、Agent（智能体）、任务或治理规则
- 不管理备份介质、分析结果或前端状态

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：SQLite（嵌入式关系数据库）结构、读取、事务写入和备份端口
- `tests/`：本能力测试

## Dependencies（依赖）

- 依赖：本能力拥有的 SQLite 业务库，以及共享时钟、序列化和文件锁工具
- 不依赖：其他能力的 `implementation/` 或 `bootstrap`

## Dependents（依赖方）

- `analysis`：消费已提交版本的资产事实
- `agent`：通过只读工具读取产品、快照和报表所需事实
- `backup`：通过 `PortfolioBackupPort（资产备份端口）`创建和恢复快照
- `conversation`、`adapters/http` 和 `bootstrap`：执行资产查询、写入和组装

## State / Data Ownership（状态 / 数据所有权）

拥有：

- `data/wealth.db` 中的产品、估值、现金流、台账版本和命令幂等回执

不拥有：

- `data/analysis.db`、`data/task_state.db`、`data/telemetry.db` 和备份目录

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- 产品目录、资产快照和单产品台账读取
- 产品资料及记录增删改、批量记录写入、版本控制和幂等回放
- SQLite 快照检查、并发文件锁和原子恢复端口

待实现：

- 使用用户提供的只读旧库副本完成数据等价性和恢复演练验收

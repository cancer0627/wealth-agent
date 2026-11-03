# analysis（理财分析）

## Responsibility（职责）

负责：

- 基于资产快照执行确定性收益和数据质量分析
- 独立调用 LLM（大语言模型）生成结构化建议
- 持久化并按产品和台账版本查询分析结果

不负责：

- 不负责 Agent（智能体）编排、任务状态或治理判断
- 不写入资产台账，不因备份恢复自动重新分析

## Code（代码）

- `contract/`：对其他能力暴露的公开契约
- `implementation/`：确定性计算、模型建议和 SQLite（嵌入式关系数据库）存储
- `tests/`：本能力测试

## Dependencies（依赖）

- 依赖：`portfolio.contract` 提供资产事实，`wealth_agent.shared.model` 提供模型网关
- 不依赖：`agent`、其他能力的 `implementation/` 或具体模型适配器

## Dependents（依赖方）

- `agent`：通过只读工具消费确定性报表、数据质量结果和结构化模型分析建议
- `adapters/http`：向前端提供报表、分析和历史结果
- `bootstrap`：注入资产读取、模型网关和分析存储

## State / Data Ownership（状态 / 数据所有权）

拥有：

- `data/analysis.db` 中按台账版本绑定的结构化分析记录
- 分析提示词、确定性算法和模型建议组装规则

不拥有：

- `data/wealth.db` 中的资产事实
- Agent 轨迹和治理评测数据

## Contract（公开契约）

对外接口见 [`contract/`](contract/README.md)。

接口、参数、返回类型和错误类型以 `contract/` 中源码为准。

## Status（状态）

已实现：

- Modified Dietz（修正迪茨）计算、版本绑定报表和数据不足处理
- 模型建议及模型失败时保留确定性报表的降级行为
- 分析记录的幂等保存、冲突检查和按版本查询

待实现：

- 使用真实模型密钥完成供应商网络调用验收

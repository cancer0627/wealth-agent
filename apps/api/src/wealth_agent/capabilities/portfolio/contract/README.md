# portfolio Contract（公开契约）

本目录定义 `portfolio` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- 读取结果携带台账版本，产品事实携带产品版本。
- 每个写入命令都校验预期台账版本和幂等键。
- 业务变化、版本更新和命令回执在同一个 SQLite（嵌入式关系数据库）事务中提交。
- 相同幂等键和相同请求返回原结果；同一幂等键对应不同请求或版本冲突时不提交变化。
- 批量记录写入全部成功或全部回滚；数据库恢复通过公开端口执行原子替换。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `PortfolioReader`、`PortfolioWriter`、`PortfolioBatchWriter` 和 `PortfolioBackupPort`
- 产品、估值、现金流、台账版本、产品版本和金额精度语义
- 版本冲突、幂等冲突、产品不存在和数据库损坏错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 写入方必须提供当前 `expected_revision` 和非空 `idempotency_key`。
- 金额必须为有限正数且最多两位小数；批量写入必须包含有效记录。
- 调用方负责上层权限和确认，本能力只执行资产业务校验。

## Verification（验证）

契约行为由 `apps/api/src/wealth_agent/capabilities/portfolio/tests/test_sqlite_reader.py`、
`apps/api/src/wealth_agent/capabilities/portfolio/tests/test_sqlite_store.py` 和
`apps/api/tests/integration/test_backup_service.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

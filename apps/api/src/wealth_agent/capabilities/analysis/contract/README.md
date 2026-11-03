# analysis Contract（公开契约）

本目录定义 `analysis` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- 确定性报表始终绑定台账版本和产品版本。
- 数据不足返回稳定状态，不伪造收益结果。
- 产品资料不完整、收益数据不足或收益率分母无效时，建议方向固定为 `insufficient_data`，不调用模型生成增减持结论。
- 模型调用或模型输出失败时保留已完成的确定性报表，并返回结构化 `model_error`。
- 相同分析记录可幂等保存；相同标识对应不同内容时拒绝覆盖。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `AnalysisEngine`、`AnalysisService` 和 `AnalysisRepository`
- `ProductReport`、`AnalysisResult`、`AnalysisRecord` 及版本绑定语义
- 收益状态、建议方向和持久化冲突错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 调用方必须提供完整 `ProductSnapshot`、台账版本和生成时间。
- 模型建议只能基于确定性报表；本能力不负责资产读取权限和业务写入确认。

## Verification（验证）

契约行为由 `apps/api/src/wealth_agent/capabilities/analysis/tests/test_deterministic_analysis.py`、
`apps/api/src/wealth_agent/capabilities/analysis/tests/test_model_analysis.py` 和
`apps/api/src/wealth_agent/capabilities/analysis/tests/test_sqlite_repository.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

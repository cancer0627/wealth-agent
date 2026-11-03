# backup Contract（公开契约）

本目录定义 `backup` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- 创建操作先在临时目录完成快照和清单，再发布为正式备份。
- 校验会检查清单、文件大小、SHA-256（安全哈希算法）、数据库结构和台账版本。
- 恢复前必须校验备份并保存当前台账；实际替换由 `PortfolioBackupPort` 原子执行。
- 确认、校验或版本检查失败时不替换当前台账，恢复也不触发其他能力。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `BackupService` 的创建、列表、校验和恢复操作
- 备份标识、清单版本、文件哈希、台账版本和恢复前备份标识
- 备份不存在、备份损坏和结构化校验错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 恢复请求的 `confirmation_backup_id` 必须与目标 `backup_id` 完全一致。
- 调用方必须提供当前预期台账版本，并确保恢复入口不是 Agent Tool（智能体工具）。
- 本能力不负责上层身份、页面入口或权限校验。

## Verification（验证）

契约行为由 `apps/api/tests/integration/test_backup_service.py` 和
`apps/api/tests/architecture/test_capability_boundaries.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

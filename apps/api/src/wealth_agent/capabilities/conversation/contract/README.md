# conversation Contract（公开契约）

本目录定义 `conversation` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- 返回的会话快照与内部可变状态隔离，消息和 Agent 运行引用按会话持久化。
- 每个会话最多保留一个待处理 OCR（光学字符识别）草稿。
- OCR 草稿记录可以按 `record_id` 单条编辑或删除；操作只修改会话草稿，不修改资产事实。
- 同一会话最多一个非终态 Agent（智能体）运行；待确认时只接受确认、拒绝或重置。
- 确认卡片不公开 Task（任务）状态机字段，确认请求只接受 `approve` 或 `reject`；卡片可展示新增台账记录或产品资料更新的具体参数。
- 只有匹配草稿且明确确认的请求才转换为带版本与幂等键的资产写入。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `ConversationService`、`ConversationTurnResult`、确认卡片和 `OcrEngine（OCR 引擎）`
- 会话、消息、活动产品、OCR 提取结果、草稿记录编辑/删除和草稿确认结果
- 会话不存在和待处理草稿冲突的结构化错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 除创建外的操作必须提供已存在的 `session_id`。
- 草稿记录编辑和删除必须提供当前 `draft_id`、`record_id`；草稿丢弃和确认必须提供当前 `draft_id`，确认写入还需要当前台账版本。
- 权限判断由 `governance` 完成，本能力只传递结果和确认意图。

## Verification（验证）

契约行为由 `apps/api/src/wealth_agent/capabilities/conversation/tests/test_in_memory_service.py` 和
`apps/api/tests/integration/test_remaining_backend_capabilities.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

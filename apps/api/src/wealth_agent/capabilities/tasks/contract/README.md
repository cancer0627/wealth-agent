# tasks Contract（公开契约）

本目录定义 `tasks` 对其他能力暴露的公开接口。

## Source of Truth（事实源）

公开接口以 [`__init__.py`](__init__.py) 为准。

其他能力只能依赖这里导出的类型、方法和端口，不应依赖本能力的 `implementation/`。

## Behavioral Guarantees（行为保证）

- `TaskPlanV2` 最多包含十个不重复步骤，每步只记录目标、完成条件和允许工具。
- 状态变更使用 `state_version` 乐观并发控制；幂等创建不会产生重复任务。
- Agent 运行、模型/工具结果、待确认提案和执行上下文可写入检查点。
- 每个任务持久化独立 `trace_id`，确认、重新规划和 Worker（后台执行器）恢复时沿用同一链路。
- 待确认写入绑定操作哈希和台账版本；环境变化使旧任务或旧确认失效。
- 业务执行只通过 `ExecutorPort（执行器端口）`，不传递进程内 Callback（回调）。

## Stability（稳定边界）

以下属于跨能力稳定边界：

- `TaskService`、`ExecutorPort`、`TaskEnvironmentPort` 和 `TaskWorkerRuntimePort`
- 任务计划、步骤、状态、检查点、链路标识、状态版本和 TaskResult（任务结果）语义
- 任务不存在、状态冲突、计划冲突、幂等冲突和持久化错误语义

修改这些内容时，需要检查依赖本能力的调用方。

## Preconditions（调用前提）

- 创建任务必须提供有效计划、当前台账版本和环境代次；Agent 任务还应关联会话与运行标识。
- 确认必须匹配任务、计划哈希、状态版本和当前台账版本。
- 调用方负责生成计划、完成治理判断和实现具体业务执行器。

## Verification（验证）

契约行为由 `apps/api/src/wealth_agent/capabilities/tasks/tests/test_task_state_machine.py`、
`apps/api/src/wealth_agent/capabilities/tasks/tests/test_sqlite_task_service.py`、
`apps/api/src/wealth_agent/capabilities/tasks/tests/test_worker_recovery.py` 和
`apps/api/tests/integration/test_task_runtime_bootstrap.py` 验证。

具体方法、参数、返回类型和错误定义以 `__init__.py` 及其引用源码为准。

# 系统设计

本文件只定义跨能力的系统级约束。

能力内部行为由对应能力的 `README.md` 和 `contract/` 定义；当前实现行为以代码和测试为准。

## Dependency Rules（依赖规则）

```text
adapters → capability.contract

capability → capability.contract
capability → shared

bootstrap → capabilities
bootstrap → adapters
bootstrap → shared
```

规则：

- 跨能力只能依赖目标能力公开的 `contract/`。
- 不得跨能力依赖 `implementation/`。
- `contract/` 不得依赖 `implementation/`。
- 业务能力不得依赖 `bootstrap/`。
- `bootstrap/` 只负责组装和依赖注入。
- `shared/` 只能包含无业务归属的公共契约和技术组件。
- Adapter（适配器）只负责协议转换，不承载业务规则。

## Capability Boundaries（能力边界）

| 能力 | 负责 | 不负责 |
| --- | --- | --- |
| `conversation` | 会话、上下文、消息、确认卡片和 Agent 运行关联 | 权限判断、任务管理和业务执行 |
| `agent` | 模型规划、任务编排、Tool（工具）决策和执行循环 | 直接访问数据库和制定治理规则 |
| `governance` | 权限、副作用和确认判断 | Tool 执行 |
| `portfolio` | 资产事实和写入一致性 | Agent 编排 |
| `analysis` | 理财分析和分析结果 | 资产写入和 Agent 编排 |
| `tasks` | Agent 内部的持久任务、检查点和执行状态 | 模型决策和页面任务管理 |
| `backup` | 备份和恢复 | Agent 计划、业务分析和单条业务写入 |

具体公开能力以对应 `contract/` 为准。

## Data Ownership（数据所有权）

- 每份持久数据必须有且只有一个能力拥有。
- 能力只能直接访问自己拥有的数据。
- HTTP（超文本传输协议）、前端和 Agent 不得直接访问持久化实现。
- 跨能力数据访问必须通过公开 `contract/`。

具体存储方式和数据结构由对应能力的实现定义。

## Core Rules（核心规则）

### Agent 副作用

```text
conversation → agent → LLM（大语言模型）规划
                         ↓
                    tasks（内部状态）
                         ↓
                    Agent Executor（智能体执行器）
                         ↓
                    LLM 决策 Tool
                         ↓
                    governance（逐次判定）
                         ↓
                    Tool → capability
```

- `governance` 是允许、拒绝和确认要求的唯一判断者。
- `conversation` 只负责会话交互、确认传递和最终输出，不调用 `TaskService`。
- `agent` 持有并推进内部任务；每轮模型只能选择一个计划允许的 Tool 或完成当前任务。
- `tasks` 负责持久状态和检查点，不向页面或 HTTP 暴露管理入口。
- 业务能力负责最终业务约束、事务和幂等。
- 只读 Tool 自动放行；写入 Tool 的确认必须绑定具体参数、步骤和台账版本。
- 备份恢复只能由备份页面发起，不能注册为 Agent Tool。
- 具体确认规则由 `governance` 的公开 `contract/` 定义。

### Model（模型）

`agent` 和需要模型能力的业务模块依赖公共 Model Gateway（模型网关），不得直接依赖具体模型供应商。

### Bootstrap（启动组装）

`bootstrap` 只负责系统组装和运行时依赖注入，不能成为业务能力之间的通信通道。

### Structured Logging（结构化日志）

- `shared/logging` 定义无业务语义的日志门面、调用上下文和有界序列化。
- `adapters/logging` 负责把 JSONL（逐行 JSON 日志）按系统本地日期写入 `logs/`。
- HTTP 入口为每次请求建立内部 `trace_id`；任务持久化该标识，并在确认、重新规划和 Worker（后台执行器）恢复时继续使用。
- 日志记录失败不得改变业务结果；日志不是资产事实、任务恢复状态或治理遥测的事实来源。

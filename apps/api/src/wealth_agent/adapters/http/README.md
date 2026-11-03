# HTTP 适配器

## Responsibility（职责）

HTTP 入站适配器，负责将 HTTP 请求转换为业务能力调用，并将结果转换为 HTTP 响应。

## Boundary（边界）

- 不包含业务逻辑。
- 不直接访问业务数据。
- 仅依赖 capability（能力）的公开 `contract（契约）`。
- 不依赖 capability 内部实现。

## Navigation（导航）

- `app.py`：应用入口与生命周期。
- `dependencies.py`：HTTP 可调用的业务能力。
- `contracts.py`：HTTP 传输结构。
- `routes/`：路由入口。
- `errors.py`：HTTP 错误映射。

`app.py` 为每个请求建立仅供后端使用的 `trace_id（链路标识）`；不新增或回传
HTTP Header（请求头），路由方法的结构化入参与出参由共享日志组件记录。

Chat 入口只暴露 Conversation（会话）消息与确认接口；不暴露内部 Task（任务）
查询、确认、暂停、恢复或取消路由。

具体路由、依赖和接口以源码为准。

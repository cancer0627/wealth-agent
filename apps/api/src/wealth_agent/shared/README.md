# shared（无业务语义共享代码）

## Responsibility（职责）

存放同时满足以下条件的共享代码：

- 可跨模块复用。
- 不包含业务语义。

## Boundary（边界）

- 具有业务语义的代码放入对应 capability（能力）。
- 外部供应商或基础设施实现放入对应 adapter（适配器）。
- 不依赖 `capabilities/`、`adapters/` 或 `bootstrap/`。
- 每个组件的 `__init__.py` 是唯一公共入口。
- 调用方不得依赖组件内部实现。

## Navigation（导航）

- `clock`：时间协议与系统时间。
- `errors`：通用结构化错误。
- `file_lock`：进程间文件锁。
- `identity`：不透明标识生成。
- `logging`：结构化方法日志、调用链上下文和非致命降级。
- `serialization`：稳定 JSON 序列化与哈希。
- `model`：模型公共契约。

具体接口、参数和行为以源码为准。

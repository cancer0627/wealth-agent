# bootstrap（启动组装）

应用的 Composition Root（组合根），负责配置、依赖组装和应用启动。

## Responsibility（职责）

- 创建并连接 capability（能力）、adapter（适配器）和基础设施实现。
- 完成 Dependency Injection（依赖注入）。
- 注册 Tool（工具）并绑定 Executor Port（执行器端口）。
- 管理应用启动与生命周期组装。
- 使用 `WEALTH_AGENT_LOG_DIR` 组装按日 JSONL（逐行 JSON 日志）文件 Sink（落盘实现）。

## Boundary（边界）

- 可以依赖各模块的公开契约和具体实现。
- 不包含业务规则、业务状态或独立业务数据。
- capability、adapter 和 shared 不得反向依赖 `bootstrap`。

具体组装关系和运行配置以源码为准。

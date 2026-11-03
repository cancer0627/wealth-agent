# api-client（前端 API 客户端）

## Responsibility（职责）

- 提供由 OpenAPI（接口描述规范）生成的传输类型，以及两个前端应用复用的 HTTP（超文本传输协议）请求客户端。

## Boundary（边界）

- 允许反映 `adapters/http/` 的公开接口，并通过公共入口提供类型化请求能力。
- 禁止包含页面实现、后端业务规则、Tool Contract（工具契约）、Executor Port（执行器端口）或 SQLite（嵌入式关系数据库）访问逻辑。
- 依赖方向为 `apps/web`、`apps/governance` → `packages/api-client` → `adapters/http`。
- 禁止手工修改生成的 `openapi.json` 和 `src/schema.d.ts`；HTTP 契约变化后必须重新生成并检查漂移。

## Navigation（导航）

- `src/index.ts`：公共类型和请求方法入口。
- `openapi.json`：由后端导出的 OpenAPI 文档。
- `src/schema.d.ts`：根据 `openapi.json` 生成的 TypeScript（静态类型编程语言）类型。
- `src/index.test.ts`：客户端请求行为测试。
- `package.json`：生成、检查和测试命令入口。

具体实现以源码为准。

# governance（B 端治理应用）

## Responsibility（职责）

- 提供治理规则、审计事件查询与确定性评测的 B 端页面。

## Boundary（边界）

- 允许通过后端 API（应用程序接口）和 `packages/api-client/` 读取治理数据并发起治理操作。
- 禁止直接访问 SQLite（嵌入式关系数据库）或复制后端治理规则。
- 依赖方向为 `apps/governance` → `packages/api-client` → 后端 API。

## Navigation（导航）

- `src/main.ts`：应用启动入口。
- `src/App.vue`：应用页面出口。
- `src/router.ts`：Hash Routing（哈希路由）入口，根路径与未知路径均转到 `#/model-governance`。
- `src/pages/model-governance/`：治理规则、审计事件与确定性评测页面。

具体实现以源码为准。

# web（C 端 Web 应用）

## Responsibility（职责）

- 提供资产报表、自然语言对话、确认交互以及备份恢复的 C 端页面。

## Boundary（边界）

- 允许通过后端 API（应用程序接口）和 `packages/api-client/` 读取业务数据并发起用户操作。
- 禁止直接访问 SQLite（嵌入式关系数据库）或复制后端业务规则。
- 依赖方向为 `apps/web` → `packages/api-client` → 后端 API。

## Navigation（导航）

- `src/main.ts`：应用启动入口。
- `src/App.vue`：应用页面出口。
- `src/router.ts`：Hash Routing（哈希路由）入口，根路径与未知路径均转到 `#/reports`。
- `src/pages/reports/`：资产报表、台账与模型分析页面。
- `src/pages/chat/`：自然语言对话、确认交互与 OCR（光学字符识别）草稿页面。
- `src/pages/backups/`：备份与恢复页面。

具体实现以源码为准。

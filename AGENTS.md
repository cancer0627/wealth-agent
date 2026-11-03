# AGENTS.md

## 事实来源

按问题读取对应文档，不重复维护同一事实：

- 长期目标和范围：`PROJECT.md`
- 架构和跨能力边界：`SYSTEM.md`
- 当前实现：代码和测试
- 后续计划：`ROADMAP.md`
- 当前任务：`docs/tasks/`
- 专项规则：`docs/decisions/`
- 能力职责：`capabilities/<name>/README.md`
- 能力公开接口：`capabilities/<name>/contract/`
- 历史：Git

不得把目标设计或旧项目行为描述成已经实现；当前行为以代码和测试为准。

## 最小上下文

默认只读取：

1. `AGENTS.md`、`README.md`
2. 当前能力的 `README.md` 和 `contract/`
3. 直接依赖能力的 `contract/`
4. 当前任务文档

其他内容按需读取。先用 `rg` / `rg --files` 定位，再读取最小相关片段。

## 架构硬规则

能力目录：

```text
capabilities/<name>/
├── README.md
├── contract/
├── implementation/
└── tests/
```

- `contract/` 是跨能力唯一公开边界。
- 禁止跨能力导入 `implementation/`。
- `contract/` 不得依赖 `implementation/`。
- 业务能力不得依赖 `bootstrap/`。
- `shared/` 只放无业务语义的通用技术代码。
- `bootstrap/` 只负责配置、组装和启动。

## 能力所有权

- `agent`：模型决策、计划和 Tool（工具）请求；不直接写业务数据库。
- `tasks`：任务状态、检查点、确认和并发控制；不负责模型决策。
- `conversation`：会话及确认交互；不决定业务写入权限。
- `portfolio`：资产数据业务写入最终边界。
- `backups`：整库备份恢复；不修改单条业务记录。
- `analysis`：确定性计算和分析；不负责 Agent 编排。

HTTP（超文本传输协议）、前端和 Agent 均不得直接访问 SQLite（嵌入式关系数据库）。

数据库职责：

- `data/wealth.db`：业务事实
- `data/task_state.db`：任务控制
- `data/telemetry.db`：遥测和评测

模型上下文、提示词和 Tool 描述不得扩张真实权限。

更新 Tool 必须遵循 `docs/decisions/agent-tool-confirmation.md`。

## 前端

- `apps/web/`：C 端
- `apps/governance/`：B 端

前端仅通过后端 API（应用程序接口）和 `packages/api-client/` 协作，不直接访问 SQLite 或复制后端业务规则。

## 修改原则

- 一次只处理一个可独立验收的能力切片。
- 边界变化先改 `contract/`，再改实现和调用方。
- 不修改 `wealth_agent_bak/`。
- 不恢复新项目未明确需要的旧功能。
- 不做当前任务之外的重构或未来预留。

文档同步：

- 能力职责变化 → `README.md`
- 架构变化 → `SYSTEM.md`
- 计划变化 → `ROADMAP.md`

## 验证

开发中运行受影响范围的检查，最终验收运行相关完整检查。

```bash
UV_CACHE_DIR=/tmp/wealth-agent-uv-cache uv sync
.venv/bin/pytest -q --tb=short
.venv/bin/ruff check .
.venv/bin/mypy
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Node.js ≥ 20。

未执行的检查必须说明原因。

边界变化必须有契约测试和架构测试，至少禁止：

- 跨能力导入 `implementation/`
- 能力依赖 `bootstrap/`
- Agent、HTTP、前端直连 SQLite

不得提交：

```text
data/*.db*
logs/
dist/
test-results/
本地密钥
```

不得使用破坏性 Git 命令覆盖用户修改。

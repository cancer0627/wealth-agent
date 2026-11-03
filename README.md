# wealth_agent

本地理财 Agent（智能体）应用。

## Run（启动）

环境：

- Python（编程语言）3.11+
- Node.js（JavaScript 运行时）20+
- `uv`
- `pnpm`

安装并启动后端：

```bash
uv sync --all-packages
pnpm install
cp .env.example .env
# 按需编辑 .env
.venv/bin/wealth-agent-api
```

启动前端：

```bash
pnpm dev
pnpm dev:governance
```

## Logging（日志）

后端把 Adapter（适配器）和 Capability（能力）的运行时业务方法写为结构化
JSONL（逐行 JSON 日志），默认目录为 `logs/`，可通过
`WEALTH_AGENT_LOG_DIR` 修改。文件名为 `wealth-agent-YYYY-MM-DD.jsonl`，按系统
本地日期拆分并永久保留；单次调用可通过 `trace_id`、`task_id` 和 `call_id` 串联。

日志保留有界的业务入参和出参，不做字段脱敏；二进制只记录长度和 SHA-256
（安全哈希算法）。日志写入失败不会改变业务结果。

## Maintenance（维护）

执行维护命令前先停止后端服务，并在项目根目录运行。

清空全部运行数据和已有业务备份，仅保留 `data/README.md` 与
`data/backups/.gitkeep`。此操作不可恢复：

```bash
./scripts/clear-all-data.sh
```

备份并清空当天日志。备份文件与原日志位于同一目录，依次使用 `.bak`、
`.bak.1`、`.bak.2` 后缀：

```bash
./scripts/backup-clear-today-log.sh
```

## Documentation（文档导航）

| 需要了解 | 读取 |
| --- | --- |
| 项目目标、范围和约束 | `PROJECT.md` |
| 系统架构和跨能力边界 | `SYSTEM.md` |
| 后续计划 | `ROADMAP.md` |
| 某个能力的职责和边界 | `apps/api/src/wealth_agent/capabilities/<name>/README.md` |
| 能力公开接口 | `apps/api/src/wealth_agent/capabilities/<name>/contract/` |

当前实现行为以代码和测试为准。

# Model adapters（模型适配器）

负责将不同模型供应商适配到统一 `ModelGateway（模型网关）`。

## Components（组件）

- `openai_compatible.py`：通用 OpenAI-compatible（OpenAI 兼容）协议适配。
- 其他供应商文件：仅处理供应商差异。

## Boundary（边界）

- 实现 `wealth_agent.shared.model` 暴露的 `ModelGateway`。
- 业务能力不得直接依赖本目录。
- 不包含 Prompt（提示词）、业务规则、Agent（智能体）策略或业务重试逻辑。

具体请求、响应和供应商差异以源码为准。
# model-governance 页面 Contract（公开契约）

## Source of Truth（事实源）

页面使用的公开方法以 [`packages/api-client/src/index.ts`](../../../../../../packages/api-client/src/index.ts) 为准，
请求和响应结构以生成的 [`schema.d.ts`](../../../../../../packages/api-client/src/schema.d.ts) 为准。

页面只能依赖 API Client（接口客户端）公开导出的类型和方法，不应依赖后端能力的
`implementation/`。

## Behavioral Guarantees（行为保证）

- 页面加载时读取全部治理事件，也可按 `model`、`tool`、`task`、`audit` 或 `evaluation` 类别重新查询。
- 页面只展示后端返回的事件和结构化载荷，不编辑规则、授权结果或已有事件。
- 当前评测入口提交固定的 `manual-smoke-v1` 数据集与两个 JSON（结构化数据格式）样例，
  页面只展示后端返回的通过数、失败数和通过率。
- 请求失败时展示错误，不在前端推导治理决策或评测结果。

## Preconditions（调用前提）

- 事件筛选值必须为空或属于客户端公开的五种事件类别。
- 运行评测需要浏览器能够生成评测标识，并且本地后端可访问。
- 页面依赖本地后端与生成客户端的当前接口版本一致；当前不提供规则编辑或任意评测数据录入。

# reports 页面 Contract（公开契约）

## Source of Truth（事实源）

页面使用的公开方法以 [`packages/api-client/src/index.ts`](../../../../../../packages/api-client/src/index.ts) 为准，
请求和响应结构以生成的 [`schema.d.ts`](../../../../../../packages/api-client/src/schema.d.ts) 为准。

页面只能依赖 API Client（接口客户端）公开导出的类型和方法，不应依赖后端能力的
`implementation/`。

## Behavioral Guarantees（行为保证）

- 页面先读取产品目录；当前选择无效时选择目录中的第一个产品，没有产品时清空详情。
- 产品详情、确定性报表和最新分析并行读取；重新分析只更新模型分析结果，不改写台账。
- 删除产品携带当前台账版本与新的幂等键；删除前必须经过浏览器确认。
- 后端请求失败时展示错误，页面不直接修改业务数据库，也不在前端补算业务结果。

## Preconditions（调用前提）

- 删除和分析需要已选择产品；删除还需要已加载产品台账并获得用户确认。
- 页面依赖本地后端与生成客户端的当前接口版本一致。

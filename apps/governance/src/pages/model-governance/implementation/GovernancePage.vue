<script setup lang="ts">
import {
  listGovernanceEvents,
  runEvaluation,
  type EvaluationResult,
  type GovernanceEventList,
} from "@wealth-agent/api-client";
import { computed, onMounted, ref } from "vue";

type Category = "model" | "tool" | "task" | "audit" | "evaluation";

const category = ref<Category | "">("");
const events = ref<GovernanceEventList>({ events: [] });
const evaluation = ref<EvaluationResult>();
const error = ref("");
const busy = ref(false);
const eventItems = computed(() => events.value.events ?? []);

async function load(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    events.value = await listGovernanceEvents(category.value || undefined);
  } catch (value) {
    error.value = value instanceof Error ? value.message : "读取治理事件失败";
  } finally {
    busy.value = false;
  }
}

async function evaluate(): Promise<void> {
  busy.value = true;
  try {
    evaluation.value = await runEvaluation({
      evaluation_id: crypto.randomUUID(),
      dataset_version: "manual-smoke-v1",
      cases: [
        { case_id: "same", expected: { status: "ok" }, actual: { status: "ok" } },
        { case_id: "different", expected: 1, actual: 2 },
      ],
    });
  } catch (value) {
    error.value = value instanceof Error ? value.message : "评测失败";
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page-stack">
    <section class="page-hero"><div><p class="eyebrow">MODEL GOVERNANCE</p><h1>模型治理</h1><p>查看脱敏运行轨迹，并执行确定性结构评测。</p></div><div class="toolbar"><select v-model="category"><option value="">全部事件</option><option value="model">模型</option><option value="tool">工具</option><option value="task">任务</option><option value="audit">审计</option><option value="evaluation">评测</option></select><button :disabled="busy" @click="load">刷新</button></div></section>
    <p v-if="error" class="notice error">{{ error }}</p>
    <section class="metric-grid"><article><span>事件数量</span><strong>{{ eventItems.length }}</strong></article><article><span>规则</span><strong>governance-v1</strong></article><article><span>原始问题</span><strong>不持久化</strong></article></section>
    <section class="panel"><h2>运行事件</h2><table><thead><tr><th>时间</th><th>类别</th><th>名称</th><th>关联标识</th><th>结构化载荷</th></tr></thead><tbody><tr v-for="event in eventItems" :key="event.event_id"><td>{{ event.occurred_at }}</td><td>{{ event.category }}</td><td>{{ event.name }}</td><td>{{ event.correlation_id ?? '—' }}</td><td><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></td></tr></tbody></table><p v-if="eventItems.length === 0">暂无事件。</p></section>
    <section class="panel"><div class="section-heading"><div><h2>确定性评测</h2><p>使用两个固定 JSON 样例验证评分链路。</p></div><button :disabled="busy" @click="evaluate">运行样例</button></div><p v-if="evaluation" class="notice success">通过 {{ evaluation.passed }}，失败 {{ evaluation.failed }}，通过率 {{ evaluation.pass_rate }}</p></section>
  </div>
</template>

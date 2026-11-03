<script setup lang="ts">
import {
  analyzeProduct,
  executePortfolioCommand,
  getLatestAnalysis,
  getProduct,
  getProductReport,
  listProducts,
  type AnalysisResult,
  type PortfolioCommand,
  type ProductCatalog,
  type ProductLedger,
  type ProductReport,
} from "@wealth-agent/api-client";
import { computed, onMounted, ref, watch } from "vue";

const catalog = ref<ProductCatalog>();
const ledger = ref<ProductLedger>();
const report = ref<ProductReport>();
const analysis = ref<AnalysisResult>();
const selectedId = ref("");
const busy = ref(false);
const error = ref("");

const selected = computed(() => catalog.value?.products.find((item) => item.product_id === selectedId.value));

function message(value: unknown): string {
  return value instanceof Error ? value.message : "操作失败";
}

async function loadCatalog(): Promise<void> {
  error.value = "";
  try {
    catalog.value = await listProducts();
    if (!catalog.value.products.some((item) => item.product_id === selectedId.value)) {
      selectedId.value = catalog.value.products[0]?.product_id ?? "";
    }
  } catch (value) {
    error.value = message(value);
  }
}

async function loadProduct(): Promise<void> {
  if (!selectedId.value) {
    ledger.value = undefined;
    report.value = undefined;
    analysis.value = undefined;
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const [nextLedger, nextReport, latest] = await Promise.all([
      getProduct(selectedId.value),
      getProductReport(selectedId.value),
      getLatestAnalysis(selectedId.value),
    ]);
    ledger.value = nextLedger;
    report.value = nextReport;
    analysis.value = latest.record?.result;
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function removeProduct(): Promise<void> {
  if (!ledger.value || !confirm(`确认删除“${ledger.value.product.name}”及全部台账记录？`)) return;
  busy.value = true;
  try {
    await executePortfolioCommand({
      kind: "delete_product",
      product_id: ledger.value.product.product_id,
      expected_revision: ledger.value.ledger_revision,
      idempotency_key: crypto.randomUUID(),
    } as PortfolioCommand);
    selectedId.value = "";
    await loadCatalog();
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function runAnalysis(): Promise<void> {
  if (!selectedId.value) return;
  busy.value = true;
  try {
    analysis.value = await analyzeProduct(selectedId.value);
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

watch(selectedId, loadProduct);
onMounted(loadCatalog);
</script>

<template>
  <div class="page-stack">
    <section class="page-hero">
      <div><p class="eyebrow">PORTFOLIO</p><h1>资产与收益报表</h1><p>确定性收益不依赖模型，模型建议单独保存。</p></div>
      <div class="toolbar">
        <select v-model="selectedId" aria-label="选择产品">
          <option value="">暂无产品</option>
          <option v-for="product in catalog?.products" :key="product.product_id" :value="product.product_id">{{ product.product_name }}</option>
        </select>
        <button :disabled="busy" @click="loadCatalog">刷新</button>
      </div>
    </section>
    <p v-if="error" class="notice error">{{ error }}</p>

    <template v-if="ledger && report">
      <section class="metric-grid">
        <article><span>累计转入</span><strong>¥ {{ report.total_transfer_in }}</strong></article>
        <article><span>累计转出</span><strong>¥ {{ report.total_transfer_out }}</strong></article>
        <article><span>修正迪茨收益率</span><strong>{{ report.overall?.modified_dietz_return ?? "数据不足" }}</strong></article>
      </section>
      <section class="panel">
        <div class="section-heading"><div><h2>{{ selected?.product_name }}</h2><small>台账版本 {{ ledger.ledger_revision }}</small></div><button class="danger" :disabled="busy" @click="removeProduct">删除产品</button></div>
        <h3>估值记录</h3>
        <table><thead><tr><th>日期</th><th>金额</th><th>来源</th></tr></thead><tbody><tr v-for="item in ledger.product.valuations" :key="item.date"><td>{{ item.date }}</td><td>{{ item.amount }}</td><td>{{ item.source_text }}</td></tr></tbody></table>
        <h3>现金流</h3>
        <table><thead><tr><th>日期</th><th>类型</th><th>金额</th></tr></thead><tbody><tr v-for="item in ledger.product.cash_flows" :key="item.flow_id"><td>{{ item.date }}</td><td>{{ item.type }}</td><td>{{ item.amount }}</td></tr></tbody></table>
        <p v-for="warning in report.warnings" :key="warning" class="notice warning">{{ warning }}</p>
      </section>
      <section class="panel">
        <div class="section-heading"><h2>模型建议</h2><button :disabled="busy" @click="runAnalysis">重新分析</button></div>
        <template v-if="analysis?.suggestion"><strong>{{ analysis.suggestion.direction }}</strong><p>{{ analysis.suggestion.summary }}</p><p>{{ analysis.suggestion.rationale }}</p></template>
        <p v-else>尚无分析结果。</p>
      </section>
    </template>
  </div>
</template>

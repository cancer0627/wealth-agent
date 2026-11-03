<script setup lang="ts">
import {
  createBackup,
  getPortfolio,
  listBackups,
  restoreBackup,
  verifyBackup,
  type BackupList,
  type BackupVerification,
  type PortfolioSnapshot,
} from "@wealth-agent/api-client";
import { computed, onMounted, ref } from "vue";

const portfolio = ref<PortfolioSnapshot>();
const backups = ref<BackupList>();
const verification = ref<BackupVerification>();
const target = ref("");
const confirmation = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");
const backupItems = computed(() => backups.value?.backups ?? []);
const verificationErrors = computed(() => verification.value?.errors ?? []);

function message(value: unknown): string {
  return value instanceof Error ? value.message : "操作失败";
}

async function refresh(): Promise<void> {
  try {
    [portfolio.value, backups.value] = await Promise.all([getPortfolio(), listBackups()]);
  } catch (value) {
    error.value = message(value);
  }
}

async function create(): Promise<void> {
  busy.value = true;
  try {
    const result = await createBackup();
    notice.value = `已创建备份 ${result.backup_id}`;
    await refresh();
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function verify(backupId: string): Promise<void> {
  try {
    verification.value = await verifyBackup(backupId);
  } catch (value) {
    error.value = message(value);
  }
}

async function restore(): Promise<void> {
  if (!portfolio.value || confirmation.value !== target.value) return;
  busy.value = true;
  try {
    const result = await restoreBackup(target.value, portfolio.value.revision);
    sessionStorage.removeItem("wealth-agent-conversation-v1");
    target.value = "";
    confirmation.value = "";
    notice.value = `恢复成功；恢复前备份为 ${result.pre_restore_backup_id}`;
    await refresh();
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="page-stack">
    <section class="page-hero"><div><p class="eyebrow">DATA SAFETY</p><h1>备份与恢复</h1><p>只备份业务台账；恢复前自动创建当前台账备份。</p></div><button :disabled="busy" @click="create">创建备份</button></section>
    <p v-if="notice" class="notice success">{{ notice }}</p><p v-if="error" class="notice error">{{ error }}</p>
    <section class="metric-grid"><article><span>当前台账版本</span><strong>{{ portfolio?.revision ?? '读取中' }}</strong></article><article><span>备份数量</span><strong>{{ backupItems.length }}</strong></article></section>
    <section class="panel">
      <table><thead><tr><th>备份标识</th><th>时间</th><th>大小</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in backupItems" :key="item.backup_id"><td><code>{{ item.backup_id }}</code></td><td>{{ item.manifest?.created_at ?? '—' }}</td><td>{{ item.manifest?.file_size ?? '—' }}</td><td>{{ item.valid ? '有效' : '异常' }}</td><td><div class="toolbar"><button @click="verify(item.backup_id)">校验</button><button class="danger" :disabled="!item.valid" @click="target = item.backup_id; confirmation = ''">恢复</button></div></td></tr></tbody></table>
      <p v-if="backupItems.length === 0">暂无备份。</p>
    </section>
    <p v-if="verification" :class="['notice', verification.valid ? 'success' : 'error']">{{ verification.backup_id }}：{{ verification.valid ? '校验通过' : verificationErrors.map(item => item.message).join('；') }}</p>
    <section v-if="target" class="panel danger-zone"><h2>确认恢复</h2><p>恢复后旧会话和页面任务状态会被清理，不会自动重新分析或规划。</p><p>请输入完整备份标识：<code>{{ target }}</code></p><input v-model="confirmation" autocomplete="off"><div class="toolbar"><button class="danger" :disabled="busy || confirmation !== target" @click="restore">确认恢复</button><button class="ghost" @click="target = ''">取消</button></div></section>
  </div>
</template>

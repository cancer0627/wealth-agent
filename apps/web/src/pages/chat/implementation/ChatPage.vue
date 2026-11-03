<script setup lang="ts">
import {
  ApiError,
  confirmOcrDraft,
  createConversation,
  createOcrDraft,
  deleteOcrDraftRecord,
  discardOcrDraft,
  getConversation,
  getPortfolio,
  respondConversationApproval,
  resetConversation,
  sendConversationMessage,
  updateOcrDraftRecord,
  type ConversationSession,
  type OcrDraftRecord,
  type OcrDraftRecordUpdate,
} from "@wealth-agent/api-client";
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";

const SESSION_KEY = "wealth-agent-conversation-v1";
const session = ref<ConversationSession>();
const text = ref("");
const busy = ref(false);
const error = ref("");
const copiedMessageId = ref("");
let copyFeedbackTimer: ReturnType<typeof globalThis.setTimeout> | undefined;
const editing = reactive<{
  recordId: string;
  product_name: string;
  record_date: string;
  amount: string;
}>({ recordId: "", product_name: "", record_date: "", amount: "" });

function isRecordReady(record: OcrDraftRecord): boolean {
  return (
    Boolean(record.product_name?.trim()) &&
    Boolean(record.amount) &&
    Number.isFinite(Number(record.amount)) &&
    Number(record.amount) > 0
  );
}

const canConfirmDraft = computed(() => {
  const records = session.value?.ocr_draft?.records ?? [];
  return records.length > 0 && records.every(isRecordReady);
});

const hasIncompleteDraft = computed(() => {
  const records = session.value?.ocr_draft?.records ?? [];
  return records.length > 0 && records.some((record) => !isRecordReady(record));
});

const hasConversationContent = computed(() => {
  return Boolean(
    session.value?.messages?.length ||
      session.value?.ocr_draft ||
      session.value?.pending_approval,
  );
});

function message(value: unknown): string {
  return value instanceof Error ? value.message : "操作失败";
}

function copyLabel(role: "user" | "assistant"): string {
  return role === "user" ? "复制问题" : "复制答案";
}

function clearCopyFeedback(): void {
  if (copyFeedbackTimer !== undefined) {
    globalThis.clearTimeout(copyFeedbackTimer);
    copyFeedbackTimer = undefined;
  }
}

async function copyMessage(messageId: string, content: string): Promise<void> {
  try {
    await globalThis.navigator.clipboard.writeText(content);
    clearCopyFeedback();
    copiedMessageId.value = messageId;
    copyFeedbackTimer = globalThis.setTimeout(() => {
      copiedMessageId.value = "";
      copyFeedbackTimer = undefined;
    }, 2000);
    error.value = "";
  } catch {
    error.value = "复制失败，请检查浏览器剪贴板权限";
  }
}

async function newSession(): Promise<ConversationSession> {
  const created = await createConversation();
  sessionStorage.setItem(SESSION_KEY, created.session_id);
  return created;
}

async function loadSession(): Promise<void> {
  error.value = "";
  const stored = sessionStorage.getItem(SESSION_KEY);
  try {
    session.value = stored ? await getConversation(stored) : await newSession();
  } catch (value) {
    if (value instanceof ApiError && value.status === 404) {
      session.value = await newSession();
    } else {
      error.value = message(value);
    }
  }
}

async function send(): Promise<void> {
  const content = text.value.trim();
  if (!session.value || !content) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await sendConversationMessage(session.value.session_id, content);
    session.value = result.session;
    text.value = "";
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function respondApproval(decision: "approve" | "reject"): Promise<void> {
  const approval = session.value?.pending_approval;
  if (!session.value || !approval) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await respondConversationApproval(
      session.value.session_id,
      approval.approval_id,
      decision,
    );
    session.value = result.session;
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function upload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !session.value) return;
  busy.value = true;
  try {
    session.value = await createOcrDraft(
      session.value.session_id,
      file,
      new Date().toISOString().slice(0, 10),
    );
  } catch (value) {
    error.value = message(value);
  } finally {
    input.value = "";
    busy.value = false;
  }
}

function startEditing(record: OcrDraftRecord): void {
  editing.recordId = record.record_id;
  editing.product_name = record.product_name ?? "";
  editing.record_date = record.record_date;
  editing.amount = record.amount ?? "";
  error.value = "";
}

function cancelEditing(): void {
  editing.recordId = "";
  editing.product_name = "";
  editing.record_date = "";
  editing.amount = "";
}

function hasValidEdit(): boolean {
  return (
    Boolean(editing.product_name.trim()) &&
    Boolean(editing.record_date) &&
    Number.isFinite(Number(editing.amount)) &&
    Number(editing.amount) > 0
  );
}

async function saveEditing(): Promise<void> {
  const draft = session.value?.ocr_draft;
  if (!session.value || !draft || !editing.recordId) return;
  if (!hasValidEdit()) {
    error.value = "产品名称、日期和有效金额不能为空";
    return;
  }
  const values: OcrDraftRecordUpdate = {
    product_name: editing.product_name.trim(),
    record_date: editing.record_date,
    amount: editing.amount.trim(),
  };
  busy.value = true;
  error.value = "";
  try {
    session.value = await updateOcrDraftRecord(
      session.value.session_id,
      draft.draft_id,
      editing.recordId,
      values,
    );
    cancelEditing();
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function removeRecord(record: OcrDraftRecord): Promise<void> {
  const draft = session.value?.ocr_draft;
  if (!session.value || !draft || editing.recordId || busy.value) return;
  const name = record.product_name?.trim() || "未识别记录";
  if (!globalThis.confirm(`确认删除 OCR 草稿中的“${name}”记录？`)) return;
  busy.value = true;
  error.value = "";
  try {
    session.value = await deleteOcrDraftRecord(
      session.value.session_id,
      draft.draft_id,
      record.record_id,
    );
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function confirmDraft(): Promise<void> {
  const draft = session.value?.ocr_draft;
  if (!session.value || !draft) return;
  const records = draft.records ?? [];
  if (editing.recordId) return;
  if (!canConfirmDraft.value) {
    error.value = records.length
      ? "请先补全每条记录的产品名称和金额"
      : "OCR 草稿没有可写入记录";
    return;
  }
  busy.value = true;
  try {
    const portfolio = await getPortfolio();
    const result = await confirmOcrDraft(
      session.value.session_id,
      draft.draft_id,
      portfolio.revision,
    );
    session.value = result.session;
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function discardDraft(): Promise<void> {
  const draft = session.value?.ocr_draft;
  if (!session.value || !draft || editing.recordId || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    session.value = await discardOcrDraft(session.value.session_id, draft.draft_id);
  } catch (value) {
    error.value = message(value);
  } finally {
    busy.value = false;
  }
}

async function reset(): Promise<void> {
  if (!session.value) return;
  cancelEditing();
  session.value = await resetConversation(session.value.session_id);
}

onMounted(async () => {
  await loadSession();
});

onUnmounted(() => {
  clearCopyFeedback();
});
</script>

<template>
  <div class="page-stack">
    <section class="page-hero"><div><p class="eyebrow">CONVERSATION</p><h1>智能会话</h1><p>Agent 可读取台账，并在你确认后新增记录或更新产品资料；备份恢复不会出现在计划或工具中。</p></div><button :disabled="busy" @click="reset">重置会话</button></section>
    <p v-if="error" class="notice error">{{ error }}</p>
    <section class="panel chat-panel">
      <div
        class="messages"
        :class="{
          'has-interactive-message': Boolean(session?.ocr_draft || session?.pending_approval),
        }"
      >
        <p v-if="!hasConversationContent" class="empty">可以询问产品列表、资料、收益或数据质量。</p>
        <div v-for="item in session?.messages ?? []" :key="item.message_id" :class="['message-item', item.role]">
          <article :class="['message', item.role]">
            <div class="message-header"><strong>{{ item.role === 'user' ? '你' : 'Agent' }}</strong></div>
            <p>{{ item.content }}</p>
          </article>
          <button
            type="button"
            class="ghost message-copy-button"
            :aria-label="copyLabel(item.role)"
            :title="copyLabel(item.role)"
            @click="copyMessage(item.message_id, item.content)"
          >
            {{ copiedMessageId === item.message_id ? "已复制" : "复制" }}
          </button>
        </div>
        <article v-if="session?.ocr_draft" class="message assistant interactive-message ocr-draft-message" aria-label="OCR 草稿确认">
          <div class="message-header"><strong>Agent</strong><span>OCR 草稿</span></div>
          <div class="interactive-message-body warning">
            <h2>OCR 草稿</h2>
            <p>原图不会保存；请核对后再写入台账。</p>
            <p v-if="hasIncompleteDraft">请编辑补全产品名称和金额，或删除无法确认的记录。</p>
            <p v-if="!(session.ocr_draft.records?.length)">当前草稿没有保留记录，请重新识别或丢弃草稿。</p>
            <div v-else class="ocr-draft-table-wrap">
              <table class="ocr-draft-table"><thead><tr><th>产品</th><th>日期</th><th>金额</th><th>提示</th><th>操作</th></tr></thead><tbody>
                <tr v-for="record in session.ocr_draft.records ?? []" :key="record.record_id">
                  <template v-if="editing.recordId === record.record_id">
                    <td><input v-model="editing.product_name" maxlength="200" aria-label="产品名称"></td>
                    <td><input v-model="editing.record_date" type="date" aria-label="记录日期"></td>
                    <td><input v-model="editing.amount" inputmode="decimal" aria-label="金额"></td>
                    <td>{{ (record.warnings ?? []).join('；') }}</td>
                    <td><div class="toolbar"><button type="button" :disabled="busy" @click="saveEditing">保存</button><button type="button" class="ghost" :disabled="busy" @click="cancelEditing">取消</button></div></td>
                  </template>
                  <template v-else>
                    <td>{{ record.product_name ?? "未识别" }}</td>
                    <td>{{ record.record_date }}</td>
                    <td>{{ record.amount ?? "未识别" }}</td>
                    <td>{{ (record.warnings ?? []).join('；') }}</td>
                    <td><div class="toolbar"><button type="button" :disabled="busy || Boolean(editing.recordId)" @click="startEditing(record)">编辑</button><button type="button" class="danger" :disabled="busy || Boolean(editing.recordId)" @click="removeRecord(record)">删除</button></div></td>
                  </template>
                </tr>
              </tbody></table>
            </div>
            <div class="toolbar"><button :disabled="busy || Boolean(editing.recordId) || !canConfirmDraft" @click="confirmDraft">确认并写入</button><button class="ghost" :disabled="busy || Boolean(editing.recordId)" @click="discardDraft">丢弃草稿</button></div>
          </div>
        </article>
        <article v-if="session?.pending_approval" class="message assistant interactive-message approval-message" aria-label="写入确认">
          <div class="message-header"><strong>Agent</strong><span>需要确认</span></div>
          <div class="interactive-message-body warning">
            <h2>需要确认</h2>
            <p>{{ session.pending_approval.summary }}</p>
            <dl>
              <div v-if="session.pending_approval.product_name"><dt>产品</dt><dd>{{ session.pending_approval.product_name }}</dd></div>
              <div v-if="session.pending_approval.product_id"><dt>产品 ID</dt><dd>{{ session.pending_approval.product_id }}</dd></div>
              <div v-if="session.pending_approval.record_date"><dt>日期</dt><dd>{{ session.pending_approval.record_date }}</dd></div>
              <div v-if="session.pending_approval.record_kind"><dt>类型</dt><dd>{{ session.pending_approval.record_kind }}</dd></div>
              <div v-if="session.pending_approval.amount"><dt>金额</dt><dd>{{ session.pending_approval.amount }}</dd></div>
              <div v-if="session.pending_approval.product_type"><dt>产品类型</dt><dd>{{ session.pending_approval.product_type }}</dd></div>
              <div v-if="session.pending_approval.risk_level"><dt>风险等级</dt><dd>{{ session.pending_approval.risk_level }}</dd></div>
              <div v-if="session.pending_approval.performance_benchmark"><dt>业绩比较基准</dt><dd>{{ session.pending_approval.performance_benchmark }}</dd></div>
            </dl>
            <small>{{ session.pending_approval.reason }}</small>
            <div class="toolbar"><button :disabled="busy" @click="respondApproval('approve')">{{ session.pending_approval.tool_name === 'update_product_profile' ? '确认更新' : '确认写入' }}</button><button class="ghost" :disabled="busy" @click="respondApproval('reject')">拒绝</button></div>
          </div>
        </article>
      </div>
      <form class="composer" @submit.prevent="send"><textarea v-model="text" rows="3" maxlength="4000" placeholder="例如：列出我的产品，新增今天 100 元估值，或补充产品资料" :disabled="busy || Boolean(session?.ocr_draft) || Boolean(session?.pending_approval)"></textarea><div class="toolbar"><label class="upload">上传截图<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="busy || Boolean(session?.ocr_draft) || Boolean(session?.pending_approval)" @change="upload"></label><button :disabled="busy || !text.trim() || Boolean(session?.pending_approval)">{{ busy ? '处理中…' : '发送' }}</button></div></form>
    </section>
  </div>
</template>

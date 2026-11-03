import type { components } from "./schema";

export type ProductCatalog = components["schemas"]["ProductCatalog"];
export type PortfolioSnapshot = components["schemas"]["PortfolioSnapshot"];
export type ProductLedger = components["schemas"]["ProductLedgerView"];
export type PortfolioCommand = components["schemas"]["PortfolioCommandRequest"];
export type MutationResult = components["schemas"]["MutationResult"];
export type ProductReport = components["schemas"]["ProductReport"];
export type AnalysisResult = components["schemas"]["AnalysisResult"];
export type LatestAnalysisResult = components["schemas"]["LatestAnalysisResult"];
export type ConversationSession = components["schemas"]["ConversationSession"];
export type ConversationTurn = components["schemas"]["ConversationTurnResult"];
export type ConfirmOcrDraftResult = components["schemas"]["ConfirmOcrDraftResult"];
export type OcrDraftRecord = components["schemas"]["OcrDraftRecord"];
export type OcrDraftRecordUpdate = components["schemas"]["OcrDraftRecordUpdate"];
export type BackupList = components["schemas"]["BackupListResult"];
export type BackupManifest = components["schemas"]["BackupManifest"];
export type BackupVerification = components["schemas"]["BackupVerification"];
export type RestoreResult = components["schemas"]["RestoreResult"];
export type GovernanceEventList = components["schemas"]["ListEventsResult"];
export type EvaluationRequest = components["schemas"]["EvaluationRequest"];
export type EvaluationResult = components["schemas"]["EvaluationResult"];
export type HttpErrorBody = components["schemas"]["HttpErrorResponse"];

const API_BASE_URL = "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: HttpErrorBody,
  ) {
    super(body.error.message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  const body: unknown = await response.json();
  if (!response.ok) throw new ApiError(response.status, body as HttpErrorBody);
  return body as T;
}

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export function listProducts(): Promise<ProductCatalog> {
  return request("/api/v1/products");
}

export function getPortfolio(): Promise<PortfolioSnapshot> {
  return request("/api/v1/portfolio");
}

export function getProduct(productId: string): Promise<ProductLedger> {
  return request(`/api/v1/products/${encoded(productId)}`);
}

export function executePortfolioCommand(command: PortfolioCommand): Promise<MutationResult> {
  return request("/api/v1/portfolio/commands", {
    method: "POST",
    body: JSON.stringify(command),
  });
}

export function getProductReport(productId: string): Promise<ProductReport> {
  return request(`/api/v1/products/${encoded(productId)}/report`);
}

export function analyzeProduct(
  productId: string,
  question?: string,
): Promise<AnalysisResult> {
  return request(`/api/v1/products/${encoded(productId)}/analysis`, {
    method: "POST",
    body: JSON.stringify({ question: question ?? null }),
  });
}

export function getLatestAnalysis(
  productId: string,
  ledgerRevision?: string,
): Promise<LatestAnalysisResult> {
  const query = ledgerRevision ? `?ledger_revision=${encoded(ledgerRevision)}` : "";
  return request(
    `/api/v1/products/${encoded(productId)}/analysis/latest${query}`,
  );
}

export function createConversation(): Promise<ConversationSession> {
  return request("/api/v1/conversations", { method: "POST" });
}

export function getConversation(sessionId: string): Promise<ConversationSession> {
  return request(`/api/v1/conversations/${encoded(sessionId)}`);
}

export function resetConversation(sessionId: string): Promise<ConversationSession> {
  return request(`/api/v1/conversations/${encoded(sessionId)}/reset`, {
    method: "POST",
  });
}

export function selectConversationProduct(
  sessionId: string,
  productId: string | null,
): Promise<ConversationSession> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/active-product`,
    { method: "PATCH", body: JSON.stringify({ product_id: productId }) },
  );
}

export function sendConversationMessage(
  sessionId: string,
  content: string,
): Promise<ConversationTurn> {
  return request(`/api/v1/conversations/${encoded(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ correlation_id: crypto.randomUUID(), content }),
  });
}

export function respondConversationApproval(
  sessionId: string,
  approvalId: string,
  decision: "approve" | "reject",
): Promise<ConversationTurn> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/approvals/${encoded(approvalId)}`,
    {
      method: "POST",
      body: JSON.stringify({ decision }),
    },
  );
}

export async function createOcrDraft(
  sessionId: string,
  file: File,
  defaultDate: string,
): Promise<ConversationSession> {
  const content = await fileToBase64(file);
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/ocr-drafts`,
    {
      method: "POST",
      body: JSON.stringify({
        content_base64: content,
        mime_type: file.type,
        default_date: defaultDate,
      }),
    },
  );
}

export function confirmOcrDraft(
  sessionId: string,
  draftId: string,
  expectedRevision: string,
): Promise<ConfirmOcrDraftResult> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/ocr-drafts/confirm`,
    {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        draft_id: draftId,
        expected_revision: expectedRevision,
        idempotency_key_prefix: crypto.randomUUID(),
        confirmed: true,
      }),
    },
  );
}

export function discardOcrDraft(
  sessionId: string,
  draftId: string,
): Promise<ConversationSession> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/ocr-drafts/${encoded(draftId)}`,
    { method: "DELETE" },
  );
}

export function updateOcrDraftRecord(
  sessionId: string,
  draftId: string,
  recordId: string,
  values: OcrDraftRecordUpdate,
): Promise<ConversationSession> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/ocr-drafts/${encoded(draftId)}/records/${encoded(recordId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(values),
    },
  );
}

export function deleteOcrDraftRecord(
  sessionId: string,
  draftId: string,
  recordId: string,
): Promise<ConversationSession> {
  return request(
    `/api/v1/conversations/${encoded(sessionId)}/ocr-drafts/${encoded(draftId)}/records/${encoded(recordId)}`,
    { method: "DELETE" },
  );
}

export function listBackups(): Promise<BackupList> {
  return request("/api/v1/backups");
}

export function createBackup(): Promise<BackupManifest> {
  return request("/api/v1/backups", { method: "POST" });
}

export function verifyBackup(backupId: string): Promise<BackupVerification> {
  return request(
    `/api/v1/backups/${encoded(backupId)}/verification`,
  );
}

export function restoreBackup(
  backupId: string,
  expectedRevision: string,
): Promise<RestoreResult> {
  return request(
    `/api/v1/backups/${encoded(backupId)}/restore`,
    {
      method: "POST",
      body: JSON.stringify({
        backup_id: backupId,
        confirmation_backup_id: backupId,
        expected_revision: expectedRevision,
      }),
    },
  );
}

export function listGovernanceEvents(
  category?: "model" | "tool" | "task" | "audit" | "evaluation",
): Promise<GovernanceEventList> {
  const query = category ? `?category=${category}` : "";
  return request(`/api/v1/governance/events${query}`);
}

export function runEvaluation(input: EvaluationRequest): Promise<EvaluationResult> {
  return request("/api/v1/governance/evaluations", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary);
}

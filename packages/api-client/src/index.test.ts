import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteOcrDraftRecord,
  listProducts,
  respondConversationApproval,
  updateOcrDraftRecord,
} from "./index";

describe("API 客户端", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("请求不附加 Authorization 头", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ledger_revision: "r1", products: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await listProducts();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/v1/products");
    expect((init.headers as Headers).has("Authorization")).toBe(false);
  });

  it("确认接口只提交确认决定", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "answered", session: {} }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await respondConversationApproval("session/1", "approval/1", "approve");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/v1/conversations/session%2F1/approvals/approval%2F1",
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ decision: "approve" });
  });

  it("编辑 OCR 草稿单条记录时提交三个可编辑字段", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "session/1" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await updateOcrDraftRecord("session/1", "draft/1", "record/1", {
      product_name: "修改产品",
      record_date: "2026-09-13",
      amount: "120.50",
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/v1/conversations/session%2F1/ocr-drafts/draft%2F1/records/record%2F1",
    );
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      product_name: "修改产品",
      record_date: "2026-09-13",
      amount: "120.50",
    });
  });

  it("删除 OCR 草稿单条记录时使用记录资源路径", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ session_id: "session/1" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await deleteOcrDraftRecord("session/1", "draft/1", "record/1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/v1/conversations/session%2F1/ocr-drafts/draft%2F1/records/record%2F1",
    );
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });
});

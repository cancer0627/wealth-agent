// @vitest-environment jsdom

import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConversationSession,
  OcrDraftRecord,
} from "@wealth-agent/api-client";

const clipboard = vi.hoisted(() => ({ writeText: vi.fn() }));
const mocks = vi.hoisted(() => ({
  confirmOcrDraft: vi.fn(),
  createConversation: vi.fn(),
  createOcrDraft: vi.fn(),
  deleteOcrDraftRecord: vi.fn(),
  discardOcrDraft: vi.fn(),
  getConversation: vi.fn(),
  getPortfolio: vi.fn(),
  respondConversationApproval: vi.fn(),
  resetConversation: vi.fn(),
  sendConversationMessage: vi.fn(),
  updateOcrDraftRecord: vi.fn(),
}));

vi.mock("@wealth-agent/api-client", () => ({
  ApiError: class ApiError extends Error {
    readonly status: number;

    constructor(status: number) {
      super("API error");
      this.status = status;
    }
  },
  ...mocks,
}));

import ChatPage from "./implementation/ChatPage.vue";

const CREATED_AT = "2026-09-12T00:00:00Z";

function record(overrides: Partial<OcrDraftRecord> = {}): OcrDraftRecord {
  return {
    record_id: "record-1",
    product_name: "识别产品",
    record_date: "2026-09-12",
    amount: "88.00",
    kind: "valuation",
    confidence: { product_name: 0.9, record_date: 0.8, amount: 0.95 },
    warnings: [],
    ...overrides,
  };
}

function session(records: OcrDraftRecord[]): ConversationSession {
  return {
    session_id: "session-1",
    messages: [],
    active_product_id: null,
    ocr_draft: {
      draft_id: "draft-1",
      image_sha256: "a".repeat(64),
      mime_type: "image/png",
      width: 100,
      height: 100,
      engine: "fake",
      model: "fake",
      parser: "fake-v1",
      records,
      warnings: [],
      created_at: CREATED_AT,
    },
    agent_status: "idle",
    pending_approval: null,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  clipboard.writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: clipboard,
  });
  sessionStorage.clear();
  sessionStorage.setItem("wealth-agent-conversation-v1", "session-1");
  mocks.getConversation.mockResolvedValue(session([record()]));
});

describe("OCR 草稿记录操作", () => {
  it("把 OCR 草稿作为助手消息放入对话流，并隐藏空会话提示", async () => {
    const wrapper = mount(ChatPage);
    await flushPromises();

    expect(wrapper.find(".messages .ocr-draft-message").exists()).toBe(true);
    expect(wrapper.find(".ocr-draft-message").classes()).toEqual(
      expect.arrayContaining(["message", "assistant"]),
    );
    expect(wrapper.find(".messages").classes()).toContain("has-interactive-message");
    expect(wrapper.find(".messages .empty").exists()).toBe(false);
  });

  it("可以编辑并保存产品名、日期和金额", async () => {
    mocks.updateOcrDraftRecord.mockResolvedValue(
      session([
        record({
          product_name: "修改产品",
          record_date: "2026-09-13",
          amount: "120.50",
        }),
      ]),
    );
    const wrapper = mount(ChatPage);
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "编辑")!.trigger("click");
    await wrapper.get('input[aria-label="产品名称"]').setValue("修改产品");
    await wrapper.get('input[aria-label="记录日期"]').setValue("2026-09-13");
    await wrapper.get('input[aria-label="金额"]').setValue("120.50");
    await wrapper.findAll("button").find((button) => button.text() === "保存")!.trigger("click");
    await flushPromises();

    expect(mocks.updateOcrDraftRecord).toHaveBeenCalledWith(
      "session-1",
      "draft-1",
      "record-1",
      {
        product_name: "修改产品",
        record_date: "2026-09-13",
        amount: "120.50",
      },
    );
    expect(wrapper.text()).toContain("修改产品");
    expect(wrapper.text()).toContain("2026-09-13");
    expect(wrapper.text()).toContain("120.50");
  });

  it("删除前需要确认，并在删除最后一条后保留空草稿", async () => {
    mocks.deleteOcrDraftRecord.mockResolvedValue(session([]));
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(true);
    const wrapper = mount(ChatPage);
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "删除")!.trigger("click");
    await flushPromises();

    expect(confirmMock).toHaveBeenCalledWith("确认删除 OCR 草稿中的“识别产品”记录？");
    expect(mocks.deleteOcrDraftRecord).toHaveBeenCalledWith(
      "session-1",
      "draft-1",
      "record-1",
    );
    expect(wrapper.text()).toContain("当前草稿没有保留记录");
    expect(
      wrapper.findAll("button").find((button) => button.text() === "确认并写入")!.attributes("disabled"),
    ).toBeDefined();
    confirmMock.mockRestore();
  });

  it("取消删除确认时不调用后端", async () => {
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mount(ChatPage);
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "删除")!.trigger("click");

    expect(confirmMock).toHaveBeenCalledOnce();
    expect(mocks.deleteOcrDraftRecord).not.toHaveBeenCalled();
    confirmMock.mockRestore();
  });
});

describe("会话消息复制", () => {
  it("可以分别复制问题和答案，并显示复制成功状态", async () => {
    mocks.getConversation.mockResolvedValue({
      ...session([]),
      messages: [
        {
          message_id: "message-question",
          role: "user",
          content: "有哪些产品？",
          created_at: CREATED_AT,
        },
        {
          message_id: "message-answer",
          role: "assistant",
          content: "当前有 2 个产品。",
          created_at: CREATED_AT,
        },
      ],
      ocr_draft: null,
    });
    const wrapper = mount(ChatPage);
    await flushPromises();

    expect(wrapper.find(".message.user .message-copy-button").exists()).toBe(false);
    expect(wrapper.find(".message.assistant .message-copy-button").exists()).toBe(false);
    expect(wrapper.find(".message-item.user .message-copy-button").exists()).toBe(true);
    expect(wrapper.find(".message-item.assistant .message-copy-button").exists()).toBe(true);

    await wrapper.get('button[aria-label="复制问题"]').trigger("click");
    await flushPromises();
    expect(clipboard.writeText).toHaveBeenCalledWith("有哪些产品？");
    expect(wrapper.get('button[aria-label="复制问题"]').text()).toBe("已复制");

    await wrapper.get('button[aria-label="复制答案"]').trigger("click");
    await flushPromises();
    expect(clipboard.writeText).toHaveBeenLastCalledWith("当前有 2 个产品。");
    expect(wrapper.get('button[aria-label="复制答案"]').text()).toBe("已复制");
  });

  it("剪贴板权限失败时显示错误提示", async () => {
    clipboard.writeText.mockRejectedValue(new Error("permission denied"));
    mocks.getConversation.mockResolvedValue({
      ...session([]),
      messages: [
        {
          message_id: "message-question",
          role: "user",
          content: "有哪些产品？",
          created_at: CREATED_AT,
        },
      ],
      ocr_draft: null,
    });
    const wrapper = mount(ChatPage);
    await flushPromises();

    await wrapper.get('button[aria-label="复制问题"]').trigger("click");
    await flushPromises();

    expect(wrapper.find(".notice.error").text()).toBe("复制失败，请检查浏览器剪贴板权限");
  });
});

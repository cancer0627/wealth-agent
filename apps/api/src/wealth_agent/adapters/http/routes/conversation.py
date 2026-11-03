"""Conversation and OCR HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Path

from wealth_agent.adapters.http.contracts import (
    ApprovalHttpRequest,
    CreateOcrDraftHttpRequest,
    SelectProductHttpRequest,
    SendMessageHttpRequest,
)
from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.adapters.http.errors import HttpApiError
from wealth_agent.capabilities.conversation.contract import (
    ConfirmOcrDraftRequest,
    ConfirmOcrDraftResult,
    ConversationSession,
    ConversationTurnResult,
    CreateOcrDraftRequest,
    CreateSessionRequest,
    DeleteOcrDraftRecordRequest,
    DiscardOcrDraftRequest,
    GetSessionRequest,
    OcrDraftRecordUpdate,
    OcrExtractionRequest,
    ResetSessionRequest,
    RespondApprovalRequest,
    SelectProductRequest,
    SendAgentMessageRequest,
    UpdateOcrDraftRecordRequest,
)
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_call


def build_conversation_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/conversations")
    @trace_call(layer="adapter")
    def create_conversation() -> ConversationSession:
        return dependencies.conversation.create(CreateSessionRequest())

    @router.get("/api/v1/conversations/{session_id}")
    @trace_call(layer="adapter")
    def get_conversation(session_id: str = Path(min_length=1)) -> ConversationSession:
        return dependencies.conversation.get(GetSessionRequest(session_id=session_id))

    @router.post("/api/v1/conversations/{session_id}/reset")
    @trace_call(layer="adapter")
    def reset_conversation(session_id: str = Path(min_length=1)) -> ConversationSession:
        return dependencies.conversation.reset(ResetSessionRequest(session_id=session_id))

    @router.patch("/api/v1/conversations/{session_id}/active-product")
    @trace_call(layer="adapter")
    def select_product(
        request: SelectProductHttpRequest,
        session_id: str = Path(min_length=1),
    ) -> ConversationSession:
        return dependencies.conversation.select_product(
            SelectProductRequest(
                session_id=session_id,
                product_id=request.product_id,
            )
        )

    @router.post("/api/v1/conversations/{session_id}/messages")
    @trace_call(layer="adapter")
    def send_message(
        request: SendMessageHttpRequest,
        session_id: str = Path(min_length=1),
    ) -> ConversationTurnResult:
        return dependencies.conversation.send_agent_message(
            SendAgentMessageRequest(
                session_id=session_id,
                correlation_id=request.correlation_id,
                content=request.content,
                config=dependencies.agent_config,
            )
        )

    @router.post("/api/v1/conversations/{session_id}/approvals/{approval_id}")
    @trace_call(layer="adapter")
    def respond_approval(
        request: ApprovalHttpRequest,
        session_id: str = Path(min_length=1),
        approval_id: str = Path(min_length=1),
    ) -> ConversationTurnResult:
        return dependencies.conversation.respond_approval(
            RespondApprovalRequest(
                session_id=session_id,
                approval_id=approval_id,
                decision=request.decision,
                config=dependencies.agent_config,
            )
        )

    @router.post("/api/v1/conversations/{session_id}/ocr-drafts")
    @trace_call(layer="adapter")
    def create_ocr_draft(
        request: CreateOcrDraftHttpRequest,
        session_id: str = Path(min_length=1),
    ) -> ConversationSession:
        try:
            content = request.decoded_content()
        except ValueError as exc:
            raise HttpApiError(
                422,
                ErrorDetail(code="invalid_image_payload", message=str(exc)),
            ) from exc
        return dependencies.conversation.create_ocr_draft(
            CreateOcrDraftRequest(
                session_id=session_id,
                extraction=OcrExtractionRequest(
                    content=content,
                    mime_type=request.mime_type,
                    default_date=request.default_date,
                ),
            )
        )

    @router.post("/api/v1/conversations/{session_id}/ocr-drafts/confirm")
    @trace_call(layer="adapter")
    def confirm_ocr_draft(
        request: ConfirmOcrDraftRequest,
        session_id: str = Path(min_length=1),
    ) -> ConfirmOcrDraftResult:
        if request.session_id != session_id:
            raise HttpApiError(
                422,
                ErrorDetail(
                    code="session_id_mismatch",
                    message="路径与请求中的会话标识不一致",
                ),
            )
        return dependencies.conversation.confirm_ocr_draft(request)

    @router.patch(
        "/api/v1/conversations/{session_id}/ocr-drafts/{draft_id}/records/{record_id}"
    )
    @trace_call(layer="adapter")
    def update_ocr_draft_record(
        request: OcrDraftRecordUpdate,
        session_id: str = Path(min_length=1),
        draft_id: str = Path(min_length=1),
        record_id: str = Path(min_length=1),
    ) -> ConversationSession:
        return dependencies.conversation.update_ocr_draft_record(
            UpdateOcrDraftRecordRequest(
                session_id=session_id,
                draft_id=draft_id,
                record_id=record_id,
                values=request,
            )
        )

    @router.delete(
        "/api/v1/conversations/{session_id}/ocr-drafts/{draft_id}/records/{record_id}"
    )
    @trace_call(layer="adapter")
    def delete_ocr_draft_record(
        session_id: str = Path(min_length=1),
        draft_id: str = Path(min_length=1),
        record_id: str = Path(min_length=1),
    ) -> ConversationSession:
        return dependencies.conversation.delete_ocr_draft_record(
            DeleteOcrDraftRecordRequest(
                session_id=session_id,
                draft_id=draft_id,
                record_id=record_id,
            )
        )

    @router.delete("/api/v1/conversations/{session_id}/ocr-drafts/{draft_id}")
    @trace_call(layer="adapter")
    def discard_ocr_draft(session_id: str, draft_id: str) -> ConversationSession:
        return dependencies.conversation.discard_ocr_draft(
            DiscardOcrDraftRequest(session_id=session_id, draft_id=draft_id)
        )

    return router


__all__ = ["build_conversation_router"]

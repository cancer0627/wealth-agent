from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.capabilities.conversation.contract import (
    AppendMessageRequest,
    CreateOcrDraftRequest,
    CreateSessionRequest,
    DeleteOcrDraftRecordRequest,
    DiscardOcrDraftRequest,
    GetSessionRequest,
    OcrDraftRecordUpdate,
    OcrExtraction,
    OcrExtractionRequest,
    OcrFieldConfidence,
    OcrRecordCandidate,
    PendingOcrDraftError,
    ResetSessionRequest,
    SessionNotFoundError,
    UpdateOcrDraftRecordRequest,
)
from wealth_agent.capabilities.conversation.implementation import (
    InMemoryConversationService,
    SqliteConversationService,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class StubOcrEngine:
    def extract(self, request: OcrExtractionRequest) -> OcrExtraction:
        assert request.content == b"image"
        return OcrExtraction(
            image_sha256="a" * 64,
            mime_type=request.mime_type,
            width=1080,
            height=1920,
            orientation_degrees=0,
            engine="stub",
            model="stub-v1",
            parser="portfolio-v1",
            records=[
                OcrRecordCandidate(
                    product_name="测试产品",
                    record_date=request.default_date,
                    amount=Decimal("100"),
                    confidence=OcrFieldConfidence(
                        product_name=0.9,
                        record_date=0.8,
                        amount=0.95,
                    ),
                )
            ],
        )


def test_session_messages_use_structured_requests_and_defensive_results() -> None:
    service = InMemoryConversationService(FixedClock())
    created = service.create(CreateSessionRequest())
    appended = service.append(
        AppendMessageRequest(
            session_id=created.session_id,
            role="user",
            content="  查看资产  ",
        )
    )

    assert appended.messages[0].content == "查看资产"
    appended.messages.clear()
    stored = service.get(GetSessionRequest(session_id=created.session_id))
    assert len(stored.messages) == 1

    reset = service.reset(ResetSessionRequest(session_id=created.session_id))
    assert reset.messages == []


def test_missing_session_returns_structured_error() -> None:
    service = InMemoryConversationService(FixedClock())
    with pytest.raises(SessionNotFoundError) as error:
        service.get(GetSessionRequest(session_id="missing"))
    assert error.value.detail.code == "session_not_found"
    assert error.value.detail.details == {"session_id": "missing"}


def test_ocr_draft_is_single_pending_state_and_can_be_discarded() -> None:
    service = InMemoryConversationService(FixedClock(), StubOcrEngine())
    session = service.create(CreateSessionRequest())
    request = CreateOcrDraftRequest(
        session_id=session.session_id,
        extraction=OcrExtractionRequest(
            content=b"image",
            mime_type="image/png",
            default_date=date(2026, 9, 12),
        ),
    )

    with_draft = service.create_ocr_draft(request)
    assert with_draft.ocr_draft is not None
    assert with_draft.ocr_draft.records[0].product_name == "测试产品"

    with pytest.raises(PendingOcrDraftError) as error:
        service.create_ocr_draft(request)
    assert error.value.detail.code == "pending_ocr_draft"

    discarded = service.discard_ocr_draft(
        DiscardOcrDraftRequest(
            session_id=session.session_id,
            draft_id=with_draft.ocr_draft.draft_id,
        )
    )
    assert discarded.ocr_draft is None


def test_ocr_draft_record_can_be_updated_and_deleted_individually() -> None:
    service = InMemoryConversationService(FixedClock(), TwoRecordOcrEngine())
    session = service.create(CreateSessionRequest())
    drafted = service.create_ocr_draft(
        CreateOcrDraftRequest(
            session_id=session.session_id,
            extraction=OcrExtractionRequest(
                content=b"image",
                mime_type="image/png",
                default_date=date(2026, 9, 12),
            ),
        )
    )
    assert drafted.ocr_draft is not None
    first, second = drafted.ocr_draft.records

    updated = service.update_ocr_draft_record(
        UpdateOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=first.record_id,
            values=OcrDraftRecordUpdate(
                product_name="  修改后的产品  ",
                record_date=date(2026, 9, 13),
                amount=Decimal("120.50"),
            ),
        )
    )
    assert updated.ocr_draft is not None
    edited = updated.ocr_draft.records[0]
    assert edited.record_id == first.record_id
    assert edited.product_name == "修改后的产品"
    assert edited.record_date == date(2026, 9, 13)
    assert edited.amount == Decimal("120.50")
    assert edited.kind == first.kind
    assert edited.confidence == first.confidence
    assert edited.warnings == first.warnings

    updated.ocr_draft.records[0].product_name = "外部修改"
    stored = service.get(GetSessionRequest(session_id=session.session_id))
    assert stored.ocr_draft is not None
    assert stored.ocr_draft.records[0].product_name == "修改后的产品"

    deleted = service.delete_ocr_draft_record(
        DeleteOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=first.record_id,
        )
    )
    assert deleted.ocr_draft is not None
    assert [record.record_id for record in deleted.ocr_draft.records] == [second.record_id]

    empty = service.delete_ocr_draft_record(
        DeleteOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=second.record_id,
        )
    )
    assert empty.ocr_draft is not None
    assert empty.ocr_draft.records == []

    with pytest.raises(PendingOcrDraftError) as error:
        service.delete_ocr_draft_record(
            DeleteOcrDraftRecordRequest(
                session_id=session.session_id,
                draft_id=drafted.ocr_draft.draft_id,
                record_id=second.record_id,
            )
        )
    assert error.value.detail.code == "pending_ocr_draft"
    assert error.value.detail.details["record_id"] == second.record_id


def test_ocr_draft_record_update_validates_and_normalizes_values() -> None:
    values = OcrDraftRecordUpdate(
        product_name="  产品  ",
        record_date=date(2026, 9, 12),
        amount=Decimal("10.00"),
    )
    assert values.product_name == "产品"
    assert values.amount == Decimal("10.00")

    with pytest.raises(ValueError):
        OcrDraftRecordUpdate(
            product_name=" ",
            record_date=date(2026, 9, 12),
            amount=Decimal("10.00"),
        )
    with pytest.raises(ValueError):
        OcrDraftRecordUpdate(
            product_name="产品",
            record_date=date(2026, 9, 12),
            amount=Decimal("10.001"),
        )


def test_sqlite_ocr_draft_record_changes_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "conversation.db"
    first_service = SqliteConversationService(database, FixedClock(), TwoRecordOcrEngine())
    session = first_service.create(CreateSessionRequest())
    drafted = first_service.create_ocr_draft(
        CreateOcrDraftRequest(
            session_id=session.session_id,
            extraction=OcrExtractionRequest(
                content=b"image",
                mime_type="image/png",
                default_date=date(2026, 9, 12),
            ),
        )
    )
    assert drafted.ocr_draft is not None
    first, second = drafted.ocr_draft.records

    first_service.update_ocr_draft_record(
        UpdateOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=first.record_id,
            values=OcrDraftRecordUpdate(
                product_name="持久化产品",
                record_date=date(2026, 9, 14),
                amount=Decimal("200.00"),
            ),
        )
    )
    first_service.delete_ocr_draft_record(
        DeleteOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=second.record_id,
        )
    )

    resumed = SqliteConversationService(database, FixedClock(), TwoRecordOcrEngine())
    restored = resumed.get(GetSessionRequest(session_id=session.session_id))
    assert restored.ocr_draft is not None
    assert len(restored.ocr_draft.records) == 1
    assert restored.ocr_draft.records[0].product_name == "持久化产品"
    assert restored.ocr_draft.records[0].record_date == date(2026, 9, 14)
    assert restored.ocr_draft.records[0].amount == Decimal("200.00")


class TwoRecordOcrEngine(StubOcrEngine):
    def extract(self, request: OcrExtractionRequest) -> OcrExtraction:
        result = super().extract(request)
        return result.model_copy(
            update={
                "records": [
                    result.records[0].model_copy(update={"warnings": ["请核对"]}),
                    result.records[0].model_copy(
                        update={
                            "product_name": "第二个产品",
                            "amount": Decimal("50.00"),
                        }
                    ),
                ]
            }
        )

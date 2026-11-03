import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from wealth_agent.adapters.http import create_http_app
from wealth_agent.bootstrap.application import (
    ApplicationConfig,
    BuildApplicationRequest,
    build_application,
    build_http_dependencies,
)
from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.capabilities.conversation.contract import (
    OcrExtraction,
    OcrExtractionRequest,
    OcrFieldConfidence,
    OcrRecordCandidate,
)
from wealth_agent.shared.model import ModelRequest, ModelResponse


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 12, tzinfo=UTC)


class UnusedGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise AssertionError("本测试不应调用模型")


class FakeOcr:
    def extract(self, request: OcrExtractionRequest) -> OcrExtraction:
        return OcrExtraction(
            image_sha256="a" * 64,
            mime_type=request.mime_type,
            width=100,
            height=100,
            orientation_degrees=0,
            engine="fake",
            model="fake",
            parser="fake-v1",
            records=[
                OcrRecordCandidate(
                    product_name="接口 OCR 产品",
                    record_date=request.default_date,
                    amount=Decimal("88.00"),
                    confidence=OcrFieldConfidence(
                        product_name=1,
                        record_date=1,
                        amount=1,
                    ),
                )
            ],
        )


def _client(tmp_path: Path, ocr_engine: FakeOcr | None = None) -> TestClient:
    bundle = build_application(
        BuildApplicationRequest(
            config=ApplicationConfig(
                data_dir=tmp_path,
                logs_dir=tmp_path / "logs",
                analysis_provider="fake",
                analysis_model="analysis-model",
                agent=AgentModelConfig(model="agent-model"),
            ),
            model_gateway=UnusedGateway(),
            clock=FixedClock(),
            ocr_engine=ocr_engine,
        )
    )
    return TestClient(create_http_app(build_http_dependencies(bundle)))


def test_http_routes_are_available_without_tokens(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        products = client.get("/api/v1/products")
        backup = client.post("/api/v1/backups")
        tasks = client.get("/api/v1/tasks")

    assert products.status_code == 200
    assert products.json()["products"] == []
    assert backup.status_code == 200
    assert tasks.status_code == 404


def test_http_and_capability_calls_share_internal_trace(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/portfolio")

    assert response.status_code == 200
    events = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("wealth-agent-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    calls = [
        event
        for event in events
        if event["event"] == "method_call"
        and event["method"]
        in {
            "build_portfolio_router.<locals>.portfolio_snapshot",
            "SqlitePortfolioReader.snapshot",
        }
    ]
    assert len(calls) == 2
    assert calls[0]["trace_id"] == calls[1]["trace_id"]
    assert calls[1]["parent_call_id"] == calls[0]["call_id"]


def test_http_write_backup_and_openapi_contracts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        snapshot = client.get("/api/v1/portfolio").json()
        written = client.post(
            "/api/v1/portfolio/commands",
            json={
                "kind": "add_record",
                "product_name": "接口产品",
                "record_date": "2026-09-12",
                "record_kind": "valuation",
                "amount": "100.00",
                "source_text": "接口测试",
                "expected_revision": snapshot["revision"],
                "idempotency_key": "http-write-1",
            },
        )
        backup = client.post("/api/v1/backups")
        schema = client.get("/openapi.json").json()

    assert written.status_code == 200
    assert written.json()["changed"] is True
    assert backup.status_code == 200
    assert backup.json()["ledger_revision"] == written.json()["ledger_revision"]
    assert schema["paths"]["/api/v1/portfolio/commands"]["post"]["requestBody"]
    assert "securitySchemes" not in schema["components"]
    assert "MutationResult" in schema["components"]["schemas"]
    assert "/api/v1/tasks" not in schema["paths"]
    assert (
        "/api/v1/conversations/{session_id}/approvals/{approval_id}"
        in schema["paths"]
    )
    assert "securitySchemes" not in schema["components"]


def test_http_ocr_draft_record_can_be_updated_and_deleted(tmp_path: Path) -> None:
    with _client(tmp_path, FakeOcr()) as client:
        session = client.post("/api/v1/conversations").json()
        drafted = client.post(
            f"/api/v1/conversations/{session['session_id']}/ocr-drafts",
            json={
                "content_base64": "ZmFrZS1pbWFnZQ==",
                "mime_type": "image/png",
                "default_date": "2026-09-12",
            },
        ).json()
        draft = drafted["ocr_draft"]
        record_id = draft["records"][0]["record_id"]
        path = (
            f"/api/v1/conversations/{session['session_id']}"
            f"/ocr-drafts/{draft['draft_id']}/records/{record_id}"
        )

        updated = client.patch(
            path,
            json={
                "product_name": "接口修改产品",
                "record_date": "2026-09-13",
                "amount": "99.00",
            },
        )
        invalid = client.patch(
            path,
            json={
                "product_name": " ",
                "record_date": "2026-09-13",
                "amount": "99.00",
            },
        )
        missing = client.delete(f"{path}-missing")
        deleted = client.delete(path)
        schema = client.get("/openapi.json").json()

    assert updated.status_code == 200
    updated_record = updated.json()["ocr_draft"]["records"][0]
    assert updated_record["product_name"] == "接口修改产品"
    assert updated_record["record_date"] == "2026-09-13"
    assert updated_record["amount"] == "99.00"
    assert invalid.status_code == 422
    assert missing.status_code == 409
    assert deleted.status_code == 200
    assert deleted.json()["ocr_draft"]["records"] == []
    assert (
        "/api/v1/conversations/{session_id}/ocr-drafts/{draft_id}/records/{record_id}"
        in schema["paths"]
    )
    path_template = (
        "/api/v1/conversations/{session_id}/ocr-drafts/{draft_id}/records/{record_id}"
    )
    assert "patch" in schema["paths"][path_template]
    assert "delete" in schema["paths"][path_template]

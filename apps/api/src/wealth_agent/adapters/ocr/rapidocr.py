"""Lazy-loaded local RapidOCR adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from importlib import import_module
from io import BytesIO
from typing import Any, Literal, cast

from wealth_agent.adapters.ocr.platform_parser import (
    PARSER_VERSION,
    OcrObservation,
    parse_records,
)
from wealth_agent.capabilities.conversation.contract import (
    OcrExtraction,
    OcrExtractionRequest,
    OcrRecordCandidate,
)
from wealth_agent.shared.logging import trace_public_methods

_SUPPORTED = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


@trace_public_methods(layer="adapter")
class RapidOcrAdapter:
    def __init__(self, recognizer: Callable[[Any], Any] | None = None) -> None:
        self._recognizer = recognizer

    def extract(self, request: OcrExtractionRequest) -> OcrExtraction:
        if len(request.content) > 15 * 1024 * 1024:
            raise ValueError("图片不得超过 15 MiB")
        image_module = import_module("PIL.Image")
        try:
            image = image_module.open(BytesIO(request.content))
            image.load()
        except Exception as exc:
            raise ValueError("图片无法解码") from exc
        if image.format != _SUPPORTED[request.mime_type]:
            raise ValueError("图片内容与 MIME 类型不一致")
        width, height = image.size
        if width * height > 25_000_000:
            raise ValueError("图片解码后不得超过 25 MP")
        recognizer = self._recognizer or self._load_recognizer()
        numpy = import_module("numpy")
        image = image.convert("RGB")
        best: tuple[int, list[OcrObservation], list[OcrRecordCandidate], list[str]] | None = None
        best_degrees = 0
        for degrees in (0, 90, 180, 270):
            rotated = image.rotate(-degrees, expand=True)
            observations = self._recognize(recognizer, numpy.asarray(rotated))
            records, warnings = parse_records(
                observations,
                width=rotated.width,
                height=rotated.height,
                default_date=request.default_date,
            )
            text = "\n".join(item.text for item in observations)
            keyword_score = sum(
                1 for keyword in ("持仓明细", "资产明细", "产品名称", "持仓金额") if keyword in text
            )
            score = len(records) * 100 + keyword_score * 10 + len(observations)
            if best is None or score > best[0]:
                best = (score, observations, records, warnings)
                best_degrees = degrees
        if best is None:
            raise RuntimeError("OCR 未返回结果")
        _, best_observations, records, warnings = best
        best_image = image.rotate(-best_degrees, expand=True)
        enhanced_observations = self._reocr_product_name_column(
            recognizer,
            best_image,
            best_observations,
            numpy,
        )
        if enhanced_observations is not best_observations:
            enhanced_records, enhanced_warnings = parse_records(
                enhanced_observations,
                width=best_image.width,
                height=best_image.height,
                default_date=request.default_date,
            )
            if len(enhanced_records) >= len(records):
                records, warnings = enhanced_records, enhanced_warnings
        return OcrExtraction(
            image_sha256=hashlib.sha256(request.content).hexdigest(),
            mime_type=request.mime_type,
            width=width,
            height=height,
            orientation_degrees=cast(Literal[0, 90, 180, 270], best_degrees),
            engine="rapidocr-onnxruntime",
            model="PP-OCRv6-small",
            parser=PARSER_VERSION,
            records=records,
            warnings=warnings,
        )

    @staticmethod
    def _reocr_product_name_column(
        recognizer: Callable[[Any], Any],
        image: Any,
        observations: list[OcrObservation],
        numpy: Any,
    ) -> list[OcrObservation]:
        name_header = next(
            (item for item in observations if "产品名称" in item.text), None
        )
        amount_header = next(
            (item for item in observations if "持仓金额" in item.text), None
        )
        if name_header is None or amount_header is None:
            return observations

        margin = max(24.0, min(80.0, (name_header.right - name_header.left) * 0.75))
        left = max(0, int(name_header.left - margin))
        right = min(image.width, int(name_header.right + margin))
        top = min(image.height, max(0, int(name_header.bottom)))
        if right <= left or top >= image.height:
            return observations

        cropped = image.crop((left, top, right, image.height))
        cropped_observations = RapidOcrAdapter._recognize(
            recognizer, numpy.asarray(cropped)
        )
        if not cropped_observations:
            return observations

        translated = [
            OcrObservation(
                text=item.text,
                confidence=item.confidence,
                left=item.left + left,
                top=item.top + top,
                right=item.right + left,
                bottom=item.bottom + top,
            )
            for item in cropped_observations
        ]
        name_left = left
        name_right = right
        retained = [
            item
            for item in observations
            if not (
                item.center_y > name_header.bottom
                and name_left <= item.center_x <= name_right
            )
        ]
        return retained + translated

    @staticmethod
    def _load_recognizer() -> Callable[[Any], Any]:
        try:
            module = import_module("rapidocr")
            return cast(Callable[[Any], Any], module.RapidOCR())
        except (ImportError, AttributeError) as exc:
            raise RuntimeError("本地 OCR 运行时不可用") from exc

    @staticmethod
    def _recognize(recognizer: Callable[[Any], Any], image: Any) -> list[OcrObservation]:
        output = recognizer(image)
        if hasattr(output, "txts"):
            values = list(output.txts) if output.txts is not None else []
            scores = list(output.scores) if output.scores is not None else []
            boxes = list(output.boxes) if output.boxes is not None else []
            return [
                RapidOcrAdapter._observation(box, str(text), float(score))
                for box, text, score in zip(boxes, values, scores, strict=False)
            ]
        raw = output[0] if isinstance(output, tuple) else output
        if raw is None:
            return []
        return [
            RapidOcrAdapter._observation(item[0], str(item[1]), float(item[2]))
            for item in raw
            if isinstance(item, (list, tuple)) and len(item) >= 3
        ]

    @staticmethod
    def _observation(box: Any, text: str, confidence: float) -> OcrObservation:
        points = list(box)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return OcrObservation(
            text=text,
            confidence=max(0.0, min(1.0, confidence)),
            left=min(xs),
            top=min(ys),
            right=max(xs),
            bottom=max(ys),
        )

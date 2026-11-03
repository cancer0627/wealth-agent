"""Parse positioned OCR observations into valuation candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from wealth_agent.capabilities.conversation.contract import (
    OcrFieldConfidence,
    OcrRecordCandidate,
)

PARSER_VERSION = "wealth-screenshot-v2"
_DATE = re.compile(r"(20\d{2})[\u5e74./-](\d{1,2})[\u6708./-](\d{1,2})")
_MONEY = re.compile(r"(?<![\d.])((?:[1-9]\d{0,2}(?:,\d{3})+|\d+)\.\d{2})(?!\d)")
_BRANDING_TERMS = (
    "招商银行",
    "商银行",
    "理财月度账单",
    "CHINA MERCHANTS BANK",
    "MERCHANTS BANK",
)


@dataclass(frozen=True, slots=True)
class OcrObservation:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def parse_records(
    observations: list[OcrObservation],
    *,
    width: int,
    height: int,
    default_date: date,
) -> tuple[list[OcrRecordCandidate], list[str]]:
    page_date, date_confidence, date_warning = _page_date(observations, default_date, height=height)
    texts = "\n".join(item.text for item in observations)
    if "\u6301\u4ed3\u660e\u7ec6" in texts or (
        "\u4ea7\u54c1\u540d\u79f0" in texts and "\u6301\u4ed3\u91d1\u989d" in texts
    ):
        records = _parse_table(
            observations,
            width=width,
            page_date=page_date,
            date_confidence=date_confidence,
            date_warning=date_warning,
        )
    elif "\u8d44\u4ea7\u660e\u7ec6" in texts:
        records = _parse_asset_details(
            observations,
            height=height,
            page_date=page_date,
            date_confidence=date_confidence,
            date_warning=date_warning,
        )
    else:
        records = _parse_generic(
            observations,
            page_date=page_date,
            date_confidence=date_confidence,
            date_warning=date_warning,
        )
    warnings = [date_warning] if date_warning else []
    if not records:
        warnings.append(
            "\u672a\u80fd\u53ef\u9760\u914d\u5bf9\u4ea7\u54c1\u540d\u79f0\u548c\u91d1\u989d"
        )
    return records, warnings


def _page_date(
    observations: list[OcrObservation], default_date: date, *, height: int
) -> tuple[date, float, str | None]:
    candidates: list[tuple[OcrObservation, date]] = []
    low_confidence = False
    for item in observations:
        match = _DATE.search(item.text)
        if match is None:
            continue
        try:
            parsed = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        candidates.append((item, parsed))

    header = next(
        (
            item
            for item in observations
            if "\u6301\u4ed3\u660e\u7ec6" in item.text or "\u8d44\u4ea7\u660e\u7ec6" in item.text
        ),
        None,
    )
    if header is not None:
        nearby = [
            candidate
            for candidate in candidates
            if abs(candidate[0].center_y - header.center_y) <= max(24.0, height * 0.05)
        ]
        if nearby:
            date_item, parsed = min(
                nearby,
                key=lambda candidate: (
                    abs(candidate[0].center_y - header.center_y),
                    0 if candidate[0].center_x >= header.center_x else 1,
                    candidate[0].left,
                ),
            )
            if date_item.confidence >= 0.8:
                return parsed, date_item.confidence, None
            low_confidence = True
        reason = (
            "\u660e\u7ec6\u65e5\u671f\u7f6e\u4fe1\u5ea6\u8f83\u4f4e"
            if low_confidence
            else "\u672a\u53ef\u9760\u8bc6\u522b\u660e\u7ec6\u65e5\u671f"
        )
        return (
            default_date,
            0.0,
            f"{reason}\uff0c\u5df2\u4f7f\u7528\u8bf7\u6c42\u9ed8\u8ba4\u65e5\u671f",
        )

    for item, parsed in sorted(candidates, key=lambda candidate: candidate[0].top):
        if item.confidence >= 0.8:
            return parsed, item.confidence, None
        low_confidence = True
    reason = (
        "\u65e5\u671f\u7f6e\u4fe1\u5ea6\u8f83\u4f4e"
        if low_confidence
        else "\u672a\u53ef\u9760\u8bc6\u522b\u65e5\u671f"
    )
    return (
        default_date,
        0.0,
        f"{reason}\uff0c\u5df2\u4f7f\u7528\u8bf7\u6c42\u9ed8\u8ba4\u65e5\u671f",
    )


def _parse_table(
    observations: list[OcrObservation],
    *,
    width: int,
    page_date: date,
    date_confidence: float,
    date_warning: str | None,
) -> list[OcrRecordCandidate]:
    name_header = next(
        (item for item in observations if "\u4ea7\u54c1\u540d\u79f0" in item.text), None
    )
    amount_header = next(
        (item for item in observations if "\u6301\u4ed3\u91d1\u989d" in item.text), None
    )
    if name_header is None or amount_header is None:
        return _parse_generic(
            observations,
            page_date=page_date,
            date_confidence=date_confidence,
            date_warning=date_warning,
        )
    header_y = max(name_header.center_y, amount_header.center_y)
    amounts = [
        item
        for item in observations
        if item.center_y > header_y
        and abs(item.center_x - amount_header.center_x) <= width * 0.06
        and _money(item.text) is not None
    ]
    names = [
        item
        for item in observations
        if item.center_y > header_y
        and abs(item.center_x - name_header.center_x) <= width * 0.07
        and _is_product_name_fragment(item.text)
    ]
    ordered_amounts = sorted(amounts, key=lambda item: item.center_y)
    max_name_row_distance = _table_name_row_distance(ordered_amounts, width=width)
    chunks: dict[int, list[OcrObservation]] = {index: [] for index in range(len(ordered_amounts))}
    for name in names:
        if not ordered_amounts:
            break
        nearest_distance = min(
            abs(amount.center_y - name.center_y) for amount in ordered_amounts
        )
        if nearest_distance > max_name_row_distance:
            continue
        index = min(
            range(len(ordered_amounts)),
            key=lambda candidate: abs(ordered_amounts[candidate].center_y - name.center_y),
        )
        chunks[index].append(name)

    records: list[OcrRecordCandidate] = []
    for index, amount_item in enumerate(ordered_amounts):
        row_chunks = sorted(chunks[index], key=lambda item: (item.center_y, item.left))
        amount = _money(amount_item.text)
        if not row_chunks or amount is None:
            continue
        product_confidence = min(item.confidence for item in row_chunks)
        records.append(
            _record(
                product_name="".join(_clean_product_name(item.text) for item in row_chunks),
                product_confidence=product_confidence,
                amount=amount,
                amount_confidence=amount_item.confidence,
                page_date=page_date,
                date_confidence=date_confidence,
                date_warning=date_warning,
            )
        )
    return records


def _table_name_row_distance(
    amounts: list[OcrObservation], *, width: int
) -> float:
    if len(amounts) > 1:
        row_gaps = [
            next_item.center_y - current_item.center_y
            for current_item, next_item in zip(amounts, amounts[1:], strict=False)
            if next_item.center_y > current_item.center_y
        ]
        if row_gaps:
            return max(24.0, min(48.0, min(row_gaps) * 0.75))
    return max(24.0, min(48.0, width * 0.03))


def _parse_asset_details(
    observations: list[OcrObservation],
    *,
    height: int,
    page_date: date,
    date_confidence: float,
    date_warning: str | None,
) -> list[OcrRecordCandidate]:
    header = next((item for item in observations if "\u8d44\u4ea7\u660e\u7ec6" in item.text), None)
    start_y = header.center_y if header is not None else height * 0.45
    return _pair_candidates(
        [item for item in observations if item.center_y > start_y and _is_product_name(item.text)],
        [
            item
            for item in observations
            if item.center_y > start_y and _money(item.text) is not None
        ],
        page_date=page_date,
        date_confidence=date_confidence,
        date_warning=date_warning,
        max_row_distance=max(20.0, min(64.0, height * 0.02)),
    )


def _parse_generic(
    observations: list[OcrObservation],
    *,
    page_date: date,
    date_confidence: float,
    date_warning: str | None,
) -> list[OcrRecordCandidate]:
    return _pair_candidates(
        [item for item in observations if _is_product_name(item.text)],
        [item for item in observations if _money(item.text) is not None],
        page_date=page_date,
        date_confidence=date_confidence,
        date_warning=date_warning,
    )


def _pair_candidates(
    names: list[OcrObservation],
    amounts: list[OcrObservation],
    *,
    page_date: date,
    date_confidence: float,
    date_warning: str | None,
    max_row_distance: float | None = None,
) -> list[OcrRecordCandidate]:
    records: list[OcrRecordCandidate] = []
    used_names: set[int] = set()
    for amount_item in sorted(amounts, key=lambda item: item.center_y):
        available = [(index, item) for index, item in enumerate(names) if index not in used_names]
        if max_row_distance is not None:
            available = [
                pair
                for pair in available
                if abs(pair[1].center_y - amount_item.center_y) <= max_row_distance
            ]
        if not available:
            if max_row_distance is None:
                break
            continue
        index, name_item = min(
            available,
            key=lambda pair: (
                abs(pair[1].center_y - amount_item.center_y),
                abs(pair[1].center_x - amount_item.center_x),
            ),
        )
        used_names.add(index)
        amount = _money(amount_item.text)
        if amount is not None:
            records.append(
                _record(
                    product_name=_clean_product_name(name_item.text),
                    product_confidence=name_item.confidence,
                    amount=amount,
                    amount_confidence=amount_item.confidence,
                    page_date=page_date,
                    date_confidence=date_confidence,
                    date_warning=date_warning,
                )
            )
    return records


def _record(
    *,
    product_name: str,
    product_confidence: float,
    amount: Decimal,
    amount_confidence: float,
    page_date: date,
    date_confidence: float,
    date_warning: str | None,
) -> OcrRecordCandidate:
    warnings = [date_warning] if date_warning else []
    if product_confidence < 0.8:
        warnings.append(
            "\u4ea7\u54c1\u540d\u79f0\u8bc6\u522b\u7f6e\u4fe1\u5ea6\u8f83\u4f4e\uff0c\u8bf7\u4eba\u5de5\u6838\u5bf9"
        )
    if amount_confidence < 0.8:
        warnings.append(
            "\u91d1\u989d\u8bc6\u522b\u7f6e\u4fe1\u5ea6\u8f83\u4f4e\uff0c\u8bf7\u4eba\u5de5\u6838\u5bf9"
        )
    return OcrRecordCandidate(
        product_name=product_name,
        record_date=page_date,
        amount=amount,
        confidence=OcrFieldConfidence(
            product_name=product_confidence,
            record_date=date_confidence,
            amount=amount_confidence,
        ),
        warnings=warnings,
    )


def _money(text: str) -> Decimal | None:
    matches = _MONEY.findall(text.replace(" ", ""))
    if len(matches) != 1:
        return None
    try:
        return Decimal(matches[0].replace(",", ""))
    except InvalidOperation:
        return None


def _is_product_name(text: str) -> bool:
    normalized = _clean_product_name(text)
    if len(normalized) < 4 or not re.search(r"[\u4e00-\u9fff]", normalized):
        return False
    excluded = (
        "\u6301\u4ed3\u660e\u7ec6",
        "\u8d44\u4ea7\u660e\u7ec6",
        "\u4ea7\u54c1\u540d\u79f0",
        "\u4ea7\u54c1\u7c7b\u578b",
        "\u6301\u4ed3\u91d1\u989d",
        "\u6301\u4ed3\u672c\u91d1",
        "\u5355\u4f4d\u51c0\u503c",
        "\u6211\u7684\u8d44\u4ea7",
        "\u6211\u7684\u6536\u76ca",
        "\u7a33\u5065\u7406\u8d22",
        "\u4eba\u6c11\u5e01",
        "\u56fa\u6536+\u8d44\u4ea7",
        "\u4ee3\u9500",
        "\u5206\u914d\u6536\u76ca",
    )
    return (
        not any(term in normalized for term in excluded)
        and not _is_branding(normalized)
        and _money(normalized) is None
    )


def _is_product_name_fragment(text: str) -> bool:
    normalized = _clean_product_name(text)
    if not normalized or _money(normalized) is not None or _DATE.search(normalized):
        return False
    excluded = (
        "\u4ee3\u9500",
        "\u6301\u4ed3\u660e\u7ec6",
        "\u4ea7\u54c1\u540d\u79f0",
        "\u4ea7\u54c1\u7c7b\u578b",
        "\u6301\u4ed3\u91d1\u989d",
        "\u6301\u4ed3\u672c\u91d1",
        "\u5355\u4f4d\u51c0\u503c",
        "\u8d27\u5e01\u7c7b\u4ea7\u54c1",
        "\u5206\u914d\u6536\u76ca",
    )
    return not any(term in normalized for term in excluded) and not _is_branding(normalized)


def _is_branding(text: str) -> bool:
    upper_text = text.upper()
    return any(term in text or term in upper_text for term in _BRANDING_TERMS)


def _clean_product_name(text: str) -> str:
    normalized = text.strip()
    return re.split(r"[*\uff0a]?\u8d27\u5e01\u7c7b\u4ea7\u54c1", normalized, maxsplit=1)[0].strip()

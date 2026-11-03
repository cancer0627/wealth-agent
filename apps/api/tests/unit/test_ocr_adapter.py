from datetime import date
from decimal import Decimal
from io import BytesIO

from PIL import Image
from wealth_agent.adapters.ocr.platform_parser import OcrObservation, parse_records
from wealth_agent.adapters.ocr.rapidocr import RapidOcrAdapter
from wealth_agent.capabilities.conversation.contract import OcrExtractionRequest


def _box(left: float, top: float, right: float, bottom: float) -> list[list[float]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_asset_details_pair_product_date_and_daily_valuation() -> None:
    observations = [
        OcrObservation("2026-06-01", 0.99, 30, 90, 180, 120),
        OcrObservation("资产明细", 0.99, 30, 140, 180, 170),
        OcrObservation("2026-08-30", 0.99, 210, 140, 400, 170),
        OcrObservation("阳光金创盈乐享11号90天持有期D", 0.96, 40, 220, 500, 250),
        OcrObservation("250,541.60", 0.98, 700, 220, 880, 250),
        OcrObservation("智悦增强180天持有期1号E", 0.95, 40, 280, 500, 310),
        OcrObservation("199,080.49", 0.97, 700, 280, 880, 310),
        OcrObservation("449,622.09", 0.99, 700, 90, 880, 120),
    ]

    records, warnings = parse_records(
        observations,
        width=943,
        height=2048,
        default_date=date(2026, 8, 31),
    )

    assert warnings == []
    assert [item.product_name for item in records] == [
        "阳光金创盈乐享11号90天持有期D",
        "智悦增强180天持有期1号E",
    ]
    assert [item.amount for item in records] == [
        Decimal("250541.60"),
        Decimal("199080.49"),
    ]
    assert {item.record_date for item in records} == {date(2026, 8, 30)}


def test_rotated_holding_table_is_selected_and_parsed_by_row() -> None:
    def recognizer(image: object) -> list[list[object]]:
        height, width = image.shape[:2]  # type: ignore[attr-defined]
        if width <= height:
            return []
        return [
            [_box(10, 5, 180, 20), "持仓明细(2026-07-31时点)", 0.99],
            [_box(20, 30, 80, 45), "产品名称", 0.99],
            [_box(145, 30, 190, 45), "持仓金额", 0.99],
            [_box(20, 70, 90, 85), "旋转测试产品", 0.95],
            [_box(145, 70, 190, 85), "123.45", 0.96],
        ]

    content = BytesIO()
    Image.new("RGB", (100, 200), "white").save(content, format="PNG")
    result = RapidOcrAdapter(recognizer).extract(
        OcrExtractionRequest(
            content=content.getvalue(),
            mime_type="image/png",
            default_date=date(2026, 8, 31),
        )
    )

    assert result.orientation_degrees == 90
    assert result.parser == "wealth-screenshot-v2"
    assert result.records[0].product_name == "旋转测试产品"
    assert result.records[0].record_date == date(2026, 7, 31)
    assert result.records[0].amount == Decimal("123.45")


def test_detail_date_with_low_confidence_falls_back_to_default_date() -> None:
    observations = [
        OcrObservation("资产明细", 0.99, 30, 140, 180, 170),
        OcrObservation("2026-08-30", 0.5, 210, 140, 400, 170),
        OcrObservation("核对日期测试产品", 0.96, 40, 220, 500, 250),
        OcrObservation("100.00", 0.98, 700, 220, 880, 250),
    ]

    records, warnings = parse_records(
        observations,
        width=943,
        height=2048,
        default_date=date(2026, 8, 31),
    )

    assert records[0].record_date == date(2026, 8, 31)
    assert "置信度较低" in warnings[0]
    assert records[0].warnings == warnings


def test_table_parser_ignores_page_logo_between_rows() -> None:
    observations = [
        OcrObservation("持仓明细(2026-08-31时点)", 0.99, 82, 448, 364, 470),
        OcrObservation("产品名称", 1.0, 238, 485, 320, 510),
        OcrObservation("持仓金额", 1.0, 862, 485, 944, 510),
        OcrObservation("招商银行", 0.99, 141, 1720, 292, 1742),
        OcrObservation("CHINA MERCHANTS BANK", 0.99, 146, 1748, 289, 1761),
        OcrObservation("浦银理财多季鑫封闭式", 1.0, 188, 1785, 370, 1807),
        OcrObservation("261号理财产品A", 1.0, 211, 1807, 347, 1829),
        OcrObservation("100,154.40", 1.0, 856, 1795, 951, 1818),
        OcrObservation("浦银理财多季鑫封闭式", 1.0, 188, 1842, 370, 1864),
        OcrObservation("262号理财产品A", 1.0, 211, 1864, 347, 1886),
        OcrObservation("50,077.80", 1.0, 859, 1853, 947, 1876),
    ]

    records, warnings = parse_records(
        observations,
        width=1301,
        height=1925,
        default_date=date(2026, 8, 31),
    )

    assert warnings == []
    assert [item.product_name for item in records] == [
        "浦银理财多季鑫封闭式261号理财产品A",
        "浦银理财多季鑫封闭式262号理财产品A",
    ]


def test_product_column_reocr_recovers_multiline_name_suffix() -> None:
    def recognizer(image: object) -> list[list[object]]:
        height, width = image.shape[:2]  # type: ignore[attr-defined]
        if width <= height:
            return []
        if height < 100:
            return [
                [_box(5, 5, 90, 22), "安悦优享日开180天持有", 0.99],
                [_box(25, 25, 75, 45), "2号A", 1.0],
            ]
        return [
            [_box(10, 5, 180, 20), "持仓明细(2026-08-31时点)", 0.99],
            [_box(20, 30, 80, 45), "产品名称", 0.99],
            [_box(145, 30, 190, 45), "持仓金额", 0.99],
            [_box(20, 70, 90, 85), "安悦优享日开180天持有", 0.95],
            [_box(145, 70, 190, 85), "100.00", 0.96],
        ]

    content = BytesIO()
    Image.new("RGB", (100, 200), "white").save(content, format="PNG")
    result = RapidOcrAdapter(recognizer).extract(
        OcrExtractionRequest(
            content=content.getvalue(),
            mime_type="image/png",
            default_date=date(2026, 8, 31),
        )
    )

    assert [item.product_name for item in result.records] == [
        "安悦优享日开180天持有2号A"
    ]

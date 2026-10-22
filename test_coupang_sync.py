from pathlib import Path

from commerce_mail_service import (
    build_coupang_merchant_name, choose_card_transaction,
    match_card_transactions, remove_coupang_memo,
    is_coupang_order_subject, parse_coupang_order_html,
)


COUPANG_SAMPLE = """
<html><body><table>
  <tr><td>구매 상세내역</td><td>쿠팡가</td><td>수량</td><td>구매금액</td><td>판매자</td></tr>
  <tr><td>생수 2L 12개</td><td>10,000원</td><td>2</td><td>20,000원</td><td>판매자</td></tr>
  <tr><td>종이컵 100개</td><td>5,500원</td><td>1</td><td>5,500원</td><td>판매자</td></tr>
  <tr><td>상품 가격</td><td>25,500원</td></tr>
  <tr><td>롯데카드쿠페이(카드) / 일시불</td><td>25,500원</td></tr>
  <tr><td>총 결제금액</td><td>25,500원</td></tr>
</table></body></html>
"""


def test_parse_coupang_order_extracts_products_payment_and_card():
    order = parse_coupang_order_html(
        COUPANG_SAMPLE,
        order_date="2026-09-10",
        source_key="coupang-mail:abc",
    )

    assert order["vendor"] == "쿠팡"
    assert order["order_date"] == "2026-09-10"
    assert order["total_amount"] == 25500
    assert order["card_company"] == "롯데카드"
    assert order["payment_method"] == "롯데카드쿠페이(카드) / 일시불"
    assert order["products"] == [
        {"name": "생수 2L 12개", "quantity": 2, "amount": 20000},
        {"name": "종이컵 100개", "quantity": 1, "amount": 5500},
    ]
    assert order["product_summary"] == "생수 2L 12개 ×2 외 1건"


def test_choose_card_transaction_requires_one_unambiguous_match():
    order = {
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [
        {"id": 1, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "롯데카드"},
        {"id": 2, "date": "2026-09-10", "amount": 25500, "name": "다른상점", "card_name": "롯데카드"},
        {"id": 3, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "현대카드"},
    ]

    assert choose_card_transaction(order, candidates) == 1


def test_choose_card_transaction_keeps_same_amount_repeats_unmatched():
    order = {
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [
        {"id": 1, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "롯데카드"},
        {"id": 2, "date": "2026-09-10", "amount": 25500, "name": "쿠팡페이", "card_name": "롯데카드"},
    ]

    assert choose_card_transaction(order, candidates) is None
    assert match_card_transactions(order, candidates)["status"] == "ambiguous"


def test_choose_card_transaction_accepts_three_day_settlement_gap():
    order = {
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [
        {"id": 7, "date": "2026-09-13", "amount": 25500, "name": "COUPANG", "card_name": "롯데카드"},
    ]

    assert choose_card_transaction(order, candidates) == 7


def test_only_coupang_order_detail_subject_is_accepted():
    assert is_coupang_order_subject("[쿠팡] 김*덕님 주문하신 내역을 확인해주세요.")
    assert not is_coupang_order_subject("[쿠팡] 김*덕님 주문이 배송되었습니다.")
    assert not is_coupang_order_subject("[쿠팡이츠] 주문이 접수되었습니다.")


def test_coupang_eats_transaction_is_never_used_for_product_order():
    order = {
        "source_key": "coupang-mail:order",
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [{
        "id": 8, "date": "2026-09-10", "amount": 25500,
        "name": "쿠팡이츠_쿠팡이츠_KCP", "card_name": "롯데카드",
    }]

    assert match_card_transactions(order, candidates)["status"] == "unmatched"


def test_legacy_coupang_product_merchant_name_is_recognized():
    assert build_coupang_merchant_name("생수 2L ×2 외 1건") == "쿠팡 - 생수 2L ×2 외 1건"


def test_remove_coupang_memo_only_removes_its_exact_marker():
    assert remove_coupang_memo("기존 메모 | [쿠팡] 생수 ×2", "생수 ×2") == "기존 메모"


def test_missing_card_company_is_never_auto_matched():
    order = {"order_date": "2026-09-10", "total_amount": 25500, "card_company": None}
    candidates = [
        {"id": 1, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "롯데카드"},
    ]

    assert match_card_transactions(order, candidates)["status"] == "unmatched"


def test_transaction_linked_to_another_order_is_not_reused():
    order = {
        "source_key": "coupang-mail:new",
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [{
        "id": 1, "date": "2026-09-10", "amount": 25500,
        "name": "쿠팡", "card_name": "롯데카드",
        "linked_source_key": "coupang-mail:other",
    }]

    assert match_card_transactions(order, candidates)["status"] == "unmatched"


def test_existing_link_is_revalidated_when_second_candidate_appears():
    order = {
        "source_key": "coupang-mail:same",
        "order_date": "2026-09-10",
        "total_amount": 25500,
        "card_company": "롯데카드",
    }
    candidates = [
        {"id": 1, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "롯데카드", "linked_source_key": "coupang-mail:same"},
        {"id": 2, "date": "2026-09-10", "amount": 25500, "name": "쿠팡", "card_name": "롯데카드", "linked_source_key": None},
    ]

    assert match_card_transactions(order, candidates)["status"] == "ambiguous"


def test_mail_sync_result_escapes_email_derived_values():
    template = Path("templates/cards.html").read_text(encoding="utf-8")

    assert "${escapeHtml(item.card_company || '카드')}" in template
    assert "${escapeHtml(item.name || '-')}" in template
    assert "${escapeHtml(item.category || '미분류')}" in template
    assert "el.status.textContent = error.message" in template


def test_legacy_product_merchant_format_is_still_available_for_migration():
    assert build_coupang_merchant_name("생수 2L") == "쿠팡 - 생수 2L"


def test_removing_legacy_coupang_marker_preserves_manual_memo():
    assert remove_coupang_memo("출장비 | [쿠팡] 생수 2L", "생수 2L") == "출장비"

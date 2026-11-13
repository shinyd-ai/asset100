import pytest
import sqlite3
from card_mail_service import classify_mail_candidate
from commerce_mail_service import (
    is_auction_order_subject, parse_auction_order_html,
    match_card_transactions, add_commerce_memo, remove_commerce_memo,
    reconcile_commerce_orders,
)

AUCTION_SAMPLE_HTML = """
<html><body>
<table>
  <tr><td>고객님께서 2026년10월21일 주문하신 1 개의 상품은 결제완료 되었습니다.</td></tr>
</table>
<table>
  <tr>
    <td>
      <table>
        <tr><th>주문번호</th><td>2573757612</td></tr>
        <tr><th>상품금액</th><td>21,000원 (수량 1개)</td></tr>
      </table>
      <a href="http://mobile.auction.co.kr/EGo.aspx?t=vp&p=F680179951&o=m">
        <u>국산 고추가루 고춧가루 1Kg(보통맛)</u>
      </a>
    </td>
  </tr>
</table>
<table>
  <tr><td>결제 정보</td></tr>
  <tr><td>Smile Pay</td><td>16,900 원</td></tr>
  <tr><td>스마일캐시·머니</td><td>0 원</td></tr>
  <tr><td>상품금액 합계 (+)</td><td>21,000 원</td></tr>
  <tr><td>총 할인금액(-)</td><td>4,100 원</td></tr>
</table>
</body></html>
"""


def test_classify_auction_order_mail():
    res = classify_mail_candidate("주문해 주셔서 감사합니다", '"옥션" <auction@auction.co.kr>')
    assert res == {"group": "payment", "type": "옥션 주문"}

    res_delivery = classify_mail_candidate("주문하신 상품이 발송되었습니다", '"옥션" <auction@auction.co.kr>')
    assert res_delivery is None


def test_is_auction_order_subject():
    assert is_auction_order_subject("주문해 주셔서 감사합니다", "auction@auction.co.kr")
    assert is_auction_order_subject("주문해주셔서 감사합니다", "auction@auction.co.kr")
    assert not is_auction_order_subject("배송안내", "auction@auction.co.kr")


def test_parse_auction_order_html():
    order = parse_auction_order_html(AUCTION_SAMPLE_HTML, "2026-10-21", "auction-mail:123")
    assert order is not None
    assert order["vendor"] == "옥션"
    assert order["order_date"] == "2026-10-21"
    assert order["total_amount"] == 16900
    assert order["payment_method"] == "Smile Pay"
    assert order["product_summary"] == "국산 고추가루 고춧가루 1Kg(보통맛)"
    assert len(order["products"]) == 1
    assert order["products"][0]["quantity"] == 1


def test_match_card_transactions_for_auction():
    order = {
        "vendor": "옥션",
        "order_date": "2026-10-21",
        "total_amount": 16900,
        "card_company": None,
    }
    candidates = [
        {"id": 1125, "date": "2026-10-21", "amount": 16900, "name": "옥션", "card_name": "현대카드"},
        {"id": 1100, "date": "2026-10-21", "amount": 60725, "name": "삼성화재", "card_name": "현대카드"},
    ]
    res = match_card_transactions(order, candidates)
    assert res["status"] == "matched"
    assert res["card_tx_id"] == 1125


def test_match_card_transactions_for_auction_smilepay():
    order = {
        "vendor": "옥션",
        "order_date": "2026-10-21",
        "total_amount": 16900,
        "card_company": None,
    }
    candidates = [
        {"id": 1129, "date": "2026-10-22", "amount": 16900, "name": "스마일페이", "card_name": "신한카드"},
    ]
    res = match_card_transactions(order, candidates)
    assert res["status"] == "matched"
    assert res["card_tx_id"] == 1129


def test_auction_memo_helpers():
    memo = add_commerce_memo("", "옥션", "고춧가루 1Kg")
    assert memo == "[옥션] 고춧가루 1Kg"

    memo2 = add_commerce_memo(memo, "옥션", "고춧가루 1Kg")
    assert memo2 == "[옥션] 고춧가루 1Kg"

    cleaned = remove_commerce_memo(memo, "옥션", "고춧가루 1Kg")
    assert cleaned == ""


def test_scan_mail_candidates_target_month_validation():
    from card_mail_service import scan_mail_candidates
    res = scan_mail_candidates(max_emails=10, group='payment', target_month='invalid-format')
    assert res['ok'] is False
    assert '형식' in res['error']


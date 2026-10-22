import pytest

from card_mail_service import (
    build_message_identity, build_source_key, classify_mail_candidate,
    decode_mail_candidate_id, encode_mail_candidate_id,
    parse_card_email_text, select_sync_mailboxes, validate_scan_limit,
)
from statement_parser import parse_samsung_statement_data
from app import (
    _classify_mail_match,
    _mail_item_already_imported, _mail_transaction_memo, _mail_transaction_result,
    _merge_candidate_result,
)


def test_select_sync_mailboxes_finds_inbox_and_naver_smart_mailboxes():
    mailbox_lines = [
        b'(\\HasNoChildren \\Inbox) "/" "INBOX"',
        b'(\\HasNoChildren) "/" "&zK2tbAC3rLDIHA-"',  # billing/payment
        b'(\\HasNoChildren) "/" "&zK2tbMEc1Wg-"',      # statements
        b'(\\HasNoChildren) "/" "SNS"',
    ]

    assert select_sync_mailboxes(mailbox_lines) == [
        "INBOX",
        "&zK2tbAC3rLDIHA-",
        "&zK2tbMEc1Wg-",
    ]


def test_source_key_is_stable_but_keeps_identical_statement_rows_distinct():
    first = build_source_key("<same-message@example.com>", "statement", 0)
    repeated = build_source_key("<same-message@example.com>", "statement", 1)

    assert first == build_source_key("<same-message@example.com>", "statement", 0)
    assert first != repeated


def test_message_identity_uses_rfc822_content_not_mutable_imap_sequence():
    raw = b"Message-ID: <same@example.com>\r\nSubject: card\r\n\r\nbody"

    assert build_message_identity(raw) == build_message_identity(raw)
    assert build_message_identity(raw) != build_message_identity(raw + b" changed")


@pytest.mark.parametrize("value", [0, -1, 201, "all", None])
def test_scan_limit_rejects_unbounded_or_invalid_values(value):
    with pytest.raises(ValueError):
        validate_scan_limit(value)


@pytest.mark.parametrize("value, expected", [(1, 1), ("50", 50), (200, 200)])
def test_scan_limit_accepts_one_through_two_hundred(value, expected):
    assert validate_scan_limit(value) == expected


def test_parse_samsung_statement_data_preserves_identical_transactions():
    response = {
        "istmList": [
            {
                "slDt": "20260901",
                "mrcNm": "Same Store",
                "slAm": "5,000",
                "istmMcn": "1",
                "pdCNm": "Samsung Card",
            },
            {
                "slDt": "20260901",
                "mrcNm": "Same Store",
                "slAm": "5,000",
                "istmMcn": "1",
                "pdCNm": "Samsung Card",
            },
        ]
    }

    result = parse_samsung_statement_data(response)

    assert len(result) == 2
    assert result[0]["card_company"] == "삼성카드"
    assert result[0]["date"] == "2026-09-01"
    assert result[0]["name"] == "Same Store"
    assert result[0]["amount"] == 5000
    assert result[0]["installment"] == 1
    assert result[0]["statement_row"] == 0
    assert result[1]["statement_row"] == 1


def test_parse_samsung_statement_data_handles_cancellation_amount():
    response = {
        "stlmCanIzList": [
            {
                "slDt": "20260902",
                "mrcNm": "Cancelled Store",
                "slAm": "12,300",
            }
        ]
    }

    result = parse_samsung_statement_data(response)

    assert len(result) == 1
    assert result[0]["amount"] == -12300


def test_parse_samsung_installment_uses_this_months_billed_amount():
    response = {
        "istmList": [{
            "slDt": "20260112",
            "stlmDt": "20261013",
            "mrcNm": "Installment purchase",
            "slAm": 33400000,
            "bilPcam": 503945,
            "bilInt": 97798,
            "bilPcam1": 601743,
            "bilSumAm": 0,
            "istmMcn": 60,
            "bilTn": 9,
        }],
    }

    result = parse_samsung_statement_data(response)

    assert len(result) == 1
    assert result[0]["amount"] == 601743
    assert result[0]["installment"] == 60
    assert result[0]["date"] == "2026-09-01"
    assert result[0]["original_date"] == "2026-01-12"
    assert result[0]["original_amount"] == 33400000
    assert result[0]["installment_number"] == 9
    assert result[0]["installment_principal"] == 503945
    assert result[0]["installment_interest"] == 97798


def test_samsung_installment_january_bill_belongs_to_previous_december():
    response = {"istmList": [{
        "slDt": "20260112", "stlmDt": "20270113",
        "mrcNm": "Installment purchase", "slAm": 33400000,
        "bilPcam1": 601743, "istmMcn": 60,
    }]}

    result = parse_samsung_statement_data(response)

    assert result[0]["date"] == "2026-12-01"


def test_samsung_installment_memo_keeps_purchase_and_billing_details():
    item = {
        "card_company": "삼성카드", "installment": 60,
        "original_date": "2026-01-12", "original_amount": 33400000,
        "installment_number": 9, "installment_principal": 503945,
        "installment_interest": 97798,
    }

    assert _mail_transaction_memo(item) == (
        "원거래일 2026-01-12 · 9/60회 · 전체 원금 33,400,000원"
        " · 이번 회차 원금 503,945원 · 이자 97,798원"
    )


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeDb:
    def __init__(self):
        self.source_keys = set()
        self.legacy_rows = [{"id": 17}]

    def execute(self, sql, params=None):
        if "source_key = ?" in sql:
            return _FakeCursor([{"id": 99}] if params[0] in self.source_keys else [])
        if "source_key IS NULL" in sql:
            return _FakeCursor(self.legacy_rows)
        raise AssertionError(sql)


def test_legacy_duplicate_is_consumed_once_but_repeated_purchase_is_preserved():
    db = _FakeDb()
    item = {
        "source_key": "naver-mail:new-row",
        "date": "2026-09-01",
        "name": "Same Store",
        "amount": 5000,
    }
    claimed_legacy_ids = set()

    assert _mail_item_already_imported(db, item, card_id=3, claimed_legacy_ids=claimed_legacy_ids)
    assert not _mail_item_already_imported(db, item, card_id=3, claimed_legacy_ids=claimed_legacy_ids)

    db.source_keys.add("naver-mail:new-row")
    assert _mail_item_already_imported(db, item, card_id=3, claimed_legacy_ids=set())


def test_new_mail_transaction_has_empty_memo():
    assert _mail_transaction_memo({"card_company": "삼성카드", "sender": "card@example.com"}) == ""


def test_payment_mail_footer_cancel_policy_does_not_negate_purchase():
    result = parse_card_email_text(
        "[하나카드] 결제 안내", "notice@example.com",
        "이용금액 15,900원\n가맹점 테스트상점\n상품의 취소/반품/교환 문의는 판매자에게 문의하세요",
    )

    assert result["amount"] == 15900
    assert result["is_cancel"] is False


def test_payment_mail_actual_approval_cancellation_stays_negative():
    result = parse_card_email_text(
        "[하나카드] 승인 안내", "notice@example.com",
        "승인취소\n이용금액 15,900원\n가맹점 테스트상점",
    )

    assert result["amount"] == -15900
    assert result["is_cancel"] is True


@pytest.mark.parametrize(
    "subject, sender, expected",
    [
        ("[삼성카드] 10월 카드 이용대금 명세서", "billing@samsungcard.com", ("statement", "명세서")),
        ("김*덕님, 쿠팡에서 [신용카드]결제하신 내역입니다.", "mail@easypay.co.kr", ("payment", "결제")),
        ("[현대카드] 승인취소 안내", "notice@hyundaicard.com", ("payment", "취소")),
        ("[쿠팡] 김*덕님 주문하신 내역을 확인해주세요.", "no_reply@coupang.com", ("payment", "쿠팡 주문")),
    ],
)
def test_mail_candidate_classifies_statement_payment_and_coupang(subject, sender, expected):
    result = classify_mail_candidate(subject, sender)

    assert (result["group"], result["type"]) == expected


@pytest.mark.parametrize(
    "subject, sender",
    [
        ("[쿠팡] 주문이 배송되었습니다.", "no_reply@coupang.com"),
        ("[삼성카드] 개인정보 처리방침 안내", "notice@samsungcard.com"),
        ("[KB국민카드] 포인트 소멸 예정 안내", "notice@kbcard.com"),
    ],
)
def test_mail_candidate_excludes_shipping_policy_and_point_notices(subject, sender):
    assert classify_mail_candidate(subject, sender) is None


def test_candidate_id_round_trip_keeps_uid_and_header_fingerprint():
    candidate_id = encode_mail_candidate_id("&zK2tbAC3rLDIHA-", "413", "header-sha")

    assert decode_mail_candidate_id(candidate_id) == {
        "mailbox": "&zK2tbAC3rLDIHA-",
        "uid": "413",
        "fingerprint": "header-sha",
    }


@pytest.mark.parametrize("candidate_id", ["", "not-base64", "e30"])
def test_candidate_id_rejects_invalid_values(candidate_id):
    with pytest.raises(ValueError):
        decode_mail_candidate_id(candidate_id)


def test_candidate_result_keeps_success_when_statement_has_duplicate_rows():
    results = {}

    _merge_candidate_result(results, "mail-1", "등록 성공")
    _merge_candidate_result(results, "mail-1", "중복")

    assert results["mail-1"] == "등록 성공"


def test_mail_review_endpoints_reject_invalid_or_empty_selection_before_db_access():
    from app import app

    client = app.test_client()
    assert client.post('/api/card-tx/mail-candidates', json={'group': 'other'}).status_code == 400
    assert client.post('/api/card-tx/sync-selected-mail', json={
        'group': 'statement', 'candidate_ids': []
    }).status_code == 400
    assert client.post('/api/card-tx/sync-selected-mail', json={
        'group': 'statement', 'candidate_ids': ['invalid-token']
    }).status_code == 400
    assert client.post('/api/card-tx/approve-unmatched-mail', json={
        'candidate_ids': []
    }).status_code == 400


def test_payment_mail_uniquely_matches_an_existing_transaction():
    item = {'date': '2026-10-14', 'name': '테스트 상점', 'amount': 12300}
    candidates = [
        {'id': 7, 'date': '2026-10-14', 'name': '테스트상점', 'amount': 12300,
         'card_name': '삼성카드', 'source_key': None},
    ]

    result = _classify_mail_match(item, candidates)

    assert result['status'] == 'matched'
    assert result['transaction']['id'] == 7


def test_payment_mail_with_two_equally_good_matches_is_ambiguous():
    item = {'date': '2026-10-14', 'name': '테스트 상점', 'amount': 12300}
    candidates = [
        {'id': 7, 'date': '2026-10-14', 'name': '테스트상점', 'amount': 12300},
        {'id': 8, 'date': '2026-10-14', 'name': '테스트 상점', 'amount': 12300},
    ]

    assert _classify_mail_match(item, candidates)['status'] == 'ambiguous'


def test_unmatched_cancellation_shows_original_positive_payment_as_reference():
    item = {'date': '2026-10-14', 'name': '테스트 상점', 'amount': -12300}
    originals = [
        {'id': 3, 'date': '2026-10-12', 'name': '테스트상점', 'amount': 12300},
    ]

    result = _classify_mail_match(item, [], originals)

    assert result['status'] == 'unmatched'
    assert result['original_transaction']['id'] == 3


def test_mail_transaction_result_exposes_registered_transaction_details():
    item = {
        'candidate_id': 'mail-1', 'date': '2026-10-14',
        'card_company': '삼성카드', 'name': '테스트가맹점', 'amount': 12300,
        'subject': '[삼성카드] 승인 안내',
    }

    assert _mail_transaction_result(item, '신규 등록') == {
        'candidate_id': 'mail-1', 'date': '2026-10-14',
        'card_company': '삼성카드', 'name': '테스트가맹점', 'amount': 12300,
        'status': '신규 등록', 'subject': '[삼성카드] 승인 안내',
    }

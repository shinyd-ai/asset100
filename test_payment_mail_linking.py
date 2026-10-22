import sqlite3

from card_mail_service import parse_card_email_text
from app import (
    _link_selected_payments, _match_mail_transaction,
    find_matching_card_id,
)


def _database():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE card_info (id INTEGER PRIMARY KEY, card_name TEXT);
        CREATE TABLE card_tx (
            id INTEGER PRIMARY KEY, card_id INTEGER, date TEXT, name TEXT,
            amount INTEGER, source_key TEXT, category TEXT, fund_group_id INTEGER,
            installment INTEGER DEFAULT 1, memo TEXT, budget_id INTEGER,
            category_locked INTEGER DEFAULT 0, fund_group_locked INTEGER DEFAULT 0
        );
        CREATE TABLE card_payment_links (
            payment_source_key TEXT PRIMARY KEY,
            card_tx_id INTEGER NOT NULL REFERENCES card_tx(id)
        );
        CREATE TABLE fund_groups (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO card_info VALUES (4, '하나카드'), (6, '롯데카드'), (7, '현대카드');
    ''')
    return db


def test_payment_mail_identifies_parenthesized_card_brand():
    parsed = parse_card_email_text(
        '신용카드 결제 안내', 'payments@example.com',
        '결제금액 9,900원\n신용카드(롯데)\n상품 취소/반품은 판매자 문의',
    )

    assert parsed['card_company'] == '롯데카드'
    assert parsed['amount'] == 9900


def test_unknown_payment_card_does_not_fall_back_to_first_card():
    db = _database()
    assert find_matching_card_id(db, '카드', fallback=False) is None
    assert find_matching_card_id(db, '롯데카드', fallback=False) == 6


def test_payment_matches_statement_with_distinct_source_key():
    db = _database()
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (944,6,'2026-09-02','쿠팡',9900,'statement-944','쇼핑',NULL,1,'')")
    item = {'date': '2026-09-02', 'amount': 9900, 'name': '결제 안내',
            'source_key': 'payment-1011'}

    match = _match_mail_transaction(db, item, None)

    assert match['status'] == 'matched'
    assert match['transaction']['id'] == 944


def test_unknown_card_with_two_same_amount_candidates_stays_ambiguous():
    db = _database()
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (1,6,'2026-09-02','상점A',9900,'statement-a','쇼핑',NULL,1,'')")
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (2,7,'2026-09-02','상점B',9900,'statement-b','쇼핑',NULL,1,'')")
    item = {'date': '2026-09-02', 'amount': 9900, 'name': '결제 안내',
            'source_key': 'payment-1'}

    assert _match_mail_transaction(db, item, None)['status'] == 'ambiguous'


def test_linking_payment_preserves_statement_source_and_is_idempotent():
    db = _database()
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (944,6,'2026-09-02','쿠팡',9900,'statement-944','쇼핑',NULL,1,'')")
    item = {'date': '2026-09-02', 'amount': 9900, 'name': '결제 안내',
            'card_company': '롯데카드', 'source_key': 'payment-1011',
            'candidate_id': 'mail-1'}

    first = _link_selected_payments(db, ['mail-1'], [item], [])
    second = _link_selected_payments(db, ['mail-1'], [item], [])

    assert first['matched_count'] == second['matched_count'] == 1
    assert db.execute('SELECT source_key FROM card_tx WHERE id=944').fetchone()[0] == 'statement-944'
    assert tuple(db.execute('SELECT payment_source_key,card_tx_id FROM card_payment_links').fetchall()[0]) == ('payment-1011', 944)
    assert db.execute('SELECT COUNT(*) FROM card_payment_links').fetchone()[0] == 1


def test_card_transactions_api_exposes_verified_link_count(monkeypatch):
    import app as application

    db = _database()
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (944,6,'2026-09-02','쿠팡',9900,'statement-944','쇼핑',NULL,1,'')")
    db.execute("INSERT INTO card_payment_links VALUES ('payment-1011',944)")
    db.commit()
    monkeypatch.setattr(application, 'database_get_db', lambda: db)

    response = application.app.test_client().get('/api/card-tx?year=2026&month=09')

    assert response.status_code == 200
    assert response.json['rows'][0]['linked_payment_count'] == 1


def test_legacy_mail_sync_links_payment_instead_of_adding_duplicate(monkeypatch):
    import app as application
    import card_mail_service
    import commerce_mail_service

    db = _database()
    db.execute("INSERT INTO card_tx (id,card_id,date,name,amount,source_key,category,fund_group_id,installment,memo) VALUES (944,6,'2026-09-02','쿠팡',9900,'statement-944','식비',NULL,1,'')")
    db.commit()
    monkeypatch.setattr(application, 'get_db', lambda: db)
    monkeypatch.setattr(card_mail_service, 'fetch_and_sync_card_emails', lambda **kw: {
        'ok': True, 'items': [{'date': '2026-09-02', 'amount': 9900,
            'name': '결제 안내', 'card_company': '롯데카드',
            'source': '실시간승인', 'source_key': 'payment-1011'}]})
    monkeypatch.setattr(commerce_mail_service, 'fetch_coupang_orders', lambda **kw: {
        'ok': True, 'orders': []})

    response = application.app.test_client().post('/api/card-tx/sync-mail', json={'max_emails': 1})

    assert response.status_code == 200
    assert response.json['added_count'] == 0
    assert response.json['linked_count'] == 1

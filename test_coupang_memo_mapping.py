import sqlite3

from commerce_mail_service import reconcile_coupang_orders
from app import _mail_transaction_memo


def _db():
    db = sqlite3.connect(':memory:')
    db.row_factory = lambda cursor, row: dict(zip((column[0] for column in cursor.description), row))
    db.create_function('NOW', 0, lambda: '2026-10-17')
    db.executescript('''
        CREATE TABLE card_info (id INTEGER PRIMARY KEY, card_name TEXT);
        CREATE TABLE card_tx (id INTEGER PRIMARY KEY, card_id INTEGER, date TEXT,
            amount INTEGER, name TEXT, memo TEXT);
        CREATE TABLE commerce_orders (id INTEGER PRIMARY KEY, source_key TEXT UNIQUE,
            vendor TEXT, order_date TEXT, total_amount INTEGER, payment_method TEXT,
            card_company TEXT, product_summary TEXT, products_json TEXT,
            matched_card_tx_id INTEGER, original_tx_name TEXT, sync_status TEXT,
            updated_at TEXT);
        INSERT INTO card_info VALUES (6, '롯데카드');
        INSERT INTO card_tx VALUES (944, 6, '2026-09-02', 9900,
            '쿠팡(쿠페이)', '출장비');
    ''')
    return db


def _order(summary='USB 케이블 2개'):
    return {'source_key': 'order-mail-1', 'vendor': '쿠팡',
            'order_date': '2026-09-02', 'total_amount': 9900,
            'payment_method': '롯데카드', 'card_company': '롯데카드',
            'product_summary': summary, 'products': []}


def test_matched_product_goes_to_memo_and_resync_replaces_own_marker():
    db = _db()
    assert reconcile_coupang_orders(db, [_order()])['matched'] == 1
    row = db.execute('SELECT name, memo FROM card_tx WHERE id=944').fetchone()
    assert row == {'name': '쿠팡(쿠페이)', 'memo': '출장비 | [쿠팡] USB 케이블 2개'}

    reconcile_coupang_orders(db, [_order()])
    assert db.execute('SELECT memo FROM card_tx WHERE id=944').fetchone()['memo'] == '출장비 | [쿠팡] USB 케이블 2개'

    reconcile_coupang_orders(db, [_order('USB 케이블 3개')])
    assert db.execute('SELECT memo FROM card_tx WHERE id=944').fetchone()['memo'] == '출장비 | [쿠팡] USB 케이블 3개'


def test_legacy_product_merchant_restores_original_merchant_and_preserves_note():
    db = _db()
    db.execute("UPDATE card_tx SET name='쿠팡 - USB 케이블 2개' WHERE id=944")
    db.execute('''INSERT INTO commerce_orders
        (source_key,vendor,order_date,total_amount,card_company,product_summary,
         products_json,matched_card_tx_id,original_tx_name,sync_status)
        VALUES ('order-mail-1','쿠팡','2026-09-02',9900,'롯데카드',
                'USB 케이블 2개','[]',944,'쿠팡(쿠페이)','matched')''')

    assert reconcile_coupang_orders(db, [_order()])['matched'] == 1
    assert db.execute('SELECT name,memo FROM card_tx WHERE id=944').fetchone() == {
        'name': '쿠팡(쿠페이)', 'memo': '출장비 | [쿠팡] USB 케이블 2개'}


def test_new_coupang_transaction_places_product_in_memo():
    db = _db()
    db.execute("UPDATE card_tx SET name='쿠팡 - USB 케이블 2개', memo='' WHERE id=944")
    assert reconcile_coupang_orders(db, [_order()])['matched'] == 1
    assert db.execute('SELECT name,memo FROM card_tx WHERE id=944').fetchone() == {
        'name': '쿠팡', 'memo': '[쿠팡] USB 케이블 2개'}
    assert _mail_transaction_memo({'product_summary': 'USB 케이블 2개'}) == '[쿠팡] USB 케이블 2개'

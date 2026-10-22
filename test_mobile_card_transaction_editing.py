import sqlite3
from pathlib import Path

import pytest

import app as application


def _database():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE card_info (
            id INTEGER PRIMARY KEY,
            card_name TEXT
        );
        CREATE TABLE fund_groups (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE budget (
            id INTEGER PRIMARY KEY,
            date TEXT,
            category TEXT,
            name TEXT,
            amount INTEGER,
            memo TEXT
        );
        CREATE TABLE card_tx (
            id INTEGER PRIMARY KEY,
            card_id INTEGER,
            date TEXT,
            name TEXT,
            category TEXT,
            amount INTEGER,
            installment INTEGER,
            memo TEXT,
            budget_id INTEGER,
            category_locked INTEGER DEFAULT 0,
            fund_group_id INTEGER,
            fund_group_locked INTEGER DEFAULT 0,
            source_key TEXT
        );
        CREATE TABLE card_payment_links (
            payment_source_key TEXT PRIMARY KEY,
            card_tx_id INTEGER NOT NULL
        );

        INSERT INTO card_info VALUES (4, '현대카드');
        INSERT INTO fund_groups VALUES (1, '생활비'), (2, '여가비');
        INSERT INTO budget VALUES (11, '2026-09-01', '식비', '기존 가맹점', 24500, '기존 메모');
        INSERT INTO card_tx VALUES (
            1, 4, '2026-09-01', '기존 가맹점', '식비', 24500, 1, '기존 메모',
            11, 1, 1, 1, 'statement-1'
        );
        INSERT INTO card_tx VALUES (
            2, 4, '2026-09-02', '영화관', '여가', 15000, 1, '기존 메모 2',
            NULL, 0, NULL, 0, 'statement-2'
        );
        INSERT INTO card_payment_links VALUES ('payment-1', 1);
    ''')
    return db


@pytest.fixture
def db(monkeypatch):
    connection = _database()
    monkeypatch.setattr(application, 'get_db', lambda: connection)
    yield connection
    connection.close()


@pytest.fixture
def client(db):
    return application.app.test_client()


def _change(transaction_id, base, fields):
    return {'id': transaction_id, 'base': base, 'fields': fields}


def _first_change(fields=None):
    base = {
        'date': '2026-09-01',
        'name': '기존 가맹점',
        'category': '식비',
        'amount': 24500,
        'installment': 1,
        'memo': '기존 메모',
        'fund_group_id': 1,
    }
    return _change(1, base, fields or {
        'date': '2026-09-03',
        'name': '새 가맹점',
        'category': '교통',
        'amount': 26000,
        'installment': 3,
        'memo': '상품명: 정기권',
        'fund_group_id': 2,
    })


def test_card_tx_list_exposes_budget_and_fund_group_display_data(client):
    response = client.get('/api/card-tx?year=2026&month=09')

    assert response.status_code == 200
    row = next(row for row in response.json['rows'] if row['id'] == 1)
    assert row['budget_linked'] is True
    assert row['fund_group_name'] == '생활비'
    assert row['linked_payment_count'] == 1


def test_bulk_save_updates_card_and_linked_budget_atomically(client, db):
    response = client.put('/api/card-tx/bulk', json={'changes': [
        _first_change(),
        _change(2, {'category': '여가', 'fund_group_id': None}, {
            'category': '문화',
            'fund_group_id': 2,
        }),
    ]})

    assert response.status_code == 200
    assert response.json['ok'] is True
    assert response.json['updated'] == 2
    assert response.json['budget_synced'] == 1
    card = db.execute('SELECT * FROM card_tx WHERE id = 1').fetchone()
    budget = db.execute('SELECT * FROM budget WHERE id = 11').fetchone()
    assert tuple(card[key] for key in ('date', 'name', 'category', 'amount', 'memo')) == tuple(
        budget[key] for key in ('date', 'name', 'category', 'amount', 'memo')
    )
    assert card['installment'] == 3
    assert card['fund_group_id'] == 2
    second_card = db.execute('SELECT category, fund_group_id FROM card_tx WHERE id = 2').fetchone()
    assert (second_card['category'], second_card['fund_group_id']) == ('문화', 2)


def test_bulk_save_rejects_whole_batch_when_one_row_is_invalid(client, db):
    before_cards = [tuple(row) for row in db.execute('SELECT * FROM card_tx ORDER BY id')]
    before_budget = tuple(db.execute('SELECT * FROM budget WHERE id = 11').fetchone())

    response = client.put('/api/card-tx/bulk', json={'changes': [
        _first_change(),
        _change(2, {'fund_group_id': None}, {'fund_group_id': 999}),
    ]})

    assert response.status_code == 400
    assert response.json['row_errors'][0]['id'] == 2
    assert [tuple(row) for row in db.execute('SELECT * FROM card_tx ORDER BY id')] == before_cards
    assert tuple(db.execute('SELECT * FROM budget WHERE id = 11').fetchone()) == before_budget


def test_bulk_save_returns_conflict_without_writing(client, db):
    db.execute("UPDATE card_tx SET name = '새 서버 값' WHERE id = 1")
    db.commit()

    response = client.put('/api/card-tx/bulk', json={'changes': [_first_change()]})

    assert response.status_code == 409
    assert response.json['row_errors'][0] == {'id': 1, 'field': 'name', 'error': '거래가 변경되었습니다.'}
    assert db.execute('SELECT name FROM card_tx WHERE id = 1').fetchone()['name'] == '새 서버 값'


def test_bulk_save_preserves_payment_link_and_manual_locks(client, db):
    response = client.put('/api/card-tx/bulk', json={'changes': [_first_change({
        'category': '교통',
        'fund_group_id': 2,
    })]})

    assert response.status_code == 200
    assert db.execute('SELECT COUNT(*) FROM card_payment_links WHERE card_tx_id = 1').fetchone()[0] == 1
    locks = db.execute(
        'SELECT category_locked, fund_group_locked FROM card_tx WHERE id = 1'
    ).fetchone()
    assert (locks['category_locked'], locks['fund_group_locked']) == (1, 1)


def test_mobile_transaction_shell_has_editing_and_accessibility_landmarks():
    html = Path('templates/cards.html').read_text(encoding='utf-8')

    for identifier in (
        'mobileTxList', 'mobileTxSelectionToggle', 'mobileTxSaveBar',
        'mobileTxEditSheet', 'mobileTxCategory', 'mobileTxFundGroup',
    ):
        assert f'id="{identifier}"' in html
    assert 'aria-live="polite"' in html
    assert 'id="mobileTxStatus" class="mobile-tx-status" aria-live="polite"' in html
    assert 'aria-label="결제 메일 연결"' in html
    assert 'aria-label="가계부 거래 연결"' in html
    assert 'aria-label="수동 분류"' in html


def test_mobile_transaction_styles_keep_touch_targets_and_desktop_table_separate():
    html = Path('templates/cards.html').read_text(encoding='utf-8')
    css = Path('static/css/style.css').read_text(encoding='utf-8')

    assert 'id="txDesktopTableWrap"' in html
    assert '@media (max-width: 767.98px)' in css
    assert '.mobile-tx-list' in css
    assert '.mobile-tx-row' in css
    assert '.mobile-tx-save-bar' in css
    assert '.mobile-tx-sheet' in css
    assert 'min-height: 44px' in css
    assert '@media (prefers-reduced-motion: reduce)' in css


def test_mobile_transaction_controller_stages_changes_and_uses_one_bulk_save():
    html = Path('templates/cards.html').read_text(encoding='utf-8')

    for method in (
        'load(', 'openSheet(', 'applyField(', 'applyBulk(', 'save(', 'hasUnsavedChanges(',
    ):
        assert method in html
    assert 'const mobileTxController' in html
    assert 'new Map()' in html
    assert 'new Set()' in html
    assert "'/api/card-tx/bulk'" in html
    assert "method: 'PUT'" in html
    assert "['category', 'fund_group_id']" in html
    assert "window.addEventListener('beforeunload'" in html


def test_mobile_transaction_controller_keeps_drafts_after_save_errors():
    html = Path('templates/cards.html').read_text(encoding='utf-8')

    assert 'response.status === 409' in html
    assert 'this.rowErrors' in html
    assert 'this.draftChanges.clear()' in html
    assert 'this.render();' in html

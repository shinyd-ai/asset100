from pathlib import Path


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def test_cards_page_uses_transaction_first_workspace():
    html = _read("templates/cards.html")
    css = _read("static/css/style.css")

    assert 'href="#tab-tx" class="card-workspace-nav active"' in html
    assert 'class="tab-pane fade show active" id="tab-tx"' in html
    assert 'class="tab-pane fade" id="tab-info"' in html
    assert '카드 설정 및 가져오기' in html
    assert 'grid-template-columns: repeat(auto-fit, minmax(135px, 1fr))' in css
    assert '.card-workspace-nav.active > *' in css


def test_cards_page_has_accessible_status_and_filter_labels():
    html = _read("templates/cards.html")

    assert 'aria-live="polite"' in html
    assert 'for="txCardFilter"' in html
    assert 'for="txYear"' in html
    assert 'for="txMonth"' in html
    assert 'for="mailScanCount"' in html
    assert 'for="mailPassword6"' in html


def test_transaction_memo_is_compact_but_keeps_full_text_available():
    html = _read("templates/cards.html")
    css = _read("static/css/style.css")

    assert "render: v => renderTxMemo(v)" in html
    assert 'class="tx-memo-cell"' in html
    assert 'title="${escaped}"' in html
    assert '.tx-memo-cell' in css
    assert 'text-overflow: ellipsis' in css


def test_transaction_memo_does_not_render_mail_or_coupang_badges():
    html = _read("templates/cards.html")
    css = _read("static/css/style.css")

    assert "replaceAll('[메일자동등록]'" not in html
    assert "replaceAll('[쿠팡]'" not in html
    assert '.tx-memo-badge' not in css


def test_transaction_analytics_are_above_a_full_width_table():
    html = _read("templates/cards.html")

    assert 'class="row g-3 tx-analytics-row mb-3"' in html
    assert 'class="col-12 order-3" id="txHistoryCard" style="display:none"' in html
    assert 'class="col-md-6 order-1"' in html
    assert 'class="col-md-6 order-2"' in html
    assert "historyCard.style.display = ''" in html
    assert '.tx-analytics-body { max-height: none; overflow: visible; }' in _read("static/css/style.css")
    assert html.index('id="txHistoryPanel"') < html.index('id="txCardFilter"')
    assert html.index('id="fundBreakdown"') < html.index('id="txCardFilter"')
    assert html.index('id="catBreakdown"') < html.index('id="txCardFilter"')
    assert '<div class="col-12">\n        <div class="card border-0 shadow-sm">' in html


def test_shared_layout_keeps_navigation_available_on_mobile():
    html = _read("templates/base.html")
    css = _read("static/css/style.css")

    assert 'id="mobileNavToggle"' in html
    assert 'aria-controls="sidebar"' in html
    assert 'id="main-content" tabindex="-1"' in html
    assert '#mobileNavToggle' in css
    assert 'body.sidebar-open #sidebar' in css
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr))' in css
    assert 'sidebar.inert = !open' in html
    assert "sidebar.setAttribute('aria-hidden', String(!open))" in html
    assert 'toggle.focus()' in html


def test_mail_sync_uses_two_reviewable_checkbox_steps():
    from app import app

    html = app.test_client().get('/cards').get_data(as_text=True)

    assert 'id="btnScanStatements"' in html
    assert 'id="btnImportStatements"' in html
    assert 'id="btnScanPayments"' in html
    assert 'id="btnImportPayments"' in html
    assert 'id="statementCandidateBody"' in html
    assert 'id="paymentCandidateBody"' in html
    assert 'id="statementResultBody"' in html
    assert 'id="paymentResultBody"' in html
    assert 'id="statementResultSummary"' in html
    assert 'id="paymentResultSummary"' in html
    assert 'type="checkbox"' in html
    assert "candidate-checkbox" in html
    assert "/api/card-tx/mail-candidates" in html
    assert "/api/card-tx/sync-selected-mail" in html
    assert "/api/card-tx/approve-unmatched-mail" in html
    assert 'id="paymentUnmatchedBody"' in html
    assert 'id="btnApproveUnmatched"' in html
    assert "approveUnmatchedMail" in html
    assert "matched_transaction" in html
    assert "renderMailTransactionResults" in html

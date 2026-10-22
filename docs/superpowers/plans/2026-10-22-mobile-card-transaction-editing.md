# Mobile Card Transaction Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a phone user stage and save single or multiple card transaction edits safely, while keeping connected budget records consistent.

**Architecture:** Keep the existing desktop `GridTable` intact and add a mobile-only transaction list and bottom-sheet controller in `templates/cards.html`. Add one atomic `PUT /api/card-tx/bulk` endpoint that validates every requested patch, detects stale edits with caller-provided base values, updates `card_tx`, and synchronizes shared fields to linked `budget` records in the same PostgreSQL transaction.

**Tech Stack:** Flask, PostgreSQL via psycopg2, Jinja templates, vanilla JavaScript, Bootstrap Icons, CSS media queries, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-mobile-card-transaction-edit-design.md`

## Global Constraints

- Keep `PUT /api/card-tx/<id>` and the desktop `GridTable` editing flow working unchanged.
- Use `PUT /api/card-tx/bulk` for all mobile saves; do not persist changes before the user presses Save.
- A bulk request must validate all rows and reject the complete request on validation error (`400`) or stale base value conflict (`409`).
- Update `budget.date`, `budget.name`, `budget.category`, `budget.amount`, and `budget.memo` only when the corresponding `card_tx.budget_id` is present.
- Do not alter `card_payment_links` or its link count during transaction editing.
- Multi-select edits may set only category and fund group.
- Display and preserve manual classification (`category_locked`, `fund_group_locked`); it remains editable and a new manual value remains locked.
- Support 360px and 390px widths, 44px minimum touch targets, text labels/accessible names for status icons, and reduced-motion preference.

## Review Focus

- A mixed batch with one invalid amount, date, missing transaction, or nonexistent fund group must leave all records unchanged; covered by Task 1 API tests.
- A transaction changed after mobile load must return `409` instead of overwriting the changed field; covered by Task 1 API tests.
- A linked budget transaction and an unlinked transaction in one batch must update only the linked budget’s shared fields; covered by Task 1 API tests.
- An existing mail link must retain both its database record and returned `linked_payment_count` after a bulk edit; covered by Task 1 API tests.
- Closing the sheet, changing a filter, or receiving a save error must not silently discard staged client edits; covered by Task 3 controller tests and Task 4 manual viewport checks.

---

## File Structure

- `app.py`: enriches card transaction list rows and implements validation, conflict detection, atomic mutation, and budget synchronization for `PUT /api/card-tx/bulk`.
- `templates/cards.html`: adds mobile transaction markup and the `mobileTxController` state/controller while retaining the existing desktop grid.
- `static/css/style.css`: contains mobile-only transaction list, bottom sheet, fixed save bar, connection status, focus, and responsive rules.
- `test_mobile_card_transaction_editing.py`: provides API fixture coverage and static contract tests for the mobile UI/controller.
- `test_cards_design.py`: extends existing template/CSS contract coverage only if an existing general design test is the clearest home for a shared accessibility assertion.

### Task 1: Atomic bulk-save API

**Files:**
- Modify: `app.py:242-298` and `app.py:1407-1435`
- Create: `test_mobile_card_transaction_editing.py`

**Interfaces:**
- Consumes: `PUT /api/card-tx/bulk` request body `{changes: Array<{id: number, base: object, fields: object}>}`.
- Produces: `200 {ok: true, updated: number, budget_synced: number, rows: CardTransaction[]}`, `400 {error: string, row_errors: RowError[]}`, or `409 {error: string, row_errors: RowError[]}`.
- Produces: `GET /api/card-tx` rows with `budget_linked: boolean`, `fund_group_name: string|null`, `linked_payment_count`, and existing card transaction fields.

- [ ] **Step 1: Write failing API tests in `test_mobile_card_transaction_editing.py`**

Create an in-memory database fixture containing `card_info`, `fund_groups`, `budget`, `card_tx`, and `card_payment_links`. Add these tests:

```python
def test_card_tx_list_exposes_budget_and_fund_group_display_data(client):
    response = client.get('/api/card-tx?year=2026&month=09')
    row = response.json['rows'][0]
    assert row['budget_linked'] is True
    assert row['fund_group_name'] == '생활비'
    assert row['linked_payment_count'] == 1

def test_bulk_save_updates_card_and_linked_budget_atomically(client, db):
    response = client.put('/api/card-tx/bulk', json=valid_changes())
    assert response.status_code == 200
    assert response.json == {'ok': True, 'updated': 2, 'budget_synced': 1, 'rows': response.json['rows']}
    assert card_and_budget_shared_fields_match(db, card_tx_id=1)

def test_bulk_save_rejects_whole_batch_when_one_row_is_invalid(client, db):
    response = client.put('/api/card-tx/bulk', json=changes_with_invalid_fund_group())
    assert response.status_code == 400
    assert card_rows_equal_before_snapshot(db)

def test_bulk_save_returns_conflict_without_writing(client, db):
    change_card_name_after_client_load(db, card_tx_id=1)
    response = client.put('/api/card-tx/bulk', json=changes_with_stale_base())
    assert response.status_code == 409
    assert current_card_name(db, 1) == '새 서버 값'

def test_bulk_save_preserves_payment_link_and_manual_locks(client, db):
    response = client.put('/api/card-tx/bulk', json=valid_manual_classification_change())
    assert response.status_code == 200
    assert payment_link_count(db, 1) == 1
    assert locks_for(db, 1) == (1, 1)
```

- [ ] **Step 2: Run the new API tests to verify they fail**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: FAIL because the bulk `PUT` route and enriched list fields do not exist.

- [ ] **Step 3: Implement the list projection and bulk update helpers in `app.py`**

Add `serialize_card_transaction(row)` for the list response, including `budget_linked` and `fund_group_name`. Add `validate_bulk_card_tx_changes(db, changes)` returning `(validated_changes, row_errors, status_code)` and `apply_bulk_card_tx_changes(db, validated_changes)` returning `(updated, budget_synced, rows)`. Validate IDs, dates (`YYYY-MM-DD`), integer amounts, installments of at least one, existing fund group IDs, editable field names, and base values for each changed field. Use `db.rollback()` before any error response after a transaction has begun.

For each validated patch, set `category_locked=1` only for a nonempty supplied category and `fund_group_locked=1` only for a non-null supplied fund group. A deliberate clear sets its corresponding lock to `0`, matching the existing single-row edit behavior. Update linked `budget` shared fields only when their corresponding values are included in `fields`.

- [ ] **Step 4: Add `PUT /api/card-tx/bulk` and enrich `GET /api/card-tx` in `app.py`**

Route `PUT /api/card-tx/bulk` before the existing delete-only bulk route. Keep `DELETE /api/card-tx/bulk` on the same URL. Ensure every transaction exists and every `base` value matches the current DB value for a field present in `fields` before any update executes. Return row-specific errors with the transaction `id` and field name.

- [ ] **Step 5: Run the API tests to verify they pass**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the API work**

```bash
git add app.py test_mobile_card_transaction_editing.py
git commit -m "feat: add atomic mobile card transaction saves"
```

### Task 2: Mobile transaction shell and responsive styles

**Files:**
- Modify: `templates/cards.html:418-516`
- Modify: `static/css/style.css:228-275` and append card transaction mobile rules
- Modify: `test_mobile_card_transaction_editing.py`

**Interfaces:**
- Consumes: list rows from `GET /api/card-tx` and category/fund-group arrays already loaded by `init()`.
- Produces: stable mobile DOM IDs `mobileTxList`, `mobileTxSelectionToggle`, `mobileTxSaveBar`, `mobileTxEditSheet`, `mobileTxCategory`, and `mobileTxFundGroup`.
- Produces: CSS classes `mobile-tx-list`, `mobile-tx-row`, `mobile-tx-save-bar`, `mobile-tx-sheet`, `tx-link-status`, and `tx-manual-status`.

- [ ] **Step 1: Write failing static UI contract tests**

Add assertions that the template includes each stable DOM ID, text alternatives for mail/budget/manual statuses, a 44px touch target declaration, a reduced-motion rule, and CSS media queries that hide the mobile list above the mobile breakpoint and hide the desktop table on mobile.

- [ ] **Step 2: Run the static UI tests to verify they fail**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: FAIL because the mobile shell and styles are absent.

- [ ] **Step 3: Add semantic mobile markup to `templates/cards.html`**

Within the transaction tab, add the card/period filter reuse area, the selection toggle, a polite live status region, empty/loading list container, fixed save bar, a bottom-sheet dialog with focusable close control, and controls for merchant, category, fund group, amount, memo, installment, date, and expandable details. Use actual `<label>` elements and `aria-describedby` for connection status; do not duplicate desktop table IDs.

- [ ] **Step 4: Add responsive and accessibility CSS in `static/css/style.css`**

At `max-width: 767.98px`, make the mobile list visible and hide only the desktop transaction table/card. Add 44px minimum control sizing, safe-area padding for the fixed save bar, focus-visible outlines, sufficient contrast using the four approved colors, and an existing-style `prefers-reduced-motion` override for the sheet. Preserve desktop styles at widths above this breakpoint.

- [ ] **Step 5: Run the static UI tests to verify they pass**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the mobile shell and styles**

```bash
git add templates/cards.html static/css/style.css test_mobile_card_transaction_editing.py
git commit -m "feat: add mobile card transaction editing shell"
```

### Task 3: Staged editing, selection, and save controller

**Files:**
- Modify: `templates/cards.html:608-732` and append mobile controller functions near the existing `txGrid`
- Modify: `test_mobile_card_transaction_editing.py`

**Interfaces:**
- Consumes: `mobileTxController.load(filters)`, API list rows, and `PUT /api/card-tx/bulk` responses from Task 1.
- Produces: `mobileTxController` with `rows`, `draftChanges`, `selectedIds`, `selectionMode`, `openSheet(id)`, `applyField(id, field, value)`, `applyBulk(fields)`, `save()`, and `hasUnsavedChanges()`.
- Produces: request patches shaped as `{id, base, fields}`, where `base` contains only original values for fields in `fields`.

- [ ] **Step 1: Write failing controller contract tests**

Assert the template defines every listed controller method and references `new Map()`, `new Set()`, `PUT`, `/api/card-tx/bulk`, `409`, a before-unload or filter-change unsaved-change guard, and code that retains drafts on non-OK responses. Assert the multi-select handler accepts only `category` and `fund_group_id`.

- [ ] **Step 2: Run the controller contract tests to verify they fail**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: FAIL because the mobile controller does not exist.

- [ ] **Step 3: Implement `mobileTxController` in `templates/cards.html`**

Render each row by overlaying `draftChanges` onto the source row. Include envelope-check, link, and pin icons only when `linked_payment_count`, `budget_linked`, `category_locked`, or `fund_group_locked` requires them, with Korean accessible labels. Keep the desktop `txGrid` load behavior and call the mobile controller from the same filter change/load path without sharing mutable selected state.

- [ ] **Step 4: Implement staging and mobile save behavior**

`applyField()` stores only changed values and removes a field when it equals its original value. `applyBulk()` refuses keys other than `category` and `fund_group_id`. `save()` constructs base values from the source rows, presents a summary including linked budget count, disables duplicate submissions, sends one `PUT`, retains drafts and renders row errors for `400`/`409`/network errors, and reloads after success. Guard bottom-sheet close, card/period filter changes, and page exit when drafts exist.

- [ ] **Step 5: Run controller contract tests to verify they pass**

Run: `pytest test_mobile_card_transaction_editing.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the staged editing controller**

```bash
git add templates/cards.html test_mobile_card_transaction_editing.py
git commit -m "feat: stage mobile card transaction edits"
```

### Task 4: End-to-end verification and regression review

**Files:**
- Modify: `test_mobile_card_transaction_editing.py` only if a discovered requirement lacks automated coverage

**Interfaces:**
- Consumes: completed API and mobile controller from Tasks 1–3.
- Produces: verification evidence; no production interface changes unless a defect is found.

- [ ] **Step 1: Run focused and existing regression tests**

Run: `pytest test_mobile_card_transaction_editing.py test_payment_mail_linking.py test_cards_design.py -q`

Expected: PASS.

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`

Expected: PASS, or document unrelated existing failures before making any change outside this feature.

- [ ] **Step 3: Perform manual browser verification at 360px, 390px, and desktop widths**

Verify: filters load the right rows; a row opens the sheet; one primary-field edit remains local before save; detail fields work; selection mode applies only category/fund group; save result reports card and budget counts; linked/pinned icons have labels; a `409` or validation error keeps drafts; desktop inline edits and delete still work.

- [ ] **Step 4: Inspect the final diff and commit any verification-driven correction**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors and no unintended generated files. If a correction is needed, add a focused test first and commit it with the correction.


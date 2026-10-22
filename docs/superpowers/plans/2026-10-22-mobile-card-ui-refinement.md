# Mobile Card UI Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mobile card transaction presentation with the approved calm financial-app visual design while preserving every editing and saving behavior.

**Architecture:** Keep `mobileTxController` and its API contract unchanged. Reshape only its rendered DOM and the mobile CSS so the list, status chips, summary, save bar, and bottom sheet use one visual system; desktop `GridTable` remains unchanged.

**Tech Stack:** Jinja, vanilla JavaScript, CSS media queries, Bootstrap Icons, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-mobile-card-transaction-edit-design.md`

## Global Constraints

- Apply this visual system only at `max-width: 767.98px`.
- Preserve the one-request `PUT /api/card-tx/bulk` save flow, draft preservation, conflict display, and connection/manual-status semantics.
- Preserve 44px touch targets, accessible icon names, and reduced-motion behavior.
- Use `#F4F6F8`, `#17212B`, `#126B73`, `#E5F3F1`, `#F5ECDF`, and `#E7EBEF` for the refined mobile visual system.

## Review Focus

- Desktop GridTable markup and behavior remain available; covered by Task 2 regression tests.
- A linked-mail, linked-budget, and manually classified row retain distinct accessible icons; covered by Task 1 rendering tests.
- Long merchant names and large amounts remain readable without overlapping; covered by Task 2 manual mobile viewport check.
- Save/error status remains visible above the fixed action bar; covered by Task 1 DOM/CSS tests.
- The bottom sheet remains reachable above the mobile browser safe area; covered by Task 2 manual mobile viewport check.

---

### Task 1: Refined transaction DOM and style contract

**Files:**
- Modify: `templates/cards.html`
- Modify: `static/css/style.css`
- Modify: `test_mobile_card_transaction_editing.py`

**Interfaces:**
- Consumes: Existing `mobileTxController.statusIcons(row)` and `render()` row data.
- Produces: CSS classes `mobile-tx-summary`, `mobile-tx-chip`, `mobile-tx-chip--fund`, `mobile-tx-amount`, and `mobile-tx-merchant` without changing controller method names or DOM IDs.

- [ ] **Step 1: Write failing static design contract tests**

Assert the approved color tokens, summary markup, distinct category/fund chip classes, visible mobile status, and 44px touch target styles exist. Assert the desktop table wrapper still exists.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest test_mobile_card_transaction_editing.py -q`

Expected: FAIL because the refined DOM/classes are absent.

- [ ] **Step 3: Update `mobileTxController.render()` and the mobile transaction shell**

Render a monthly summary above the list, a compact transaction count/select line, restrained connection icons beside merchant names, separate category/fund chips, and right-aligned amount/date. Preserve selection controls, draft/error display, and all existing IDs.

- [ ] **Step 4: Replace mobile-only styles in `static/css/style.css`**

Use the approved palette, reduce repeated card borders, add readable spacing and truncation, style the fixed save bar and status area, and restyle the edit sheet. Do not alter desktop styles.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m pytest test_mobile_card_transaction_editing.py -q` then `python -m pytest -q`

Expected: PASS, aside from any pre-existing local Hyundai test collection failure documented before this plan.

- [ ] **Step 6: Commit**

```bash
git add templates/cards.html static/css/style.css test_mobile_card_transaction_editing.py
git commit -m "feat: refine mobile card transaction design"
```

### Task 2: Render, syntax, and responsive verification

**Files:**
- Modify: `test_mobile_card_transaction_editing.py` only if an uncovered requirement needs a regression test.

**Interfaces:**
- Consumes: Completed refined mobile markup and styles from Task 1.
- Produces: Verification evidence; no production interface change unless a defect is found.

- [ ] **Step 1: Verify the cards page and inline JavaScript**

Run Flask `/cards` rendering and parse the inline script with Node `new Function`.

- [ ] **Step 2: Check responsive layout at 360px and 390px**

Verify the summary, long merchant names, category/fund chips, fixed save bar, and bottom sheet do not overlap and remain usable. Verify desktop still presents the GridTable.

- [ ] **Step 3: Run final checks**

Run: `python -m pytest test_mobile_card_transaction_editing.py test_payment_mail_linking.py test_cards_design.py -q`, `git diff --check`, and `git status --short`.


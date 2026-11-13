import hashlib
import imaplib
import email
import json
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from card_mail_service import (
    NAVER_EMAIL, NAVER_PASSWORD, _candidate_fingerprint,
    classify_mail_candidate, decode_imap_utf7, decode_mail_candidate_id,
    encode_mail_candidate_id,
    decode_mime_words, select_sync_mailboxes, validate_scan_limit,
)


load_dotenv()

COUPANG_MAILBOXES = ('INBOX', '청구·결제')
COMMERCE_MAILBOXES = ('INBOX', '청구·결제')
CARD_COMPANIES = (
    '현대카드', '삼성카드', '롯데카드', '하나카드', '신한카드',
    'KB국민카드', '국민카드', '우리카드', 'NH농협카드', '농협카드',
)

COUPANG_ORDER_SUBJECT_RE = re.compile(
    r'^\[쿠팡\]\s+.+님 주문하신 내역을 확인해주세요\.\s*$'
)


def _amount(value):
    digits = re.sub(r'[^0-9-]', '', str(value or ''))
    if not digits or digits == '-':
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _normalize_card_company(text):
    for company in CARD_COMPANIES:
        if company in (text or ''):
            return company.replace('KB국민카드', '국민카드').replace('NH농협카드', '농협카드')
    return None


def is_coupang_order_subject(subject):
    return bool(COUPANG_ORDER_SUBJECT_RE.match(str(subject or '').strip()))


def is_auction_order_subject(subject, sender=None):
    subj = str(subject or '').strip()
    snd = str(sender or '').strip().lower()
    if 'auction' in snd or '옥션' in snd:
        return any(phrase in subj for phrase in ('주문해 주셔서 감사합니다', '주문해주셔서 감사합니다'))
    return any(phrase in subj for phrase in ('주문해 주셔서 감사합니다', '주문해주셔서 감사합니다')) and ('옥션' in subj or 'auction' in subj)


def parse_coupang_order_html(html, order_date, source_key):
    soup = BeautifulSoup(html or '', 'html.parser')
    products = []
    total_amount = None
    payment_method = None

    for tr in soup.find_all('tr'):
        if tr.find('tr'):
            continue
        cells = [' '.join(cell.get_text(' ', strip=True).split())
                 for cell in tr.find_all(['th', 'td'], recursive=False)]
        if not cells:
            continue

        if len(cells) >= 2 and '총 결제금액' in cells[0]:
            total_amount = _amount(cells[1])

        if len(cells) >= 2 and '카드' in cells[0] and _amount(cells[1]) is not None:
            payment_method = cells[0]

        if len(cells) == 5:
            name = cells[0].strip()
            unit_amount = _amount(cells[1])
            quantity = _amount(cells[2])
            purchase_amount = _amount(cells[3])
            if (name and name != '구매 상세내역' and unit_amount is not None
                    and quantity is not None and purchase_amount is not None):
                products.append({
                    'name': name,
                    'quantity': quantity,
                    'amount': purchase_amount,
                })

    if total_amount is None or not products:
        return None

    first = products[0]
    product_summary = first['name']
    if first['quantity'] > 1:
        product_summary += f" ×{first['quantity']}"
    if len(products) > 1:
        product_summary += f" 외 {len(products) - 1}건"

    return {
        'source_key': source_key,
        'vendor': '쿠팡',
        'order_date': order_date,
        'total_amount': total_amount,
        'payment_method': payment_method or '',
        'card_company': _normalize_card_company(payment_method),
        'products': products,
        'product_summary': product_summary[:500],
    }


def parse_auction_order_html(html, order_date, source_key):
    soup = BeautifulSoup(html or '', 'html.parser')
    products = []

    for a in soup.find_all('a'):
        href = a.get('href', '')
        if 't=vp' in href or 'itempage3' in href:
            name = ' '.join(a.get_text(' ', strip=True).split())
            if not name or len(name) < 2:
                continue
            quantity = 1
            amount = None
            parent_td = a.find_parent('td')
            if parent_td:
                td_text = parent_td.get_text(' ', strip=True)
                m = re.search(r'([0-9,]+)\s*원\s*\(\s*수량\s*([0-9]+)\s*개\s*\)', td_text)
                if m:
                    amount = _amount(m.group(1))
                    quantity = int(m.group(2))
            products.append({
                'name': name,
                'quantity': quantity,
                'amount': amount or 0,
            })

    total_amount = None
    payment_method = None
    for tr in soup.find_all('tr'):
        if tr.find('tr'):
            continue
        cells = [' '.join(cell.get_text(' ', strip=True).split())
                 for cell in tr.find_all(['th', 'td'], recursive=False)]
        if len(cells) == 2:
            key, val = cells[0], cells[1]
            if any(pm in key for pm in ['Smile Pay', '스마일페이', '카드', '신용카드', '체크카드', '간편결제', '계좌이체']):
                amt = _amount(val)
                if amt is not None and amt > 0:
                    total_amount = amt
                    payment_method = key

    if total_amount is None:
        for tr in soup.find_all('tr'):
            if tr.find('tr'):
                continue
            cells = [' '.join(cell.get_text(' ', strip=True).split())
                     for cell in tr.find_all(['th', 'td'], recursive=False)]
            if len(cells) >= 2 and any(k in cells[0] for k in ['결제금액', '총 결제금액', '최종 결제금액']):
                amt = _amount(cells[1])
                if amt is not None and amt > 0:
                    total_amount = amt
                    payment_method = payment_method or cells[0]

    if total_amount is None or not products:
        return None

    first = products[0]
    product_summary = first['name']
    if first['quantity'] > 1:
        product_summary += f" ×{first['quantity']}"
    if len(products) > 1:
        product_summary += f" 외 {len(products) - 1}건"

    return {
        'source_key': source_key,
        'vendor': '옥션',
        'order_date': order_date,
        'total_amount': total_amount,
        'payment_method': payment_method or '',
        'card_company': _normalize_card_company(payment_method),
        'products': products,
        'product_summary': product_summary[:500],
    }


def choose_card_transaction(order, candidates):
    result = match_card_transactions(order, candidates)
    return result['card_tx_id'] if result['status'] == 'matched' else None


def match_card_transactions(order, candidates):
    vendor = order.get('vendor') or '쿠팡'
    card_company = order.get('card_company')
    if vendor == '쿠팡' and not card_company:
        return {'status': 'unmatched', 'card_tx_id': None, 'candidate_count': 0}

    try:
        order_day = datetime.strptime(order['order_date'], '%Y-%m-%d').date()
    except (KeyError, TypeError, ValueError):
        return {'status': 'unmatched', 'card_tx_id': None, 'candidate_count': 0}

    matches = []
    for candidate in candidates:
        linked_source_key = candidate.get('linked_source_key')
        if linked_source_key and linked_source_key != order.get('source_key'):
            continue
        try:
            candidate_day = datetime.strptime(str(candidate.get('date')), '%Y-%m-%d').date()
        except ValueError:
            continue
        merchant = str(candidate.get('name') or '').lower()

        if vendor == '쿠팡':
            if ('쿠팡이츠' in merchant or 'coupang eats' in merchant
                    or 'coupangeats' in merchant):
                continue
            if '쿠팡' not in merchant and 'coupang' not in merchant:
                continue
            if card_company and card_company not in str(candidate.get('card_name') or ''):
                continue
        elif vendor == '옥션':
            auction_hints = ('옥션', 'auction', '스마일페이', 'smilepay', '지마켓', 'gmarket', '이베이코리아')
            if not any(hint in merchant for hint in auction_hints):
                continue
            if card_company and card_company not in str(candidate.get('card_name') or ''):
                continue
        else:
            if vendor.lower() not in merchant:
                continue
            if card_company and card_company not in str(candidate.get('card_name') or ''):
                continue

        if int(candidate.get('amount') or 0) != int(order.get('total_amount') or 0):
            continue
        if abs((candidate_day - order_day).days) > 3:
            continue
        matches.append(candidate.get('id'))

    if len(matches) == 1:
        return {'status': 'matched', 'card_tx_id': matches[0], 'candidate_count': 1}
    if len(matches) > 1:
        return {'status': 'ambiguous', 'card_tx_id': None, 'candidate_count': len(matches)}
    return {'status': 'unmatched', 'card_tx_id': None, 'candidate_count': 0}


def build_commerce_merchant_name(vendor, product_summary):
    summary = str(product_summary or '').strip()
    return f"{vendor} - {summary}"[:500] if summary else vendor


def build_coupang_merchant_name(product_summary):
    return build_commerce_merchant_name('쿠팡', product_summary)


def remove_commerce_memo(existing_memo, vendor, product_summary):
    marker = f"[{vendor}] {product_summary}".strip()
    parts = [part.strip() for part in str(existing_memo or '').split(' | ')]
    return ' | '.join(part for part in parts if part and part != marker)


def remove_coupang_memo(existing_memo, product_summary):
    return remove_commerce_memo(existing_memo, '쿠팡', product_summary)


def add_commerce_memo(existing_memo, vendor, product_summary, previous_summary=None):
    memo = remove_commerce_memo(existing_memo, vendor, previous_summary) if previous_summary else str(existing_memo or '').strip()
    summary = str(product_summary or '').strip()
    if not summary:
        return memo
    memo = remove_commerce_memo(memo, vendor, summary)
    marker = f"[{vendor}] {summary}"
    return f"{memo} | {marker}" if memo else marker


def add_coupang_memo(existing_memo, product_summary, previous_summary=None):
    return add_commerce_memo(existing_memo, '쿠팡', product_summary, previous_summary)


def restore_commerce_merchant(current_name, original_name, vendor, product_summary):
    if current_name != build_commerce_merchant_name(vendor, product_summary):
        return current_name
    if original_name and original_name != current_name:
        return original_name
    return vendor


def restore_coupang_merchant(current_name, original_name, product_summary):
    return restore_commerce_merchant(current_name, original_name, '쿠팡', product_summary)


def reconcile_commerce_orders(db, orders):
    summary = {'orders': len(orders), 'matched': 0, 'ambiguous': 0, 'unmatched': 0}
    for order in orders:
        vendor = order.get('vendor') or '쿠팡'
        order_day = datetime.strptime(order['order_date'], '%Y-%m-%d').date()
        start = (order_day - timedelta(days=3)).isoformat()
        end = (order_day + timedelta(days=3)).isoformat()
        candidates = db.execute(
            "SELECT t.id, t.date, t.amount, t.name, t.memo, c.card_name, "
            "co.source_key AS linked_source_key "
            "FROM card_tx t LEFT JOIN card_info c ON t.card_id = c.id "
            "LEFT JOIN commerce_orders co ON co.matched_card_tx_id = t.id "
            "WHERE t.amount = ? AND t.date BETWEEN ? AND ? ORDER BY t.id",
            (order['total_amount'], start, end)
        ).fetchall()
        candidates = [dict(row) for row in candidates]
        match = match_card_transactions(order, candidates)

        existing = db.execute(
            "SELECT id, matched_card_tx_id, product_summary, original_tx_name FROM commerce_orders WHERE source_key = ?",
            (order['source_key'],)
        ).fetchone()
        matched_card_tx_id = match['card_tx_id']
        status = match['status']
        old_matched_id = existing.get('matched_card_tx_id') if existing else None
        if old_matched_id and old_matched_id != matched_card_tx_id:
            old_tx = db.execute("SELECT name, memo FROM card_tx WHERE id = ?", (old_matched_id,)).fetchone()
            if old_tx:
                cleaned = remove_commerce_memo(old_tx.get('memo'), vendor, existing.get('product_summary'))
                db.execute(
                    "UPDATE card_tx SET memo = ?, name = ? WHERE id = ?",
                    (cleaned, restore_commerce_merchant(
                        old_tx.get('name'), existing.get('original_tx_name'),
                        vendor, existing.get('product_summary')), old_matched_id)
                )

        candidate = next((row for row in candidates if row['id'] == matched_card_tx_id), None)
        original_tx_name = existing.get('original_tx_name') if existing else None
        if matched_card_tx_id and not original_tx_name and candidate:
            original_tx_name = candidate.get('name')

        db.execute(
            "INSERT INTO commerce_orders "
            "(source_key, vendor, order_date, total_amount, payment_method, card_company, product_summary, products_json, matched_card_tx_id, original_tx_name, sync_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (source_key) DO UPDATE SET "
            "vendor=EXCLUDED.vendor, order_date=EXCLUDED.order_date, total_amount=EXCLUDED.total_amount, "
            "payment_method=EXCLUDED.payment_method, card_company=EXCLUDED.card_company, "
            "product_summary=EXCLUDED.product_summary, products_json=EXCLUDED.products_json, "
            "matched_card_tx_id=EXCLUDED.matched_card_tx_id, "
            "original_tx_name=COALESCE(commerce_orders.original_tx_name, EXCLUDED.original_tx_name), "
            "sync_status=EXCLUDED.sync_status, updated_at=NOW()",
            (order['source_key'], order['vendor'], order['order_date'], order['total_amount'],
             order.get('payment_method'), order.get('card_company'), order['product_summary'],
             json.dumps(order['products'], ensure_ascii=False), matched_card_tx_id,
             original_tx_name, status)
        )

        if status == 'matched' and matched_card_tx_id:
            if candidate:
                previous_summary = existing.get('product_summary') if existing else None
                merchant_name = restore_commerce_merchant(
                    candidate.get('name'), original_tx_name,
                    vendor, previous_summary or order.get('product_summary'))
                memo = add_commerce_memo(
                    candidate.get('memo'), vendor, order.get('product_summary'), previous_summary)
                db.execute(
                    "UPDATE card_tx SET name = ?, memo = ? WHERE id = ?",
                    (merchant_name, memo, matched_card_tx_id)
                )

        summary[status] += 1
    return summary


reconcile_coupang_orders = reconcile_commerce_orders


def _decode_html_part(part):
    payload = part.get_payload(decode=True) or b''
    charset = part.get_content_charset() or 'utf-8'
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return payload.decode('utf-8', errors='ignore')


def fetch_commerce_orders(max_emails=50, selected_candidates=None):
    if not NAVER_EMAIL or not NAVER_PASSWORD:
        return {'ok': False, 'error': '네이버 메일 계정 설정이 없습니다.'}
    try:
        limit = validate_scan_limit(max_emails)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    mail = imaplib.IMAP4_SSL('imap.naver.com', 993)
    try:
        mail.login(NAVER_EMAIL, NAVER_PASSWORD)
        status, lines = mail.list()
        mailboxes = select_sync_mailboxes(lines if status == 'OK' else [])
        mailboxes = [box for box in mailboxes if decode_imap_utf7(box) in COMMERCE_MAILBOXES]
        found = {decode_imap_utf7(box) for box in mailboxes}
        missing = [name for name in COMMERCE_MAILBOXES if name not in found]
        if missing:
            return {'ok': False, 'error': '주문 메일함을 찾을 수 없습니다.', 'missing_mailboxes': missing}

        selected_by_mailbox = None
        if selected_candidates is not None:
            selected_by_mailbox = {}
            for candidate_id in selected_candidates:
                ref = decode_mail_candidate_id(candidate_id)
                selected_by_mailbox.setdefault(ref['mailbox'], []).append(ref)

        orders = []
        seen_messages = set()
        for mailbox in mailboxes:
            if selected_by_mailbox is not None and mailbox not in selected_by_mailbox:
                continue
            if mail.select(f'"{mailbox}"', readonly=True)[0] != 'OK':
                return {'ok': False, 'error': '주문 메일함을 열 수 없습니다.'}
            if selected_by_mailbox is None:
                # Search both Coupang and Auction
                found_targets = []
                for search_sender in ('"coupang"', '"auction"'):
                    status, data = mail.search(None, 'FROM', search_sender)
                    if status == 'OK' and data and data[0]:
                        found_targets.extend(data[0].split())
                # Deduplicate and sort by mail number
                unique_ids = sorted(list(set(found_targets)), key=lambda x: int(x))
                targets = [{'id': value, 'uid': False, 'fingerprint': None}
                           for value in reversed(unique_ids[-limit:])]
            else:
                targets = [{'id': ref['uid'].encode('ascii'), 'uid': True,
                            'fingerprint': ref['fingerprint']}
                           for ref in selected_by_mailbox[mailbox]]
            for target in targets:
                mail_id = target['id']
                if target['uid']:
                    status, header_data = mail.uid(
                        'fetch', mail_id,
                        '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])'
                    )
                    raw_header = next((part[1] for part in header_data if isinstance(part, tuple)), None)
                    if status != 'OK' or not raw_header:
                        continue
                    header = email.message_from_bytes(raw_header)
                    classification = classify_mail_candidate(
                        decode_mime_words(header.get('Subject')),
                        decode_mime_words(header.get('From')),
                    )
                    if (_candidate_fingerprint(header) != target['fingerprint']
                            or not classification or classification['type'] not in ('쿠팡 주문', '옥션 주문')):
                        continue
                    status, message_data = mail.uid('fetch', mail_id, '(BODY.PEEK[])')
                else:
                    status, message_data = mail.fetch(mail_id, '(RFC822)')
                if status != 'OK':
                    continue
                for part_data in message_data:
                    if not isinstance(part_data, tuple):
                        continue
                    raw_message = part_data[1]
                    identity = hashlib.sha256(raw_message).hexdigest()
                    if identity in seen_messages:
                        continue
                    seen_messages.add(identity)
                    message = email.message_from_bytes(raw_message)
                    subject = decode_mime_words(message.get('Subject'))
                    from_sender = decode_mime_words(message.get('From'))
                    
                    is_coupang = is_coupang_order_subject(subject)
                    is_auction = is_auction_order_subject(subject, from_sender)
                    if not is_coupang and not is_auction:
                        continue
                    try:
                        mail_date = parsedate_to_datetime(message.get('Date')).date().isoformat()
                    except (TypeError, ValueError, OverflowError):
                        continue
                    prefix = 'coupang-mail:' if is_coupang else 'auction-mail:'
                    source_key = prefix + identity
                    for mime_part in message.walk():
                        if mime_part.get_content_type() != 'text/html':
                            continue
                        html_text = _decode_html_part(mime_part)
                        order = parse_coupang_order_html(html_text, mail_date, source_key) if is_coupang else parse_auction_order_html(html_text, mail_date, source_key)
                        if order:
                            order['mailbox'] = decode_imap_utf7(mailbox)
                            order['subject'] = subject
                            if target['uid']:
                                order['candidate_id'] = encode_mail_candidate_id(
                                    mailbox, mail_id.decode('ascii'), target['fingerprint']
                                )
                            orders.append(order)
                            break
        return {'ok': True, 'orders': orders, 'count': len(orders)}
    except Exception:
        return {'ok': False, 'error': '주문메일 동기화에 실패했습니다.'}
    finally:
        try:
            mail.logout()
        except Exception:
            pass


fetch_coupang_orders = fetch_commerce_orders

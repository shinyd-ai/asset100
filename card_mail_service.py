import os
import imaplib
import email
import json
import re
import base64
import hashlib
import requests
from datetime import datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import urljoin, urlparse, urlencode

from statement_parser import (
    decrypt_unisafe_html, decrypt_vestmail_html, parse_hana_statement_html,
    parse_lotte_statement_html, parse_hyundai_statement_html,
    parse_samsung_statement_data,
)

load_dotenv()

NAVER_EMAIL = os.getenv('NAVER_EMAIL')
NAVER_PASSWORD = os.getenv('NAVER_PASSWORD')

SYNC_MAILBOX_NAMES = ('INBOX', '청구·결제', '청구서함')

CARD_SENDER_HINTS = (
    '카드', 'card', 'hanacard', 'shinhancard', 'samsungcard', 'hyundaicard',
    'lottecard', 'kbcard', 'wooricard', 'bccard', 'nonghyup',
)
PAYMENT_SENDER_HINTS = (
    *CARD_SENDER_HINTS, 'easypay', 'nicepg', 'kicc', 'smartro',
    'tosspayments', 'naver', 'coupang',
)


def classify_mail_candidate(subject, sender):
    """Classify a header without downloading its body or attachments."""
    subject = str(subject or '').strip()
    sender = str(sender or '').strip()
    combined = f'{subject} {sender}'.lower()
    if re.match(r'^\[쿠팡\]\s+.+님 주문하신 내역을 확인해주세요\.\s*$', subject):
        return {'group': 'payment', 'type': '쿠팡 주문'}

    excluded = ('배송', '발송', '약관', '개인정보', '포인트', '광고성', '이벤트')
    if any(word in subject for word in excluded):
        return None

    sender_is_card = any(hint in combined for hint in CARD_SENDER_HINTS)
    if sender_is_card and any(word in subject for word in ('이용대금명세서', '명세서', '청구서')):
        return {'group': 'statement', 'type': '명세서'}

    sender_is_payment = any(hint in combined for hint in PAYMENT_SENDER_HINTS)
    if sender_is_payment and any(word in subject for word in ('승인취소', '결제취소', '취소')):
        return {'group': 'payment', 'type': '취소'}
    if sender_is_payment and any(word in subject for word in ('승인', '결제', '영수증')):
        return {'group': 'payment', 'type': '결제'}
    return None


def encode_mail_candidate_id(mailbox, uid, fingerprint):
    payload = json.dumps({
        'mailbox': str(mailbox), 'uid': str(uid), 'fingerprint': str(fingerprint),
    }, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def decode_mail_candidate_id(candidate_id):
    try:
        token = str(candidate_id or '')
        raw = base64.urlsafe_b64decode(token + '=' * ((4 - len(token) % 4) % 4))
        data = json.loads(raw.decode('utf-8'))
        if set(data) != {'mailbox', 'uid', 'fingerprint'}:
            raise ValueError
        if not data['mailbox'] or not str(data['uid']).isdigit() or not data['fingerprint']:
            raise ValueError
        return {key: str(data[key]) for key in ('mailbox', 'uid', 'fingerprint')}
    except Exception as exc:
        raise ValueError('잘못된 메일 선택값입니다.') from exc


def _candidate_fingerprint(message):
    raw = '|'.join([
        str(message.get('Message-ID') or '').strip(),
        decode_mime_words(message.get('Subject')),
        decode_mime_words(message.get('From')),
        str(message.get('Date') or '').strip(),
    ])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def decode_imap_utf7(value):
    """Decode the modified UTF-7 names returned by IMAP LIST."""
    def replace(match):
        token = match.group(1)
        if token == '':
            return '&'
        token = token.replace(',', '/')
        token += '=' * ((4 - len(token) % 4) % 4)
        return base64.b64decode(token).decode('utf-16-be')

    return re.sub(r'&([^-]*)-', replace, value)


def select_sync_mailboxes(mailbox_lines):
    """Return server mailbox tokens for the three mailboxes we synchronize."""
    found = {}
    for raw_line in mailbox_lines or []:
        line = raw_line.decode('ascii', errors='replace') if isinstance(raw_line, bytes) else str(raw_line)
        quoted = re.findall(r'"([^"]*)"', line)
        token = quoted[-1] if quoted else line.rsplit(' ', 1)[-1].strip('"')
        decoded = decode_imap_utf7(token)
        if decoded in SYNC_MAILBOX_NAMES:
            found[decoded] = token
    return [found[name] for name in SYNC_MAILBOX_NAMES if name in found]


def build_source_key(message_id, source_kind, row_number):
    raw = f"{str(message_id or '').strip()}|{source_kind}|{int(row_number)}"
    return 'naver-mail:' + hashlib.sha256(raw.encode('utf-8')).hexdigest()


def build_message_identity(raw_message):
    return 'rfc822:' + hashlib.sha256(raw_message).hexdigest()


def validate_scan_limit(value):
    if isinstance(value, bool):
        raise ValueError('검색할 메일 수는 1~200 사이의 정수여야 합니다.')
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise ValueError('검색할 메일 수는 1~200 사이의 정수여야 합니다.')
    if limit < 1 or limit > 200:
        raise ValueError('검색할 메일 수는 1~200 사이의 정수여야 합니다.')
    return limit


def _candidate_card_company(subject, sender):
    text = f'{subject} {sender}'
    for company in (
        '하나카드', 'KB국민카드', '국민카드', '신한카드', '삼성카드',
        '현대카드', '롯데카드', '우리카드', 'BC카드', '비씨카드',
        'NH농협카드', '농협카드',
    ):
        if company in text:
            return company.replace('KB국민카드', '국민카드').replace('비씨카드', 'BC카드').replace('NH농협카드', '농협카드')
    return '카드'


def scan_mail_candidates(max_emails=50, group='statement'):
    """Return header-only candidates; no body is downloaded and no flags change."""
    if group not in ('statement', 'payment'):
        return {'ok': False, 'error': '메일 종류가 올바르지 않습니다.'}
    if not NAVER_EMAIL or not NAVER_PASSWORD:
        return {'ok': False, 'error': '.env에 네이버 메일 계정 설정이 없습니다.'}
    try:
        limit = validate_scan_limit(max_emails)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    mail = imaplib.IMAP4_SSL('imap.naver.com', 993)
    try:
        mail.login(NAVER_EMAIL, NAVER_PASSWORD)
        status, lines = mail.list()
        mailboxes = select_sync_mailboxes(lines if status == 'OK' else [])
        candidates = []
        seen_fingerprints = set()
        for mailbox in mailboxes:
            if mail.select(f'"{mailbox}"', readonly=True)[0] != 'OK':
                continue
            status, data = mail.uid('search', None, 'ALL')
            uids = data[0].split()[-limit:] if status == 'OK' and data and data[0] else []
            for uid in reversed(uids):
                status, parts = mail.uid(
                    'fetch', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])'
                )
                if status != 'OK':
                    continue
                raw = next((part[1] for part in parts if isinstance(part, tuple)), None)
                if not raw:
                    continue
                message = email.message_from_bytes(raw)
                subject = decode_mime_words(message.get('Subject'))
                sender = decode_mime_words(message.get('From'))
                classification = classify_mail_candidate(subject, sender)
                if not classification or classification['group'] != group:
                    continue
                fingerprint = _candidate_fingerprint(message)
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                try:
                    received_at = parsedate_to_datetime(message.get('Date')).date().isoformat()
                except (TypeError, ValueError, OverflowError):
                    received_at = ''
                candidates.append({
                    'candidate_id': encode_mail_candidate_id(mailbox, uid.decode('ascii'), fingerprint),
                    'type': classification['type'],
                    'date': received_at,
                    'card_company': _candidate_card_company(subject, sender),
                    'subject': subject,
                    'sender': sender,
                    'mailbox': decode_imap_utf7(mailbox),
                    'checked': True,
                })
        return {'ok': True, 'group': group, 'count': len(candidates), 'candidates': candidates}
    except Exception:
        return {'ok': False, 'error': '메일 후보를 조회하지 못했습니다.'}
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _samsung_common(sequence=1):
    now = datetime.now()
    return {
        'dlngEvnDvC': 'S',
        'scrnId': 'UBLDRP0201P4',
        'stdEtxtCrtDt': now.strftime('%Y%m%d'),
        'stdEtxtCrtSysNm': 'P0000000',
        'stdEtxtSn': now.strftime('%H%M%S%f')[:9] + f'{sequence:05d}',
        'stdEtxtPrgDvNo': 0,
        'stdEtxtPrgNo': 0,
        'indvInfIncYn': 'N',
        'usid': 'USERID0',
    }


def fetch_samsung_statement_data(decrypted_html, timeout=20):
    """Follow Samsung Card's statement token flow and return its JSON data."""
    soup = BeautifulSoup(decrypted_html or '', 'html.parser')
    form = soup.find('form')
    if not form or not form.get('action'):
        return None

    action = form.get('action').strip()
    parsed_action = urlparse(action)
    if parsed_action.scheme != 'https' or parsed_action.hostname != 'bill.samsungcard.com':
        return None
    fields = {
        item.get('name'): item.get('value', '')
        for item in form.find_all('input') if item.get('name')
    }
    if not fields.get('pvalue'):
        return None

    session = requests.Session()
    first = session.post(action, data=fields, timeout=timeout)
    first.raise_for_status()
    redirect_match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", first.text)
    if not redirect_match or not redirect_match.group(1).startswith('/personal/statement/'):
        return None

    statement_url = urljoin('https://bill.samsungcard.com', redirect_match.group(1))
    main = session.get(statement_url, timeout=timeout)
    main.raise_for_status()
    session_node = BeautifulSoup(main.text, 'html.parser').find(id='sessionStr')
    session_parts = (session_node.get('value', '').strip().split(',') if session_node else [])
    if len(session_parts) < 4:
        return None

    encrypted_number, statement_type, statement_date = session_parts[1:4]
    fragment_url = 'https://bill.samsungcard.com/personal/statement/card/UBLDRP0201M0.jsp?' + urlencode({
        'bilMngtNoEncr': encrypted_number,
        'bilMngtNoDvC': statement_type,
        'stlmDt': statement_date,
        'emailYn': 'Y',
    })
    fragment = session.get(fragment_url, timeout=timeout)
    fragment.raise_for_status()

    base_payload = {
        'batHistSeqn': 0,
        'bilMngtNo': 0,
        'bilMngtNoEncr': encrypted_number,
        'bilMngtNoDvC': statement_type,
        'stlmDt': statement_date,
    }
    summary = session.post(
        'https://bill.samsungcard.com/frontservice/SBLDRP0201S26',
        json={**base_payload, 'common': _samsung_common(1)},
        headers={'Referer': fragment_url}, timeout=timeout,
    )
    summary.raise_for_status()
    summary_data = summary.json()
    basic = summary_data.get('bDBDTBBLD001DVO') or {}

    details_payload = {
        **base_payload,
        'stlmDtEnc': basic.get('stlmDtEncr', ''),
        'cstMngtNoe': summary_data.get('cstMngtNoe', ''),
        'crtlPgeNo': 1,
        'expsrListEcn': 1000,
        'inqrRnk': 1,
        'exrCndtCn1': '',
        'exrCndtCn2': '',
        'exrCndtCn3': '',
        'common': _samsung_common(2),
    }
    details = session.post(
        'https://bill.samsungcard.com/frontservice/SBLDRP0201S25',
        json=details_payload, headers={'Referer': fragment_url}, timeout=timeout,
    )
    details.raise_for_status()
    result = details.json()
    if str((result.get('common') or {}).get('procsRsDvC', '0')) != '0':
        return None
    return result

def decode_mime_words(s):
    if not s:
        return ""
    decoded_fragments = decode_header(s)
    res = []
    for frag, enc in decoded_fragments:
        if isinstance(frag, bytes):
            encs = [enc, 'utf-8', 'euc-kr', 'cp949'] if enc else ['utf-8', 'euc-kr', 'cp949']
            decoded = False
            for e in encs:
                if not e: continue
                try:
                    res.append(frag.decode(e))
                    decoded = True
                    break
                except Exception:
                    continue
            if not decoded:
                res.append(frag.decode('utf-8', errors='replace'))
        else:
            res.append(str(frag))
    return "".join(res)

def extract_email_body_and_attachments(msg):
    """이메일 본문 텍스트 및 HTML 첨부파일 목록 추출"""
    body_text = ""
    html_attachments = []
    
    for part in msg.walk():
        ctype = part.get_content_type()
        cdisp = str(part.get('Content-Disposition'))
        fname = part.get_filename()
        if fname:
            fname = decode_mime_words(fname)
            
        payload = part.get_payload(decode=True)
        if not payload:
            continue
            
        charset = part.get_content_charset() or 'utf-8'
        try:
            raw_str = payload.decode(charset)
        except Exception:
            try:
                raw_str = payload.decode('euc-kr')
            except Exception:
                raw_str = payload.decode('cp949', errors='ignore')
                
        if fname and (fname.lower().endswith('.html') or fname.lower().endswith('.htm')):
            html_attachments.append({'filename': fname, 'content': raw_str, 'raw_bytes': payload})
        elif 'attachment' in cdisp:
            if fname and (fname.lower().endswith('.html') or fname.lower().endswith('.htm')):
                html_attachments.append({'filename': fname, 'content': raw_str, 'raw_bytes': payload})
        else:
            if ctype == 'text/html':
                soup = BeautifulSoup(raw_str, 'html.parser')
                for tag in soup(["script", "style"]):
                    tag.decompose()
                body_text += "\n" + soup.get_text(separator=' ')
            elif ctype == 'text/plain':
                body_text += "\n" + raw_str
                
    return body_text, html_attachments

def parse_card_email_text(subject, from_sender, body_text):
    """단건 실시간 승인 메일 파싱"""
    card_company = "카드"
    companies = ['하나카드', 'KB국민카드', '국민카드', '신한카드', '삼성카드', '현대카드', '롯데카드', '우리카드', 'BC카드', '비씨카드', 'NH농협카드', '농협카드', '카카오뱅크', '토스뱅크']
    for comp in companies:
        if comp in subject or comp in from_sender:
            card_company = comp.replace('KB국민카드', '국민카드').replace('NH농협카드', '농협카드').replace('비씨카드', 'BC카드')
            break
    if card_company == '카드':
        card_label = re.search(r'(?:신용|체크)\s*카드\s*[\(\[]\s*([^\)\]]+)', body_text)
        if card_label:
            for brand in ('하나', '국민', '신한', '삼성', '현대', '롯데', '우리', '농협', 'BC'):
                if brand in card_label.group(1):
                    card_company = brand + '카드'
                    break
    if card_company == '카드':
        for comp in companies:
            if comp in body_text:
                card_company = comp.replace('KB국민카드', '국민카드').replace('NH농협카드', '농협카드').replace('비씨카드', 'BC카드')
                break
            
    amount = None
    amt_patterns = [
        r'(?:이용금액|승인금액|결제금액|금액)\s*[:：]?\s*([0-9,]+)\s*원',
        r'([0-9,]+)\s*원\s*(?:결제|승인|출금)',
        r'([0-9,]+)\s*KRW',
        r'([0-9,]{2,})\s*원'
    ]
    for p in amt_patterns:
        m = re.search(p, body_text)
        if m:
            clean_amt = m.group(1).replace(',', '')
            if clean_amt.isdigit():
                amount = int(clean_amt)
                break

    tx_date = datetime.today().strftime('%Y-%m-%d')
    date_patterns = [
        r'(\d{4}[-./]\d{1,2}[-./]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)',
        r'(\d{1,2}[-./]\d{1,2}\s+\d{1,2}:\d{2})',
        r'(\d{1,2}월\s*\d{1,2}일\s*\d{1,2}:\d{2})'
    ]
    for p in date_patterns:
        m = re.search(p, body_text)
        if m:
            d_str = m.group(1).strip()
            try:
                clean_d = re.split(r'\s+', d_str)[0].replace('.', '-').replace('/', '-')
                parts = clean_d.split('-')
                if len(parts) == 3:
                    tx_date = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                elif len(parts) == 2:
                    current_year = datetime.today().year
                    tx_date = f"{current_year:04d}-{int(parts[0]):02d}-{int(parts[1]):02d}"
            except Exception:
                pass
            break

    merchant = ""
    merch_patterns = [
        r'(?:가맹점명?|이용가맹점|이용처|사용처|상호명)\s*[:：]?\s*([^\n\r,()]{2,30})',
        r'([^\n\r]{2,20})\s*(?:에서\s*)?[0-9,]+원\s*(?:결제|승인)'
    ]
    for p in merch_patterns:
        m = re.search(p, body_text)
        if m:
            candidate = m.group(1).strip()
            candidate = re.sub(r'[\(\)\[\]{}<>]', '', candidate).strip()
            if candidate and not any(k in candidate for k in ['이용금액', '승인번호', '카드번호', '합계', '누적']):
                merchant = candidate
                break

    if not merchant:
        if '결제' in subject or '승인' in subject:
            merchant = subject.replace('[', '').replace(']', '').strip()[:20]
        else:
            merchant = f"{card_company} 결제"

    installment = 1
    inst_match = re.search(r'(\d+)\s*개월\s*(?:할부)?', body_text)
    if inst_match:
        installment = int(inst_match.group(1))

    cancellation_detail = re.search(
        r'(?:승인|결제|이용)\s*취소|취소\s*(?:금액|승인|결제)', body_text
    )
    is_cancel = amount is not None and ('취소' in subject or bool(cancellation_detail))
    if is_cancel and amount:
        amount = -abs(amount)

    return {
        'card_company': card_company,
        'date': tx_date,
        'name': merchant,
        'amount': amount,
        'installment': installment,
        'is_cancel': is_cancel,
        'source': '실시간승인'
    }

def fetch_and_sync_card_emails(max_emails=50, mark_seen=False, password_6=None,
                               selected_candidates=None, sync_group=None):
    """
    네이버 IMAP에 접속하여 실시간 승인 메일 및 월별 이용명세서 첨부파일을 읽어 파싱
    """
    if not NAVER_EMAIL or not NAVER_PASSWORD:
        return {'ok': False, 'error': '.env에 NAVER_EMAIL 또는 NAVER_PASSWORD가 설정되지 않았습니다.'}

    try:
        max_emails = validate_scan_limit(max_emails)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    selected_by_mailbox = None
    if selected_candidates is not None:
        if sync_group not in ('statement', 'payment'):
            return {'ok': False, 'error': '메일 종류가 올바르지 않습니다.'}
        try:
            selected_by_mailbox = {}
            for candidate_id in selected_candidates:
                ref = decode_mail_candidate_id(candidate_id)
                selected_by_mailbox.setdefault(ref['mailbox'], []).append(ref)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}

    # 기본 비밀번호는 .env의 CARD_STATEMENT_PW 또는 파라미터로 전달된 값
    statement_pw = password_6 or os.getenv('CARD_STATEMENT_PW') or ''

    try:
        mail = imaplib.IMAP4_SSL('imap.naver.com', 993)
        mail.login(NAVER_EMAIL, NAVER_PASSWORD)
        list_status, mailbox_lines = mail.list()
        mailboxes = select_sync_mailboxes(mailbox_lines if list_status == 'OK' else [])
        discovered_names = {decode_imap_utf7(name) for name in mailboxes}
        missing_mailboxes = [name for name in SYNC_MAILBOX_NAMES if name not in discovered_names]
        if missing_mailboxes:
            mail.logout()
            return {
                'ok': False,
                'error': '필수 메일함을 찾을 수 없습니다.',
                'missing_mailboxes': missing_mailboxes,
            }

        parsed_items = []
        seen_message_ids = set()
        scanned_mailboxes = []
        failed_mailboxes = []
        parse_failure_count = 0
        card_keywords = ['카드', '승인', '결제', '명세서', '청구서', '이용대금', '체크카드', '신용카드', '영수증', '하나카드', '국민카드', '신한카드', '삼성카드', '현대카드', '롯데카드', '우리카드', 'BC카드', '비씨카드', '토스', '카카오뱅크']

        def append_transactions(txs, message_id, mailbox, mail_id, subject, sender, source_context):
            for index, tx in enumerate(txs):
                row_number = tx.pop('statement_row', index)
                source_kind = f"{tx.get('source') or 'statement'}:{source_context}"
                tx['email_id'] = mail_id.decode(errors='replace')
                tx['message_id'] = message_id
                tx['mailbox'] = decode_imap_utf7(mailbox)
                tx['subject'] = subject
                tx['sender'] = sender
                tx['source_key'] = build_source_key(message_id, source_kind, row_number)
                if selected_by_mailbox is not None:
                    tx['candidate_id'] = encode_mail_candidate_id(
                        mailbox, m_id.decode('ascii'), target['fingerprint']
                    )
                parsed_items.append(tx)

        for mailbox in mailboxes:
            if selected_by_mailbox is not None and mailbox not in selected_by_mailbox:
                continue
            select_status, _ = mail.select(f'"{mailbox}"', readonly=not mark_seen)
            if select_status != 'OK':
                failed_mailboxes.append(decode_imap_utf7(mailbox))
                continue
            scanned_mailboxes.append(decode_imap_utf7(mailbox))

            if selected_by_mailbox is None:
                typ, data = mail.search(None, 'ALL')
                if typ != 'OK' or not data or not data[0]:
                    continue
                targets = [{'id': value, 'uid': False, 'fingerprint': None}
                           for value in reversed(data[0].split()[-max_emails:])]
            else:
                targets = [{'id': ref['uid'].encode('ascii'), 'uid': True,
                            'fingerprint': ref['fingerprint']}
                           for ref in selected_by_mailbox[mailbox]]

            for target in targets:
                m_id = target['id']
                if target['uid']:
                    res, hdr_data = mail.uid(
                        'fetch', m_id, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])'
                    )
                else:
                    res, hdr_data = mail.fetch(m_id, '(RFC822.HEADER)')
                if res != 'OK' or not hdr_data:
                    continue

                subject = ""
                from_sender = ""
                for hp in hdr_data:
                    if isinstance(hp, tuple):
                        msg_hdr = email.message_from_bytes(hp[1])
                        subject = decode_mime_words(msg_hdr.get('Subject'))
                        from_sender = decode_mime_words(msg_hdr.get('From'))

                if target['uid']:
                    classification = classify_mail_candidate(subject, from_sender)
                    if (_candidate_fingerprint(msg_hdr) != target['fingerprint']
                            or not classification
                            or classification['group'] != sync_group
                            or classification['type'] == '쿠팡 주문'):
                        continue

                if not any(k in subject for k in card_keywords) and not any(k in from_sender for k in card_keywords):
                    continue

                if target['uid']:
                    res, msg_data = mail.uid('fetch', m_id, '(BODY.PEEK[])')
                else:
                    res, msg_data = mail.fetch(m_id, '(RFC822)')
                if res != 'OK':
                    continue

                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    raw_message = response_part[1]
                    msg = email.message_from_bytes(raw_message)
                    message_id = build_message_identity(raw_message)
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)

                    body_text, html_attachments = extract_email_body_and_attachments(msg)
                    has_statement = False
                    statement_detected = False

                    for att in html_attachments:
                        fname = att['filename']
                        content = att['content']
                        raw_b = att.get('raw_bytes')
                        html_input = raw_b.decode('utf-8', errors='ignore') if raw_b else content
                        txs = []

                        if 'hanacard' in fname.lower() or '하나카드' in content or '하나카드 이용대금명세서' in content:
                            statement_detected = True
                            txs = parse_hana_statement_html(content)
                        elif 'lottecard' in fname.lower() or 'lotte' in fname.lower() or '롯데카드' in subject:
                            statement_detected = True
                            decrypted_html = decrypt_unisafe_html(html_input, statement_pw)
                            if decrypted_html:
                                txs = parse_lotte_statement_html(decrypted_html)
                        elif 'samsungcard_detail' in fname.lower() or ('삼성카드' in subject and '명세서' in subject):
                            statement_detected = True
                            decrypted_html = decrypt_vestmail_html(html_input, statement_pw)
                            if decrypted_html:
                                try:
                                    samsung_data = fetch_samsung_statement_data(decrypted_html)
                                except (requests.RequestException, ValueError):
                                    samsung_data = None
                                txs = parse_samsung_statement_data(samsung_data or {})
                        elif 'hyundaicard' in fname.lower() or 'hyundai' in fname.lower() or '현대카드' in subject or '현대카드' in from_sender:
                            statement_detected = True
                            decrypted_html = decrypt_vestmail_html(html_input, statement_pw)
                            if decrypted_html:
                                txs = parse_hyundai_statement_html(decrypted_html)

                        if txs:
                            has_statement = True
                            append_transactions(
                                txs, message_id, mailbox, m_id, subject, from_sender,
                                f'attachment:{fname.lower()}',
                            )

                    if statement_detected and not has_statement:
                        parse_failure_count += 1

                    if not statement_detected and body_text:
                        parsed = parse_card_email_text(subject, from_sender, body_text)
                        if parsed.get('amount'):
                            append_transactions(
                                [parsed], message_id, mailbox, m_id, subject, from_sender, 'body'
                            )
                            if mark_seen:
                                mail.store(m_id, '+FLAGS', '\\Seen')

        mail.logout()
        if failed_mailboxes:
            return {
                'ok': False,
                'error': '일부 필수 메일함을 열 수 없습니다.',
                'failed_mailboxes': failed_mailboxes,
            }
        return {
            'ok': True,
            'count': len(parsed_items),
            'items': parsed_items,
            'mailboxes': scanned_mailboxes,
            'parse_failure_count': parse_failure_count,
        }

    except Exception:
        return {'ok': False, 'error': '메일 연동 중 서버 오류가 발생했습니다.'}

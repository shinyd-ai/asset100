import os
import imaplib
import email
import re
from datetime import datetime
from email.header import decode_header
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from statement_parser import decrypt_unisafe_html, decrypt_vestmail_html, parse_hana_statement_html, parse_lotte_statement_html, parse_hyundai_statement_html

load_dotenv()

NAVER_EMAIL = os.getenv('NAVER_EMAIL')
NAVER_PASSWORD = os.getenv('NAVER_PASSWORD')

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
            html_attachments.append({'filename': fname, 'content': raw_str})
        elif 'attachment' in cdisp:
            if fname and (fname.lower().endswith('.html') or fname.lower().endswith('.htm')):
                html_attachments.append({'filename': fname, 'content': raw_str})
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
    for comp in ['하나카드', 'KB국민카드', '국민카드', '신한카드', '삼성카드', '현대카드', '롯데카드', '우리카드', 'BC카드', '비씨카드', 'NH농협카드', '농협카드', '카카오뱅크', '토스뱅크']:
        if comp in subject or comp in from_sender or comp in body_text:
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

    is_cancel = ('취소' in subject or '취소' in body_text) and amount is not None
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

def fetch_and_sync_card_emails(max_emails=50, mark_seen=False, password_6=None):
    """
    네이버 IMAP에 접속하여 실시간 승인 메일 및 월별 이용명세서 첨부파일을 읽어 파싱
    """
    if not NAVER_EMAIL or not NAVER_PASSWORD:
        return {'ok': False, 'error': '.env에 NAVER_EMAIL 또는 NAVER_PASSWORD가 설정되지 않았습니다.'}

    # 기본 비밀번호는 .env의 CARD_STATEMENT_PW 또는 파라미터로 전달된 값
    statement_pw = password_6 or os.getenv('CARD_STATEMENT_PW') or ''

    try:
        mail = imaplib.IMAP4_SSL('imap.naver.com', 993)
        mail.login(NAVER_EMAIL, NAVER_PASSWORD)
        mail.select('INBOX')
        
        typ, data = mail.search(None, 'ALL')
        if typ != 'OK' or not data[0]:
            mail.logout()
            return {'ok': True, 'count': 0, 'items': [], 'message': '메일이 없습니다.'}
            
        mail_ids = data[0].split()
        target_ids = list(reversed(mail_ids[-max_emails:]))
        
        parsed_items = []
        card_keywords = ['카드', '승인', '결제', '명세서', '청구서', '이용대금', '체크카드', '신용카드', '영수증', '하나카드', '국민카드', '신한카드', '삼성카드', '현대카드', '롯데카드', '우리카드', 'BC카드', '비씨카드', '토스', '카카오뱅크']
        
        for m_id in target_ids:
            # 1. 헤더 먼저 가져와서 카드 관련 메일인지 빠르게 필터링 (네트워크 대역폭 & 타임아웃 방지)
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
                    
            if not any(k in subject for k in card_keywords) and not any(k in from_sender for k in card_keywords):
                continue
                
            # 2. 카드 관련 메일인 경우에만 본문/첨부파일 다운로드
            res, msg_data = mail.fetch(m_id, '(RFC822)')
            if res != 'OK':
                continue
                
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    body_text, html_attachments = extract_email_body_and_attachments(msg)
                    
                    # 1) 명세서 첨부파일이 있는 경우 (월별 명세서)
                    has_statement = False
                    for att in html_attachments:
                        fname = att['filename']
                        content = att['content']
                        
                        # 하나카드 명세서
                        if 'hanacard' in fname.lower() or '하나카드' in content or '하나카드 이용대금명세서' in content:
                            txs = parse_hana_statement_html(content)
                            if txs:
                                has_statement = True
                                for tx in txs:
                                    tx['email_id'] = m_id.decode()
                                    tx['subject'] = subject
                                    tx['sender'] = from_sender
                                    parsed_items.append(tx)
                                    
                        # 롯데카드 보안 명세서
                        elif 'lottecard' in fname.lower() or 'lotte' in fname.lower() or '롯데카드' in subject:
                            decrypted_html = decrypt_unisafe_html(content, statement_pw)
                            if decrypted_html:
                                txs = parse_lotte_statement_html(decrypted_html)
                                if txs:
                                    has_statement = True
                                    for tx in txs:
                                        tx['email_id'] = m_id.decode()
                                        tx['subject'] = subject
                                        tx['sender'] = from_sender
                                        parsed_items.append(tx)

                        # 현대카드 보안 명세서 (VestMail)
                        elif 'hyundaicard' in fname.lower() or 'hyundai' in fname.lower() or '현대카드' in subject or '현대카드' in from_sender:
                            decrypted_html = decrypt_vestmail_html(content, statement_pw)
                            if decrypted_html:
                                txs = parse_hyundai_statement_html(decrypted_html)
                                if txs:
                                    has_statement = True
                                    for tx in txs:
                                        tx['email_id'] = m_id.decode()
                                        tx['subject'] = subject
                                        tx['sender'] = from_sender
                                        parsed_items.append(tx)

                    # 2) 첨부파일 명세서가 아닌 경우 실시간 결제 승인 본문 파싱
                    if not has_statement and body_text:
                        parsed = parse_card_email_text(subject, from_sender, body_text)
                        if parsed.get('amount'):
                            parsed['email_id'] = m_id.decode()
                            parsed['subject'] = subject
                            parsed['sender'] = from_sender
                            parsed_items.append(parsed)
                            
                            if mark_seen:
                                mail.store(m_id, '+FLAGS', '\\Seen')

        mail.logout()
        return {'ok': True, 'count': len(parsed_items), 'items': parsed_items}
        
    except Exception as e:
        return {'ok': False, 'error': f"메일 연동 실패: {str(e)}"}

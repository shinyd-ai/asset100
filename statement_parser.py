import os
import re
import base64
import hashlib
import subprocess
import tempfile
from bs4 import BeautifulSoup
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Hash import MD5

def decrypt_vestmail_html(html_str, password_6):
    """
    현대카드 VestMail(YettieSoft) 보안 메일 HTML 복호화
    Node.js 엔진을 호출하여 VestMail 자체 JS 암호화 모듈 실행
    """
    if not password_6 or not html_str:
        return None

    if 'var s= new Array()' not in html_str and 's = new Array()' not in html_str and 's[0]' not in html_str:
        return None

    s_idx = html_str.find('var s= new Array();')
    if s_idx == -1:
        s_idx = html_str.find('var s = new Array();')
    if s_idx == -1:
        s_idx = html_str.find('s[0]')
    if s_idx == -1:
        return None

    s_end = html_str.find('</SCRIPT>', s_idx)
    if s_end == -1:
        s_end = html_str.find('</script>', s_idx)
    if s_end == -1:
        return None
    s_script = html_str[s_idx:s_end]

    core_idx = html_str.find('"undefined"!=typeof x')
    if core_idx == -1:
        return None
    core_end = html_str.find('</SCRIPT>', core_idx)
    if core_end == -1:
        core_end = html_str.find('</script>', core_idx)
    if core_end == -1:
        return None
    core_script = html_str[core_idx:core_end]
    core_script = core_script.replace('k=window.x={}', 'k=window.x=x={}')

    bp_idx = html_str.find("var b_p = '")
    if bp_idx == -1:
        return None
    bp_end = html_str.find("eval(atob(b_p));", bp_idx)
    if bp_end == -1:
        return None
    bp_script = html_str[bp_idx:bp_end + len("eval(atob(b_p));")]

    with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as out_f:
        out_path = out_f.name.replace('\\', '/')

    mock_env = f"""
var fs = require('fs');
var window = {{
    location: {{ href: "" }},
    vestmail_browser_info: {{ browser: "Chrome", version: 100, mobile: false }}
}};
var navigator = {{ platform: "Win32", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" }};
global.window = window;
global.navigator = navigator;
var document = {{
    getElementById: function(id) {{
        return {{
            value: {repr(str(password_6).strip())},
            classList: {{ contains: function() {{ return false; }} }},
            className: "",
            getAttribute: function() {{ return ""; }},
            parentNode: {{ removeChild: function() {{}} }},
            style: {{}},
            innerHTML: ""
        }};
    }},
    createElement: function() {{ return {{ style: {{}}, appendChild: function() {{}} }}; }},
    body: {{ appendChild: function() {{}}, removeChild: function() {{}} }},
    write: function(content) {{
        fs.writeFileSync({repr(out_path)}, content, 'utf-8');
    }},
    close: function() {{}}
}};
global.document = document;
var org = {{ style: {{}} }};
global.org = org;
function alert(msg) {{}}
"""

    runner_js = "\n\n".join([
        mock_env,
        "var x = {};\nwindow.x = x;\n",
        s_script,
        core_script,
        bp_script,
        "if (typeof window.doAction === 'function') { window.doAction(); }"
    ])

    with tempfile.NamedTemporaryFile(suffix='.js', mode='w', encoding='utf-8', delete=False) as js_f:
        js_f.write(runner_js)
        js_path = js_f.name

    try:
        proc = subprocess.run(['node', js_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            with open(out_path, 'r', encoding='utf-8', errors='ignore') as f:
                decrypted_html = f.read()
            return decrypted_html
    except Exception as e:
        print("VestMail decryption error:", e)
    finally:
        if os.path.exists(js_path):
            try: os.remove(js_path)
            except: pass
        if os.path.exists(out_path):
            try: os.remove(out_path)
            except: pass

    return None

def parse_hyundai_statement_html(html_str, default_year=None):
    """현대카드 복호화된 명세서 HTML 테이블 파싱"""
    soup = BeautifulSoup(html_str, 'html.parser')
    rows = soup.find_all('tr')
    
    current_year = default_year or datetime.today().year
    year_match = re.search(r'(\d{4})년\s*\d{1,2}월', html_str)
    if year_match:
        current_year = int(year_match.group(1))

    transactions = []

    for tr in rows:
        # width="10" 또는 공백 spacer td 제외
        tds = [td for td in tr.find_all('td') if td.get_text().strip() and td.get_text().strip() != '\xa0']
        if len(tds) >= 4:
            td_texts = [td.get_text().strip().replace('\xa0', '') for td in tds]
            # Pattern: 06.01 (월.일)
            date_match = re.search(r'^(\d{2})[./-](\d{2})$', td_texts[0])
            if date_match:
                month, day = date_match.groups()
                tx_date = f"{current_year:04d}-{int(month):02d}-{int(day):02d}"
                card_name = td_texts[1]
                store_name = td_texts[2]
                amt_str = td_texts[3].replace(',', '')
                if amt_str.replace('-', '').isdigit() and len(amt_str) >= 1:
                    amount = int(amt_str)
                    transactions.append({
                        'card_company': '현대카드',
                        'card_name': card_name,
                        'date': tx_date,
                        'name': store_name,
                        'amount': amount,
                        'installment': 1,
                        'source': '이용명세서'
                    })

    return transactions

def evp_bytestokey(password, salt, key_len=16, iv_len=16):
    """OpenSSL EvpBytesToKey (MD5) key derivation used by UniSafe/CryptoJS"""
    dtot = b''
    d = b''
    while len(dtot) < (key_len + iv_len):
        d = MD5.new(d + password + salt).digest()
        dtot += d
    return dtot[:key_len], dtot[key_len:key_len + iv_len]

def decrypt_unisafe_html(html_str, password_6):
    """롯데카드 및 국내 카드사 UniSafe 보안 메일 HTML 복호화"""
    if not password_6:
        return None
        
    m = re.search(r"USSM_M_DATA\s*=\s*'([^']+)'", html_str)
    if not m:
        m = re.search(r'USSM_M_DATA\s*=\s*"([^"]+)"', html_str)
    if not m:
        return None

    try:
        encrypted_b64 = m.group(1).strip()
        raw_data = base64.b64decode(encrypted_b64)

        if raw_data.startswith(b'Salted__'):
            salt = raw_data[8:16]
            ciphertext = raw_data[16:]
        else:
            salt = b''
            ciphertext = raw_data

        password_bytes = str(password_6).strip().encode('utf-8')
        
        # 1) UniSafe CryptoJS 표준 방식 (MD5(pw) as Key and IV)
        key_md5 = hashlib.md5(password_bytes).digest()
        iv_md5 = hashlib.md5(password_bytes).digest()
        try:
            cipher = AES.new(key_md5, AES.MODE_CBC, iv_md5)
            dec = cipher.decrypt(ciphertext)
            pad_len = dec[-1]
            if 0 < pad_len <= 16:
                dec = dec[:-pad_len]
            if b'<!--LIC_TO UNISECURE-->' in dec:
                lic_idx = dec.find(b'<!--LIC_TO UNISECURE-->')
                clean_bytes = dec[lic_idx + len(b'<!--LIC_TO UNISECURE-->'):]
                # Check for charset declaration inside HTML
                if b'charset=euc-kr' in clean_bytes.lower() or b'charset="euc-kr"' in clean_bytes.lower():
                    enc_list = ['euc-kr', 'cp949', 'utf-8']
                else:
                    enc_list = ['euc-kr', 'cp949', 'utf-8']
                for enc in enc_list:
                    try:
                        return clean_bytes.decode(enc)
                    except Exception:
                        continue
                return clean_bytes.decode('utf-8', errors='ignore')
        except Exception:
            pass

        # 2) OpenSSL EVP KDF 방식
        key, iv = evp_bytestokey(password_bytes, salt, 16, 16)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(ciphertext)
        pad_len = decrypted[-1]
        if 0 < pad_len <= 16:
            decrypted = decrypted[:-pad_len]

        if b'<!--LIC_TO UNISECURE-->' in decrypted:
            lic_idx = decrypted.find(b'<!--LIC_TO UNISECURE-->')
            clean_bytes = decrypted[lic_idx + len(b'<!--LIC_TO UNISECURE-->'):]
            for enc in ['euc-kr', 'cp949', 'utf-8']:
                try:
                    return clean_bytes.decode(enc)
                except Exception:
                    continue
            return clean_bytes.decode('utf-8', errors='ignore')
        return None
    except Exception as e:
        print(f"Decryption error: {e}")
        return None

def parse_hana_statement_html(html_str, default_year=None):
    """하나카드 월별 명세서 HTML 테이블 파싱"""
    soup = BeautifulSoup(html_str, 'html.parser')
    rows = soup.find_all('tr')
    
    current_year = default_year or datetime.today().year
    # 년도 탐색
    year_match = re.search(r'(\d{4})년\s*\d{1,2}월', html_str)
    if year_match:
        current_year = int(year_match.group(1))

    current_card_name = "하나카드"
    transactions = []
    
    for tr in rows:
        text = tr.get_text()
        if '(본인신용카드)' in text or '(가족신용카드)' in text or '(본인체크카드)' in text:
            m = re.search(r'고객님_?\s*([^_]+)_?', text)
            if m:
                current_card_name = m.group(1).strip()
            continue
            
        tds = tr.find_all('td')
        if len(tds) >= 3:
            td_texts = [td.get_text().strip().replace('\xa0', '') for td in tds]
            # Pattern: 07/02 (월/일), 가맹점명, 금액
            date_match = re.match(r'^(\d{2})/(\d{2})$', td_texts[0])
            if date_match:
                month, day = date_match.groups()
                tx_date = f"{current_year:04d}-{int(month):02d}-{int(day):02d}"
                store_name = td_texts[1]
                amt_str = td_texts[2].replace(',', '')
                if amt_str.replace('-', '').isdigit():
                    amount = int(amt_str)
                    installment = 1
                    if len(td_texts) >= 4 and td_texts[3].isdigit():
                        installment = int(td_texts[3])
                        
                    transactions.append({
                        'card_company': '하나카드',
                        'card_name': current_card_name,
                        'date': tx_date,
                        'name': store_name,
                        'amount': amount,
                        'installment': installment,
                        'source': '이용명세서'
                    })
                    
    return transactions

def parse_lotte_statement_html(html_str, default_year=None):
    """롯데카드 복호화된 명세서 HTML 테이블 파싱"""
    soup = BeautifulSoup(html_str, 'html.parser')
    rows = soup.find_all('tr')
    
    current_year = default_year or datetime.today().year
    year_match = re.search(r'(\d{4})년\s*\d{1,2}월', html_str)
    if year_match:
        current_year = int(year_match.group(1))

    transactions = []
    current_card_name = "롯데카드"

    for tr in rows:
        # 1. em.useday 가 있는 경우 (롯데카드 이용대금 명세서 표준 포맷)
        em_useday = tr.find('em', class_='useday')
        if em_useday:
            day_text = em_useday.get_text().strip()
            date_m = re.search(r'(\d{1,2})\.(\d{1,2})', day_text)
            if date_m:
                month, day = date_m.groups()
                tx_date = f"{current_year:04d}-{int(month):02d}-{int(day):02d}"
                
                # 가맹점명: em.useday 부모 td에서 em 텍스트 제거
                td_first = tr.find('td', class_='first') or tr.find('td')
                if td_first:
                    td_clone = BeautifulSoup(str(td_first), 'html.parser')
                    for em in td_clone.find_all('em'):
                        em.decompose()
                    store_name = td_clone.get_text().strip()
                else:
                    store_name = "롯데카드 가맹점"

                # 금액: headers에 card_field4가 있거나 숫자인 td 탐색
                amount = 0
                for td in tr.find_all('td'):
                    t_str = td.get_text().strip().replace(',', '')
                    if re.match(r'^-?[0-9]+$', t_str) and len(t_str) >= 3:
                        amount = int(t_str)
                        break

                if amount > 0 and store_name:
                    transactions.append({
                        'card_company': '롯데카드',
                        'card_name': current_card_name,
                        'date': tx_date,
                        'name': store_name,
                        'amount': amount,
                        'installment': 1,
                        'source': '이용명세서'
                    })
                continue

        # 2. 일반 테이블 포맷 fallback
        tds = tr.find_all('td')
        if len(tds) >= 3:
            td_texts = [td.get_text().strip().replace('\xa0', '') for td in tds]
            for idx, text in enumerate(td_texts[:2]):
                date_match = re.search(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})', text) or re.search(r'^(\d{1,2})[-./](\d{1,2})$', text)
                if date_match:
                    groups = date_match.groups()
                    if len(groups) == 3:
                        tx_date = f"{int(groups[0]):04d}-{int(groups[1]):02d}-{int(groups[2]):02d}"
                    else:
                        tx_date = f"{current_year:04d}-{int(groups[0]):02d}-{int(groups[1]):02d}"
                        
                    store_candidates = [t for t in td_texts if len(t) > 1 and not re.match(r'^[0-9,.-]+$', t)]
                    amt_candidates = [t.replace(',', '') for t in td_texts if re.match(r'^-?[0-9,]+$', t) and len(t.replace(',', '')) >= 3]
                    
                    if store_candidates and amt_candidates:
                        store_name = store_candidates[0]
                        amount = int(amt_candidates[0])
                        transactions.append({
                            'card_company': '롯데카드',
                            'card_name': current_card_name,
                            'date': tx_date,
                            'name': store_name,
                            'amount': amount,
                            'installment': 1,
                            'source': '이용명세서'
                        })
                    break

    return transactions

from flask import Flask, request, jsonify, render_template, send_file
from bs4 import BeautifulSoup
import requests, re, json, io, time, random, os, socket, smtplib, ssl
from urllib.parse import urlparse, urljoin
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Set, Dict
from openpyxl import Workbook

# Optional extras
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL = True
except Exception:
    HAS_CURL = False

# Optional scheduler (only active if env ENABLE_SCHEDULER=1)
ENABLE_SCHED = os.getenv("ENABLE_SCHEDULER", "0") == "1"
if ENABLE_SCHED:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:
        ENABLE_SCHED = False

app = Flask(__name__)

# -------- Helpers --------
MONEY_RE = re.compile(r'([\$£€]|CA\$)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+(?:\.[0-9]{2})?)')
CURRENCY_SYMBOLS = {'USD': '$', 'CAD': 'CA$', 'usd': '$', 'cad': 'CA$'}

def clean_text(s: Optional[str]) -> str:
    if not s: return ""
    return re.sub(r'\s+', ' ', s).strip()

def host_currency(host: str) -> str:
    host = (host or '').lower()
    if host.endswith('.ca') or host.startswith('ca.') or '.ca.' in host: return 'CAD'
    return 'USD'

def parse_price_number(text: str) -> Optional[float]:
    if not text: return None
    m = MONEY_RE.search(text)
    if not m: return None
    num = m.group(2).replace(',', '')
    try:
        return float(num)
    except Exception:
        return None

def fmt_price(val: Optional[float], currency: Optional[str], host: str) -> str:
    if val is None: return ""
    sym = CURRENCY_SYMBOLS.get((currency or '').upper()) or CURRENCY_SYMBOLS.get(host_currency(host)) or '$'
    return f"{sym}{val:,.2f}"

def apply_rules(price: Optional[float], percent_off: float, absolute_off: float):
    if price is None: return None
    p = float(price)
    if percent_off and percent_off > 0:
        p *= (1 - percent_off/100.0)
    if absolute_off and absolute_off > 0:
        p -= absolute_off
    return round(p + 1e-9, 2)

# -------- Data --------
@dataclass
class Item:
    url: str
    site: str
    title: str
    price_value: Optional[float]
    price_currency: Optional[str]
    price_text: str
    discounted_value: Optional[float]
    discounted_formatted: str
    original_formatted: str
    source: str
    image_url: str

# -------- HTTP --------
def build_session(retries: int = 3, verify_ssl: bool = True, use_curl: bool = False):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if use_curl and HAS_CURL:
        s = curl_requests.Session(impersonate="chrome120")
        s.headers.update(headers)
        s.verify = verify_ssl
        s.timeout = 30
        return s, True
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = requests.Session()
    s.headers.update(headers)
    retry = Retry(
        total=max(0, int(retries)),
        read=max(0, int(retries)),
        connect=max(0, int(retries)),
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(['GET','HEAD','OPTIONS'])
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    s.verify = verify_ssl
    return s, False

def get_html(sess, url: str, timeout: int = 30) -> Tuple[str, str]:
    r = sess.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return (str(r.url), r.text)

# -------- Extractors --------
def find_jsonld_products(soup: BeautifulSoup) -> List[dict]:
    out = []
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(tag.string or tag.get_text() or '')
        except Exception:
            continue
        if isinstance(data, dict):
            candidates = [data]
        elif isinstance(data, list):
            candidates = data
        else:
            continue
        for obj in candidates:
            if isinstance(obj, dict) and (obj.get('@type') == 'Product'):
                out.append(obj)
            if isinstance(obj, dict) and isinstance(obj.get('@graph'), list):
                for g in obj['@graph']:
                    if isinstance(g, dict) and g.get('@type') == 'Product':
                        out.append(g)
    return out

def price_from_offers(offers) -> Tuple[Optional[float], Optional[str]]:
    if isinstance(offers, dict):
        price = offers.get('price')
        currency = offers.get('priceCurrency')
        try:
            return float(price), currency
        except Exception:
            return parse_price_number(str(price)), currency
    if isinstance(offers, list):
        for off in offers:
            v, c = price_from_offers(off)
            if v is not None:
                return v, c
    return None, None

def extract_title(soup: BeautifulSoup) -> str:
    for sel in ['h1.page-title .base', 'span[data-ui-id="page-title-wrapper"]', 'h1.product', 'h1']:
        el = soup.select_one(sel)
        if el:
            t = clean_text(el.get_text())
            if t: return t
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get('content'): return clean_text(og['content'])
    return ""

def extract_canonical_or_og_url(soup: BeautifulSoup, fallback: str) -> str:
    can = soup.select_one('link[rel="canonical"]')
    if can and can.get('href'): return can['href']
    og = soup.select_one('meta[property="og:url"]')
    if og and og.get('content'): return og['content']
    return fallback

def extract_price(soup: BeautifulSoup) -> Tuple[Optional[float], str, str]:
    el = soup.select_one('[data-price-amount]')
    if el and el.get('data-price-amount'):
        try:
            v = float(el['data-price-amount'])
            return v, '', 'data-price-amount'
        except Exception:
            pass
    for sel in [
        'span.price-final_price [data-price-amount]',
        'span.price-final_price span.price',
        'div.price-box [data-price-amount]',
        'div.price-box span.price',
        'span[id^="product-price-"] [data-price-amount]',
        'span[id^="product-price-"] span.price',
        'span.price',
        '[class*="price"]', '[id*="price"]'
    ]:
        for e in soup.select(sel):
            txt = clean_text(e.get_text())
            v = parse_price_number(txt)
            if v is not None:
                return v, '', sel
    return None, '', ''

def extract_image_url(container: BeautifulSoup) -> str:
    for sel in ['img[data-src]', 'img[srcset]', 'img[src]']:
        el = container.select_one(sel)
        if not el: continue
        return el.get('data-src') or el.get('src') or ''
    return ''

def is_product_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one('h1.page-title, h1.product')) or bool(find_jsonld_products(soup))

def is_category_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one('ul.product-listing li.item')) or \
           bool(soup.select_one('ol.products li.product-item')) or \
           bool(soup.select_one('div.product-item-info, div.product-card, li.product'))

def find_next_page_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    cand = soup.select_one('li.pages-item-next a, a.action.next, a[rel="next"]')
    if cand and cand.get('href'):
        return urljoin(base_url, cand['href'])
    return None

# -------- Scrapers --------
def scrape_product(sess, final_url: str, html: str, rules: Dict) -> List[Item]:
    host = urlparse(final_url).hostname or ''
    soup = BeautifulSoup(html, 'html.parser')
    final_url = extract_canonical_or_og_url(soup, final_url)

    title = ""
    price_val = None
    currency = None
    source = "product"
    jl = find_jsonld_products(soup)
    if jl:
        obj = jl[0]
        title = clean_text(obj.get('name') or '')
        pv, cur = price_from_offers(obj.get('offers'))
        if pv is not None:
            price_val, currency, source = pv, cur, "jsonld"

    if not title:
        title = extract_title(soup)
    if price_val is None:
        pv, cur, src = extract_price(soup)
        price_val, currency = pv, cur or currency
        if pv is not None: source = src

    img = ''
    gal = soup.select_one('.gallery-placeholder, .product.media, .fotorama, .product-image')
    img = extract_image_url(gal or soup)

    percent_off = float(rules.get('percent_off') or 0.0)
    absolute_off = float(rules.get('absolute_off') or 0.0)
    final_price = apply_rules(price_val, percent_off, absolute_off)

    return [Item(
        url=final_url, site=host, title=title or '',
        price_value=price_val, price_currency=currency or host_currency(host),
        price_text='' if price_val is not None else 'price_not_found_or_hidden',
        discounted_value=final_price,
        discounted_formatted=fmt_price(final_price, currency, host) if final_price is not None else '',
        original_formatted=fmt_price(price_val, currency, host),
        source=source, image_url=img
    )]

def scrape_category_page(sess, final_url: str, html: str, rules: Dict) -> List[Item]:
    host = urlparse(final_url).hostname or ''
    soup = BeautifulSoup(html, 'html.parser')
    out: List[Item] = []

    cards = soup.select('ul.product-listing li.item')
    if not cards:
        cards = soup.select('ol.products li.product-item, div.product-item-info, div.product-card, li.product')

    percent_off = float(rules.get('percent_off') or 0.0)
    absolute_off = float(rules.get('absolute_off') or 0.0)

    for card in cards:
        a = card.select_one('a[href]')
        if not a:
            continue
        title = clean_text(a.get_text())
        href = a.get('href') or ''
        prod_url = urljoin(final_url, href)
        image = extract_image_url(card)

        price_val = None
        price_text = ''
        pel = card.select_one('[data-price-amount]')
        if pel and pel.get('data-price-amount'):
            try:
                price_val = float(pel['data-price-amount'])
            except Exception:
                price_val = None
        if price_val is None:
            pt_el = card.select_one('.price, .price-final_price .price, [class*="price"]')
            price_text = clean_text(pt_el.get_text()) if pt_el else ''
            price_val = parse_price_number(price_text)

        final_price = apply_rules(price_val, percent_off, absolute_off)

        out.append(Item(
            url=prod_url, site=host, title=title or '',
            price_value=price_val, price_currency=host_currency(host),
            price_text=price_text if price_val is None else '',
            discounted_value=final_price,
            discounted_formatted=fmt_price(final_price, None, host) if final_price is not None else '',
            original_formatted=fmt_price(price_val, None, host),
            source='category-card', image_url=image
        ))

    return out

def get_html_safe(sess, url: str, delay_ms: int):
    if delay_ms:
        time.sleep(delay_ms / 1000.0 + random.random()*0.05)
    try:
        return get_html(sess, url)
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'

def scrape_category_all_pages(sess, start_url: str, rules: Dict, max_pages: int = 20, delay_ms: int = 400):
    items: List[Item] = []
    seen: Set[str] = set()
    url = start_url
    pages = 0
    while url and pages < max_pages:
        pages += 1
        pair = get_html_safe(sess, url, delay_ms)
        if pair[0] is None:
            # record error row
            items.append(Item(url=url, site=urlparse(url).hostname or '', title='',
                              price_value=None, price_currency=None, price_text=f'fetch_failed: {pair[1]}',
                              discounted_value=None, discounted_formatted='', original_formatted='',
                              source='error', image_url=''))
            break
        final_url, html = pair
        soup = BeautifulSoup(html, 'html.parser')
        items.extend(scrape_category_page(sess, final_url, html, rules))
        seen.add(final_url)
        nxt = find_next_page_url(soup, final_url)
        if not nxt or nxt in seen:
            break
        url = nxt
    return items

def scrape_url(sess, url: str, rules: Dict, crawl_pagination: bool, max_pages: int, delay_ms: int) -> List[Item]:
    pair = get_html_safe(sess, url, delay_ms=0)
    if pair[0] is None:
        return [Item(url=url, site=urlparse(url).hostname or '', title='',
                     price_value=None, price_currency=None, price_text=f'fetch_failed: {pair[1]}',
                     discounted_value=None, discounted_formatted='', original_formatted='',
                     source='error', image_url='')]
    final_url, html = pair
    soup = BeautifulSoup(html, 'html.parser')
    if is_product_page(soup):
        return scrape_product(sess, final_url, html, rules)
    if is_category_page(soup):
        if crawl_pagination:
            return scrape_category_all_pages(sess, final_url, rules, max_pages=max_pages, delay_ms=delay_ms)
        return scrape_category_page(sess, final_url, html, rules)
    return scrape_product(sess, final_url, html, rules)

# -------- Routes --------
@app.get('/')
def index():
    return render_template('index.html')

@app.post('/api/scrape')
def api_scrape():
    data = request.get_json(silent=True) or {}
    urls_raw = data.get('urls') or ''
    crawl_pagination = bool(data.get('crawl_pagination', True))
    max_pages = int(data.get('max_pages') or 20)
    max_pages = 1 if max_pages < 1 else 100 if max_pages > 100 else max_pages

    delay_ms = int(data.get('delay_ms') or 400)
    retries = int(data.get('retries') or 3)
    verify_ssl = bool(data.get('verify_ssl', True))
    use_curl = bool(data.get('use_curl', False))

    rules = {
        "percent_off": float(data.get('percent_off') or 0.0),
        "absolute_off": float(data.get('absolute_off') or 0.0),
    }

    urls = [u.strip() for u in (urls_raw.splitlines() if isinstance(urls_raw, str) else urls_raw) if u.strip()]
    seen_u = set(); urls = [u for u in urls if not (u in seen_u or seen_u.add(u))]

    sess, using_curl = build_session(retries=retries, verify_ssl=verify_ssl, use_curl=use_curl)
    items: List[Item] = []
    for u in urls:
        items.extend(scrape_url(sess, u, rules, crawl_pagination, max_pages, delay_ms))

    return jsonify({
        "rules": rules,
        "count": len(items),
        "using_curl": using_curl,
        "has_curl": HAS_CURL,
        "items": [asdict(i) for i in items],
    })

@app.post('/api/export/xlsx')
def export_xlsx():
    data = request.get_json(silent=True) or {}
    rows = data.get('rows') or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Extract"
    headers = []
    # dynamic headers from keys (preserve a friendly order if present)
    preferred = ["image_url","title","original","percent_off","absolute_off","final","url","source",
                 "clean_title","model","compare_price","delta","delta_pct","cost","fees_pct","target_margin_pct","margin_pct","profit","recommended_price","watchlisted"]
    if rows:
        keys = list(rows[0].keys())
        for k in preferred:
            if k in keys and k not in headers: headers.append(k)
        for k in keys:
            if k not in headers: headers.append(k)
    else:
        headers = ["image_url","title","original","percent_off","absolute_off","final","url","source"]
    ws.append([k for k in headers])
    for r in rows:
        ws.append([r.get(k, "") for k in headers])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="mobilesentrix_prices.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# -------- Optional: scheduler email (env-driven) --------
def try_send_email(subject: str, body: str, attachments: List[Tuple[str, bytes]] = None):
    host = os.getenv("SMTP_HOST"); user = os.getenv("SMTP_USER"); pwd = os.getenv("SMTP_PASS")
    port = int(os.getenv("SMTP_PORT", "587")); mail_from = os.getenv("SMTP_FROM"); mail_to = os.getenv("REPORT_EMAIL_TO")
    if not (host and user and pwd and mail_from and mail_to):
        print("[scheduler] SMTP not configured; skipping email")
        return
    attachments = attachments or []
    import email, email.mime.multipart, email.mime.text, email.mime.base, email.encoders
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = mail_from; msg["To"] = mail_to; msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(body, "plain"))
    for fname, data in attachments:
        part = email.mime.base.MIMEBase("application", "octet-stream")
        part.set_payload(data)
        email.encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
        msg.attach(part)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as server:
        server.starttls(context=ctx)
        server.login(user, pwd)
        server.send_message(msg)
    print("[scheduler] email sent")

def scheduled_job():
    urls = os.getenv("REPORT_URLS", "").strip()
    if not urls:
        print("[scheduler] REPORT_URLS empty; skipping")
        return
    payload = {
        "urls": urls,
        "percent_off": float(os.getenv("REPORT_PERCENT_OFF", "0") or 0),
        "absolute_off": float(os.getenv("REPORT_ABS_OFF", "0") or 0),
        "crawl_pagination": True,
        "max_pages": int(os.getenv("REPORT_MAX_PAGES", "20") or 20),
    }
    sess, _ = build_session()
    items: List[Item] = []
    for u in [u.strip() for u in urls.splitlines() if u.strip()]:
        items.extend(scrape_url(sess, u, {"percent_off": payload["percent_off"], "absolute_off": payload["absolute_off"]}, True, payload["max_pages"], 400))
    # Make CSV and XLSX
    import csv
    from io import StringIO
    headers = ["image_url","title","original_formatted","discounted_formatted","url","site"]
    sio = StringIO()
    w = csv.writer(sio)
    w.writerow(headers)
    for i in items:
        w.writerow([i.image_url, i.title, i.original_formatted, i.discounted_formatted, i.url, i.site])
    csv_bytes = sio.getvalue().encode("utf-8")
    # XLSX
    wb = Workbook(); ws = wb.active; ws.title="Report"; ws.append(headers)
    for i in items: ws.append([i.image_url, i.title, i.original_formatted, i.discounted_formatted, i.url, i.site])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    try_send_email("MobileSentrix Auto Report", "Attached latest scrape.", [("report.csv", csv_bytes), ("report.xlsx", bio.read())])

if ENABLE_SCHED:
    cron = os.getenv("CRON", "0 8 * * *")
    try:
        from apscheduler.triggers.cron import CronTrigger
        scheduler = BackgroundScheduler(daemon=True)
        h, m, *_ = [0,0]
        # Use CronTrigger directly from CRON env string if available
        trigger = CronTrigger.from_crontab(cron)
        scheduler.add_job(scheduled_job, trigger)
        scheduler.start()
        print(f"[scheduler] enabled with CRON={cron}")
    except Exception as e:
        print(f"[scheduler] failed to start: {e}")

# -------- Main --------
def find_free_port(start=5000, end=5050):
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('0.0.0.0', p))
                return p
            except OSError:
                continue
    return 0

if __name__ == '__main__':
    port = int(os.getenv("PORT", "0")) or find_free_port()
    if not port:
        raise SystemExit("No free port in 5000–5050. Set PORT env var to a free port.")
    app.run(host='0.0.0.0', port=port, debug=True)
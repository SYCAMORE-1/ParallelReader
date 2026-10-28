"""Microsoft's public web translator, with temporary credentials kept in memory."""
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from reader.azure_translation import LANGUAGES, translate_sentences

VERSION = 'microsoft-web-html-slots-v1'
PAGE = 'https://www.bing.com/translator'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36 Edg/130'


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    return parsed.scheme == 'https' and (host == 'bing.com' or host.endswith('.bing.com')) and not parsed.username and not parsed.password and parsed.port in {None, 443}


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not allowed_url(new_url):
            raise RuntimeError('微软翻译返回了不支持的跳转地址')
        if request.method == 'POST' and code in {301, 302, 303, 307, 308}:
            return urllib.request.Request(new_url, data=request.data, headers=dict(request.headers), method='POST')
        return super().redirect_request(request, response, code, message, headers, new_url)


def parse_session(page, url, now):
    if not allowed_url(url):
        raise RuntimeError('微软翻译返回了不支持的页面地址')
    auth = re.search(r'params_AbusePreventionHelper\s*=\s*\[\s*(\d+)\s*,\s*"([^"\r\n]+)"\s*,\s*(\d+)\s*\]', page)
    ig = re.search(r'IG\s*:\s*"([A-Fa-f0-9]+)"', page)
    iid = re.search(r'data-iid\s*=\s*["\x27]([^"\x27]+)["\x27]', page)
    if not (auth and ig and iid):
        raise RuntimeError('微软翻译暂不可用或需要网页验证，请稍后重试，或切换大模型／Azure API')
    parsed = urllib.parse.urlsplit(url)
    return {'key': auth[1], 'token': auth[2], 'ig': ig[1], 'iid': html.unescape(iid[1]),
            'url': f'https://{parsed.hostname}/ttranslatev3',
            'expires': now + max(0, min(int(auth[3]) / 1000, 3600) - 60)}


def utf16_length(value):
    return len(value.encode('utf-16-le')) // 2


class TextRuns(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.runs = []; self.protected = False
        self.feed(value); self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'span': self.protected = 'notranslate' in dict(attrs).get('class', '').split()

    def handle_endtag(self, tag):
        if tag == 'span': self.protected = False

    def handle_data(self, value):
        self.runs.append((value, self.protected))


def markup(runs):
    groups = []
    for text, protected in runs:
        if groups and groups[-1][1] == protected:
            groups[-1] = (groups[-1][0] + text, protected)
        else: groups.append((text, protected))
    return '<p>' + ''.join(('<span class="notranslate">' + html.escape(text) + '</span>') if protected else html.escape(text) for text, protected in groups) + '</p>'


def chunks(value, limit=1000):
    """Keep words, HTML escaping, and notranslate spans valid within each request."""
    result, current = [], []
    for text, protected in TextRuns(value).runs:
        for token in re.findall(r'\S+\s*|\s+', text):
            if utf16_length(markup(current + [(token, protected)])) <= limit:
                current.append((token, protected)); continue
            if current:
                result.append(markup(current)); current = []
            # An unusually long word / identifier still needs bounded requests.
            for char in token:
                if utf16_length(markup(current + [(char, protected)])) > limit:
                    result.append(markup(current)); current = []
                current.append((char, protected))
    if current: result.append(markup(current))
    return result


class MicrosoftTranslator:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(3)
        self.session = None
        self.opener = urllib.request.build_opener(Redirects(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def fetch(self, request):
        try:
            with self.opener.open(request, timeout=20) as response:
                if not allowed_url(response.url):
                    raise RuntimeError('微软翻译返回了不支持的地址')
                body = response.read(3 * 1024 * 1024 + 1)
                if len(body) > 3 * 1024 * 1024: raise RuntimeError('微软翻译响应过大，请重试')
                return body.decode('utf-8'), response.url
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 429: raise RuntimeError('微软翻译请求过于频繁，请稍后重试') from exc
            raise RuntimeError(f'微软翻译暂不可用（HTTP {exc.code}），请稍后重试或切换服务') from exc
        except (urllib.error.URLError, OSError, UnicodeError) as exc:
            raise RuntimeError('无法连接微软翻译，请检查网络或切换服务') from exc

    def authentication(self):
        with self.lock:
            if self.session and self.session['expires'] > time.monotonic():
                return dict(self.session)
            page, url = self.fetch(urllib.request.Request(PAGE, headers={'User-Agent': USER_AGENT}))
            self.session = parse_session(page, url, time.monotonic())
            return dict(self.session)

    def request(self, path, fields):
        if path not in {'/ttranslatev3', '/tlookupv3'}:
            raise ValueError('不支持的微软翻译接口')
        with self.slots:
            for attempt in range(2):
                session = self.authentication()
                url = session['url'].rsplit('/', 1)[0] + path + '?' + urllib.parse.urlencode({'isVertical': '1', 'IG': session['ig'], 'IID': session['iid']})
                body = urllib.parse.urlencode({**fields, 'key': session['key'], 'token': session['token']}).encode()
                request = urllib.request.Request(url, data=body, headers={'User-Agent': USER_AGENT,
                    'Referer': PAGE, 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
                raw, _ = self.fetch(request)
                try: value = json.loads(raw)
                except ValueError as exc: raise RuntimeError('微软翻译返回了无效响应，请稍后重试') from exc
                code = value.get('statusCode') if isinstance(value, dict) else None
                if code == 205 and attempt == 0:
                    with self.lock:
                        if self.session and self.session['token'] == session['token']: self.session = None
                    continue
                if code is not None and code != 200:
                    raise RuntimeError('微软翻译请求未成功，请稍后重试或切换服务')
                return value

    def one(self, text, language):
        value = self.request('/ttranslatev3', {'fromLang': 'auto-detect', 'to': language, 'text': text})
        entries = value[0].get('translations') if isinstance(value, list) and value and isinstance(value[0], dict) else None
        target = next((entry.get('text') for entry in entries if isinstance(entry, dict) and entry.get('to') == language), None) if isinstance(entries, list) else None
        if not isinstance(target, str) or not target.strip():
            raise RuntimeError('微软翻译没有返回有效译文，请重试')
        return target

    def dictionary(self, word, language):
        value = self.request('/tlookupv3', {'from': 'en', 'to': language, 'text': word})
        return value if isinstance(value, list) else []

    def translate(self, settings, texts):
        language = LANGUAGES[settings['targetLanguage']]
        return [''.join(self.one(part, language) for part in chunks(text)) for text in texts]


CLIENT = MicrosoftTranslator()


def translate(book, pid, settings, store=None):
    return translate_sentences(book, pid, settings, store, CLIENT.translate, 'microsoft', VERSION)

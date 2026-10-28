"""Azure Translator Text API v3.0; no LLM calls or embedded credentials."""
import hashlib
import html
from html.parser import HTMLParser
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from reader.text import split_sentences
from reader.translation_rules import RULES_VERSION, REDACTION, is_literal_reference, clean_person_names, nickname_hints

VERSION = 'azure-text-html-slots-v1'
LANGUAGES = {'简体中文': 'zh-Hans', '繁體中文': 'zh-Hant', '日本語': 'ja'}


def endpoint_url(endpoint, language):
    endpoint = endpoint.strip().rstrip('/')
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请先设置有效的 Azure Translator Endpoint')
    if not parsed.path.strip('/') and parsed.hostname.endswith('.cognitiveservices.azure.com'):
        endpoint += '/translator/text/v3.0'
    if not endpoint.endswith('/translate'):
        endpoint += '/translate'
    return endpoint + '?' + urllib.parse.urlencode({'api-version': '3.0', 'to': language, 'textType': 'html'})


def request_translations(settings, texts, timeout=45):
    key = settings.get('azureApiKey', '').strip()
    if not key:
        raise ValueError('请先填写 Azure Translator Key')
    language = LANGUAGES[settings['targetLanguage']]
    headers = {'Content-Type': 'application/json; charset=UTF-8', 'Ocp-Apim-Subscription-Key': key,
               'User-Agent': 'ParallelReader/0.1'}
    region = settings.get('azureRegion', '').strip()
    if region:
        headers['Ocp-Apim-Subscription-Region'] = region
    request = urllib.request.Request(endpoint_url(settings.get('azureEndpoint', ''), language),
        data=json.dumps([{'Text': text} for text in texts], ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
    try:
        # Respect the user's system proxy for the cloud service.
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        exc.close()
        messages = {400: 'Azure 请求无效，请检查 Endpoint、Region 和语言设置',
                    401: 'Azure 身份验证失败，请检查 Key', 403: 'Azure 请求被拒绝，请检查权限、Region 或网络限制',
                    429: 'Azure 请求过于频繁或配额不足，请稍后重试'}
        raise RuntimeError(messages.get(exc.code, f'Azure 服务暂时不可用（HTTP {exc.code}），请稍后重试')) from exc
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        raise RuntimeError('无法连接 Azure Translator，请检查网络和 Endpoint') from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError('Azure 返回了无效响应，请稍后重试') from exc
    if not isinstance(result, list) or len(result) != len(texts):
        raise RuntimeError('Azure 返回的翻译数量不完整，请重试')
    translated = []
    for item in result:
        entries = item.get('translations', []) if isinstance(item, dict) else []
        if not isinstance(entries, list):
            raise RuntimeError('Azure 返回了无效译文，请重试')
        value = next((entry.get('text') for entry in entries if isinstance(entry, dict) and entry.get('to') == language), None)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError('Azure 返回了空译文，请重试')
        translated.append(value)
    return translated


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'br':
            self.parts.append('\n')


def protect_text(text, names):
    import re
    spans = [match.span() for match in REDACTION.finditer(text)]
    spans += [match.span() for match in re.finditer(r'\[(?:redacted|redaction|classified)[^\]]*\]', text, re.I)]
    if names:
        pattern = re.compile(r'(?<![A-Za-z])(?:' + '|'.join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r')(?![A-Za-z])', re.I)
        spans += [match.span() for match in pattern.finditer(text)]
    cursor, parts = 0, []
    for start, end in sorted(spans):
        if start < cursor:
            continue
        parts += [html.escape(text[cursor:start]), '<span class="notranslate">' + html.escape(text[start:end]) + '</span>']
        cursor = end
    return '<p>' + ''.join(parts) + html.escape(text[cursor:]) + '</p>'


def cached_names(book, pid, settings, store):
    if store is None:
        return []
    chapter = book['paragraphs'][pid]['chapter']
    identity = [RULES_VERSION, book['id'], chapter, settings.get('baseUrl', ''), settings.get('model', '')]
    key = 'person-names:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    cached = store.language_result(key)
    if not cached:
        return []
    reference = '\n'.join(p['text'] for p in book['paragraphs'] if p['chapter'] == chapter)
    return clean_person_names(cached, reference, nickname_hints(reference))


def translate(book, pid, settings, store=None):
    return translate_sentences(book, pid, settings, store, request_translations, 'azure', VERSION)


def translate_sentences(book, pid, settings, store, request_fn, provider, version):
    source = split_sentences(book['paragraphs'][pid]['text'])
    names = cached_names(book, pid, settings, store)
    target = list(source)
    indices = [index for index, text in enumerate(source) if not is_literal_reference(text)]
    if indices:
        values = request_fn(settings, [protect_text(source[index], names) for index in indices])
        for index, value in zip(indices, values):
            parser = PlainText(); parser.feed(value); parser.close()
            translated = ''.join(parser.parts).strip()
            if not translated:
                raise RuntimeError(('Azure' if provider == 'azure' else '微软翻译') + ' 返回了空译文，请重试')
            target[index] = translated
    return {'text': ''.join(target).strip(), 'sourceSentences': source, 'targetSentences': target,
            'alignment': [{'source': [i], 'target': [i]} for i in range(len(source))],
            'alignmentMethod': provider + '-sentence-slots', 'provider': provider, 'providerVersion': version}

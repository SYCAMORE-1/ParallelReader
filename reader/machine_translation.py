"""Shared sentence alignment and markup handling for machine translation."""
import hashlib
import html
from html.parser import HTMLParser
import json
from reader.text import split_sentences
from reader.translation_rules import RULES_VERSION, REDACTION, is_literal_reference, clean_person_names, nickname_hints

LANGUAGES = {'简体中文': 'zh-Hans', '繁體中文': 'zh-Hant', '日本語': 'ja'}


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
                raise RuntimeError('Azure Translate' + ' 返回了空译文，请重试')
            target[index] = translated
    return {'text': ''.join(target).strip(), 'sourceSentences': source, 'targetSentences': target,
            'alignment': [{'source': [i], 'target': [i]} for i in range(len(source))],
            'alignmentMethod': provider + '-sentence-slots', 'provider': provider, 'providerVersion': version}

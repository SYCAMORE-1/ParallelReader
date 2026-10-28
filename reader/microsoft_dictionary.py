"""Keyless dictionary meanings and marked-word translation with past-only context."""
import html
from html.parser import HTMLParser
from reader.machine_translation import LANGUAGES, PlainText
from reader.microsoft_translation import CLIENT, utf16_length
from reader.text import sentence_window, split_sentences

VERSION = 'microsoft-word-past-only-v1'
POS = {'NOUN': 'n.', 'VERB': 'v.', 'ADJ': 'adj.', 'ADJECTIVE': 'adj.', 'ADV': 'adv.', 'ADVERB': 'adv.',
       'PRON': 'pron.', 'PRONOUN': 'pron.', 'PREP': 'prep.', 'PREPOSITION': 'prep.', 'CONJ': 'conj.',
       'CONJUNCTION': 'conj.', 'DET': 'det.', 'INTERJ': 'interj.', 'NUM': 'num.', 'OTHER': ''}


class MarkedText(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.stack = []; self.sentence = []; self.word = []
        self.feed(value); self.close()

    def handle_starttag(self, tag, attrs):
        if tag in {'br', 'img', 'hr'}: return
        attrs = dict(attrs)
        previous = self.stack[-1][1:] if self.stack else (False, False)
        self.stack.append((tag, previous[0] or attrs.get('id') == 'pr-current', previous[1] or attrs.get('id') == 'pr-word'))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]; break

    def handle_data(self, text):
        if self.stack and self.stack[-1][1]: self.sentence.append(text)
        if self.stack and self.stack[-1][2]: self.word.append(text)


def context_markup(book, pid, offset, word):
    window = sentence_window(book, pid, offset, radius=10, following_radius=0)
    cursor = 0
    for part in split_sentences(book['paragraphs'][pid]['text']):
        if cursor <= offset < cursor + len(part): break
        cursor += len(part)
    relative = offset - cursor
    sentence = window['sentence']
    left, right = sentence[:relative], sentence[relative + len(word):]
    selected = sentence[relative:relative + len(word)]
    def current():
        return '<p id="pr-current">' + html.escape(left) + '<b id="pr-word">' + html.escape(selected) + '</b>' + html.escape(right) + '</p>'
    trimmed = False
    while utf16_length(current()) > 950:
        trimmed = True
        if len(left) > len(right): left = left[max(1, len(left) // 4):]
        elif right: right = right[:-max(1, len(right) // 4)]
        else: break
    kept = []
    for previous in reversed(window['previousSentences']):
        candidate = [previous, *kept]
        if utf16_length('<p>' + html.escape(''.join(candidate)) + '</p>' + current()) > 1000: break
        kept = candidate
    return ('<p>' + html.escape(''.join(kept)) + '</p>' if kept else '') + current(), current(), len(kept), trimmed or len(kept) < len(window['previousSentences'])


def explain(book, pid, word, settings, offset):
    language = LANGUAGES[settings['targetLanguage']]
    try: entries = CLIENT.dictionary(word, language)
    except RuntimeError: entries = []  # Some language pairs have no bilingual dictionary.
    entry = entries[0] if entries and isinstance(entries[0], dict) else {}
    values = entry.get('translations', [])
    meanings = [item for item in values if isinstance(item, dict) and isinstance(item.get('displayTarget'), str) and item['displayTarget'].strip()] if isinstance(values, list) else []
    basic = meanings[0]['displayTarget'] if meanings else ''
    if not basic:
        parser = PlainText(); parser.feed(CLIENT.one('<p>' + html.escape(word) + '</p>', language)); basic = ''.join(parser.parts).strip()
    if not basic: raise RuntimeError('Azure Translate没有返回有效词义，请重试')
    markup, current, before, trimmed = context_markup(book, pid, offset, word)
    parsed = MarkedText(CLIENT.one(markup, language))
    if not parsed.sentence:
        raw = CLIENT.one(current, language); parsed = MarkedText(raw)
        if not parsed.sentence:
            plain = PlainText(); plain.feed(raw); parsed.sentence = plain.parts
    contextual = ''.join(parsed.word).strip()
    sentence = ''.join(parsed.sentence).strip()
    if not sentence: raise RuntimeError('Azure Translate没有返回有效语境译文，请重试')
    tags = list(dict.fromkeys(POS.get(str(item.get('posTag') or '').upper(), '') for item in meanings)); tags = [tag for tag in tags if tag]
    lemma = entry.get('normalizedSource')
    return {'lemma': lemma if isinstance(lemma, str) and lemma.strip() else word, 'partOfSpeech': ' / '.join(tags[:3]),
            'basicMeaning': basic, 'contextMeaning': contextual or sentence, 'sentenceTranslation': sentence,
            'contextKind': 'term' if contextual else 'sentence', 'note': '', 'provider': 'microsoft',
            'model': 'Azure Translate', 'contextWindow': {'before': before, 'after': 0, 'trimmed': trimmed}}

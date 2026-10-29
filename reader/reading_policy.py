"""Identify books that are already readable without another translation pass."""
import re


def source_only(book):
    if type(book.get('sourceOnly')) is bool:
        return book['sourceOnly']
    if book.get('alreadyTranslated'):
        return True
    language = str(book.get('language', '')).lower()
    if language.startswith(('zh', 'cmn', 'yue')):
        return True
    sample = ''.join(p['text'] for p in book.get('paragraphs', [])[:160])[:60000]
    chinese = len(re.findall(r'[\u3400-\u9fff]', sample))
    latin = len(re.findall(r'[A-Za-z]', sample))
    kana = len(re.findall(r'[\u3040-\u30ff]', sample))
    return bool(chinese >= 20 and kana < max(3, chinese * .05)
                and (language.startswith(('zh', 'cmn', 'yue')) or chinese / max(1, chinese + latin) >= .6))


def prepare_book(book):
    return {**book, 'sourceOnly': source_only(book)}

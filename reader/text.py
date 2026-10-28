"""Shared, lossless sentence boundaries for translation and sentence navigation."""
import re

ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "u.s", "u.k", "a.m", "p.m", "lt", "col", "sgt", "gen", "capt", "cmdr", "adm"}


def split_sentences(text):
    result, start = [], 0
    pattern = r'''[。！？]+[”’」』）)\]]*\s*|[.!?]+[”’"')\]]*(?:\s+|$)'''
    for match in re.finditer(pattern, text):
        punctuation = match.group().rstrip()
        if punctuation.startswith('.') and not punctuation.startswith('...'):
            word = re.search(r"([A-Za-z.]+)$", text[:match.start()])
            if word and (word[1].lower() in ABBREVIATIONS or (len(word[1]) == 1 and word[1].isupper()) or re.fullmatch(r'(?:[A-Z]\.)+[A-Z]', word[1])):
                continue
        if re.search(r'''[”’"']''', punctuation) and match.end() < len(text) and text[match.end()].islower():
            continue
        result.append(text[start:match.end()])
        start = match.end()
    if start < len(text):
        result.append(text[start:])
    # EPUBs sometimes put a space between nested closing quotation marks:
    # ...reason.’ ”. Keep the closing marks with the preceding sentence,
    # losslessly, rather than inventing a quotation-only sentence to translate.
    merged = []
    for part in result:
        if merged and re.fullmatch(r'''[\s”’"'」』）)\]]+''', part):
            merged[-1] += part
        else:
            merged.append(part)
    return merged or [text]


def sentence_window(book, pid, offset=0, radius=10, following_radius=None):
    """Exact sentence counts across paragraph/chapter boundaries, without clipping."""
    parts = split_sentences(book['paragraphs'][pid]['text'])
    cursor, selected = 0, len(parts) - 1
    for index, part in enumerate(parts):
        if cursor <= offset < cursor + len(part):
            selected = index
            break
        cursor += len(part)
    previous = parts[:selected][-radius:]
    following_radius = radius if following_radius is None else following_radius
    following = parts[selected + 1:selected + 1 + following_radius]
    index = pid - 1
    while len(previous) < radius and index >= 0:
        previous = split_sentences(book['paragraphs'][index]['text']) + previous
        previous = previous[-radius:]
        index -= 1
    index = pid + 1
    while len(following) < following_radius and index < len(book['paragraphs']):
        following.extend(split_sentences(book['paragraphs'][index]['text']))
        following = following[:following_radius]
        index += 1
    return {'sentence': parts[selected], 'previousSentences': previous, 'followingSentences': following}


def paragraph_context(book, pid, radius=10):
    text = book['paragraphs'][pid]['text']
    return {'previous_context': sentence_window(book, pid, 0, radius)['previousSentences'],
            'following_context': sentence_window(book, pid, max(0, len(text) - 1), radius)['followingSentences']}

"""Select saved text for reading without changing the service used for new work."""
import json
import time
from reader.translation import profile, draft_settings
from reader.providers import review_enabled


def sources(store, book, settings):
    current = profile(settings)
    with store.lock:
        rows = store.db.execute('SELECT t.profile,COUNT(*),MAX(t.created),p.value FROM translations t '
            'LEFT JOIN translation_profiles p ON p.profile=t.profile '
            'WHERE t.book=? AND t.paragraph>=0 AND t.paragraph<? AND LENGTH(TRIM(t.text))>0 '
            'GROUP BY t.profile ORDER BY COUNT(*) DESC,MAX(t.created) DESC', (book['id'], len(book['paragraphs']))).fetchall()
        result = []
        for fingerprint, count, updated, raw in rows:
            metadata = json.loads(raw) if raw else {}
            if not metadata:
                detail = store.db.execute('SELECT value FROM translation_details WHERE book=? AND profile=? LIMIT 1', (book['id'], fingerprint)).fetchone()
                detail = json.loads(detail[0]) if detail else {}
                provider = detail.get('provider', 'llm')
                job = store.db.execute('SELECT model FROM book_translation_jobs WHERE book=? AND profile=?', (book['id'], fingerprint)).fetchone()
                metadata = {'provider': provider, 'model': {'azure': 'Azure Translate', 'microsoft': 'Azure Translate'}.get(provider, (job[0] if job else '') or '大模型译文'), 'targetLanguage': None}
            if metadata.get('provider') in {'azure', 'microsoft'}:
                metadata['model'] = 'Azure Translate'
            result.append({**metadata, 'profile': fingerprint, 'completed': count, 'total': len(book['paragraphs']),
                           'percent': round(count * 100 / max(1, len(book['paragraphs'])), 1), 'updated': updated, 'current': fingerprint == current})
        return result


def selection(store, book, settings):
    current = profile(settings)
    if book.get('sourceOnly'):
        return {'book': book['id'], 'configurationProfile': current, 'selectedProfile': current, 'sources': [], 'selectionRequired': False, 'sourceOnly': True}
    available = sources(store, book, settings)
    with store.lock:
        row = store.db.execute('SELECT source FROM reading_sources WHERE book=? AND configuration=?', (book['id'], current)).fetchone()
    valid = row and (row[0] == current or any(item['profile'] == row[0] for item in available))
    selected = row[0] if valid else current
    primary = profile(draft_settings(settings)) if review_enabled(settings) else current
    # An initial translation for the same LLM pipeline can go straight to
    # proofreading; it is not a reason to block that pipeline with a prompt.
    has_current = any(item['current'] or item['profile'] == primary for item in available)
    return {'book': book['id'], 'configurationProfile': current, 'selectedProfile': selected, 'sources': available,
            'selectionRequired': bool(available and not has_current and not valid)}


def choose(store, book, settings, source, configuration):
    current = profile(settings)
    if configuration != current:
        raise ValueError('翻译配置已变化，请刷新后重新选择译文')
    if source != current and not any(item['profile'] == source for item in sources(store, book, settings)):
        raise ValueError('所选本地译文不存在')
    with store.lock, store.db:
        store.db.execute('INSERT OR REPLACE INTO reading_sources VALUES (?,?,?,?)', (book['id'], current, source, time.time()))
    store.touch_book(book['id'])
    return selection(store, book, settings)


def translations(store, book, ids, settings):
    state = selection(store, book, settings)
    if book.get('sourceOnly'):
        return state, {pid: {'text': book['paragraphs'][pid]['text'], 'profile': state['selectedProfile']} for pid in ids}
    profiles = list(dict.fromkeys([state['selectedProfile'], state['configurationProfile']]))
    result = {}
    for fingerprint in reversed(profiles):
        for pid, text in store.translations(book['id'], ids, fingerprint).items():
            if text.strip(): result[pid] = {'text': text, 'profile': fingerprint}
    return state, result


def progress(store, book, settings):
    state, cached = translations(store, book, [p['id'] for p in book['paragraphs']], settings)
    total = len(book['paragraphs'])
    return {**state, 'completed': len(cached), 'total': total, 'percent': round(len(cached) * 100 / max(1, total), 1)}

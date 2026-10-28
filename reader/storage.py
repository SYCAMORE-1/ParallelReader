import json
import hashlib
import re
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path


def migrate_settings(value):
    value = dict(value)
    for key in ['azureEndpoint', 'azureApiKey', 'azureRegion', 'hasAzureApiKey', 'clearAzureApiKey']:
        value.pop(key, None)
    if value.get('translationProvider') == 'azure':
        value['translationProvider'] = 'microsoft'
    if value.get('wordProvider') == 'azure':
        value['wordProvider'] = 'microsoft'
    # Preserve a legacy inherited lookup model once, then keep it independent.
    if value.get('wordProvider') == 'llm' and not value.get('wordBaseUrl') and not value.get('wordModel'):
        if value.get('baseUrl') and value.get('model'):
            value.update(wordBaseUrl=value['baseUrl'], wordModel=value['model'], wordApiKey=value.get('apiKey', ''))
    return value


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        if (self.directory / 'maintenance.lock').exists():
            raise RuntimeError('书库正在整理，请稍后再启动阅读器')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "reader.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS books (id TEXT PRIMARY KEY, data TEXT, page INTEGER, updated REAL);
            CREATE TABLE IF NOT EXISTS removed_books (book TEXT PRIMARY KEY, removed REAL);
            CREATE TABLE IF NOT EXISTS bookmarks (book TEXT PRIMARY KEY, page INTEGER, manual INTEGER, updated REAL);
            CREATE TABLE IF NOT EXISTS translations (book TEXT, paragraph INTEGER, profile TEXT, text TEXT, created REAL, PRIMARY KEY(book, paragraph, profile));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS translation_profiles (profile TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS reading_sources (book TEXT, configuration TEXT, source TEXT, updated REAL, PRIMARY KEY(book, configuration));
            CREATE TABLE IF NOT EXISTS language_cache (key TEXT PRIMARY KEY, value TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS translation_details (book TEXT, paragraph INTEGER, profile TEXT, value TEXT, PRIMARY KEY(book, paragraph, profile));
            CREATE TABLE IF NOT EXISTS translation_drafts (book TEXT, paragraph INTEGER, profile TEXT, value TEXT, created REAL, PRIMARY KEY(book, paragraph, profile));
            CREATE TABLE IF NOT EXISTS book_translation_jobs (book TEXT, profile TEXT, model TEXT, state TEXT, reason TEXT, updated REAL, PRIMARY KEY(book, profile));
            CREATE TABLE IF NOT EXISTS book_translation_failures (book TEXT, profile TEXT, paragraph INTEGER, error TEXT, created REAL, PRIMARY KEY(book, profile, paragraph));
            CREATE TABLE IF NOT EXISTS reproof_jobs (id TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS translation_revisions (id INTEGER PRIMARY KEY, book TEXT, paragraph INTEGER, profile TEXT, job TEXT, text TEXT, detail TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS translations_profile ON translations(book, profile);
            INSERT OR IGNORE INTO bookmarks SELECT id,page,0,updated FROM books;
        """)
        self.db.commit()
        self.register_translation_profiles(self.settings())

    def save_source(self, book_id, data):
        if hashlib.sha256(data).hexdigest()[:24] != book_id:
            raise ValueError('EPUB 文件与书籍不匹配')
        directory = self.directory / 'sources'
        directory.mkdir(exist_ok=True)
        target = directory / (book_id + '.epub')
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()[:24] == book_id:
            return
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def image(self, book, asset):
        book = self.book(book) if isinstance(book, str) else book
        item = book.get('images', {}).get(asset)
        if item is None:
            raise KeyError('插图不存在')
        if not re.fullmatch(r'[a-f0-9]{24}', book['id']):
            raise ValueError('无效的书籍标识')
        if item['mime'] not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}:
            raise ValueError('不支持的插图格式')
        source = self.directory / 'sources' / (book['id'] + '.epub')
        if not source.is_file():
            raise ValueError('插图源文件尚未恢复，请重新导入原书')
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo(item['path'])
            if info.file_size > 20 * 1024 * 1024:
                raise ValueError('插图文件过大')
            data = archive.read(info)
        return data, item['mime']

    def add(self, book, source_bytes=None):
        if source_bytes is not None:
            self.save_source(book['id'], source_bytes)
        with self.lock, self.db:
            row = self.db.execute('SELECT data,page FROM books WHERE id=?', (book['id'],)).fetchone()
            if row and source_bytes is not None:
                previous = json.loads(row[0])
                old_text = [(p['id'], p['text']) for p in previous['paragraphs']]
                new_text = [(p['id'], p['text']) for p in book['paragraphs']]
                if old_text != new_text:
                    raise ValueError('正文与已导入版本不同，原有译文和进度已保留')
                page_of = {pid: page['id'] for page in book['pages'] for pid in page['paragraphs']}
                def remap(page):
                    ids = previous['pages'][max(0, min(page, len(previous['pages']) - 1))]['paragraphs']
                    return page_of[ids[0]] if ids else min(page, len(book['pages']) - 1)
                keys = ['paragraphs', 'pages', 'chapters', 'images', 'illustrations', 'imageVersion', 'coverAsset']
                updated = {**previous, **{key: book[key] for key in keys if key in book}}
                if len(previous['pages']) != len(book['pages']) and not previous.get('legacyPageMap'):
                    updated['legacyPageMap'] = [remap(page) for page in range(len(previous['pages']))]
                updated['startPage'] = remap(previous['startPage'])
                updated['cover'] = book.get('cover') or previous.get('cover')
                self.db.execute('UPDATE books SET data=?,page=? WHERE id=?', (json.dumps(updated, ensure_ascii=False), remap(row[1]), book['id']))
                mark = self.bookmark(book['id'])
                if mark:
                    self.db.execute('UPDATE bookmarks SET page=? WHERE book=?', (remap(mark['page']), book['id']))
            self.db.execute("INSERT OR IGNORE INTO books VALUES (?,?,?,?)", (book["id"], json.dumps(book, ensure_ascii=False), book["startPage"], time.time()))
            self.db.execute('INSERT OR IGNORE INTO bookmarks SELECT id,page,0,updated FROM books WHERE id=?', (book['id'],))
            self.db.execute('DELETE FROM removed_books WHERE book=?', (book['id'],))
        return self.book(book["id"])

    def remove(self, book_id):
        """Remove a shelf entry while keeping the source, translations and position recoverable."""
        with self.lock, self.db:
            self.book(book_id)
            self.db.execute('INSERT OR REPLACE INTO removed_books VALUES (?,?)', (book_id, time.time()))

    def book(self, book_id):
        with self.lock:
            row = self.db.execute("SELECT data,page FROM books WHERE id=? AND NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id)", (book_id,)).fetchone()
            bookmark = self.bookmark(book_id)
        if row is None:
            raise KeyError("书籍不存在")
        return {**json.loads(row[0]), "currentPage": row[1], 'bookmark': bookmark}

    def library_progress(self, fingerprint, draft_profile=None):
        """Show the latest whole-book review pass, falling back to finalized translations."""
        with self.lock:
            totals = self.db.execute("SELECT id,json_array_length(data,'$.paragraphs') FROM books WHERE NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id)").fetchall()
            completed = dict(self.db.execute('SELECT book,count(*) FROM translations WHERE profile=? GROUP BY book', (fingerprint,)))
            drafted = dict(self.db.execute('''SELECT book,count(*) FROM (
                SELECT book,paragraph FROM translation_drafts WHERE profile=?
                UNION SELECT book,paragraph FROM translations WHERE profile IN (?,?)
                ) GROUP BY book''', (draft_profile, fingerprint, draft_profile))) if draft_profile else completed
            jobs = {job['id']: job for row in self.db.execute("SELECT value FROM reproof_jobs WHERE json_extract(value,'$.profile')=?", (fingerprint,)) for job in [json.loads(row[0])]}
        result = {}
        for book, total in totals:
            ready = min(total, completed.get(book, 0))
            result[book] = {'completed': ready, 'total': total,
                            'percent': 100.0 if ready == total else min(99.9, round(ready * 100 / max(total, 1), 1)),
                            'drafted': min(total, drafted.get(book, 0)),
                            'reviewEnabled': draft_profile is not None, 'profile': fingerprint}
        latest = {}
        for job in jobs.values():
            if job['scope'] != 'book' or job['book'] not in result:
                continue
            previous = latest.get(job['book'])
            if previous is None or (job.get('round', 1), job['created']) > (previous.get('round', 1), previous['created']):
                latest[job['book']] = job
        for book, parent in latest.items():
            children = [jobs[key] for key in parent['children'] if key in jobs]
            total = sum(len(child['paragraphs']) for child in children)
            if len(children) != len(parent['children']) or total != result[book]['total']:
                continue
            ready = sum(len(set(child['completed']) & set(child['paragraphs'])) for child in children)
            states = {child['state'] for child in children}
            state = ('completed' if states == {'completed'} else 'paused' if parent['state'] == 'paused' or 'paused' in states
                     else 'running' if 'running' in states else 'queued' if 'queued' in states else 'failed')
            result[book].update(mode='reproof', reproofId=parent['id'], round=parent.get('round', 1),
                                state=state, created=parent['created'], updated=max(job.get('updated', job['created']) for job in [parent, *children]),
                                completed=ready, total=total,
                                percent=100.0 if ready == total else min(99.9, round(ready * 100 / max(total, 1), 1)))
        if hasattr(self, 'image_progress'):
            return self.image_progress(result)
        return result

    def library(self, fingerprint=None, draft_profile=None):
        with self.lock:
            rows = self.db.execute("SELECT data,page,updated FROM books WHERE NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id) ORDER BY updated DESC").fetchall()
            marks = {row[0]: {'page': row[1], 'manual': bool(row[2]), 'updated': row[3]} for row in self.db.execute('SELECT book,page,manual,updated FROM bookmarks')}
        statistics = self.library_progress(fingerprint, draft_profile) if fingerprint else {}
        return [{k: b[k] for k in ("id", "title", "author", "cover")} | {"pageCount": len(b["pages"]), "currentPage": page, "updated": updated, 'bookmark': marks.get(b['id'])}
                | ({'translationProgress': statistics.get(b['id'])} if fingerprint else {}) for raw, page, updated in rows for b in [json.loads(raw)]]

    def progress(self, book_id, page, layout_version=None):
        book = self.book(book_id)
        if book.get('legacyPageMap') and layout_version != book.get('imageVersion'):
            page = book['legacyPageMap'][max(0, min(int(page), len(book['legacyPageMap']) - 1))]
        page = max(0, min(int(page), len(book["pages"]) - 1))
        with self.lock, self.db:
            self.db.execute("UPDATE books SET page=?,updated=? WHERE id=?", (page, time.time(), book_id))
            self.db.execute('UPDATE bookmarks SET page=?,updated=? WHERE book=? AND manual=0', (page, time.time(), book_id))

    def bookmark(self, book_id):
        with self.lock:
            row = self.db.execute('SELECT page,manual,updated FROM bookmarks WHERE book=?', (book_id,)).fetchone()
        return {'page': row[0], 'manual': bool(row[1]), 'updated': row[2]} if row else None

    def set_bookmark(self, book_id, page, manual=True, layout_version=None):
        book = self.book(book_id)
        if type(page) is int and book.get('legacyPageMap') and layout_version != book.get('imageVersion'):
            page = book['legacyPageMap'][max(0, min(page, len(book['legacyPageMap']) - 1))]
        if type(page) is not int or not 0 <= page < len(book['pages']) or type(manual) is not bool:
            raise ValueError('请选择有效的书签页码')
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO bookmarks VALUES (?,?,?,?)', (book_id, page if manual else book['currentPage'], int(manual), time.time()))
        return self.bookmark(book_id)

    def settings(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key='model'").fetchone()
            saved = json.loads(row[0]) if row else {}
            migrated = migrate_settings(saved)
            if saved != migrated:
                with self.db:
                    self.db.execute("UPDATE settings SET value=? WHERE key='model'", (json.dumps(migrated, ensure_ascii=False),))
        defaults = {"baseUrl": "", "model": "", "apiKey": "", "targetLanguage": "简体中文",
                    "reviewEnabled": True, "reviewBaseUrl": "", "reviewModel": "", "reviewApiKey": "",
                    "wordBaseUrl": "", "wordModel": "", "wordApiKey": "",
                    "translationProvider": "llm", "wordProvider": "microsoft"}
        return defaults | migrated

    def save_settings(self, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('model',?)", (json.dumps(migrate_settings(value), ensure_ascii=False),))
        self.register_translation_profiles(self.settings())

    def register_translation_profiles(self, settings):
        from reader.translation import profile, draft_settings
        from reader.providers import service_name
        with self.lock, self.db:
            for values in [settings, draft_settings(settings)]:
                metadata = {'provider': values.get('translationProvider', 'llm'), 'model': service_name(values),
                            'targetLanguage': values['targetLanguage'], 'reviewEnabled': bool(values.get('reviewEnabled'))}
                self.db.execute('INSERT OR REPLACE INTO translation_profiles VALUES (?,?)',
                                (profile(values), json.dumps(metadata, ensure_ascii=False)))

    def translation(self, book, paragraph, profile):
        with self.lock:
            row = self.db.execute("SELECT text FROM translations WHERE book=? AND paragraph=? AND profile=?", (book, paragraph, profile)).fetchone()
        return row[0] if row else None

    def translations(self, book, paragraphs, profile):
        if not paragraphs:
            return {}
        with self.lock:
            rows = self.db.execute(f"SELECT paragraph,text FROM translations WHERE book=? AND profile=? AND paragraph IN ({','.join('?' for _ in paragraphs)})", (book, profile, *paragraphs)).fetchall()
        return dict(rows)

    def cache(self, book, paragraph, profile, text, detail=None):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?,?)", (book, paragraph, profile, text, time.time()))
            if detail is not None:
                self.db.execute("INSERT OR REPLACE INTO translation_details VALUES (?,?,?,?)", (book, paragraph, profile, json.dumps(detail, ensure_ascii=False)))
            else:
                self.db.execute("DELETE FROM translation_details WHERE book=? AND paragraph=? AND profile=?", (book, paragraph, profile))
            self.db.execute("DELETE FROM book_translation_failures WHERE book=? AND profile=? AND paragraph=?", (book, profile, paragraph))

    def cached_paragraphs(self, book, profile):
        with self.lock:
            return {row[0] for row in self.db.execute("SELECT paragraph FROM translations WHERE book=? AND profile=?", (book, profile))}

    def draft(self, book, paragraph, profile):
        with self.lock:
            row = self.db.execute("SELECT value FROM translation_drafts WHERE book=? AND paragraph=? AND profile=?", (book, paragraph, profile)).fetchone()
            if row:
                return json.loads(row[0])
            # Existing translations from this primary model can be proofread
            # without paying for another initial translation.
            text = self.translation(book, paragraph, profile)
            return ({**(self.translation_detail(book, paragraph, profile) or {}), 'text': text} if text else None)

    def cache_draft(self, book, paragraph, profile, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO translation_drafts VALUES (?,?,?,?,?)", (book, paragraph, profile, json.dumps(value, ensure_ascii=False), time.time()))

    def drafted_paragraphs(self, book, profile):
        with self.lock:
            rows = self.db.execute("SELECT paragraph FROM translation_drafts WHERE book=? AND profile=? UNION SELECT paragraph FROM translations WHERE book=? AND profile=?", (book, profile, book, profile))
            return {row[0] for row in rows}

    def bulk_jobs(self):
        with self.lock:
            rows = self.db.execute("SELECT book,profile,model,state,reason FROM book_translation_jobs ORDER BY updated").fetchall()
        return [dict(zip(('book', 'profile', 'model', 'state', 'reason'), row)) for row in rows]

    def save_bulk_job(self, book, profile, model, state, reason=''):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO book_translation_jobs VALUES (?,?,?,?,?,?)", (book, profile, model, state, reason, time.time()))

    def bulk_failures(self, book, profile):
        with self.lock:
            return dict(self.db.execute("SELECT paragraph,error FROM book_translation_failures WHERE book=? AND profile=?", (book, profile)))

    def fail_bulk_paragraph(self, book, profile, paragraph, error):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO book_translation_failures VALUES (?,?,?,?,?)", (book, profile, paragraph, error, time.time()))

    def clear_bulk_failures(self, book, profile):
        with self.lock, self.db:
            self.db.execute("DELETE FROM book_translation_failures WHERE book=? AND profile=?", (book, profile))

    def translation_detail(self, book, paragraph, profile):
        with self.lock:
            row = self.db.execute("SELECT value FROM translation_details WHERE book=? AND paragraph=? AND profile=?", (book, paragraph, profile)).fetchone()
        return json.loads(row[0]) if row else None

    def translation_snapshot(self, book, paragraph, profile):
        with self.lock:
            row = self.db.execute('SELECT t.text,t.created,d.value FROM translations t LEFT JOIN translation_details d ON t.book=d.book AND t.paragraph=d.paragraph AND t.profile=d.profile WHERE t.book=? AND t.paragraph=? AND t.profile=?', (book, paragraph, profile)).fetchone()
        return {'text': row[0], 'created': row[1], 'rawDetail': row[2], 'detail': json.loads(row[2]) if row[2] else None} if row else None

    def replace_translation(self, book, paragraph, profile, expected, result, job):
        """Atomically archive/replace only the version that was reviewed."""
        with self.lock, self.db:
            current = self.translation_snapshot(book, paragraph, profile)
            if current != expected:
                return False
            self.db.execute('INSERT INTO translation_revisions(book,paragraph,profile,job,text,detail,created) VALUES (?,?,?,?,?,?,?)', (book, paragraph, profile, job, current['text'], current['rawDetail'], time.time()))
            self.db.execute('UPDATE translations SET text=?,created=? WHERE book=? AND paragraph=? AND profile=?', (result['text'], time.time(), book, paragraph, profile))
            self.db.execute('INSERT OR REPLACE INTO translation_details VALUES (?,?,?,?)', (book, paragraph, profile, json.dumps(result, ensure_ascii=False)))
            self.db.execute('DELETE FROM book_translation_failures WHERE book=? AND profile=? AND paragraph=?', (book, profile, paragraph))
            return True

    def reproof_jobs(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT value FROM reproof_jobs')]

    def save_reproof_job(self, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO reproof_jobs VALUES (?,?)', (value['id'], json.dumps(value, ensure_ascii=False)))

    def save_reproof_jobs(self, values):
        """Create/update a whole-book group and its chapters atomically."""
        with self.lock, self.db:
            self.db.executemany('INSERT OR REPLACE INTO reproof_jobs VALUES (?,?)',
                                [(value['id'], json.dumps(value, ensure_ascii=False)) for value in values])

    def language_result(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM language_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_language_result(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO language_cache VALUES (?,?,?)", (key, json.dumps(value, ensure_ascii=False), time.time()))


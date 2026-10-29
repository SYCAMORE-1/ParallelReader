"""Human-readable book folders with self-contained, credential-free transfer state."""
import hashlib
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path


BOOK_TABLES = {
    'books': 'id=?', 'bookmarks': 'book=?', 'translations': 'book=?',
    'translation_details': 'book=?', 'translation_drafts': 'book=?',
    'reading_sources': 'book=?', 'book_translation_jobs': 'book=?',
    'book_translation_failures': 'book=?', 'translation_revisions': 'book=?',
    'reproof_jobs': "json_extract(value,'$.book')=?",
    'language_cache_books': 'book=?',
    'language_cache': 'key IN (SELECT key FROM language_cache_books WHERE book=?)',
}


def image_cache_alias(book_id, origin):
    return 'image-translation:' + hashlib.sha256(json.dumps([book_id, origin]).encode()).hexdigest()


def folder_name(book):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', book.get('title') or 'Untitled')
    title = re.sub(r'\s+', ' ', title).strip(' .')[:64].rstrip(' .') or 'Untitled'
    while len(title.encode('utf-16-le')) > 128:
        title = title[:-1]
    if title.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}:
        title = '_' + title
    suffix = re.sub(r'[^A-Za-z0-9_-]', '', book['id'])[:12]
    if not suffix:
        raise ValueError('无效的书籍标识')
    return title + ' -- ' + suffix


def checked(root, path):
    root, path = Path(root).resolve(), Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('书库路径越界或使用了链接')
    return path


class BookFolders:
    def __init__(self, store):
        self.store, self.root = store, store.directory.resolve()
        self.root_books = checked(self.root, self.root / 'books')
        self.root_books.mkdir(exist_ok=True)
        self.paths, self.dirty = {}, set()
        self.last_flush = 0
        self.warnings = []

    def directory(self, book, create=False):
        if isinstance(book, str):
            row = self.store.db.execute('SELECT data FROM books WHERE id=?', (book,)).fetchone()
            if not row:
                raise KeyError('书籍不存在')
            book = json.loads(row[0])
        bid = book['id']
        if bid not in self.paths:
            row = self.store.db.execute('SELECT folder FROM book_locations WHERE book=?', (bid,)).fetchone()
            name = row[0] if row else folder_name(book)
            self.paths[bid] = checked(self.root_books, self.root_books / name)
        directory = checked(self.root_books, self.paths[bid])
        if create:
            directory.mkdir(exist_ok=True)
            metadata = {'format': 1, 'id': bid, 'title': book.get('title', ''), 'author': book.get('author', '')}
            target = directory / 'book.json'
            if target.is_file() and json.loads(target.read_text(encoding='utf-8')).get('id') != bid:
                raise ValueError('书文件夹名称冲突，请重命名已有文件夹')
            temporary = directory / 'book.json.tmp'
            temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
            with self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO book_locations VALUES (?,?)', (bid, directory.name))
        return directory

    def available(self, bid):
        return bid in self.paths and self.paths[bid].is_dir()

    def touch(self, bid):
        if bid and bid in self.paths:
            self.dirty.add(bid)

    def initialise(self):
        # A copied folder is discoverable even when the destination has no
        # central database. Folder names are labels; book.json carries identity.
        for directory in sorted(self.root_books.iterdir()):
            if not directory.is_dir():
                continue
            checked(self.root_books, directory)
            metadata = directory / 'book.json'
            if not metadata.is_file():
                continue
            value = json.loads(metadata.read_text(encoding='utf-8'))
            if value.get('format') != 1 or not isinstance(value.get('id'), str):
                raise ValueError('不支持的书文件夹：' + directory.name)
            bid = value['id']
            if bid in self.paths:
                raise ValueError('同一本书有两个文件夹，请保留一个：' + directory.name)
            self.paths[bid] = directory
            if not self.store.db.execute('SELECT 1 FROM books WHERE id=?', (bid,)).fetchone():
                self.import_state(directory, bid)
            with self.store.db:
                self.store.db.execute('INSERT OR REPLACE INTO book_locations VALUES (?,?)', (bid, directory.name))
        for bid, raw in self.store.db.execute('SELECT id,data FROM books').fetchall():
            book = json.loads(raw)
            location = self.store.db.execute('SELECT folder FROM book_locations WHERE book=?', (bid,)).fetchone()
            # A deliberately moved-out book stays absent; do not recreate it.
            if location and bid not in self.paths:
                continue
            directory = self.directory(book, create=True)
            legacy = checked(self.root, self.root / 'sources' / (bid + '.epub'))
            if legacy.is_file():
                target = directory / 'original.epub'
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(legacy.read_bytes()).digest():
                        raise ValueError('原书文件冲突：' + book['title'])
                    legacy.unlink()
                else:
                    legacy.replace(target)
            exports = self.root / 'exports'
            for old in exports.glob(bid + '-*'):
                if old.is_file():
                    destination = directory / 'exports'
                    destination.mkdir(exist_ok=True)
                    self.move_file(old, destination / old.name)
            self.touch(bid)
        self.migrate_images()
        self.flush(force=True)
        for name in ['sources', 'translated-images', 'exports']:
            legacy = checked(self.root, self.root / name)
            if legacy.is_dir() and not any(legacy.iterdir()):
                legacy.rmdir()

    def move_file(self, source, target):
        checked(self.root, source); checked(self.root, target)
        if target.exists():
            if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                raise ValueError('书籍资源文件冲突：' + target.name)
            source.unlink()
        else:
            source.replace(target)

    def owner(self, value):
        if not isinstance(value, dict):
            return None
        bid = value.get('book')
        match = re.match(r'/api/books/([^/]+)/', value.get('imageUrl', ''))
        return bid if isinstance(bid, str) else match[1] if match else None

    def migrate_images(self):
        rows = self.store.db.execute("SELECT key,value FROM language_cache WHERE key LIKE 'image-%'").fetchall()
        old_files = set()
        for key, raw in rows:
            value = json.loads(raw)
            bid = self.owner(value)
            if bid not in self.paths or not self.available(bid):
                continue
            with self.store.db:
                for owned_key in [key, key + ':draft', key + ':reviewed']:
                    self.store.db.execute('INSERT OR IGNORE INTO language_cache_books VALUES (?,?)', (owned_key, bid))
            relative = value.get('render', {}).get('file')
            if relative:
                source = checked(self.root, self.root / relative)
                destination = self.directory(bid) / 'translated-images'
                destination.mkdir(exist_ok=True)
                target = checked(self.root, destination / source.name)
                if source.is_file():
                    if source.resolve() != target.resolve():
                        if not target.exists(): shutil.copy2(source, target)
                        if hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                            raise ValueError('译图文件冲突：' + target.name)
                        old_files.add(source)
                    value['render']['file'] = target.relative_to(self.root).as_posix()
                    with self.store.db:
                        self.store.db.execute('UPDATE language_cache SET value=? WHERE key=?', (json.dumps(value, ensure_ascii=False), key))
                else:
                    self.warnings.append('缺少已有译图：' + relative)
            self.touch(bid)
        # Some historical entries can share a bitmap. Remove a flat copy only
        # after every remaining cache reference has been redirected.
        referenced = set()
        for raw, in self.store.db.execute("SELECT value FROM language_cache WHERE key LIKE 'image-translation:%'"):
            value = json.loads(raw)
            relative = value.get('render', {}).get('file') if isinstance(value, dict) else None
            if relative: referenced.add((self.root / relative).resolve())
        for path in old_files:
            if path.resolve() not in referenced: checked(self.root, path).unlink()

    def state_rows(self, bid):
        return self.portable_rows(self.store.db, bid)

    @staticmethod
    def portable_rows(db, bid):
        result = {}
        for table, clause in BOOK_TABLES.items():
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            rows = db.execute('SELECT * FROM ' + table + ' WHERE ' + clause, (bid,)).fetchall()
            if table == 'book_translation_jobs':
                rows = [(*r[:3], 'paused' if r[3] in {'running', 'queued'} else r[3], *r[4:]) for r in rows]
            elif table in {'language_cache', 'reproof_jobs'}:
                edited = []
                for row in rows:
                    value = json.loads(row[1])
                    if isinstance(value, dict):
                        value = dict(value)
                        if value.get('state') in {'running', 'queued'}:
                            value.update(state='paused', reason='迁移副本已暂停，请确认服务配置后继续。')
                        if value.get('render', {}).get('file'):
                            value['render'] = {**value['render'], 'file': 'translated-images/' + Path(value['render']['file']).name}
                    edited.append((row[0], json.dumps(value, ensure_ascii=False), *row[2:]))
                rows = edited
            result[table] = rows
        profiles = set()
        for table in ['translations', 'translation_drafts', 'book_translation_jobs']:
            index = 1 if table == 'book_translation_jobs' else 2
            profiles.update(row[index] for row in result.get(table, []))
        result['translation_profiles'] = [r for r in db.execute('SELECT * FROM translation_profiles') if r[0] in profiles]
        return result

    @staticmethod
    def write_snapshot(db, bid, target):
        connection = sqlite3.connect(target)
        try:
            with connection:
                for table, rows in BookFolders.portable_rows(db, bid).items():
                    schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                    connection.execute(schema)
                    if rows:
                        connection.executemany('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('单书迁移快照校验失败')
        finally:
            connection.close()

    def flush(self, force=False):
        if not force and time.monotonic() - self.last_flush < 10:
            return
        with self.store.lock:
            for bid in list(self.dirty):
                if not self.available(bid):
                    self.dirty.discard(bid)
                    continue
                directory = self.directory(bid)
                target = checked(self.root, directory / 'book.sqlite3')
                temporary = checked(self.root, directory / 'book.sqlite3.tmp')
                temporary.unlink(missing_ok=True)
                self.write_snapshot(self.store.db, bid, temporary)
                temporary.replace(target)
                self.dirty.discard(bid)
            self.last_flush = time.monotonic()

    def import_state(self, directory, bid):
        snapshot = checked(self.root, directory / 'book.sqlite3')
        if not snapshot.is_file():
            raise ValueError('书文件夹缺少 book.sqlite3：' + directory.name)
        source = checked(self.root, directory / 'original.epub')
        connection = sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True)
        try:
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('书文件夹中的数据库损坏')
            data = connection.execute('SELECT id,data,page,updated FROM books').fetchall()
            if len(data) != 1 or data[0][0] != bid or json.loads(data[0][1])['id'] != bid:
                raise ValueError('书文件夹中的书籍信息不一致')
            source_id = json.loads(data[0][1]).get('sourceId', bid)
            if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()[:24] != source_id:
                raise ValueError('书文件夹中的原书与编号不匹配')
            # Do not import SQL, global settings, model credentials or tasks
            # for other books from a copied database.
            incoming = {}
            for table in [*BOOK_TABLES, 'translation_profiles']:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                rows = connection.execute('SELECT * FROM ' + table).fetchall()
                for row in rows:
                    if table in BOOK_TABLES and table not in {'language_cache', 'reproof_jobs', 'language_cache_books'}:
                        owner = row[1] if table == 'translation_revisions' else row[0]
                        if owner != bid: raise ValueError('单书数据库混入了其他书籍')
                    if table == 'language_cache_books' and row[1] != bid:
                        raise ValueError('单书缓存归属不匹配')
                    if table == 'reproof_jobs' and json.loads(row[1]).get('book') != bid:
                        raise ValueError('单书校对任务归属不匹配')
                if table == 'language_cache':
                    edited = []
                    for row in rows:
                        value = json.loads(row[1])
                        if isinstance(value, dict) and value.get('render', {}).get('file'):
                            relative = value['render']['file']
                            path = checked(directory / 'translated-images', directory / relative)
                            value['render']['file'] = path.relative_to(self.root).as_posix()
                        edited.append((row[0], json.dumps(value, ensure_ascii=False), *row[2:]))
                    rows = edited
                if table == 'translation_revisions':
                    rows = [(None, *row[1:]) for row in rows]
                incoming[table] = rows
            with self.store.db:
                for table, rows in incoming.items():
                    if rows:
                        self.store.db.executemany('INSERT OR IGNORE INTO ' + table + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
        finally:
            connection.close()


class LibraryConnection(sqlite3.Connection):
    """Closing a library also finalises every portable book snapshot."""
    def close(self):
        callback = getattr(self, 'before_close', None)
        if callback:
            self.before_close = None
            callback()
        super().close()

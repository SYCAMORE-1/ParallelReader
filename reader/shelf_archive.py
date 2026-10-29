"""Password protected, bounded ZIP volumes and lossless bookshelf merging."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

import pyzipper

from reader.book_folders import BOOK_TABLES, BookFolders, checked, folder_name, image_cache_alias
from reader.library_packages import PART_BYTES, VolumeReader, VolumeWriter, file_hash
from reader.reading_policy import prepare_book

FORMAT = 'parallel-reader-encrypted-shelf-1'
MAX_EXPANDED = 50 * 1024 ** 3


def password_bytes(password):
    if not isinstance(password, str) or not password.strip():
        raise ValueError('请输入你自己的压缩／解压密码。')
    if len(password.encode('utf-8')) > 1024:
        raise ValueError('密码过长，请使用不超过 1024 字节的密码。')
    return password.encode('utf-8')


def canonical(value, bid):
    if isinstance(value, dict):
        return {k: canonical(v, bid) for k, v in value.items()
                if k not in {'created', 'updated', 'sourceId', 'cacheIdentity', 'cacheOrigin'}}
    if isinstance(value, list):
        return [canonical(v, bid) for v in value]
    if isinstance(value, str):
        return value.replace(bid, '@book')
    return value


def content_fingerprint(rows, source_hash, directory):
    """Reading position and task timestamps do not turn one edition into two."""
    book = prepare_book(json.loads(rows['books'][0][1]))
    bid = book['id']
    book.pop('id', None)
    def normalise(value):
        return canonical(canonical(value, book.get('cacheIdentity', bid)), bid)
    material = {'source': source_hash, 'book': normalise(book)}
    material['position'] = rows['books'][0][2]
    material['bookmarks'] = [list(row[1:3]) for row in rows.get('bookmarks', [])]
    material['readingSources'] = sorted([list(row[1:3]) for row in rows.get('reading_sources', [])])
    for table in ('translations', 'translation_drafts', 'translation_details'):
        values = []
        for row in rows.get(table, []):
            value = row[3]
            if table != 'translations':
                value = normalise(json.loads(value))
            values.append([row[1], row[2], value])
        material[table] = sorted(values, key=lambda x: (x[0], x[1]))
    material['revisions'] = sorted([
        [row[2], row[3], row[5], normalise(json.loads(row[6])) if row[6] else None]
        for row in rows.get('translation_revisions', [])], key=lambda x: json.dumps(x, sort_keys=True))
    images = []
    for key, raw, *_ in rows.get('language_cache', []):
        if key.startswith('image-book-job:'):
            continue
        value = json.loads(raw)
        origin = value.get('cacheOrigin', key) if isinstance(value, dict) else key
        relative = value.get('render', {}).get('file') if isinstance(value, dict) else None
        if relative:
            target = checked(directory, directory / 'translated-images' / Path(relative).name)
            value['render']['file'] = Path(relative).name
            value['render']['contentHash'] = file_hash(target) if target.is_file() else 'missing'
        images.append([origin, normalise(value)])
    material['savedCaches'] = sorted(images, key=lambda x: x[0])
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def export_shelf(store, output, password, part_bytes=PART_BYTES, progress=lambda **kw: None):
    secret = password_bytes(password)
    if not 1 <= part_bytes <= PART_BYTES:
        raise ValueError('分卷大小超出限制。')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('归档输出目录必须为空。')
    warnings = []
    with tempfile.TemporaryDirectory(prefix='shelf-snapshot-', dir=output.parent) as temp:
        temp = Path(temp)
        with closing(sqlite3.connect(temp / 'library.sqlite3')) as snapshot:
            with store.lock:
                store.db.backup(snapshot)
                books = [(json.loads(raw), store.book_directory(bid)) for bid, raw in snapshot.execute(
                    'SELECT id,data FROM books WHERE NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id) ORDER BY id')
                    if store.folders.available(bid)]
            if not books:
                raise ValueError('书架为空，请先导入图书。')
            entries, files = [], []
            for index, (book, directory) in enumerate(books):
                progress(message='正在保存：' + book['title'], completed=index, total=len(books))
                source = checked(directory, directory / 'original.epub')
                if not source.is_file() or file_hash(source)[:24] != book.get('sourceId', book['id']):
                    raise ValueError('原书缺失或损坏：' + book['title'])
                prefix = f'books/{index:06d}/'
                target = temp / f'{index:06d}.sqlite3'
                BookFolders.write_snapshot(snapshot, book['id'], target)
                book_files = [('original.epub', source), ('book.sqlite3', target)]
                images = directory / 'translated-images'
                if images.is_dir():
                    for image in sorted(images.iterdir()):
                        checked(images, image)
                        if image.is_file():
                            book_files.append(('translated-images/' + image.name, image))
                records = []
                for relative, path in book_files:
                    record = {'name': prefix + relative, 'bytes': path.stat().st_size, 'sha256': file_hash(path)}
                    records.append(record); files.append((record, path))
                entries.append({'id': book['id'], 'prefix': prefix, 'files': records})
            manifest = {'format': FORMAT, 'books': entries}
            writer = VolumeWriter(output, 'bookshelf-' + datetime.now().strftime('%Y%m%d-%H%M%S'), part_bytes)
            try:
                with pyzipper.AESZipFile(writer, 'w', compression=pyzipper.ZIP_DEFLATED, compresslevel=4,
                                         encryption=pyzipper.WZ_AES, encryption_kwargs={'nbits': 256}) as archive:
                    archive.setpassword(secret)
                    archive.writestr('shelf.json', json.dumps(manifest, ensure_ascii=False).encode())
                    for index, (record, path) in enumerate(files):
                        progress(message='正在加密打包', completed=index, total=len(files))
                        archive.write(path, record['name'])
                parts = writer.finalise()
            finally:
                writer.close()
    progress(message='打包完成', completed=len(books), total=len(books))
    return {'books': len(books), 'parts': parts, 'warnings': warnings}


def ordered_volumes(paths):
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError('请选择 ZIP 或它的全部分卷。')
    if len(paths) == 1 and paths[0].name.lower().endswith('.zip'):
        return paths
    parsed = [re.fullmatch(r'(.+\.zip)\.(\d{3,5})', p.name, re.I) for p in paths]
    if any(m is None for m in parsed) or len({m[1].casefold() for m in parsed}) != 1:
        raise ValueError('一次请选择同一个归档的全部分卷（.zip.0001、.0002…）。')
    pairs = sorted((int(m[2]), p) for m, p in zip(parsed, paths))
    if [number for number, _ in pairs] != list(range(1, len(paths) + 1)):
        raise ValueError('分卷不完整或重复，请从第一卷开始选择全部分卷。')
    return [p for _, p in pairs]


def snapshot_rows(store, path, expected):
    """Read known columns only; never execute a transferred schema or settings."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('归档中的单书数据库损坏。')
        rows = {}
        for table in [*BOOK_TABLES, 'translation_profiles']:
            kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
            if not kind:
                rows[table] = []
                continue
            if kind[0] != 'table':
                raise ValueError('单书数据库结构无效。')
            columns = [r[1] for r in store.db.execute('PRAGMA table_info(' + table + ')')]
            rows[table] = db.execute('SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM ' + table).fetchall()
        if len(rows['books']) != 1 or rows['books'][0][0] != expected:
            raise ValueError('单书数据库编号不匹配。')
        book = prepare_book(json.loads(rows['books'][0][1]))
        if book['id'] != expected or not re.fullmatch('[a-f0-9]{24}', expected) or not book.get('pages') or not book.get('paragraphs'):
            raise ValueError('归档中的书籍信息无效。')
        owners = {r[0] for r in rows.get('language_cache_books', []) if r[1] == expected}
        for table, values in rows.items():
            for row in values:
                if table in BOOK_TABLES and table not in {'language_cache', 'language_cache_books', 'reproof_jobs'}:
                    if row[1 if table == 'translation_revisions' else 0] != expected:
                        raise ValueError('单书数据库混入了其他书籍。')
                if table == 'language_cache_books' and row[1] != expected:
                    raise ValueError('缓存归属不匹配。')
                if table == 'language_cache' and row[0] not in owners:
                    raise ValueError('缓存归属缺失。')
                if table == 'reproof_jobs' and json.loads(row[1]).get('book') != expected:
                    raise ValueError('校对任务归属不匹配。')
        return rows, book


def remap_rows(rows, book, new_id, directory, root):
    old = book['id']
    book = {**book, 'id': new_id, 'sourceId': book.get('sourceId', old)}
    if new_id != old:
        book['cacheIdentity'] = book.get('cacheIdentity', old)
    job_ids = {r[0]: hashlib.sha256((new_id + r[0]).encode()).hexdigest()[:32] for r in rows.get('reproof_jobs', [])}
    keys = {}
    values = {}
    for key, raw, *_ in rows.get('language_cache', []):
        value = json.loads(raw)
        origin = value.get('cacheOrigin', key) if isinstance(value, dict) else key
        if key.startswith('image-translation:'):
            suffix = ':draft' if origin.endswith(':draft') else ':reviewed' if origin.endswith(':reviewed') else ''
            target = image_cache_alias(new_id, origin.removesuffix(suffix) if suffix else origin) + suffix
        else:
            target = 'imported:' + hashlib.sha256((new_id + origin).encode()).hexdigest()
        keys[key] = target if new_id != old else key
        if isinstance(value, dict):
            value = dict(value)
            value['cacheOrigin'] = origin
            if value.get('book') == old: value['book'] = new_id
            if isinstance(value.get('imageUrl'), str): value['imageUrl'] = value['imageUrl'].replace('/' + old + '/', '/' + new_id + '/')
            if value.get('state') in {'running', 'queued'}: value.update(state='paused', reason='恢复的任务已暂停。')
            if value.get('render', {}).get('file'):
                name = Path(value['render']['file']).name
                if '/' in name or '\\' in name: raise ValueError('译图文件名无效。')
                value['render'] = {**value['render'], 'file': (directory / 'translated-images' / name).relative_to(root).as_posix()}
        values[key] = json.dumps(value, ensure_ascii=False)
    result = {}
    for table, records in rows.items():
        edited = []
        for record in records:
            row = list(record)
            if table == 'books': row[:2] = [new_id, json.dumps(book, ensure_ascii=False)]
            elif table == 'translation_revisions':
                row[0], row[1], row[4] = None, new_id, job_ids.get(row[4], row[4])
            elif table == 'reproof_jobs':
                value = json.loads(row[1]); value['book'] = new_id
                for key in ('id', 'parent'):
                    if key in value: value[key] = job_ids.get(value[key], value[key])
                if 'children' in value: value['children'] = [job_ids.get(x, x) for x in value['children']]
                if value.get('state') in {'running', 'queued'}: value.update(state='paused', reason='恢复的任务已暂停。')
                row = [job_ids[row[0]], json.dumps(value, ensure_ascii=False)]
            elif table == 'language_cache': row[:2] = [keys[row[0]], values[row[0]]]
            elif table == 'language_cache_books': row[:2] = [keys.get(row[0], row[0]), new_id]
            elif table in BOOK_TABLES:
                row[0] = new_id
                if table == 'book_translation_jobs' and row[3] in {'running', 'queued'}:
                    row[3], row[4] = 'paused', '恢复的任务已暂停。'
            edited.append(tuple(row))
        result[table] = edited
    return result, book


def import_shelf(store, paths, password, progress=lambda **kw: None):
    secret = password_bytes(password)
    paths = ordered_volumes(paths)
    with tempfile.TemporaryDirectory(prefix='shelf-restore-', dir=store.directory) as temp:
        temp = Path(temp)
        try:
            with VolumeReader(paths) as volumes, pyzipper.AESZipFile(volumes) as archive:
                archive.setpassword(secret)
                info = archive.getinfo('shelf.json')
                if info.file_size > 16 * 1024 ** 2 or not info.flag_bits & 1:
                    raise ValueError('请选择本阅读器导出的加密书架归档。')
                manifest = json.loads(archive.read('shelf.json'))
                if manifest.get('format') != FORMAT or not isinstance(manifest.get('books'), list) or not manifest['books']:
                    raise ValueError('不支持此书架归档格式。')
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValueError('归档含有重复路径。')
                expected, size = {'shelf.json'}, 0
                incoming = []
                ids = set()
                for index, entry in enumerate(manifest['books']):
                    prefix = f'books/{index:06d}/'
                    if entry.get('prefix') != prefix or entry.get('id') in ids:
                        raise ValueError('归档的书籍目录无效。')
                    ids.add(entry['id'])
                    directory = temp / f'{index:06d}'; directory.mkdir()
                    for record in entry['files']:
                        name = record['name']
                        relative = name.removeprefix(prefix)
                        if name == relative or not (relative in {'original.epub', 'book.sqlite3'} or re.fullmatch(r'translated-images/[^/\\:]+', relative)) or '..' in relative.split('/'):
                            raise ValueError('归档包含无效路径。')
                        if name in expected: raise ValueError('归档含重复文件。')
                        expected.add(name)
                        info = archive.getinfo(name)
                        size += info.file_size
                        if not info.flag_bits & 1 or info.file_size != record['bytes'] or size > MAX_EXPANDED:
                            raise ValueError('归档大小或加密校验失败。')
                        target = checked(directory, directory / relative); target.parent.mkdir(exist_ok=True)
                        digest = hashlib.sha256()
                        with archive.open(info) as source, target.open('xb') as output:
                            for block in iter(lambda: source.read(1024 * 1024), b''):
                                digest.update(block); output.write(block)
                        if digest.hexdigest() != record['sha256']:
                            raise ValueError('归档校验失败，文件可能不完整。')
                    with store.lock:
                        rows, book = snapshot_rows(store, directory / 'book.sqlite3', entry['id'])
                    source_hash = file_hash(directory / 'original.epub')
                    if source_hash[:24] != book.get('sourceId', book['id']):
                        raise ValueError('原书与书籍记录不匹配。')
                    fingerprint = content_fingerprint(rows, source_hash, directory)
                    incoming.append((directory, rows, book, source_hash, fingerprint))
                    progress(message='已校验：' + book['title'], completed=index + 1, total=len(manifest['books']))
                if set(names) != expected:
                    raise ValueError('归档含有未声明的文件。')
        except (RuntimeError, pyzipper.BadZipFile, EOFError) as exc:
            raise ValueError('解压失败：密码错误，或分卷缺失／损坏。请选择全部分卷后重试。') from exc
        except KeyError as exc:
            raise ValueError('归档缺少必要文件，请选择全部分卷。') from exc
        restored, skipped, created = [], [], []
        with store.lock:
            local = {}
            for bid, raw in store.db.execute('SELECT id,data FROM books').fetchall():
                if not store.folders.available(bid): continue
                book = json.loads(raw); directory = store.book_directory(book)
                source = directory / 'original.epub'
                if not source.is_file(): continue
                source_hash = file_hash(source)
                fingerprint = content_fingerprint(BookFolders.portable_rows(store.db, bid), source_hash, directory)
                local.setdefault(source_hash, {})[fingerprint] = bid
            planned = []
            used = {row[0] for row in store.db.execute('SELECT id FROM books')}
            for staged, rows, book, source_hash, fingerprint in incoming:
                duplicate = local.get(source_hash, {}).get(fingerprint)
                if duplicate:
                    skipped.append({'id': duplicate, 'title': book['title']})
                    continue
                bid = book['id']
                if bid in used:
                    bid = hashlib.sha256((source_hash + fingerprint).encode()).hexdigest()[:24]
                    while bid in used: bid = uuid.uuid4().hex[:24]
                directory = checked(store.directory / 'books', store.directory / 'books' / folder_name({**book, 'id': bid}))
                if directory.exists(): raise ValueError('目标书籍文件夹已存在，未覆盖：' + directory.name)
                mapped, book = remap_rows(rows, book, bid, directory, store.directory)
                planned.append((staged, directory, mapped, book))
                used.add(bid); local.setdefault(source_hash, {})[fingerprint] = bid
            try:
                # Validate every book before changing the live shelf. One transaction
                # commits all database rows; directory copies are removed on failure.
                with store.db:
                    for staged, directory, rows, book in planned:
                        created.append(directory); shutil.copytree(staged, directory)
                        (directory / 'book.json').write_text(json.dumps({'format': 1, 'id': book['id'], 'title': book['title'], 'author': book.get('author', '')}, ensure_ascii=False), encoding='utf-8')
                        for table, records in rows.items():
                            if records:
                                store.db.executemany('INSERT OR IGNORE INTO ' + table + ' VALUES (' + ','.join('?' for _ in records[0]) + ')', records)
                        store.db.execute('INSERT INTO book_locations VALUES (?,?)', (book['id'], directory.name))
                        snapshot = directory / 'book.sqlite3.tmp'
                        BookFolders.write_snapshot(store.db, book['id'], snapshot)
                        snapshot.replace(directory / 'book.sqlite3')
                        restored.append({'id': book['id'], 'title': book['title']})
                    for item in skipped:
                        store.db.execute('DELETE FROM removed_books WHERE book=?', (item['id'],))
            except Exception:
                for directory in created:
                    checked(store.directory / 'books', directory)
                    shutil.rmtree(directory)
                raise
            for _, directory, _, book in planned:
                store.folders.paths[book['id']] = directory
                store.touch_book(book['id'])
        return {'restoredBooks': len(restored), 'skippedBooks': len(skipped), 'books': restored, 'skipped': skipped}


class ArchiveJobs:
    def __init__(self, store):
        self.store = store
        self.root = store.directory.parent / 'private-transfer' / 'shelf-archives'
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock, self.jobs = threading.RLock(), {}
        self.closed = False
        self.threads = []

    def _new(self, kind):
        with self.lock:
            if self.closed: raise ValueError('阅读器正在关闭。')
            if any(j['state'] in {'working', 'uploading'} for j in self.jobs.values()):
                raise ValueError('已有打包或恢复任务正在进行，请等待完成。')
            identity = uuid.uuid4().hex
            directory = checked(self.root, self.root / identity); directory.mkdir()
            self.jobs[identity] = {'id': identity, 'kind': kind, 'state': 'uploading' if kind == 'import' else 'working', 'message': '准备中', 'completed': 0, 'total': 0, 'parts': [], 'created': time.time()}
            return identity, directory

    def status(self, identity):
        with self.lock:
            if identity not in self.jobs: raise ValueError('找不到归档任务，请重新操作。')
            return dict(self.jobs[identity])

    def _update(self, identity, **values):
        with self.lock: self.jobs[identity].update(values)

    def _run(self, identity, callback):
        def work():
            try:
                result = callback(lambda **kw: self._update(identity, **kw))
                if 'parts' in result:
                    for part in result['parts']: part['url'] = '/api/archive/files/' + identity + '/' + part['file']
                self._update(identity, state='completed', message='已完成', **result)
            except Exception as exc:
                # Never expose a worker's arguments or passwords in diagnostics.
                self._update(identity, state='failed', error=str(exc)[:350], message='操作失败')
        thread = threading.Thread(target=work, name='shelf-archive', daemon=True)
        self.threads.append(thread); thread.start()
        return self.status(identity)

    def export(self, password, confirmation):
        password_bytes(password)
        if password != confirmation: raise ValueError('两次输入的密码不一致。')
        identity, directory = self._new('export')
        return self._run(identity, lambda update: export_shelf(self.store, directory, password, progress=update))

    def start_import(self):
        identity, _ = self._new('import')
        return self.status(identity)

    def upload(self, identity, filename, stream, length):
        with self.lock:
            job = self.status(identity)
            if job['kind'] != 'import' or job['state'] != 'uploading': raise ValueError('上传任务已结束。')
            if not re.fullmatch(r'[^/\\:\x00-\x1f]{1,180}\.zip(?:\.\d{3,5})?', filename, re.I): raise ValueError('请选择 ZIP 或 ZIP 分卷。')
            if not 0 < length <= 2 * 1024 ** 3: raise ValueError('分卷为空或超过 2 GiB，请重新打包为较小分卷。')
            path = checked(self.root, self.root / identity / filename)
            with path.open('xb') as output:
                remaining = length
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block: raise ValueError('上传中断，请重新导入。')
                    output.write(block); remaining -= len(block)
            self.jobs[identity]['parts'].append({'file': filename, 'bytes': length})
            return {'ok': True}

    def finish_import(self, identity, password):
        password_bytes(password)
        with self.lock:
            job = self.status(identity)
            if job['kind'] != 'import' or job['state'] != 'uploading': raise ValueError('上传任务已结束。')
            paths = ordered_volumes([self.root / identity / p['file'] for p in job['parts']])
            self._update(identity, state='working', message='正在校验并恢复')
        def restore(update):
            try: return import_shelf(self.store, paths, password, progress=update)
            finally:
                directory = checked(self.root, self.root / identity)
                shutil.rmtree(directory)
        return self._run(identity, restore)

    def cancel(self, identity):
        with self.lock:
            job = self.status(identity)
            if job['state'] != 'uploading': raise ValueError('任务已开始，请等待完成。')
            shutil.rmtree(checked(self.root, self.root / identity))
            self._update(identity, state='cancelled')
        return {'ok': True}

    def download(self, identity, filename):
        job = self.status(identity)
        if job['kind'] != 'export' or job['state'] != 'completed' or filename not in {p['file'] for p in job['parts']}:
            raise ValueError('归档尚未完成或文件不存在。')
        return checked(self.root, self.root / identity / filename)

    def close(self):
        self.closed = True
        for thread in self.threads: thread.join()

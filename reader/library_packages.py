"""Independent book ZIPs, bounded volumes, and verified selective restoration."""
import bisect
from datetime import datetime, timezone
import hashlib
import io
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from reader.book_folders import BookFolders, checked, folder_name
from reader.library_transfer import snapshot_library

MANIFEST = 'library-manifest.json'
PART_BYTES = 500 * 1024 * 1024


def fs_path(path):
    """Use Windows extended paths for deep staging directories and long titles."""
    value = str(Path(path).absolute())
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return Path(value)
    return Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)


def plain_path(path):
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'): value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'): value = value[4:]
    return Path(value)


def file_hash(path):
    digest = hashlib.sha256()
    with fs_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class VolumeWriter(io.RawIOBase):
    def __init__(self, directory, stem, limit):
        super().__init__()
        self.directory, self.stem, self.limit = Path(directory), stem, limit
        self.position, self.stream, self.parts = 0, None, []

    def writable(self): return True
    def seekable(self): return False
    def tell(self): return self.position

    def write(self, data):
        data = memoryview(data)
        total = len(data)
        while data:
            if self.stream is None:
                self.path = self.directory / (self.stem + '.part-' + str(len(self.parts) + 1))
                self.stream = self.path.open('xb')
                self.used, self.digest = 0, hashlib.sha256()
            count = min(len(data), self.limit - self.used)
            self.stream.write(data[:count]); self.digest.update(data[:count])
            self.used += count; self.position += count; data = data[count:]
            if self.used == self.limit: self.finish_part()
        return total

    def flush(self):
        if self.stream is not None: self.stream.flush()

    def finish_part(self):
        if self.stream is None: return
        self.stream.close(); self.stream = None
        self.parts.append({'file': self.path.name, 'bytes': self.used, 'sha256': self.digest.hexdigest()})

    def close(self):
        if not self.closed:
            self.finish_part()
            super().close()

    def finalise(self):
        self.close()
        for i, part in enumerate(self.parts, 1):
            name = self.stem + '.zip' + (f'.{i:04d}' if len(self.parts) > 1 else '')
            (self.directory / part['file']).rename(self.directory / name)
            part['file'] = name
        return self.parts


class VolumeReader(io.RawIOBase):
    def __init__(self, paths):
        super().__init__()
        self.paths = [Path(path) for path in paths]
        self.ends, self.position, self.stream, self.index = [], 0, None, -1
        for path in self.paths:
            self.ends.append((self.ends[-1] if self.ends else 0) + path.stat().st_size)
        self.size = self.ends[-1] if self.ends else 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        if whence not in (0, 1, 2): raise ValueError('Invalid seek mode')
        position = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if position < 0: raise ValueError('Negative seek')
        self.position = position
        return position

    def read(self, size=-1):
        remaining = max(0, self.size - self.position)
        size = remaining if size is None or size < 0 else min(size, remaining)
        result = []
        while size:
            index = bisect.bisect_right(self.ends, self.position)
            start = self.ends[index - 1] if index else 0
            if index != self.index:
                if self.stream is not None: self.stream.close()
                self.stream = self.paths[index].open('rb'); self.index = index
            self.stream.seek(self.position - start)
            block = self.stream.read(min(size, self.ends[index] - self.position))
            if not block: raise EOFError('分卷文件被截断')
            result.append(block); size -= len(block); self.position += len(block)
        return b''.join(result)

    def close(self):
        if self.stream is not None: self.stream.close()
        super().close()


def select_books(books, selectors):
    if not selectors: return books
    chosen = set()
    for selector in selectors:
        matches = [book for book in books if selector == book['id'] or selector.casefold() == book['title'].casefold()]
        if not matches: raise ValueError('找不到所选书籍：' + selector)
        chosen.update(book['id'] for book in matches)
    return [book for book in books if book['id'] in chosen]


def zip_info(name):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def package_library(source, output, max_part_bytes=PART_BYTES, books=None, include_removed=False, progress=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not 1 <= max_part_bytes < 2 * 1024 ** 3:
        raise ValueError('每卷大小必须大于零并小于 2 GiB')
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('打包输出必须放在原书库以外的独立目录')
    if output.exists(): raise FileExistsError('输出目录已存在，不会覆盖：' + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    warnings, entries = [], []
    # The SQLite backup includes committed WAL data. Assets have immutable
    # content-addressed names, so the original reader can keep running.
    with tempfile.TemporaryDirectory(prefix='book-pack-', dir=output.parent) as temporary:
        staging = checked(output.parent, Path(temporary))
        snapshot_library(source, staging)
        db = sqlite3.connect((staging / 'reader.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            query = 'SELECT data FROM books'
            if not include_removed:
                query += ' WHERE NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id)'
            selected = select_books([json.loads(row[0]) for row in db.execute(query + " ORDER BY json_extract(data,'$.title')")], books)
            if not selected: raise ValueError('没有可打包的书籍')
            packed = staging / 'packages'; packed.mkdir()
            for book in selected:
                bid, label = book['id'], folder_name(book)
                location = db.execute('SELECT folder FROM book_locations WHERE book=?', (bid,)).fetchone()
                if not location: raise ValueError('请先用当前阅读器整理旧版书库')
                directory = checked(source / 'books', source / 'books' / location[0])
                if not directory.is_dir() and not books:
                    continue  # A moved-out book is absent from the current shelf.
                original = checked(directory, directory / 'original.epub')
                if not original.is_file() or file_hash(original)[:24] != book.get('sourceId', bid):
                    raise ValueError('原书缺失或校验不符：' + book['title'])
                snapshot = staging / (bid + '.sqlite3')
                BookFolders.write_snapshot(db, bid, snapshot)
                metadata = json.dumps({'format': 1, 'id': bid, 'title': book['title'], 'author': book.get('author', '')}, ensure_ascii=False).encode('utf-8')
                prefix = 'books/' + label + '/'
                resources = [(original, 'original.epub'), (snapshot, 'book.sqlite3')]
                images = directory / 'translated-images'
                if images.exists():
                    checked(directory, images)
                    for image in sorted(images.iterdir()):
                        checked(images, image)
                        if image.is_file() and image.suffix.lower() == '.png':
                            resources.append((image, 'translated-images/' + image.name))
                for raw, in db.execute("SELECT value FROM language_cache WHERE key IN (SELECT key FROM language_cache_books WHERE book=?)", (bid,)):
                    value = json.loads(raw)
                    relative = value.get('render', {}).get('file') if isinstance(value, dict) else None
                    if relative and not checked(source, source / relative).is_file():
                        warnings.append({'book': bid, 'missingHistoricalImage': Path(relative).name})
                writer = VolumeWriter(packed, label, max_part_bytes)
                try:
                    with zipfile.ZipFile(writer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                        archive.writestr(zip_info(prefix + 'book.json'), metadata)
                        for path, relative in resources:
                            info = zip_info(prefix + relative); info.file_size = path.stat().st_size
                            if path.suffix.lower() in {'.epub', '.png'}:
                                info.compress_type = zipfile.ZIP_STORED
                            with path.open('rb') as source_file, archive.open(info, 'w', force_zip64=True) as output_file:
                                shutil.copyfileobj(source_file, output_file, 1024 * 1024)
                    parts = writer.finalise()
                finally: writer.close()
                entry = {'id': bid, 'title': book['title'], 'folder': label, 'parts': parts,
                         'uncompressedBytes': len(metadata) + sum(path.stat().st_size for path, _ in resources),
                         'translations': db.execute('SELECT count(*) FROM translations WHERE book=?', (bid,)).fetchone()[0]}
                entries.append(entry)
                if progress: progress(entry)
            if not entries: raise ValueError('没有找到可打包的书文件夹')
            manifest = {'format': 'parallel-reader-book-packages', 'version': 1,
                        'createdAt': datetime.now(timezone.utc).isoformat(), 'maxPartBytes': max_part_bytes,
                        'includesModelSettings': False, 'books': entries, 'warnings': warnings}
            write_restore_tools(packed)
            (packed / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            instructions = ('私人书库迁移包\n\n每本书独立打包，不含导出缓存、总数据库、日志、备份或模型配置。\n'
                '普通 .zip 可单独解压到阅读器的 .data/ 目录，再启动即可识别。\n'
                '分卷 .zip.0001 等必须收齐该书全部卷，并保留 library-manifest.json。\n'
                '将下载的包与清单放在同一目录，先关闭目标阅读器，再把目录拖到 restore-books.bat。\n'
                '旧版程序没有 restore-books.bat 时，把阅读器程序文件夹拖到本包自带的 restore-packages.bat。\n'
                '命令行：runtime\\python.exe tools\\restore_library_packages.py --packages "包目录" --target .data\n'
                '也可用 --book "完整书名或编号" 只恢复选中的书。未下载的整本书自动跳过，缺少部分分卷会报错。\n'
                '已有同一本书时不会覆盖；请先迁出旧书或恢复到一个新的 .data/，以免覆盖已有进度。\n'
                '这是私人图书数据，请使用私人存储或私有仓库的 Release；不要混入公开程序 Release。\n')
            (packed / '使用说明.txt').write_text(instructions, encoding='utf-8-sig')
            packed.rename(output)
        finally: db.close()
    return manifest


def checked_parts(packages, entry):
    paths = []
    if not entry.get('parts'): raise ValueError('清单缺少分卷信息')
    for part in entry['parts']:
        name = part['file']
        if Path(name).name != name or '/' in name or '\\' in name or name in {'.', '..'}:
            raise ValueError('分卷文件名越界')
        path = checked(packages, packages / name)
        if not path.is_file(): raise FileNotFoundError('缺少分卷：' + name)
        if path.stat().st_size != part['bytes'] or file_hash(path) != part['sha256']:
            raise ValueError('分卷校验失败：' + name)
        paths.append(path)
    return paths


def restore_library(packages, target, books=None):
    packages, target = Path(packages), Path(target)
    if packages.is_file(): packages = packages.parent
    packages = packages.resolve()
    if target.is_symlink(): raise ValueError('目标书库不能是链接')
    target = target.resolve()
    manifest = json.loads((packages / MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('format') != 'parallel-reader-book-packages' or manifest.get('version') != 1:
        raise ValueError('不支持的迁移包清单')
    selected = select_books(manifest['books'], books)
    if not books:
        selected = [entry for entry in selected if any((packages / part['file']).is_file() for part in entry['parts'])]
    if not selected: raise ValueError('没有找到已下载的完整书包')
    names, ids, plans = set(), set(), []
    existing = set()
    database = target / 'reader.sqlite3'
    if database.is_file():
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try: existing = {row[0] for row in connection.execute('SELECT id FROM books')}
        finally: connection.close()
    for entry in selected:
        label, bid = entry['folder'], entry['id']
        if Path(label).name != label or label in {'.', '..'} or '/' in label or '\\' in label:
            raise ValueError('书文件夹名称越界')
        if label.casefold() in names or bid in ids: raise ValueError('清单包含重复书籍')
        names.add(label.casefold()); ids.add(bid)
        destination = checked(target, target / 'books' / label)
        if destination.exists() or bid in existing:
            raise FileExistsError('目标已有此书，不会覆盖：' + entry['title'])
        plans.append((entry, checked_parts(packages, entry)))
    target.parent.mkdir(parents=True, exist_ok=True)
    required = sum(entry['uncompressedBytes'] for entry, _ in plans)
    if shutil.disk_usage(target.parent).free < required: raise OSError('恢复书库的磁盘空间不足')
    with tempfile.TemporaryDirectory(prefix='r-', dir=fs_path(target.parent)) as temporary:
        staging = checked(target.parent, plain_path(temporary))
        for entry, parts in plans:
            prefix = ('books', entry['folder'])
            with VolumeReader(parts) as reader, zipfile.ZipFile(reader) as archive:
                members = set(); total = 0
                for member in archive.infolist():
                    path = PurePosixPath(member.filename)
                    if ('\\' in member.filename or path.is_absolute() or '..' in path.parts or ':' in member.filename
                            or path.parts[:2] != prefix or member.is_dir()
                            or stat.S_ISLNK(member.external_attr >> 16)):
                        raise ValueError('压缩包包含越界或不支持的路径')
                    relative = path.parts[2:]
                    if not (relative in [('book.json',), ('book.sqlite3',), ('original.epub',)]
                            or len(relative) == 2 and relative[0] == 'translated-images' and relative[1].endswith('.png')):
                        raise ValueError('压缩包包含非书库资源')
                    canonical = member.filename.casefold()
                    if canonical in members: raise ValueError('压缩包包含重复路径')
                    members.add(canonical); total += member.file_size
                    if total > entry['uncompressedBytes']: raise ValueError('压缩包体积与清单不符')
                    destination = checked(staging, staging.joinpath(*path.parts))
                    fs_path(destination.parent).mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, fs_path(destination).open('xb') as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                if total != entry['uncompressedBytes']: raise ValueError('书包不完整')
            directory = staging / 'books' / entry['folder']
            info = json.loads((directory / 'book.json').read_text(encoding='utf-8'))
            if info.get('format') != 1 or info.get('id') != entry['id']:
                raise ValueError('书籍身份与清单不符')
            connection = sqlite3.connect((directory / 'book.sqlite3').as_uri() + '?mode=ro', uri=True)
            try:
                book = json.loads(connection.execute('SELECT data FROM books').fetchone()[0])
                if file_hash(directory / 'original.epub')[:24] != book.get('sourceId', entry['id']):
                    raise ValueError('原书校验失败')
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('单书数据库损坏')
                if connection.execute('SELECT id FROM books').fetchall() != [(entry['id'],)]: raise ValueError('单书数据库身份不符')
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name='settings'").fetchone(): raise ValueError('书包包含全局配置')
            finally: connection.close()
        destination_books = checked(target, target / 'books')
        fs_path(destination_books).mkdir(parents=True, exist_ok=True)
        moved = []
        try:
            for entry, _ in plans:
                source = staging / 'books' / entry['folder']
                destination = checked(target, destination_books / entry['folder'])
                if fs_path(destination).exists(): raise FileExistsError('目标目录已存在')
                fs_path(source).rename(fs_path(destination)); moved.append((source, destination))
        except Exception:
            for source, destination in reversed(moved): fs_path(destination).rename(fs_path(source))
            raise
    return {'restoredBooks': len(plans), 'books': [{'id': entry['id'], 'title': entry['title']} for entry, _ in plans],
            'target': str(target), 'existingDataOverwritten': False}


def write_restore_tools(directory):
    """Ship a standard-library-only restorer that also works with older releases."""
    imports = ('import bisect, hashlib, io, json, os, shutil, sqlite3, stat, tempfile, zipfile\n'
               'from pathlib import Path, PurePosixPath\nMANIFEST = "library-manifest.json"\n\n')
    functions = [checked, fs_path, plain_path, file_hash, VolumeReader, select_books, checked_parts, restore_library]
    source = imports + '\n\n'.join(inspect.getsource(function) for function in functions)
    source += '''

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Verify and restore private book packages')
    parser.add_argument('--packages', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--book', action='append')
    args = parser.parse_args()
    print(json.dumps(restore_library(args.packages, args.target, args.book), ensure_ascii=False))
'''
    (directory / 'restore-packages.py').write_text(source, encoding='utf-8')
    batch = ('@echo off\r\n'
             'if not exist "%~1\\runtime\\python.exe" (\r\n'
             '  echo Close the reader, then drag its program folder onto this file.\r\n'
             '  pause\r\n  exit /b 1\r\n)\r\n'
             '"%~1\\runtime\\python.exe" -X utf8 "%~dp0restore-packages.py" --packages "%~dp0." --target "%~1\\.data"\r\npause\r\n')
    (directory / 'restore-packages.bat').write_bytes(batch.encode('ascii'))

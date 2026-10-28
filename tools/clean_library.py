"""Remove obsolete generated files; keep books, saved versions and reading state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.storage import migrate_settings

TABLE_KEYS = {'books': 'id', 'translations': 'book,paragraph,profile', 'translation_details': 'book,paragraph,profile',
              'bookmarks': 'book', 'settings': 'key', 'translation_drafts': 'book,paragraph,profile',
              'language_cache': 'key', 'reproof_jobs': 'id', 'book_translation_jobs': 'book,profile',
              'book_translation_failures': 'book,profile,paragraph', 'removed_books': 'book',
              'translation_profiles': 'profile', 'reading_sources': 'book,configuration'}


def fingerprint(db):
    result = {}
    for table, key in TABLE_KEYS.items():
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(): continue
        digest = hashlib.sha256(); count = 0
        for row in db.execute('SELECT * FROM ' + table + ' ORDER BY ' + key):
            digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode()); count += 1
        result[table] = (count, digest.hexdigest())
    return result


def safe_file(root, path):
    path = Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Refusing to remove a linked or out-of-library file')
    return path


def obsolete_files(root, db):
    trash, referenced = [], set()
    for (raw,) in db.execute("SELECT value FROM language_cache WHERE key LIKE 'image-translation:%'"):
        value = json.loads(raw)
        name = value.get('render', {}).get('file') if isinstance(value, dict) else None
        if name: referenced.add((root / name.replace('\\', '/')).resolve())
    images = root / 'translated-images'
    for path in images.glob('*'):
        safe_file(root, path)
        if path.is_file() and path.resolve() not in referenced: trash.append(path)
    if any(not path.is_file() for path in referenced):
        raise ValueError('A saved translated image is missing; cleanup stopped')
    books = {row[0] for row in db.execute('SELECT id FROM books')}
    groups = {}
    exports = root / 'exports'
    for path in exports.glob('*.epub'):
        safe_file(root, path)
        match = re.fullmatch(r'([a-f0-9]{24})-[a-f0-9]{20}-(bilingual|translation)\.epub', path.name)
        if match and match[1] in books: groups.setdefault((match[1], match[2]), []).append(path)
    for values in groups.values():
        for path in sorted(values, key=lambda value: (value.stat().st_mtime_ns, value.name), reverse=True)[1:]:
            trash.append(path)
            stamp = path.with_suffix('.json')
            if stamp.is_file(): trash.append(safe_file(root, stamp))
    return list(dict.fromkeys(trash))


def clean_library(directory, apply=False, prune_history=False):
    root = Path(directory).resolve()
    database = root / 'reader.sqlite3'
    if not database.is_file() or not (root / 'sources').is_dir():
        raise ValueError('Not a reader library directory')
    marker = root / 'maintenance.lock'; backup = root / '.maintenance-backup.sqlite3'
    if marker.exists() or backup.exists(): raise ValueError('An earlier maintenance operation needs inspection')
    db = sqlite3.connect(database, timeout=2)
    created_backup = verified = marked = False
    before_bytes = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
    try:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('Database is not healthy')
        for (book_id,) in db.execute('SELECT id FROM books'):
            source = safe_file(root, root / 'sources' / (book_id + '.epub'))
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()[:24] != book_id:
                raise ValueError('An original EPUB is missing or damaged')
        trash = obsolete_files(root, db)
        history = db.execute('SELECT COUNT(*) FROM translation_revisions').fetchone()[0]
        report = {'apply': apply, 'beforeBytes': before_bytes, 'obsoleteFiles': len(trash),
                  'obsoleteFileBytes': sum(path.stat().st_size for path in trash), 'oldRevisionRows': history if prune_history else 0}
        if not apply: return report
        with marker.open('x', encoding='ascii') as stream: stream.write('Library maintenance in progress')
        marked = True
        try:
            if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
                raise RuntimeError('Close the reader before cleaning the library')
        except sqlite3.OperationalError as exc:
            raise RuntimeError('Close the reader before cleaning the library') from exc
        target = sqlite3.connect(backup)
        try: db.backup(target)
        finally: target.close()
        created_backup = True
        with db:
            row = db.execute("SELECT value FROM settings WHERE key='model'").fetchone()
            if row:
                value = migrate_settings(json.loads(row[0]))
                db.execute("UPDATE settings SET value=? WHERE key='model'", (json.dumps(value, ensure_ascii=False),))
        baseline = fingerprint(db)
        with db:
            if prune_history: db.execute('DELETE FROM translation_revisions')
        db.execute('VACUUM')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or fingerprint(db) != baseline:
            raise RuntimeError('Verification failed; maintenance backup retained')
        db.execute('PRAGMA journal_mode=WAL')
        verified = True
        for path in trash: safe_file(root, path).unlink()
        report['preservedRows'] = {name: value[0] for name, value in baseline.items()}
    finally:
        db.close()
        if apply:
            if created_backup and verified: safe_file(root, backup).unlink(missing_ok=True)
            if marked: marker.unlink(missing_ok=True)
    report['afterBytes'] = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
    report['reclaimedBytes'] = before_bytes - report['afterBytes']
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / '.data')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--prune-history', action='store_true', help='Discard previous proofreading snapshots, retaining current saved versions')
    args = parser.parse_args()
    print(json.dumps(clean_library(args.data, args.apply, args.prune_history), ensure_ascii=False))

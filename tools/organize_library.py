"""Preview or migrate the private library to self-contained book-name folders."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.book_folders import folder_name
from reader.storage import Store


def organise(directory, apply=False):
    directory = Path(directory).resolve()
    database = directory / 'reader.sqlite3'
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        books = [json.loads(raw) for raw, in connection.execute('SELECT data FROM books')]
    finally:
        connection.close()
    result = {'apply': apply, 'books': [{'id': book['id'], 'title': book['title'], 'folder': folder_name(book)} for book in books]}
    if not apply:
        return result
    connection = sqlite3.connect(database, timeout=2)
    try:
        try:
            connection.execute('PRAGMA journal_mode=DELETE')
        except sqlite3.OperationalError as exc:
            raise RuntimeError('请先关闭阅读器，再整理书库') from exc
        connection.execute('PRAGMA journal_mode=WAL')
        backups = directory / 'backups'
        backups.mkdir(exist_ok=True)
        backup = backups / ('before-book-folders-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
        target = sqlite3.connect(backup)
        try: connection.backup(target)
        finally: target.close()
    finally:
        connection.close()
    store = Store(directory)
    try:
        result.update(backup=str(backup), warnings=store.folders.warnings,
                      folders=[str(store.book_directory(book)) for book in books if store.folders.available(book['id'])])
    finally:
        store.db.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=ROOT / '.data')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(organise(args.data, args.apply), ensure_ascii=False), flush=True)

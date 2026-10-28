"""Upgrade existing books from their original EPUBs, preserving cached translations."""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.epub import parse_epub
from reader.storage import Store


def cached_digest(store, book_id):
    rows = store.db.execute('SELECT paragraph,profile,text FROM translations WHERE book=? ORDER BY paragraph,profile', (book_id,)).fetchall()
    return len(rows), hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='.data')
    parser.add_argument('books', nargs='+')
    args = parser.parse_args()
    store = Store(args.data)
    try:
        planned = []
        for filename in args.books:
            path = Path(filename)
            book = parse_epub(path.read_bytes(), path.name)
            previous = store.book(book['id'])
            if [(p['id'], p['text']) for p in previous['paragraphs']] != [(p['id'], p['text']) for p in book['paragraphs']]:
                raise ValueError('原文段落不一致，已停止迁移：' + path.name)
            planned.append((path, book, previous, cached_digest(store, book['id'])))
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        directory = store.directory / 'backups'; directory.mkdir(exist_ok=True)
        backup = directory / ('before-illustrations-' + stamp + '.sqlite3')
        with sqlite3.connect(backup) as connection:
            store.db.backup(connection)
        results = []
        for path, book, previous, cached in planned:
            current_pid = previous['pages'][previous['currentPage']]['paragraphs'][0]
            mark = previous.get('bookmark')
            bookmark_pid = previous['pages'][mark['page']]['paragraphs'][0] if mark else None
            updated = store.add(book, source_bytes=path.read_bytes())
            assert cached_digest(store, book['id']) == cached, '译文缓存发生变化'
            assert updated['pages'][updated['currentPage']]['paragraphs'][0] == current_pid, '阅读位置发生变化'
            if mark:
                assert updated['pages'][updated['bookmark']['page']]['paragraphs'][0] == bookmark_pid, '书签位置发生变化'
                assert updated['bookmark']['manual'] == mark['manual']
            result = {'id': book['id'], 'title': updated['title'], 'images': len(updated['illustrations']),
                      'chapters': len(updated['chapters']), 'pages': len(updated['pages']), 'cover': bool(updated.get('cover')),
                      'oldPage': previous['currentPage'], 'page': updated['currentPage'], 'cachedTranslations': cached[0],
                      'translationsPreserved': True, 'readingPositionPreserved': True}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        (directory / ('illustrations-migration-' + stamp + '.json')).write_text(json.dumps({'backup': str(backup), 'books': results}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Backup: ' + str(backup), flush=True)
    finally:
        store.db.close()


if __name__ == '__main__':
    main()

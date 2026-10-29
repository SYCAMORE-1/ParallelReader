"""Offline verification; never starts translation or changes the library."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import pyzipper
    from Cryptodome.Cipher import AES
    from PIL import Image, ImageFont
    from reader.image_render import chinese_font
    import server
    # Source ZIPs include the same runtime, but only release builds have a manifest.
    manifest_path = ROOT / 'release-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {'files': {}}
    for relative, expected in manifest['files'].items():
        path = (ROOT / relative).resolve()
        path.relative_to(ROOT)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Missing or changed program/resource file: ' + relative)
    font = chinese_font()
    if not font.is_relative_to(ROOT):
        raise RuntimeError('The portable Chinese font is missing')
    ImageFont.truetype(str(font), 24).getmask('中文排版检查')
    database = ROOT / '.data' / 'reader.sqlite3'
    summary = {}
    if database.is_file():
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Library database integrity check failed')
            summary = {name: connection.execute('SELECT count(*) FROM ' + name).fetchone()[0]
                       for name in ['books', 'translations', 'bookmarks']}
            for book_id, raw in connection.execute('SELECT id,data FROM books'):
                book = json.loads(raw)
                locations = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_locations'").fetchone()
                location = connection.execute('SELECT folder FROM book_locations WHERE book=?', (book_id,)).fetchone() if locations else None
                source = ROOT / '.data' / 'books' / location[0] / 'original.epub' if location else ROOT / '.data' / 'sources' / (book_id + '.epub')
                source.resolve().relative_to((ROOT / '.data').resolve())
                if not source.is_file():
                    raise RuntimeError('Missing original EPUB: ' + book['title'])
            for raw, in connection.execute("SELECT value FROM language_cache WHERE key LIKE 'image-translation:%'"):
                value = json.loads(raw)
                relative = value.get('render', {}).get('file') if isinstance(value, dict) else None
                if relative:
                    path = (ROOT / '.data' / relative).resolve()
                    path.relative_to(ROOT / '.data')
                    if not path.is_file():
                        raise RuntimeError('Missing translated illustration: ' + relative)
        finally:
            connection.close()
    print(json.dumps({'ok': True, 'python': sys.version.split()[0], 'runtimePrefix': sys.prefix,
                      'pillow': Image.__version__, 'checkedFiles': len(manifest['files']), 'manifestAvailable': manifest_path.is_file(),
                      'portableFont': str(font.relative_to(ROOT)), 'sampleAvailable': server.DEFAULT_SAMPLE.is_file(),
                      'library': summary}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

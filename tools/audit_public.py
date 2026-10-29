"""Check first-party public files for private data and deployment details."""
import argparse
import ipaddress
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.data', 'dist', 'test-results', '__pycache__', '.venv', 'runtime', 'books', 'private-transfer', 'private-library'}
TEXT = {'.py', '.js', '.cjs', '.html', '.css', '.md', '.json', '.ps1', '.bat', '.txt'}
BOOK_FILES = {'.epub', '.mobi', '.azw', '.azw3', '.fb2', '.cbz', '.cbr', '.sqlite3', '.db'}
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
PERSONAL_PATH = re.compile(r'[A-Za-z]:[\\/](?:Users|EReader)[\\/]', re.I)
TEST_NETWORKS = [ipaddress.ip_network(value) for value in ['192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24']]


def audit_public(root=ROOT):
    root = Path(root).resolve()
    known_books = []
    catalog = ROOT / '.data' / 'library-catalog.json'
    if catalog.is_file():
        known_books = [entry['bookId'] for entry in json.loads(catalog.read_text(encoding='utf-8'))]
    checked, failures = 0, []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDED for part in relative.parts):
            continue
        checked += 1
        if path.suffix.lower() in BOOK_FILES or path.name.endswith(('.sqlite3-wal', '.sqlite3-shm')):
            failures.append(str(relative) + ': private library file')
            continue
        if path.suffix.lower() not in TEXT:
            continue
        value = path.read_text(encoding='utf-8-sig')
        if PERSONAL_PATH.search(value):
            failures.append(str(relative) + ': personal absolute path')
        if any(identity in value for identity in known_books):
            failures.append(str(relative) + ': private book identifier')
        for match in IPV4.finditer(value):
            try:
                address = ipaddress.ip_address(match[0])
            except ValueError:
                continue
            if not (address.is_loopback or address.is_unspecified or any(address in network for network in TEST_NETWORKS)):
                failures.append(str(relative) + ': deployment/network address')
                break
    if failures:
        raise RuntimeError('Public source audit failed:\n' + '\n'.join(failures))
    return {'ok': True, 'checkedFiles': checked, 'privateResourcesExcluded': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=ROOT)
    print(json.dumps(audit_public(parser.parse_args().root)))

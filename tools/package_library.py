"""Build independent private book ZIPs, splitting oversized books automatically."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.library_packages import package_library


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / '.data')
    parser.add_argument('--output', type=Path, default=ROOT / 'private-transfer' / ('book-packages-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--part-mib', type=int, default=500)
    parser.add_argument('--book', action='append', help='Exact book title or ID; repeat to select several books')
    parser.add_argument('--include-removed', action='store_true', help='Also package books removed from the bookshelf')
    args = parser.parse_args()
    result = package_library(args.source, args.output, args.part_mib * 1024 * 1024, args.book, args.include_removed,
        progress=lambda entry: print(json.dumps({'packed': entry['title'], 'parts': len(entry['parts']),
            'bytes': sum(p['bytes'] for p in entry['parts'])}, ensure_ascii=False), flush=True))
    print(json.dumps({'output': str(args.output.resolve()), 'books': len(result['books']),
        'parts': sum(len(b['parts']) for b in result['books']),
        'bytes': sum(p['bytes'] for b in result['books'] for p in b['parts']),
        'warnings': len(result['warnings']), 'includesModelSettings': False}, ensure_ascii=False), flush=True)

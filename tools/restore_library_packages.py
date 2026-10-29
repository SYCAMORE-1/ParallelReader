"""Verify and restore downloaded private book packages, including split ZIPs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.library_packages import restore_library


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True, help='Directory containing ZIPs and library-manifest.json')
    parser.add_argument('--target', type=Path, default=ROOT / '.data')
    parser.add_argument('--book', action='append', help='Exact book title or ID; repeat to select several books')
    args = parser.parse_args()
    print(json.dumps(restore_library(args.packages, args.target, args.book), ensure_ascii=False), flush=True)

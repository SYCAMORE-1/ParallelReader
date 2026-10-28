"""Export a private .data folder separately; never add it to a public release."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reader.library_transfer import copy_library


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / '.data')
    parser.add_argument('--output', type=Path, required=True, help='A new, separate .data destination directory')
    args = parser.parse_args()
    result = copy_library(args.source, args.output)
    print(json.dumps({'folder': str(args.output.resolve()), 'library': result, 'apiKeysRemoved': True, 'unfinishedJobsPaused': True}, ensure_ascii=False))

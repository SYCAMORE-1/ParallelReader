"""Preview a real illustrated EPUB with deterministic, local-only translations."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reader.epub import parse_epub
from reader.text import split_sentences
from server import make_server

parser = argparse.ArgumentParser()
parser.add_argument('--data', required=True)
parser.add_argument('--book', required=True)
args = parser.parse_args()


def preview(book, pid, settings):
    source = split_sentences(book['paragraphs'][pid]['text'])
    target = ['本段为隔离预览译文。' for _ in source]
    return {'text': ''.join(target), 'sourceSentences': source, 'targetSentences': target,
            'alignment': [{'source': [i], 'target': [i]} for i in range(len(source))], 'alignmentMethod': 'translation-slots'}


app = make_server(port=0, data_dir=args.data, translate_fn=preview)
path = Path(args.book); data = path.read_bytes(); book = parse_epub(data, path.name)
app.store.add(book, source_bytes=data)
print(json.dumps({'url': f'http://127.0.0.1:{app.server_port}', 'book': book['id']}), flush=True)
try:
    app.serve_forever()
finally:
    app.translator.close(); app.server_close()

"""Small, lazy shelf images; originals and portable book metadata stay intact."""
import base64
import hashlib
import io
import threading
from collections import OrderedDict

from PIL import Image, ImageOps


class CoverThumbnails:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    def get(self, bid):
        # Check visibility even on cache hits, so removed/moved books stay hidden.
        with self.store.lock:
            visible = self.store.db.execute('''SELECT 1 FROM books WHERE id=?
                AND NOT EXISTS (SELECT 1 FROM removed_books WHERE book=books.id)''', (bid,)).fetchone()
            if not visible or not self.store.folders.available(bid):
                raise KeyError('书籍不存在')
        with self.lock:
            if bid in self.cache:
                self.cache.move_to_end(bid)
                return self.cache[bid]
            book = self.store.book(bid)
            cover = book.get('cover') or ''
            if cover.startswith('data:image/') and ';base64,' in cover:
                try:
                    data = base64.b64decode(cover.split(';base64,', 1)[1], validate=True)
                except ValueError as exc:
                    raise ValueError('封面数据无法读取') from exc
            else:
                prefix = f'/api/books/{bid}/images/'
                asset = book.get('coverAsset') or (cover[len(prefix):] if cover.startswith(prefix) else None)
                if not asset:
                    raise KeyError('封面不存在')
                data, _ = self.store.image(book, asset)
            try:
                with Image.open(io.BytesIO(data)) as original:
                    original.thumbnail((320, 480))
                    image = ImageOps.exif_transpose(original).convert('RGBA')
                    canvas = Image.new('RGB', image.size, 'white')
                    canvas.paste(image, mask=image.getchannel('A'))
                    output = io.BytesIO()
                    canvas.save(output, format='JPEG', quality=78, optimize=True)
                    result = output.getvalue()
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                raise ValueError('封面图片无法读取') from exc
            value = result, '"' + hashlib.sha256(result).hexdigest() + '"'
            self.cache[bid] = value
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return value

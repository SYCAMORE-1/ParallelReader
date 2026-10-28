import collections
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from reader.epub import parse_epub
from reader.image_translation import ImageTranslator
from reader.storage import Store
from test_illustrations import illustrated_epub
from test_image_translation import sample, fake_render
from test_reader import until


class PerBookImageConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name)
        self.books=[]
        for index in range(3):
            output=io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(illustrated_epub())) as source_zip, zipfile.ZipFile(output,'w') as target_zip:
                for name in source_zip.namelist():
                    content=source_zip.read(name)
                    if name.endswith('.opf'):content=content.replace(b'Illustrated Handbook',('Volume '+str(index)).encode())
                    target_zip.writestr(name,content)
            data=output.getvalue()
            book=self.store.add(parse_epub(data),source_bytes=data)
            source=next(iter(book['images'].values()));assets=['picture'+str(i) for i in range(4)]
            book['images'].update({a:dict(source) for a in assets})
            book['illustrations']=[{'asset':a,'beforeParagraph':0} for a in assets]
            with self.store.lock,self.store.db:
                self.store.db.execute('UPDATE books SET data=? WHERE id=?',(json.dumps(book),book['id']))
            self.books.append(book)
        self.lock=threading.Lock();self.active=collections.Counter();self.peaks=collections.Counter();self.calls=[]
        self.release={b['id']:threading.Event() for b in self.books}
        self.worker=ImageTranslator(self.store,self.read,fake_render)

    def tearDown(self):
        for event in self.release.values():event.set()
        self.worker.close();self.worker.executor.shutdown(wait=True)
        self.store.db.close();self.temp.cleanup()

    def read(self,book,asset,*args):
        with self.lock:
            self.active[book['id']]+=1;self.peaks[book['id']]=max(self.peaks[book['id']],self.active[book['id']])
            self.calls.append((book['id'],asset,'review' if len(args)==4 else 'initial'))
        try:
            if not self.release[book['id']].wait(10):raise RuntimeError('测试等待超时')
            return sample()
        finally:
            with self.lock:self.active[book['id']]-=1

    def test_three_books_run_six_images_and_pause_is_independent(self):
        review_book=self.books[2]
        for asset in self.worker.book_jobs.assets(review_book):
            key=self.worker.key(review_book,asset,self.store.settings())
            self.store.cache_language_result(key+':draft',sample())
        for index,book in enumerate(self.books):
            self.worker.book_jobs.action(book,'start','review' if index==2 else 'bulk','round1' if index==2 else '')
        # Two records for one book still share its two slots.
        self.worker.book_jobs.action(self.books[0],'start','review','round1')
        until(lambda:all(self.active[b['id']]==2 for b in self.books))
        self.assertEqual(sum(self.active.values()),6)
        paused=self.books[1];self.worker.book_jobs.action(paused,'pause')
        for event in self.release.values():event.set()
        until(lambda:self.worker.book_jobs.status(self.books[0])['state']=='completed')
        until(lambda:self.worker.book_jobs.status(review_book,'review','round1')['state']=='completed')
        until(lambda:self.worker.book_jobs.status(paused)['completed']==2)
        time.sleep(.6)
        self.assertEqual(self.worker.book_jobs.status(paused)['state'],'paused')
        self.assertEqual(self.worker.book_jobs.status(paused)['completed'],2)
        self.worker.book_jobs.action(paused,'resume')
        until(lambda:self.worker.book_jobs.status(paused)['state']=='completed')
        self.assertEqual(dict(self.peaks),{b['id']:2 for b in self.books})
        self.assertTrue(all(stage=='review' for bid,_,stage in self.calls if bid==review_book['id']))

    def test_manual_queue_shares_book_limit_without_blocking_another_book(self):
        first,second=self.books[:2]
        for book in [first,second]:
            for asset in self.worker.book_jobs.assets(book):self.worker.request(book,asset)
        until(lambda:self.active[first['id']]==2 and self.active[second['id']]==2)
        self.assertEqual(sum(self.active.values()),4)
        self.worker.remove_book(first['id'])
        self.assertEqual(sum(j['future'].cancelled() for j in self.worker.jobs.values() if j['book']==first['id']),2)
        self.release[second['id']].set()
        until(lambda:self.worker.book_jobs.status(second)['state']=='completed')
        self.assertEqual(self.active[first['id']],2)
        self.assertEqual(self.peaks[second['id']],2)

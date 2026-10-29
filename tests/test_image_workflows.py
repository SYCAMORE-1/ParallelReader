import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageChops
from reader.epub import parse_epub
from reader.export import BookExporter
from reader.image_render import render_image, chinese_font, text_region
from reader.image_translation import ImageTranslator
from reader.reproof import Reproofreader
from reader.storage import Store
from reader.translation import Translator, profile
from reader.translation_rules import finish_translation, translation_plan
from test_image_translation import fake_render, sample
from test_illustrations import illustrated_epub
from test_reader import until


class ImageWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(self.temp.name)
        data = illustrated_epub(); self.book = self.store.add(parse_epub(data), source_bytes=data)
        self.calls = []; self.gate = threading.Event(); self.gate.set(); self.fail = False
        self.images = ImageTranslator(self.store, self.read, fake_render)
        self.store.image_service = self.images; self.store.image_progress = self.images.book_jobs.library
        self.worker = None
        self.fp = profile(self.store.settings())
        for p in self.book['paragraphs']:
            plan = translation_plan(p['text'], [])
            detail = finish_translation(plan, ['原有译文。'] * sum(not piece['fixed'] for piece in plan['pieces']))
            self.store.cache(self.book['id'], p['id'], self.fp, detail['text'], detail)
        self.original = self.store.source_path(self.book).read_bytes()

    def read(self, *args):
        self.calls.append(('review' if len(args) == 6 else 'initial', args[1]))
        self.gate.wait(3)
        if self.fail: raise RuntimeError('测试模型失败')
        return sample()

    def tearDown(self):
        self.gate.set()
        if self.worker:
            self.worker.close()
            for thread in self.worker.threads: thread.join(3)
        self.images.close(); self.images.executor.shutdown(wait=True)
        self.store.db.close(); self.temp.cleanup()

    def complete_images(self):
        self.images.book_jobs.action(self.book, 'start')
        until(lambda: self.images.book_jobs.status(self.book)['state'] == 'completed')

    def test_bulk_auto_images_progress_pause_and_persist(self):
        self.gate.clear()
        self.worker = Translator(self.store, workers=1, image_translator=self.images)
        value = self.worker.bulk_action(self.book, 'start')
        self.assertEqual(value['textCompleted'], len(self.book['paragraphs']))
        self.assertEqual(value['imageTotal'], 2)  # repeated image asset only processed once
        self.assertLess(value['percent'], 100)
        paused = self.worker.bulk_action(self.book, 'pause')
        self.assertEqual(paused['state'], 'paused')
        time.sleep(.6); self.assertEqual(self.calls, [])
        restored = ImageTranslator(self.store, self.read, fake_render)
        try: self.assertEqual(restored.book_jobs.status(self.book)['state'], 'paused')
        finally: restored.close(); restored.executor.shutdown(wait=True)
        self.gate.set(); self.worker.bulk_action(self.book, 'resume')
        until(lambda: self.worker.bulk_status(self.book)['state'] == 'completed')
        value = self.worker.bulk_status(self.book)
        self.assertEqual(value['imageCompleted'], 2); self.assertEqual(value['percent'], 100)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.store.source_path(self.book).read_bytes(), self.original)

    def test_retry_failure_really_retries_and_keeps_success(self):
        self.fail = True; self.images.book_jobs.action(self.book, 'start')
        until(lambda: self.images.book_jobs.status(self.book)['state'] == 'failed')
        self.assertEqual(self.images.book_jobs.status(self.book)['failed'], 2)
        self.fail = False; self.images.book_jobs.action(self.book, 'retry')
        until(lambda: self.images.book_jobs.status(self.book)['state'] == 'completed')
        calls = len(self.calls); self.images.book_jobs.action(self.book, 'resume'); time.sleep(.6)
        self.assertEqual(len(self.calls), calls)

    def test_scattered_failures_do_not_count_as_three_consecutive_failures(self):
        source = next(iter(self.book['images'].values()))
        assets = ['fail1', 'ok1', 'fail2', 'ok2', 'fail3', 'ok3']
        self.book['images'].update({asset: dict(source) for asset in assets})
        self.book['illustrations'] = [{'asset': asset, 'beforeParagraph': 0} for asset in assets]
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE books SET data=? WHERE id=?', (json.dumps(self.book), self.book['id']))
        def read(book, asset, *args):
            if asset.startswith('fail'):
                raise RuntimeError('单张图片失败')
            return sample()
        self.images.read = read
        self.images.book_jobs.action(self.book, 'start')
        until(lambda: self.images.book_jobs.status(self.book)['state'] in {'paused', 'failed'})
        status = self.images.book_jobs.status(self.book)
        self.assertEqual((status['state'], status['failed'], status['completed']), ('failed', 3, 3))

    def test_new_review_round_rechecks_images_without_repeating_initial_ocr(self):
        self.complete_images(); self.calls.clear()
        queue = Reproofreader(self.store, review_fn=lambda book,pid,settings,snapshot,selection: snapshot['detail'], image_translator=self.images)
        self.gate.clear()
        first = queue.request(self.book, {'scope': 'book'})
        self.assertEqual(first['imageCompleted'], 0)
        self.assertEqual(first['completed'], 0)
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})
        self.assertEqual(self.images.book_jobs.status(self.book, 'review', first['id'])['state'], 'paused')
        self.gate.set(); queue.request(self.book, {'scope': 'book', 'action': 'resume'})
        while True:
            work = queue.take(set())
            if work is None: break
            key, book, settings, _ = work; queue.execute(key, book, settings); queue.finish(key, None)
        until(lambda: queue.public(queue.jobs[first['id']])['state'] == 'completed')
        self.assertEqual([call[0] for call in self.calls], ['review', 'review'])
        shelf = self.store.library_progress(self.fp)[self.book['id']]
        self.assertEqual((shelf['percent'], shelf['imageCompleted']), (100, 2))
        self.gate.clear(); second = queue.request(self.book, {'scope': 'book'})
        self.assertNotEqual(first['id'], second['id']); self.assertEqual(second['imageCompleted'], 0)
        self.assertLess(self.store.library_progress(self.fp)[self.book['id']]['percent'], 100)
        queue.request(self.book, {'scope': 'book', 'action': 'pause'})

    def test_export_requires_and_embeds_translated_images(self):
        exporter = BookExporter(self.store)
        try:
            with self.assertRaisesRegex(ValueError, '图片'): exporter.export(self.book,self.store.settings())
            self.complete_images()
            for kind in ['translation', 'bilingual']:
                exported = exporter.export(self.book, self.store.settings(), kind)
                with zipfile.ZipFile(exported) as archive:
                    translated = [n for n in archive.namelist() if n.endswith('-translated.png')]
                    self.assertEqual(len(translated), 2)
                    self.assertIn(b'image/png', archive.read('EPUB/package.opf'))
                    body = ''.join(archive.read(n).decode() for n in archive.namelist() if n.startswith('EPUB/chapter-'))
                    self.assertIn('-translated.png', body)
                    if kind == 'bilingual': self.assertIn('id="illustration-', body)
        finally: exporter.close()


class ImageRenderTests(unittest.TestCase):
    def test_clipped_line_uses_full_glyphs_instead_of_a_punctuation_seed(self):
        image = Image.new('RGB', (420, 220), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 65, 390, 145), outline='black', width=2)
        for x in range(40, 310, 18):
            draw.rectangle((x, 100, x + 8, 119), fill='black')
        draw.rectangle((67, 98, 69, 101), fill='black')  # only this dot falls inside the approximate box
        box = (35, 80, 320, 103)
        refined = text_region(image, box)
        self.assertGreater(refined[2] - refined[0], 250)
        self.assertGreaterEqual(refined[3] - refined[1], 20)
        self.assertGreater(refined[1], 65)
        self.assertLess(refined[3], 145)
        stream = io.BytesIO(); image.save(stream, format='PNG')
        value = {'blocks': [{'source': 'A complete annotation line', 'translation': '完整的中文图注',
                             'bbox': [round(box[0] / 420 * 1000), round(box[1] / 220 * 1000),
                                      round(box[2] / 420 * 1000), round(box[3] / 220 * 1000)]}]}
        with tempfile.TemporaryDirectory() as directory:
            result = render_image(stream.getvalue(), value, directory)
            self.assertGreater(result['regions'][0][2] - result['regions'][0][0], 250)

    def test_isolated_speck_cannot_shrink_a_whole_text_box(self):
        image = Image.new('RGB', (420, 220), 'white')
        ImageDraw.Draw(image).rectangle((65, 100, 68, 103), fill='black')
        box = (35, 80, 320, 120)
        self.assertEqual(text_region(image, box), box)

    def test_only_local_regions_change_and_dark_panel_is_supported(self):
        image=Image.new('RGB',(600,400),'white'); draw=ImageDraw.Draw(image)
        font=ImageFont.truetype(str(chinese_font()),24)
        draw.text((40,40),'Hello world',fill='black',font=font)
        draw.rectangle((20,200,580,340),fill=(20,20,20)); draw.text((40,225),'A dark caption',fill='white',font=font)
        stream=io.BytesIO(); image.save(stream,format='PNG'); data=stream.getvalue()
        result={'blocks':[{'source':'Hello world','translation':'你好世界','bbox':[60,90,400,200]},
                          {'source':'A dark caption','translation':'深色底图中的文字','bbox':[60,550,500,675]}]}
        with tempfile.TemporaryDirectory() as directory:
            output=render_image(data,result,directory)
            actual=Image.open(Path(directory)/output['file']).convert('RGB')
            self.assertEqual(actual.size,image.size); self.assertNotEqual(actual.tobytes(),image.tobytes())
            diff=ImageChops.difference(actual,image)
            for box in output['regions']: ImageDraw.Draw(diff).rectangle((box[0],box[1],box[2]-1,box[3]-1),fill='black')
            self.assertIsNone(diff.getbbox())
            self.assertEqual(actual.getpixel((30,210)),(20,20,20))
            self.assertEqual(render_image(data,result,directory)['digest'],output['digest'])
            with self.assertRaisesRegex(RuntimeError,'定位'): render_image(data,{'blocks':[{'source':'a','translation':'甲'}]},directory)

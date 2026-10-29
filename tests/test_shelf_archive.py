from contextlib import closing
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pyzipper

from reader.epub import parse_epub
from reader.book_folders import BookFolders
from reader.library_packages import VolumeReader
from reader.reading_policy import prepare_book
from reader.shelf_archive import ArchiveJobs, export_shelf, import_shelf, ordered_volumes
from reader.storage import Store
from reader.translation import Translator, profile
from test_illustrations import illustrated_epub


class ShelfArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = Store(self.root / 'source')
        self.target = Store(self.root / 'target')
        self.data = illustrated_epub()
        self.book = self.source.add(parse_epub(self.data), source_bytes=self.data)
        self.source.cache(self.book['id'], 0, 'profile-a', '测试译文甲。')
        self.source.save_settings(self.source.settings() | {'apiKey': 'not-for-archive-sentinel'})
        self.source.save_bulk_job(self.book['id'], 'profile-a', 'test-model', 'running')
        self.counter = 0

    def tearDown(self):
        self.source.db.close(); self.target.db.close(); self.temp.cleanup()

    def pack(self, store=None, **options):
        self.counter += 1
        directory = self.root / ('archive-' + str(self.counter))
        result = export_shelf(store or self.source, directory, '用户输入的测试密码', **options)
        return [directory / part['file'] for part in result['parts']], result

    def restore(self, paths):
        return import_shelf(self.target, paths, '用户输入的测试密码')

    def test_encrypted_bounded_volumes_restore_current_wal_without_keys(self):
        paths, result = self.pack(part_bytes=1024)
        self.assertGreater(len(paths), 1)
        self.assertTrue(all(p.stat().st_size <= 1024 for p in paths))
        with VolumeReader(paths) as reader, pyzipper.AESZipFile(reader) as archive:
            self.assertTrue(all(info.flag_bits & 1 for info in archive.infolist()))
            self.assertTrue(all(info.wz_aes_strength == 3 and info.wz_aes_version == 2 for info in archive.infolist()))
            with self.assertRaises(RuntimeError): archive.read('shelf.json')
            archive.setpassword('用户输入的测试密码'.encode())
            self.assertNotIn(b'not-for-archive-sentinel', b''.join(archive.read(name) for name in archive.namelist()))
        restored = self.restore(list(reversed(paths)))
        self.assertEqual(restored['restoredBooks'], 1)
        self.assertEqual(self.target.translation(self.book['id'], 0, 'profile-a'), '测试译文甲。')
        self.assertEqual(self.target.bulk_jobs()[0]['state'], 'paused')
        self.assertEqual(self.source.bulk_jobs()[0]['state'], 'running')
        self.assertEqual(self.target.settings()['apiKey'], '')

    def test_exact_duplicates_skip_but_different_translation_and_same_title_survive(self):
        paths, _ = self.pack(); self.restore(paths)
        self.assertEqual(self.restore(paths)['skippedBooks'], 1)
        self.target.cache(self.book['id'], 0, 'profile-a', '不同的本地译文乙。')
        restored = self.restore(paths)
        self.assertEqual(restored['restoredBooks'], 1)
        variant = restored['books'][0]['id']
        self.assertNotEqual(variant, self.book['id'])
        self.assertEqual(self.target.translation(variant, 0, 'profile-a'), '测试译文甲。')
        self.assertEqual(self.target.translation(self.book['id'], 0, 'profile-a'), '不同的本地译文乙。')
        self.assertEqual(self.restore(paths)['skippedBooks'], 1)
        # Both variants round trip into a third shelf and are still deduplicated.
        merged, _ = self.pack(self.target, part_bytes=2048)
        with closing(Store(self.root / 'third').db):
            pass
        third = Store(self.root / 'third')
        try:
            self.assertEqual(import_shelf(third, merged, '用户输入的测试密码')['restoredBooks'], 2)
            self.assertEqual(import_shelf(third, merged, '用户输入的测试密码')['skippedBooks'], 2)
            self.assertEqual(len(third.library()), 2)
        finally: third.db.close()
        self.target.db.close(); self.target = Store(self.root / 'target')
        self.assertEqual(len(self.target.library()), 2)
        self.assertEqual(self.target.source_path(variant).read_bytes(), self.data)

    def test_same_title_with_different_original_keeps_both(self):
        self.target.add(parse_epub(self.data), source_bytes=self.data)
        stream = io.BytesIO(self.data)
        with zipfile.ZipFile(stream, 'a') as archive: archive.comment = b'a different edition'
        data = stream.getvalue()
        another = self.source.add(parse_epub(data), source_bytes=data)
        self.assertEqual(another['title'], self.book['title'])
        paths, _ = self.pack(); result = self.restore(paths)
        self.assertEqual(result['restoredBooks'], 2)
        self.assertEqual(len(self.target.library()), 3)  # local untranslated copy also retained

    def test_changed_bookmarks_and_saved_language_content_are_retained(self):
        paths, _ = self.pack(); self.restore(paths)
        self.target.set_bookmark(self.book['id'], 1)
        self.assertEqual(self.restore(paths)['restoredBooks'], 1)
        self.source.cache_language_result('synthetic-name-cache', {'names': ['Example Name']}, book=self.book['id'])
        changed, _ = self.pack()
        self.assertEqual(self.restore(changed)['restoredBooks'], 1)
        self.assertEqual(self.restore(changed)['skippedBooks'], 1)

    def test_late_snapshot_write_failure_rolls_back_all_books_and_directories(self):
        stream = io.BytesIO(self.data)
        with zipfile.ZipFile(stream, 'a') as archive: archive.comment = b'another fixture'
        data = stream.getvalue(); self.source.add(parse_epub(data), source_bytes=data)
        paths, _ = self.pack()
        write = BookFolders.write_snapshot
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2: raise OSError('synthetic disk failure')
            return write(*args)
        with patch.object(BookFolders, 'write_snapshot', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'synthetic disk failure'): self.restore(paths)
        self.assertEqual(self.target.library(), [])
        self.assertEqual(list((self.target.directory / 'books').iterdir()), [])

    def test_bad_password_missing_and_corrupted_parts_leave_shelf_untouched(self):
        paths, _ = self.pack(part_bytes=1024)
        with self.assertRaisesRegex(ValueError, '密码|分卷'):
            import_shelf(self.target, paths, 'wrong-password')
        with self.assertRaises(ValueError): self.restore(paths[1:])
        with paths[-1].open('r+b') as file:
            file.seek(-10, 2); file.write(b'broken-data')
        with self.assertRaises(ValueError): self.restore(paths)
        self.assertEqual(self.target.library(), [])
        self.assertEqual(list((self.target.directory / 'books').iterdir()), [])

    def test_password_required_and_user_confirmation(self):
        for password in ('', None, '   '):
            with self.assertRaises(ValueError): export_shelf(self.source, self.root / 'none', password)
        jobs = ArchiveJobs(self.source)
        try:
            with self.assertRaises(ValueError): jobs.export('a-password', 'another-password')
            job = jobs.start_import()
            with self.assertRaises(ValueError): jobs.upload(job['id'], '../outside.zip', io.BytesIO(b'x'), 1)
            with self.assertRaises(ValueError): jobs.finish_import(job['id'], '')
            jobs.cancel(job['id'])
        finally: jobs.close()

    def test_variant_image_translation_keeps_both_bitmap_versions(self):
        folder = self.source.book_directory(self.book) / 'translated-images'; folder.mkdir()
        (folder / 'test.png').write_bytes(b'synthetic-image')
        self.source.cache_language_result('image-translation:test', {'book': self.book['id'], 'blocks': [{'text': '译图'}], 'render': {'file': (folder / 'test.png').relative_to(self.source.directory).as_posix()}}, book=self.book['id'])
        self.target.add(parse_epub(self.data), source_bytes=self.data)
        paths, _ = self.pack(); result = self.restore(paths)
        variant = result['books'][0]['id']
        with self.target.lock:
            raw = self.target.db.execute('SELECT value FROM language_cache JOIN language_cache_books USING(key) WHERE book=?', (variant,)).fetchone()[0]
        value = json.loads(raw)
        self.assertEqual((self.target.directory / value['render']['file']).read_bytes(), b'synthetic-image')
        self.assertEqual(self.restore(paths)['skippedBooks'], 1)


class DirectReadingTests(unittest.TestCase):
    def test_chinese_language_and_translated_export_are_direct_reading(self):
        self.assertTrue(prepare_book({'language': 'zh-CN', 'paragraphs': [{'text': '第一章'}]})['sourceOnly'])
        self.assertTrue(prepare_book({'language': 'en', 'alreadyTranslated': True, 'paragraphs': []})['sourceOnly'])
        self.assertTrue(prepare_book({'language': 'en', 'paragraphs': [{'text': '这是一段已经翻译成中文的内容。' * 8}]})['sourceOnly'])
        self.assertFalse(prepare_book({'language': 'en', 'paragraphs': [{'text': 'An ordinary English novel.'}]})['sourceOnly'])

    def test_direct_reading_never_calls_translation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            data = illustrated_epub(); book = parse_epub(data); book['language'] = 'zh'; book.pop('sourceOnly', None)
            book = store.add(book, source_bytes=data)
            store.save_bulk_job(book['id'], profile(store.settings()), 'old-test-model', 'running')
            translator = Translator(store, translate_fn=lambda *args: self.fail('unexpected translation request'))
            try:
                translator.window(book, 0)
                self.assertFalse(translator.desired)
                self.assertEqual(store.bulk_jobs()[0]['state'], 'paused')
                self.assertEqual(translator.bulk_status(book)['percent'], 100)
                with self.assertRaises(ValueError): translator.bulk_action(book, 'start')
                with self.assertRaises(ValueError): translator.reproofread(book, {})
                self.assertTrue(store.library()[0]['sourceOnly'])
            finally: translator.close(); store.db.close()

    def test_large_dune_sized_translation_query_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            try:
                store.cache('synthetic-book', 39999, 'test-profile', 'large-book-translation')
                old = store.db.setlimit(9, 999) if hasattr(store.db, 'setlimit') else None
                self.assertEqual(store.translations('synthetic-book', list(range(40000)), 'test-profile'), {39999: 'large-book-translation'})
            finally: store.db.close()

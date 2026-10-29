from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

from reader.epub import parse_epub
from reader.library_packages import MANIFEST, VolumeReader, fs_path, package_library, restore_library
from reader.storage import Store
from test_illustrations import illustrated_epub


class LibraryPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'source')
        self.data = illustrated_epub()
        self.book = self.store.add(parse_epub(self.data), source_bytes=self.data)
        self.store.save_settings(self.store.settings() | {'apiKey': 'package-secret-sentinel'})
        self.store.cache(self.book['id'], 0, 'saved-profile', '已有中文译文。')
        self.store.cache_draft(self.book['id'], 1, 'draft-profile', {'text': '未完成初译。'})
        self.store.progress(self.book['id'], 1)
        self.store.set_bookmark(self.book['id'], 2)
        self.store.save_bulk_job(self.book['id'], 'saved-profile', 'model', 'paused')
        folder = self.store.book_directory(self.book)
        images = folder / 'translated-images'; images.mkdir()
        (images / 'sample.png').write_bytes(b'preserved image')
        self.store.cache_language_result('image-translation:sample', {'render': {'file': (images / 'sample.png').relative_to(self.store.directory).as_posix()}}, book=self.book['id'])
        exports = folder / 'exports'; exports.mkdir()
        (exports / 'excluded.epub').write_bytes(b'not included')

    def tearDown(self):
        self.store.db.close()
        self.temporary.cleanup()

    def pack(self, name='packages', **options):
        destination = self.root / name
        return destination, package_library(self.store.directory, destination, **options)

    def add_second(self, removed=False):
        stream = io.BytesIO(self.data)
        with zipfile.ZipFile(stream, 'a') as archive: archive.comment = b'another edition'
        data = stream.getvalue(); book = parse_epub(data); book['title'] = 'Another Handbook'
        book = self.store.add(book, source_bytes=data)
        self.store.cache(book['id'], 0, 'another-profile', '另一本书的译文。')
        if removed: self.store.remove(book['id'])
        return book

    def test_pack_uses_latest_wal_state_without_mutating_source_or_copying_exports(self):
        folder = self.store.book_directory(self.book)
        with closing(sqlite3.connect(folder / 'book.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT count(*) FROM translations').fetchone()[0], 0)
        destination, manifest = self.pack()
        entry = manifest['books'][0]
        self.assertEqual(len(entry['parts']), 1)
        with zipfile.ZipFile(destination / entry['parts'][0]['file']) as archive:
            names = archive.namelist()
            self.assertTrue(all(name.startswith('books/' + entry['folder'] + '/') for name in names))
            self.assertFalse(any('/exports/' in name or name.endswith('reader.sqlite3') for name in names))
            self.assertEqual(archive.read('books/' + entry['folder'] + '/original.epub'), self.data)
            snapshot = self.root / 'inspect.sqlite3'
            snapshot.write_bytes(archive.read('books/' + entry['folder'] + '/book.sqlite3'))
        with closing(sqlite3.connect(snapshot)) as db:
            self.assertEqual(db.execute('SELECT text FROM translations').fetchone()[0], '已有中文译文。')
            self.assertEqual(db.execute('SELECT page FROM bookmarks').fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='settings'").fetchone())
        self.assertNotIn(b'package-secret-sentinel', snapshot.read_bytes())
        self.assertEqual(self.store.settings()['apiKey'], 'package-secret-sentinel')
        self.assertEqual(self.store.source_path(self.book).read_bytes(), self.data)

    def test_split_zip_obeys_exact_size_cap_and_restores_without_joining_large_file(self):
        destination, manifest = self.pack(max_part_bytes=1024)
        entry = manifest['books'][0]
        self.assertGreater(len(entry['parts']), 1)
        self.assertTrue(all(p['bytes'] <= 1024 for p in entry['parts']))
        self.assertTrue(all(p['file'].endswith(f'.{i:04d}') for i, p in enumerate(entry['parts'], 1)))
        with VolumeReader([destination / p['file'] for p in entry['parts']]) as reader, zipfile.ZipFile(reader) as archive:
            self.assertIsNone(archive.testzip())
        target = self.root / 'restored'
        result = restore_library(destination, target)
        self.assertEqual(result['restoredBooks'], 1)
        restored = Store(target)
        try:
            self.assertEqual(restored.translation(self.book['id'], 0, 'saved-profile'), '已有中文译文。')
            self.assertEqual(restored.draft(self.book['id'], 1, 'draft-profile')['text'], '未完成初译。')
            self.assertEqual(restored.bookmark(self.book['id'])['page'], 2)
            cached = restored.language_result('image-translation:sample')
            self.assertEqual((restored.directory / cached['render']['file']).read_bytes(), b'preserved image')
            self.assertEqual(restored.settings()['apiKey'], '')
        finally: restored.db.close()

    def test_selective_download_skips_books_with_no_downloaded_parts(self):
        second = self.add_second()
        destination, manifest = self.pack()
        subset = self.root / 'downloaded'; subset.mkdir()
        shutil.copy2(destination / MANIFEST, subset / MANIFEST)
        wanted = next(book for book in manifest['books'] if book['id'] == second['id'])
        for part in wanted['parts']: shutil.copy2(destination / part['file'], subset / part['file'])
        result = restore_library(subset, self.root / 'selected')
        self.assertEqual(result['books'], [{'id': second['id'], 'title': second['title']}])

    def test_selector_and_removed_book_policy(self):
        second = self.add_second(removed=True)
        _, default = self.pack()
        self.assertEqual([b['id'] for b in default['books']], [self.book['id']])
        _, selected = self.pack('selected', include_removed=True, books=[second['id']])
        self.assertEqual([b['id'] for b in selected['books']], [second['id']])

    def test_moved_out_book_is_not_included_in_default_packages(self):
        second = self.add_second()
        folder = self.store.book_directory(second)
        self.store.flush_book_folders(force=True)
        folder.rename(self.root / 'moved-away')
        _, manifest = self.pack()
        self.assertEqual([b['id'] for b in manifest['books']], [self.book['id']])

    def test_unfinished_tasks_are_paused_only_in_package(self):
        self.store.save_bulk_job(self.book['id'], 'saved-profile', 'model', 'running')
        destination, manifest = self.pack()
        target = self.root / 'paused'; restore_library(destination, target)
        restored = Store(target)
        try: self.assertEqual(restored.bulk_jobs()[0]['state'], 'paused')
        finally: restored.db.close()
        self.assertEqual(self.store.bulk_jobs()[0]['state'], 'running')

    def test_missing_volume_fails_before_creating_target(self):
        destination, manifest = self.pack(max_part_bytes=1024)
        part = destination / manifest['books'][0]['parts'][0]['file']; part.unlink()
        target = self.root / 'missing'
        with self.assertRaises(FileNotFoundError): restore_library(destination, target)
        self.assertFalse(target.exists())

    def test_corrupt_volume_does_not_touch_existing_target_files(self):
        destination, manifest = self.pack(max_part_bytes=1024)
        part = destination / manifest['books'][0]['parts'][0]['file']; part.write_bytes(part.read_bytes() + b'corrupt')
        target = self.root / 'existing'; target.mkdir(); sentinel = target / 'keep.txt'; sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, '校验失败'): restore_library(destination, target)
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assertFalse((target / 'books').exists())

    def test_existing_book_is_never_overwritten(self):
        destination, _ = self.pack(); target = self.root / 'existing'
        restore_library(destination, target)
        original = next((target / 'books').glob('*/original.epub')); before = original.read_bytes()
        with self.assertRaises(FileExistsError): restore_library(destination, target)
        self.assertEqual(original.read_bytes(), before)

    def test_zip_traversal_is_rejected_even_with_valid_volume_checksum(self):
        destination, manifest = self.pack()
        entry = manifest['books'][0]; part = destination / entry['parts'][0]['file']
        with zipfile.ZipFile(part, 'w') as archive: archive.writestr('../escape.txt', 'bad')
        entry['parts'][0].update(bytes=part.stat().st_size, sha256=hashlib.sha256(part.read_bytes()).hexdigest())
        entry['uncompressedBytes'] = 3
        (destination / MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '越界'): restore_library(destination, self.root / 'bad')
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_unchanged_book_produces_identical_zip_hash(self):
        _, first = self.pack('first'); _, second = self.pack('second')
        self.assertEqual(first['books'][0]['parts'], second['books'][0]['parts'])

    def test_shipped_restore_tool_runs_without_reader_module_imports(self):
        destination, _ = self.pack(max_part_bytes=1024)
        helper = destination / 'restore-packages.py'
        self.assertNotIn('from reader', helper.read_text(encoding='utf-8'))
        result = subprocess.run([sys.executable, '-X', 'utf8', str(helper), '--target', str(self.root / 'standalone')],
                                cwd=self.root, text=True, encoding='utf-8', capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['restoredBooks'], 1)

    def test_deep_staging_path_with_long_title_and_image_filename(self):
        changed = dict(self.book, title='A long illustrated handbook ' + 'x' * 80)
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE books SET data=? WHERE id=?', (json.dumps(changed), self.book['id']))
        name = 'a' * 64 + '.png'
        (self.store.book_directory(self.book) / 'translated-images' / name).write_bytes(b'long-path-image')
        destination, manifest = self.pack()
        parent = self.root / ('nested-restore-parent-' + 'x' * 18)
        target = parent / '.data'
        try:
            restore_library(destination, target)
            image = target / 'books' / manifest['books'][0]['folder'] / 'translated-images' / name
            self.assertEqual(fs_path(image).read_bytes(), b'long-path-image')
        finally:
            if fs_path(parent).exists():
                parent.resolve().relative_to(self.root.resolve())
                shutil.rmtree(fs_path(parent))

    def test_output_and_size_limits_are_checked(self):
        with self.assertRaises(ValueError): package_library(self.store.directory, self.store.directory / 'output')
        with self.assertRaises(ValueError): self.pack(max_part_bytes=2 * 1024 ** 3)
        with self.assertRaisesRegex(ValueError, '找不到'): self.pack(books=['unknown-book'])
        self.assertFalse((self.root / 'packages').exists())

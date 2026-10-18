import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from tag_store import TagStore, fingerprint, discover, canonical


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'images'
        self.root.mkdir()
        self.store = TagStore(self.base / 'tags.json')

    def tagged(self, path, content=b'first'):
        path.write_bytes(content)
        digest, stamp = fingerprint(path)
        self.store.record(path, digest, stamp, ['blue_hair', 'solo'], 'test', .35, .85)

    def test_replacement_and_deleted_last_copy(self):
        image = self.root / 'one.png'
        self.tagged(image)
        image.write_bytes(b'other')
        self.assertFalse(self.store.has_tags(image))
        with self.assertRaises(ValueError):
            self.store.copy_text(image, [self.root])
        self.store.health([self.root])
        self.assertEqual(self.store.data['images'], {})

    def test_external_move_copy_and_cleanup(self):
        image = self.root / 'one.png'
        self.tagged(image)
        other = self.root / 'two.png'
        image.rename(other)
        duplicate = self.root / 'three.png'
        duplicate.write_bytes(other.read_bytes())
        self.store.health([self.root])
        self.assertTrue(self.store.has_tags(other))
        self.assertTrue(self.store.has_tags(duplicate))
        self.assertEqual(len(self.store.data['images']), 1)
        other.unlink()
        self.store.health([self.root])
        self.assertEqual(len(self.store.data['paths']), 1)

    def test_inheritance_edit_rename_and_empty_save(self):
        child = self.root / 'character'
        child.mkdir()
        image = child / 'one.png'
        self.tagged(image)
        self.store.associate(self.root, 'game_name solo')
        self.store.associate(child, 'character_name, solo')
        self.assertEqual(self.store.copy_text(image, [self.root]), 'game_name solo character_name blue_hair')
        moved = self.root / 'renamed'
        child.rename(moved)
        self.store.health([self.root])
        self.assertEqual(self.store.folder_text(moved), 'character_name, solo')
        self.store.associate(moved, '')
        self.assertEqual(self.store.copy_text(moved / 'one.png', [self.root]), 'game_name solo blue_hair')

    def test_unavailable_root_retains_records(self):
        image = self.root / 'one.png'
        self.tagged(image)
        original = self.store.data.copy()
        with patch('tag_store.discover', side_effect=PermissionError('offline')):
            errors = self.store.health([self.root])
        self.assertTrue(errors)
        self.assertEqual(self.store.data, original)

    def test_atomic_save_failure_keeps_previous_data(self):
        self.store.associate(self.root, 'old')
        with patch('tag_store.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.associate(self.root, 'new')
        self.assertEqual(self.store.folder_text(self.root), 'old')
        self.assertEqual(TagStore(self.store.filename).folder_text(self.root), 'old')

    def test_discovery_deduplicates_and_cancel_does_not_prune(self):
        image = self.root / 'one.png'
        self.tagged(image)
        (self.root / 'video.mp4').write_bytes(b'x')
        self.assertEqual(len(discover([image, self.root])[0]), 1)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            self.store.health([self.root], stop)
        self.assertTrue(self.store.has_tags(image))

    def test_known_rename_and_undo(self):
        folder = self.root / 'old'
        folder.mkdir()
        self.store.associate(folder, 'my_tag')
        self.tagged(folder / 'image.png')
        moved = folder.with_name('new')
        folder.rename(moved)
        self.store.remap(folder, moved)
        self.assertEqual(self.store.folder_text(moved), 'my_tag')
        moved.rename(folder)
        self.store.remap(moved, folder)
        self.assertTrue(self.store.has_tags(folder / 'image.png'))


if __name__ == '__main__':
    unittest.main()

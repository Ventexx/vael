import copy
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import scan, NamingRules, compile_pattern, matches, parse_query, FileService, top_level_selection, DEFAULTS


class NamingTests(unittest.TestCase):
    def test_multiple_patterns_inheritance_and_query(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "Misty;Pokemon" / "Set 01"
            folder.mkdir(parents=True)
            (folder / "image.png").write_bytes(b"image")
            config = copy.deepcopy(DEFAULTS)
            entries, errors = scan([root], config, True, threading.Event(), root / "index.sqlite")
            image = next(e for e in entries if e.name == "image.png")
            self.assertFalse(errors)
            self.assertEqual(image.fields, {"CHAR": "Misty", "GENRE": "Pokemon"})
            self.assertTrue(matches(image, parse_query('Pokemon g ch:Misty', config['fields'])))
            self.assertFalse(matches(image, parse_query('g:Other', config['fields'])))

    def test_patterns_reject_ambiguity(self):
        for value in ("[GENRE][CHAR]", "[MISSING]", "[CHAR]-[CHAR]"):
            with self.assertRaises(ValueError):
                compile_pattern(value, DEFAULTS["fields"])

    def test_quoted_search_and_scoped_rules(self):
        self.assertEqual(parse_query('s:"Beach Night"', DEFAULTS['fields']), [("SCENE", "beach night")])
        with self.assertRaises(ValueError):
            parse_query('s:"Beach', DEFAULTS['fields'])
        config = copy.deepcopy(DEFAULTS)
        config['rules'][0]['scope'] = str(Path('/another'))
        self.assertEqual(NamingRules(config).extract(Path('/root/Pokemon-Misty_Beach'), Path('/root')), {})


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'sorting'
        self.root.mkdir()
        self.source = self.base / 'source'
        self.source.mkdir()
        self.image = self.source / 'a.png'
        self.image.write_bytes(b'original')
        self.service = FileService(self.root)

    def test_external_copy_only_and_undo(self):
        with self.assertRaises(ValueError):
            self.service.transfer(self.image, self.root, move=True)
        copied = self.service.transfer(self.image, self.root)
        self.assertEqual(copied.read_bytes(), b'original')
        self.service.undo()
        self.assertTrue(self.image.exists())
        self.assertFalse(copied.exists())

    def test_root_boundary_and_sibling_prefix(self):
        for path in (self.root, self.source, self.base / 'sorting-other' / 'a', self.root / '..' / 'outside'):
            self.assertFalse(self.service.inside(path))
        with self.assertRaises(ValueError):
            self.service.transfer(self.image, self.source)
        with self.assertRaises(ValueError):
            self.service.rename(self.image, 'b.png')
        with self.assertRaises(ValueError):
            self.service.mkdir(self.root, '../escape')

    def test_collision_never_overwrites(self):
        first = self.service.transfer(self.image, self.root)
        second = self.service.transfer(self.image, self.root)
        self.assertNotEqual(first, second)
        self.assertIsNone(self.service.transfer(self.image, self.root, policy='skip'))
        with self.assertRaises(FileExistsError):
            self.service.transfer(self.image, self.root, policy='error')
        self.assertEqual(first.read_bytes(), b'original')

    def test_move_rename_undo_and_modified_guard(self):
        copied = self.service.transfer(self.image, self.root)
        folder = self.service.mkdir(self.root, 'target')
        moved = self.service.transfer(copied, folder, move=True)
        self.assertFalse(copied.exists())
        self.service.undo()
        self.assertTrue(copied.exists())
        renamed = self.service.rename(copied, 'renamed.png')
        renamed.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.service.undo()
        self.assertEqual(renamed.read_bytes(), b'changed')

    def test_nested_selection_and_recursive_copy(self):
        folder = self.root / 'a'
        folder.mkdir()
        image = folder / 'a.png'
        image.write_bytes(b'x')
        self.assertEqual(top_level_selection([folder, image, folder]), [folder])
        with self.assertRaises(ValueError):
            self.service.transfer(folder, folder)

    def test_symlink_boundary(self):
        link = self.root / 'linked'
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation needs developer mode or platform permissions.')
        self.assertFalse(self.service.inside(link / 'a.png'))
        with self.assertRaises(ValueError):
            self.service.transfer(self.image, link)
        with self.assertRaises(ValueError):
            self.service.transfer(link, self.root)


if __name__ == '__main__':
    unittest.main()

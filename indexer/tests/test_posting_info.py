"""Optional integration tests for the user's untracked posting-info script."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "posting_info.py"
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location("posting_info", SCRIPT)
    posting = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(posting)


@unittest.skipUnless(SCRIPT.exists(), "Personal posting-info script is not installed")
class PostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.library = self.root / "CHARS"
        self.roots = {m: self.root / m for m in ("Mira", "Kim")}
        for root in self.roots.values():
            for folder in ("!Download", ".!Promote"):
                (root / folder).mkdir(parents=True)
        self.write(self.app / "roots.json", {"CHARS": str(self.library)})
        self.write(self.app / "notes.json", {"1 Mira": {"Maid": "scene", "Empty": ""}})
        self.write(self.library / "Genre" / "!F-Genre.json", {"tags": "genre"})
        for name in ("A", "B", "C", "D", "A_2"):
            self.write(self.library / "Genre" / f"{name}.json", {"tags": name + "-tag"})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def zip(self, mode, name, count=3):
        path = self.roots[mode] / "!Download" / (name + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            for i in range(count):
                archive.writestr(f"nested/{i}.png", b"image")
            archive.writestr("notes.txt", "not an image")
        return path

    def promote(self, name, characters):
        path = self.roots["Mira"] / ".!Promote" / name
        path.mkdir()
        for character in characters:
            (path / f"{character}_base_1.png").write_bytes(b"image")
        (path / "B3-Split_00003_.png").write_bytes(b"cover")
        return path

    def run_generation(self, state=None, reset=None):
        return posting.generate(state or posting.new_state(), self.roots, self.app, reset)

    def test_exact_download_and_promote_formats(self):
        self.zip("Mira", "Genre-A_2_Maid")
        self.zip("Kim", "A;Genre")
        (self.roots["Kim"] / ".!Promote" / "A;Genre").mkdir()
        state = self.run_generation()
        self.assertEqual(state["errors"], [])
        self.assertEqual(state["modes"]["Mira"]["Download"]["Genre-A_2_Maid"]["texts"][0]["value"],
                         "[T2] 3P A_2 - Maid")
        self.assertEqual(state["modes"]["Kim"]["Download"]["A;Genre"]["texts"][0]["value"], "A-tag A [3p]")
        self.assertEqual(state["modes"]["Kim"]["Promote"]["A;Genre"]["texts"][0]["value"], "A-tag genre BBC NTR")

    def test_mira_threshold_variants_dot_and_empty_scene(self):
        self.promote(".Genre_Maid_3-72P", ["C", "A_2", "B"])
        self.promote("Genre_Empty_4-72P", ["D", "C", "B", "A"])
        state = self.run_generation()
        self.assertEqual(state["errors"], [])
        entries = state["modes"]["Mira"]["Promote"]
        self.assertEqual(entries["Genre_Maid_3-72P"]["texts"][0]["value"], "genre A_2-tag B-tag C-tag scene")
        self.assertEqual([t["value"] for t in entries["Genre_Empty_4-72P"]["texts"]],
                         ["genre", "A-tag B-tag C-tag D-tag"])

    def test_collect_all_errors_and_repair_on_rerun(self):
        self.promote("Genre_Missing_3-72P", ["A", "B", "Unknown"])
        self.write(self.library / "Genre" / "A.json", {})
        self.write(self.library / "Genre" / "B.json", {"tags": ""})
        state = self.run_generation()
        entry = state["modes"]["Mira"]["Promote"]["Genre_Missing_3-72P"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertEqual(entry["texts"], [{"label": "Tags", "value": "genre"}])
        self.assertEqual(len(entry["errors"]), 4)
        for name in ("A", "B", "Unknown"):
            self.write(self.library / "Genre" / f"{name}.json", {"tags": name})
        self.write(self.app / "notes.json", {"1 Mira": {"Missing": ""}})
        state = self.run_generation(state)
        self.assertEqual(state["errors"], [])
        self.assertEqual(state["modes"]["Mira"]["Promote"]["Genre_Missing_3-72P"]["status"], "complete")

    def test_counter_reruns_resets_and_deleted_entries(self):
        first = self.zip("Kim", "20260927-OC_3p")
        state = self.run_generation()
        self.assertEqual(state["modes"]["Kim"]["Download"][first.stem]["texts"][0]["value"], "Vol. 002 - 3p [DL Only]")
        state = self.run_generation(state)
        self.assertEqual(state["counter"]["next"], 3)
        second = self.zip("Kim", "20260927-OC_3p-1")
        state = self.run_generation(state, reset=10)
        self.assertEqual(state["modes"]["Kim"]["Download"][first.stem]["volume"], 2)
        self.assertEqual(state["modes"]["Kim"]["Download"][second.stem]["volume"], 10)
        first.unlink()
        state = self.run_generation(state)
        self.assertNotIn(first.stem, state["modes"]["Kim"]["Download"])
        self.assertEqual(state["counter"]["next"], 11)
        self.zip("Kim", first.stem)
        state = self.run_generation(state)
        self.assertEqual(state["modes"]["Kim"]["Download"][first.stem]["volume"], 2)

    def test_unavailable_location_preserves_entries_but_empty_scan_removes(self):
        path = self.zip("Mira", "Genre-A_Maid")
        state = self.run_generation()
        original = copy.deepcopy(state["modes"]["Mira"]["Download"])
        path.parent.rename(path.parent.with_name("offline"))
        state = self.run_generation(state)
        self.assertEqual(state["modes"]["Mira"]["Download"], original)
        self.assertTrue(any(e["code"] == "location" for e in state["errors"]))
        path.parent.mkdir()
        state = self.run_generation(state)
        self.assertEqual(state["modes"]["Mira"]["Download"], {})

    def test_corrupt_state_is_not_reset(self):
        path = self.app / "posting_info.json"
        self.write(path, {"version": 1, "modes": {}, "counter": {}})
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            posting.load_state(path)
        self.assertEqual(path.read_bytes(), before)

    def test_nested_parent_tags_deepest_first_once(self):
        for name, subfolder in (("Nested", "Generation/Region"), ("Second", "Generation/Region")):
            self.write(self.library / "Genre" / subfolder / f"{name}.json", {"tags": name})
        self.write(self.library / "Genre" / "Generation" / "!F-Generation.json", {"tags": "generation"})
        self.write(self.library / "Genre" / "Generation" / "Region" / "!F-Region.json", {"tags": "region"})
        self.promote("Genre_Maid_2-72P", ["Nested", "Second"])
        (self.roots["Kim"] / ".!Promote" / "Nested;Genre").mkdir()
        state = self.run_generation()
        self.assertEqual(state["errors"], [])
        self.assertEqual(state["modes"]["Mira"]["Promote"]["Genre_Maid_2-72P"]["texts"][0]["value"],
                         "region generation genre Nested Second scene")
        self.assertEqual(state["modes"]["Kim"]["Promote"]["Nested;Genre"]["texts"][0]["value"],
                         "Nested region generation genre BBC NTR")

    def test_duplicate_nested_character_is_not_guessed(self):
        self.write(self.library / "Genre" / "Other" / "A.json", {"tags": "different"})
        self.zip("Kim", "A;Genre")
        state = self.run_generation()
        entry = state["modes"]["Kim"]["Download"]["A;Genre"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertIn("ambiguous", entry["errors"][0]["message"])

    def test_case_mismatch_is_an_error(self):
        self.zip("Kim", "A;genre")
        state = self.run_generation()
        self.assertEqual(state["modes"]["Kim"]["Download"]["A;genre"]["status"], "incomplete")

    def test_volume_setting_change_and_disk_roundtrip(self):
        first = self.zip("Kim", "20260927-OC_3p")
        state = self.run_generation()
        path = self.app / "posting_info.json"
        posting.atomic_save(path, state)
        state = posting.load_state(path)
        second = self.zip("Kim", "20260928-OC_3p")
        previous = posting.KIM_OC_START_VOLUME
        try:
            posting.KIM_OC_START_VOLUME = 20
            state = self.run_generation(state)
            self.assertEqual(state["modes"]["Kim"]["Download"][first.stem]["volume"], 2)
            self.assertEqual(state["modes"]["Kim"]["Download"][second.stem]["volume"], 20)
            self.assertEqual(self.run_generation(state)["counter"]["next"], 21)
        finally:
            posting.KIM_OC_START_VOLUME = previous

    def test_corrupt_zip_does_not_consume_volume_or_stop_other_entries(self):
        bad = self.roots["Kim"] / "!Download" / "20260927-OC_3p.zip"
        bad.write_bytes(b"not a zip")
        good = self.zip("Kim", "20260928-OC_3p")
        state = self.run_generation()
        self.assertEqual(state["modes"]["Kim"]["Download"][bad.stem]["status"], "incomplete")
        self.assertEqual(state["modes"]["Kim"]["Download"][good.stem]["volume"], 2)

    def test_promote_dot_rename_and_removal(self):
        path = self.promote("Genre_Maid_1-72P", ["A"])
        state = self.run_generation()
        renamed = path.with_name("." + path.name)
        path.rename(renamed)
        state = self.run_generation(state)
        self.assertEqual(list(state["modes"]["Mira"]["Promote"]), [path.name])
        renamed.rename(self.root / "finished")
        state = self.run_generation(state)
        self.assertEqual(state["modes"]["Mira"]["Promote"], {})

    def test_state_lock_prevents_overlap_and_releases(self):
        path = self.app / "posting_info.json"
        with posting.state_lock(path):
            with self.assertRaises(ValueError):
                with posting.state_lock(path):
                    self.fail("Concurrent state writer was allowed")
        with posting.state_lock(path):
            posting.atomic_save(path, posting.new_state())
        self.assertEqual(posting.load_state(path)["counter"]["next"], 2)

    def test_folder_metadata_exemption_persists_and_does_not_hide_character_errors(self):
        self.promote("Genre_Maid_1-72P", ["A"])
        folder = self.library / "Genre"
        (folder / "!F-Genre.json").unlink()
        self.write(folder / "A.json", {})
        state = self.run_generation()
        entry = state["modes"]["Mira"]["Promote"]["Genre_Maid_1-72P"]
        self.assertEqual({e["code"] for e in entry["errors"]}, {"folder_metadata", "character_tags"})
        posting.ignore_folder_error(state, folder)
        path = self.app / "posting_info.json"
        posting.atomic_save(path, state)
        state = self.run_generation(posting.load_state(path))
        entry = state["modes"]["Mira"]["Promote"]["Genre_Maid_1-72P"]
        self.assertEqual([e["code"] for e in entry["errors"]], ["character_tags"])
        self.assertEqual(entry["texts"][0]["value"], "scene")
        self.write(folder / "A.json", {"tags": "A"})
        state = self.run_generation(state)
        self.assertFalse(state["errors"])
        self.assertEqual(state["modes"]["Mira"]["Promote"]["Genre_Maid_1-72P"]["texts"][0]["value"], "A scene")
        # Exemption skips errors, not valid tags added later.
        self.write(folder / "!F-Genre.json", {"tags": "restored"})
        state = self.run_generation(state)
        self.assertEqual(state["modes"]["Mira"]["Promote"]["Genre_Maid_1-72P"]["texts"][0]["value"], "restored A scene")

    def test_cannot_exempt_a_character_or_unknown_folder(self):
        self.zip("Kim", "Missing;Genre")
        state = self.run_generation()
        with self.assertRaises(ValueError):
            posting.ignore_folder_error(state, self.library / "Genre")
        self.assertEqual(state["ignored_folder_metadata"], [])

    def test_reports_group_and_deduplicate_causes(self):
        self.promote("Genre_Maid_1-72P", ["A"])
        self.promote("Genre_Empty_1-72P", ["A"])
        (self.library / "Genre" / "!F-Genre.json").unlink()
        state = self.run_generation()
        reports = list(posting.error_reports(state["errors"]))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].count("Missing folder metadata"), 1)
        self.assertIn("Genre_Maid_1-72P", reports[0])
        self.assertIn("Genre_Empty_1-72P", reports[0])
        self.assertIn("1 issue", reports[0])


if __name__ == "__main__":
    unittest.main()

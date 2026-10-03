"""Real 7-Zip integration tests. All inputs/passwords are disposable test data.

Run: python -m unittest discover -s backup/tests -v
Set VAEL_TEST_7ZIP to test another compatible archiver executable.
"""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup
import protect

PASSWORD = "Test-only pässphrase 🔒 123!"
SECOND_PASSWORD = "Second test-only password 456!"
EXE = os.environ.get("VAEL_TEST_7ZIP") or shutil.which("7z") or shutil.which("7zz")


@unittest.skipUnless(EXE, "7-Zip executable required")
class ProtectionIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vael-test-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.close_logs)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        self.source = self.root / "physical"
        self.source.mkdir()
        (self.source / "héllo.txt").write_text("original content", encoding="utf-8")
        (self.source / "gone.txt").write_text("delete me")
        (self.source / "empty").mkdir()
        (self.source / "empty.txt").touch()
        self.items = [backup.BackupItem("Documents", self.source)]
        self.archive = self.app / "Backup.7z"
        self.patch = mock.patch.object(backup, "SEVEN_ZIP_PATH", str(EXE))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.session = protect.PasswordSession(EXE, PASSWORD)
        self.addCleanup(self.session.close)

    @staticmethod
    def close_logs():
        for handler in backup.logger.handlers:
            handler.close()
        backup.logger.handlers.clear()

    def manager(self, encrypted=True):
        return backup.BackupManager(self.app, protection=self.session if encrypted else None)

    def create(self, encrypted=True):
        result = self.manager(encrypted).new_backup(self.items)
        self.assertEqual(result.exit_code, 0, result.message)
        return result

    def test_encrypted_create_update_verify_restore(self):
        self.create()
        protect.assert_protected(self.session, self.archive)
        self.assertTrue(protect.needs_password(EXE, self.archive))
        initial = protect.file_hash(self.archive)
        (self.source / "héllo.txt").write_text("changed content much longer", encoding="utf-8")
        (self.source / "gone.txt").unlink()
        (self.source / "new.txt").write_text("new content")
        result = self.manager().update_backup(self.archive, self.items, False)
        self.assertEqual(result.exit_code, 0, result.message)
        self.assertNotEqual(protect.file_hash(self.archive), initial)
        protect.assert_protected(self.session, self.archive)
        verification = backup.VerificationManager(self.manager().runner, backup.ManifestManager()).verify(self.archive, self.manager().history)
        self.assertTrue(verification.ok, verification.summary)
        self.assertTrue(verification.is_latest, verification.summary)
        destination = self.root / "restored"
        protect.restore(self.archive, destination, exe=EXE, password=PASSWORD)
        self.assertEqual((destination / "Documents" / "héllo.txt").read_text(encoding="utf-8"), "changed content much longer")
        self.assertFalse((destination / "Documents" / "gone.txt").exists())
        self.assertTrue((destination / "Documents" / "empty").is_dir())
        self.assertEqual((destination / "Documents" / "empty.txt").stat().st_size, 0)

    def test_plain_backup_still_works(self):
        self.create(False)
        self.assertFalse(protect.needs_password(EXE, self.archive))
        result = self.manager(False).update_backup(self.archive, self.items, False)
        self.assertEqual(result.exit_code, 0, result.message)

    def test_wrong_password_preserves_archive(self):
        self.create()
        initial = protect.file_hash(self.archive)
        with self.assertRaises(protect.ProtectionError):
            protect.open_session(EXE, self.archive, "incorrect")
        self.assertEqual(protect.file_hash(self.archive), initial)
        self.assertFalse(list(self.app.glob("*.new")))

    def test_standalone_conversion_password_change_and_decrypt(self):
        self.create(False)
        original = protect.file_hash(self.archive)
        encrypted = self.app / "protected.7z"
        protect.transform(self.archive, encrypted, exe=EXE, new_password=PASSWORD)
        self.assertEqual(protect.file_hash(self.archive), original)
        protect.assert_protected(self.session, encrypted)
        changed = self.app / "changed.7z"
        protect.transform(encrypted, changed, exe=EXE, password=PASSWORD, new_password=SECOND_PASSWORD, mode="password")
        with self.assertRaises(protect.ProtectionError):
            protect.open_session(EXE, changed, PASSWORD)
        decrypted = self.app / "plain.7z"
        protect.transform(changed, decrypted, exe=EXE, password=SECOND_PASSWORD, mode="decrypt")
        self.assertFalse(protect.needs_password(EXE, decrypted))
        result = self.manager(False).update_backup(decrypted, self.items, False)
        self.assertEqual(result.exit_code, 0, result.message)

    def test_generic_file_roundtrip(self):
        source = self.root / "report.bin"
        source.write_bytes(bytes(range(256)) * 100)
        archive = self.app / "file.7z"
        protect.transform(source, archive, exe=EXE, new_password=PASSWORD)
        destination = self.root / "files"
        protect.restore(archive, destination, exe=EXE, password=PASSWORD)
        self.assertEqual(source.read_bytes(), (destination / source.name).read_bytes())

    def test_both_tools_share_the_same_lock(self):
        self.create()
        with protect.archive_lock(self.archive):
            result = self.manager().update_backup(self.archive, self.items, False)
            self.assertFalse(result.ok)
        with backup._CrossPlatformLock(self.app / ".Backup.7z.lock", blocking=False):
            with self.assertRaises(protect.ProtectionError):
                with protect.archive_lock(self.archive):
                    self.fail("lock should be busy")
        # Persistent lock sidecars must still lock byte zero on subsequent runs.
        result = self.manager().update_backup(self.archive, self.items, False)
        self.assertTrue(result.ok, result.message)

    def test_failed_validation_keeps_previous_encrypted_backup(self):
        self.create()
        initial = protect.file_hash(self.archive)
        manager = self.manager()
        with mock.patch.object(manager.txn_mgr, "validate_new_archive", return_value=(False, ["injected failure"])):
            result = manager.update_backup(self.archive, self.items, False)
        self.assertFalse(result.ok)
        self.assertEqual(protect.file_hash(self.archive), initial)
        self.assertFalse(list(self.app.glob("*.new")))

    def test_cancellation_keeps_previous_backup(self):
        self.create()
        initial = protect.file_hash(self.archive)
        manager = self.manager()
        with mock.patch.object(manager.archive_mgr, "synchronize_item", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.update_backup(self.archive, self.items, False)
        self.assertEqual(protect.file_hash(self.archive), initial)
        self.assertFalse(list(self.app.glob("*.new")))

    def test_no_overwrite(self):
        self.create()
        with self.assertRaises(protect.ProtectionError):
            protect.transform(self.archive, self.archive, exe=EXE, new_password=SECOND_PASSWORD)
        destination = self.root / "existing"
        destination.mkdir()
        with self.assertRaises(protect.ProtectionError):
            protect.restore(self.archive, destination, exe=EXE, password=PASSWORD)

    def test_backup_cli_hooks(self):
        log = self.root / "test.log"
        # Use the same module instance as the dynamic companion loader for mocks.
        with mock.patch.object(backup, "load_protection", return_value=protect), \
                mock.patch.object(protect, "prompt_password", return_value=PASSWORD), \
                mock.patch.object(backup, "BACKUP_ITEMS", self.items), \
                contextlib.redirect_stdout(io.StringIO()):
            common = ["--history", str(self.app), "--log-file", str(log)]
            self.assertEqual(backup.main(["--new", "--encrypt", "--output", str(self.archive), *common]), 0)
            self.assertEqual(backup.main(["--update", str(self.archive), *common]), 0)
            self.assertEqual(backup.main(["--verify", str(self.archive), *common]), 0)
            self.assertEqual(backup.main(["--update", str(self.archive), "--dry-run", *common]), 0)
        self.assertNotIn(PASSWORD, log.read_text(encoding="utf-8"))
        self.assertNotIn(PASSWORD, (self.app / "backup_history.txt").read_text(encoding="utf-8"))

    def test_companion_creates_and_updates_real_backup(self):
        with mock.patch.object(protect, "load_backup", return_value=backup), \
                mock.patch.object(backup, "load_protection", return_value=protect), \
                mock.patch.object(protect, "prompt_password", return_value=PASSWORD), \
                mock.patch.object(backup, "BACKUP_ITEMS", self.items), \
                mock.patch.object(backup, "_resolve_history_dir", return_value=self.app), \
                mock.patch.object(backup, "setup_logging"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(protect.main(["--new-backup", "--output", str(self.archive)]), 0)
            self.assertEqual(protect.main(["--update-backup", str(self.archive)]), 0)
        protect.assert_protected(self.session, self.archive)

    def test_two_sources_and_confirmed_configuration_changes(self):
        other = self.root / "second"
        other.mkdir()
        (other / "photo.txt").write_text("photo")
        self.items.append(backup.BackupItem("Photos", other))
        self.create()
        initial = protect.file_hash(self.archive)
        changed = [backup.BackupItem("Renamed", self.source)]
        result = self.manager().update_backup(self.archive, changed, False)
        self.assertFalse(result.ok)
        self.assertEqual(protect.file_hash(self.archive), initial)
        result = self.manager().update_backup(self.archive, changed, True)
        self.assertTrue(result.ok, result.message)
        destination = self.root / "changed-sources"
        protect.restore(self.archive, destination, exe=EXE, password=PASSWORD)
        self.assertTrue((destination / "Renamed" / "héllo.txt").exists())
        self.assertFalse((destination / "Photos").exists())
        self.assertFalse((destination / "Documents").exists())

    def test_missing_source_preserves_encrypted_backup(self):
        self.create()
        initial = protect.file_hash(self.archive)
        missing = [backup.BackupItem("Documents", self.root / "missing")]
        result = self.manager().update_backup(self.archive, missing, True)
        self.assertFalse(result.ok)
        self.assertEqual(protect.file_hash(self.archive), initial)

    def test_refuses_backup_inside_source(self):
        output = self.source / "recursive.7z"
        manager = backup.BackupManager(self.app, protection=self.session, output=output)
        result = manager.new_backup(self.items)
        self.assertFalse(result.ok)
        self.assertIn("outside", result.message)
        self.assertFalse(output.exists())

    def test_tampered_archive_is_not_restored(self):
        self.create()
        data = bytearray(self.archive.read_bytes())
        data[len(data) // 2] ^= 0x55
        self.archive.write_bytes(data)
        destination = self.root / "damaged"
        with self.assertRaises(protect.ProtectionError):
            protect.restore(self.archive, destination, exe=EXE, password=PASSWORD)
        self.assertFalse(destination.exists())

    def test_standalone_failure_never_publishes(self):
        self.create(False)
        initial = protect.file_hash(self.archive)
        output = self.app / "failed.7z"
        with mock.patch.object(protect, "assert_protected", side_effect=protect.ProtectionError("injected")):
            with self.assertRaises(protect.ProtectionError):
                protect.transform(self.archive, output, exe=EXE, new_password=PASSWORD)
        self.assertFalse(output.exists())
        self.assertEqual(protect.file_hash(self.archive), initial)
        self.assertFalse(list(self.app.glob("*.new")))

    def test_empty_directory_only_backup(self):
        empty = self.root / "empty-source"
        empty.mkdir()
        self.items = [backup.BackupItem("Empty", empty)]
        self.create()
        output = self.app / "reprotected.7z"
        protect.transform(self.archive, output, exe=EXE, password=PASSWORD, new_password=SECOND_PASSWORD)
        destination = self.root / "empty-restore"
        protect.restore(output, destination, exe=EXE, password=SECOND_PASSWORD)
        self.assertTrue((destination / "Empty").is_dir())

    def test_native_recovery_without_either_script(self):
        self.create()
        destination = self.root / "native"
        result = protect.run_process(EXE, ["x", str(self.archive), f"-o{destination}", "-y"], password=PASSWORD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination / "Documents" / "héllo.txt").read_text(encoding="utf-8"), "original content")

    def test_backup_still_runs_without_companion(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        script = Path(backup.__file__).read_text(encoding="utf-8")
        # Preserve the real program, replacing only its documented example config.
        start = script.index("BACKUP_ITEMS: list[BackupItem] = [")
        end = script.index("\n]", start) + 2
        script = script[:start] + f"BACKUP_ITEMS = [BackupItem('Documents', Path({str(self.source)!r}))]" + script[end:]
        (isolated / "backup.py").write_text(script, encoding="utf-8")
        result = subprocess.run([sys.executable, str(isolated / "backup.py"), "--new", "--plain", "--sevenzip", str(EXE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((isolated / "Backup.7z").exists())

    def test_protect_runs_without_backup_companion(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        script = isolated / "protect.py"
        shutil.copyfile(protect.__file__, script)
        self.create(False)
        result = subprocess.run([sys.executable, str(script), "--verify", str(self.archive), "--sevenzip", str(EXE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT encrypted", result.stdout)


class ProtectionUnitTests(unittest.TestCase):
    def test_reject_unsafe_archive_members(self):
        for name in ("../escape", "/absolute", "C:/escape", "file:stream", "CON.txt", "dir/../file", "file.", "a\nfile"):
            with self.subTest(name=name), self.assertRaises(protect.ProtectionError):
                protect.safe_members([{"Path": name}])
        with self.assertRaises(protect.ProtectionError):
            protect.safe_members([{"Path": "link", "Symbolic Link": "../outside"}])
        with self.assertRaises(protect.ProtectionError):
            protect.safe_members([{"Path": "A"}, {"Path": "a"}])

    def test_password_never_in_process_arguments(self):
        session = protect.PasswordSession("7z", PASSWORD)
        result = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(protect.subprocess, "run", return_value=result) as run:
            session.run(["a", "new.7z", "file.txt"])
        positional, options = run.call_args
        self.assertNotIn(PASSWORD, " ".join(positional[0]))
        self.assertNotIn("env", options)
        self.assertIn(PASSWORD, options["input"])
        self.assertNotIn(PASSWORD, repr(session))
        session.close()
        with self.assertRaises(protect.ProtectionError):
            session.run(["t", "new.7z"])

    def test_noninteractive_password_entry_fails_closed(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaises(protect.ProtectionError):
                protect.prompt_password()

    def test_reject_invalid_passwords(self):
        for password in ("", "a\nb", "a\rb", "a\x00b"):
            with self.assertRaises(protect.ProtectionError):
                protect.PasswordSession("7z", password)

    def test_encryption_companion_calls_backup(self):
        module = mock.Mock()
        module.main.return_value = 0
        with mock.patch.object(protect, "load_backup", return_value=module):
            self.assertEqual(protect.main(["--new-backup", "--output", "new.7z"]), 0)
        module.main.assert_called_once_with(["--new", "--encrypt", "--output", "new.7z"])

    def test_companion_menu_preserves_options_and_confirmation(self):
        module = mock.Mock()
        module.main.return_value = 0
        with mock.patch.object(protect, "load_backup", return_value=module), \
                mock.patch("builtins.input", side_effect=["7", "private.7z"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(protect.main(["--sevenzip", "custom-7z", "--history", "history"]), 0)
        module.main.assert_called_once_with(["--update", "private.7z", "--sevenzip", "custom-7z", "--history", "history"], interactive=True)


if __name__ == "__main__":
    unittest.main()

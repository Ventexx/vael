"""Cover regression checks using temporary data and a mocked ComfyUI server.

Run: python -B -m unittest discover -s cover/tests -v
"""
import importlib.util
import os
from pathlib import Path
import time
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

QT_APP = QApplication.instance() or QApplication([])
# Windows' offscreen Qt platform may not discover system fonts by itself.
if os.name == "nt":
    for font in ("consola.ttf", "segoeui.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
spec = importlib.util.spec_from_file_location("vael_cover", Path(__file__).parents[1] / "app.py")
cover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cover)


def drain_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QT_APP.processEvents()
        time.sleep(0.005)
    if not predicate():
        raise AssertionError("Background work did not finish")


def job(name):
    return dict(id=name, tab_name=name, server="http://test", raw_workflow={"name": name},
                image_map={}, optional_node_id=None, param_values={})


def tearDownModule():
    cover.PIXMAP_WORKER.stop()
    cover.PIXMAP_WORKER.wait()


class QueueTests(unittest.TestCase):
    def test_failure_does_not_retry_or_block_later_jobs(self):
        manager = cover.QueueManager(None)
        manager.add_item(job("bad"))
        manager.add_item(job("good"))
        calls = []

        def execute(server, workflow, *args, **kwargs):
            calls.append(workflow["name"])
            if workflow["name"] == "bad":
                raise RuntimeError("Invalid workflow")

        with patch.object(cover, "execute_workflow_sync", side_effect=execute):
            manager.run_queue()
            drain_until(lambda: not manager.running)
            self.assertEqual(calls, ["bad", "good"])
            self.assertEqual(manager.items[0]["status"], "Error")
            self.assertIn("Invalid workflow", manager.items[0]["error"])
            manager.run_queue()
            self.assertEqual(calls, ["bad", "good"])
            manager.retry_failed()
            drain_until(lambda: not manager.running)
            self.assertEqual(calls, ["bad", "good", "bad"])

    def test_pending_job_resumes_with_the_same_state(self):
        manager = cover.QueueManager(None)
        manager.add_item(job("slow"))
        states = []

        def execute(*args, run_state, **kwargs):
            states.append(run_state)
            if not run_state:
                run_state["prompt_id"] = "existing-prompt"
                raise cover.PendingRunError("Still generating")

        with patch.object(cover, "execute_workflow_sync", side_effect=execute):
            manager.run_queue()
            drain_until(lambda: not manager.running)
            self.assertEqual(manager.items[0]["status"], "Check status")
            manager.retry_failed()
            self.assertEqual(len(states), 1)
            manager.check_pending()
            drain_until(lambda: not manager.running)
        self.assertEqual(manager.items, [])
        self.assertIs(states[0], states[1])

    def test_shutdown_stops_monitoring_and_does_not_start_next_job(self):
        manager = cover.QueueManager(None)
        manager.add_item(job("active"))
        manager.add_item(job("waiting"))
        started = cover.threading.Event()
        calls = []

        def execute(*args, stop_event, **kwargs):
            calls.append(args[1]["name"])
            started.set()
            if stop_event.wait(2):
                raise cover.RunStoppedError()

        with patch.object(cover, "execute_workflow_sync", side_effect=execute):
            manager.run_queue()
            drain_until(started.is_set)
            manager.request_stop()
            drain_until(lambda: manager._thread is None)
        self.assertEqual(calls, ["active"])
        self.assertFalse(manager.running)


class OutputTrashTests(unittest.TestCase):
    def test_trash_failure_never_permanently_deletes_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.png"
            path.write_bytes(b"example")
            with patch.object(cover.QFile, "moveToTrash", return_value=(False, "")):
                with self.assertRaises(OSError):
                    cover.trash_output(path, directory)
            self.assertEqual(path.read_bytes(), b"example")

    def test_files_outside_the_output_folder_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "outputs"
            root.mkdir()
            outside = Path(directory) / "other.png"
            outside.write_bytes(b"example")
            with patch.object(cover.QFile, "moveToTrash") as trash:
                with self.assertRaises(ValueError):
                    cover.trash_output(outside, root)
                trash.assert_not_called()

    def test_partial_failure_reports_the_file_and_continues(self):
        panel = SimpleNamespace(main_window=SimpleNamespace(output_dir="outputs"), refresh=Mock())
        with patch.object(cover.QMessageBox, "question", return_value=cover.QMessageBox.Yes), \
                patch.object(cover.QMessageBox, "warning") as warning, \
                patch.object(cover, "trash_output", side_effect=[OSError("locked"), None]) as trash:
            cover.OutputsTab._trash_outputs(panel, [Path("a.png"), Path("b.png")])
            self.assertEqual(trash.call_count, 2)
            self.assertIn("a.png: locked", warning.call_args.args[2])
            panel.refresh.assert_called_once()


class OutputRefreshTests(unittest.TestCase):
    def test_background_thumbnail_is_bounded_and_refresh_preserves_unchanged_items(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.png"
            image = cover.QImage(1800, 1200, cover.QImage.Format_RGB32)
            image.fill(cover.QColor("red"))
            self.assertTrue(image.save(str(path)))
            main = SimpleNamespace(output_dir=directory, queue_manager=cover.QueueManager(None))
            panel = cover.OutputsTab(main)
            panel.resize(360, 700)
            panel.show()
            try:
                drain_until(lambda: str(path) in panel._thumbnail_cache)
                item = panel._output_items[str(path)]
                icon_key = item.icon().cacheKey()
                item.setSelected(True)
                panel.refresh()
                drain_until(lambda: not panel._scan_busy)
                self.assertIs(panel._output_items[str(path)], item)
                self.assertEqual(item.icon().cacheKey(), icon_key)
                self.assertTrue(item.isSelected())
                self.assertTrue(all(s.width() <= 280 and s.height() <= 280 for s in item.icon().availableSizes()))

                # Same filename, new content must replace the old preview.
                image.fill(cover.QColor("blue"))
                self.assertTrue(image.save(str(path)))
                stat = path.stat()
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
                panel.refresh()
                drain_until(lambda: not panel._scan_busy and not item.icon().isNull()
                            and item.icon().cacheKey() != icon_key)
                # Adding an image must not recreate existing list items.
                second = Path(directory) / "second.png"
                self.assertTrue(image.save(str(second)))
                panel.refresh()
                drain_until(lambda: not panel._scan_busy)
                self.assertEqual(panel.outputs_list.count(), 2)
                self.assertIs(panel._output_items[str(path)], item)
            finally:
                panel.stop_loading()
                panel._output_pool.waitForDone()
                panel.close()

    def test_unreadable_directory_reports_error_without_clearing_existing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            main = SimpleNamespace(output_dir=directory, queue_manager=cover.QueueManager(None))
            panel = cover.OutputsTab(main)
            try:
                drain_until(lambda: not panel._scan_busy)
                item = cover.QListWidgetItem("retained.png")
                panel.outputs_list.addItem(item)
                with patch.object(cover.os, "scandir", side_effect=PermissionError("Access denied")):
                    panel.refresh()
                    drain_until(lambda: not panel._scan_busy)
                self.assertIn("Access denied", panel.output_status.text())
                self.assertEqual(panel.outputs_list.count(), 1)
            finally:
                panel.stop_loading()
                panel._output_pool.waitForDone()
                panel.close()


class WindowSmokeTests(unittest.TestCase):
    def test_main_window_loads_and_closes_without_touching_user_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(cover.DEFAULTS, output_dir=directory, tabs=[])
            with patch.object(cover, "load_config", return_value=config), \
                    patch.object(cover, "save_config", return_value=True):
                cover.apply_style(QT_APP)
                window = cover.MainWindow()
                window.show()
                try:
                    drain_until(lambda: not window.outputs_tab._scan_busy)
                    window.outputs_sidebar.set_open(True)
                    drain_until(lambda: window.outputs_sidebar._anim.state() == cover.QPropertyAnimation.State.Stopped)
                    window.outputs_tab._set_mode(1)
                    QT_APP.processEvents()
                    preview = os.environ.get("COVER_TEST_PREVIEW")
                    if preview:
                        self.assertTrue(window.grab().save(str(Path(preview).with_stem("cover-queue-preview"))))
                    window.outputs_tab._set_mode(0)
                    QT_APP.processEvents()
                    if preview:
                        self.assertTrue(window.grab().save(preview))
                finally:
                    window.close()
                    drain_until(lambda: not window.isVisible())


class WorkflowTests(unittest.TestCase):
    def test_repeated_run_does_not_replace_the_active_worker(self):
        main = SimpleNamespace(server="server", outputs_tab=Mock())
        state = cover.WorkflowState(main)
        state.raw_workflow = {"1": {"class_type": "SaveImage", "inputs": {}}}
        started = cover.threading.Event()
        release = cover.threading.Event()
        calls = []

        def execute(*args, **kwargs):
            calls.append(1)
            started.set()
            release.wait(2)

        with patch.object(cover, "execute_workflow_sync", side_effect=execute):
            state.run_now()
            original_thread = state._thread
            try:
                drain_until(started.is_set)
                state.run_now()
                self.assertIs(state._thread, original_thread)
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                drain_until(lambda: state._thread is None)
            state.run_now()
            drain_until(lambda: state._thread is None)
            self.assertEqual(len(calls), 2)

    def test_run_is_ignored_during_shutdown(self):
        state = cover.WorkflowState(SimpleNamespace(_closing=True))
        with patch.object(cover, "RunWorker") as worker:
            state.run_now()
            worker.assert_not_called()


class ExecutionTests(unittest.TestCase):
    def test_stop_before_submission_makes_no_server_calls(self):
        stop = cover.threading.Event()
        stop.set()
        with patch.object(cover, "ComfyAPI") as api_class:
            with self.assertRaises(cover.RunStoppedError):
                cover.execute_workflow_sync("server", {}, {}, None, {}, stop_event=stop)
            api_class.assert_not_called()

    def test_timeout_reconnects_without_uploading_or_submitting_again(self):
        run_state = {}
        with patch.object(cover, "ComfyAPI") as api_class, patch.object(cover, "POLL_INTERVAL", 0), \
                patch.object(cover, "POLL_TIMEOUT", 0):
            api = api_class.return_value
            api.queue_prompt.return_value = "prompt-1"
            with self.assertRaises(cover.PendingRunError):
                cover.execute_workflow_sync("server", {}, {}, None, {}, run_state)
            self.assertEqual(run_state["prompt_id"], "prompt-1")
            api.get_history.return_value = {
                "prompt-1": {"status": {"completed": True}, "outputs": {"1": {"images": [{}]}}}}
            with patch.object(cover, "POLL_TIMEOUT", 10):
                cover.execute_workflow_sync("server", {}, {}, None, {}, run_state)
            api.queue_prompt.assert_called_once()
            api.upload_image.assert_not_called()

    def test_temporary_disconnect_keeps_polling_the_existing_prompt(self):
        with patch.object(cover, "ComfyAPI") as api_class, patch.object(cover, "POLL_INTERVAL", 0):
            api = api_class.return_value
            api.get_history.side_effect = [cover.requests.ConnectionError("offline"), {
                "p": {"status": {"completed": True}, "outputs": {"1": {"images": [{}]}}}}]
            cover.execute_workflow_sync("server", {}, {}, None, {}, {"prompt_id": "p"})
            self.assertEqual(api.get_history.call_count, 2)
            api.queue_prompt.assert_not_called()

    def test_lost_submission_response_is_not_retried(self):
        run_state = {}
        with patch.object(cover, "ComfyAPI") as api_class:
            api = api_class.return_value
            api.queue_prompt.side_effect = cover.requests.Timeout("response lost")
            for _ in range(2):
                with self.assertRaises(cover.PendingRunError):
                    cover.execute_workflow_sync("server", {}, {}, None, {}, run_state)
            api.queue_prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()

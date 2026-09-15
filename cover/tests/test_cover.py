"""Cover regression checks using temporary data and a mocked ComfyUI server.

Run: python -B -m unittest discover -s cover/tests -v
"""
import importlib.util
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

QT_APP = QApplication.instance() or QApplication([])
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

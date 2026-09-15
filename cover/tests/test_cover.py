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

        def execute(server, workflow, *args):
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


if __name__ == "__main__":
    unittest.main()

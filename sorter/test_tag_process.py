from pathlib import Path
import subprocess
import sys
import unittest

from tag_process import TagProcess


class ProcessTests(unittest.TestCase):
    def test_shutdown_terminates_worker_and_prevents_restart(self):
        client = TagProcess(Path(__file__).parent)
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        client.process = process
        try:
            client.close(permanent=True)
            self.assertIsNotNone(process.poll())
            with self.assertRaisesRegex(RuntimeError, 'shutting down'):
                client.start()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

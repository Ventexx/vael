import unittest
from unittest.mock import patch
from engine_process import engine_process_options


class EngineProcessTests(unittest.TestCase):
    def test_windows_engines_have_no_console(self):
        with patch('engine_process.sys.platform', 'win32'), patch('engine_process.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True):
            self.assertEqual(engine_process_options(), {'creationflags': 0x08000000})

    def test_other_platforms_receive_no_windows_flags(self):
        with patch('engine_process.sys.platform', 'linux'):
            self.assertEqual(engine_process_options(), {})

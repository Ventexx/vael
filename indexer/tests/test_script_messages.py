import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from script_messages import read_messages


class NotificationTests(unittest.TestCase):
    def test_only_explicit_notifications_are_displayed(self):
        output = io.BytesIO(b'progress\nVAEL_NOTIFY {"level":"error","message":"Missing tags"}\n'
                            b'normal output\nVAEL_NOTIFY {"level":"info","message":"Done"}\n')
        messages = read_messages(output, "Example")
        self.assertEqual([m["message"] for m in messages], ["Missing tags", "Done"])
        self.assertEqual(messages[0]["source"], "Example")

    def test_invalid_protocol_is_visible_without_crashing(self):
        messages = read_messages(io.BytesIO(b'VAEL_NOTIFY null\nVAEL_NOTIFY {bad}\n'), "Example")
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(m["level"] == "error" for m in messages))

    def test_long_ordinary_output_does_not_hide_following_errors(self):
        output = io.BytesIO(b'x' * 2_000_000 + b'\nVAEL_NOTIFY {"level":"error","message":"Oops"}\n')
        self.assertEqual(read_messages(output, "Example")[0]["message"], "Oops")


if __name__ == "__main__":
    unittest.main()

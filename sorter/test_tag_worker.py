import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class WorkerTests(unittest.TestCase):
    def test_silent_protocol_reuses_engine_and_stops_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Path(directory) / 'backend.py'
            backend.write_text('''
class Tagger:
    def __init__(self, progress):
        print('backend noise')
        progress('ready')
        self.count = 0
    def tags(self, path, general, character, excluded, separator):
        self.count += 1
        if path.name == 'bad.png':
            raise ValueError('broken image')
        return 'tag_' + str(self.count)
''', encoding='utf-8')
            requests = [dict(command='tag', path=name, general=.35, character=.85)
                        for name in ('one.png', 'two.png', 'bad.png', 'never.png')]
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('tag_worker.py')), str(backend)],
                                    input=''.join(json.dumps(row) + '\n' for row in requests),
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(result.returncode, 0, result.stderr)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([row['tags'] for row in events if row['event'] == 'result'], [['tag_1'], ['tag_2']])
            self.assertEqual(events[-1]['event'], 'error')
            self.assertIn('broken image', events[-1]['message'])
            self.assertIn('backend noise', result.stderr)


if __name__ == '__main__':
    unittest.main()

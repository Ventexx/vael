"""Silent reusable subprocess client, called only from background jobs."""
import json
import os
from pathlib import Path
import subprocess
import threading


class TagProcess:
    def __init__(self, app_dir):
        self.app_dir = Path(app_dir)
        self.process = None
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            bundle = self.app_dir / 'wd14-tagger'
            executable = bundle / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            backend = bundle / 'tagger.py'
            if not backend.is_file() or not executable.is_file():
                raise RuntimeError('Local WD14 setup is missing. See README → Auto-tagging setup. '
                                   'Expected wd14-tagger/tagger.py and wd14-tagger/.venv.')
            env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
                       HF_HUB_DISABLE_PROGRESS_BARS='1', HF_HUB_DISABLE_TELEMETRY='1')
            self.process = subprocess.Popen(
                [str(executable), '-u', str(self.app_dir / 'tag_worker.py'), str(backend)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', bufsize=1, env=env,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def tag(self, path, general, character, phase, model_dir=None):
        self.start()
        process = self.process
        try:
            process.stdin.write(json.dumps(dict(command='tag', path=str(path), general=general,
                                                character=character, model_dir=model_dir)) + '\n')
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f'Tagging worker exited unexpectedly (code {process.poll()}). '
                                       'Check the local backend and its dependencies.')
                event = json.loads(line)
                if event['event'] == 'phase':
                    phase(event['text'])
                elif event['event'] == 'result':
                    tags = event['tags']
                    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                        raise ValueError('Invalid tags returned by local worker.')
                    return tags
                elif event['event'] == 'error':
                    raise RuntimeError(event['message'])
                else:
                    raise ValueError('Invalid response from local worker.')
        except Exception:
            self.close()
            raise

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            if process:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                for stream in (process.stdin, process.stdout):
                    if stream:
                        stream.close()

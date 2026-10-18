"""Private JSON-lines bridge to an optional, locally supplied tagging backend.

This file contains no model implementation. The backend is never redistributed
with Sorter. Standard output is reserved for the parent application's protocol.
"""
import contextlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    output = sys.stdout

    def send(event, **values):
        output.write(json.dumps(dict(event=event, **values), ensure_ascii=False) + '\n')
        output.flush()

    backend_file = Path(sys.argv[1])
    engine = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get('command') == 'quit':
                return
            if request.get('command') != 'tag':
                raise ValueError('Unknown worker command.')
            with contextlib.redirect_stdout(sys.stderr):
                if engine is None:
                    send('phase', text='Loading local tagging backend…')
                    spec = importlib.util.spec_from_file_location('sorter_local_backend', backend_file)
                    if spec is None or spec.loader is None:
                        raise RuntimeError('Cannot load local tagging backend.')
                    backend = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(backend)
                    kwargs = {}
                    if request.get('model_dir'):
                        kwargs['model_dir'] = request['model_dir']
                    import inspect
                    if 'progress' in inspect.signature(backend.Tagger).parameters:
                        kwargs['progress'] = lambda text: send('phase', text=text)
                    engine = backend.Tagger(**kwargs)
                send('phase', text='Tagging image…')
                text = engine.tags(Path(request['path']), request['general'], request['character'], [], ' ')
                if not isinstance(text, str):
                    raise TypeError('Local backend must return a space-separated tag string.')
            send('result', tags=text.split())
        except Exception as error:
            send('error', message=f'{type(error).__name__}: {error}')
            return  # No further requests after any backend error.


if __name__ == '__main__':
    main()

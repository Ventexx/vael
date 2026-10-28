"""Sorter-owned metadata; no image data or model assets are stored here."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.avif'}


def canonical(path):
    return os.path.normcase(os.path.abspath(path))


def within(path, root):
    return Path(canonical(path)).is_relative_to(Path(canonical(root)))


def linked(path):
    path = Path(path)
    return any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
               for p in [path, *path.parents])


def identity(path):
    info = Path(path).stat()
    # Birth time helps distinguish a deleted/recreated directory after ID reuse.
    return f'{info.st_dev}:{info.st_ino}:{getattr(info, "st_birthtime_ns", 0)}'


def stamp(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def fingerprint(path, cancel=None):
    if linked(path):
        raise ValueError(f'Linked images are not supported: {path}')
    before = stamp(path)
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise InterruptedError('Stopped by user.')
            digest.update(chunk)
    if before != stamp(path):
        raise ValueError(f'Image changed while being read: {path}')
    return digest.hexdigest(), before


def tokens(text):
    return [item for item in re.split(r'[\s,]+', text.strip()) if item]


def discover(paths, cancel=None, progress=lambda text: None):
    """Strict scan: filesystem errors propagate, rather than silently skipping."""
    images, directories = {}, {}

    def visit(path):
        if cancel and cancel.is_set():
            raise InterruptedError('Stopped by user.')
        path = Path(path)
        if linked(path):
            return  # Match Sorter's existing link/junction boundary.
        info = path.stat()  # Do not confuse inaccessible with missing.
        if stat.S_ISDIR(info.st_mode):
            directories[canonical(path)] = identity(path)
            progress(f'Scanning {path}')
            with os.scandir(path) as entries:
                children = sorted(entries, key=lambda item: item.name.casefold())
            for entry in children:
                if not entry.name.startswith('.sorter-'):
                    visit(Path(entry.path))
        elif stat.S_ISREG(info.st_mode) and path.suffix.lower() in IMAGE_EXTENSIONS:
            images[canonical(path)] = path

    for path in paths:
        visit(path)
    return list(images.values()), directories


class TagStore:
    def __init__(self, filename):
        self.filename = Path(filename)
        self.lock = threading.RLock()
        self.data = {'version': 1, 'images': {}, 'paths': {}, 'folders': {}, 'links': {}}
        if self.filename.exists():
            data = json.loads(self.filename.read_text(encoding='utf-8'))
            data.setdefault('links', {})
            if not isinstance(data['links'], dict) or not all(isinstance(v, str) for v in data['links'].values()):
                raise ValueError('Invalid image links in tags.json.')
            if data.get('version') != 1 or any(not isinstance(data.get(k), dict) for k in ('images', 'paths', 'folders')):
                raise ValueError('Unsupported or damaged tags.json. Restore it from a backup before tagging.')
            for key, row in data['images'].items():
                if not isinstance(row, dict) or not isinstance(row.get('tags'), list) or not all(isinstance(t, str) for t in row['tags']):
                    raise ValueError(f'Invalid image tag record: {key}')
            for row in data['paths'].values():
                if not isinstance(row, dict) or row.get('hash') not in (data['images'].keys() | data['links'].keys()) or not isinstance(row.get('stamp'), list):
                    raise ValueError('Invalid image location record in tags.json.')
            for row in data['folders'].values():
                if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not isinstance(row.get('identity'), str):
                    raise ValueError('Invalid folder association in tags.json.')
            self.data = data

    def _write(self, data):
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        temp = self.filename.with_name(self.filename.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.filename)
        finally:
            temp.unlink(missing_ok=True)
        self.data = data

    @staticmethod
    def _prune(data):
        data['paths'] = {p: row for p, row in data['paths'].items()
                         if row['hash'] in data['images'] or row['hash'] in data['links']}
        used = {row['hash'] for row in data['paths'].values()}
        data['images'] = {key: row for key, row in data['images'].items() if key in used}
        data['links'] = {key: value for key, value in data['links'].items() if key in used}

    def has_link(self, path):
        with self.lock:
            row = self.data['paths'].get(canonical(path))
            try:
                return bool(row and row['hash'] in self.data['links'] and row['stamp'] == stamp(path))
            except OSError:
                return False

    def image_link(self, path, cancel=None):
        digest, _ = fingerprint(path, cancel)
        with self.lock:
            return self.data['links'].get(digest, '')

    def associate_link(self, path, url, expected_hash, cancel=None):
        digest, file_stamp = fingerprint(path, cancel)
        if digest != expected_hash:
            raise ValueError('Image changed while the link editor was open. Reopen Associate Link.')
        with self.lock:
            data = copy.deepcopy(self.data)
            if url:
                data['links'][digest] = url
                data['paths'][canonical(path)] = {'hash': digest, 'stamp': file_stamp}
            else:
                data['links'].pop(digest, None)
            self._prune(data)
            self._write(data)

    def record(self, path, digest, file_stamp, tags, model, general, character):
        with self.lock:
            if stamp(path) != file_stamp:
                raise ValueError(f'Image changed before its tags could be saved: {path}')
            data = copy.deepcopy(self.data)
            data['images'][digest] = dict(tags=list(dict.fromkeys(tags)), model=model,
                                          general_threshold=general, character_threshold=character,
                                          tagged_at=time.time())
            data['paths'][canonical(path)] = {'hash': digest, 'stamp': file_stamp}
            self._prune(data)
            self._write(data)

    def has_tags(self, path):
        with self.lock:
            row = self.data['paths'].get(canonical(path))
            try:
                return bool(row and row['hash'] in self.data['images'] and row['stamp'] == stamp(path))
            except OSError:
                return False

    def reuse_tags(self, path, digest, file_stamp):
        """Recognize a moved/copied image without running the model again."""
        with self.lock:
            if digest not in self.data['images']:
                return False
            if stamp(path) != file_stamp:
                raise ValueError(f'Image changed while checking saved tags: {path}')
            data = copy.deepcopy(self.data)
            data['paths'][canonical(path)] = {'hash': digest, 'stamp': file_stamp}
            self._write(data)
            return True

    def folder_text(self, path):
        with self.lock:
            row = self.data['folders'].get(canonical(path))
            return row['text'] if row and row['identity'] == identity(path) else ''

    def associate(self, path, text):
        if not Path(path).is_dir() or linked(path):
            raise ValueError('Choose an existing ordinary folder.')
        with self.lock:
            data = copy.deepcopy(self.data)
            if text.strip():
                data['folders'][canonical(path)] = {'identity': identity(path), 'text': text.strip()}
            else:
                data['folders'].pop(canonical(path), None)
            self._write(data)

    def copy_text(self, path, roots, cancel=None):
        digest, file_stamp = fingerprint(path, cancel)
        with self.lock:
            image = self.data['images'].get(digest)
            if image is None:
                raise ValueError('This image is untagged or has been replaced. Run Auto-Tag Image/s first.')
            inherited = []
            for folder in reversed(Path(path).absolute().parents):
                if any(within(folder, root) for root in roots):
                    inherited.extend(tokens(self.folder_text(folder)))
            result = ' '.join(dict.fromkeys(inherited + image['tags']))
            if stamp(path) != file_stamp:
                raise ValueError('Image changed before its tags could be copied.')
            return result

    def remap(self, old, new):
        """Known application moves/renames; health scan handles external changes."""
        old = Path(canonical(old))
        with self.lock:
            data = copy.deepcopy(self.data)
            for section in ('paths', 'folders'):
                matches = [(p, row) for p, row in data[section].items() if within(p, old)]
                for p, row in matches:
                    del data[section][p]
                    if new is not None:
                        target = Path(new) / Path(p).relative_to(old)
                        if target.exists():
                            row = dict(row)
                            if section == 'folders':
                                row['identity'] = identity(target)
                            else:
                                row['stamp'] = stamp(target)
                            data[section][canonical(target)] = row
            # Do not prune until a complete scan can find remaining copies.
            self._write(data)

    def health(self, roots, cancel=None, progress=lambda text: None):
        """Scan all roots before pruning; preserve data for failed/inaccessible roots."""
        with self.lock:
            original = copy.deepcopy(self.data)
        if not original['images'] and not original['folders'] and not original['links']:
            return []
        paths, folders, failed, errors = {}, {}, [], []
        for root in dict.fromkeys(canonical(p) for p in roots if p):
            try:
                images, directory_ids = discover([root], cancel, progress)
                staged = {}
                for i, path in enumerate(images):
                    if original['images'] or original['links']:
                        progress(f'Checking image {i + 1}/{len(images)}: {path.name}')
                        digest, file_stamp = fingerprint(path, cancel)
                        if digest in original['images'] or digest in original['links']:
                            staged[canonical(path)] = {'hash': digest, 'stamp': file_stamp}
                paths.update(staged)
                folders.update(directory_ids)
            except InterruptedError:
                raise
            except OSError as error:
                # A missing root may be an unplugged disk; don't delete its metadata.
                failed.append(root)
                errors.append(f'Could not check {root}; its records were retained: {error}')
            except ValueError as error:
                failed.append(root)
                errors.append(f'Could not finish checking {root}; its records were retained: {error}')
        if cancel and cancel.is_set():
            raise InterruptedError('Stopped by user.')
        with self.lock:
            # UI prevents mutation during health; guard against future callers too.
            if self.data != original:
                raise RuntimeError('Tag records changed during health check. Please run the check again.')
            data = copy.deepcopy(original)
            for path, row in original['paths'].items():
                if any(within(path, root) for root in failed):
                    paths.setdefault(path, row)
            data['paths'] = paths
            by_id = {value: key for key, value in folders.items()}
            data['folders'] = {}
            for path, row in original['folders'].items():
                relocated = by_id.get(row['identity'])
                if relocated:
                    data['folders'][relocated] = row
                elif any(within(path, root) for root in failed):
                    data['folders'][path] = row
            self._prune(data)
            if data != original:
                self._write(data)
        return errors

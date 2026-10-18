"""Sorter UI and queue coordinator for the optional local image tagger."""
from pathlib import Path
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from tag_process import TagProcess
from tag_store import TagStore, IMAGE_EXTENSIONS, canonical, discover, fingerprint, linked, within


class Tagging(QObject):
    def __init__(self, window, app_dir):
        super().__init__(window)
        self.window = window
        self.app_dir = Path(app_dir)
        self.task = None
        self.closing = False
        self.process = TagProcess(app_dir)
        self.process_config = None
        self.errors = []
        self.store = None
        self.load_error = None
        self.state = dict(phase='Ready', done=0, total=0, current='', started=None, ended=None)
        try:
            self.store = TagStore(self.app_dir / '.local' / 'tags.json')
        except Exception as error:
            self.load_error = str(error)
        self.button = QPushButton('Tagger: Ready')
        self.button.setFlat(True)
        self.button.setMaximumWidth(300)
        self.button.setToolTip('Tagging progress, settings and errors')
        self.button.clicked.connect(lambda: self.show())
        self.dialog = QDialog(window)
        self.dialog.setWindowTitle('Auto-Tag Images')
        self.dialog.resize(560, 400)
        layout = QVBoxLayout(self.dialog)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        overview = QWidget()
        body = QVBoxLayout(overview)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.progress = QProgressBar()
        body.addWidget(self.progress)
        self.current = QPlainTextEdit()
        self.current.setReadOnly(True)
        body.addWidget(self.current)
        controls = QHBoxLayout()
        self.stop = QPushButton('Stop after current image')
        self.stop.clicked.connect(self.cancel)
        controls.addWidget(self.stop)
        self.check = QPushButton('Check tag health')
        self.check.clicked.connect(self.health)
        controls.addWidget(self.check)
        body.addLayout(controls)
        self.tabs.addTab(overview, 'Progress')
        errors_page = QWidget()
        error_layout = QVBoxLayout(errors_page)
        self.error_text = QPlainTextEdit()
        self.error_text.setReadOnly(True)
        error_layout.addWidget(self.error_text)
        clear = QPushButton('Clear errors')
        clear.clicked.connect(self.clear_errors)
        error_layout.addWidget(clear)
        self.tabs.addTab(errors_page, 'Errors')
        settings = QWidget()
        form = QFormLayout(settings)
        preferences = window.config.get('tagging', {})
        self.general = QDoubleSpinBox()
        self.character = QDoubleSpinBox()
        for control, default, key in ((self.general, .35, 'general'), (self.character, .85, 'character')):
            control.setRange(0, 1)
            control.setDecimals(2)
            control.setSingleStep(.05)
            control.setValue(float(preferences.get(key, default)))
        form.addRow('General threshold', self.general)
        form.addRow('Character threshold', self.character)
        self.model_dir = QLineEdit(preferences.get('model_dir', ''))
        self.model_dir.setPlaceholderText('Empty = use backend model cache / download')
        form.addRow('Existing model folder', self.model_dir)
        browse = QPushButton('Choose model folder…')
        browse.clicked.connect(self.choose_model)
        form.addRow(browse)
        note = QLabel('Optional local backend required. See README → Auto-tagging setup.\n'
                      'Settings are saved when a tagging job starts. Images stay local.')
        note.setWordWrap(True)
        form.addRow(note)
        self.tabs.addTab(settings, 'Settings')
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.dialog.hide)
        layout.addWidget(close)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.render)
        self.clock.start()
        self.render()
        QTimer.singleShot(0, self.startup)

    @property
    def busy(self):
        return self.task is not None

    def roots(self):
        return list(dict.fromkeys(str(Path(p).absolute()) for p in
                    [*self.window.config.get('sources', []), self.window.config.get('sort_root', '')] if p))

    def startup(self):
        if self.load_error:
            self.state['phase'] = 'Storage unavailable'
            self.error(self.load_error)
        else:
            self.health()

    def choose_model(self):
        path = QFileDialog.getExistingDirectory(self.dialog, 'Existing model folder', self.model_dir.text())
        if path:
            self.model_dir.setText(path)

    def show(self, errors=False):
        if errors:
            self.tabs.setCurrentIndex(1)
        self.render()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def error(self, text):
        self.errors.append(f'{time.strftime("%H:%M:%S")} — {text}')
        self.errors = self.errors[-200:]
        self.error_text.setPlainText('\n\n'.join(self.errors))
        self.tabs.setTabText(1, f'Errors ({len(self.errors)})')
        if not self.closing:
            self.show(errors=True)

    def clear_errors(self):
        self.errors.clear()
        self.error_text.clear()
        self.tabs.setTabText(1, 'Errors')

    def render(self):
        state = self.state
        total, done = state['total'], state['done']
        count = f'{done}/{total}' if total else ''
        phase = state['phase']
        short_phase = ('Downloading model…' if phase.startswith('Downloading') else
                       'Loading model…' if phase.startswith('Loading') else
                       'Tagging' if phase == 'Tagging image…' else phase)
        label = f'Tagged {count}' if short_phase == 'Tagging' else f'{short_phase}{": " + count if count else ""}'
        self.button.setText('Tagger: ' + label)
        self.button.setToolTip(label + ('\n' + state['current'] if state['current'] else ''))
        elapsed = max(0, int((state.get('ended') or time.monotonic()) - state['started'])) if state['started'] else 0
        self.summary.setText(f'{phase}\nCompleted: {done} / {total}    Remaining: {max(0, total - done)}\nElapsed: {elapsed}s')
        self.current.setPlainText(state['current'])
        self.progress.setRange(0, total if total else (0 if self.busy else 1))
        self.progress.setValue(done if total else 0)
        self.stop.setEnabled(self.busy)
        self.check.setEnabled(not self.busy and self.store is not None and not self.window.operation_task)
        for control in (self.general, self.character, self.model_dir):
            control.setEnabled(not self.busy)

    def update(self, values):
        self.state.update(values)
        self.render()

    def available(self):
        if self.load_error or self.store is None:
            self.error(self.load_error or 'Tag storage is unavailable.')
            return False
        if self.busy or self.window.operation_task:
            self.error('Wait for the current tag check, tagging queue or file operation to finish.')
            return False
        return True

    def cancel(self):
        if self.task:
            self.task.cancel.set()
            self.state['phase'] = 'Stopping after current image'
            self.render()

    def _begin(self, phase):
        self.state = dict(phase=phase, done=0, total=0, current='', started=time.monotonic(), ended=None)

    def _finish(self, result):
        self.task = None
        self.state.update(result)
        self.state['ended'] = time.monotonic()
        self.render()
        for message in result.get('errors', []):
            self.error(message)

    def health(self):
        if self.closing or not self.available():
            return
        self._begin('Checking tag health')
        roots = self.roots()

        def execute(task):
            try:
                report = self._reporter(task)
                errors = self.store.health(roots, task.cancel, lambda text: report(current=text))
                return dict(phase='Health check finished' if not errors else 'Health check incomplete', errors=errors)
            except InterruptedError:
                return dict(phase='Health check stopped')
            except Exception as error:
                return dict(phase='Health check failed', errors=[str(error)])
        self.task = self.window.jobs.start(execute, self._finish,
            lambda error: self._finish(dict(phase='Health check failed', errors=[error])), partial=self.update)
        self.render()

    @staticmethod
    def _reporter(task):
        last = [0.0]
        def report(force=False, **values):
            now = time.monotonic()
            if force or now - last[0] >= .1:
                task.signals.partial.emit(values)
                last[0] = now
        return report

    def start(self, paths):
        if not self.available():
            return
        roots = self.roots()
        if any(not any(within(path, root) for root in roots) or linked(path) for path in paths):
            self.error('Select ordinary images or folders inside the configured sources or workspace.')
            return
        general, character = self.general.value(), self.character.value()
        model_dir = self.model_dir.text().strip() or None
        config = (general, character, model_dir)
        if config != self.process_config:
            self.process.close()
            self.process_config = config
        self.window.config['tagging'] = dict(general=general, character=character, model_dir=model_dir or '')
        self.window.persist()
        self._begin('Scanning images')

        def execute(task):
            done, total, current = 0, 0, ''
            try:
                report = self._reporter(task)
                images, _ = discover(paths, task.cancel, lambda text: report(current=text))
                total = len(images)
                report(force=True, total=total)
                if not images:
                    raise ValueError('No supported images were found in the selection or its subfolders.')
                for path in images:
                    if task.cancel.is_set():
                        return dict(phase='Stopped', done=done, total=total)
                    current = str(path)
                    report(force=True, current=current, phase='Checking image', done=done)
                    digest, file_stamp = fingerprint(path, task.cancel)
                    tags = self.process.tag(path, general, character,
                        lambda text: report(force=True, phase=text, current=current), model_dir)
                    # Hash again: even same-size/timestamp replacements must not receive these tags.
                    after, after_stamp = fingerprint(path)
                    if digest != after or file_stamp != after_stamp:
                        raise ValueError('Image changed during tagging; result was discarded.')
                    self.store.record(path, digest, after_stamp, tags,
                        'SmilingWolf/wd-v1-4-convnextv2-tagger-v2@v2.0', general, character)
                    done += 1
                    report(force=True, phase='Tagging', done=done)
                return dict(phase='Complete', done=done, total=total, current=current)
            except InterruptedError:
                return dict(phase='Stopped', done=done, total=total, current=current)
            except Exception as error:
                return dict(phase='Stopped on error', done=done, total=total, current=current,
                            errors=[f'{current + chr(10) if current else ""}{error}\nQueue stopped; completed results were kept.'])
        self.task = self.window.jobs.start(execute, self._finish,
            lambda error: self._finish(dict(phase='Stopped on error', errors=[error])), partial=self.update)
        self.render()

    def shutdown(self):
        self.closing = True
        self.clock.stop()
        if self.task:
            self.task.cancel.set()
        self.process.close(permanent=True)
        self.dialog.hide()

    def add_image_actions(self, menu, paths):
        if not paths or any(linked(p) or not (p.is_dir() or
                (p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)) for p in paths):
            return
        if any(not any(within(p, root) for root in self.roots()) for p in paths):
            return
        menu.addSeparator()
        action = menu.addAction('Auto-Tag Image/s', lambda: self.start(list(paths)))
        action.setEnabled(not self.busy and not self.window.operation_task)
        if len(paths) == 1 and paths[0].is_file() and self.store and self.store.has_tags(paths[0]):
            action = menu.addAction('Copy Tags', lambda: self.copy_tags(paths[0]))
            action.setEnabled(not self.busy and not self.window.operation_task)

    def copy_tags(self, path):
        if not self.available():
            return
        roots = self.roots()
        self._begin('Verifying image for Copy Tags')
        def execute(task):
            return self.store.copy_text(path, roots, task.cancel)
        def finished(text):
            if self.closing:
                return
            QApplication.clipboard().setText(text)
            self._finish(dict(phase='Tags copied', current=str(path)))
        self.task = self.window.jobs.start(execute, finished,
            lambda error: self._finish(dict(phase='Copy failed', errors=[f'{path}\n{error}'])))
        self.render()

    def add_folder_action(self, menu, paths):
        if (len(paths) == 1 and paths[0].is_dir() and not linked(paths[0])
                and any(within(paths[0], root) for root in self.roots())):
            action = menu.addAction('Associate Tags', lambda: self.associate(paths[0]))
            action.setEnabled(not self.busy and not self.window.operation_task)

    def associate(self, path):
        if not self.available():
            return
        try:
            initial = self.store.folder_text(path)
        except Exception as error:
            self.error(f'{path}\n{error}')
            return
        editor = QDialog(self.window)
        editor.setWindowTitle('Associate Tags')
        editor.resize(460, 240)
        layout = QVBoxLayout(editor)
        label = QLabel(f'{path}\nTags apply to every image in this folder and all subfolders.\n'
                       'Separate tags with spaces or commas; use underscores inside a tag.')
        label.setWordWrap(True)
        layout.addWidget(label)
        text = QPlainTextEdit(initial)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.rejected.connect(editor.reject)
        def save():
            try:
                self.store.associate(path, text.toPlainText())
                editor.accept()
            except Exception as error:
                self.error(f'Could not save folder tags for {path}: {error}')
        buttons.accepted.connect(save)
        layout.addWidget(buttons)
        self.window.exec_dialog(editor)
        editor.deleteLater()

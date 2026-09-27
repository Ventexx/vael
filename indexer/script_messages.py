"""Opt-in, line-based notifications from scripts. Ordinary stdout stays hidden."""
import json

PREFIX = "VAEL_NOTIFY "


def read_messages(output, source):
    """Read a captured binary stream without interpreting ordinary log output."""
    output.seek(0)
    messages = []
    while True:
        line = output.readline(1_048_577)
        if not line:
            break
        if len(line) > 1_048_576:
            while line and not line.endswith(b"\n"):
                line = output.readline(1_048_577)
            continue
        text = line.decode("utf-8", errors="replace")
        if not text.startswith(PREFIX):
            continue
        try:
            item = json.loads(text[len(PREFIX):])
            if (not isinstance(item, dict) or item.get("level") not in ("error", "warning", "info")
                    or not isinstance(item.get("message"), str) or not item["message"].strip()):
                raise ValueError("Expected level and nonempty message")
            messages.append({"source": source, "level": item["level"], "message": item["message"]})
        except (ValueError, TypeError):
            messages.append({"source": source, "level": "error",
                             "message": "Script emitted an invalid VAEL_NOTIFY notification."})
    return messages


def show_messages(parent, messages):
    if not messages:
        return
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton
    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle("Script notifications")
    dialog.resize(680, 440)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"{len(messages)} script notification(s)"))
    body = QPlainTextEdit()
    body.setReadOnly(True)
    body.setPlainText("\n\n".join(
        f"[{m['level'].upper()}] {m['source']}\n{m['message']}" for m in messages))
    layout.addWidget(body)
    close = QPushButton("Close")
    close.clicked.connect(dialog.close)
    layout.addWidget(close)
    dialog.show()
    return dialog

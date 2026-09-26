"""Local UI verification using the board API and an isolated session file.

Reads the saved engine path for real review tests; never writes user settings.

Run from the chess directory: python tests/preview.py
"""
import json
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app

events = []
lock = threading.Lock()


class PreviewWindow:
    def evaluate_js(self, script):
        with lock:
            events.append(script)


app.window = PreviewWindow()
engine_path = app.load_settings().get('engine_path')
app.load_settings = lambda: {'engine_path':engine_path}
app.save_settings = lambda _: None
api = app.Api(str(ROOT / 'tests' / '.preview-session.json'))

ALLOWED = {"get_state", "legal_moves", "engine_status", "get_saved_settings", "make_move", "new_game", "go_to_ply", "set_fen", "import_pgn", "export_pgn", "start_live", "stop_live", "get_live_status", "pause_live", "resume_live", "switch_live_tab", "set_view_preferences", "go_to_node", "get_review", "start_review", "cancel_review", "review_position"}
BRIDGE = """<script>
window.pywebview={api:new Proxy({}, {get:(_,name)=>async(...args)=>{
const r=await fetch('/api/'+name,{method:'POST',headers:{'X-Vael-Preview':'1'},body:JSON.stringify(args)});return r.json();}})};
setInterval(async()=>{for(const script of await(await fetch('/events')).json()) (0,eval)(script);},200);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, data, content_type="application/json"):
        data = data.encode() if isinstance(data, str) else data
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/events":
            with lock:
                pending = list(events)
                events.clear()
            return self.respond(json.dumps(pending))
        name = "index.html" if self.path == "/" else self.path.lstrip("/")
        if name not in ("index.html", "style.css", "app.js", "pieces.js", "review.js"):
            return self.send_error(404)
        data = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        if name == "index.html":
            data = data.replace('<script src="pieces.js">', BRIDGE + '<script src="pieces.js">')
        self.respond(data, {".html":"text/html; charset=utf-8", ".css":"text/css", ".js":"text/javascript"}[Path(name).suffix])

    def do_POST(self):
        name = self.path.removeprefix("/api/")
        if name not in ALLOWED or self.headers.get("X-Vael-Preview") != "1":
            return self.send_error(403)
        args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if name == "start_live" and args != [False, "browser"] and args != [True, "browser"]:
            return self.send_error(403)
        self.respond(json.dumps(getattr(api, name)(*args)))


if __name__ == "__main__":
    print("Preview ready at http://127.0.0.1:8766", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
    finally:
        api.shutdown()

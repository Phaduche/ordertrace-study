"""ORDERTRACE(VISTA-01) 연구용 로컬 서버 (파이썬 표준 라이브러리만 사용, 설치 불필요).

실행:  python3 server.py          →  http://localhost:8000 (이 컴퓨터에서만)
      python3 server.py --lan    →  같은 와이파이의 다른 기기에서도 접속 가능
  /            참가자 과제 페이지
  /admin.html  연구자 콘솔 (세션 수, 방식별 오순서 단정률, CSV)

기록은 data/study.db (SQLite) 에 저장되며 git 에 올라가지 않는다.
--lan 으로 열면 같은 네트워크의 누구나 연구자 콘솔에도 들어올 수 있으니 실험용 와이파이에서만 쓴다.
"""

import csv
import io
import json
import secrets
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "study.db"
OT_COLS = ["sid", "code", "ts", "kind", "trial_index", "attempt", "clip_id", "method", "obs_fps", "soa_ms", "profile", "truth",
           "response", "confidence", "rt_ms", "skipped_frames", "play_ms", "anomaly"]


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ot_sessions (
            sid TEXT PRIMARY KEY, code TEXT NOT NULL, created_at TEXT NOT NULL, trial_order TEXT NOT NULL,
            finished_at TEXT, confirm TEXT
        );
        CREATE TABLE IF NOT EXISTS ot_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, code TEXT, ts TEXT NOT NULL,
            kind TEXT, clip_id TEXT, method TEXT, obs_fps INTEGER, soa_ms INTEGER, profile TEXT, truth TEXT,
            response TEXT, confidence INTEGER, rt_ms INTEGER, skipped_frames INTEGER, play_ms INTEGER, anomaly INTEGER,
            trial_index INTEGER, attempt INTEGER
        );
        """
    )
    return conn


def to_csv(header, rows):
    buf = io.StringIO()
    buf.write("﻿")  # 엑셀에서 한글이 깨지지 않도록 BOM
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def wrong_definite(truth, resp):
    """실제 순서와 다른 특정 순서(red/blue)를 단정한 시행 = 단일 1차 결과."""
    return 1 if resp in ("red", "blue") and truth in ("red", "blue") and resp != truth else 0


def ordertrace_csv(c):
    rows = c.execute(f"SELECT {', '.join(OT_COLS)} FROM ot_trials ORDER BY id").fetchall()
    out = [list(r) + [wrong_definite(dict(zip(OT_COLS, r))["truth"], dict(zip(OT_COLS, r))["response"])] for r in rows]
    return to_csv(OT_COLS + ["wrong_definite"], out)


def ordertrace_summary(c):
    n_sess = c.execute("SELECT COUNT(*) FROM ot_sessions").fetchone()[0]
    n_done = c.execute("SELECT COUNT(*) FROM ot_sessions WHERE finished_at IS NOT NULL").fetchone()[0]
    by = {}
    for m, truth, resp, n in c.execute(
            "SELECT method, truth, response, COUNT(*) FROM ot_trials WHERE kind='main' GROUP BY method, truth, response"):
        b = by.setdefault(m, {"trials": 0, "wrong_definite": 0})
        b["trials"] += n
        b["wrong_definite"] += n * wrong_definite(truth, resp)
    return {"sessions": n_sess, "finished": n_done, "by_method": by}


def manifest_order():
    """manifest 의 본시행 clip id 를 무작위 순서로. manifest 가 없으면 빈 목록."""
    p = ROOT / "stimuli" / "manifest.json"
    if not p.exists():
        return []
    ids = [cl["id"] for cl in json.loads(p.read_text())["clips"] if cl["kind"] == "main"]
    secrets.SystemRandom().shuffle(ids)
    return ids


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def send_head(self):
        path = urlparse(self.path).path
        if path.startswith("/data") or path.startswith("/.git") or path.endswith(".py"):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/ot/response" not in self.path:
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, body, name):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/export/ordertrace.csv":
            with db() as c:
                return self.send_file(ordertrace_csv(c), f"ordertrace_{datetime.now():%Y%m%d_%H%M}.csv")
        if path == "/api/ot/summary":
            with db() as c:
                return self.send_json(ordertrace_summary(c))
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.send_json({"error": "bad json"}, 400)

        if path == "/api/ot/start":
            code = str(data.get("code") or "").strip().upper()[:32]
            if len(code) < 4:
                return self.send_json({"error": "code"}, 400)
            order = manifest_order()
            sid = "S" + secrets.token_hex(4).upper()
            with db() as c:
                c.execute("INSERT INTO ot_sessions (sid, code, created_at, trial_order) VALUES (?,?,?,?)", (sid, code, now(), json.dumps(order)))
            return self.send_json({"sid": sid, "order": order})

        if path == "/api/ot/response":
            num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            txt = lambda v: str(v)[:40] if v is not None else None
            with db() as c:
                c.execute(
                    "INSERT INTO ot_trials (sid, code, ts, kind, clip_id, method, obs_fps, soa_ms, profile, truth, response, confidence,"
                    " rt_ms, skipped_frames, play_ms, anomaly, trial_index, attempt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (txt(data.get("sid")), txt(data.get("code")), now(), txt(data.get("kind")), txt(data.get("clip_id")), txt(data.get("method")),
                     num(data.get("obs_fps")), num(data.get("soa_ms")), txt(data.get("profile")), txt(data.get("truth")), txt(data.get("response")),
                     num(data.get("confidence")), num(data.get("rt_ms")), num(data.get("skipped_frames")), num(data.get("play_ms")),
                     num(data.get("anomaly")), num(data.get("index")), num(data.get("attempt"))))
            return self.send_json({"ok": True})

        if path == "/api/ot/finish":
            sid = str(data.get("sid") or "")
            with db() as c:
                r = c.execute("SELECT confirm FROM ot_sessions WHERE sid=?", (sid,)).fetchone()
                if not r:
                    return self.send_json({"error": "unknown sid"}, 404)
                confirm = r["confirm"] or ("V" + secrets.token_hex(3).upper())
                c.execute("UPDATE ot_sessions SET finished_at=COALESCE(finished_at, ?), confirm=? WHERE sid=?", (now(), confirm, sid))
            return self.send_json({"confirm": confirm})

        return self.send_json({"error": "not found"}, 404)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[0]) if args else 8000
    lan = "--lan" in sys.argv
    db().close()
    print(f"ORDERTRACE 서버 실행 중: http://localhost:{port}  (연구자 콘솔: http://localhost:{port}/admin.html)")
    if lan:
        ip = lan_ip()
        print(f"같은 와이파이의 다른 기기에서: http://{ip}:{port}" if ip else "와이파이 IP를 찾지 못했습니다.")
    ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler).serve_forever()

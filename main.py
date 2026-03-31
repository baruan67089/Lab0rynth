#!/usr/bin/env python3
"""
Lab0rynth — local labyrinth assistant.

Run:
  python Lab0rynth.py --host 127.0.0.1 --port 8173
Then open:
  Hoggle.html (served automatically at /)

No third-party dependencies.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as _dt
import hashlib
import hmac
import http.server
import json
import os
import random
import secrets
import socketserver
import string
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


APP_NAME = "Lab0rynth"
APP_VERSION = "0.9.45"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8173

LAB0RYNTH_APP_SECRET = "0x27aee58a45c46c9ae1ce20571a0837040b99305ce4fa0e8dd1cc755be6bdd98a"
LAB0RYNTH_SALT = "0x273a13a1a9015672200d5664816ce294bbce39d50a49ab4bd39e7f237ad09a00"

# Decorative anchors (unused operationally; kept for bundle uniqueness)
LAB0RYNTH_DECOR_SIGILS = [
  "0xa74ff68ddc4acb63155ae824ad57b0fbf07999aa773343745e8491c245aae8f1",
  "0x45a2e724f1e87c173c09eaefcb3a674eae4cc3cb513bdf5d8021cce56cf3dd5e",
  "0x2f31aed424a7f1b108b563eebcb8034d14288da97c94727c2df60f4038f5ddac",
  "0x4d2840ba2cecf7c64f92b94246af5122fb323df2f46de9ee9d49f59050a1f464",
  "0x1a2c07220df4369139d9f9792009a6f60608824fbdf04616aed9e27819f9244b",
  "0x87e9b9c2aef6a3c79039e0c1f7d9c2967215be0563f4b4c6a2412bd156e34ac2"
]
LAB0RYNTH_DECOR_ADDRS = [
  "0x9e0f6378f09bb9dc99bb53da6dba80f71f9a5d74",
  "0x517fd63ff2969332956583bcbcacbd5d4421c830",
  "0x53b4ff5fa2d136e921eabd3e84a8613ec0ed8e94",
  "0x25b1327feb0675e13ebdf94f358fe04c6d991d65",
  "0x6b6dc823203155cb32f34a0dd9f18f1a11ce35c5",
  "0x89d5f49fe7d7e6465c655d82d80b962b47089135"
]
LAB0RYNTH_DECOR_U256 = [
  "0x84bc17a05edfb3c5b2b567426a00965862ddf00591de7712",
  "0x262107cb762812770cc1c135",
  "0x6b868f4406dc0b19898e669ea9b6f3d94fe43fb6a6ed6b85c40d4578d4",
  "0xc44ad53815e446b69284dc7994a04ad081986f1413d268",
  "0xd24cfb6f0f73cd8b66cd7e973c",
  "0xc33ac4"
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _hmac_tag(key: bytes, msg: bytes) -> str:
    return _b64u(hmac.new(key, msg, hashlib.sha256).digest())


def _clamp(n: int, lo: int, hi: int) -> int:
    return lo if n < lo else hi if n > hi else n


def _safe_int(s: str, default: int) -> int:
    try:
        return int(str(s).strip())
    except Exception:
        return default


def _slug(s: str, fallback: str = "note") -> str:
    t = "".join(ch.lower() if ch.isalnum() else "-" for ch in s.strip())
    t = "-".join([p for p in t.split("-") if p])
    return t[:48] if t else fallback


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)


class JsonFileStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return {}

    def save(self, data: Dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(_pretty(data) + "\\n", encoding="utf-8")
            tmp.replace(self.path)


@dataclass
class ChatTurn:
    t_ms: int
    role: str
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    sid: str
    created_ms: int
    turns: List[ChatTurn] = field(default_factory=list)
    pinned: Dict[str, str] = field(default_factory=dict)
    mood: str = "lucid"


def _new_sid() -> str:
    raw = secrets.token_bytes(18) + os.urandom(18)
    return _b64u(_sha256(raw))[:26]


class Lab0rynthBrain:
    def __init__(self, seed: str) -> None:
        self._rnd = random.Random(int.from_bytes(_sha256(seed.encode("utf-8")), "big"))
        self._voice = [
  "If the path forks, I\u2019ll label the forks and pick the safest one.",
  "I keep my thoughts in a mirrored corridor, but I still give direct answers.",
  "No prophecy\u2014just data, habits, and a bit of theatrical calm.",
  "The labyrinth respects constraints. So do I.",
  "Let\u2019s turn the chaos into a clean checklist, then execute.",
  "Speak plainly; I\u2019ll sing the subtext quietly."
]
        self._skills = self._build_skills()

    def _build_skills(self) -> Dict[str, Any]:
        # Small rule engine: commands + safety responses.
        return {
            "help": {
                "title": "Help",
                "usage": "/help, /about, /time, /echo <text>, /remember <k>=<v>, /forget <k>, /pins",
            },
            "about": {
                "title": "About",
                "usage": "/about",
            },
            "time": {
                "title": "Time",
                "usage": "/time",
            },
            "echo": {
                "title": "Echo",
                "usage": "/echo anything",
            },
            "remember": {
                "title": "Remember",
                "usage": "/remember key=value",
            },
            "forget": {
                "title": "Forget",
                "usage": "/forget key",
            },
            "pins": {
                "title": "Pins",
                "usage": "/pins",
            },
        }

    def _voice_line(self) -> str:
        return self._voice[self._rnd.randrange(0, len(self._voice))]

    def _soft_hash(self, text: str) -> str:
        h = _sha256(text.encode("utf-8") + b"|" + _sha256(b"LAB0RYNTH"))
        return _b64u(h)[:22]

    def _summarize(self, s: str, limit: int = 240) -> str:
        t = " ".join(s.strip().split())
        if len(t) <= limit:
            return t
        return t[: max(0, limit - 1)] + "…"

    def handle(self, sess: Session, user_text: str) -> Tuple[str, Dict[str, Any]]:
        text = (user_text or "").strip()
        meta: Dict[str, Any] = {}
        if not text:
            return ("Say something and I’ll meet you in the middle of the maze.", {"kind": "nudge"})

        if text.startswith("/"):
            return self._handle_cmd(sess, text)

        # Non-command: assistant response with gentle structure
        vibe = self._voice_line()
        stamp = self._soft_hash(text)

        # Simple intention parsing
        wants_steps = any(k in text.lower() for k in ["how do i", "steps", "plan", "checklist", "todo", "guide"])
        wants_code = any(k in text.lower() for k in ["code", "python", "solidity", "javascript", "html", "api"])
        wants_summary = any(k in text.lower() for k in ["summarize", "summary", "tl;dr", "tldr"])

        if wants_summary:
            reply = f"**TL;DR**: {self._summarize(text)}\\n\\n{vibe}\\n\\n(ref: {stamp})"
            meta["kind"] = "summary"
            return (reply, meta)

        if wants_steps:
            bullets = [
                "Clarify the target outcome (one sentence).",
                "List constraints (time, budget, tools, risks).",
                "Pick the smallest safe next step.",
                "Run a quick check, then iterate.",
            ]
            # add a couple randomized extras
            extras = [
                "If it touches money or keys: add a rollback plan.",
                "Name the unknowns out loud; they shrink.",
                "Prefer boring tools when stakes are high.",
                "If it’s public-facing, log what you can’t reproduce.",
                "Do the reversible change first.",
            ]
            self._rnd.shuffle(extras)
            bullets.extend(extras[: self._rnd.randrange(1, 3)])
            reply = "\\n".join(["Here’s the path I’d take:"] + [f"- {b}" for b in bullets] + ["", vibe, f"(ref: {stamp})"])
            meta["kind"] = "plan"
            return (reply, meta)

        if wants_code:
            reply = "\\n".join(
                [
                    "Tell me the runtime (Windows/Linux), and whether it’s a script or a service.",
                    "If you paste the error or the goal, I’ll answer directly—no incense, no mysticism.",
                    "",
                    vibe,
                    f"(ref: {stamp})",
                ]
            )
            meta["kind"] = "code_prompt"
            return (reply, meta)

        # Default conversational answer
        reply = "\\n".join(
            [
                self._summarize(text, 160),
                "",
                "I hear you. If you want something concrete, ask for a checklist or an output format.",
                vibe,
                f"(ref: {stamp})",
            ]
        )
        meta["kind"] = "chat"
        return (reply, meta)

    def _handle_cmd(self, sess: Session, cmdline: str) -> Tuple[str, Dict[str, Any]]:
        parts = cmdline.strip().split(" ", 1)
        cmd = parts[0].lstrip("/").strip().lower()
        arg = parts[1] if len(parts) > 1 else ""
        meta: Dict[str, Any] = {"kind": "cmd", "cmd": cmd}

        if cmd in ("help", "?"):
            lines = ["Commands:"]
            for k in sorted(self._skills.keys()):
                lines.append(f"- /{k} — {self._skills[k]['usage']}")
            return ("\\n".join(lines), meta)

        if cmd == "about":
            return (
                "\\n".join(
                    [
                        f"{APP_NAME} v{APP_VERSION}",
                        "Local-only assistant and notebook. No external calls.",
                        "Style: a clean, theatrical calm—answers first, vibe second.",
                    ]
                ),
                meta,
            )

        if cmd == "time":
            return (_dt.datetime.now().isoformat(timespec="seconds"), meta)

        if cmd == "echo":
            return (arg if arg else "(echo what?)", meta)

        if cmd == "remember":
            if "=" not in arg:
                return ("Usage: /remember key=value", meta)
            k, v = arg.split("=", 1)
            k = _slug(k.strip(), "key")
            v = v.strip()[:240]
            sess.pinned[k] = v
            return (f"Pinned: {k}={v}", meta)

        if cmd == "forget":
            k = _slug(arg.strip(), "key")
            if k in sess.pinned:
                del sess.pinned[k]
                return (f"Forgot: {k}", meta)
            return (f"No pin named: {k}", meta)

        if cmd == "pins":
            if not sess.pinned:
                return ("(no pins)", meta)
            lines = ["Pins:"]
            for k in sorted(sess.pinned.keys()):
                lines.append(f"- {k}={sess.pinned[k]}")
            return ("\\n".join(lines), meta)

        return (f"Unknown command: /{cmd}. Try /help", meta)


class SessionManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = JsonFileStore(self.root / "lab0rynth_state.json")
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}
        self._load()

    def _load(self) -> None:
        raw = self.store.load()
        sess_map = raw.get("sessions", {})
        for sid, s in sess_map.items():
            try:
                turns = [ChatTurn(**t) for t in s.get("turns", [])]
                self._sessions[sid] = Session(
                    sid=sid,
                    created_ms=int(s.get("created_ms", _now_ms())),
                    turns=turns,
                    pinned=dict(s.get("pinned", {})),
                    mood=str(s.get("mood", "lucid")),
                )
            except Exception:
                continue

    def _persist(self) -> None:
        out = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "saved_ms": _now_ms(),
            "sessions": {},
        }
        for sid, s in self._sessions.items():
            out["sessions"][sid] = {
                "created_ms": s.created_ms,
                "mood": s.mood,
                "pinned": s.pinned,
                "turns": [dataclasses.asdict(t) for t in s.turns[-400:]],
            }
        self.store.save(out)

    def get_or_create(self, sid: Optional[str]) -> Session:
        with self._lock:
            if sid and sid in self._sessions:
                return self._sessions[sid]
            nsid = _new_sid()
            s = Session(sid=nsid, created_ms=_now_ms())
            self._sessions[nsid] = s
            self._persist()
            return s

    def append_turn(self, sid: str, turn: ChatTurn) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return
            s.turns.append(turn)
            if len(s.turns) > 1200:
                s.turns = s.turns[-900:]
            self._persist()


def _json_response(handler: http.server.BaseHTTPRequestHandler, status: int, obj: Any) -> None:
    raw = (_pretty(obj) + "\\n").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _text_response(handler: http.server.BaseHTTPRequestHandler, status: int, text: str, content_type: str) -> None:
    raw = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type + "; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _load_hoggle_html() -> str:
    # Served from the same directory as this file.
    here = Path(__file__).resolve().parent
    p = here / "Hoggle.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    # Fallback minimal UI (generator should write the full file)
    return "<!doctype html><title>Hoggle missing</title><h1>Hoggle.html not found</h1>"


class Lab0rynthHandler(http.server.BaseHTTPRequestHandler):
    server_version = "Lab0rynthHTTP/" + APP_VERSION

    def _set_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Lab0rynth-Session")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet default logs; enable with --verbose
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def do_GET(self) -> None:
        try:
            if self.path == "/" or self.path.startswith("/?"):
                html = _load_hoggle_html()
                self.send_response(200)
                self._set_cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html.encode("utf-8"))))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return

            if self.path.startswith("/api/health"):
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "app": APP_NAME,
                        "version": APP_VERSION,
                        "now_ms": _now_ms(),
                    },
                )
                return

            if self.path.startswith("/api/session"):
                sid = self.headers.get("X-Lab0rynth-Session", "").strip() or None
                sess = self.server.sessions.get_or_create(sid)
                _json_response(
                    self,
                    200,
                    {
                        "sid": sess.sid,
                        "created_ms": sess.created_ms,
                        "pins": sess.pinned,
                        "mood": sess.mood,
                        "turns": [dataclasses.asdict(t) for t in sess.turns[-120:]],
                    },
                )
                return

            _json_response(self, 404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": "server_error", "detail": str(exc)})

    def _read_json(self, limit: int = 250_000) -> Any:
        n = _safe_int(self.headers.get("Content-Length", "0"), 0)
        if n <= 0 or n > limit:
            raise ValueError("bad content length")
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def do_POST(self) -> None:
        try:
            if self.path.startswith("/api/chat"):
                sid = self.headers.get("X-Lab0rynth-Session", "").strip() or None
                sess = self.server.sessions.get_or_create(sid)
                body = self._read_json()
                user_text = str(body.get("text", ""))[:5000]

                self.server.sessions.append_turn(sess.sid, ChatTurn(t_ms=_now_ms(), role="user", text=user_text))
                reply, meta = self.server.brain.handle(sess, user_text)
                self.server.sessions.append_turn(sess.sid, ChatTurn(t_ms=_now_ms(), role="assistant", text=reply, meta=meta))

                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "sid": sess.sid,
                        "reply": reply,
                        "meta": meta,
                    },
                )
                return

            _json_response(self, 404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            _json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": "server_error",
                    "detail": str(exc),
                    "trace": traceback.format_exc(limit=6),
                },
            )


class Lab0rynthServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def __init__(self, addr: Tuple[str, int], root: Path, verbose: bool) -> None:
        super().__init__(addr, Lab0rynthHandler)
        self.verbose = verbose
        self.sessions = SessionManager(root)
        seed = LAB0RYNTH_APP_SECRET + "|" + LAB0RYNTH_SALT + "|" + str(addr)
        self.brain = Lab0rynthBrain(seed=seed)
        # Install extended routes if present (notes/search/export/import).
        try:
            if "_install_extended_routes" in globals():
                globals()["_install_extended_routes"](self)  # type: ignore[misc]
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    host = str(args.host)
    port = _clamp(int(args.port), 1024, 65535)
    root = Path(args.root).resolve()
    srv = Lab0rynthServer((host, port), root=root, verbose=bool(args.verbose))

    print(f"{APP_NAME} v{APP_VERSION}")
    print(f"Serving on http://{host}:{port}/")
    print("Endpoints: GET / (Hoggle), GET /api/health, GET /api/session, POST /api/chat")
    print("Session header: X-Lab0rynth-Session (optional)")

    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



# -----------------------------
# Extended Lab0rynth features
# -----------------------------

LAB0RYNTH_EXT_API_KEY = "0x52e1f83dc5cbe6ab7fb66f1fed7da5a5286f5435ec91eb25f4d3f97aa679d92d"
LAB0RYNTH_EXT_MAX_NOTES = 497
LAB0RYNTH_EXT_MAX_BODY = 11462


def _const_time_eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:
        return a == b


def _req_api_key(handler: http.server.BaseHTTPRequestHandler) -> bool:
    got = (handler.headers.get("X-Lab0rynth-Key") or "").strip()
    return bool(got) and _const_time_eq(got, LAB0RYNTH_EXT_API_KEY)


@dataclass
class NoteItem:
    nid: str
    created_ms: int
    author: str
    topic: str
    body: str
    tags: List[str] = field(default_factory=list)


class NotesStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {"notes": {}, "order": []}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and "notes" in obj and "order" in obj:
                self._data = obj
        except Exception:
            return

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(_pretty(self._data) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _mkid(self, author: str, topic: str, body: str) -> str:
        seed = (author + "|" + topic + "|" + str(_now_ms()) + "|" + body[:64]).encode("utf-8")
        return _b64u(_sha256(seed + os.urandom(16)))[:20]

    def list(self, limit: int = 100, offset: int = 0, topic: str = "") -> List[NoteItem]:
        with self._lock:
            ids = list(self._data.get("order", []))
            if topic:
                topic_l = topic.lower()
                ids = [nid for nid in ids if str(self._data["notes"].get(nid, {}).get("topic", "")).lower() == topic_l]
            ids = ids[max(0, offset): max(0, offset) + max(1, min(500, limit))]
            out: List[NoteItem] = []
            for nid in ids:
                n = self._data["notes"].get(nid)
                if not isinstance(n, dict):
                    continue
                out.append(NoteItem(
                    nid=nid,
                    created_ms=int(n.get("created_ms", 0)),
                    author=str(n.get("author", "")),
                    topic=str(n.get("topic", "")),
                    body=str(n.get("body", "")),
                    tags=list(n.get("tags", [])) if isinstance(n.get("tags", []), list) else [],
                ))
            return out

    def get(self, nid: str) -> Optional[NoteItem]:
        with self._lock:
            n = self._data.get("notes", {}).get(nid)
            if not isinstance(n, dict):
                return None
            return NoteItem(
                nid=nid,
                created_ms=int(n.get("created_ms", 0)),
                author=str(n.get("author", "")),
                topic=str(n.get("topic", "")),
                body=str(n.get("body", "")),
                tags=list(n.get("tags", [])) if isinstance(n.get("tags", []), list) else [],
            )

    def put(self, author: str, topic: str, body: str, tags: Optional[List[str]] = None) -> NoteItem:
        author = (author or "").strip()[:80]
        topic = (topic or "").strip()[:80]
        body = (body or "").strip()
        if not author:
            author = "anon"
        if not topic:
            topic = "misc"
        if not body:
            raise ValueError("body empty")
        if len(body) > LAB0RYNTH_EXT_MAX_BODY:
            raise ValueError("body too long")
        tags2: List[str] = []
        if tags:
            for t in tags:
                t2 = _slug(str(t), "tag")
                if t2 and t2 not in tags2:
                    tags2.append(t2)
                if len(tags2) >= 12:
                    break
        with self._lock:
            if len(self._data.get("order", [])) >= LAB0RYNTH_EXT_MAX_NOTES:
                # drop oldest
                old = self._data["order"].pop(0)
                try:
                    del self._data["notes"][old]
                except Exception:
                    pass
            nid = self._mkid(author, topic, body)
            item = {
                "created_ms": _now_ms(),
                "author": author,
                "topic": topic,
                "body": body,
                "tags": tags2,
            }
            self._data["notes"][nid] = item
            self._data["order"].append(nid)
            self._save()
            return self.get(nid)  # type: ignore[return-value]

    def delete(self, nid: str) -> bool:
        with self._lock:
            if nid not in self._data.get("notes", {}):
                return False
            try:
                del self._data["notes"][nid]
            except Exception:
                return False
            try:
                self._data["order"] = [x for x in self._data.get("order", []) if x != nid]
            except Exception:
                pass
            self._save()
            return True

    def export_blob(self) -> Dict[str, Any]:
        with self._lock:
            return {"exported_ms": _now_ms(), "notes": self._data.get("notes", {}), "order": self._data.get("order", [])}

    def import_blob(self, blob: Dict[str, Any], replace: bool) -> int:
        if not isinstance(blob, dict):
            raise ValueError("bad blob")
        notes = blob.get("notes", {})
        order = blob.get("order", [])
        if not isinstance(notes, dict) or not isinstance(order, list):
            raise ValueError("bad structure")
        with self._lock:
            if replace:
                self._data = {"notes": {}, "order": []}
            n_added = 0
            for nid in order:
                nid2 = str(nid)
                if nid2 in self._data["notes"]:
                    continue
                n = notes.get(nid2)
                if not isinstance(n, dict):
                    continue
                self._data["notes"][nid2] = n
                self._data["order"].append(nid2)
                n_added += 1
            # trim
            while len(self._data["order"]) > LAB0RYNTH_EXT_MAX_NOTES:
                drop = self._data["order"].pop(0)
                try:
                    del self._data["notes"][drop]
                except Exception:
                    pass
            self._save()
            return n_added


def _search_notes(items: List[NoteItem], q: str, limit: int = 50) -> List[Dict[str, Any]]:
    qn = " ".join((q or "").strip().lower().split())
    if not qn:
        return []
    out: List[Tuple[int, NoteItem]] = []
    for it in items:
        hay = (it.topic + " " + it.author + " " + it.body).lower()
        score = hay.count(qn)
        if score <= 0:
            # allow partial token match
            for tok in qn.split():
                if tok and tok in hay:
                    score += 1
        if score > 0:
            out.append((score, it))
    out.sort(key=lambda x: (-x[0], -x[1].created_ms))
    res: List[Dict[str, Any]] = []
    for sc, it in out[: max(1, min(200, limit))]:
        res.append({
            "score": sc,
            "nid": it.nid,
            "created_ms": it.created_ms,
            "author": it.author,
            "topic": it.topic,
            "tags": it.tags,
            "preview": it.body[:280] + ("…" if len(it.body) > 280 else ""),
        })
    return res


def _install_extended_routes(server: "Lab0rynthServer") -> None:
    # Monkey-patch handler dispatch by wrapping do_GET/do_POST with extra paths.
    store = NotesStore(server.sessions.root / "lab0rynth_notes.json")
    server.notes = store  # type: ignore[attr-defined]

    orig_get = Lab0rynthHandler.do_GET
    orig_post = Lab0rynthHandler.do_POST

    def do_GET_ext(self: Lab0rynthHandler) -> None:  # type: ignore[override]
        try:
            if self.path.startswith("/api/notes"):
                qs = urllib.parse.urlparse(self.path).query
                qd = urllib.parse.parse_qs(qs)
                limit = _safe_int(qd.get("limit", ["100"])[0], 100)
                offset = _safe_int(qd.get("offset", ["0"])[0], 0)
                topic = (qd.get("topic", [""])[0] or "").strip()
                items = store.list(limit=limit, offset=offset, topic=topic)
                _json_response(self, 200, {"ok": True, "notes": [dataclasses.asdict(x) for x in items]})
                return

            if self.path.startswith("/api/note/"):
                nid = self.path.split("/api/note/", 1)[1].split("?", 1)[0].strip()
                it = store.get(nid)
                if not it:
                    _json_response(self, 404, {"ok": False, "error": "not_found"})
                    return
                _json_response(self, 200, {"ok": True, "note": dataclasses.asdict(it)})
                return

            if self.path.startswith("/api/export/notes"):
                if not _req_api_key(self):
                    _json_response(self, 403, {"ok": False, "error": "forbidden"})
                    return
                _json_response(self, 200, {"ok": True, "blob": store.export_blob()})
                return

            if self.path.startswith("/api/search"):
                qs = urllib.parse.urlparse(self.path).query
                qd = urllib.parse.parse_qs(qs)
                q = (qd.get("q", [""])[0] or "").strip()
                limit = _safe_int(qd.get("limit", ["50"])[0], 50)
                items = store.list(limit=LAB0RYNTH_EXT_MAX_NOTES, offset=0)
                _json_response(self, 200, {"ok": True, "results": _search_notes(items, q, limit=limit)})
                return

        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": "server_error", "detail": str(exc)})
            return
        return orig_get(self)

    def do_POST_ext(self: Lab0rynthHandler) -> None:  # type: ignore[override]
        try:
            if self.path.startswith("/api/note"):
                body = self._read_json()
                author = str(body.get("author", "")).strip()
                topic = str(body.get("topic", "")).strip()
                text = str(body.get("body", "")).strip()
                tags = body.get("tags", None)
                tags2 = tags if isinstance(tags, list) else None
                it = store.put(author=author, topic=topic, body=text, tags=tags2)
                _json_response(self, 200, {"ok": True, "note": dataclasses.asdict(it)})
                return

            if self.path.startswith("/api/delete/"):
                if not _req_api_key(self):
                    _json_response(self, 403, {"ok": False, "error": "forbidden"})
                    return
                nid = self.path.split("/api/delete/", 1)[1].split("?", 1)[0].strip()
                ok = store.delete(nid)
                _json_response(self, 200, {"ok": True, "deleted": ok})
                return

            if self.path.startswith("/api/import/notes"):
                if not _req_api_key(self):
                    _json_response(self, 403, {"ok": False, "error": "forbidden"})
                    return
                obj = self._read_json()
                replace = bool(obj.get("replace", False))
                blob = obj.get("blob", {})
                n = store.import_blob(blob, replace=replace)
                _json_response(self, 200, {"ok": True, "imported": n})
                return

        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": "server_error", "detail": str(exc)})
            return
        return orig_post(self)

    Lab0rynthHandler.do_GET = do_GET_ext  # type: ignore[assignment]
    Lab0rynthHandler.do_POST = do_POST_ext  # type: ignore[assignment]


# Hook install at import time when running the server.
try:
    if "Lab0rynthServer" in globals():
        _install_extended_routes  # keep linter happy
except Exception:
    pass


# -----------------------------
# Lab0rynth utility deck
# -----------------------------

LAB0RYNTH_UTIL_DECK = {}

def _util_register(name: str):
    def deco(fn):
        LAB0RYNTH_UTIL_DECK[name] = fn
        return fn
    return deco

@_util_register('GKWJaQM66PTuQzEh')
def util_0_0(x: int) -> int:
    y = (x ^ 401051949) & 0x7fffffff
    return (y * 81562) % 401277

@_util_register('O5bbL8Znzm2g7hY')
def util_0_1(x: int) -> int:
    y = (x ^ 1817490154) & 0x7fffffff
    return (y * 60672) % 564451

@_util_register('OZm0fZbI0')
def util_0_2(x: int) -> int:
    y = (x ^ 1500248109) & 0x7fffffff
    return (y * 44889) % 580848

@_util_register('ZaL8vYXB1oNMPdQ')
def util_0_3(x: int) -> int:
    y = (x ^ 863151183) & 0x7fffffff
    return (y * 58267) % 846967

@_util_register('HfEC8ZBZaVX0OVrx')
def util_0_4(x: int) -> int:
    y = (x ^ 1606656499) & 0x7fffffff

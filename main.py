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

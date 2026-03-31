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

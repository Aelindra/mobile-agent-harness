# -*- coding: utf-8 -*-
"""事件面：logcat 常驻流与帧差分产生的结构化事件。

- LogcatTail：`adb logcat -b main,events` 解析为 page_change/app_start/app_crash
- FrameWatcher：帧流差分产生 visual_change（帧流开启时）
- 消费：events.tail / events.wait / subscribe_event
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional

from .runtime import Context

_MU = threading.Lock()
_TAIL: Optional["LogcatTail"] = None
_SUBS: List[Callable] = []


# ---------------- Logcat 常驻流 ----------------

_DISPLAYED_RE = re.compile(
    r"Displayed (?P<pkg>[\w.]+)/(?P<act>[\w.$]+) for user")
_PROC_RE = re.compile(r"am_proc_start \[0,(?P<pid>\d+),(?:\d+),(?P<pkg>[\w.]+),")
_CRASH_RE = re.compile(r"FATAL EXCEPTION:.*|\(Process: (?P<cpkg>[\w.]+)\)")


class LogcatTail:
    def __init__(self, serial: str, max_events: int = 200):
        self.serial = serial
        self.events: Deque[dict] = deque(maxlen=max_events)
        self._mu = threading.Lock()
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._gen = 0

    def start(self):
        self._gen += 1
        self._th = threading.Thread(target=self._loop, args=(self._gen,), daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()

    def _loop(self, gen: int):
        while not self._stop.is_set() and gen == self._gen:
            try:
                cmd = ["adb", "-s", self.serial, "logcat", "-v", "epoch",
                       "-b", "main,events", "-T", "1"]
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True,
                                     encoding="utf-8", errors="replace")
                for line in p.stdout:  # type: ignore[union-attr]
                    if self._stop.is_set() or gen != self._gen:
                        break
                    ev = self._parse(line)
                    if ev:
                        with self._mu:
                            self.events.append(ev)
                        for fn in list(_SUBS):
                            try:
                                fn(ev)
                            except Exception:
                                pass
                p.kill()
            except Exception:
                pass
            if not self._stop.is_set() and gen == self._gen:
                time.sleep(1.0)

    @staticmethod
    def _parse(line: str, _pending: List = []) -> Optional[dict]:
        ts_m = re.match(r"(\d{2}-\d{2} [\d:.]+)", line)
        ts = ts_m.group(1) if ts_m else ""
        m = _PROC_RE.search(line)
        if m:
            return {"type": "app_start", "pkg": m.group("pkg"),
                    "pid": m.group("pid"), "ts": ts}
        dm = _DISPLAYED_RE.search(line)   # MIUI 实测主信号：Displayed pkg/act
        if dm:
            fpkg = dm.group("pkg")
            if not fpkg.startswith(("android", "com.android.systemui")):
                return {"type": "page_change", "pkg": fpkg,
                        "activity": dm.group("act"), "ts": ts}
        if "Window became focused" in line or "START u0" in line or "START intent" in line:
            fpkg = None
            m2 = re.search(r"Window\{[^}]*?\s([\w.]+)(?:/[^ \}]*)?\}", line)
            m3 = re.search(r"cmp=([\w.]+)/", line)
            if m2:
                fpkg = m2.group(1)
            elif m3:
                fpkg = m3.group(1)
            if fpkg and not fpkg.startswith(("android", "com.android.systemui")):
                return {"type": "page_change", "pkg": fpkg, "ts": ts}
        if "FATAL EXCEPTION" in line:
            _pending.clear()
            _pending.append(ts)          # 包名在后续 "Process:" 行，先挂起
            return None
        if _pending and "Process:" in line:
            m4 = re.search(r"Process: ([\w.]+)", line)
            _pending.clear()
            if m4:
                return {"type": "app_crash", "pkg": m4.group(1), "ts": ts}
        return None


def get_tail(serial: str) -> Optional[LogcatTail]:
    global _TAIL
    with _MU:
        if _TAIL is None:
            try:
                _TAIL = LogcatTail(serial)
                _TAIL.start()
            except Exception:
                return None
        return _TAIL


# ---------------- 帧差分事件（游戏/canvas） ----------------

class FrameWatcher:
    """帧流上的本地变化检测：0.2s 间隔比较 320x180 灰度帧，变化超阈值发事件。"""

    def __init__(self, frames_mod, serial: str):
        self.frames = frames_mod
        self.serial = serial
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None

    def start(self):
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        import numpy as np
        from PIL import Image
        last = None
        while not self._stop.is_set():
            time.sleep(0.2)
            fs = self.frames.get(self.serial)
            if fs is None or not fs.fresh() or fs._frame is None:
                continue
            with fs._mu:
                a = np.asarray(Image.fromarray(fs._frame)
                               .convert("L").resize((320, 180)), dtype=int)
            if last is not None:
                score = float(np.abs(a - last).mean())
                if score > 8.0:
                    _emit({"type": "visual_change", "score": round(score, 2),
                           "ts": time.strftime("%H:%M:%S")})
            last = a


def _emit(ev: dict):
    with _MU:
        tail = _TAIL
    if tail is not None:
        with tail._mu:
            tail.events.append(ev)
    for fn in list(_SUBS):
        try:
            fn(ev)
        except Exception:
            pass


def subscribe_event(fn: Callable) -> Callable[[], None]:
    _SUBS.append(fn)

    def _dispose():
        if fn in _SUBS:
            _SUBS.remove(fn)
    return _dispose


# ---------------- 工具面 ----------------

def register(ctx: Context):
    b_serial = getattr(ctx.bridge, "serial", None)

    @ctx.register_tool(
        "events.tail", "事件面：最近的设备事件（page_change/app_start/app_crash/"
        "visual_change）——'刚才发生了什么'的场景感知，推送非轮询",
        {"type": "object", "properties": {
            "n": {"type": "integer", "default": 20},
            "type": {"type": "string",
                     "description": "过滤事件类型，缺省全部"}}})
    def events_tail(br, eng, a):
        tail = get_tail(b_serial) if b_serial else None
        if tail is None:
            return {"ok": False, "error": "事件流未就绪（需 adb 连接）"}
        with tail._mu:
            evs = list(tail.events)
        if a.get("type"):
            evs = [e for e in evs if e.get("type") == a["type"]]
        return {"ok": True, "count": len(evs), "events": evs[-int(a.get("n", 20)):]}

    @ctx.register_tool(
        "events.wait", "等一个匹配事件（推送，非 sleep 轮询）：type=page_change/"
        "app_start/app_crash/visual_change；可限定 pkg。事件到后仍应用帧/快照验证防误报",
        {"type": "object", "properties": {
            "type": {"type": "string"},
            "pkg": {"type": "string"},
            "timeout": {"type": "number", "default": 15}},
         "required": ["type"]})
    def events_wait(br, eng, a):
        tail = get_tail(b_serial) if b_serial else None
        if tail is None:
            return {"ok": False, "error": "事件流未就绪（需 adb 连接）"}
        want_t, want_p = a["type"], a.get("pkg")
        deadline = time.time() + float(a.get("timeout", 15))
        seen = 0
        with tail._mu:
            seen = len(tail.events)
        while time.time() < deadline:
            time.sleep(0.15)
            with tail._mu:
                evs = list(tail.events)[seen:]
            for ev in evs:
                if ev.get("type") == want_t and (not want_p or ev.get("pkg") == want_p):
                    return {"ok": True, "event": ev}
            with tail._mu:
                seen = len(tail.events)
        return {"ok": False, "error": f"等待 {want_t} 超时", "timeout": True}

    # 帧流开启时附带视觉变化事件
    if os.environ.get("MAH_FRAME_STREAM") == "1":
        try:
            from . import frames
            if b_serial:
                FrameWatcher(frames, b_serial).start()
        except Exception:
            pass


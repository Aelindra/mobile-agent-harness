# -*- coding: utf-8 -*-
"""实时监控：前台状态流 + 瞬时 Toast 捕获。

Toast 是 Android 上最常见但 adb 截图最容易错过的反馈（1-3 秒即逝）。
抓取原理：toast 显示时 WindowManager 会创建一个 TYPE_TOAST 权限的临时窗口，
其内容是一个可访问性节点——在高频轮询的 dump 里以"游离节点"出现（不属于
当前 app 包），或在 u2 的全窗口 dump 里可见。本模块用 300-500ms 高频轮询 +
窗口差异比对捕获瞬态，并落盘留证。

用法：
    m = Monitor(bridge)
    m.record_start()                    # 后台开始记录（线程）
    ... 执行被测操作（点击目标按钮等）...
    m.record_stop() -> {"toasts":[...], "frames":[...], "events":[...]}
"""
from __future__ import annotations
import json, threading, time, os
from datetime import datetime
from typing import List, Optional


class Monitor:
    def __init__(self, bridge, workdir: str = "./monitor_shots"):
        self.b = bridge
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self.toasts: List[dict] = []
        self.events: List[dict] = []

    # ---------- 单次快照 ----------
    def snapshot(self) -> dict:
        """当前前台状态 + 可访问性树摘要 + 游离窗口节点（toast/弹窗候选）。"""
        d = self.b.d
        fg = {}
        try:
            fg = d.app_current() or {}
        except Exception:
            pass
        nodes, transient = [], []
        try:
            root = self.b.dump()
            pkg = fg.get("package", "")
            for n in root.iter():
                item = {
                    "id": n.res_id, "cls": n.cls, "text": (n.text or "")[:60],
                    "desc": (n.desc or "")[:40], "center": list(n.center),
                }
                if n.package and pkg and n.package != pkg:
                    item["foreign_pkg"] = n.package
                    transient.append(item)          # 别的包的窗口 → toast/悬浮窗候选
                elif n.text or n.desc:
                    nodes.append(item)
        except Exception as e:
            transient.append({"error": str(e)[:120]})
        return {"ts": datetime.now().isoformat(timespec="milliseconds"),
                "foreground": fg, "nodes": nodes[:40], "transient": transient}

    def wait_toast(self, timeout: float = 6.0, expect: Optional[str] = None,
                   action: Optional[callable] = None) -> List[dict]:
        """同步等待 toast：先起监视线程，执行 action()（如点击），捕获 1-3 秒内出现的
        瞬态窗口节点。expect 为正则（匹配 text/desc）。"""
        found: List[dict] = []
        baseline = self._visible_texts()
        deadline = time.time() + timeout
        do_action = threading.Thread(target=action, daemon=True) if action else None
        do_action and do_action.start()
        while time.time() < deadline:
            if do_action and not do_action.is_alive() and time.time() < deadline - 0.3:
                pass
            try:
                root = self.b.dump()
                cur = set()
                for n in root.iter():
                    key = (n.res_id, n.text, n.desc)
                    cur.add(key)
                    if key in baseline or not (n.text or n.desc):
                        continue
                    hit = (n.text or "") + " " + (n.desc or "")
                    if expect and not __import__("re").search(expect, hit):
                        continue
                    found.append({"text": n.text, "desc": n.desc, "id": n.res_id,
                                  "cls": n.cls, "center": list(n.center),
                                  "ts": datetime.now().isoformat(timespec="milliseconds")})
            except Exception:
                pass
            baseline = self._visible_texts()
            if found and do_action and not do_action.is_alive():
                break
            time.sleep(0.35)
        self.toasts.extend(found)
        return found

    def _visible_texts(self):
        try:
            return {(n.res_id, n.text, n.desc) for n in self.b.dump().iter()}
        except Exception:
            return set()

    # ---------- 后台记录模式 ----------
    def record_start(self, interval: float = 0.8):
        self._stop.clear()
        self.toasts, self.events = [], []

        def loop():
            while not self._stop.is_set():
                try:
                    snap = self.snapshot()
                    self.events.append(snap)
                    shot = os.path.join(self.workdir,
                                        f"m_{datetime.now().strftime('%H%M%S_%f')[:-3]}.png")
                    self.b.d.screenshot(shot)
                except Exception:
                    pass
                self._stop.wait(interval)
        self._th = threading.Thread(target=loop, daemon=True)
        self._th.start()

    def record_stop(self) -> dict:
        self._stop.set()
        if self._th:
            self._th.join(timeout=5)
        return {"toasts": self.toasts, "frames": len(self.events),
                "dir": self.workdir,
                "transient_last": (self.events[-1]["transient"] if self.events else [])}

    def save_report(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"toasts": self.toasts, "events": self.events}, f,
                      ensure_ascii=False, indent=1)

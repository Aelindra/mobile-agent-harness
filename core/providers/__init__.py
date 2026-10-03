# -*- coding: utf-8 -*-
"""默认 adb 级 provider 集 + 组装器：把 Bridge（u2 引擎）按缝注册进 Context。

privilege/stealth 元数据对应检测面分级：
- 全部内置 provider 为 adb 级、stealth=1（u2 注入：无公开硬标记）
- root 级 provider（shell 缝第二个实例）同样由这里注册——根通道是否可用由
  Bridge.shell(root=True) 的运行时探测决定，装上 root provider 则 root 能力自动出现
- mem / proto 缝无内置 provider（私域插件提供），capabilities() 如实显示为空

内置工具（tools.py TOOLS + input/events 新原语）在组装时注册为 "builtin-tools" owner，
P1 阶段工具 handler 直连 bridge（同一信任域）；缝供插件（外部消费者）使用。
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import List, Optional

from ..bridge import Bridge
from ..harness import HarnessEngine
from ..monitor import Monitor
from ..pipeline import Pipeline, install_builtin_hooks
from ..runtime import Context, PluginRuntime
from ..transcript import Transcript

_STEALTH_INJECTED = 1  # adb/u2 注入级


# ---------------- 观测组 provider ----------------

class UiTreeProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def dump(self):
        return self.b.dump()

    def find(self, selector: str, pkg: Optional[str] = None, timeout: float = 0) -> list:
        return self.b.find(selector, pkg, timeout=timeout)


class VisionProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def screenshot(self, path: str) -> str:
        return self.b.screenshot(path)


class DataProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def prefs(self, pkg: str, name: str) -> dict:
        import xml.etree.ElementTree as ET
        xml = self.b.shell(f"cat /data/data/{pkg}/shared_prefs/{name}.xml", root=True)
        if "No such file" in xml or not xml.strip():
            raise RuntimeError(xml.strip()[:200] or "空文件")
        root = ET.fromstring(xml)
        out = {}
        for el in root.iter():
            if el.tag in ("int", "long", "boolean", "string", "float") and el.get("name"):
                out[el.get("name")] = el.get("value")
        return out

    def db(self, pkg: str, db: str, sql: Optional[str] = None) -> dict:
        import sqlite3, tempfile, time
        remote = db if db.startswith("/") else f"/data/data/{pkg}/databases/{db}"
        local = os.path.join(tempfile.gettempdir(),
                             f"hdb_{int(time.time()*1000)}_{os.path.basename(remote)}")
        b64 = self.b.shell(f"base64 '{remote}'", root=True, timeout=120)
        raw = base64.b64decode("".join(b64.split()))
        with open(local, "wb") as f:
            f.write(raw)
        con = sqlite3.connect(local)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute(
                sql or "SELECT name FROM sqlite_master WHERE type='table'").fetchmany(200)]
            return {"rows": rows, "local_copy": local}
        finally:
            con.close()


class EventsProvider:
    """设备事件流（Toast/前台变化），Monitor 的缝化包装。"""

    def __init__(self, bridge: Bridge, workdir: str = "./monitor_shots"):
        self.b = bridge
        self._mon = Monitor(bridge, workdir)

    def snapshot(self) -> dict:
        return self._mon.snapshot()

    def wait_toast(self, timeout: float = 6.0, expect: Optional[str] = None,
                   action=None) -> list:
        return self._mon.wait_toast(timeout=timeout, expect=expect, action=action)

    def record_start(self, interval: float = 0.8):
        self._mon.record_start(interval)

    def record_stop(self) -> dict:
        return self._mon.record_stop()


# ---------------- 动作组 provider ----------------

class InputProvider:
    """输入注入原语（“指定位置写入”通用层的补齐）。"""

    def __init__(self, bridge: Bridge):
        self.b = bridge

    def tap(self, x: int, y: int, duration_ms: int = 50) -> dict:
        self.b.ensure()
        if duration_ms > 120:  # 长按语义
            self.b.d.long_click(x, y, duration_ms / 1000.0)
        else:
            self.b.d.click(x, y)
        return {"ok": True, "x": x, "y": y}

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict:
        self.b.ensure()
        self.b.d.swipe(x1, y1, x2, y2, duration_ms / 1000.0)
        return {"ok": True, "from": [x1, y1], "to": [x2, y2]}

    def key(self, keycode) -> dict:
        self.b.ensure()
        self.b.d.press(keycode)   # u2 接受 'back'/'home'/... 或 int
        return {"ok": True, "key": str(keycode)}

    def text(self, s: str, selector: Optional[str] = None,
             pkg: Optional[str] = None, clear: bool = True) -> dict:
        return self.b.set_text(s, selector, pkg, clear=clear)


class ShellProvider:
    """shell 缝。privilege 在注册元数据上区分（adb 实例 / root 实例），
    root 白名单门在 pipeline（策略与实现分离）。"""

    def __init__(self, bridge: Bridge, root: bool):
        self.b, self.root = bridge, root

    def run(self, cmd: str, root: bool = False, timeout: float = 30) -> str:
        return self.b.shell(cmd, root=(root or self.root), timeout=timeout)


class FsWriteProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def push(self, local: str, remote: str, root: bool = False, chunk: int = 32768) -> dict:
        with open(local, "rb") as f:
            data = f.read()
        md5 = hashlib.md5(data).hexdigest()
        b64 = base64.b64encode(data).decode()
        self.b.shell(f"rm -f '{remote}'", root=root)
        for i in range(0, len(b64), chunk):
            self.b.shell(f"echo {b64[i:i + chunk]} | base64 -d >> '{remote}'",
                         root=root, timeout=60)
        out = self.b.shell(f"md5sum '{remote}'", root=root).strip()
        ok = md5 in out
        return {"ok": ok, "bytes": len(data), "md5": md5,
                **({} if ok else {"error": f"MD5 不一致: {out[:80]}"})}

    def write(self, path: str, data: bytes, root: bool = False) -> dict:
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tf.write(data)
            tmp = tf.name
        try:
            r = self.push(tmp, path, root=root)
            return r
        finally:
            os.unlink(tmp)

    def rm(self, path: str, root: bool = False) -> dict:
        out = self.b.shell(f"rm '{path}'", root=root)
        return {"ok": "No such file" not in out, "output": out[:200]}


class AppCtrlProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def launch(self, pkg: str, timeout: float = 15) -> dict:
        self.b.launch(pkg, timeout=timeout)
        return {"ok": True, "current": self.b.current_app()}

    def stop(self, pkg: str) -> dict:
        self.b.stop(pkg)
        return {"ok": True}

    def current(self) -> tuple:
        return self.b.current_app()

    def list_apps(self, third_party: bool = True, filter_sub: str = "") -> List[str]:
        out = self.b.shell("pm list packages -3" if third_party else "pm list packages",
                           timeout=30)
        pkgs = [ln.split(":", 1)[1].strip() for ln in out.splitlines()
                if ln.startswith("package:")]
        if filter_sub:
            pkgs = [p for p in pkgs if filter_sub.lower() in p.lower()]
        return sorted(pkgs)

    def wait_idle(self, pkg: str, timeout: float = 15) -> bool:
        return self.b.wait_idle(pkg, timeout=timeout)


class ClipboardProvider:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    def set(self, text: str) -> dict:
        self.b.ensure()
        self.b.d.set_clipboard(text)
        return {"ok": True, "length": len(text)}

    def get(self) -> str:
        self.b.ensure()
        d = self.b.d
        for way in (lambda: d.get_clipboard(), lambda: d.clipboard):
            try:
                v = way()
                if isinstance(v, str):
                    return v
            except Exception:
                continue
        raise RuntimeError("当前 u2 版本不支持剪贴板读取（set 可用）")


# ---------------- 组装器 ----------------

def register_providers(ctx: Context):
    b: Bridge = ctx.bridge
    with ctx.scoped("builtin-providers"):
        ctx.provide("ui_tree", UiTreeProvider(b), name="u2", stealth=_STEALTH_INJECTED)
        ctx.provide("vision", VisionProvider(b), name="u2-shot", stealth=_STEALTH_INJECTED)
        ctx.provide("data", DataProvider(b), name="adb-base64", stealth=_STEALTH_INJECTED)
        ctx.provide("events", EventsProvider(b), name="monitor", stealth=_STEALTH_INJECTED)
        ctx.provide("input", InputProvider(b), name="u2-input", stealth=_STEALTH_INJECTED)
        ctx.provide("shell", ShellProvider(b, root=False), name="adb",
                    privilege="adb", stealth=_STEALTH_INJECTED)
        ctx.provide("shell", ShellProvider(b, root=True), name="su",
                    privilege="root", stealth=_STEALTH_INJECTED)
        ctx.provide("fs_write", FsWriteProvider(b), name="base64-pipe",
                    stealth=_STEALTH_INJECTED)
        ctx.provide("app_ctrl", AppCtrlProvider(b), name="u2-app", stealth=_STEALTH_INJECTED)
        ctx.provide("clipboard", ClipboardProvider(b), name="u2-clip",
                    stealth=_STEALTH_INJECTED)


def register_builtin_tools(ctx: Context):
    from .. import tools as T
    from .builtin_extras import register_extras
    with ctx.scoped("builtin-tools"):
        for t in T.TOOLS.values():
            ctx.register_tool(t["name"], t["description"], t["inputSchema"], t["handler"])
        register_extras(ctx)


def _root_dir():
    # core/providers/__init__.py → 上溯三层 = agent_harness/
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _default_plugin_dirs():
    """插件目录：plugins/（本仓与社区插件的落点，热载）+ private_plugins/
    （.gitignore 挡住，root 敏感与对抗手法插件放这里，永不随仓分发）。
    环境变量 AGENT_PLUGIN_DIRS 可整体覆盖。"""
    env = os.environ.get("AGENT_PLUGIN_DIRS")
    if env:
        return [d for d in env.split(",") if d.strip()]
    root_dir = _root_dir()
    return [d for d in (os.path.join(root_dir, "plugins"),
                        os.path.join(root_dir, "private_plugins"))
            if os.path.isdir(d)]


def assemble(config: Optional[dict] = None):
    """构建完整运行时。config: {serial, transcript_path, shot_dir, plugin_dirs, harness_dir}"""
    cfg = config or {}
    root_dir = _root_dir()
    tr_path = cfg.get("transcript_path") or os.path.join(
        root_dir, "logs", f"run_{os.path.basename(sys_argv0())}_{_ts()}.jsonl")
    os.makedirs(os.path.dirname(tr_path), exist_ok=True)
    tr = Transcript(tr_path)

    ctx = Context(cfg.get("ctx_name", "phone"), transcript=tr)
    ctx.bridge = Bridge(serial=cfg.get("serial"), lazy=True)
    ctx.engine = HarnessEngine(
        ctx.bridge, harness_dir=cfg.get("harness_dir"),
        transcript=tr, shot_dir=cfg.get("shot_dir"))

    register_providers(ctx)
    register_builtin_tools(ctx)

    pipe = Pipeline(ctx, tr)
    with ctx.scoped("builtin-hooks"):
        install_builtin_hooks(pipe)

    pr = PluginRuntime(ctx, cfg.get("plugin_dirs") or _default_plugin_dirs())
    with ctx.scoped("builtin-plugin-loader"):
        pass
    loaded = pr.load_all()
    tr.log("assembled", serial=cfg.get("serial"), plugins=loaded,
           providers={k: len(v) for k, v in ctx.capabilities().items()})
    return ctx, pipe, pr


def _ts():
    import time
    return time.strftime("%Y%m%d_%H%M%S")


def sys_argv0():
    import sys
    return os.path.splitext(os.path.basename(sys.argv[0] or "mcp"))[0]

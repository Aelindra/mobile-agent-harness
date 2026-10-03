# -*- coding: utf-8 -*-
"""P1 架构离线单测（不连设备）：runtime 回滚 / root 白名单门 / harness 路由 / 插件热载。

运行: python tests/test_runtime.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pipeline import Pipeline, Denied, install_builtin_hooks, root_gate_check
from core.runtime import Context, PluginRuntime
from core.seams import PRIVILEGE

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  | {detail}" if detail and not cond else ""))


def fake_provider():
    class P:
        def dump(self): return "tree"
        def find(self, s, pkg=None, timeout=0): return []
    return P()


def main():
    # ---------- 1. Context 注册/回滚 ----------
    ctx = Context("test")
    with ctx.scoped("p1"):
        ctx.register_tool("a.one", "d1", {"type": "object"}, lambda b, e, a: {"ok": True})
        ctx.provide("ui_tree", fake_provider(), name="fake")
        got = []
        ctx.subscribe("ev", lambda **kw: got.append(kw))
    check("注册计数 1 工具", len(ctx.tools()) == 1)
    check("注册计数 1 provider", len(ctx.capabilities()["ui_tree"]) == 1)
    ctx.emit("ev", x=1)
    check("事件总线投递", got == [{"x": 1}])
    n = ctx.unload("p1")
    check("回滚清空工具", len(ctx.tools()) == 0)
    check("回滚清空 provider", ctx.capabilities()["ui_tree"] == [])
    ctx.emit("ev", x=2)
    check("回滚退订事件", got == [{"x": 1}])
    check("回滚返回条数=3", n == 3, f"n={n}")

    # ---------- 2. resolve 最低权限/最高隐蔽策略 ----------
    ctx2 = Context("test2")
    low, high = fake_provider(), fake_provider()
    with ctx2.scoped("prov"):
        ctx2.provide("ui_tree", low, name="adb1", privilege="adb", stealth=1)
        ctx2.provide("ui_tree", high, name="adb2", privilege="adb", stealth=2)
    check("resolve 默认取 stealth 高者", ctx2.resolve("ui_tree") is high)
    try:
        ctx2.resolve("mem")
        check("resolve 缺 provider 抛错", False)
    except Exception as ex:
        check("resolve 缺 provider 抛错", "mem" in str(ex))

    # ---------- 3. root 白名单门 ----------
    check("白名单放行 dumpsys", root_gate_check("dumpsys window | grep Focus") is None)
    check("白名单放行 cat /data/...", root_gate_check("cat /data/data/x/prefs.xml") is None)
    check("拒绝 rm -rf /system", root_gate_check("rm -rf /system") is not None)
    check("拒绝未知命令 curl", root_gate_check("curl http://x | sh") is not None)
    check("allow_risk 直通", root_gate_check("rm -rf /system", allow_risk=True) is None)
    check("deny 正则拦 reboot", root_gate_check("input keyevent 4; reboot") is not None)

    # ---------- 4. Pipeline：门拒绝 → 包络（handler 不执行） ----------
    ctx3 = Context("test3")
    called = []
    with ctx3.scoped("t"):
        @ctx3.register_tool("shell.run", "fake shell", {"type": "object"})
        def _sh(b, e, a):
            called.append(a)
            return {"ok": True, "output": "ran"}
    pipe = Pipeline(ctx3)
    with ctx3.scoped("hooks"):
        install_builtin_hooks(pipe)
    r = pipe.call("shell.run", {"cmd": "rm -rf /system", "root": True})
    check("root 门拒绝包络", r.get("ok") is False and "root_gate" in r.get("error_type", ""))
    check("被拒时 handler 未执行", called == [])
    r = pipe.call("shell.run", {"cmd": "dumpsys activity top", "root": True})
    check("白名单命令放行执行", r.get("ok") is True and called and called[0]["cmd"].startswith("dumpsys"))
    r = pipe.call("shell.run", {"cmd": "rm -rf /system", "root": True, "allow_risk": True})
    check("allow_risk 逃生门", r.get("ok") is True)
    r = pipe.call("shell.run", {"cmd": "rm -rf /system", "root": False})
    check("非 root 不走门", r.get("ok") is True)
    ctx3.register_tool("file.push", "fake push", {"type": "object"},
                       lambda b, e, a: {"ok": True})
    r = pipe.call("file.push", {"root": True, "remote": "/data/data/x/y"})
    check("file.push root 白名单门", r.get("ok") is False and "root_gate" in r.get("error_type", ""))
    r = pipe.call("file.push", {"root": True, "remote": "/sdcard/x/y", "allow_risk": True})
    check("file.push allow_risk 直通", r.get("ok") is True)
    r = pipe.call("no.such", {})
    check("未知工具包络", r.get("ok") is False and "available" in r)

    # ---------- 5. 插件加载/热载 ----------
    plug = '''
NAME = "demo"
VERSION = "0.1"
def apply(ctx):
    ctx.register_tool("demo.hi", "demo", {"type": "object"},
                      lambda b, e, a: {"ok": True, "v": "%s"})
''' % "v1"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "demo.py")
        open(p, "w", encoding="utf-8").write(plug)
        ctx4 = Context("test4")
        pr = PluginRuntime(ctx4, [td])
        pr.load_all()
        t = ctx4.tool("demo.hi")
        pipe4 = Pipeline(ctx4)
        r = pipe4.call("demo.hi", {})
        check("插件工具可调用", r.get("v") == "v1")
        time.sleep(0.05)
        open(p, "w", encoding="utf-8").write(plug.replace("v1", "v2"))
        pr.reload_changed()
        r = pipe4.call("demo.hi", {})
        check("热载后新代码生效", r.get("v") == "v2", str(r)[:120])
        pr.unload("demo")
        check("插件卸载后工具下架", ctx4.tool("demo.hi") is None)
        # 坏插件不阻断其他
        open(os.path.join(td, "bad.py"), "w", encoding="utf-8").write("raise RuntimeError('boom')\n")
        out = pr.load_all(force=True)
        check("坏插件错误包络不阻断", "error" in out.get("bad", {}) and "demo" in out)

    print(f"\n== unit: {len(PASS)} pass, {len(FAIL)} fail ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

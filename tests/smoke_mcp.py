# -*- coding: utf-8 -*-
"""MCP stdio server 冒烟测试：以标准 MCP 客户端身份与 server 全流程对话。

用法: python tests/smoke_mcp.py [--serial X]
覆盖: initialize(instructions) / ping / tools/list / tools/call（只读安全工具+
      file round-trip+vision diff）/ 未知工具与未知方法的错误包络。
不点任何 UI（不干扰真机当前状态）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  | {detail}" if detail and not cond else ""))


def main():
    serial = None
    if "--serial" in sys.argv:
        serial = sys.argv[sys.argv.index("--serial") + 1]
    env = dict(os.environ)
    env.pop("ANDROID_SERIAL", None)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "core", "mcp_server.py")] + (["--serial", serial] if serial else []),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=ROOT, env=env, bufsize=1)

    _id = [0]

    def rpc(method, params=None, timeout=60):
        _id[0] += 1
        i = _id[0]
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i,
                                     "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == i:
                return msg
        return {"error": "timeout"}

    def call(name, args=None, timeout=90):
        r = rpc("tools/call", {"name": name, "arguments": args or {}}, timeout)
        res = r.get("result") or {}
        try:
            payload = json.loads(res["content"][0]["text"])
        except Exception:
            payload = {"_raw": res}
        return payload, res.get("isError", True)

    try:
        # 1. initialize
        r = rpc("initialize", {"protocolVersion": "2024-11-05",
                               "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
        res = r.get("result") or {}
        check("initialize.protocolVersion", res.get("protocolVersion") == "2024-11-05")
        check("initialize.serverInfo", (res.get("serverInfo") or {}).get("name") == "mobile-agent-harness")
        check("initialize.instructions 含选择器语法", "选择器" in (res.get("instructions") or ""))

        # 2. ping + 未知方法
        check("ping", "result" in rpc("ping"))
        r = rpc("resources/list")
        check("未知方法→-32601", (r.get("error") or {}).get("code") == -32601, str(r)[:120])

        # 3. tools/list
        r = rpc("tools/list")
        tools = (r.get("result") or {}).get("tools") or []
        names = {t["name"] for t in tools}
        check("tools/list ≥28", len(tools) >= 28, f"got {len(tools)}: {sorted(names)}")
        for need in ("ui.click", "ui.wait_for", "ui.get_text", "app.list", "file.push",
                     "file.pull", "net.http", "vision.diff", "harness.check_versions",
                     "ui.snapshot", "ui.click_handle", "ui2.state"):
            check(f"工具存在 {need}", need in names)
        schemas_ok = all(isinstance(t.get("inputSchema"), dict) and t["inputSchema"].get("type") == "object"
                         for t in tools)
        check("全部工具有 object inputSchema", schemas_ok)
        wx = [t for t in tools if t["name"] == "com.android.settings__search"]
        check("harness 工具暴露且 schema 正确",
              bool(wx) and wx[0]["inputSchema"]["properties"].get("query", {}).get("type") == "string")

        # 4. 只读工具实测（真机）
        p, err = call("app.current")
        check("app.current", p.get("ok") is True and "package" in p, str(p)[:150])
        p, err = call("app.list", {"third_party": True, "filter": "settings"})
        check("app.list filter", p.get("ok") is True and any("settings" in x for x in p.get("packages", [])),
              str(p)[:150])
        p, err = call("ui.get_text", {"selector": "text@^不存在的选择器xyz$", "timeout": 1})
        check("ui.get_text 未命中→错误包络", p.get("ok") is False and "error_type" in p, str(p)[:150])

        p, err = call("ui.wait_for", {"selector": "text@^绝不存在的节点$", "timeout": 2})
        check("ui.wait_for 超时 ok=false（不抛异常）", p.get("ok") is False and p.get("condition_met") is False,
              str(p)[:150])

        p, err = call("vision.screenshot", {"path": os.path.join(ROOT, "logs", "smoke_base.png")})
        check("vision.screenshot", p.get("ok") is True and os.path.exists(p.get("path", "")), str(p)[:150])
        time.sleep(1.2)
        p2, err = call("vision.diff", {})
        check("vision.diff 缺省基线", p2.get("ok") is True and "changed_ratio" in p2, str(p2)[:200])
        p3, err = call("vision.diff", {"baseline": os.path.join(ROOT, "logs", "smoke_base.png"),
                                       "current": os.path.join(ROOT, "logs", "smoke_base.png")})
        check("vision.diff 同图 identical", p3.get("ok") is True and p3.get("identical") is True, str(p3)[:200])

        # 5. file round-trip（含中文+二进制内容）
        import base64
        blob = os.urandom(2048) + "中文内容测试".encode("utf-8")
        lp = os.path.join(ROOT, "logs", "smoke_push.bin")
        with open(lp, "wb") as f:
            f.write(blob)
        rp = "/data/local/tmp/smoke_push.bin"
        p, err = call("file.push", {"local": lp, "remote": rp})
        check("file.push md5 校验", p.get("ok") is True and p.get("chunks", 0) >= 1, str(p)[:200])
        lp2 = os.path.join(ROOT, "logs", "smoke_pull.bin")
        p, err = call("file.pull", {"remote": rp, "local": lp2})
        back = open(lp2, "rb").read() if os.path.exists(lp2) else b""
        check("file.pull round-trip 字节一致", p.get("ok") is True and back == blob, str(p)[:200])

        p, err = call("harness.check_versions")
        hh = p.get("harnesses") or []
        check("harness.check_versions", p.get("ok") is True and
              any(h.get("package") == "com.android.settings" and h.get("match") for h in hh), str(p)[:300])

        # 6. 未知工具 → 错误包络
        p, err = call("no.such_tool")
        check("未知工具错误包络", p.get("ok") is False and "available" in p, str(p)[:150])

        # 7. transcript 落盘
        logs = sorted(f for f in os.listdir(os.path.join(ROOT, "logs")) if f.startswith("mcp_"))
        check("transcript JSONL 生成", bool(logs), str(logs))

    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print(f"\n== smoke: {len(PASS)} pass, {len(FAIL)} fail ==")
    if FAIL:
        print("failed:", FAIL)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""工具执行管线：pre 钩子 → execute（设备锁内）→ post 钩子 → result 事件。

风险门/审计/结果整形均为管线钩子，工具只写业务。所有 call 返回统一包络
{"tool", "ok", "error"?, ...}，不向调用方抛异常（MCP isError 随 ok）。
"""
from __future__ import annotations

import json
import re
import time
import traceback
from typing import Callable, List, Optional, Tuple

from .runtime import Context, ToolDef
from .selectors import SelectorError, SelectorNotFound


class Denied(Exception):
    """pre 钩子拒绝执行。message 进入错误包络，error_type=denied。"""

    def __init__(self, by: str, reason: str):
        self.by, self.reason = by, reason
        super().__init__(f"[{by}] {reason}")


# ---------------- root 命令白名单门（替代旧 _RISKY 黑名单正则） ----------------

_ROOT_ALLOW_PREFIXES = (
    "settings ", "dumpsys ", "getprop", "setprop ", "cat ", "ls ", "stat ",
    "md5sum ", "sha1sum ", "base64 ", "echo ", "head ", "tail ", "grep ",
    "pm list", "pm path", "pm dump", "pm clear", "pm grant", "pm revoke",
    "am start", "am force-stop", "am broadcast", "am kill", "am stack",
    "input ", "ime ", "svc power", "svc wifi", "svc data",
    "uiautomator ", "logcat ", "top -n", "ps -", "df ", "wc ", "cp ", "mv ",
    "mkdir ", "touch ", "chmod ", "toybox ", "kill ", "date", "uptime",
)
_ROOT_DENY_PATTERNS = re.compile(
    r"\b(rm\s+-rf\s+/(?!data/local/tmp)|dd\s+if=|flash_|erase_image|"
    r"reboot|recovery|fastboot|remount|mkfs|wipe|format\s+userdata)\b")


def root_gate_check(cmd: str, allow_risk: bool = False) -> Optional[str]:
    """返回 None=放行；否则为拒绝理由。白名单优先于 deny 正则：
    命中白名单前缀 → 放行；命中 deny 模式 → 拒绝；两者都不中（未知命令）→ 拒绝。"""
    if allow_risk:
        return None
    c = cmd.strip()
    if any(c.startswith(p) or c.startswith(f"su -c {p}") for p in _ROOT_ALLOW_PREFIXES):
        # 白名单命中后仍要过 deny 模式（rm -rf /data/local/tmp 已在白名单豁免）
        if _ROOT_DENY_PATTERNS.search(c):
            return f"root 命令命中危险模式: {c[:80]}"
        return None
    return (f"root 命令不在白名单（需 allow_risk=true）: {c[:80]}；"
            f"白名单前缀见 core/pipeline.py _ROOT_ALLOW_PREFIXES")


class Pipeline:
    def __init__(self, ctx: Context, transcript=None):
        self.ctx = ctx
        self.transcript = transcript or ctx.transcript
        self._pre: List[Tuple[str, Callable]] = []
        self._post: List[Tuple[str, Callable]] = []

    # ---------- 钩子注册（随 owner 记账，可回滚） ----------
    def add_pre(self, name: str, fn: Callable) -> Callable:
        """fn(td, args) -> args'（可改参）/ 抛 Denied / 正常返回原 args。"""
        self._pre.append((name, fn))
        def _dispose():
            self._pre = [(n, f) for n, f in self._pre if n != name or f is not fn]
        self.ctx._book(_dispose)
        return _dispose

    def add_post(self, name: str, fn: Callable) -> Callable:
        """fn(result_dict) -> result_dict'（可整形/截断）。"""
        self._post.append((name, fn))
        def _dispose():
            self._post = [(n, f) for n, f in self._post if n != name or f is not fn]
        self.ctx._book(_dispose)
        return _dispose

    # ---------- 执行 ----------
    def call(self, name: str, args: Optional[dict] = None) -> dict:
        args = dict(args or {})
        td: Optional[ToolDef] = self.ctx.tool(name)
        if td is None:
            avail = sorted(t.name for t in self.ctx.tools())
            return {"tool": name, "ok": False,
                    "error": f"未知工具 {name}", "available": avail[:200]}
        t0 = time.time()
        try:
            for hname, fn in list(self._pre):
                args = fn(td, args) or args
            with self.ctx.device_lock:       # 单设备串行铁律
                result = td.handler(self.ctx.bridge, self.ctx.engine, args)
            for _hname, fn in list(self._post):
                result = fn(result)
            if not isinstance(result, dict):
                result = {"ok": True, "value": result}
            result.setdefault("ok", True)
        except Denied as ex:
            result = {"ok": False, "error": str(ex), "error_type": f"denied:{ex.by}"}
        except SelectorNotFound as ex:
            result = {"ok": False, "error": str(ex), "error_type": "selector_not_found"}
        except SelectorError as ex:
            result = {"ok": False, "error": str(ex), "error_type": "selector_syntax"}
        except Exception as ex:  # noqa: BLE001 - 包络化，不向调用方抛
            result = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
            if self.transcript:
                self.transcript.log("tool_trace", tool=name,
                                    err=traceback.format_exc()[-1500:])
        result = {"tool": name, **result}
        duration_ms = int((time.time() - t0) * 1000)
        result["duration_ms"] = duration_ms
        if self.transcript:
            self.transcript.log("tool_call", tool=name, args=args,
                                duration_ms=duration_ms, ok=result.get("ok", True),
                                result=_clip(result))
        self.ctx.emit("tool_result", tool=name, ok=result.get("ok", False),
                      duration_ms=duration_ms)
        return result


def _clip(obj, limit=4000):
    # MCP 协议载荷（图像内容块）是通道契约，不参与截断
    if isinstance(obj, dict) and "_mcp_content" in obj:
        rest = {k: v for k, v in obj.items() if k != "_mcp_content"}
        out = _clip(rest, limit)
        out["_mcp_content"] = obj["_mcp_content"]
        return out
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return obj
    return {"_clipped": s[:limit] + "...(truncated)"}


# ---------------- 内置钩子：root 门 + 结果截断 ----------------

def install_builtin_hooks(pipe: Pipeline):
    """默认策略钩子（作为 runtime 内置 owner 注册，可整体卸载观察）。"""

    def root_gate(td: ToolDef, args: dict) -> dict:
        if td.name == "shell.run" and args.get("root"):
            reason = root_gate_check(str(args.get("cmd", "")),
                                     allow_risk=bool(args.get("allow_risk")))
            if reason:
                raise Denied("root_gate", reason)
        if td.name == "file.push" and args.get("root"):
            remote = str(args.get("remote", ""))
            if not remote.startswith(("/data/local/tmp/", "/sdcard/"))                     and not args.get("allow_risk"):
                raise Denied("root_gate",
                             f"file.push root=true 目标不在白名单（/data/local/tmp、/sdcard）：{remote[:80]}；需 allow_risk=true")
        return args

    def ensure_size(result: dict) -> dict:
        return _clip(result, limit=20000)

    pipe.add_pre("root_gate", root_gate)
    pipe.add_post("clip", ensure_size)

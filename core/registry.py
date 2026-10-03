# -*- coding: utf-8 -*-
"""工具注册表视图：ctx.tools() 与 harness 工具的统一出口。

`<pkg>__<tool>` 形式的 harness 工具在此路由到 engine.run。
"""
from __future__ import annotations

import time
from typing import Optional

from .harness import HarnessEngine, TrustError
from .pipeline import Pipeline
from .runtime import Context


def harness_tool_schema(params: dict) -> dict:
    """harness.json5 的 {name: "string"} 简化 params → JSON Schema。"""
    props = {}
    for k, v in (params or {}).items():
        props[k] = v if isinstance(v, dict) else {
            "type": v if v in ("string", "number", "integer", "boolean", "array", "object")
            else "string"}
    return {"type": "object", "properties": props, "required": list(props)}


class Registry:
    def __init__(self, ctx: Context, pipeline: Pipeline,
                 engine: Optional[HarnessEngine] = None,
                 plugin_runtime=None):
        self.ctx = ctx
        self.pipe = pipeline
        self.engine = engine or ctx.engine
        self.plugin_runtime = plugin_runtime
        ctx._plugin_runtime = plugin_runtime   # sys.capabilities 热载入口用

    # ---------- 列举 ----------
    def list(self):
        items = [t.as_dict() for t in self.ctx.tools()]
        if self.engine:
            for h in self.engine.list():
                for t in h.get("tools", []):
                    items.append({
                        "name": f"{h['package']}__{t['name']}",
                        "description": f"[harness:{h['package']}] {t['description']}",
                        "inputSchema": harness_tool_schema(t.get("params", {})),
                        "source": f"harness:{h['package']}",
                    })
        return items

    # ---------- 调用 ----------
    def call(self, name: str, args: Optional[dict] = None):
        args = dict(args or {})
        if self.ctx.tool(name):
            return self.pipe.call(name, args)
        # harness 路由：<pkg>__<tool>（或直接 harness.run）
        if name == "harness.run":
            return self.pipe.call(name, args)
        if "__" in name:
            pkg, tool = name.split("__", 1)
            if self.engine:
                if any(h.get("package") == pkg for h in self.engine.list()):
                    trust = bool(args.pop("__trust", False)) or \
                            bool(args.pop("allow_untrusted", False))
                    dry = bool(args.pop("dry_run", False))
                    t0 = time.time()
                    try:
                        r = self.engine.run(pkg, tool, args, dry_run=dry,
                                            allow_untrusted=trust)
                    except TrustError as ex:
                        r = {"ok": False, "error": str(ex), "error_type": "trust"}
                    except Exception as ex:  # noqa: BLE001
                        r = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
                    r.setdefault("ok", True)
                    return {"tool": name, "duration_ms": int((time.time() - t0) * 1000),
                            **r}
        avail = sorted(t.name for t in self.ctx.tools())
        return {"tool": name, "ok": False, "error": f"未知工具 {name}",
                "available": avail[:200]}

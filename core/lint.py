# -*- coding: utf-8 -*-
"""harness lint：静态检查脆弱选择器与结构性问题（加分项 3）。"""
from __future__ import annotations

import re

ALLOWED_ACTIONS = {"click", "long_click", "set_text", "clear_input", "scroll", "back",
                   "home", "launch", "back_until", "assert", "screenshot"}
COORD_BAN = re.compile(r"input\s+tap|\bx\s*[:=]\s*\d+|\by\s*[:=]\s*\d+|\[\s*\d+\s*,\s*\d+\s*\]")


def lint_harness(h) -> dict:
    """h: core.harness.Harness。返回 {package, ok, findings:[{tool,severity,message}]}"""
    findings = []

    def add(tool_name, severity, message):
        findings.append({"tool": tool_name, "severity": severity, "message": message})

    for tname, t in h.tools.items():
        params = t.get("params", {}) or {}
        used_params = set()

        for raw in t.get("steps", []):
            blob = str(raw)
            for m in re.finditer(r"\$(\w+)", blob):
                used_params.add(m.group(1))
            action = raw.get("action")
            if action not in ALLOWED_ACTIONS:
                add(tname, "error", f"非法动作 {action}（白名单外，shell 类动作被禁止）")
            if COORD_BAN.search(blob):
                add(tname, "error", f"疑似裸坐标/坐标点击: {blob[:120]}")
            target = raw.get("target") or raw.get("container")
            if not target and action not in ("back", "home", "launch", "screenshot"):
                add(tname, "warn", f"动作 {action} 无 target")
            if target:
                parts = [p.strip() for p in target.split("&&")]
                kinds = [p.split("@", 1)[0] if "@" in p else "prop" for p in parts]
                if "pkg" not in kinds and "id" not in kinds:
                    add(tname, "warn", f"选择器未限定包名/资源 id（跨 app 误点风险）: {target[:80]}")
                if kinds == ["text"] or ("text" in kinds and not
                                         ({"id", "desc", "class"} & set(kinds))):
                    add(tname, "warn", f"纯 text 匹配属脆弱选择器（建议叠加 id/desc/class）: {target[:80]}")
                if kinds.count("under") + kinds.count("parent") > 2:
                    add(tname, "warn", f"深层嵌套相对定位（UI 改版最易碎）: {target[:80]}")
                if "nth" in kinds and "id" not in kinds:
                    add(tname, "warn", f"nth 依赖匹配顺序且无 id 锚定: {target[:80]}")

        undeclared = used_params - set(params)
        if undeclared:
            add(tname, "error", f"步骤使用了未声明的参数: {sorted(undeclared)}")
        if not t.get("postconditions"):
            add(tname, "info", "无 postcondition（建议至少一个 exists 断言）")
        if not t.get("description"):
            add(tname, "info", "无 description（影响 MCP 工具面可读性）")

    return {"package": h.package, "ok": not any(f["severity"] == "error" for f in findings),
            "findings": findings}

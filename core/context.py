# -*- coding: utf-8 -*-
"""L0 上下文探针：一次调用收集确定性旁证（不经过模型判读）。

包名/组件/版本等信号不解释业务，但能塌缩应用层假设空间——场景路由
不依赖模型自评（校准在长尾失效），依赖这类外部确定性信号。
"""
from __future__ import annotations

import re
import time

from .runtime import Context


def probe_context(br) -> dict:
    """收集当前设备上下文旁证。每项独立容错：单项失败不阻断其余。"""
    t0 = time.time()
    out: dict = {"ok": True}

    try:
        pkg, act = br.current_app()
        out["package"], out["activity"] = pkg, act
    except Exception as ex:  # noqa: BLE001
        out["package"] = None
        out["errors"] = [f"focus: {type(ex).__name__}"]

    pkg = out.get("package")
    if pkg:
        try:
            dump = br.shell(f"dumpsys package {pkg}", timeout=20)
            ver = re.search(r"versionName=([\S]+)", dump)
            vcode = re.search(r"versionCode=(\d+)", dump)
            inst = re.search(r"firstInstallTime=([\S]+)", dump)
            upd = re.search(r"lastUpdateTime=([\S]+)", dump)
            out["version"] = ver.group(1) if ver else None
            out["version_code"] = int(vcode.group(1)) if vcode else None
            out["installed_at"] = inst.group(1) if inst else None
            out["updated_at"] = upd.group(1) if upd else None
        except Exception as ex:  # noqa: BLE001
            out.setdefault("errors", []).append(f"package: {type(ex).__name__}")

    try:
        wake = br.shell("dumpsys power | grep mWakefulness", timeout=10)
        m = re.search(r"mWakefulness=(\w+)", wake)
        out["screen"] = m.group(1) if m else None
    except Exception as ex:  # noqa: BLE001
        out.setdefault("errors", []).append(f"power: {type(ex).__name__}")

    try:
        w, h = br.d.window_size()
        out["display"] = {"w": int(w), "h": int(h),
                          "orientation": "landscape" if w > h else "portrait"}
    except Exception as ex:  # noqa: BLE001
        out.setdefault("errors", []).append(f"display: {type(ex).__name__}")

    out["ms"] = int((time.time() - t0) * 1000)
    return out


def match_packs(packs: dict, pkg: str) -> list:
    """按包名匹配已加载知识包：meta 的 app/apps/game/pkgs 任一命中即匹配。"""
    hit = []
    for name, p in (packs or {}).items():
        meta = p.get("meta") or {}
        declared = set()
        for key in ("app", "game"):
            if meta.get(key):
                declared.add(str(meta[key]))
        for key in ("apps", "pkgs"):
            for v in (meta.get(key) or []):
                declared.add(str(v))
        if pkg and pkg in declared:
            hit.append(name)
    return hit


def register(ctx: Context):
    @ctx.register_tool(
        "app.probe",
        "L0 上下文探针：一次收集确定性旁证（前台包名/Activity/版本/安装更新时间/"
        "亮灭屏/屏幕方向）。这些信号不经过模型判读、不会骗人——判读或规划前先调它，"
        "可塌缩应用层假设空间",
        {"type": "object"})
    def app_probe(br, eng, a):
        return probe_context(br)

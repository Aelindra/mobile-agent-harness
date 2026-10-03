# -*- coding: utf-8 -*-
"""知识包加载器：pack 目录 = pack.json5 + guide.md + templates/。

加载后 atlas 目录加入 ui2.find_templates 搜索路径，states 供
ui2.check_states 求值，guide 经 knowledge.guide 读取。加载即注册
（ctx.scoped 记账），卸载逆序回滚。`_` 前缀目录不自动加载。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .runtime import Context

_PACKS: Dict[str, dict] = {}          # name -> {dir, meta}
_ATLAS_EXTRA: List[str] = []          # 额外图集目录（pack 注入，vision_match 读取）


def root_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "knowledge")


def packs() -> Dict[str, dict]:
    return dict(_PACKS)


def extra_atlas_dirs() -> List[str]:
    return list(_ATLAS_EXTRA)


def _parse_meta(path: str) -> dict:
    try:
        import json5
        with open(path, encoding="utf-8") as f:
            return json5.load(f) or {}
    except ImportError:
        import json
        text = open(path, encoding="utf-8").read()
        import re
        cleaned = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
        return json.loads(cleaned)


def load_pack_dir(ctx: Context, pdir: str) -> dict:
    """加载单个包目录（注册副作用并记账）。返回包摘要。"""
    meta_path = os.path.join(pdir, "pack.json5")
    if not os.path.exists(meta_path):
        raise ValueError(f"缺 pack.json5: {pdir}")
    meta = _parse_meta(meta_path)
    name = str(meta.get("name") or os.path.basename(pdir))
    if name in _PACKS:
        raise ValueError(f"知识包重名: {name}")
    atlas = meta.get("atlas")
    adir = os.path.join(pdir, str(atlas)) if atlas else None
    if adir and not os.path.isdir(adir):
        raise ValueError(f"atlas 目录不存在: {adir}")
    _PACKS[name] = {"dir": pdir, "meta": meta}
    if adir:
        _ATLAS_EXTRA.append(adir)

    def _dispose():
        _PACKS.pop(name, None)
        if adir and adir in _ATLAS_EXTRA:
            _ATLAS_EXTRA.remove(adir)

    with ctx.scoped(name):        # 记账到包名 owner：ctx.unload(name) 可整体回滚
        ctx._book(_dispose)
    return {"name": name, "version": meta.get("version", "?"),
            "game": meta.get("game"), "dir": pdir}


def load_all(ctx: Context) -> dict:
    """扫描 knowledge/ 自动加载（`_` 前缀跳过；坏包报错不阻断）。"""
    out = {}
    rd = root_dir()
    if not os.path.isdir(rd):
        return out
    for entry in sorted(os.listdir(rd)):
        pdir = os.path.join(rd, entry)
        if entry.startswith("_") or not os.path.isdir(pdir):
            continue
        try:
            out[entry] = load_pack_dir(ctx, pdir)
        except Exception as ex:  # noqa: BLE001 - 单包失败不阻断
            out[entry] = {"error": f"{type(ex).__name__}: {ex}"}
    return out


# ---------------- 判别式求值（证据表达式 → 状态候选） ----------------

def flatten_evidence(ev: dict) -> Dict[str, object]:
    """证据 dict → 判别式短键。约定键名：
    overlay / motion / hud(present|absent) / color.<hue> / filter.vignette.flag /
    filter.flash.spike（其余点路径平铺保留）。"""
    out: Dict[str, object] = {}

    def _walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, f"{prefix}{k}.")
        else:
            out[prefix[:-1]] = obj
    _walk(ev)
    ov, mo, hud = ev.get("overlay") or {}, ev.get("motion") or {}, ev.get("hud") or {}
    if "level" in ov:
        out["overlay"] = ov["level"]
    if "label" in mo:
        out["motion"] = mo["label"]
    if "present" in hud:
        out["hud"] = "present" if hud.get("present") else "absent"
    for c, v in ((ev.get("color") or {}).get("hue_mass") or {}).items():
        out[f"color.{c}"] = v
    return out


def _eval_expr(expr: str, flat: Dict[str, object]):
    expr = expr.strip()
    for op in ("!=", ">=", "<=", "=", ">", "<"):
        if op in expr:
            key, val = expr.split(op, 1)
            key, val = key.strip(), val.strip()
            actual = flat.get(key)
            if actual is None:
                return None, None          # 证据缺失：不算失败，算"缺证据"
            try:
                a, b = float(actual), float(val)
            except (TypeError, ValueError):
                a, b = str(actual).lower(), str(val).lower()
            hit = {"=": a == b, "!=": a != b, ">": a > b,
                   "<": a < b, ">=": a >= b, "<=": a <= b}[op]
            return hit, f"{key}={actual}"
    return None, None


def evaluate_states(ev: dict, states: dict) -> List[dict]:
    """对一组状态声明求值。判别式纪律：全满足才 ok；缺证据≠不匹配（单列）。"""
    flat = flatten_evidence(ev)
    out = []
    for sname, spec in (states or {}).items():
        missing, failed = [], []
        for expr in (spec.get("all") or []):
            hit, detail = _eval_expr(str(expr), flat)
            if hit is None:
                missing.append({"expr": str(expr), "detail": "证据缺失"})
            elif not hit:
                failed.append({"expr": str(expr), "actual": detail})
        out.append({"state": sname,
                    "ok": not missing and not failed,
                    "insufficient_evidence": bool(missing),
                    "missing": missing, "failed": failed,
                    "desc": spec.get("desc", "")})
    return out


# ---------------- 工具面 ----------------

def register(ctx: Context):
    load_all(ctx)

    @ctx.register_tool(
        "knowledge.list", "列出已加载知识包（guide=上下文面，templates/states=运行时面）",
        {"type": "object"})
    def knowledge_list(br, eng, a):
        items = []
        for name, p in _PACKS.items():
            meta = p["meta"]
            items.append({"name": name, "version": meta.get("version", "?"),
                          "game": meta.get("game", ""),
                          "guide": bool(meta.get("guide")),
                          "states": sorted((meta.get("states") or {}).keys()),
                          "dir": p["dir"]})
        return {"ok": True, "packs": items}

    @ctx.register_tool(
        "knowledge.guide", "读取知识包的 guide（上下文面：业务流程/使用时机/注意事项）。"
        "用某 app 前先看对应包的 guide",
        {"type": "object", "properties": {"pack": {"type": "string"}}, "required": ["pack"]})
    def knowledge_guide(br, eng, a):
        p = _PACKS.get(a["pack"])
        if not p:
            return {"ok": False, "error": f"未加载的包 {a['pack']}",
                    "available": sorted(_PACKS)}
        g = p["meta"].get("guide")
        if not g:
            return {"ok": False, "error": "该包无 guide"}
        path = os.path.join(p["dir"], str(g))
        if not os.path.exists(path):
            return {"ok": False, "error": f"guide 文件缺失: {path}"}
        text = open(path, encoding="utf-8").read()
        return {"ok": True, "pack": a["pack"], "guide": text[:8000],
                "truncated": len(text) > 8000}

    @ctx.register_tool(
        "ui2.check_states", "按知识包的状态判别式求值当前屏幕：先采证据（同 ui2.screen_evidence），"
        "再对声明状态逐个判别式求值——全满足=ok，缺证据单列（不出具结论）。"
        "这是'证据与判读分离'的声明面",
        {"type": "object", "properties": {
            "pack": {"type": "string", "description": "只求值该包；缺省求值全部已加载包"},
            "game": {"type": "string"}}})
    def ui2_check_states(br, eng, a):
        from .vision_match import collect_evidence
        ev = collect_evidence(br, a.get("game"))
        if not ev.get("ok"):
            return ev
        targets = ([a["pack"]] if a.get("pack") else sorted(_PACKS))
        results = []
        for name in targets:
            p = _PACKS.get(name)
            if not p:
                continue
            for st in evaluate_states(ev, p["meta"].get("states") or {}):
                results.append({"pack": name, **st})
        return {"ok": True, "evidence": ev,
                "states": results,
                "rule": "ok=true 才是结论；insufficient_evidence=true 的状态只是证据不足，继续采集"}

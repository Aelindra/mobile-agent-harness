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


def _eval_seq(seq: List[str], frame_flats: List[Dict[str, object]]) -> dict:
    """序列判别式：expr 依序命中时间线各帧（帧严格前进）。
    部分命中=证据性失败（时间线已观察但序列不成立），非"缺证据"。"""
    matched, details = 0, []
    fi = 0
    for expr in seq:
        found = None
        while fi < len(frame_flats):
            hit, detail = _eval_expr(str(expr), frame_flats[fi])
            if hit is True:
                found = fi
                fi += 1
                break
            fi += 1
        if found is None:
            return {"matched": matched, "total": len(seq),
                    "ok": False,
                    "detail": f"seq 在第 {matched + 1} 步中断: {expr}"}
        matched += 1
        details.append(f"[f{found}] {expr}")
    return {"matched": matched, "total": len(seq), "ok": True, "detail": " -> ".join(details)}


def evaluate_states(ev: dict, states: dict, timeline: Optional[List[dict]] = None) -> List[dict]:
    """对一组状态声明求值。判别式纪律：全满足才 ok；缺证据≠不匹配（单列）。

    timeline=帧证据列表（ui2.timeline 产物，各元素为单帧 evidence dict）。
    状态可声明 seq（表达式列表，依序命中时间线）——单帧证据无法表达的
    时序形状（进入结构→指示圈→弹出）由 seq 承载。
    返回附 score=可测判别式的命中率，供无全命中时输出排序假设。"""
    flat = flatten_evidence(ev)
    frame_flats = [flatten_evidence(t) for t in (timeline or [])]
    out = []
    for sname, spec in (states or {}).items():
        missing, failed, okd = [], [], 0
        for expr in (spec.get("all") or []):
            hit, detail = _eval_expr(str(expr), flat)
            if hit is None:
                missing.append({"expr": str(expr), "detail": "证据缺失"})
            elif hit:
                okd += 1
            else:
                failed.append({"expr": str(expr), "actual": detail})
        seq_detail = None
        if spec.get("seq"):
            if not frame_flats:
                missing.append({"expr": f"seq({len(spec['seq'])}步)",
                                "detail": "时间线证据缺失（传 timeline=true 重采）"})
            else:
                seq_detail = _eval_seq(spec["seq"], frame_flats)
                if seq_detail["ok"]:
                    okd += 1
                else:
                    failed.append({"expr": "seq", "actual": seq_detail["detail"]})
        testable = okd + len(failed) + len(missing)
        out.append({"state": sname,
                    "ok": not missing and not failed,
                    "insufficient_evidence": bool(missing),
                    "score": round(okd / testable, 2) if testable else 0.0,
                    "missing": missing, "failed": failed,
                    "seq": seq_detail,
                    "desc": spec.get("desc", "")})
    out.sort(key=lambda r: (-r["score"], r["state"]))
    return out


def hypotheses(results: List[dict], top: int = 3) -> List[dict]:
    """无全命中时的假设枚举出口：按 score 排序的部分匹配候选+缺什么证据。"""
    cands = [r for r in results if not r["ok"] and r["score"] > 0]
    cands.sort(key=lambda r: -r["score"])
    return [{"state": r["state"],
             "score": r["score"],
             "missing": [m["expr"] for m in r["missing"]],
             "failed": [f["expr"] for f in r["failed"]],
             "desc": r.get("desc", "")} for r in cands[:top]]


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
        "再对声明状态逐个判别式求值——全满足=ok，缺证据单列，无全命中时给排序假设。"
        "状态声明含 seq（时序形状）时需 timeline=true 先采帧时间线",
        {"type": "object", "properties": {
            "pack": {"type": "string", "description": "只求值该包；缺省求值全部已加载包"},
            "game": {"type": "string"},
            "timeline": {"type": "boolean", "default": False,
                         "description": "true 时先采帧时间线（供 seq 判别式），多花约帧数*间隔"}}})
    def ui2_check_states(br, eng, a):
        from .vision_match import collect_evidence, collect_timeline
        ev = collect_evidence(br, a.get("game"))
        if not ev.get("ok"):
            return ev
        timeline = None
        need_seq = any((p["meta"].get("states") or {}) and
                       any(s.get("seq") for s in (p["meta"].get("states") or {}).values())
                       for p in _PACKS.values())
        if a.get("timeline") or need_seq:
            timeline = collect_timeline(br, frames=int(a.get("frames", 6)),
                                        interval=a.get("interval", 0.5))
            if not timeline.get("ok"):
                timeline = None
        targets = ([a["pack"]] if a.get("pack") else sorted(_PACKS))
        results = []
        for name in targets:
            p = _PACKS.get(name)
            if not p:
                continue
            for st in evaluate_states(ev, p["meta"].get("states") or {}, timeline):
                results.append({"pack": name, **st})
        full = [r for r in results if r["ok"]]
        return {"ok": True, "evidence": ev,
                "timeline_frames": len(timeline) if timeline else 0,
                "states": results,
                "matched": full,
                "hypotheses": ([] if full else hypotheses(results)),
                "rule": "ok=true 才是结论；无 ok 时 hypotheses 是按证据命中率的排序候选，"
                        "继续补 missing 证据再判，不得跳到结论"}

    @ctx.register_tool(
        "ui2.orient", "情境定向（L0→L1 组合出口）：先探上下文旁证（app.probe：包名/版本/"
        "方向等确定性信号，塌缩应用层假设），再按包名匹配已加载知识包并求值状态判别式"
        "（含 seq 时自动采时间线）。无匹配包时返回 probe+提示。判读/规划前的第一步",
        {"type": "object", "properties": {"game": {"type": "string"}}})
    def ui2_orient(br, eng, a):
        from .context import probe_context, match_packs
        probe = probe_context(br)
        pkg = probe.get("package")
        matched = match_packs(_PACKS, pkg or "")
        out: dict = {"ok": True, "probe": probe, "packs": matched}
        if not matched:
            out["next"] = ("无已加载知识包匹配该应用：判读走通用证据"
                           "（ui2.screen_evidence/ui2.state），或为该应用建包"
                           "（pack.json5 的 app 字段填包名）")
            return out
        from .vision_match import collect_evidence, collect_timeline
        ev = collect_evidence(br, a.get("game"))
        if not ev.get("ok"):
            out["evidence_error"] = ev
            return out
        timeline = None
        states_all = {}
        results = []
        for name in matched:
            p = _PACKS[name]
            st = p["meta"].get("states") or {}
            states_all[name] = sorted(st.keys())
            if any(s.get("seq") for s in st.values()) and timeline is None:
                timeline = collect_timeline(br)
                if not timeline.get("ok"):
                    timeline = None
            for r in evaluate_states(ev, st, timeline):
                results.append({"pack": name, **r})
        full = [r for r in results if r["ok"]]
        out.update({"evidence": ev,
                    "timeline_frames": len(timeline) if timeline else 0,
                    "states": results, "matched_states": full,
                    "hypotheses": ([] if full else hypotheses(results)),
                    "guides": [n for n in matched if _PACKS[n]["meta"].get("guide")]})
        out["next"] = (f"命中 {len(full)} 个状态；先 knowledge.guide 看业务流程再行动"
                       if full else
                       "无全命中：按 hypotheses 补采集（missing 列了缺什么），不要下结论")
        return out

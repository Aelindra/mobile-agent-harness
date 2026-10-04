# -*- coding: utf-8 -*-
"""图标模板匹配：声明式"图标→语义"图集（atlas）。

template.add 从截图裁剪图标并声明名称/用途；ui2.find_templates 在任意
截图上返回图标位置与置信度。图集目录 <repo>/templates/<game>/，
manifest.json：{"<模板名>": {"label": ..., "desc": ..., "kind": "hud|icon|item|..."}}。
图集内容可独立发布共享，基座只带引擎（templates/ 除 README 外 gitignored）。
"""
from __future__ import annotations

import json
import os
from typing import List, Optional

from .runtime import Context


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def atlas_dir(game: str) -> str:
    """<repo>/templates/<game>/；game 名做白名单清洗。template.add 写入用。"""
    safe = "".join(c for c in game if c.isalnum() or c in "-_") or "default"
    return os.path.join(_root(), "templates", safe)


def iter_roots() -> List[str]:
    """图集根目录：repo templates/ + 知识包注入目录（knowledge.extra_atlas_dirs）。"""
    roots = [os.path.join(_root(), "templates")]
    try:
        from .knowledge import extra_atlas_dirs
        roots.extend(d for d in extra_atlas_dirs() if os.path.isdir(d))
    except Exception:
        pass
    return [r for r in roots if os.path.isdir(r)]


def find_atlas(game: str) -> Optional[str]:
    """跨全部图集根查找某 game 的图集目录（repo 优先）。"""
    safe = "".join(c for c in game if c.isalnum() or c in "-_") or "default"
    for root in iter_roots():
        d = os.path.join(root, safe)
        if os.path.exists(os.path.join(d, "manifest.json")):
            return d
    return None


def _manifest_path(game: str) -> str:
    return os.path.join(atlas_dir(game), "manifest.json")


def load_manifest(game: str) -> dict:
    d = find_atlas(game)
    p = os.path.join(d, "manifest.json") if d else _manifest_path(game)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_manifest(game: str, mf: dict):
    os.makedirs(atlas_dir(game), exist_ok=True)
    with open(_manifest_path(game), "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=1)


def _cv_gray(img):
    import cv2
    import numpy as np
    arr = np.asarray(img.convert("L"))
    return cv2.Canny(arr, 50, 150) if False else arr  # 灰度直接匹配（同设备同分辨率场景）


def find_in_image(screen_img, tmpl_img, threshold: float = 0.78,
                  max_results: int = 8) -> List[dict]:
    """单模板多目标匹配（灰度 TM_CCOEFF_NORMED + 贪心 NMS）。"""
    import cv2
    import numpy as np
    scr = np.asarray(screen_img.convert("L"))
    tpl = np.asarray(tmpl_img.convert("L"))
    th_, tw_ = tpl.shape[:2]
    if scr.shape[0] < th_ or scr.shape[1] < tw_:
        return []
    res = cv2.matchTemplate(scr, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    cands = sorted(zip(xs.tolist(), ys.tolist(), res[ys, xs].tolist()),
                   key=lambda t: -t[2])
    out, taken = [], []
    for x, y, score in cands:
        if any(abs(x - px) < tw_ // 2 and abs(y - py) < th_ // 2 for px, py in taken):
            continue
        taken.append((x, y))
        out.append({"center": [x + tw_ // 2, y + th_ // 2],
                    "bounds": [int(x), int(y), int(x + tw_), int(y + th_)],
                    "conf": round(float(score), 3)})
        if len(out) >= max_results:
            break
    return out


def frame_evidence(img, prev_img=None, t: float = 0.0) -> dict:
    """单帧轻量证据（无 OCR/无模板）：overlay/motion/color——时间线原料。"""
    import numpy as np
    hsv = np.asarray(img.convert("HSV").resize((320, 180)))
    H, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    sat, val = float(S.mean()), float(V.mean())
    ev = {"t": round(float(t), 2),
          "overlay": "gray" if sat < 25 else "dimmed" if sat < 45 else "normal",
          "brightness": round(val, 1)}
    if prev_img is not None:
        a1 = np.asarray(prev_img.convert("L").resize((320, 180)), dtype=int)
        a2 = np.asarray(img.convert("L").resize((320, 180)), dtype=int)
        m = float(np.abs(a1 - a2).mean())
        ev["motion"] = m
        ev["motion_label"] = "static" if m < 1.0 else "low" if m < 6 else "high"
    Hdeg = H * 360 // 255
    chroma = S >= 40
    total = H.size
    buckets = {"red": ((Hdeg < 15) | (Hdeg >= 345)) & chroma,
               "orange": (Hdeg >= 15) & (Hdeg < 40) & chroma,
               "yellow": (Hdeg >= 40) & (Hdeg < 65) & chroma,
               "green": (Hdeg >= 65) & (Hdeg < 160) & chroma,
               "blue": (Hdeg >= 195) & (Hdeg < 260) & chroma,
               "purple": (Hdeg >= 260) & (Hdeg < 345) & chroma}
    hue = {k: round(int(m.sum()) / total, 3) for k, m in buckets.items()}
    hue["neutral"] = round(1.0 - sum(hue.values()), 3)
    ev["color"] = {"hue_mass": hue}
    return ev


def collect_timeline(br, frames: int = 6, interval: float = 0.5) -> dict:
    """帧时间线：N 帧轻量证据按时间排列——seq 判别式与运动形状的原料。"""
    import time as _t
    from . import frames as _frames
    out, prev, t0 = [], None, _t.time()
    for i in range(max(2, min(frames, 20))):
        try:
            img, _ = _frames.grab(br)
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"取帧失败: {type(ex).__name__}: {ex}"}
        fe = frame_evidence(img, prev, t=_t.time() - t0)
        fe.pop("color", None)          # 时间线只留序列相关字段，控体积
        out.append(fe)
        prev = img
        if i < max(2, min(frames, 20)) - 1:
            _t.sleep(max(0.05, interval))
    return {"ok": True, "frames": out, "interval": interval}


def collect_evidence(br, game: Optional[str] = None) -> dict:
    """屏幕证据采集（只出证据不下结论）。ui2.screen_evidence 与 ui2.check_states 共用。"""
    try:
        import time as _t
        t0 = _t.time()
        from . import frames
        from PIL import Image
        import numpy as np
        p1 = os.path.join(_root(), "logs", "ev1.png")
        p2 = os.path.join(_root(), "logs", "ev2.png")
        im1, _ = frames.grab(br, p1)
        _t.sleep(0.4)
        im2, _ = frames.grab(br, p2)
        hsv = np.asarray(im1.convert("HSV"))
        sat = float(hsv[:, :, 1].mean())
        val = float(hsv[:, :, 2].mean())
        overlay = "gray" if sat < 25 else "dimmed" if sat < 45 else "normal"
        a1 = np.asarray(im1.convert("L").resize((320, 180)), dtype=int)
        a2 = np.asarray(im2.convert("L").resize((320, 180)), dtype=int)
        motion = float(np.abs(a1 - a2).mean())
        motion_label = "static" if motion < 1.0 else "low" if motion < 6 else "high"

        # ---- 色彩证据：色相质量分布（低饱和归 neutral；PIL H=0-255 → 换算角度）----
        hsv_s = np.asarray(im1.convert("HSV").resize((320, 180)))
        H, S, V = hsv_s[:, :, 0].astype(int), hsv_s[:, :, 1].astype(int), hsv_s[:, :, 2].astype(int)
        Hdeg = H * 360 // 255
        chroma = S >= 40
        total_px = H.size
        buckets = {"red": ((Hdeg < 15) | (Hdeg >= 345)) & chroma,
                   "orange": (Hdeg >= 15) & (Hdeg < 40) & chroma,
                   "yellow": (Hdeg >= 40) & (Hdeg < 65) & chroma,
                   "green": (Hdeg >= 65) & (Hdeg < 160) & chroma,
                   "cyan": (Hdeg >= 160) & (Hdeg < 195) & chroma,
                   "blue": (Hdeg >= 195) & (Hdeg < 260) & chroma,
                   "purple": (Hdeg >= 260) & (Hdeg < 345) & chroma}
        hue_mass = {k: round(int(m.sum()) / total_px, 3) for k, m in buckets.items()}
        hue_mass["neutral"] = round(1.0 - sum(hue_mass.values()), 3)

        # ---- 滤镜证据：边环比（暗角/红晕类全屏滤镜=边缘特异）----
        h_, w_ = H.shape
        ys, xs = np.mgrid[0:h_, 0:w_]
        border = (xs < w_ * 0.18) | (xs >= w_ * 0.82) | (ys < h_ * 0.18) | (ys >= h_ * 0.82)
        red = buckets["red"]
        b_red = float(red[border].mean()) if border.any() else 0.0
        c_red = float(red[~border].mean()) if (~border).any() else 0.0
        b_val = float(V[border].mean())
        c_val = float(V[~border].mean())
        vignette = {"border_red_ratio": round(b_red, 3),
                    "center_red_ratio": round(c_red, 3),
                    "border_val_ratio": round(b_val / max(1.0, c_val), 2),
                    "flag": ("red_border_heavy" if b_red > 0.25 and b_red > 3 * max(c_red, 0.01)
                             else "dark_borders" if b_val < 0.6 * c_val else "none")}

        # ---- 滤镜证据：亮度突变（全屏闪光类）----
        dv = float(np.abs(a2 - a1).mean())
        flash = {"delta_val": round(dv, 2), "spike": bool(dv > 30)}

        # ---- 布局证据：4x3 粗粒度边缘密度网格（UI 密集区=文本/控件热区）----
        g = np.abs(np.diff(a1, axis=0, prepend=a1[:1, :])) + \
            np.abs(np.diff(a1, axis=1, prepend=a1[:, :1]))
        gh, gw = g.shape
        grid = [round(float(g[r * gh // 3:(r + 1) * gh // 3, c * gw // 4:(c + 1) * gw // 4].mean()), 1)
                for r in range(3) for c in range(4)]

        # HUD 在位性：图集里 kind=hud 的模板命中过半视为在位
        hud = None
        if game:
            mf = load_manifest(game)
            adir = find_atlas(game)
            huds = {k: v for k, v in mf.items() if str(v.get("kind", "")).startswith("hud")}
            if huds:
                hits = 0
                for k, meta in huds.items():
                    tp = os.path.join(adir or "", meta.get("file", k + ".png"))
                    if os.path.exists(tp) and find_in_image(
                            im1, Image.open(tp).convert("RGB"), threshold=0.8, max_results=1):
                        hits += 1
                hud = {"present": hits >= max(1, len(huds) // 2),
                       "hits": hits, "of": len(huds)}
        return {"ok": True,
                "overlay": {"level": overlay, "sat_mean": round(sat, 1),
                            "val_mean": round(val, 1)},
                "motion": {"score": round(motion, 2), "label": motion_label},
                "color": {"hue_mass": hue_mass,
                          "note": "各色相像素占比（客观分布，非判读）"},
                "filter": {"vignette": vignette, "flash": flash},
                "layout": {"edge_density_3x4": grid,
                           "note": "高值=文本/控件密集区（UI 热区），低值=画面区"},
                "hud": hud,
                "ms": int((t0 and (_t.time() - t0)) * 1000)}
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}


def register(ctx: Context):
    @ctx.register_tool(
        "ui2.find_templates",
        "图标模板匹配：在当前屏幕上找声明图集里的图标（无需 hover，图标在屏即命中），"
        "返回位置+置信度+manifest 里声明的语义。templates 缺省匹配图集全部；"
        "图集用 template.add 从截图裁剪声明",
        {"type": "object", "properties": {
            "game": {"type": "string", "description": "图集名（templates/<game>/），缺省扫全部"},
            "templates": {"type": "array", "items": {"type": "string"},
                          "description": "模板名列表，缺省全部"},
            "threshold": {"type": "number", "default": 0.78},
            "max_per": {"type": "integer", "default": 6}}})
    def ui2_find_templates(br, eng, a):
        try:
            import cv2  # noqa: F401
        except ImportError:
            return {"ok": False, "error": "opencv-python 未安装：pip install opencv-python"}
        import numpy as np  # noqa: F401
        games = [a["game"]] if a.get("game") else sorted(
            {d for root in iter_roots()
             for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))})
        if not games:
            return {"ok": False, "error": "图集为空：先用 template.add 从截图裁剪声明"}
        want = set(a.get("templates") or [])
        import time
        t0 = time.time()
        from . import frames
        from PIL import Image
        path = os.path.join(_root(), "logs", "tmpl_match.png")
        scr, path = frames.grab(br, path)
        matches = []
        for g in games:
            mf = load_manifest(g)
            adir = find_atlas(g)
            for stem, meta in mf.items():
                if want and stem not in want:
                    continue
                tp = os.path.join(adir or "", meta.get("file", stem + ".png"))
                if not os.path.exists(tp):
                    continue
                for m in find_in_image(scr, Image.open(tp).convert("RGB"),
                                       threshold=float(a.get("threshold", 0.78)),
                                       max_results=int(a.get("max_per", 6))):
                    matches.append({"game": g, "name": stem,
                                    "label": meta.get("label", stem),
                                    "desc": meta.get("desc", ""), **m})
        matches.sort(key=lambda m: -m["conf"])
        return {"ok": True, "screen": path, "count": len(matches),
                "matches": matches[:40], "ms": int((time.time() - t0) * 1000),
                "note": "图标在屏即命中（无需 hover）；语义来自 manifest 声明"}

    @ctx.register_tool(
        "ui2.screen_evidence",
        "屏幕证据采集（只出证据不下结论）：overlay=灰屏/暗化覆盖（HSV）、motion=两帧运动量、"
        "color=色相分布、filter=vignette/flash、layout=UI 热区网格、hud=动作栏图标在位性"
        "（图集 kind=hud 模板）。防幻觉纪律：缺证据时列备选假设，不得凭单一特征断言状态",
        {"type": "object", "properties": {"game": {"type": "string"}}})
    def ui2_screen_evidence(br, eng, a):
        ev = collect_evidence(br, a.get("game"))
        if ev.get("ok"):
            ev["discriminators"] = {
                "grayed(覆盖遮蔽)": "overlay=gray 且 motion=static/low 且 hud 缺席",
                "transition(过渡中)": "hud 缺席 但 overlay=normal 且 motion=high（画面在动）",
                "cutscene(过场/播片)": "hud 缺席 且 motion=low 且 overlay=normal"}
            ev["rule"] = "只有判别式全部满足才能下结论；不满足时输出备选假设并继续采集证据"
        return ev

    @ctx.register_tool(
        "ui2.timeline",
        "帧时间线（事实面时序证据）：N 帧轻量证据（overlay/motion/brightness）按时间排列。"
        "瞬态/中间态在单帧里歧义（走位 vs 飞行 vs 过场），时序形状消解歧义；"
        "也是知识包 seq 判别式的原料",
        {"type": "object", "properties": {
            "frames": {"type": "integer", "default": 6},
            "interval": {"type": "number", "default": 0.5}}})
    def ui2_timeline(br, eng, a):
        return collect_timeline(br, frames=int(a.get("frames", 6)),
                                interval=float(a.get("interval", 0.5)))

    @ctx.register_tool(
        "ui2.scene_match",
        "场景/花纹零样本分类（小模型层）：给候选标签短语（如 '设置页面'/'聊天列表页'），"
        "CLIP 类模型计算图文相似度返回各候选得分——加新场景=加一个标签，无需训练。"
        "定位：色彩/布局等像素统计概括不了的花纹与场景语义。需可选依赖："
        "pip install 'mobile-agent-harness[scene]'；模型经 MAH_SCENE_MODEL 配置"
        "（中文标签用 OFA-Sys/chinese-clip-vit-base-p16，英文标签默认 openai/clip-vit-base-patch32）",
        {"type": "object", "properties": {
            "candidates": {"type": "array", "items": {"type": "string"},
                           "description": "候选场景/花纹标签短语（封闭集合声明）"},
            "box": {"type": "array", "items": {"type": "integer"},
                    "description": "[l,t,r,b] 只对裁剪区域分类"},
            "from": {"type": "string", "description": "截图路径，缺省最近一次 vision.screenshot"},
            "top": {"type": "integer", "default": 3}},
         "required": ["candidates"]})
    def ui2_scene_match(br, eng, a):
        try:
            import torch  # noqa: F401
            from transformers import AutoProcessor, AutoModel
        except ImportError as ex:
            return {"ok": False,
                    "error": f"scene 层依赖未安装: {ex}",
                    "hint": "pip install torch --index-url https://download.pytorch.org/whl/cpu && "
                            "pip install transformers && (国内) export HF_ENDPOINT=https://hf-mirror.com"}
        cands = [str(x) for x in (a.get("candidates") or [])][:20]
        if not cands:
            return {"ok": False, "error": "candidates 不能为空"}
        from .tools import _LAST_SHOT
        src = a.get("from") or _LAST_SHOT.get("path")
        if not src or not os.path.exists(src):
            return {"ok": False, "error": "无可用截图：先 vision.screenshot 或传 from 路径"}
        from PIL import Image
        img = Image.open(src).convert("RGB")
        if a.get("box"):
            l, t, r, b = [int(v) for v in a["box"]]
            img = img.crop((l, t, r, b))
        import time as _t
        t0 = _t.time()
        try:
            mname = os.environ.get("MAH_SCENE_MODEL", "openai/clip-vit-base-patch32")
            import transformers as _tf
            if "chinese-clip" in mname:
                proc = _tf.ChineseCLIPProcessor.from_pretrained(mname)
                model = _tf.ChineseCLIPModel.from_pretrained(mname)
            else:
                proc = _tf.CLIPProcessor.from_pretrained(mname)
                model = _tf.CLIPModel.from_pretrained(mname)
            model.eval()
            import torch as _torch
            with _torch.no_grad():
                ins = proc(text=cands, images=img, return_tensors="pt", padding=True)
                out = model(**ins)
                probs = out.logits_per_image[0].softmax(dim=0).tolist()
            pairs = sorted(zip(cands, (round(p, 4) for p in probs)), key=lambda x: -x[1])
            return {"ok": True, "model": mname, "ranked": pairs[:int(a.get("top", 3))],
                    "all": dict(zip(cands, (round(p, 4) for p in probs))),
                    "note": "得分为归一化相似度（封闭集合内相对值，非绝对置信度）",
                    "ms": int((_t.time() - t0) * 1000)}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

    @ctx.register_tool(
        "template.add",
        "声明图标模板：从截图（缺省最近一次 vision.screenshot）按 box 裁剪图标，"
        "写入 templates/<game>/ 并更新 manifest。这是'图标→语义'声明层的一次性成本，"
        "之后 ui2.find_templates 永久可匹配（无需 hover）",
        {"type": "object", "properties": {
            "game": {"type": "string"},
            "name": {"type": "string", "description": "模板名（ascii slug，如 flash）"},
            "label": {"type": "string", "description": "显示名（如 闪现）"},
            "desc": {"type": "string", "description": "用途说明（声明层核心字段）"},
            "kind": {"type": "string"},
            "box": {"type": "array", "items": {"type": "integer"},
                    "description": "[l,t,r,b] 截图上的裁剪框"},
            "from": {"type": "string", "description": "截图路径，缺省最近一次 vision.screenshot"}},
         "required": ["game", "name", "box"]})
    def template_add(br, eng, a):
        from .tools import _LAST_SHOT
        from PIL import Image
        src = a.get("from") or _LAST_SHOT.get("path")
        if not src or not os.path.exists(src):
            return {"ok": False, "error": "无可用截图：先 vision.screenshot 或传 from 路径"}
        l, t, r, b = [int(v) for v in a["box"]]
        if r - l < 8 or b - t < 8:
            return {"ok": False, "error": f"box 太小 {r - l}x{b - t}"}
        img = Image.open(src).convert("RGB").crop((l, t, r, b))
        g = a["game"]
        os.makedirs(atlas_dir(g), exist_ok=True)
        fn = a["name"] + ".png"
        safe = "".join(c for c in a["name"] if c.isalnum() or c in "-_")
        if not safe:
            return {"ok": False, "error": "name 需为 ascii slug"}
        img.save(os.path.join(atlas_dir(g), safe + ".png"))
        mf = load_manifest(g)
        mf[safe] = {"file": fn, "label": a.get("label", safe),
                    "desc": a.get("desc", ""), "kind": a.get("kind", ""),
                    "declared_at": a.get("from") or src}
        save_manifest(g, mf)
        return {"ok": True, "game": g, "name": safe, "size": list(img.size),
                "atlas": atlas_dir(g),
                "note": "已入图集；此后 ui2.find_templates 可在任意屏幕匹配"}

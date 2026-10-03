# -*- coding: utf-8 -*-
"""a11y×OCR 融合的界面状态：元素 + 类型 + 句柄。

a11y 锚点提供可交互性真值（clickable/editable/id）；OCR 覆盖 canvas 文本，
按 bounds 与锚点配对。未配对的 OCR 行退回启发式并以 src="ocr" 标记。
元素句柄与 ui.snapshot 共用，ui.click_handle 可直接操作。

工具：ui2.state / ui2.diff / ui2.wait_change。
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

from . import snapshot as snap

VERB_HINTS = ("点击", "进入", "开始", "领取", "确定", "确认", "取消", "关闭", "登录",
              "签到", "立即", "前往", "购买", "兑换", "挑战", "扫荡", "跳过", "返回")


def classify(text: str, box: Tuple[int, int, int, int], img_w: int) -> dict:
    """启发式元素分类 + 可交互推断（仅用于配不上 a11y 的 OCR 独有元素）。"""
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    t = text.strip()
    interactive = False
    reason = ""
    if re.fullmatch(r"[\d\s:/.\-%+~×xX|]+", t):
        pass  # 纯数字/计分板/时间戳——永不标可交互
    elif any(v in t for v in VERB_HINTS) and len(t) <= 12:
        interactive, reason = True, "动词短文本"
    elif re.fullmatch(r"×|X|x|✕", t):
        interactive, reason = True, "关闭符"
    elif len(t) <= 6 and w < img_w * 0.25 and h < 80:
        interactive, reason = True, "短文本孤立块（按钮样式）"
    kind = "btn" if interactive else ("text" if h > 40 and len(t) > 8 else "text")
    return {"kind": kind, "interactive": interactive, "why": reason}


def _ocr_lines(ocr, img, max_side: int = 1280) -> List[dict]:
    """OCR 前降采样到 max_side（2772 宽原图 CPU 推理 ~2s，1280 约 ~0.6s），框坐标按比例还原。"""
    import numpy as np
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    small = img.resize((int(w * scale), int(h * scale))) if scale < 1.0 else img
    res, _ = ocr(np.asarray(small))
    inv = 1.0 / scale
    lines = []
    for x in (res or []):
        box = x[0]
        x1, y1 = int(min(p[0] for p in box) * inv), int(min(p[1] for p in box) * inv)
        x2, y2 = int(max(p[0] for p in box) * inv), int(max(p[1] for p in box) * inv)
        t = (x[1] or "").strip()
        if t:
            lines.append({"t": t, "c": [(x1 + x2) // 2, (y1 + y2) // 2],
                          "bounds": [x1, y1, x2, y2]})
    return lines


class UISnapshot:
    def __init__(self, bridge, ocr=None):
        self.b = bridge
        self._ocr = ocr

    def ocr(self, img):
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        return self._ocr(img)

    def capture(self, tag: str = "ui", max_elements: int = 80) -> dict:
        """截屏 + dump → a11y×OCR 融合元素表（分配句柄）。"""
        from . import frames
        t0 = time.time()
        path = f"logs/uiv_{tag}.png"
        img, path = frames.grab(self.b, path)
        W, H = img.size
        t_ocr = time.time()
        lines = _ocr_lines(self.ocr, img)
        ocr_ms = int((time.time() - t_ocr) * 1000)
        t_a11y = time.time()
        pkg, _act = "", ""
        try:
            pkg, _act = self.b.current_app()
        except Exception:
            pass
        # 完整记录（含 bounds/cls/pkg 内部字段）
        els = snap.a11y_elements(self.b.dump(), pkg, max_elements=10 ** 6)
        a11y_ms = int((time.time() - t_a11y) * 1000)

        # ---- 融合：OCR 行按中心落入 a11y bounds 配对 ----
        # 全屏锚点（canvas/SurfaceView/面积占比>0.85）只作背景，不吸收 OCR 行——
        # 否则游戏画面的整个 HUD 会被糊成一条文本
        screen_area = float(W * H)
        used = set()
        for el in els:
            l, tp, r, bt = el["bounds"]
            area_ratio = max(1, r - l) * max(1, bt - tp) / screen_area
            is_surface = el.get("cls", "").endswith(
                ("SurfaceView", "TextureView", "VideoView", "WebView"))
            if is_surface or area_ratio > 0.85:
                if is_surface:
                    el["k"] = "canvas"
                continue
            hit = [i for i, ln in enumerate(lines)
                   if l <= ln["c"][0] <= r and tp <= ln["c"][1] <= bt]
            if not hit:
                continue
            used.update(hit)
            if not el["t"]:
                el["t"] = " ".join(lines[i]["t"] for i in hit)
                el["src"] = "both"

        # ---- OCR 独有元素（canvas/自绘）：启发式，src=ocr ----
        for i, ln in enumerate(lines):
            if i in used:
                continue
            cls = classify(ln["t"], ln["bounds"], W)
            el = {"t": ln["t"], "k": cls["kind"], "c": ln["c"],
                  "bounds": ln["bounds"], "src": "ocr"}
            if cls["interactive"]:
                el["i"] = 1
                el["why"] = cls["why"]
            els.append(el)

        els.sort(key=lambda e: (round(e["c"][1] / 48), e["c"][0]))
        els = els[:max_elements]
        snap.TABLE.assign(els)

        return {"ok": True, "screen": [W, H], "app": pkg,
                "elements": snap.public_view(els, include_bounds=False),
                "stats": {"a11y": sum(1 for e in els if e["src"] == "a11y"),
                          "both": sum(1 for e in els if e["src"] == "both"),
                          "ocr": sum(1 for e in els if e["src"] == "ocr")},
                "ocr_ms": ocr_ms, "a11y_ms": a11y_ms,
                "total_ms": int((time.time() - t0) * 1000)}


def register(ctx):
    """注册到 harness 工具面（ui2.* 命名空间）。"""

    @ctx.register_tool(
        "ui2.state", "结构化 UI 状态 v2：a11y×OCR 融合——a11y 提供零误报可交互性，"
        "OCR 补 canvas/自绘文本（src=ocr 的元素为启发式推断）。元素带句柄，"
        "ui.click_handle 直接操作。canvas/自绘界面必用",
        {"type": "object", "properties": {
            "max_elements": {"type": "integer", "default": 80}}})
    def ui2_state(br, eng, a):
        try:
            return UISnapshot(br).capture(max_elements=int(a.get("max_elements", 80)))
        except ImportError:
            return {"ok": False, "error": "rapidocr_onnxruntime 未安装：pip install rapidocr-onnxruntime"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

    @ctx.register_tool(
        "ui2.diff", "界面变更检测：对比两次融合状态的文本元素增删",
        {"type": "object", "properties": {
            "gap": {"type": "number", "default": 2.0}}})
    def ui2_diff(br, eng, a):
        try:
            s1 = UISnapshot(br).capture("d1")
            time.sleep(float(a.get("gap", 2.0)))
            s2 = UISnapshot(br).capture("d2")
            t1 = {e.get("t", "") for e in s1["elements"]}
            t2 = {e.get("t", "") for e in s2["elements"]}
            return {"ok": True,
                    "appeared": sorted(x for x in t2 - t1 if x)[:20],
                    "disappeared": sorted(x for x in t1 - t2 if x)[:20],
                    "app": s2["app"]}
        except ImportError:
            return {"ok": False, "error": "rapidocr_onnxruntime 未安装：pip install rapidocr-onnxruntime"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

    @ctx.register_tool(
        "ui2.wait_change", "等画面变化（点了按钮后确认生效），变化时返回新界面的可交互元素",
        {"type": "object", "properties": {
            "timeout": {"type": "number", "default": 15},
            "baseline_text": {"type": "string", "description": "当前页面的标志文本（消失=变化）"}}})
    def ui2_wait(br, eng, a):
        try:
            snap0 = UISnapshot(br).capture("w0")
            t0_texts = {e.get("t", "") for e in snap0["elements"]}
            baseline = a.get("baseline_text")
            deadline = time.time() + float(a.get("timeout", 15))
            while time.time() < deadline:
                time.sleep(1.5)
                s = UISnapshot(br).capture("w1")
                t1_texts = {e.get("t", "") for e in s["elements"]}
                if baseline:
                    changed = baseline not in "".join(t1_texts)
                else:
                    changed = t1_texts != t0_texts
                if changed:
                    return {"ok": True, "changed": True,
                            "interactive": [e for e in s["elements"] if e.get("i")][:12],
                            "texts": sorted(x for x in t1_texts if x)[:20]}
            return {"ok": True, "changed": False}
        except ImportError:
            return {"ok": False, "error": "rapidocr_onnxruntime 未安装：pip install rapidocr-onnxruntime"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

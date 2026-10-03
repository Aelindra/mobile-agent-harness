# -*- coding: utf-8 -*-
"""屏幕快照与句柄操作：a11y 语义裁剪 + 编号句柄（e0,e1,...）。

ui.snapshot 裁剪 a11y 树为紧凑元素列表；ui.click_handle / ui.text_handle
按句柄操作，操作前在实时树上重定位并做漂移校验。句柄表为模块级单例
（工具调用在设备锁内串行）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import selectors as S
from .node import Node

_SYSTEM_PKGS = ("com.android.systemui",)
_CONTAINER_CLS = ("android.view.View", "android.view.ViewGroup",
                  "android.widget.FrameLayout", "android.widget.LinearLayout",
                  "android.widget.RelativeLayout",
                  "androidx.recyclerview.widget.RecyclerView",
                  "android.widget.ScrollView")


class HandleTable:
    """句柄 -> 元素完整记录。每次快照重置。"""

    def __init__(self):
        self._els: Dict[str, dict] = {}
        self._n = 0

    def reset(self):
        self._els.clear()
        self._n = 0

    def assign(self, els: List[dict]) -> List[dict]:
        """按序给元素编号（e0,e1,...）；元素为带内部字段的完整记录。"""
        self.reset()
        for el in els:
            h = f"e{self._n}"
            self._n += 1
            el["h"] = h
            self._els[h] = el
        return els

    def get(self, handle: str) -> Optional[dict]:
        return self._els.get(str(handle))


TABLE = HandleTable()


# ---------------- a11y 语义元素提取 ----------------

def _interactive(n: Node) -> bool:
    return bool(n.clickable or n.editable or n.checkable or n.scrollable or n.long_clickable)


def _kind(n: Node) -> str:
    if n.editable:
        return "input"
    if n.checkable:
        return "toggle"
    if n.scrollable:
        return "scroll"
    if n.clickable or n.long_clickable:
        return "btn"
    if n.desc and not n.text:
        return "icon"
    return "text"


def _anchor(n: Node, pkg: str, scr: tuple = (0, 0, 0, 0)) -> dict:
    """完整记录（含句柄解析所需的 cls/bounds/pkg 内部字段，输出前剥离）。"""
    el = {"t": n.text or "", "d": n.desc or "", "k": _kind(n),
          "c": list(n.center), "bounds": list(n.bounds), "cls": n.cls,
          "pkg": pkg, "src": "a11y"}
    if n.res_id:
        el["id"] = n.res_id.split("/")[-1]
    if n.tooltip:
        el["tip"] = n.tooltip          # 零交互可读的工具提示（非 hover 所得）
    if n.visibility and n.visibility != "visible":
        el["vis"] = n.visibility
    # 屏幕外缓存内容（ViewPager 相邻页等）：已布局有文本但与视口不相交
    l, t, r, b = el["bounds"]
    sl, st, sr, sb = scr
    if (sr > sl or sb > st) and (r <= sl + 1 or l >= sr - 1 or b <= st + 1 or t >= sb - 1):
        el["off"] = 1
    if _interactive(n):
        el["i"] = 1
        if n.editable:
            el["e"] = 1
        if n.checkable:
            el["v"] = int(bool(n.checked))
        if n.scrollable:
            el["s"] = 1
    return el


def _el_score(el: dict) -> tuple:
    return (bool(el.get("t") or el.get("d") or el.get("tip")),
            el.get("cls") not in _CONTAINER_CLS,
            bool(el.get("i")))


def a11y_elements(root: Node, pkg: str, max_elements: int = 60) -> List[dict]:
    """树 → 裁剪排序后的语义元素（完整记录，未编号）。

    裁剪规则：剔除系统包/无文本且不可交互的节点；同中心嵌套去重
    （容器类让位给带文本或更具体类的元素）；阅读序（y 行 × x 列）排列。
    """
    by_center: Dict[Tuple[int, int], dict] = {}
    scr = root.bounds               # 根节点 bounds = 视口；degenerate 时用第一个子节点
    if scr[2] <= scr[0] or scr[3] <= scr[1]:
        scr = root.children[0].bounds if root.children else (0, 0, 0, 0)
    for n in root.iter():
        if not n.package or n.package in _SYSTEM_PKGS:
            continue
        if pkg and n.package != pkg:
            continue
        if not _interactive(n) and not (n.text or n.desc or n.tooltip):
            continue
        el = _anchor(n, pkg, scr)
        key = tuple(el["c"])
        old = by_center.get(key)
        if old is None or _el_score(el) > _el_score(old):
            by_center[key] = el
    out = [e for e in by_center.values()
           if e.get("t") or e.get("d") or e.get("i") or e.get("tip")]
    # 屏幕外缓存页排到正文之后（是"邻居页"不是"当前页"）
    out.sort(key=lambda e: (e.get("off", 0), round(e["c"][1] / 48), e["c"][0]))
    return out[:max_elements]


def public_view(els: List[dict], include_bounds: bool = False) -> List[dict]:
    """完整记录 → 输出视图（剥离内部字段，短键省 token）。"""
    view = []
    for el in els:
        v = {"h": el["h"], "k": el["k"], "c": el["c"]}
        if el.get("t"):
            v["t"] = el["t"]
        if el.get("d"):
            v["d"] = el["d"]
        if el.get("id"):
            v["id"] = el["id"]
        if el.get("src") != "a11y":
            v["src"] = el["src"]
        if el.get("i"):
            v["i"] = 1
        if el.get("e"):
            v["e"] = 1
        if el.get("k") == "toggle":
            v["v"] = int(bool(el.get("v")))
        if el.get("s"):
            v["s"] = 1
        if el.get("why"):
            v["why"] = el["why"]
        if el.get("tip"):
            v["tip"] = el["tip"]
        if el.get("off"):
            v["off"] = 1
        if include_bounds and el.get("bounds"):
            v["b"] = el["bounds"]
        view.append(v)
    return view


# ---------------- 句柄 → 当前界面节点解析 ----------------

def _dist(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def build_selector(el: dict) -> Optional[str]:
    pkg = el.get("pkg")
    if el.get("id"):
        return f"id@{el['id']} && pkg@{pkg}"
    if el.get("t"):
        return f"text@^{re.escape(el['t'])}$ && pkg@{pkg}"
    if el.get("d"):
        return f"desc@^{re.escape(el['d'])}$ && pkg@{pkg}"
    if el.get("cls") and el.get("i"):
        return f"class@{el['cls']} && [clickable=true] && pkg@{pkg}"
    return None


def resolve(bridge, handle: str):
    """句柄 → (mode, target)。mode="node"（a11y 重定位成功，target=Node）
    或 "coord"（纯视觉元素，target=元素记录，用中心点击）。"""
    el = TABLE.get(handle)
    if el is None:
        raise LookupError(f"句柄 {handle} 不存在或已过期——界面可能已变化，重新调用 ui.snapshot / ui2.state")
    sel = build_selector(el)
    if sel is None:
        return "coord", el
    nodes = S.find_nodes(bridge.dump(), sel, default_pkg=el.get("pkg"))
    if not nodes and el.get("cls") and el.get("i"):
        sel = f"class@{el['cls']} && pkg@{el.get('pkg')}"
        nodes = S.find_nodes(bridge.dump(), sel, default_pkg=el.get("pkg"))
    if not nodes:
        raise LookupError(f"句柄 {handle} 的目标已不在当前界面（选择器 {sel} 未命中）——重新打快照")
    node = min(nodes, key=lambda n: _dist(n.center, el["c"]))
    if _dist(node.center, el["c"]) > 160:
        raise LookupError(f"句柄 {handle} 目标漂移过远（{el['c']}→{list(node.center)}）——界面已变化，重新打快照")
    return "node", node


# ---------------- 工具注册 ----------------

def register(ctx):
    b = ctx.bridge

    @ctx.register_tool(
        "ui.snapshot",
        "读屏默认首选：a11y 语义裁剪快照，元素编号 e0/e1/...（token 约为 ui.dump 的 1/10）。"
        "k 类型 btn/input/toggle/scroll/icon/text；i=可交互 e=可输入 s=可滚动 v=开关态。"
        "用 ui.click_handle/ui.text_handle 按句柄操作",
        {"type": "object", "properties": {
            "pkg": {"type": "string", "description": "缺省取当前前台应用"},
            "max_elements": {"type": "integer", "default": 60},
            "include_bounds": {"type": "boolean", "default": False}}})
    def ui_snapshot(br, eng, a):
        try:
            pkg = a.get("pkg") or br.current_app()[0]
            if not pkg:
                return {"ok": False, "error": "无法确定前台应用（可显式传 pkg）"}
            els = TABLE.assign(a11y_elements(br.dump(), pkg,
                                             max_elements=int(a.get("max_elements", 60))))
            return {"ok": True, "app": pkg, "count": len(els),
                    "elements": public_view(els, bool(a.get("include_bounds"))),
                    "note": "句柄按本结果有效；操作用 ui.click_handle/ui.text_handle"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

    @ctx.register_tool(
        "ui.click_handle",
        "按快照句柄点击/长按（a11y 元素先重定位防漂移，纯视觉元素用快照中心）",
        {"type": "object", "properties": {
            "handle": {"type": "string"},
            "long_click": {"type": "boolean", "default": False}},
         "required": ["handle"]})
    def ui_click_handle(br, eng, a):
        try:
            mode, target = resolve(br, a["handle"])
            if mode == "coord":
                x, y = target["c"]
                if a.get("long_click"):
                    br.d.long_click(x, y, 1.0)
                else:
                    br.d.click(x, y)
                return {"ok": True, "method": "ocr_center",
                        "text": target.get("t"), "center": [x, y]}
            x, y = target.center
            if a.get("long_click"):
                br.d.long_click(x, y, 1.0)
            else:
                br.d.click(x, y)
            return {"ok": True, "method": "handle_node", "id": target.res_id,
                    "text": target.text, "center": [x, y]}
        except LookupError as ex:
            return {"ok": False, "error": str(ex), "error_type": "handle_stale"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

    @ctx.register_tool(
        "ui.hold_read",
        "长按读悬浮提示（hover/tooltip）：后台线程长按目标，按住期间读屏对比，返回新出现的提示文本。"
        "适合悬浮说明、图标长按菜单等按住才显示的提示。ocr=false 只比 a11y 树（快，~1s，普通 app 用）；"
        "ocr=true 加 OCR 对比（~6s，canvas/自绘界面用）",
        {"type": "object", "properties": {
            "handle": {"type": "string", "description": "快照句柄（与 selector/x,y 三选一）"},
            "selector": {"type": "string"},
            "pkg": {"type": "string"},
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "hold_ms": {"type": "integer", "default": 2500},
            "ocr": {"type": "boolean", "default": False}},
         })
    def ui_hold_read(br, eng, a):
        import threading
        import time as _t

        def _center():
            if a.get("handle"):
                mode, target = resolve(br, a["handle"])
                return list(target.center if mode == "node" else target["c"])
            if a.get("selector"):
                nodes = br.find(a["selector"], a.get("pkg"), timeout=3)
                return list(nodes[0].center)
            if a.get("x") is not None and a.get("y") is not None:
                return [int(a["x"]), int(a["y"])]
            raise ValueError("handle/selector/x,y 至少给一个")

        def _a11y_texts():
            try:
                return {(n.res_id, n.text, n.desc) for n in br.dump().iter()
                        if n.text or n.desc}
            except Exception:
                return set()

        try:
            c = _center()
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
        ocr_mode = bool(a.get("ocr"))
        hold_ms = int(a.get("hold_ms", 2500))

        pre_a11y = _a11y_texts()
        pre_ocr = None
        if ocr_mode:
            try:
                from ..ui_vision import UISnapshot
                pre = UISnapshot(br).capture("hr_pre")
                pre_ocr = {e.get("t", "") for e in pre.get("elements", []) if e.get("t")}
            except Exception:
                pre_ocr = None   # 无 OCR 依赖时静默退化为 a11y-only

        out = {}

        def _hold():
            try:
                br.d.swipe(c[0], c[1], c[0], c[1], hold_ms / 1000.0)
            except Exception:
                pass

        th = threading.Thread(target=_hold, daemon=True)
        th.start()
        _t.sleep(max(0.8, hold_ms / 1000.0 * 0.55))   # 按住期间读屏
        try:
            post_a11y = _a11y_texts()
            out["a11y_added"] = sorted(
                (t or d) for (_id, t, d) in (post_a11y - pre_a11y))[:20]
        except Exception as ex:  # noqa: BLE001
            out["a11y_error"] = str(ex)[:120]
        if ocr_mode:
            try:
                from ..ui_vision import UISnapshot
                post = UISnapshot(br).capture("hr_post")
                post_ocr = {e.get("t", "") for e in post.get("elements", []) if e.get("t")}
                out["ocr_added"] = sorted(x for x in (post_ocr - (pre_ocr or set())) if x)[:20]
            except Exception:
                pass
        th.join(timeout=max(1.0, hold_ms / 1000.0))
        out.update({"ok": True, "held_at": c, "hold_ms": hold_ms,
                    "note": "tooltip 若在松手即消失，a11y/ocr_added 即按住期间新出现的文本"})
        return out

    @ctx.register_tool(
        "ui.text_handle",
        "按句柄向输入元素写文本（a11y 元素走 set_text 原生通道链；纯视觉元素先点中心聚焦）",
        {"type": "object", "properties": {
            "handle": {"type": "string"}, "text": {"type": "string"},
            "clear": {"type": "boolean", "default": True}},
         "required": ["handle", "text"]})
    def ui_text_handle(br, eng, a):
        try:
            mode, target = resolve(br, a["handle"])
            if mode == "coord":
                x, y = target["c"]
                br.d.click(x, y)
                import time
                time.sleep(0.5)
                return br.set_text(a["text"], None, target.get("pkg"),
                                   clear=a.get("clear", True))
            sel = build_selector({"id": target.res_id.split("/")[-1] if target.res_id else "",
                                  "t": target.text, "d": target.desc,
                                  "pkg": target.package})
            return br.set_text(a["text"], sel, target.package,
                               clear=a.get("clear", True))
        except LookupError as ex:
            return {"ok": False, "error": str(ex), "error_type": "handle_stale"}
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}

# -*- coding: utf-8 -*-
"""L1 节点树：把 u2 dump 的 XML 解析为带完整控件语义的 Node 树。

这是与旧 `uiautomator dump` 用法的本质区别：文本不再是唯一信息，
class/resource-id/content-desc/checkable/checked/clickable/scrollable/long-clickable
全部结构化，供选择器引擎与 harness 引擎使用。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterator, List, Optional


class Node:
    __slots__ = ("attrs", "children", "parent", "depth", "path")

    def __init__(self, attrs, children=None, parent=None, depth=0, path=""):
        self.attrs = attrs
        self.children: List[Node] = children or []
        self.parent: Optional[Node] = parent
        self.depth = depth
        self.path = path  # 形如 "0/1/3" 的深度路径

    # ---- 文本语义 ----
    @property
    def text(self) -> str:
        return self.attrs.get("text", "") or ""

    @property
    def res_id(self) -> str:
        return self.attrs.get("resource-id", "") or ""

    @property
    def desc(self) -> str:
        return self.attrs.get("content-desc", "") or ""

    @property
    def cls(self) -> str:
        return self.attrs.get("class", "") or ""

    @property
    def package(self) -> str:
        return self.attrs.get("package", "") or ""

    @property
    def hint(self) -> str:
        return self.attrs.get("hint", "") or ""

    @property
    def tooltip(self) -> str:
        """tooltipText（API 26+）：开发者声明的工具提示——零交互可读，无需 hover。"""
        return self.attrs.get("tooltipText", "") or ""

    @property
    def visibility(self) -> str:
        return self.attrs.get("visibility", "") or ""

    # ---- 几何 ----
    @property
    def bounds(self):
        m = re.findall(r"-?\d+", self.attrs.get("bounds", "[0,0][0,0]"))
        if len(m) == 4:
            return tuple(int(x) for x in m)
        return (0, 0, 0, 0)

    @property
    def center(self):
        l, t, r, b = self.bounds
        return ((l + r) // 2, (t + b) // 2)

    # ---- 状态布尔 ----
    def _b(self, key) -> bool:
        return str(self.attrs.get(key, "false")).lower() == "true"

    @property
    def clickable(self): return self._b("clickable")

    @property
    def checkable(self): return self._b("checkable")

    @property
    def checked(self): return self._b("checked")

    @property
    def scrollable(self): return self._b("scrollable")

    @property
    def long_clickable(self): return self._b("long-clickable")

    @property
    def enabled(self): return self._b("enabled")

    @property
    def focusable(self): return self._b("focusable")

    @property
    def focused(self): return self._b("focused")

    @property
    def selected(self): return self._b("selected")

    @property
    def editable(self):
        # dump XML 无 editable 属性，按控件类型推断
        return self.cls.endswith("EditText")

    # ---- 树操作 ----
    def iter(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.iter()

    def ancestors(self) -> Iterator["Node"]:
        p = self.parent
        while p:
            yield p
            p = p.parent

    def to_dict(self, max_nodes: int = 4000):
        out = {"count": 0, "nodes": []}
        for n in self.iter():
            if out["count"] >= max_nodes:
                out["truncated"] = True
                break
            out["nodes"].append({
                "path": n.path,
                "package": n.package,
                "class": n.cls,
                "id": n.res_id,
                "text": n.text,
                "desc": n.desc,
                "bounds": list(n.bounds),
                "center": list(n.center),
                "clickable": n.clickable,
                "long_clickable": n.long_clickable,
                "checkable": n.checkable,
                "checked": n.checked,
                "scrollable": n.scrollable,
                "editable": n.editable,
                "focused": n.focused,
                "enabled": n.enabled,
            })
            out["count"] += 1
        return out


def parse_dump(xml: str) -> Node:
    """解析 u2 dump_hierarchy 的 XML 为 Node 树。"""
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    return _build(root, None, 0, "")


def _build(el: ET.Element, parent: Optional[Node], depth: int, path: str) -> Node:
    node = Node(attrs=dict(el.attrib), parent=parent, depth=depth, path=path)
    kids = []
    for i, child in enumerate(el):
        if child.tag != "node":
            continue
        kids.append(_build(child, node, depth + 1, f"{path}/{i}" if path else str(i)))
    node.children = kids
    return node

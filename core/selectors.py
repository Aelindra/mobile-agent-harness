# -*- coding: utf-8 -*-
"""L2 选择器引擎：GKD 风格语法，解析后在本机 dump 的 Node 树上做语义匹配。

语法（大小写敏感，组合用 ` && `）：
  id@com.example.app:id/title             resource-id（省略包名前缀时按 default_pkg 补全）
  text@^Settings$                          text 正则（re.search 语义，^$ 锚定精确匹配）
  desc@Send                                content-desc 正则
  class@android.widget.Switch             class 精确；不带点时按后缀匹配
  pkg@com.android.settings                包名过滤（强烈建议始终带，避免点到别的 app）
  [clickable=true] [checked=false] [editable=true] [scrollable=true] ... 属性过滤
  nth@0 / nth@-1                          第 n 个匹配（0 基，负数从尾部数）
  parent@<sel>                            父节点匹配 <sel>
  child@<sel>                             存在直接子节点匹配 <sel>
  under@<sel>                             存在祖先匹配 <sel>
  hastext@<regex>                         存在后代节点文本匹配（列表项定位）

禁止裸坐标：任何 harness 步骤里不允许出现 x/y。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .node import Node

_KINDS = {"id", "text", "desc", "class", "pkg", "nth", "parent", "child", "under", "hastext"}
_PROPS = {"clickable", "checkable", "checked", "scrollable", "long-clickable", "long_clickable",
          "enabled", "focusable", "focused", "selected", "editable", "password"}


def _compile_regex(pattern: str):
    try:
        return re.compile(pattern)
    except re.error:
        return re.compile("^" + re.escape(pattern) + "$")


def parse(selector: str) -> List[Tuple[str, str]]:
    """把选择器字符串解析为 (kind, arg) 列表。"""
    parts: List[Tuple[str, str]] = []
    for raw in selector.split("&&"):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]"):
            body = s[1:-1]
            if "=" in body:
                k, v = body.split("=", 1)
                k, v = k.strip(), v.strip().lower()
                if k not in _PROPS:
                    raise SelectorError(f"未知属性过滤 [{k}]，支持: {_PROPS}")
                parts.append(("prop:" + k, v))
            else:
                raise SelectorError(f"属性过滤需要 [k=v] 形式: {s}")
            continue
        if "@" not in s:
            raise SelectorError(f"无法解析的选择器片段: {s}（应为 kind@value 或 [k=v]）")
        k, v = s.split("@", 1)
        k = k.strip()
        if k not in _KINDS:
            raise SelectorError(f"未知选择器类型 {k}@，支持: {sorted(_KINDS)}")
        parts.append((k, v))
    if not parts:
        raise SelectorError("空选择器")
    return parts


class SelectorError(ValueError):
    pass


class SelectorNotFound(LookupError):
    def __init__(self, selector, pkg=None):
        self.selector, self.pkg = selector, pkg
        super().__init__(f"选择器未命中: {selector}" + (f" (pkg={pkg})" if pkg else ""))


def _match_one(node: Node, kind: str, arg: str, default_pkg: Optional[str]) -> bool:
    if kind == "id":
        full = arg if "/" in arg and ":" in arg else (
            f"{default_pkg}:id/{arg}" if default_pkg and ":" not in arg else arg)
        return node.res_id == full
    if kind == "text":
        return bool(_compile_regex(arg).search(node.text))
    if kind == "desc":
        return bool(_compile_regex(arg).search(node.desc))
    if kind == "class":
        return node.cls == arg or ("." not in arg and node.cls.endswith("." + arg))
    if kind == "pkg":
        return node.package == arg
    if kind == "hastext":
        return any(_compile_regex(arg).search(n.text) for n in node.iter())
    if kind == "parent":
        return node.parent is not None and match(node.parent, arg, default_pkg)
    if kind == "child":
        return any(match(c, arg, default_pkg) for c in node.children)
    if kind == "under":
        return any(match(a, arg, default_pkg) for a in node.ancestors())
    raise SelectorError(f"相对/扩展片段须由 find 处理: {kind}@{arg}")


def match(node: Node, selector: str, default_pkg: Optional[str] = None) -> bool:
    """单节点是否满足选择器（不含 nth）。"""
    for kind, arg in parse(selector):
        if kind == "nth":
            continue
        if kind.startswith("prop:"):
            prop = kind[5:]
            want = arg == "true"
            have = getattr(node, prop.replace("-", "_"))
            if bool(have) != want:
                return False
        elif not _match_one(node, kind, arg, default_pkg):
            return False
    return True


def find_nodes(root: Node, selector: str, default_pkg: Optional[str] = None) -> List[Node]:
    """在树上求值选择器，返回按深度优先序的节点列表（应用 nth 后）。"""
    parts = parse(selector)
    nth = None
    for kind, arg in parts:
        if kind == "nth":
            nth = int(arg)
    hits = [n for n in root.iter() if match(n, selector, default_pkg)]
    if nth is not None:
        try:
            hits = [hits[nth]]
        except IndexError:
            return []
    return hits


def direct_parts(selector: str) -> List[Tuple[str, str]]:
    """可直接翻译为 u2 UiSelector 的片段（剔除 nth 与相对定位）。"""
    out = []
    for kind, arg in parse(selector):
        if kind == "nth" or kind in ("parent", "child", "under", "hastext"):
            continue
        out.append((kind, arg))
    return out


def has_relative(selector: str) -> bool:
    return any(k in ("parent", "child", "under", "hastext", "nth") for k, _ in parse(selector))


def to_u2_kwargs(selector: str, default_pkg: Optional[str] = None) -> dict:
    """把选择器中可直接表达的部分翻译为 uiautomator2 选择器参数。

    正则统一走 textMatches/descriptionMatches（Java matches 语义=全匹配，
    非 ^...$ 锚定的模式两侧补 .* 以近似 re.search）。
    """
    kw = {}
    for kind, arg in direct_parts(selector):
        if kind == "id":
            kw["resourceId"] = arg if (":" in arg and "/" in arg) else (
                f"{default_pkg}:id/{arg}" if default_pkg and ":" not in arg else arg)
        elif kind == "text":
            pat = arg if (arg.startswith("^") or arg.endswith("$")) else f".*{arg}.*"
            kw["textMatches"] = pat
        elif kind == "desc":
            pat = arg if (arg.startswith("^") or arg.endswith("$")) else f".*{arg}.*"
            kw["descriptionMatches"] = pat
        elif kind == "class":
            if "." in arg:
                kw["className"] = arg
            else:
                kw["classNameMatches"] = f".*\\.{re.escape(arg)}"
        elif kind == "pkg":
            kw["packageName"] = arg
        elif kind.startswith("prop:"):
            prop = kind[5:].replace("long-clickable", "longClickable").replace("long_clickable", "longClickable")
            if prop in ("clickable", "checkable", "checked", "scrollable", "enabled",
                        "focusable", "focused", "selected", "longClickable"):
                kw[prop] = (arg == "true")
            # editable/password 无原生字段，跳过（由树侧过滤）
    return kw

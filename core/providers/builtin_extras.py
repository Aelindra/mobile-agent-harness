# -*- coding: utf-8 -*-
"""原语工具：input.*（"指定位置写入"通用层）与 events.*（监控接线）。

裸坐标注入原语 + monitor.py 能力的工具面接线。
"""
from __future__ import annotations

import os
import tempfile
import time

from ..monitor import Monitor
from ..runtime import Context


def register_extras(ctx: Context):
    b = ctx.bridge

    @ctx.register_tool(
        "input.tap", "裸坐标点击（通用层原语：不解析选择器，直接注入；长按调大 duration_ms）",
        {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "duration_ms": {"type": "integer", "default": 50,
                            "description": ">120 视为长按"}}, "required": ["x", "y"]})
    def input_tap(br, eng, a):
        b.ensure()
        if a.get("duration_ms", 50) > 120:
            b.d.long_click(a["x"], a["y"], a["duration_ms"] / 1000.0)
        else:
            b.d.click(a["x"], a["y"])
        return {"ok": True, "x": a["x"], "y": a["y"]}

    @ctx.register_tool(
        "input.swipe", "裸坐标滑动（通用层原语）",
        {"type": "object", "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
            "duration_ms": {"type": "integer", "default": 300}}, "required": ["x1", "y1", "x2", "y2"]})
    def input_swipe(br, eng, a):
        b.ensure()
        b.d.swipe(a["x1"], a["y1"], a["x2"], a["y2"], a.get("duration_ms", 300) / 1000.0)
        return {"ok": True, "from": [a["x1"], a["y1"]], "to": [a["x2"], a["y2"]]}

    @ctx.register_tool(
        "input.key", "按键注入（back/home/menu/volume_up/volume_down/power/enter... 或键码整数）",
        {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]})
    def input_key(br, eng, a):
        k = a["key"]
        b.ensure()
        b.d.press(int(k) if k.isdigit() else k)
        return {"ok": True, "key": k}

    @ctx.register_tool(
        "input.clipboard", "剪贴板读写（set=写入设备剪贴板；get 读回，需 u2 支持）",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "text": {"type": "string"}}, "required": ["action"]})
    def input_clipboard(br, eng, a):
        b.ensure()
        if a["action"] == "set":
            if "text" not in a:
                return {"ok": False, "error": "set 需要 text"}
            b.d.set_clipboard(a["text"])
            return {"ok": True, "length": len(a["text"])}
        d = b.d
        for way in (lambda: d.get_clipboard(), lambda: d.clipboard):
            try:
                v = way()
                if isinstance(v, str):
                    return {"ok": True, "text": v}
            except Exception:
                continue
        return {"ok": False, "error": "当前 u2 版本不支持剪贴板读取"}

    # ---- events.*：Monitor 接线 ----
    mon_holder = {"m": Monitor(b, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "monitor_shots"))}

    @ctx.register_tool(
        "events.capture", "执行动作同时捕获瞬时 Toast/弹层（Monitor 接线；action=click 可带 selector；expect 为正则过滤）",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["click", "none"], "default": "click"},
            "selector": {"type": "string"}, "pkg": {"type": "string"},
            "timeout": {"type": "number", "default": 6},
            "expect": {"type": "string", "description": "text/desc 命中正则，缺省收全部瞬态"}}})
    def events_capture(br, eng, a):
        m = mon_holder["m"]
        act = None
        if a.get("action", "click") == "click":
            if not a.get("selector"):
                return {"ok": False, "error": "action=click 需要 selector"}
            sel, pkg = a["selector"], a.get("pkg")
            def act():
                try:
                    b.click(sel, pkg)
                except Exception:
                    pass
        toasts = m.wait_toast(timeout=a.get("timeout", 6), expect=a.get("expect"), action=act)
        return {"ok": True, "toasts": toasts, "count": len(toasts)}

    @ctx.register_tool(
        "events.snapshot", "单次前台状态快照（含游离窗口节点=toast/悬浮窗候选）",
        {"type": "object"})
    def events_snapshot(br, eng, a):
        return {"ok": True, **mon_holder["m"].snapshot()}

    @ctx.register_tool(
        "events.record_start", "后台连拍+状态流开始（record_stop 收结果）",
        {"type": "object", "properties": {"interval": {"type": "number", "default": 0.8}}})
    def events_record_start(br, eng, a):
        mon_holder["m"].record_start(interval=a.get("interval", 0.8))
        return {"ok": True}

    @ctx.register_tool(
        "events.record_stop", "停止后台记录，返回 toasts/帧数/目录",
        {"type": "object"})
    def events_record_stop(br, eng, a):
        return {"ok": True, **mon_holder["m"].record_stop()}

    # ---- ui.snapshot + 句柄操作（读屏快路径）----
    from .. import snapshot
    snapshot.register(ctx)

    # ---- task.*：业务意图日志（mah-console 业务时间线的数据源）----
    from .. import journal
    journal.register(ctx)

    # ---- ui2.find_templates / template.add：图标→语义声明图集 ----
    from .. import vision_match
    vision_match.register(ctx)

    # ---- knowledge.*：知识包加载（guide=上下文面/atlas+states=运行时面）----
    from .. import knowledge
    knowledge.register(ctx)

    # ---- events.tail/wait：事件面（logcat 推送 + 帧差分）----
    from .. import eventbus
    eventbus.register(ctx)

    # ---- ui2.*：a11y×OCR 融合状态（agent 的眼睛）----
    from .. import ui_vision
    ui_vision.register(ctx)

    # ---- sys.*：运行时自省 ----
    @ctx.register_tool(
        "sys.capabilities", "能力发现：各缝当前可用 provider 及 privilege/stealth 元数据；插件热载入口",
        {"type": "object", "properties": {
            "reload_plugins": {"type": "boolean", "default": False,
                               "description": "true 时先热载插件目录再报告"}}})
    def sys_capabilities(br, eng, a):
        if a.get("reload_plugins"):
            ctx_info = getattr(ctx, "_plugin_runtime", None)
            if ctx_info:
                return {"ok": True, "reloaded": ctx_info.reload_changed(),
                        "capabilities": ctx.capabilities(), "owners": ctx.owners()}
        return {"ok": True, "capabilities": ctx.capabilities(), "owners": ctx.owners()}

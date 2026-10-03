# -*- coding: utf-8 -*-
"""业务意图日志：task.begin / task.note / task.end 写入 transcript。

事件（kinds: task_begin/task_note/task_end）供 mah-console 渲染
"目标 → 想法 → 预期 → 结果"时间线。task_id 由调用方持有并回传。
"""
from __future__ import annotations

import uuid


def register(ctx):
    tr = ctx.transcript

    @ctx.register_tool(
        "task.begin", "开始一个业务任务：声明目标（如'领所有商品的券'）。返回 task_id，"
        "后续 task.note/task.end 回传它",
        {"type": "object", "properties": {
            "goal": {"type": "string", "description": "业务目标一句话"}},
         "required": ["goal"]})
    def task_begin(br, eng, a):
        task_id = uuid.uuid4().hex[:8]
        tr.log("task_begin", task_id=task_id, goal=str(a["goal"])[:500])
        return {"ok": True, "task_id": task_id}

    @ctx.register_tool(
        "task.note", "记录当前想法/判定/预期（业务化历史的核心）：比如'我判断这个按钮是搜索入口，"
        "点击预期出现搜索框'。可能错，先声明后验证",
        {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "text": {"type": "string", "description": "当前想法/判断"},
            "expect": {"type": "string", "description": "预期会发生什么"},
            "about": {"type": "string", "description": "相关句柄/元素，如 e12"}},
         "required": ["text"]})
    def task_note(br, eng, a):
        tr.log("task_note", task_id=a.get("task_id"), text=str(a["text"])[:500],
               expect=(str(a["expect"])[:300] if a.get("expect") else None),
               about=a.get("about"))
        return {"ok": True}

    @ctx.register_tool(
        "task.end", "结束任务：实际结果与目标对照（成功/失败/原因）",
        {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "ok": {"type": "boolean"},
            "outcome": {"type": "string", "description": "实际结果一句话"}},
         "required": ["ok"]})
    def task_end(br, eng, a):
        tr.log("task_end", task_id=a.get("task_id"), ok=bool(a["ok"]),
               outcome=(str(a["outcome"])[:500] if a.get("outcome") else None))
        return {"ok": True}

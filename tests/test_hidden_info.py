# -*- coding: utf-8 -*-
"""零交互隐藏信息解析的离线单测：tooltipText / 屏幕外缓存页 / 可见性标记。

运行: python tests/test_hidden_info.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.node import parse_dump
from core.snapshot import a11y_elements

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (f"  | {detail}" if detail and not cond else ""))


XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
 <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.app"
       content-desc="" checkable="false" bounds="0,0,1080,2400">
  <node index="0" text="" resource-id="com.app:id/btn_share" class="android.widget.Button"
        package="com.app" content-desc="分享" tooltipText="长按也可分享" checkable="false"
        clickable="true" bounds="100,200,300,280"/>
  <node index="1" text="下一页内容标题" resource-id="com.app:id/next_title"
        class="android.widget.TextView" package="com.app" content-desc="" checkable="false"
        bounds="1300,100,1600,160"/>
  <node index="2" text="左邻页" resource-id="com.app:id/prev_title"
        class="android.widget.TextView" package="com.app" content-desc="" checkable="false"
        bounds="-500,100,-200,160"/>
  <node index="3" text="" resource-id="com.app:id/icon" class="android.widget.ImageView"
        package="com.app" content-desc="" tooltipText="仅提示无文本" checkable="false"
        clickable="false" bounds="400,400,460,460"/>
 </node>
</hierarchy>"""


def main():
    root = parse_dump(XML)
    els = a11y_elements(root, "com.app")
    by_id = {e.get("id"): e for e in els}

    check("tooltip 进快照", by_id["btn_share"].get("tip") == "长按也可分享",
          str(by_id.get("btn_share")))
    check("tooltip-only 节点不被过滤", "icon" in by_id, str(sorted(by_id)))
    check("tooltip-only 标记为非交互（不冒充按钮）", "i" not in by_id.get("icon", {}))
    check("屏幕外缓存页（右邻）标记 off", by_id["next_title"].get("off") == 1,
          str(by_id.get("next_title")))
    check("屏幕外缓存页（左邻）标记 off", by_id["prev_title"].get("off") == 1)
    check("屏内元素不标 off", "off" not in by_id["btn_share"])
    check("屏幕外元素排在正文之后",
          not any(e.get("off") for e in els[:2]) and any(e.get("off") for e in els[-2:]),
          str([(e.get("id"), e.get("off")) for e in els]))

    # tooltip 节点可被 build_selector/句柄解析用（有 id → id 选择器）
    from core.snapshot import build_selector
    check("tooltip 节点可回指操作", build_selector(by_id["btn_share"]) ==
          "id@btn_share && pkg@com.app")

    # MIUI 实测事件行（Displayed 格式）
    from core.eventbus import LogcatTail
    e = LogcatTail._parse("1791029209.965 3266 3361 I ActivityTaskManager: "
                          "Displayed com.example.game/com.example.game.WelcomeActivity for user 0: +36ms")
    check("事件面: MIUI Displayed 行→page_change",
          bool(e) and e.get("type") == "page_change" and e.get("pkg") == "com.example.game",
          str(e))
    print(f"\n== hidden-info: {len(PASS)} pass, {len(FAIL)} fail ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

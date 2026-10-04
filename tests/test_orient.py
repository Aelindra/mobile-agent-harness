# -*- coding: utf-8 -*-
"""L0/L1/L2 离线测试：上下文探针解析、seq 序列判别式、假设枚举排序、包匹配。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.context import probe_context, match_packs          # noqa: E402
from core.knowledge import evaluate_states, hypotheses        # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


class FakeShell:
    def __init__(self, replies):
        self.replies = replies

    def __call__(self, cmd, timeout=30):
        for key, val in self.replies.items():
            if key in cmd:
                return val
        return ""


class FakeBr:
    serial = "fake"
    d = type("D", (), {"window_size": staticmethod(lambda: (2400, 1080))})()

    def __init__(self):
        self.shell = FakeShell({
            "mCurrentFocus": "mCurrentFocus=Window{7f u0 com.example.game/com.example.game.MainActivity}",
            "dumpsys package com.example.game":
                "versionName=2.14.0\nversionCode=214012\nfirstInstallTime=2025-01-02 10:00:00\n"
                "lastUpdateTime=2026-09-30 08:00:00\n",
            "mWakefulness": "mWakefulness=Awake\n",
        })

    def current_app(self):
        return "com.example.game", "com.example.game.MainActivity"


# ---- L0：探针解析 ----
print("[L0] probe_context 解析")
p = probe_context(FakeBr())
check("包名/Activity", p.get("package") == "com.example.game"
      and p.get("activity", "").endswith("MainActivity"), str(p))
check("版本三元组", p.get("version") == "2.14.0" and p.get("version_code") == 214012, str(p))
check("安装/更新时间", p.get("updated_at", "").startswith("2026-09-30"), str(p))
check("亮屏", p.get("screen") == "Awake", str(p))
check("横屏判定", p.get("display", {}).get("orientation") == "landscape", str(p))
check("无错误", not p.get("errors"), str(p.get("errors")))

# ---- 包匹配 ----
print("[L0] match_packs")
packs = {
    "game.example": {"meta": {"app": "com.example.game"}},
    "game.other": {"meta": {"game": "com.other.pkg"}},
    "game.multi": {"meta": {"apps": ["com.a", "com.b"]}},
    "nomatch": {"meta": {"name": "x"}},
}
check("app 字段命中", match_packs(packs, "com.example.game") == ["game.example"])
check("game 字段命中", match_packs(packs, "com.other.pkg") == ["game.other"])
check("apps 列表命中", "game.multi" in match_packs(packs, "com.b"))
check("不误匹配", match_packs(packs, "com.unknown") == [])

# ---- L1：单帧判别式 + 排序 ----
print("[L1] evaluate_states 单帧")
states = {
    "s_full": {"all": ["overlay=normal", "motion=high"], "desc": "全命中"},
    "s_partial": {"all": ["overlay=normal", "hud=present"], "desc": "缺 hud"},
    "s_wrong": {"all": ["overlay=gray"], "desc": "冲突"},
}
ev = {"overlay": {"level": "normal"}, "motion": {"label": "high"}}
rs = evaluate_states(ev, states)
by = {r["state"]: r for r in rs}
check("全命中 ok", by["s_full"]["ok"] and by["s_full"]["score"] == 1.0, str(by["s_full"]))
check("缺证据单列", by["s_partial"]["insufficient_evidence"]
      and by["s_partial"]["score"] == 0.5, str(by["s_partial"]))
check("冲突 failed", not by["s_wrong"]["ok"] and by["s_wrong"]["score"] == 0.0, str(by["s_wrong"]))
check("排序=分数降序", [r["state"] for r in rs][0] == "s_full", str([r["state"] for r in rs]))
hyp = hypotheses(rs)
check("假设枚举只含正分候选", len(hyp) == 1 and hyp[0]["state"] == "s_partial", str(hyp))
check("纯冲突不进候选", all(h["state"] != "s_wrong" for h in hyp), str(hyp))
check("假设列出缺什么", "hud=present" in hyp[0]["missing"], str(hyp[0]))

# ---- L1：seq 序列判别式 ----
print("[L1] seq 序列判别式")
timeline = [
    {"t": 0.0, "overlay": {"level": "normal"}, "motion": {"label": "static"}},
    {"t": 0.5, "overlay": {"level": "normal"}, "motion": {"label": "static"}},
    {"t": 1.0, "overlay": {"level": "dimmed"}, "motion": {"label": "low"}},
    {"t": 1.5, "overlay": {"level": "normal"}, "motion": {"label": "high"}},
    {"t": 2.0, "overlay": {"level": "normal"}, "motion": {"label": "high"}},
]
seq_states = {
    "seq_ok": {"seq": ["overlay=normal", "overlay=dimmed", "motion=high"]},
    "seq_mid": {"seq": ["overlay=normal", "overlay=gray", "motion=high"]},
    "seq_one": {"seq": ["overlay=gray"]},
}
rs2 = evaluate_states(ev, seq_states, timeline)
by2 = {r["state"]: r for r in rs2}
check("seq 全命中 ok", by2["seq_ok"]["ok"] and by2["seq_ok"]["score"] == 1.0,
      str(by2["seq_ok"]))
check("seq 中断=failed", not by2["seq_mid"]["ok"] and by2["seq_mid"]["score"] == 0.0
      and "中断" in by2["seq_mid"]["failed"][0]["actual"], str(by2["seq_mid"]))
check("seq 全中断", not by2["seq_one"]["ok"], str(by2["seq_one"]))
check("无时间线→缺证据", not evaluate_states(ev, seq_states)[0]["ok"]
      and evaluate_states(ev, seq_states)[0]["insufficient_evidence"])
check("乱序时间线不命中", not evaluate_states(
    ev, seq_states, list(reversed(timeline)))[0]["ok"])

# ---- 混合 all+seq ----
mix = {"combo": {"all": ["overlay=normal"], "seq": ["motion=static", "motion=high"]}}
rs3 = evaluate_states(ev, mix, timeline)
check("all+seq 组合 ok", rs3[0]["ok"] and rs3[0]["score"] == 1.0, str(rs3[0]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

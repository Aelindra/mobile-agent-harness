# -*- coding: utf-8 -*-
"""定时任务：schedules.json → 到点执行 harness 工具（含前台占用守卫）。

schedules.json（repo 根，gitignored）：
{
  "schedules": [
    {"name": "daily-settings-check",
     "hhmm": "09:30",            // 每天该时刻跑一次
     "pkg": "com.android.settings",
     "tool": "launch",
     "params": {},
     "guard": true,              // 前台被用户占用（非目标 app/锁屏/桌面）则本次跳过
     "enabled": true}
  ]
}

运行：mah-scheduler   （或 python -m core.scheduler）
每次运行落 logs/scheduler_runs.jsonl，UI（mah-console）读它渲染历史。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime


def schedules_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "schedules.json")


def load_schedules() -> list:
    p = schedules_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f) or {}).get("schedules", [])
    except Exception:
        return []


def _foreground() -> str:
    try:
        out = subprocess.run(["adb", "shell", "dumpsys window | grep mCurrentFocus"],
                             capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
        import re
        m = re.search(r"mCurrentFocus=Window\{[^}]*?\s([^ /\}]+)(?:/([^ \}]+))?\}", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def busy(fg: str, pkg: str) -> bool:
    """占用守卫：前台不是目标 app/锁屏/空（息屏）则视为用户占用。"""
    if not fg:
        return False                      # 息屏/无焦点 = 空闲
    return pkg not in fg and "Keyguard" not in fg and "NotificationShade" not in fg


def run_schedule(s: dict) -> dict:
    """同步执行一条 schedule（独立进程调 CLI，避免与常驻进程状态纠缠）。"""
    t0 = time.time()
    pkg, tool = s.get("pkg"), s.get("tool")
    if not pkg or not tool:
        return {"ok": False, "error": "schedule 缺 pkg/tool"}
    if s.get("guard", True):
        fg = _foreground()
        if busy(fg, pkg):
            return {"ok": False, "skipped": "busy", "foreground": fg}
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys_executable(), "-m", "core.cli", "run", "--pkg", pkg,
           "--tool", tool, "--params", json.dumps(s.get("params", {}))]
    if s.get("trust"):
        cmd.append("--trust")
    r = subprocess.run(cmd, cwd=repo, capture_output=True, timeout=600,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = r.stdout.decode("utf-8", "replace")[-2000:]
    try:
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception:
        payload = {"raw": out[:300]}
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "name": s.get("name"),
           "pkg": pkg, "tool": tool, "ok": bool(payload.get("ok")),
           "elapsed_ms": int((time.time() - t0) * 1000),
           "verdict": str(payload.get("verdict") or payload.get("error"))[:200]}
    _append_run(rec)
    return rec


def _append_run(rec: dict):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(repo, "logs", "scheduler_runs.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def sys_executable() -> str:
    import sys
    return sys.executable


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="mah-scheduler")
    ap.add_argument("--once", action="store_true", help="只检查一轮即退出（调试）")
    ap.add_argument("--interval", type=float, default=30)
    a = ap.parse_args(argv)
    last_date: dict = {}
    print(f"[mah-scheduler] 已启动（每 {a.interval}s 检查；schedules.json 见 repo 根）")
    while True:
        now = datetime.now()
        hhmm = now.strftime("%H:%M")
        today = now.strftime("%Y%m%d")
        for s in load_schedules():
            if not s.get("enabled", True):
                continue
            if s.get("hhmm") != hhmm:
                continue
            key = s.get("name", "?")
            if last_date.get(key) == today:
                continue
            last_date[key] = today
            print(f"[run] {key} -> {s.get('pkg')}.{s.get('tool')}")
            rec = run_schedule(s)
            print(f"      {json.dumps(rec, ensure_ascii=False)[:180]}")
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()

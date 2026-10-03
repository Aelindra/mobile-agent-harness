# -*- coding: utf-8 -*-
"""端到端验收：单命令、无人值守（wake → dry-run 预检 → 真实执行 → 证据落盘）。

  python run_acceptance.py                       # 默认：内置 Settings 示例 harness
  python run_acceptance.py --pkg <pkg> --tool <tool> --params '{"k":"v"}' [--trust]

trust=dry_run 的 harness 真实执行需 --trust；每步工具调用/截图/transcript/summary
全部落 acceptance/（gitignored）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.providers import assemble                  # noqa: E402
from core.registry import Registry                   # noqa: E402
from core.harness import TrustError                  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default="com.android.settings")
    ap.add_argument("--tool", default="launch")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--trust", action="store_true",
                    help="允许 trust=dry_run 的 harness 真实执行")
    a = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    acc_dir = os.path.join(ROOT, "acceptance")
    shots = os.path.join(acc_dir, "shots")
    os.makedirs(shots, exist_ok=True)
    tr_path = os.path.join(acc_dir, f"transcript_{ts}.jsonl")
    summary_path = os.path.join(acc_dir, f"summary_{ts}.json")

    result = {"ts": ts, "pkg": a.pkg, "tool": a.tool,
              "transcript": tr_path, "steps": []}
    tr = None
    try:
        ctx, pipe, pr = assemble({"transcript_path": tr_path, "shot_dir": shots,
                                  "ctx_name": "acceptance"})
        tr = ctx.transcript
        bridge = ctx.bridge
        engine = ctx.engine
        reg = Registry(ctx, pipe, engine, plugin_runtime=pr)
        tr.log("acceptance_start", pkg=a.pkg, tool=a.tool, device=bridge.serial)
        result["device"] = bridge.serial

        # 0. 设备 setup：亮屏 + 按需解非安全锁屏（agent 自身能力）
        wake = reg.call("device.wake_unlock", {})
        result["steps"].append({"step": "setup_wake", "ok": wake.get("ok", False)})

        # 1. dry-run 预检：只验证选择器命中，无副作用
        r0 = engine.run(a.pkg, a.tool, json.loads(a.params), dry_run=True)
        result["steps"].append({"tool": f"{a.pkg}.{a.tool}", "phase": "dry_run",
                                "ok": r0["ok"], "elapsed_ms": r0["elapsed_ms"]})

        # 2. 真实执行
        try:
            r1 = engine.run(a.pkg, a.tool, json.loads(a.params),
                            allow_untrusted=a.trust)
        except TrustError as ex:
            r1 = {"ok": False, "error": str(ex)}
        result["steps"].append({"tool": f"{a.pkg}.{a.tool}", "phase": "real",
                                "ok": r1["ok"], "elapsed_ms": r1.get("elapsed_ms"),
                                "stale_marked": r1.get("stale_marked", False),
                                "error": r1.get("error")})

        ev = os.path.join(shots, f"ev_done_{ts}.png")
        try:
            bridge.screenshot(ev)
            result["steps"].append({"evidence": ev})
        except Exception:
            pass

        result["ok"] = bool(r1["ok"])
        result["verdict"] = ("PASS：harness 链路完成（dry-run 预检 + 真实执行），证据见 acceptance/shots/"
                             if r1["ok"] else f"FAIL: {str(r1.get('error', ''))[:300]}")
    except Exception as ex:  # noqa: BLE001
        result["ok"] = False
        result["verdict"] = f"FAIL: {type(ex).__name__}: {ex}"
    finally:
        if tr is not None:
            tr.log("acceptance_end", ok=result.get("ok", False), verdict=result["verdict"])
            tr.close()
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)

    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

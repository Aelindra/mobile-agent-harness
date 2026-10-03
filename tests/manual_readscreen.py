# -*- coding: utf-8 -*-
"""ui.snapshot / 句柄操作 / ui2.state v2 真机测试 v2。

纪律（修正版）：PASSIVE 模式下只做只读测试（snapshot + ui2.state），
app.launch / ui.home / click_handle 一律硬性 gated 在 ACTIVE 之后。
"""
import json
import os
import sys
import time

REPO = r"E:\ai\mobile\mobile-agent-harness"
os.chdir(REPO)
sys.path.insert(0, REPO)

from core.providers import assemble      # noqa: E402
from core.registry import Registry       # noqa: E402

ctx, pipe, pr = assemble({"ctx_name": "snaptest"})
reg = Registry(ctx, pipe, ctx.engine, plugin_runtime=pr)
b = ctx.bridge
report = []


def call(name, args=None, quiet=False):
    r = reg.call(name, args or {})
    if not quiet:
        line = f"  {name:<16} ok={str(r.get('ok')):<5} {r.get('duration_ms')}ms"
        if not r.get("ok"):
            line += f"  err={str(r.get('error'))[:100]}"
        print(line)
    return r


def jsize(r):
    return len(json.dumps(r, ensure_ascii=False))


def snapshot_settled(max_tries=3):
    """快照过空（转屏/加载中）自动重试。"""
    for i in range(max_tries):
        r = call("ui.snapshot", {"include_bounds": True})
        if r.get("ok") and len(r.get("elements", [])) >= 3:
            return r
        print(f"    (快照仅 {len(r.get('elements', []))} 元素，疑似转屏/加载中，1.5s 后重试 {i+1}/{max_tries})")
        time.sleep(1.5)
    return r


def focus_pkg():
    out = b.shell("dumpsys window | grep mCurrentFocus", timeout=10)
    import re
    m = re.search(r"mCurrentFocus=Window\{[^}]*?\s([^ /\}]+)(?:/([^ \}]+))?\}", out)
    return (m.group(1) if m else ""), out


# ---------- 0. 前台/亮屏占用检查 ----------
wake = b.shell("dumpsys power | grep mWakefulness", timeout=10)
pkg, focus_raw = focus_pkg()
awake = "Awake" in wake
LOCKISH = ("Keyguard", "NotificationShade", "StatusBar")
LAUNCHERS = ("com.miui.home", "com.android.launcher")
is_locked = any(k in focus_raw for k in LOCKISH) or not focus_raw.strip()
active_ok = (not awake) or is_locked or any(k in pkg for k in LAUNCHERS) or pkg == "com.android.settings"
print(f"[0] awake={awake} focus={pkg or '(null)'}")
print(f"[0] mode = {'ACTIVE（息屏/桌面/设置——可主动操作）' if active_ok else f'PASSIVE（用户在 {pkg} 中——只读测试）'}")

# ---------- 1. PASSIVE：只读 ----------
if not active_ok:
    print("\n[A] 只读测试（不碰任何 UI）")
    s1 = snapshot_settled()
    els = s1.get("elements", [])
    print(f"  -> snapshot({s1.get('app')}): {len(els)} 元素 {jsize(s1)}B / {s1['duration_ms']}ms")
    report.append((f"snapshot@{s1.get('app','?')[:14]}", len(els), jsize(s1), s1["duration_ms"]))
    for e in els[:8]:
        print("     ", json.dumps(e, ensure_ascii=False)[:120])
    dump = call("ui.dump", quiet=True)
    print(f"  -> ui.dump 同屏: {jsize(dump)}B / {dump['duration_ms']}ms"
          f"（快照 ≈ 1/{max(1, jsize(dump) // max(1, jsize(s1)))} 体积）")
    report.append(("ui.dump 同屏", "-", jsize(dump), dump["duration_ms"]))
    st = call("ui2.state", {"max_elements": 60})
    if st.get("ok"):
        print(f"  -> ui2.state 融合 {len(st['elements'])} 元素 {st['stats']}"
              f"（ocr {st['ocr_ms']}ms + a11y {st['a11y_ms']}ms，总 {st['total_ms']}ms）")
        for e in st["elements"][:12]:
            print("     ", json.dumps(e, ensure_ascii=False)[:120])
        report.append(("ui2.state 融合", len(st["elements"]), jsize(st), st["total_ms"]))
    else:
        print(f"  -> ui2.state 失败: {st.get('error')}")

# ---------- 1.5 PASSIVE 后等待空闲（每 45s 复查，最多 8 次） ----------
if not active_ok:
    print("\n[等待] 每 45s 复查前台，回到桌面/息屏后自动进入主动链路（最多 8 次）")
    for i in range(8):
        time.sleep(45)
        wake = b.shell("dumpsys power | grep mWakefulness", timeout=10)
        pkg, focus_raw = focus_pkg()
        awake = "Awake" in wake
        is_locked = any(k in focus_raw for k in LOCKISH) or not focus_raw.strip()
        active_ok = (not awake) or is_locked or any(k in pkg for k in LAUNCHERS) or pkg == "com.android.settings"
        print(f"  [复查 {i+1}] awake={awake} focus={pkg or '(null)'} -> {'ACTIVE' if active_ok else '仍占用'}")
        if active_ok:
            break

# ---------- 2. ACTIVE：完整链路 ----------
if active_ok:
    if not awake or is_locked:
        call("device.wake_unlock")
        time.sleep(1.0)
    print("\n[A] 桌面快照")
    call("ui.home")
    time.sleep(1.5)
    s0 = snapshot_settled()
    print(f"  -> 桌面: {len(s0.get('elements', []))} 元素 {jsize(s0)}B / {s0['duration_ms']}ms")
    for e in s0.get("elements", [])[:6]:
        print("     ", json.dumps(e, ensure_ascii=False)[:120])
    report.append(("snapshot 桌面", len(s0.get("elements", [])), jsize(s0), s0["duration_ms"]))

    print("\n[B] 设置页：快照 vs dump token 对比 + 句柄操作")
    call("app.launch", {"pkg": "com.android.settings"})
    time.sleep(2.0)   # 转屏/加载稳定
    s1 = snapshot_settled()
    els_s = s1.get("elements", [])
    dump = call("ui.dump", quiet=True)
    print(f"  -> snapshot: {len(els_s)} 元素 {jsize(s1)}B / {s1['duration_ms']}ms")
    print(f"  -> ui.dump : {jsize(dump)}B / {dump['duration_ms']}ms"
          f"（快照/dump ≈ 1/{max(1, jsize(dump) // max(1, jsize(s1)))}）")
    report.append(("snapshot 设置页", len(els_s), jsize(s1), s1["duration_ms"]))
    report.append(("ui.dump 设置页", "-", jsize(dump), dump["duration_ms"]))
    inter = [e for e in els_s if e.get("i")]
    print(f"  -> 可交互 {len(inter)}:")
    for e in inter[:10]:
        print("     ", json.dumps(e, ensure_ascii=False)[:130])

    search = next((e for e in els_s if "search" in str(e.get("id", "")).lower()
                   or "搜索" in str(e.get("t", "")) or "Search" in str(e.get("t", ""))
                   or "搜索" in str(e.get("d", "")) or "Search" in str(e.get("d", ""))), None)
    print(f"[C] 搜索句柄 = {json.dumps(search, ensure_ascii=False)[:120] if search else '未找到'}")
    if search:
        t0 = time.time()
        rc = call("ui.click_handle", {"handle": search["h"]})
        print(f"  -> click_handle 墙钟 {int((time.time()-t0)*1000)}ms: "
              f"{json.dumps(rc, ensure_ascii=False)[:120]}")
        time.sleep(1.2)
        s2 = snapshot_settled()
        inputs = [e for e in s2.get("elements", []) if e.get("e")]
        print(f"  -> 点击后可输入元素 {len(inputs)}: "
              f"{json.dumps(inputs[:2], ensure_ascii=False)[:140]}")
        if inputs:
            rt = call("ui.text_handle", {"handle": inputs[0]["h"], "text": "bluetooth"})
            print(f"  -> text_handle ok={rt.get('ok')} method={rt.get('method')}")
            time.sleep(1.5)
            s3 = snapshot_settled()
            hits = [e.get("t") for e in s3.get("elements", [])
                    if "luetooth" in str(e.get("t", "")) or "牙" in str(e.get("t", ""))]
            print(f"  -> 搜索结果命中: {hits[:6]}")

    print("\n[D] ui2.state v2 融合（设置搜索结果页）")
    st = call("ui2.state", {"max_elements": 60})
    if st.get("ok"):
        print(f"  -> 融合 {len(st['elements'])} 元素 {st['stats']}"
              f"（ocr {st['ocr_ms']}ms + a11y {st['a11y_ms']}ms，总 {st['total_ms']}ms）")
        for e in st["elements"][:10]:
            print("     ", json.dumps(e, ensure_ascii=False)[:120])
        report.append(("ui2.state 融合", len(st["elements"]), jsize(st), st["total_ms"]))

    call("ui.home")

print("\n==== 汇总 ====")
print(f"{'场景':<18}{'元素':>6}{'体积B':>9}{'耗时ms':>9}")
for name, n, sz, ms in report:
    print(f"{name:<18}{str(n):>6}{str(sz):>9}{str(ms):>9}")

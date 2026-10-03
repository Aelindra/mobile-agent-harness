# -*- coding: utf-8 -*-
"""Harness Distillation（把一个 app 的操作知识自动蒸馏成可复用 harness）。

流程：launch → 有界 BFS 探索（步数/深度上限防失控）→ 按 activity 聚类 screen →
启发式归纳候选工具签名（LLM 可插拔点：propose_tools）→ 生成 harness.json5 →
dry-run 校验 → 注册生效（trust=dry_run，首次真实执行需 --trust，安全阀 6.4）。
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from typing import Optional

from . import selectors as S
from .bridge import Bridge
from .harness import HarnessEngine
from .lint import lint_harness

# 蒸馏安全阀
MAX_ACTIONS = int(os.environ.get("DISTILL_MAX_ACTIONS", "24"))
MAX_SCREENS = 12
MAX_INTERACTIVE_PER_SCREEN = 8
DANGEROUS_TEXT = re.compile(r"退出|删除|卸载|注销|清空|支付|付款|转账|充值", re.I)
_SKIP_RE = re.compile(r"上传|切换至语音|自动朗读|拍照|摄像头", re.I)  # 仅探索跳过，不影响记录
_INPUT_RE = re.compile(r"发消息|输入|搜索|说点什么|message|input|search|按住说话", re.I)


def _is_input_node(n) -> bool:
    """Compose 应用的输入框不是 EditText 类，按可读文本/描述识别。"""
    if n.editable:
        return True
    return bool(_INPUT_RE.search(n.text or "") or _INPUT_RE.search(n.desc or ""))


def _selector_of(n, pkg, root=None) -> Optional[str]:
    """为交互节点挑选最稳的选择器：id 优先，desc/text 兜底；
    Compose 类无 id/desc/text 的可点击 View 用 class+clickable+位置兜底。"""
    if n.res_id:
        short = n.res_id.split("/")[-1]
        return f"id@{short} && pkg@{pkg}"
    if n.desc:
        return f"desc@^{_rx(n.desc)}$ && pkg@{pkg}"
    if n.text and len(n.text) <= 12:
        return f"text@^{_rx(n.text)}$ && pkg@{pkg}"
    if root is not None and n.clickable and n.cls:
        sibs = [m for m in root.iter() if m.cls == n.cls and m.clickable]
        idx = sibs.index(n)
        return f"class@{n.cls} && [clickable=true] && nth@{idx} && pkg@{pkg}"
    return None


def _rx(s: str) -> str:
    return s.replace("\\", "\\\\").replace("^", "\\^").replace("$", "\\$") \
            .replace(".", "\\.").replace("*", "\\*").replace("+", "\\+") \
            .replace("?", "\\?").replace("(", "\\(").replace(")", "\\)") \
            .replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


class _Screen:
    def __init__(self, activity, sig):
        self.activity = activity
        self.sig = sig
        self.name = None
        self.interactive = []  # [(selector, label, kind)]
        self.reachable_from = None  # (from_screen, selector) 主界面上的一跳入口


def distill(bridge: Bridge, pkg: str, engine: HarnessEngine, transcript=None,
            max_actions: int = MAX_ACTIONS, shot_dir: Optional[str] = None,
            repair: bool = False) -> dict:
    t0 = time.time()
    bridge.launch(pkg)
    screens: dict = {}
    queue = deque()

    def snapshot(prev=None, entry_sel=None):
        cur_pkg, act = bridge.current_app()
        if cur_pkg != pkg:
            return None
        root = bridge.dump()
        interactive = []
        for n in root.iter():
            if n.package != pkg:
                continue
            # desc 是语义动作标签（如 发送/开启新对话），即使节点本身不可点击也纳入
            desc_action = bool(n.desc)
            kind = ("editable" if (n.editable or _is_input_node(n)) else
                    "clickable" if (n.clickable or (n.children and any(c.clickable for c in n.children))
                                    or desc_action) else None)
            if not kind:
                continue
            sel = _selector_of(n, pkg, root)
            if not sel:
                continue
            label = n.text or n.desc or (n.res_id.split("/")[-1] if n.res_id else "")
            interactive.append((sel, label, kind, n.editable or _is_input_node(n)))
        sig = (act, tuple(sorted((s, k) for s, l, k, e in interactive)))
        if sig not in screens:
            sc = _Screen(act, sig)
            sc.interactive = interactive
            sc.reachable_from = (prev, entry_sel) if prev else None
            screens[sig] = sc
            queue.append(sc)
        else:
            # 同一 screen 探针后新增节点（如发送按钮显形）则合并
            old = screens[sig]
            have = {s for s, _, _, _ in old.interactive}
            for item in interactive:
                if item[0] not in have:
                    old.interactive.append(item)
        return screens[sig]

    def probe_input(sc) -> Optional[tuple]:
        """输入探针：让『输入后才出现』的发送按钮显形并并入 screen.interactive。

        注意：点击输入框聚焦后提示语文本节点会消失，所以聚焦用探针文本自身的
        text@ 选择器（set_text 内部先 resolve 再点击，再走原生输入通道）。
        """
        inp = next(((s, l) for s, l, k, e in sc.interactive if e and _INPUT_RE.search(l or "")), None)
        if inp is None:
            inp = next(((s, l) for s, l, k, e in sc.interactive if e), None)
        if inp is None:
            return None
        probe = "你好"
        try:
            bridge.set_text(probe, inp[0], pkg)
            time.sleep(1.4)
            snapshot(prev=sc.reachable_from[0] if sc.reachable_from else None,
                     entry_sel=sc.reachable_from[1] if sc.reachable_from else None)
            # 清理：聚焦（以探针文本节点为锚）→ 退格 → 校验
            probe_sel = f"text@^{_rx(probe)}$ && pkg@{pkg}"
            for _ in range(5):
                root2 = bridge.dump()
                if not any(probe in (n.text or "") for n in root2.iter()):
                    return (inp[0], probe)
                try:
                    bridge.click(probe_sel, pkg)
                except Exception:
                    pass
                time.sleep(0.4)
                bridge.shell("input keyevent 123")
                for _ in range(10):
                    bridge.shell("input keyevent 67")
                    time.sleep(0.08)
                bridge.shell("input keyevent --longpress 67")
                time.sleep(0.6)
            return (inp[0], probe)
        except Exception:
            return None

    main = snapshot()
    if main is None:
        return {"ok": False, "error": f"启动 {pkg} 失败"}

    probe_input_sel = None
    actions = 0
    while queue and actions < max_actions and len(screens) < MAX_SCREENS:
        sc = queue.popleft()
        # 从 main 出发的 UI 层级回退：探索新 screen 的入口前先回主界面
        bridge.launch(pkg)
        time.sleep(1.0)
        if sc.reachable_from:
            prev, entry = sc.reachable_from
            try:
                bridge.click(entry, pkg)
                time.sleep(1.2)
            except Exception:
                continue
            snapshot(prev=prev, entry_sel=entry)
        # 输入探针：让『输入后才显形』的发送按钮等被蒸馏器看到
        if any(e for _, _, _, e in sc.interactive) and actions < max_actions:
            pr = probe_input(sc)
            if pr:
                probe_input_sel = probe_input_sel or pr[0]
                actions += 1
        tried = set()
        for sel, label, kind, editable in sc.interactive[:MAX_INTERACTIVE_PER_SCREEN]:
            if actions >= max_actions:
                break
            if DANGEROUS_TEXT.search(label or "") or _SKIP_RE.search(label or ""):
                continue
            if editable or (sel, label) in tried:
                continue
            tried.add((sel, label))
            actions += 1
            try:
                bridge.click(sel, pkg)
                time.sleep(1.4)
                snapshot(prev=sc, entry_sel=sel)
                bridge.back()
                time.sleep(0.9)
            except Exception:
                try:
                    bridge.back()
                    time.sleep(0.6)
                except Exception:
                    pass
        # 队列里的 screen 若其入口记录 prev 不是 main，也按一跳近似处理（有界，不追求全覆盖）

    # ---------- 工具归纳（LLM 可插拔点）----------
    tools = propose_tools(pkg, screens, main, probe_input_sel=probe_input_sel)

    out_dir = os.path.join(engine.harness_dir, pkg)
    os.makedirs(out_dir, exist_ok=True)
    harness = {
        "package": pkg,
        "name": f"{pkg}（蒸馏生成）",
        "versionRange": "*",
        "trust": "dry_run",
        "notes": f"由 agent distill 自动生成于 {time.strftime('%Y-%m-%d %H:%M')}，"
                 f"探索 {actions} 步、{len(screens)} 个 screen；默认 dry-run，"
                 f"首次真实执行需 --trust。",
        "tools": tools,
    }
    path = os.path.join(out_dir, "harness.json5")
    with open(path, "w", encoding="utf-8") as f:
        f.write("// 由 agent distill 自动生成（trust=dry_run；首次真实执行需 --trust）\n")
        f.write(json.dumps(harness, ensure_ascii=False, indent=2))

    # notes.md：界面地图
    with open(os.path.join(out_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(f"# {pkg} 界面地图（蒸馏）\n\n")
        for sc in screens.values():
            f.write(f"## screen: {sc.activity}\n")
            for sel, label, kind, editable in sc.interactive:
                f.write(f"- [{kind}] `{label}` ← `{sel}`\n")
            f.write("\n")

    # dry-run 校验每个工具
    bridge.home()
    bridge.launch(pkg)
    dry = []
    ok_all = True
    for t in tools:
        if t["steps"]:
            r = engine.run(pkg, t["name"], _fake_params(t), dry_run=True)
            dry.append({"tool": t["name"], "ok": r["ok"]})
            ok_all &= r["ok"]
        else:
            dry.append({"tool": t["name"], "ok": True, "note": "无 target 步骤"})
    lint = lint_harness(engine.load(pkg))
    report = {"ok": ok_all, "pkg": pkg, "actions_used": actions,
              "screens": [s.activity for s in screens.values()],
              "tools": [t["name"] for t in tools], "dry_run": dry, "lint": lint,
              "harness_path": path, "elapsed_s": round(time.time() - t0, 1)}
    if transcript:
        transcript.log("distill", **{k: report[k] for k in ("pkg", "actions_used", "tools", "ok")})
    return report


def _fake_params(t):
    return {k: "sample" for k in (t.get("params") or {})}


def propose_tools(pkg: str, screens: dict, main: "_Screen", probe_input_sel: Optional[str] = None):
    """启发式工具归纳。若接入 LLM，把 screens 摘要交给模型产出同构 JSON 即可。

    规则：
    1. main 的底部/入口可点击文本项 → open_<label> 工具（一跳导航）
    2. 任何 screen 出现 EditText + 发送类按钮 → send_message(text) 工具
    3. 其他 screen 的入口 → open_<screen 入口 label>
    """
    tools = []

    def add(name, description, params, steps, postconditions=None):
        tools.append({"name": name, "description": description, "params": params,
                      "steps": steps, "postconditions": postconditions or []})

    send_screen = None
    send_btn = None
    for sc in screens.values():
        editable = [(s, l) for s, l, k, e in sc.interactive if e]
        btn = [(s, l) for s, l, k, e in sc.interactive
               if not e and re.search(r"send|发送|send_btn", s + " " + (l or ""), re.I)]
        if editable and btn:
            send_screen, send_btn = sc, btn[0]
            break

    if send_screen is not None:
        # 输入框选择器优先取『输入类标签』（发消息/输入/搜索），避免探针残留文本被当锚点
        input_item = next(((s, l) for s, l, k, e in send_screen.interactive
                           if e and _INPUT_RE.search(l or "")), None) or \
                     next(((s, l) for s, l, k, e in send_screen.interactive if e), None)
        input_sel = input_item[0]
        add("send_message", "在输入框输入文本并点击发送（蒸馏自界面结构）",
            {"text": "string"},
            [{"action": "set_text", "target": input_sel, "value": "$text",
              "then_wait": {"selector": send_btn[0], "timeout": 6}},
             {"action": "click", "target": send_btn[0], "wait": 1.0}],
            postconditions=[f"not_exists({send_btn[0]})"])

    # 一跳入口工具
    named = 0
    for sc in screens.values():
        prev, entry = sc.reachable_from or (None, None)
        if not prev or not entry:
            continue
        label = None
        for s, l, k, e in prev.interactive:
            if s == entry:
                label = l
                break
        if not label or DANGEROUS_TEXT.search(label):
            continue
        named += 1
        safe = "open_" + re.sub(r"[^\w]+", "_", label)[:16].strip("_").lower()
        if any(t["name"] == safe for t in tools):
            continue
        add(safe, f"从主界面打开『{label}』", {},
            [{"action": "click", "target": entry, "wait": {"timeout": 6}}])
    return tools

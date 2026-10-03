# -*- coding: utf-8 -*-
"""L3 Harness 引擎：harness.json5 的加载/热更/信任门/步骤执行/postcondition/stale 标记。

harness = manifest(package/versionRange/trust) + tools[]（params schema、
语义选择器步骤序列、前置/后置条件）。**步骤里禁止裸坐标**，所有目标都是选择器。

自愈：SelectorNotFound / postcondition 失败 → state.json 标记 stale →
`agent distill --pkg X --repair` 触发局部重蒸馏。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from .bridge import Bridge
from .selectors import SelectorNotFound

try:
    import json5
except ImportError:  # pragma: no cover
    json5 = None


class HarnessError(RuntimeError):
    pass


class TrustError(HarnessError):
    pass


class Harness:
    def __init__(self, data: dict, path: str):
        self.path = path
        self.dir = os.path.dirname(path)
        self.package = data["package"]
        self.name = data.get("name", self.package)
        self.version_range = data.get("versionRange", "*")
        self.trust = data.get("trust", "dry_run")
        self.notes = data.get("notes", "")
        self.tools = {t["name"]: t for t in data.get("tools", [])}

    def summary(self):
        return {"package": self.package, "name": self.name, "trust": self.trust,
                "versionRange": self.version_range,
                "tools": [{"name": t["name"], "description": t.get("description", ""),
                           "params": t.get("params", {}), "steps": len(t.get("steps", []))}
                          for t in self.tools.values()]}


class HarnessEngine:
    def __init__(self, bridge: Bridge, harness_dir: Optional[str] = None,
                 transcript=None, shot_dir: Optional[str] = None):
        self.bridge = bridge
        self.harness_dir = harness_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harnesses")
        self.transcript = transcript
        self.shot_dir = shot_dir
        self._cache = {}  # pkg -> (mtime, Harness)

    # ---------- 加载 ----------
    def load(self, pkg: str, hot: bool = True) -> Harness:
        path = os.path.join(self.harness_dir, pkg, "harness.json5")
        if not os.path.exists(path):
            raise HarnessError(f"未找到 harness: {path}")
        mtime = os.path.getmtime(path)
        cached = self._cache.get(pkg)
        if cached and hot and cached[0] == mtime:
            return cached[1]
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if json5 is not None:
            data = json5.loads(text)
        else:  # 极简降级：去注释+尾逗号
            cleaned = re.sub(r"^\s*//.*$", "", text, flags=re.M)
            cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
            data = json.loads(cleaned)
        h = Harness(data, path)
        if h.package != pkg:
            raise HarnessError(f"harness.package({h.package}) 与目录名({pkg})不一致")
        self._cache[pkg] = (mtime, h)
        return h

    def list(self):
        out = []
        if os.path.isdir(self.harness_dir):
            for pkg in sorted(os.listdir(self.harness_dir)):
                try:
                    out.append(self.load(pkg).summary())
                except Exception as ex:  # noqa: BLE001
                    out.append({"package": pkg, "error": str(ex)})
        return out

    # ---------- stale 状态 ----------
    def _state_path(self, pkg):
        return os.path.join(self.harness_dir, pkg, "state.json")

    def mark_stale(self, pkg: str, tool_name: str, reason: str):
        st = {}
        p = self._state_path(pkg)
        if os.path.exists(p):
            try:
                st = json.load(open(p, encoding="utf-8"))
            except Exception:
                st = {}
        st.setdefault("stale", {})[tool_name] = {"ts": time.time(), "reason": reason}
        json.dump(st, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def stale_tools(self, pkg: str):
        p = self._state_path(pkg)
        if not os.path.exists(p):
            return {}
        try:
            return json.load(open(p, encoding="utf-8")).get("stale", {})
        except Exception:
            return {}

    # ---------- 执行 ----------
    def _sub(self, obj, params):
        """$param 替换（递归字符串）。"""
        if isinstance(obj, str):
            for k, v in params.items():
                obj = obj.replace(f"${k}", str(v))
            return obj
        if isinstance(obj, list):
            return [self._sub(x, params) for x in obj]
        if isinstance(obj, dict):
            return {k: self._sub(v, params) for k, v in obj.items()}
        return obj

    def _check_condition(self, cond: str, pkg: str) -> bool:
        cond = cond.strip()
        m = re.match(r"exists\((.+)\)$", cond, re.S)
        if m:
            try:
                return len(self.bridge.find(m.group(1).strip().strip("'\""), pkg, timeout=0)) > 0
            except SelectorNotFound:
                return False
        m = re.match(r"not_exists\((.+)\)$", cond, re.S)
        if m:
            try:
                return len(self.bridge.find(m.group(1).strip().strip("'\""), pkg, timeout=0)) == 0
            except SelectorNotFound:
                return True
        m = re.match(r"current_app\((\S+)\)$", cond)
        if m:
            return self.bridge.current_app()[0] == m.group(1)
        m = re.match(r"text\((.+?)\)\s+contains\s+(.+)$", cond, re.S)
        if m:
            sel, needle = m.group(1).strip().strip("'\""), m.group(2).strip().strip("'\"")
            try:
                nodes = self.bridge.find(sel, pkg, timeout=0)
            except SelectorNotFound:
                return False
            return any(needle in n.text for n in nodes)
        raise HarnessError(f"无法解析的前置/后置条件: {cond}")

    def _shot(self, tag: str) -> Optional[str]:
        if not self.shot_dir:
            return None
        os.makedirs(self.shot_dir, exist_ok=True)
        path = os.path.join(self.shot_dir, f"{time.strftime('%H%M%S')}_{tag}.png")
        try:
            self.bridge.screenshot(path)
            return path
        except Exception:
            return None

    def _exec_step(self, step: dict, pkg: str, dry_run: bool, idx: int) -> dict:
        action = step.get("action")
        target = step.get("target")
        rec = {"step": idx, "action": action, "target": target,
               "desc": step.get("desc", "")}
        if dry_run:
            if action == "assert":
                rec.update(ok=True, dry_run=True, note="assert 条件跳过（dry-run）")
                return rec
            if target:
                try:
                    nodes = self.bridge.find(target, pkg)
                    rec.update(ok=True, dry_run=True, hits=len(nodes),
                               first=_node_brief(nodes[0]))
                except SelectorNotFound as ex:
                    alt = step.get("target_alt")
                    if alt:
                        try:
                            nodes = self.bridge.find(alt, pkg)
                            rec.update(ok=True, dry_run=True, hits=len(nodes),
                                       first=_node_brief(nodes[0]), used_alt=True)
                            return rec
                        except SelectorNotFound:
                            pass
                    rec.update(ok=False, error=str(ex))
            else:
                rec.update(ok=True, dry_run=True, note="无 target 的动作，dry-run 跳过")
            return rec

        # 等待语义（动作前）
        wait = step.get("wait")
        if wait is not None:
            if isinstance(wait, (int, float)):
                time.sleep(wait)
            elif wait == "idle":
                self._wait_screen_stable(8)
            elif isinstance(wait, dict) and wait.get("selector"):
                self.bridge.find(wait["selector"], pkg, timeout=wait.get("timeout", 8))

        def _run(target_sel):
            if action in ("click", "long_click"):
                return self.bridge.click(target_sel, pkg, long_click=(action == "long_click"))
            if action == "set_text":
                return self.bridge.set_text(step.get("value", ""), target_sel, pkg)
            if action == "clear_input":
                return self.bridge.clear_input(target_sel, pkg,
                                               empty_when_gone=step.get("empty_when_gone"))
            if action == "scroll":
                return self.bridge.scroll(step.get("direction", "down"),
                                          step.get("container"), pkg)
            raise ValueError("no target action")

        if action in ("click", "long_click", "set_text", "clear_input", "scroll"):
            try:
                rec["result"] = _run(target)
            except SelectorNotFound as ex:
                alt = step.get("target_alt")
                if alt:
                    rec["result"] = _run(alt)
                    rec["used_alt"] = True
                else:
                    raise
        elif action == "back_until":
            # 条件导航：最多 max 次返回直到 target 选择器出现（先查后按）
            max_n = int(step.get("max", 3))
            done = False
            presses = 0
            for _ in range(max_n):
                try:
                    self.bridge.find(target, pkg, timeout=0)
                    done = True
                    break
                except SelectorNotFound:
                    self.bridge.back()
                    presses += 1
                    time.sleep(1.0)
            if not done:
                try:
                    self.bridge.find(target, pkg, timeout=0)
                    done = True
                except SelectorNotFound:
                    pass
            rec.update(ok=done, presses=presses)
            if not done:
                rec["error"] = f"back_until 未到达: {target}"
            return rec
        elif action == "back":
            self.bridge.back()
            rec["result"] = {"ok": True}
        elif action == "home":
            self.bridge.home()
            rec["result"] = {"ok": True}
        elif action == "launch":
            p = step.get("pkg") or pkg
            self.bridge.launch(p)
            rec["result"] = {"ok": True, "current": self.bridge.current_app()}
        elif action == "assert":
            ok = self._check_condition(target, pkg)
            rec.update(ok=ok)
            if not ok:
                rec["error"] = f"assert 失败: {target}"
            return rec
        elif action == "screenshot":
            rec["result"] = {"ok": True, "path": self._shot(step.get("tag", "step"))}
        else:
            raise HarnessError(f"harness 步骤不支持的动作: {action}（shell 等非白名单动作被禁止）")

        # 等待语义（动作后）
        then_wait = step.get("then_wait")
        if isinstance(then_wait, dict) and then_wait.get("selector"):
            self.bridge.find(then_wait["selector"], pkg, timeout=then_wait.get("timeout", 6))
            rec["then_wait"] = then_wait["selector"]
        elif isinstance(then_wait, (int, float)):
            time.sleep(then_wait)
            rec["then_wait"] = then_wait
        rec["ok"] = True
        return rec

    def _wait_screen_stable(self, timeout: float = 8.0):
        """等界面稳定：连续两次 dump 的结构签名一致。"""
        def sig():
            root = self.bridge.dump()
            return tuple(sorted((n.res_id, n.text, n.desc) for n in root.iter()
                                if n.package != "com.android.systemui"))
        deadline = time.time() + timeout
        last, stable = None, 0
        while time.time() < deadline:
            s = sig()
            stable = stable + 1 if s == last else 1
            last = s
            if stable >= 2:
                return True
            time.sleep(0.7)
        return False

    def run(self, pkg: str, tool_name: str, params: Optional[dict] = None,
            dry_run: bool = False, allow_untrusted: bool = False) -> dict:
        h = self.load(pkg)
        t = h.tools.get(tool_name)
        if not t:
            raise HarnessError(f"harness {pkg} 无工具 {tool_name}，可用: {sorted(h.tools)}")
        params = params or {}
        if self.transcript:
            self.transcript.log("harness_start", pkg=pkg, tool=tool_name, params=params,
                                dry_run=dry_run, trust=h.trust)
        steps_out, t0 = [], time.time()

        if not dry_run:
            if h.trust not in ("confirmed", "trusted") and not allow_untrusted:
                raise TrustError(f"harness {pkg} trust={h.trust}：首次真实执行需要 --trust 或先 dry-run 校验")
            for cond in t.get("preconditions", []):
                cond = self._sub(cond, params)
                if not self._check_condition(cond, pkg):
                    raise HarnessError(f"前置条件不满足: {cond}")

        fail = None
        for i, raw_step in enumerate(t.get("steps", [])):
            step = self._sub(raw_step, params)
            try:
                rec = self._exec_step(step, pkg, dry_run, i)
            except SelectorNotFound as ex:
                rec = {"step": i, "action": step.get("action"), "target": step.get("target"),
                       "ok": False, "error": str(ex)}
                fail = (i, step, ex)
            except HarnessError as ex:
                rec = {"step": i, "action": step.get("action"), "ok": False, "error": str(ex)}
                fail = (i, step, ex)
            if self.shot_dir and not dry_run:
                rec["screenshot"] = self._shot(f"{tool_name}_s{i}")
            if self.transcript:
                self.transcript.log("harness_step", pkg=pkg, tool=tool_name, **_brief(rec))
            steps_out.append(rec)
            if fail:
                break

        result = {"tool": f"{pkg}.{tool_name}", "dry_run": dry_run, "ok": fail is None,
                  "steps": steps_out, "elapsed_ms": int((time.time() - t0) * 1000)}
        if fail is None and not dry_run:
            for cond in t.get("postconditions", []):
                cond = self._sub(cond, params)
                ok = self._check_condition(cond, pkg)
                result.setdefault("postconditions", []).append({"cond": cond, "ok": ok})
                if not ok:
                    result["ok"] = False
                    result["error"] = f"后置条件失败: {cond}"
                    fail = (-1, None, RuntimeError(result["error"]))
                    break
        if fail is not None and not dry_run:
            self.mark_stale(pkg, tool_name, str(fail[2]))
            result["stale_marked"] = True
        if self.transcript:
            self.transcript.log("harness_end", pkg=pkg, tool=tool_name, ok=result["ok"],
                                elapsed_ms=result["elapsed_ms"])
        return result


def _node_brief(n):
    return {"id": n.res_id, "text": n.text, "desc": n.desc, "class": n.cls,
            "center": list(n.center)}


def _brief(rec):
    keep = {k: rec[k] for k in ("step", "action", "target", "ok", "error") if k in rec}
    if "result" in rec:
        keep["result_ok"] = rec["result"].get("ok")
        keep["method"] = rec["result"].get("method")
    return keep

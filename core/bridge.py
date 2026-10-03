# -*- coding: utf-8 -*-
"""L1 Bridge：uiautomator2 v3 封装（app_process + wetest uia2 jar 路线，零 APK 安装）。

提供：实时 dump → Node 树、语义查找、节点级动作（选择器解析，服务端重定位后
点击节点中心；非裸坐标）、截图、root shell、应用生命周期管理。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

from . import selectors as S
from .node import Node, parse_dump

# 设备 serial 解析优先级：显式参数 > ANDROID_SERIAL > AGENT_SERIAL > DEFAULT_SERIAL
# （示例值仅示意；USB 兜底经 AGENT_SERIAL_USB 配置，不配则不启用）
DEFAULT_SERIAL = os.environ.get("AGENT_SERIAL_DEFAULT", "emulator-5555")
USB_SERIAL_FALLBACK = os.environ.get("AGENT_SERIAL_USB") or None
# adb 断连类错误的特征（u2/adbutils 异常类型跨版本不稳，按消息特征判定）
_CONN_ERR_HINTS = ("eof", "broken pipe", "closed", "offline", "device not found",
                   "no devices", "refused", "reset by peer", "unreachable",
                   "connection", "timeout", "disconnected", "cannot connect")


class BridgeError(RuntimeError):
    pass


class Bridge:
    """u2 桥。lazy=True 时构造不连设备（MCP server 用），首次调用自动连接；
    任何原语遇断连类异常自动重连（WiFi serial 先 adb connect）并重试一次。"""

    def __init__(self, serial: Optional[str] = None, lazy: bool = False):
        self.serial_pref = (serial or os.environ.get("ANDROID_SERIAL")
                            or os.environ.get("AGENT_SERIAL") or DEFAULT_SERIAL)
        self.serial = self.serial_pref     # lazy 下连接前的名义值，_connect 成功后更新
        self.d = None
        self._ok_ts = 0.0
        if not lazy:
            self._connect()

    # ---------- 连接管理（P1.1 自恢复） ----------
    def _connect(self):
        import uiautomator2 as u2
        cands: List[str] = []
        for s in (self.serial_pref, USB_SERIAL_FALLBACK):
            if s and s not in cands:
                cands.append(s)
        last = None
        for s in cands:
            try:
                if ":" in s:  # WiFi serial：adb 先 connect（IP 变更/掉线场景）
                    subprocess.run(["adb", "connect", s], capture_output=True, timeout=10)
                d = u2.connect(s)
                try:
                    d.settings["wait_timeout"] = 3.0
                except Exception:
                    pass
                self.d = d
                self.serial = s
                self._ok_ts = time.time()
                return d
            except Exception as e:  # noqa: BLE001
                last = e
        raise BridgeError(f"设备连接失败（试过 {cands}）: {type(last).__name__}: {last}")

    def ensure(self, force: bool = False):
        """保证桥可用：TTL 内免探活；失效则（逐候选 serial）重连。"""
        if self.d is None or force:
            self._connect()
            return
        if time.time() - self._ok_ts < 5.0:
            return
        try:
            r = self.d.shell("echo :ok", timeout=6)
            if ":ok" in (r.output if hasattr(r, "output") else str(r)):
                self._ok_ts = time.time()
                return
        except Exception:
            pass
        self._connect()

    @staticmethod
    def _is_conn_err(ex: Exception) -> bool:
        msg = str(ex).lower()
        return any(h in msg for h in _CONN_ERR_HINTS)

    def _guard(self, fn):
        """原语执行包装：断连类异常 → 强制重连后重试一次。"""
        self.ensure()
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if self._is_conn_err(e):
                self.ensure(force=True)
                return fn()
            raise

    # ---------- 基础 ----------
    def dump(self) -> Node:
        """实时 dump（u2 桥数据，无 `uiautomator dump` 的旧缓存问题）。"""
        def _once():
            try:
                return self.d.dump_hierarchy()
            except Exception:
                self._reset()
                return self.d.dump_hierarchy()
        return parse_dump(self._guard(_once))

    def _reset(self):
        try:
            self.d.reset_uiautomator()
        except Exception as e:  # pragma: no cover
            raise BridgeError(f"uiautomator server 重启失败: {e}")

    def screenshot(self, path: str):
        img = self._guard(lambda: self.d.screenshot())
        img.save(path)
        return path

    def shell(self, cmd, root: bool = False, timeout: float = 30) -> str:
        def _once():
            if root:
                r = self.d.shell(["su", "-c", cmd], timeout=timeout)
            else:
                r = self.d.shell(cmd, timeout=timeout)
            return r.output if hasattr(r, "output") else str(r)
        return self._guard(_once)

    # ---------- 应用 ----------
    _FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^}]*?\s([^ /\}]+)(?:/([^ \}]+))?\}")
    _OVERLAY_NAMES = {"NotificationShade", "StatusBar", "Recents", "ScreenDecorOverlay",
                      "PopupWindow", "InputMethod", "MagazinePreload"}

    def current_app(self) -> Tuple[str, str]:
        """前台应用判定走 shell 侧 ground truth（u2 server 的 app_current 在
        Android 16 上慢且返回过期数据，实测弃用）。
        无包名/覆盖层类焦点窗口（PopupWindow 等）视为无效，落到 activity 兜底。"""
        try:
            out = self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedWindow'",
                             timeout=10)
        except Exception:
            out = ""
        for m in self._FOCUS_RE.finditer(out or ""):
            pkg, act = m.group(1), m.group(2) or ""
            if act and pkg not in self._OVERLAY_NAMES and "." in pkg:
                return pkg, act
        out = self.shell("dumpsys activity activities | grep topResumedActivity", timeout=15)
        m = re.search(r"topResumedActivity=ActivityRecord\{[^}]*\s\S+\s(\S+)/(\S+)", out or "")
        if m:
            return m.group(1), m.group(2)
        return "", ""

    def launch(self, pkg: str, wait: bool = True, timeout: float = 15):
        self._guard(lambda: self.d.app_start(pkg))
        if wait:
            self.wait_idle(pkg, timeout=timeout)

    def stop(self, pkg: str):
        self._guard(lambda: self.d.app_stop(pkg))

    def wait_idle(self, pkg: str, timeout: float = 15):
        """等待 pkg 成为前台且连续两次采样稳定。"""
        deadline = time.time() + timeout
        last = None
        stable = 0
        while time.time() < deadline:
            cur, _act = self.current_app()
            if cur == pkg:
                stable = stable + 1 if last == cur else 1
                last = cur
                if stable >= 2:
                    time.sleep(0.4)
                    return True
            else:
                stable = 0
                last = cur
            time.sleep(0.5)
        raise BridgeError(f"等待 {pkg} 前台空闲超时（当前 {last}）")

    def assert_foreground(self, pkg: str):
        cur, act = self.current_app()
        if cur != pkg:
            raise BridgeError(f"前台应用为 {cur}（{act}），期望 {pkg}——动作已拒绝以避免误点其他 app")

    def back(self):
        self._guard(lambda: self.d.press("back"))

    def home(self):
        self._guard(lambda: self.d.press("home"))

    # ---------- 查找 ----------
    def find(self, selector: str, pkg: Optional[str] = None, timeout: float = 0) -> List[Node]:
        """语义查找；timeout>0 时轮询直到命中或超时。"""
        deadline = time.time() + timeout
        while True:
            root = self.dump()
            nodes = S.find_nodes(root, selector, default_pkg=pkg)
            if nodes:
                return nodes
            if time.time() >= deadline:
                raise S.SelectorNotFound(selector, pkg)
            time.sleep(0.6)

    def _resolve(self, selector: str, pkg: Optional[str]) -> Tuple[Node, Optional[dict]]:
        """dump→匹配→(node, u2选择器参数或None)。

        只有当选择器不含相对定位片段时才给出 u2 参数（服务端重定位）；
        含相对定位时动作退化为节点中心点击（坐标来自本次 dump 的该节点）。
        pkg 为 None 时从当前前台应用推断（id@短名 补全依赖 default_pkg）。
        """
        # id@短名 补全依赖 default_pkg；pkg@ 过滤片段不参与补全，故总是推断
        if pkg is None:
            try:
                pkg = self.current_app()[0]
            except Exception:
                pass
        # 界面动态刷新（联想列表/动画）会造成节点瞬时消失，短重试
        last_err: Optional[Exception] = None
        for _ in range(3):
            try:
                nodes = S.find_nodes(self.dump(), selector, default_pkg=pkg)
                if nodes:
                    node = nodes[0]
                    break
            except S.SelectorNotFound as e:
                last_err = e
            time.sleep(0.5)
        else:
            raise last_err or S.SelectorNotFound(selector, pkg)
        node = nodes[0]
        if S.has_relative(selector):
            return node, None
        dparts = S.direct_parts(selector)
        if not dparts:
            return node, None
        inst = nodes.index(node)
        kw = S.to_u2_kwargs(selector, default_pkg=pkg)
        if not kw:
            return node, None
        return node, {**kw, "instance": inst}

    # ---------- 节点动作 ----------
    def click(self, selector: str, pkg: Optional[str] = None, nth: int = 0,
              long_click: bool = False, timeout: float = 3) -> dict:
        node, kw = self._resolve(selector, pkg)
        if nth:
            nodes = S.find_nodes(self.dump(), selector, default_pkg=pkg)
            if len(nodes) <= nth:
                raise S.SelectorNotFound(f"{selector} [nth={nth}]", pkg)
            node, kw = nodes[nth], (None if S.has_relative(selector)
                                    else {**S.to_u2_kwargs(selector, default_pkg=pkg),
                                          "instance": nth})
        method = "uia_selector"
        done = False
        if kw:
            try:
                el = self.d(**kw)
                if el.wait(timeout=timeout):
                    if long_click:
                        el.long_click()
                    else:
                        el.click()
                    done = True
            except Exception:
                done = False
        if not done:
            # 兜底：仍为节点派生坐标（本次 dump 树中该节点的中心），绝非硬编码
            x, y = node.center
            if long_click:
                self.d.long_click(x, y, 1.0)
            else:
                self.d.click(x, y)
            method = "node_center_fallback"
        return {"ok": True, "method": method, "id": node.res_id, "text": node.text,
                "desc": node.desc, "class": node.cls, "center": list(node.center),
                "bounds": list(node.bounds)}

    # ---------- 输入 ----------

    def clear_input(self, selector: str, pkg: Optional[str] = None,
                    empty_when_gone: Optional[str] = None, max_rounds: int = 8) -> dict:
        """清空输入框：点击聚焦 → 长按退格多轮。

        空判定：优先 a11y 可读文本为空；不可读时（自绘输入框常见）用
        empty_when_gone 选择器（如发送按钮）消失作为信号。
        """
        self.click(selector, pkg)
        time.sleep(0.5)
        self.shell("input keyevent 123")  # MOVE_END
        for i in range(max_rounds):
            if self._field_empty(pkg, empty_when_gone):
                return {"ok": True, "rounds": i}
            for _ in range(2):
                self.shell("input keyevent --longpress 67")
                time.sleep(0.45)
            for _ in range(5):
                self.shell("input keyevent 67")
                time.sleep(0.1)
            time.sleep(0.4)
        return {"ok": self._field_empty(pkg, empty_when_gone), "rounds": max_rounds}

    def _field_empty(self, pkg, empty_when_gone) -> bool:
        root = self.dump()
        cands = [n for n in root.iter() if n.editable and n.focused and n.package != "com.android.systemui"]
        for n in cands:
            if n.text:
                return False
            if n.hint and not n.text:
                return True
        if cands and not cands[0].text:
            return True
        if empty_when_gone:
            return len(S.find_nodes(root, empty_when_gone, default_pkg=pkg)) == 0
        return False

    def set_text(self, text: str, selector: Optional[str] = None, pkg: Optional[str] = None,
                 clear: bool = True) -> dict:
        """精确输入到指定组件（全原生注入，无定制输入法依赖）。

        通道优先级：
        1. ACTION_SET_TEXT —— 原生无障碍动作，标准 EditText 可用；
           自绘/加固的自定义输入框常不响应；
        2. 剪贴板 + KEYCODE_PASTE(279) —— uia2 instrumentation 原生写剪贴板 +
           InputManager 键事件粘贴，支持任意 Unicode（中文验证通过）；
        3. root `input text` —— InputManager 文本注入，仅 ASCII。
        （更早的 IME 广播通道在部分自绘/加固输入框上广播成功但文本不落框，已移除。）
        """
        if selector:
            node, kw = self._resolve(selector, pkg)
        else:
            root = self.dump()
            cands = [n for n in root.iter() if n.editable and n.focused] or \
                    [n for n in root.iter() if n.editable]
            if not cands:
                raise S.SelectorNotFound("editable+focused 节点", pkg)
            node, kw = cands[0], ({"focused": True} if cands[0].focused else {})
        if kw:
            try:
                el = self.d(**kw)
                if el.wait(timeout=3):
                    if clear:
                        try:
                            el.clear_text()
                        except Exception:
                            pass
                    if el.set_text(text):
                        return {"ok": True, "method": "action_set_text", "id": node.res_id,
                                "center": list(node.center)}
            except Exception:
                pass
        # 通道 2/3：点击聚焦 → 光标至末尾 → 清空 → 原生注入
        x, y = node.center
        self.d.click(x, y)
        time.sleep(0.6)
        if clear:
            self.d.shell("input keyevent KEYCODE_MOVE_END")
            for _ in range(max(20, 2 * len(text) + 10)):
                self.d.shell("input keyevent KEYCODE_DEL")
            time.sleep(0.4)
        if any(ord(c) > 126 for c in text):
            self.d.set_clipboard(text)
            time.sleep(0.3)
            self.d.shell("input keyevent 279")   # KEYCODE_PASTE
            time.sleep(0.6)
            return {"ok": True, "method": "clipboard_paste", "id": node.res_id,
                    "center": [x, y]}
        self.d.shell("input text " + text.replace(" ", "%s").replace("&", r"\&"))
        time.sleep(0.4)
        return {"ok": True, "method": "input_text", "id": node.res_id,
                "center": [x, y]}

    def scroll(self, direction: str = "down", container: Optional[str] = None,
               pkg: Optional[str] = None, percent: float = 0.55) -> dict:
        """在指定（或任一）scrollable 容器内滑动。down=查看下方内容（手指上滑）。"""
        if container:
            node, _ = self._resolve(container, pkg)
        else:
            root = self.dump()
            cands = [n for n in root.iter() if n.scrollable and n.package != "com.android.systemui"]
            if not cands:
                raise S.SelectorNotFound("任意 scrollable 容器", pkg)
            node = cands[0]
        l, t, r, b = node.bounds
        cx, cy = (l + r) // 2, (t + b) // 2
        if direction in ("up", "down"):
            dy = (b - t) * percent
            start, end = (cx, cy + dy / 2), (cx, cy - dy / 2)
            if direction == "up":
                start, end = end, start
        elif direction in ("left", "right"):
            dx = (r - l) * percent
            start, end = (cx + dx / 2, cy), (cx - dx / 2, cy)
            if direction == "right":
                start, end = end, start
        else:
            raise BridgeError(f"未知滚动方向 {direction}")
        self.d.swipe(start[0], start[1], end[0], end[1], 0.25)
        return {"ok": True, "container_bounds": list(node.bounds), "direction": direction}

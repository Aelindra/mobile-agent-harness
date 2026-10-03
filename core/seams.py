# -*- coding: utf-8 -*-
"""缝（capability seam）声明：观测×动作两组能力域。

- Definition：本文件——缝名/组/接口约定（Protocol，鸭子类型校验）
- Provider：实现某缝的对象，注册时携带 privilege/stealth 元数据
- Consumer：工具与插件经 ctx.resolve() 取用

privilege 与 stealth 是 provider 属性而非缝属性：同一缝可有 adb 版与
root 版 provider，运行时按消费方声明的最低要求选择。

接口约定（Protocol 不强制继承，鸭子类型）：
- ui_tree:   dump() -> Node; find(selector, pkg, timeout) -> List[Node]
- vision:    screenshot(path) -> str
- input:     tap(x, y) / swipe(x1,y1,x2,y2,dur) / key(code) / text(s, selector, pkg, clear) / clipboard
- shell:     run(cmd, root, timeout) -> str
- fs_write:  push(local, remote, root) / write(path, data, root) / rm(path, root)
- app_ctrl:  launch(pkg) / stop(pkg) / current() / list(filter, third_party) / wait_idle(pkg, timeout)
- data:      prefs(pkg, file) / db(pkg, db, sql)
- events:    subscribe(kind, fn) / wait_toast(timeout, expect, action)
- mem:       （保留，无内置 provider——私域插件提供：read/scan/search）
- proto:     （保留，无内置 provider——私域插件提供：send/recv/decode）
- clipboard: get() / set(text)
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

# ---------------- 缝清单（观测 × 动作） ----------------

OBSERVATION_SEAMS = ("ui_tree", "vision", "data", "mem", "proto", "events")
ACTION_SEAMS = ("input", "shell", "fs_write", "app_ctrl", "clipboard")
ALL_SEAMS = OBSERVATION_SEAMS + ACTION_SEAMS

SEAM_INFO: Dict[str, Dict[str, str]] = {
    # 观测组
    "ui_tree":  {"group": "observation", "desc": "无障碍语义树实时读取（dump/语义查找）"},
    "vision":   {"group": "observation", "desc": "截屏/OCR 视觉观测"},
    "data":     {"group": "observation", "desc": "应用数据层读取（shared_prefs/sqlite）"},
    "mem":      {"group": "observation", "desc": "进程内存读取/扫描（无内置 provider，私域）"},
    "proto":    {"group": "observation", "desc": "协议层收发/解码（无内置 provider，私域）"},
    "events":   {"group": "observation", "desc": "设备事件流（前台切换/Toast/崩溃）"},
    # 动作组
    "input":    {"group": "action", "desc": "输入注入（tap/swipe/key/text，多通道降级）"},
    "shell":    {"group": "action", "desc": "命令执行（adb 级 / root 级 provider）"},
    "fs_write": {"group": "action", "desc": "设备文件写入（push/write/rm，base64 通道）"},
    "app_ctrl": {"group": "action", "desc": "应用生命周期（launch/stop/current/list）"},
    "clipboard": {"group": "action", "desc": "剪贴板读写"},
}

# ---------------- provider 元数据 ----------------

# 权限级：数值仅用于"至少达到"比较
PRIVILEGE = {"adb": 0, "root": 1}

# 隐蔽等级（检测面分级，数值越高越接近人工；语义见 README「插件开发指南」）
STEALTH = {
    0: "ime",        # IME 广播/定制输入法——服务列表+输入流异常（已有被目标 app 拉黑的实证）
    1: "injected",   # adb/u2 注入——无公开硬标记，行为模型可查（helloworld 存续实证）
    2: "humanized",  # 拟人增强（偏移/时长/轨迹抖动）——私域包装器
    3: "root_forged",  # root 伪造输入栈/直改——注入无标记，root 本身是独立战场
}
STEALTH_MIN_DEFAULT = 1  # 默认不接受低于 adb 注入级的通道（IME 通道已被实证拉黑）


@runtime_checkable
class UiTreeProvider(Protocol):
    def dump(self): ...
    def find(self, selector: str, pkg: Optional[str] = None, timeout: float = 0) -> list: ...


@runtime_checkable
class InputProvider(Protocol):
    def tap(self, x: int, y: int, duration_ms: int = 50) -> dict: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict: ...
    def key(self, keycode) -> dict: ...
    def text(self, s: str, selector: Optional[str] = None, pkg: Optional[str] = None,
             clear: bool = True) -> dict: ...


@runtime_checkable
class ShellProvider(Protocol):
    def run(self, cmd: str, root: bool = False, timeout: float = 30) -> str: ...


@runtime_checkable
class AppCtrlProvider(Protocol):
    def launch(self, pkg: str, timeout: float = 15) -> dict: ...
    def stop(self, pkg: str) -> dict: ...
    def current(self) -> tuple: ...


_PROTOCOLS: Dict[str, type] = {
    "ui_tree": UiTreeProvider, "input": InputProvider, "shell": ShellProvider,
    "app_ctrl": AppCtrlProvider,
}


class ProviderDescriptor:
    """一次 provider 注册的全部元数据（能力发现的数据源）。"""

    __slots__ = ("name", "seam", "provider", "privilege", "stealth", "source")

    def __init__(self, name: str, seam: str, provider: Any,
                 privilege: str = "adb", stealth: int = STEALTH_MIN_DEFAULT,
                 source: str = "builtin"):
        if seam not in ALL_SEAMS:
            raise ValueError(f"未知缝 {seam}，支持: {ALL_SEAMS}")
        if privilege not in PRIVILEGE:
            raise ValueError(f"未知 privilege {privilege}，支持: {sorted(PRIVILEGE)}")
        if stealth not in STEALTH:
            raise ValueError(f"未知 stealth {stealth}，支持: {sorted(STEALTH)}")
        proto = _PROTOCOLS.get(seam)
        if proto is not None and not isinstance(provider, proto):
            raise TypeError(
                f"provider {name} 不满足缝 {seam} 的接口约定 {proto.__name__}（缺方法）")
        self.name, self.seam, self.provider = name, seam, provider
        self.privilege, self.stealth, self.source = privilege, stealth, source

    def as_dict(self) -> dict:
        return {"name": self.name, "seam": self.seam, "privilege": self.privilege,
                "stealth": self.stealth, "stealth_label": STEALTH[self.stealth],
                "source": self.source}

    def __repr__(self):
        return (f"<Provider {self.name} seam={self.seam} privilege={self.privilege} "
                f"stealth={STEALTH[self.stealth]} src={self.source}>")

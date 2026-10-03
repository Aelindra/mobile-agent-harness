# -*- coding: utf-8 -*-
"""插件运行时：注册即副作用、卸载逆序回滚、能力发现、mtime 热载。

- ctx.register_* 返回 disposer 并记入 owner 账本；卸载 = 逆序回滚
- 依赖在 apply 时 eager 解析，缺失即报 CapabilityUnavailable
- capabilities() 汇总各缝 provider；device_lock 串行化工具调用
- 同步 + 线程；状态一律进 ctx，禁止模块级可变全局

Python 插件格式：
    NAME = "my_plugin"
    VERSION = "0.1"
    def apply(ctx):
        ctx.register_tool(...)          # 无需自管清理
        ctx.provide("input", obj, ...)
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional

from .seams import ALL_SEAMS, PRIVILEGE, ProviderDescriptor


class ToolDef:
    __slots__ = ("name", "description", "schema", "handler", "source")

    def __init__(self, name: str, description: str, schema: dict,
                 handler: Callable, source: str):
        self.name, self.description, self.schema = name, description, schema
        self.handler, self.source = handler, source

    def as_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": self.schema, "source": self.source}


Disposer = Callable[[], None]


class CapabilityUnavailable(RuntimeError):
    def __init__(self, seam: str, need: str, have: List[str]):
        self.seam, self.need, self.have = seam, need, have
        super().__init__(f"缝 {seam} 无满足要求（{need}）的 provider；当前: {have or '无'}")


class Context:
    """插件运行时上下文：缝/工具/事件三本账 + 设备锁。所有注册可回滚。"""

    def __init__(self, name: str = "default", transcript=None):
        self.name = name
        self.transcript = transcript
        self.device_lock = threading.RLock()   # 单设备串行铁律；热载静默也走它
        self._mu = threading.RLock()
        self._providers: Dict[str, List[ProviderDescriptor]] = {s: [] for s in ALL_SEAMS}
        self._tools: Dict[str, ToolDef] = {}
        self._events: Dict[str, List[Callable]] = {}
        self._ledgers: Dict[str, List[Disposer]] = {}   # owner -> [disposer]（注册序）
        self._owner_stack: List[str] = []               # apply() 期间的记账对象
        # 供内置工具 handler 使用的知名服务（assembler 填充；不算"注册"）
        self.bridge = None
        self.engine = None

    # ---------- 记账 ----------
    def _book(self, disposer: Disposer):
        owner = self._owner_stack[-1] if self._owner_stack else "adhoc"
        self._ledgers.setdefault(owner, []).append(disposer)

    @property
    def current_owner(self) -> str:
        return self._owner_stack[-1] if self._owner_stack else "adhoc"

    @contextmanager
    def scoped(self, owner: str):
        """把作用域内的全部注册记到 owner 账上（内置组件/组装器用）。"""
        self._owner_stack.append(owner)
        try:
            yield self
        finally:
            self._owner_stack.pop()

    def unload(self, owner: str) -> int:
        """逆序回滚 owner 的全部注册，返回回滚条数。"""
        with self._mu:
            disposers = self._ledgers.pop(owner, [])
        n = 0
        for d in reversed(disposers):
            try:
                d()
                n += 1
            except Exception:  # noqa: BLE001 - 卸载尽力而为，不中断其余回滚
                if self.transcript:
                    self.transcript.log("unload_error", owner=owner, err=traceback.format_exc()[-400:])
        if self.transcript:
            self.transcript.log("unloaded", owner=owner, disposers=n)
        return n

    def owners(self) -> List[str]:
        with self._mu:
            return sorted(self._ledgers)

    # ---------- 缝 / provider ----------
    def provide(self, seam: str, provider: Any, *, name: Optional[str] = None,
                privilege: str = "adb", stealth: int = 1) -> Disposer:
        """注册 provider 到缝；返回 disposer（摘除该注册）。"""
        desc = ProviderDescriptor(name or f"{seam}:{self.current_owner}", seam,
                                  provider, privilege, stealth, self.current_owner)
        with self._mu:
            self._providers[seam].append(desc)

        def _dispose():
            with self._mu:
                lst = self._providers[seam]
                if desc in lst:
                    lst.remove(desc)

        self._book(_dispose)
        if self.transcript:
            self.transcript.log("provide", **desc.as_dict())
        return _dispose

    def resolve(self, seam: str, min_privilege: str = "adb",
                min_stealth: int = 1) -> Any:
        """取满足约束的 provider。策略：满足前提下 stealth 最高、privilege 最低
        （能力够用即可、检测面最小优先），同级取最新注册。找不到抛 CapabilityUnavailable。"""
        need_p = PRIVILEGE[min_privilege]
        with self._mu:
            cands = [p for p in self._providers[seam]
                     if PRIVILEGE[p.privilege] >= need_p and p.stealth >= min_stealth]
        if not cands:
            with self._mu:
                have = [f"{p.name}({p.privilege}/{p.stealth})" for p in self._providers[seam]]
            raise CapabilityUnavailable(seam, f"privilege>={min_privilege},stealth>={min_stealth}", have)
        cands.sort(key=lambda p: (-p.stealth, PRIVILEGE[p.privilege]))
        return cands[0].provider

    def capabilities(self) -> Dict[str, List[dict]]:
        """能力发现视图：每缝当前可用 provider 及其元数据。"""
        with self._mu:
            return {s: [p.as_dict() for p in ps] for s, ps in self._providers.items()}

    # ---------- 工具 ----------
    def register_tool(self, name: str, description: str,
                      schema: Optional[dict] = None,
                      handler: Optional[Callable] = None) -> Disposer:
        """注册工具；handler 省略时返回装饰器（两种用法等价，均返回 disposer）。"""
        if not name or "." not in name:
            raise ValueError(f"工具名必须是 ns.name 形式: {name!r}")

        def _do(h: Callable) -> Disposer:
            with self._mu:
                if name in self._tools:
                    raise ValueError(f"工具名冲突: {name}（owner={self.current_owner}）")
                self._tools[name] = ToolDef(name, description,
                                            schema or {"type": "object"}, h,
                                            self.current_owner)

            def _dispose():
                with self._mu:
                    self._tools.pop(name, None)

            self._book(_dispose)
            return _dispose

        if handler is not None:
            return _do(handler)

        def _deco(h: Callable) -> Callable:
            _do(h)
            return h
        return _deco

    def tool(self, name: str) -> Optional[ToolDef]:
        with self._mu:
            return self._tools.get(name)

    def tools(self) -> List[ToolDef]:
        with self._mu:
            return list(self._tools.values())

    # ---------- 事件总线 ----------
    def subscribe(self, event: str, fn: Callable) -> Disposer:
        with self._mu:
            self._events.setdefault(event, []).append(fn)

        def _dispose():
            with self._mu:
                lst = self._events.get(event, [])
                if fn in lst:
                    lst.remove(fn)

        self._book(_dispose)
        return _dispose

    def emit(self, event: str, **data):
        with self._mu:
            fns = list(self._events.get(event, []))
        for fn in fns:
            try:
                fn(**data)
            except Exception:  # noqa: BLE001 - 订阅者错误不阻断广播
                if self.transcript:
                    self.transcript.log("event_error", event=event,
                                        err=traceback.format_exc()[-300:])


class PluginRuntime:
    """Python 插件加载/卸载/热载（仅插件目录；core provider 重启级——P 定案四）。

    热载 = unload(旧 disposers 回滚) → 以新代模块名 import → apply 重跑。
    模块级全局可变量在插件纪律中禁止，故新代模块无需清理旧引用。
    """

    def __init__(self, ctx: Context, plugin_dirs: Optional[List[str]] = None):
        self.ctx = ctx
        self.plugin_dirs = [os.path.abspath(p) for p in (plugin_dirs or [])]
        self._gen = 0
        self._mtime: Dict[str, float] = {}      # plugin_id -> (file mtime)
        self._files: Dict[str, str] = {}        # plugin_id -> path
        self._mu = threading.Lock()

    def scan(self) -> Dict[str, str]:
        """插件目录扫描：单文件 plugin.py 与 plugin_dir/plugin.py 两种形态。"""
        out: Dict[str, str] = {}
        for d in self.plugin_dirs:
            if not os.path.isdir(d):
                continue
            for entry in sorted(os.listdir(d)):
                p = os.path.join(d, entry)
                if entry.endswith(".py") and entry != "__init__.py":
                    out[entry[:-3]] = p
                elif os.path.isdir(p) and os.path.isfile(os.path.join(p, "plugin.py")):
                    out[entry] = os.path.join(p, "plugin.py")
        return out

    def load(self, plugin_id: str, path: str, force: bool = False) -> dict:
        with self._mu:
            mt = os.path.getmtime(path)
            if not force and self._mtime.get(plugin_id) == mt:
                return {"loaded": plugin_id, "hot": False}
            self._gen += 1
            modname = f"_plugin_{plugin_id.replace('.', '_')}_g{self._gen}"
            # 直接 compile+exec：同路径同长度的热改会命中旧 pyc（头部 mtime 取整+size 相等），
            # 文件系统字节码缓存对插件热载不可信
            import types as _types
            src = open(path, encoding="utf-8").read()
            mod = _types.ModuleType(modname)
            mod.__file__ = path
            exec(compile(src, path, "exec"), mod.__dict__)
            sys.modules[modname] = mod   # 新代模块号；绝不 reload
            apply = getattr(mod, "apply", None)
            if not callable(apply):
                sys.modules.pop(modname, None)
                raise RuntimeError(f"插件 {plugin_id} 缺 apply(ctx) 入口")
            self.ctx.unload(plugin_id)           # 旧代回滚（若有）
            self.ctx._owner_stack.append(plugin_id)
            try:
                apply(self.ctx)
            finally:
                self.ctx._owner_stack.pop()
            self._mtime[plugin_id] = mt
            self._files[plugin_id] = path
        if self.ctx.transcript:
            self.ctx.transcript.log("plugin_load", plugin=plugin_id, path=path,
                                    name=getattr(mod, "NAME", plugin_id),
                                    version=getattr(mod, "VERSION", "?"))
        return {"loaded": plugin_id, "hot": self._gen > 1}

    def load_all(self, force: bool = False) -> dict:
        out = {}
        for pid, path in self.scan().items():
            try:
                out[pid] = self.load(pid, path, force=force)
            except Exception as ex:  # noqa: BLE001 - 单插件失败不阻断其余
                out[pid] = {"error": f"{type(ex).__name__}: {ex}"}
                if self.ctx.transcript:
                    self.ctx.transcript.log("plugin_error", plugin=pid,
                                            err=traceback.format_exc()[-500:])
        return out

    def reload_changed(self) -> dict:
        """mtime 变化的插件热载；热载在设备锁内静默进行。"""
        with self.ctx.device_lock, self._mu:
            changed = {pid: p for pid, p in self._files.items()
                       if os.path.getmtime(p) != self._mtime[pid]}
            fresh = {pid: p for pid, p in self.scan().items()
                     if pid not in self._mtime and pid not in self._files}
        out = {}
        for pid, path in {**changed, **fresh}.items():
            try:
                out[pid] = self.load(pid, path)
            except Exception as ex:  # noqa: BLE001
                out[pid] = {"error": f"{type(ex).__name__}: {ex}"}
        return out

    def unload(self, plugin_id: str) -> int:
        with self._mu:
            self._mtime.pop(plugin_id, None)
            self._files.pop(plugin_id, None)
        return self.ctx.unload(plugin_id)

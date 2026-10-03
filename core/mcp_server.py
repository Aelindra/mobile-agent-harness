# -*- coding: utf-8 -*-
"""L4 MCP stdio server：把 L2 Base Tools + L3 harness 工具暴露给任意 MCP 客户端。

启动（三等价形态，任意 cwd 均可）：
  cd mobile-agent-harness && python core/mcp_server.py
  python -m core.mcp_server
  claude mcp add phone-agent -- python core/mcp_server.py   # 任意 MCP 客户端等价

设备选择优先级：--serial > ANDROID_SERIAL > AGENT_SERIAL > DEFAULT_SERIAL（缺省 emulator-5555，
AGENT_SERIAL_DEFAULT 可覆盖，AGENT_SERIAL_USB 配 USB 兜底；设备离线时 server 照常启动，调用时自动重连）。

协议：stdio 按行 JSON-RPC 2.0（MCP 2024-11-05），实现
initialize / ping / tools/list / tools/call。所有工具返回
{"ok": bool, "error"?: str, ...} 包络，isError 随 ok，永不抛未捕获异常。
"""
from __future__ import annotations

import json
import os
import sys
import time

if __package__ in (None, ""):  # 直接脚本运行：python mcp_server.py
    # 先摘掉脚本目录，防止 core/selectors.py 遮蔽 stdlib selectors（adbutils→socket 链会踩中）
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]
    sys.path.insert(0, os.path.dirname(_here))
    from core.providers import assemble
    from core.registry import Registry
else:
    from .providers import assemble
    from .registry import Registry

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "mobile-agent-harness", "version": "0.5.0"}

INSTRUCTIONS = """整台安卓手机的组件级操控（uiautomator2 无障碍桥 + GKD 风格语义选择器 + harness 插件）。禁止裸坐标：所有定位用选择器。

## 标准工作流
1. app.launch {pkg} 拉起目标应用
2. ui.snapshot 拿紧凑快照（读屏默认首选：token 约为 ui.dump 的 1/10；ui.click_handle/ui.text_handle 按句柄直接操作）；ui.dump/ui.find 用于深度定位
3. ui.click / ui.set_text / ui.scroll 执行动作
4. vision.screenshot 截图确认（部分自绘输入框的 dump 文本不可靠，必须截图验证输入）

## 选择器语法（组合用 ` && `，pkg@ 建议始终带上防误点其他应用）
id@xxx:id/yyy 或 id@yyy(按 pkg 补全) | text@正则(^…$ 锚定精确) | desc@正则 | class@android.widget.EditText
pkg@com.x | [clickable=true] [checked=false] [editable=true] [scrollable=true] | nth@0/-1
相对定位: parent@<sel> child@<sel> under@<sel> hastext@<sel>

## 返回包络
所有工具返回 {"ok": bool, "error"?: str, "error_type"?: str, ...工具特定字段}；
ok=false 是业务结果（如选择器未命中 error_type=selector_not_found、等待超时），按内容决策而非重试。

## 高层优先
有对应 harness 工具时优先 harness.run（内置最小示例：com.android.settings 的 launch/search；
社区 app 包见 awesome-mobile-agent-harness 仓库），比裸 ui.* 更稳。首次真实执行 dry_run=true 预检选择器命中。

## 其他
- 中文输入走 ui.set_text（ACTION_SET_TEXT→剪贴板粘贴→input text 三级原生通道，已内置）
- shell.run root=true 走 su（设备需已 root）；风险命令需 allow_risk=true
- vision.diff 对比两次截图变化（无需 OCR 的界面变化检测）；ui.wait_for 等弹窗/加载
- file.push/file.pull 用 base64 管道传输（WiFi adb push 二进制不可靠的替代）
- net.http 支持 proxy 参数（可接 mitmproxy 等审计代理）
- ui.dump 返回大 JSON，字段: nodes[{path,package,class,id,text,desc,bounds,center,clickable,checked,...}]
- ui.snapshot=纯 a11y 紧凑快照（首选读屏）；ui2.state=a11y×OCR 融合（游戏 canvas 等无语义界面用）；两者元素都带句柄 eN，用 ui.click_handle/ui.text_handle 操作
"""


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="mcp_server", description="手机 agent MCP stdio server")
    ap.add_argument("--serial", default=None,
                    help="设备 serial（缺省读 ANDROID_SERIAL/AGENT_SERIAL，再缺省 emulator-5555）")
    ap.add_argument("--transcript", default=None, help="JSONL 记录路径（缺省 logs/mcp_*.jsonl）")
    ap.add_argument("--plugin-dirs", default=None,
                    help="插件目录（逗号分隔；缺省 <root>/plugins）")
    a = ap.parse_args(argv)

    # Windows 控制台默认 GBK，MCP stdio 必须 UTF-8
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tr_path = a.transcript or os.path.join(root, "logs",
                                           f"mcp_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    ctx, pipe, pr = assemble({
        "serial": a.serial,
        "transcript_path": tr_path,
        "plugin_dirs": a.plugin_dirs.split(",") if a.plugin_dirs else None,
        "ctx_name": "mcp",
    })
    tr = ctx.transcript
    reg = Registry(ctx, pipe, ctx.engine, plugin_runtime=pr)
    tr.log("mcp_start", serial=a.serial, transcript=tr_path)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                tr.log("mcp_bad_json", line=line[:200])
                continue
            method = msg.get("method", "")
            mid = msg.get("id")
            if method.startswith("notifications/"):
                tr.log("mcp_notify", method=method)
                continue  # 通知无需应答
            t0 = time.time()
            try:
                if method == "initialize":
                    result = {"protocolVersion": PROTOCOL_VERSION,
                              "capabilities": {"tools": {"listChanged": False}},
                              "serverInfo": SERVER_INFO,
                              "instructions": INSTRUCTIONS}
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": reg.list()}
                elif method == "tools/call":
                    params = msg.get("params") or {}
                    name = params.get("name") or ""
                    args = dict(params.get("arguments") or {})
                    if args.pop("__trust", False) and name == "harness.run":
                        args["allow_untrusted"] = True
                    r = reg.call(name, args)
                    mcp_blocks = r.pop("_mcp_content", None) if isinstance(r, dict) else None
                    if mcp_blocks:
                        result = {"content": mcp_blocks, "isError": not r.get("ok", True)}
                    else:
                        text = json.dumps(r, ensure_ascii=False, indent=1)
                        result = {"content": [{"type": "text", "text": text}],
                                  "isError": not r.get("ok", True)}
                else:
                    raise KeyError(f"method not found: {method}")
                payload = {"jsonrpc": "2.0", "id": mid, "result": result}
            except KeyError as ex:  # 未知方法 → JSON-RPC 标准错误码
                payload = {"jsonrpc": "2.0", "id": mid,
                           "error": {"code": -32601, "message": str(ex)}}
            except Exception as ex:  # noqa: BLE001 - 不让任何异常杀死 server
                payload = {"jsonrpc": "2.0", "id": mid,
                           "error": {"code": -32603,
                                     "message": f"{type(ex).__name__}: {ex}"}}
            if method == "tools/call":
                ok = not payload.get("result", {}).get("isError", True)
            else:
                ok = "result" in payload
            tr.log("mcp", method=method, duration_ms=int((time.time() - t0) * 1000),
                   ok=ok)
            _send(payload)
    finally:
        tr.log("mcp_stop")
        tr.close()


if __name__ == "__main__":
    serve()

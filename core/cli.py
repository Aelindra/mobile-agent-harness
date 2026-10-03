# -*- coding: utf-8 -*-
"""L4 CLI 入口：agent 的命令行形态。

用法：
  python -m core.cli tools                      # 列出全部工具（L2+L3）
  python -m core.cli call ui.click --args '{"selector":"text@^Display$","pkg":"com.android.settings"}'
  python -m core.cli run --pkg com.android.settings --tool search --params '{"query":"bluetooth"}'
  python -m core.cli run ... --dry-run          # 安全阀：只验证选择器命中
  python -m core.cli distill --pkg <pkg>
  python -m core.cli lint --pkg com.android.settings
  python -m core.cli mcp                        # MCP stdio server 模式
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if __package__ in (None, ""):  # 直接脚本运行：防止 core/ 遮蔽 stdlib（selectors 坑）
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]
    sys.path.insert(0, os.path.dirname(_here))
    from core.harness import TrustError
    from core.providers import assemble
    from core.registry import Registry
else:
    from .harness import TrustError
    from .providers import assemble
    from .registry import Registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(transcript_path=None, shot_dir=None):
    """组装运行时（P1 新架构：ctx + pipeline + 动态 Registry）。"""
    ctx, pipe, pr = assemble({"transcript_path": transcript_path, "shot_dir": shot_dir,
                              "ctx_name": "cli"})
    reg = Registry(ctx, pipe, ctx.engine, plugin_runtime=pr)
    return ctx.bridge, reg, ctx.engine, ctx.transcript


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent", description="手机 AI Agent Harness CLI")
    ap.add_argument("--transcript", default=None, help="JSONL 记录路径")
    ap.add_argument("--shots", default=None, help="截图输出目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tools", help="列出工具面")
    p_call = sub.add_parser("call", help="调用 L2 基础工具")
    p_call.add_argument("tool")
    p_call.add_argument("--args", default="{}")
    p_run = sub.add_parser("run", help="运行 harness 工具")
    p_run.add_argument("--pkg", required=True)
    p_run.add_argument("--tool", required=True)
    p_run.add_argument("--params", default="{}")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--trust", action="store_true", help="信任门：允许 dry_run 级 harness 真实执行")
    p_distill = sub.add_parser("distill", help="对某 app 蒸馏 harness")
    p_distill.add_argument("--pkg", required=True)
    p_distill.add_argument("--max-actions", type=int, default=24)
    p_lint = sub.add_parser("lint", help="harness 静态检查")
    p_lint.add_argument("--pkg", default=None)
    p_mcp = sub.add_parser("mcp", help="启动 MCP stdio server")
    p_dump = sub.add_parser("dump", help="dump 当前节点树")
    p_dump.add_argument("--save", default=None)

    a = ap.parse_args(argv)
    if a.cmd == "mcp":
        from .mcp_server import serve
        serve()
        return 0

    bridge, reg, engine, tr = build(a.transcript, a.shots)
    try:
        if a.cmd == "tools":
            print(json.dumps(reg.list(), ensure_ascii=False, indent=1))
        elif a.cmd == "call":
            r = reg.call(a.tool, json.loads(a.args))
            if isinstance(r, dict) and "_mcp_content" in r:
                r = {k: v for k, v in r.items() if k != "_mcp_content"}
                r["note"] = "截图内容块已按 client 模式生成（仅 MCP 通道可携带，CLI 已省略）"
            print(json.dumps(r, ensure_ascii=False, indent=1))
        elif a.cmd == "run":
            try:
                r = engine.run(a.pkg, a.tool, json.loads(a.params),
                               dry_run=a.dry_run, allow_untrusted=a.trust)
            except TrustError as ex:
                print(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False))
                return 2
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r.get("ok") else 1
        elif a.cmd == "distill":
            from .distill import distill
            r = distill(bridge, a.pkg, engine, transcript=tr, max_actions=a.max_actions,
                        shot_dir=a.shots)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r.get("ok") else 1
        elif a.cmd == "lint":
            from .lint import lint_harness
            pkgs = [a.pkg] if a.pkg else [h["package"] for h in engine.list() if "package" in h]
            out = [lint_harness(engine.load(p)) for p in pkgs]
            print(json.dumps(out, ensure_ascii=False, indent=1))
        elif a.cmd == "dump":
            root = bridge.dump()
            data = root.to_dict(max_nodes=100000)
            if a.save:
                with open(a.save, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                print(f"saved {len(data['nodes'])} nodes -> {a.save}")
            else:
                print(json.dumps(root.to_dict(max_nodes=200), ensure_ascii=False, indent=1))
        return 0
    finally:
        if tr:
            tr.close()


if __name__ == "__main__":
    sys.exit(main())

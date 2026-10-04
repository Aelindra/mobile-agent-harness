# mobile-agent-harness

**Android device automation for AI agents.** MCP server, CLI, and a hot-reloadable plugin runtime on top of a self-healing `uiautomator2` bridge. No device-side app required.

> 中文文档：[README.zh-CN.md](README.zh-CN.md)

This is the base runtime only. Plugins live in the community catalog: [awesome-mobile-agent-harness-plugin](https://github.com/Aelindra/awesome-mobile-agent-harness-plugin). App business knowledge for AI lives in [app-knowledge-packs](https://github.com/Aelindra/app-knowledge-packs), which this repo's `knowledge/` loader reads directly.

## Install

```bash
git clone https://github.com/Aelindra/mobile-agent-harness
cd mobile-agent-harness
pip install -e .          # add ".[ocr]" for vision.ocr / ui2.*
```

Requires Python 3.9+ and `adb` on PATH with USB debugging enabled.

## Quick start

1. Point the bridge at your device:

```bash
export AGENT_SERIAL_DEFAULT=192.168.1.23:5555   # or "emulator-5555"
```

2. List the tool surface (works without a device) and run the offline tests:

```bash
python -m core.cli tools
python tests/test_runtime.py
```

3. Call a tool:

```bash
python -m core.cli call ui.snapshot
python -m core.cli call ui.click --args '{"selector":"text@^Network & internet$","pkg":"com.android.settings"}'
```

4. Attach to any MCP client:

```json
{
  "mcpServers": {
    "phone": {
      "command": "python",
      "args": ["-m", "core.cli", "mcp"],
      "cwd": "/path/to/mobile-agent-harness",
      "env": { "AGENT_SERIAL_DEFAULT": "192.168.1.23:5555" }
    }
  }
}
```

All tools return a `{"ok": bool, "error"?, ...}` envelope. `ok=false` is a business result (e.g. `error_type=selector_not_found`) — adapt instead of retrying.

## Tools

| Group | Tools |
|---|---|
| Observation | `ui.snapshot` `ui.dump` `ui.find` `ui2.state` `ui2.screen_evidence` `ui2.timeline` `ui2.find_templates` `vision.screenshot` `vision.ocr` `vision.diff` |
| Action | `ui.click` `ui.click_handle` `ui.text_handle` `ui.hold_read` `ui.set_text` `ui.scroll` `ui.back` `ui.home` `input.tap` `input.swipe` `input.key` |
| App | `app.launch` `app.current` `app.probe` `app.list` `app.stop` `app.wait_idle` |
| Shell & data | `shell.run` `state.prefs` `state.db` `file.push` `file.pull` `net.http` |
| Events | `events.tail` `events.wait` `events.capture` `events.record_start` `events.record_stop` |
| Knowledge & tasks | `knowledge.list` `knowledge.guide` `ui2.check_states` `ui2.orient` `task.begin` `task.note` `task.end` `template.add` `sys.capabilities` |

`ui.snapshot` returns a compact accessibility-tree listing with element handles (`e0`, `e1`, ...); `ui.click_handle` acts on a handle with drift detection. `ui2.state` fuses the accessibility tree with OCR for canvas-drawn UIs. `ui2.screen_evidence` collects measurable screen features (overlay level, motion, color, layout density) without interpreting them. `app.probe` returns deterministic side signals (package, version, orientation); `ui2.timeline` captures N frames of lightweight evidence in time order; `ui2.orient` probes context, routes to matching knowledge packs, and evaluates their state discriminators — returning matched states or ranked hypotheses with the missing evidence listed.

## Selectors

Selectors resolve against the live accessibility tree. Coordinates are not used.

```python
# ui.click selector examples
"id@d98 && pkg@com.example.app"       # resource-id, short form completed with pkg@
"text@^Settings$ && pkg@com.example.app"
"class@android.widget.EditText && [clickable=true]"
"desc@^Search$"                        # content-desc regex
```

## Harnesses and plugins

A harness file declares per-app flows as selector steps with pre/postconditions; `distill` drafts one by exploring an app, `lint` checks selector quality, and failed postconditions mark tools stale for re-distillation. See `harnesses/com.android.settings/harness.json5` for the built-in example.

Plugins drop into `plugins/` and register tools or capability providers at load time; registrations are reversible and hot-reloaded on file change. See the Plugin development guide in [README.zh-CN.md](README.zh-CN.md) and `knowledge/README.md` for the knowledge-pack format.

For business context on apps where model priors are unreliable, point agents at [app-knowledge-packs](https://github.com/Aelindra/app-knowledge-packs) — a community catalog of structural app surveys in the open Agent Skills format, split per feature module for complex apps. Mobile scenarios in particular are private-domain and underrepresented in training data, so agents benefit from loading the relevant survey before operating an unfamiliar app. The local `knowledge/` directory remains this repo's runtime layer (packs, icon templates, state discriminators).

## Configuration

| Variable | Description | Example |
|---|---|---|
| `AGENT_SERIAL_DEFAULT` | Device serial used when `--serial`/`ANDROID_SERIAL` absent | `192.168.1.23:5555` |
| `AGENT_SERIAL_USB` | USB serial fallback | `0123456789abcdef` |
| `MAH_FRAME_STREAM` | `1` enables the H.264 frame stream for observation tools | `1` |
| `MAH_VISION_MODE` | Vision enhancement: `off` / `client` / `endpoint` | `client` |
| `MAH_VLM_URL` / `MAH_VLM_MODEL` | External vision endpoint (OpenAI-compatible) | `http://127.0.0.1:11434/v1/chat/completions` |

## How it compares

- vs **mobile-mcp / agent-device**: they are generic device toolsets; this project adds the layer above transport — per-app knowledge as files (harnesses), a hot-reloadable plugin runtime with capability seams, and an explicit adb/root privilege model.
- vs **droidrun**: droidrun requires a device-side accessibility app; this project is zero-install over adb.
- Root support is declarative: if the device already has `su`, the shell seam gains a root provider gated by a command allowlist. This project does not provide root.

## Known limitations

Messaging, banking, and e-commerce apps run active anti-automation risk control; the adb tier is unreliable on them. Target use is development, testing, and your own device workflows.

## Contributing

Plugins are contributed to [awesome-mobile-agent-harness-plugin](https://github.com/Aelindra/awesome-mobile-agent-harness-plugin); app business knowledge to [app-knowledge-packs](https://github.com/Aelindra/app-knowledge-packs) — see their contributing guides.

## License

MIT.

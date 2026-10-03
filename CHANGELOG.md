# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/) (informally); versioning is SemVer-ish.

## 0.5.0 - 2026-10-03

First public release.

### Added

- `ui.snapshot` — LLM-friendly compact accessibility snapshot: pruned, deduplicated,
  reading-order element list with stable handles (`e0`, `e1`, ...). Token cost is
  roughly an order of magnitude below `ui.dump` on content-rich screens (~0.2s).
- `ui.click_handle` / `ui.text_handle` — act by snapshot handle; a11y-backed handles
  re-localize on the live tree with drift guards (`handle_stale` error envelope when
  the screen changed), OCR-only handles fall back to snapshot centers.
- `ui2.state` v2 — a11y×OCR fusion: elements matched to an a11y anchor take their
  interactivity from accessibility attributes (no heuristics); unmatched OCR lines
  fall back to heuristics and are graded `src:"ocr"`. Full-screen canvas anchors
  (SurfaceView etc.) are kept as `k:"canvas"` background and no longer swallow OCR
  lines. Screenshot auto-downscales to 1280px before OCR (warm ~1.6s CPU on a busy
  2772×1280 game HUD).
- `tests/manual_readscreen.py` — on-device manual acceptance with a
  foreground-occupancy guard (read-only while the user is inside an app).
- `ui.hold_read` — long-press tooltip reader: holds the target in a background
  thread and diffs a11y tree (+ optional OCR) while held, returning the tooltip
  text (tooltips, icon menus).
- `task.begin / task.note / task.end` — business-intent journal: the agent
  declares goal / current reasoning + expectation / outcome alongside tool calls;
  rendered by mah-console as a business-level operation timeline.
- `core/scheduler.py` + `mah-scheduler` — JSON-configured scheduled harness runs
  (`schedules.json`, gitignored) with a foreground-occupancy guard; run history
  in `logs/scheduler_runs.jsonl`.
- `ui2.find_templates` / `template.add` — declarative icon atlas (`templates/<game>/`):
  crop an icon from any screenshot, declare its label/description once, then
  locate it on any screen by grayscale template matching — identification works
  whenever the icon is on screen, no hover/tooltips needed. Validated on-device:
  cross-match confidence 0.94/0.91 at pixel-exact positions; correctly reports
  "not on screen" when the HUD is hidden (death/lobby).
- `ui2.screen_evidence` — screen evidence collector (evidence, not verdicts):
  HUD action-bar presence (atlas templates with `kind:"hud"`), gray/dimmed
  overlay detection (HSV saturation), and two-frame motion score, with
  per-state discriminators (dead vs in-transit vs cinematic). Designed to
  prevent interpretation hallucinations on transition frames: conclusions
  require all declared discriminators to match; otherwise alternative
  hypotheses are listed.
- `ui2.scene_match` (optional, `.[scene]`) — zero-shot scene/pattern classification
  over candidate label phrases via a CLIP-family model (labels are declarations;
  adding a scene = adding a phrase, no training). Chinese labels via
  `MAH_SCENE_MODEL=OFA-Sys/chinese-clip-vit-base-p16`.
- `vision.ask` — configuration-driven general-vision entry point (enhancement
  layer, not the main path). `MAH_VISION_MODE`: `client` returns the screenshot
  as an MCP image content block so vision-capable client models judge it
  themselves (zero extra deployment); `endpoint` proxies the question to an
  externally deployed VLM (`MAH_VLM_URL`/`MAH_VLM_MODEL`, same as
  `vision.vlm`); `off` (default) keeps the deterministic evidence layer only.
  MCP image content blocks are exempt from result clipping (protocol payload).
- `knowledge/` + `core/knowledge.py` — knowledge packs: one knowledge source,
  two consumption faces. `guide.md` = context face (read by the LLM, surfaced
  via `knowledge.guide` to any MCP client regardless of skill support);
  `templates/` + `states` = runtime face (interpreted by core: pack atlases
  join `ui2.find_templates` search automatically; `ui2.check_states` evaluates
  declared state discriminators against collected screen evidence — `ok=true`
  requires all expressions to match, insufficient evidence is listed separately,
  never treated as a negative). Loading is registration-with-rollback under the
  pack's owner, same contract as plugins. `_`-prefixed dirs are not auto-loaded
  (`_demo_pack` ships as a living format example).
- **Zero-interaction hidden info** — `ui.snapshot` now surfaces what was always
  in the dump but previously dropped: `tooltipText` (developer-declared tooltips,
  API 26+ — readable without any hover; tooltip-only nodes are included and
  marked non-interactive), `off:1` for off-screen cached pages (ViewPager
  neighbors laid out outside the viewport, sorted after on-screen content),
  and `vis` for non-visible nodes. `dumpsys activity top` view-hierarchy was
  evaluated for detecting GONE views and rejected: it times out on large apps
  (observed on HyperOS) — noted as an unreliable path rather than shipped.
- `core/frames.py` (optional, `MAH_FRAME_STREAM=1`) — H.264 frame stream:
  `screenrecord --output-format=h264 -` piped over adb exec-out and decoded with
  PyAV (60fps observed on-device) into a latest-frame cache. All observation
  tools (`vision.screenshot`, `ui2.state`, `ui2.find_templates`,
  `ui2.screen_evidence`) read the cache transparently when fresh (~250ms/frame
  vs ~420ms per-call screenshot in bursts; frame age ≤33ms instead of ~400ms),
  with automatic fallback to per-call screenshots. Costs: ~2s encoder spin-up,
  a persistent decode thread, a <1s gap every 180s (screenrecord time-limit,
  auto-restarted). Off by default.
- `core/eventbus.py` — the event plane: a persistent `adb logcat -v epoch -b
  main,events` stream parsed into structured events (`page_change` incl. MIUI's
  `Displayed pkg/activity` signal, `app_start` from `am_proc_start`, `app_crash`
  with Process-line correlation), plus frame-diff `visual_change` events when
  the frame stream is on. Exposed as `events.tail` (recent-events situational
  awareness) and `events.wait` (push-wait instead of sleep-polling; verify by
  frame/snapshot after the event). Events are signals — semantic judgment stays
  with the evidence/discriminator layer.
- Related project: `mah-console` — local web console (business timeline,
  schedule editor, device status). MCP stdio remains the universal egress for
  desktop agents; the console is the egress for humans.

### Added (base runtime)

- **L1 bridge**: self-healing `uiautomator2` bridge (lazy connect, probe TTL,
  disconnect-pattern detection with automatic reconnect and retry, WiFi `adb connect`
  with USB fallback). Zero device-side install (u2 v3 app_process route).
- **L1 semantic tree + selector engine**: full accessibility-tree parse
  (`resource-id/text/desc/class/checked/clickable/...`) and a GKD-style selector
  language (`id@ text@ desc@ class@ pkg@ [k=v] nth@ parent@ child@ under@ hastext@`)
  with server-side relocation and node-center fallback. Bare coordinates are banned
  by convention and flagged by the linter.
- **L2 base tools**: `ui.*` `app.*` `vision.*` (screenshot/OCR/diff) `shell.*`
  (script mode via base64 pipe) `state.*` (prefs/sqlite) `file.*` (chunked base64
  transfer with MD5) `net.*` (proxy-capable HTTP) `input.*` `events.*`
  (transient toast capture) `ui2.*` (OCR-based structured UI state) `sys.*`
  (capability discovery) `device.*`.
- **L3 harness engine**: declarative `harness.json5` per app — selector step
  sequences, `$param` substitution, pre/postconditions, back_until conditional
  navigation, trust gate (`dry_run` default), stale marking with
  re-distillation self-heal, app-version range checks.
- **Distiller**: bounded-BFS UI exploration with action budget and dangerous-label
  skip, input probing (makes send-buttons appear), heuristic tool proposal
  (LLM-pluggable), dry-run validation of the generated harness.
- **L4 plugin runtime** (dsh-style semantics): registration returns a disposer;
  unload/hot-reload rolls back all of a plugin's registrations in reverse order;
  capability seams (observation × action) with `privilege` (adb/root) and `stealth`
  metadata; `compile+exec` per-generation hot reload that bypasses stale `.pyc`
  caching; broken plugins load as error envelopes without blocking others.
- **Tool pipeline**: pre-hooks → handler (inside the single-device lock) → post-hooks
  → result envelope; built-in root whitelist gate (`allow_risk` escape hatch,
  dangerous-pattern deny list) and result clipping.
- **Exits**: MCP stdio server (protocol `2024-11-05`, envelope semantics, harness
  tools exposed as `<pkg>__<tool>`) and a CLI (`tools/call/run/distill/lint/dump/mcp`).
- **Base examples**: a minimal hand-written Android Settings harness (`launch`/`search`)
  that runs on any device. App packs (plugins, real-world app harnesses) are
  collected separately in the community catalog `awesome-mobile-agent-harness`.
- **Tests**: 25 offline unit tests (rollback, root gate, harness routing, hot reload),
  a live-device MCP smoke test, and a single-command end-to-end acceptance script.

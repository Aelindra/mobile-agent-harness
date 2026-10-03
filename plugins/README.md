# plugins/

Drop-in plugin directory — every `plugin.py` here (single file or `<name>/plugin.py`)
is discovered and hot-reloaded by the runtime at startup.

- **Authoring guide** (runtime contract, disposer/rollback semantics, capability
  seams): see the root [README](../README.md#plugin-development-guide).
- **Community catalog**: ready-made plugins and app harnesses are collected in the
  `awesome-mobile-agent-harness` repository — copy what you need into this directory.
- `private_plugins/` (sibling of this directory) is gitignored by design: put
  root-only or otherwise non-distributable plugins there; they are never shipped
  with this repository.
- `AGENT_PLUGIN_DIRS` (comma-separated) overrides the default search path.

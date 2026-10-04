# mobile-agent-harness

**面向 AI agent 的安卓设备自动化。** MCP server、CLI 与热重载插件运行时，构建在自恢复的 `uiautomator2` 桥之上。无需在设备上安装任何 app。

> English documentation: [README.md](README.md)

本仓只含基座运行时。插件在社区目录：[awesome-mobile-agent-harness-plugin](https://github.com/Aelindra/awesome-mobile-agent-harness-plugin)；面向 AI 的 app 业务知识在 [app-lore](https://github.com/Aelindra/app-lore)，本仓 `knowledge/` 加载器可直接读取该格式。

## 安装

```bash
git clone https://github.com/Aelindra/mobile-agent-harness
cd mobile-agent-harness
pip install -e .          # 加 ".[ocr]" 启用 vision.ocr / ui2.*
```

需要 Python 3.9+，PATH 中有 `adb`，设备已开启 USB 调试。

## 快速开始

1. 指定设备：

```bash
export AGENT_SERIAL_DEFAULT=192.168.1.23:5555    # 或 "emulator-5555"
```

2. 列出工具面（不连设备也可运行）并跑离线测试：

```bash
python -m core.cli tools
python tests/test_runtime.py
```

3. 调用工具：

```bash
python -m core.cli call ui.snapshot
python -m core.cli call ui.click --args '{"selector":"text@^Network & internet$","pkg":"com.android.settings"}'
```

4. 接入任意 MCP 客户端：

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

所有工具返回 `{"ok": bool, "error"?, ...}` 包络。`ok=false` 是业务结果（如 `error_type=selector_not_found`），应据此调整而非重试。

## 工具面

| 分组 | 工具 |
|---|---|
| 观测 | `ui.snapshot` `ui.dump` `ui.find` `ui2.state` `ui2.screen_evidence` `ui2.timeline` `ui2.find_templates` `vision.screenshot` `vision.ocr` `vision.diff` |
| 操作 | `ui.click` `ui.click_handle` `ui.text_handle` `ui.hold_read` `ui.set_text` `ui.scroll` `ui.back` `ui.home` `input.tap` `input.swipe` `input.key` |
| 应用 | `app.launch` `app.current` `app.probe` `app.list` `app.stop` `app.wait_idle` |
| Shell 与数据 | `shell.run` `state.prefs` `state.db` `file.push` `file.pull` `net.http` |
| 事件 | `events.tail` `events.wait` `events.capture` `events.record_start` `events.record_stop` |
| 知识与任务 | `knowledge.list` `knowledge.guide` `ui2.check_states` `ui2.orient` `task.begin` `task.note` `task.end` `template.add` `sys.capabilities` |

`ui.snapshot` 输出带元素句柄（`e0`、`e1`…）的紧凑无障碍树快照；`ui.click_handle` 按句柄操作并带漂移检测。`ui2.state` 将无障碍树与 OCR 融合，覆盖 canvas 自绘界面。`ui2.screen_evidence` 采集可度量的屏幕特征（遮蔽程度、运动量、色彩、布局密度），只出证据不做判读。`app.probe` 返回确定性旁证（包名/版本/方向）；`ui2.timeline` 按时间序采集 N 帧轻量证据；`ui2.orient` 探上下文、按包名路由知识包并求值状态判别式——返回全命中状态或带缺口清单的排序假设。

## 选择器

选择器在实时无障碍树上解析，不使用坐标。

```python
# ui.click 的 selector 示例
"id@d98 && pkg@com.example.app"       # resource-id，短名按 pkg@ 补全
"text@^Settings$ && pkg@com.example.app"
"class@android.widget.EditText && [clickable=true]"
"desc@^Search$"                        # content-desc 正则
```

## Harness 与插件

harness 文件按"选择器步骤 + 前/后置条件"声明某 app 的操作流程；`distill` 通过探索自动起草，`lint` 检查选择器质量，后置条件失败会把工具标记为 stale 待重蒸馏。内置示例见 `harnesses/com.android.settings/harness.json5`。

插件放入 `plugins/` 即在加载时注册工具或能力提供者；注册可回滚，文件变更热重载。插件开发详见下文，知识包格式见 `knowledge/README.md`。

## 配置

对模型先验不可靠的长尾应用，建议让 agent 加载 [app-lore](https://github.com/Aelindra/app-lore) 作为业务上下文——社区维护的应用结构综述目录，采用开放的 Agent Skills 格式，复杂应用按功能模块拆分。移动场景偏私域、在训练数据中覆盖稀薄，操作不熟悉应用前加载对应综述收益明显。本仓的 `knowledge/` 目录仍是运行时层（知识包、图标模板、状态判别式）。

## 配置

| 变量 | 说明 | 示例 |
|---|---|---|
| `AGENT_SERIAL_DEFAULT` | 无 `--serial`/`ANDROID_SERIAL` 时使用的设备 serial | `192.168.1.23:5555` |
| `AGENT_SERIAL_USB` | USB serial 兜底 | `0123456789abcdef` |
| `MAH_FRAME_STREAM` | `1` 启用 H.264 帧流供观测工具使用 | `1` |
| `MAH_VISION_MODE` | 识图增强：`off` / `client` / `endpoint` | `client` |
| `MAH_VLM_URL` / `MAH_VLM_MODEL` | 外部视觉端点（OpenAI 兼容） | `http://127.0.0.1:11434/v1/chat/completions` |

## 与同类项目的差异

- **mobile-mcp / agent-device**：它们是通用设备工具集；本项目在其上增加传输之上的层次——以文件形式沉淀的 app 知识（harness）、带能力缝的热重载插件运行时、显式的 adb/root 权限模型。
- **droidrun**：droidrun 需要设备端无障碍 app；本项目通过 adb 实现零安装。
- root 支持是声明式的：设备已有 `su` 时 shell 缝自动出现 root provider，并受命令白名单约束。本项目不提供 root。

## 已知局限

聊天、银行、电商类 app 存在主动反自动化风控，adb 通道在其上不可靠。适用场景为开发、测试与自有设备工作流。

## Contributing

插件贡献到 [awesome-mobile-agent-harness-plugin](https://github.com/Aelindra/awesome-mobile-agent-harness-plugin)；app 业务知识贡献到 [app-lore](https://github.com/Aelindra/app-lore)——格式与规则见各自贡献指南。

## License

MIT。

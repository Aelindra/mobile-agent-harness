# knowledge/ — 知识包（knowledge pack）

**一份知识，两个消费面**：`guide.md` 是上下文知识（LLM 读，经 `knowledge.guide`
工具暴露给任意 MCP 客户端——不依赖客户端的 skill 机制）；`templates/` 与
`states` 是运行时知识（core 解释：加载后 `ui2.find_templates` 自动搜索包内
图集、`ui2.check_states` 按包求值状态判别式）。

## 目录规范

```
knowledge/<pack>/
  pack.json5     # 必需：{name, version, game?, guide?, atlas?, states?}
  guide.md       # 可选：业务流程/使用时机/注意事项（上下文面）
  templates/     # 可选：图集（与 templates/ 同构：<game>/manifest.json + *.png）
```

`pack.json5` 示例（完整可运行样例见 `_demo_pack/`，`_` 前缀不参与自动加载，
拷贝去掉前缀即生效）：

```json5
{
  name: "my-pack",
  version: "0.1",
  game: "com.example.app",       // 可选：限定目标 app
  guide: "guide.md",             // 可选：上下文面
  atlas: "templates",            // 可选：运行时图集
  states: {                      // 可选：状态判别式（全满足才 ok；缺证据单列）
    grayed: { all: ["overlay=gray", "motion!=high", "hud=absent"] }
  }
}
```

## 运行时语义

加载=注册即副作用（图集进搜索路径、状态进判别表，进程内记账）；卸载=逆序回滚，
与插件运行时同一套契约。规则：states 判别式只对 `ui2.screen_evidence` 的证据
字段求值（overlay/motion/hud/color.*/filter.*），表达式形如 `key(=|!=|>|<)value`；
`ok=true` 才是结论，`insufficient_evidence=true` 表示证据不足（继续采集，不是否定）。

游戏向知识包归社区目录（awesome-mobile-agent-harness）收录；本目录除本 README
与 `_` 前缀样例外已 gitignore。

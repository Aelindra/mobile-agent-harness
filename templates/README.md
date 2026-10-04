# templates/ — 声明式图标图集（atlas）

"图标 → 语义"的声明层：把游戏 HUD 图标裁成模板 PNG + 在 `manifest.json` 里声明
名称与用途，之后 `ui2.find_templates` 即可在任何屏幕上识别它们的位置与语义
——**图标在屏即命中，无需 hover/长按**。

- 工作流：`vision.screenshot` → `template.add {game, name, label, desc, box}` → 永久可匹配
- 目录结构：`<game>/manifest.json` + `<game>/<name>.png`
- 图集内容可在独立目录发布共享；
  本目录除本 README 外已 gitignore（各人自己积累）

# -*- coding: utf-8 -*-
"""L2 Base Tools：与 app 无关的原子工具层（MCP 风格 schema）。

命名空间：ui.* / app.* / vision.* / shell.* / state.* / net.* / harness.*
每个工具 = {name, description, schema(JSON Schema), handler(bridge, engine, args)}。
Registry.call 统一做 transcript 记录。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from typing import Optional

from . import selectors as S
from .bridge import Bridge
from .selectors import SelectorError, SelectorNotFound

TOOLS = {}


def tool(name, description, schema=None):
    def deco(fn):
        TOOLS[name] = {"name": name, "description": description,
                       "inputSchema": schema or {"type": "object"}, "handler": fn}
        return fn
    return deco


def _node_summary(n) -> dict:
    return {"path": n.path, "package": n.package, "class": n.cls, "id": n.res_id,
            "text": n.text, "desc": n.desc, "bounds": list(n.bounds),
            "center": list(n.center), "clickable": n.clickable, "checked": n.checked,
            "checkable": n.checkable, "scrollable": n.scrollable, "editable": n.editable,
            "long_clickable": n.long_clickable}


# ---------------- ui.* ----------------

@tool("ui.dump", "全量节点树 JSON（实时桥数据）：class/id/text/desc/bounds/checked/clickable 等语义",
      {"type": "object", "properties": {
          "max_nodes": {"type": "integer", "default": 1500},
          "save_to": {"type": "string", "description": "可选，完整 JSON 落盘路径"}}})
def ui_dump(b: Bridge, e, a):
    root = b.dump()
    data = root.to_dict(max_nodes=a.get("max_nodes", 1500))
    if a.get("save_to"):
        with open(a["save_to"], "w", encoding="utf-8") as f:
            json.dump(root.to_dict(max_nodes=100000), f, ensure_ascii=False, indent=1)
        data["saved_to"] = a["save_to"]
    return data


@tool("ui.find", "语义查找节点（GKD 风格选择器），返回节点句柄摘要列表",
      {"type": "object", "properties": {
          "selector": {"type": "string"}, "pkg": {"type": "string"},
          "timeout": {"type": "number", "default": 0}}, "required": ["selector"]})
def ui_find(b: Bridge, e, a):
    nodes = b.find(a["selector"], a.get("pkg"), timeout=a.get("timeout", 0))
    return {"count": len(nodes), "nodes": [_node_summary(n) for n in nodes[:30]]}


@tool("ui.get_text", "读取选择器命中节点的文本（只读不点击）",
      {"type": "object", "properties": {
          "selector": {"type": "string"}, "pkg": {"type": "string"},
          "limit": {"type": "integer", "default": 10, "description": "最多返回节点数"}},
       "required": ["selector"]})
def ui_get_text(b: Bridge, e, a):
    nodes = b.find(a["selector"], a.get("pkg"), timeout=a.get("timeout", 0))
    items = [{"text": n.text or "", "desc": n.desc or "", "id": n.res_id}
             for n in nodes[:max(1, int(a.get("limit", 10)))]]
    first = items[0] if items else {}
    return {"ok": True, "count": len(nodes), "items": items,
            "text": first.get("text") or first.get("desc") or ""}


@tool("ui.wait_for", "轮询等待选择器出现/消失（等弹窗、加载完成、页面跳转），超时返回 ok=false",
      {"type": "object", "properties": {
          "selector": {"type": "string", "description": "GKD 选择器"},
          "pkg": {"type": "string", "description": "包名（id 短名补全用）"},
          "gone": {"type": "boolean", "default": False, "description": "true=等待其消失"},
          "timeout": {"type": "number", "default": 10, "description": "最长等待秒数"}},
       "required": ["selector"]})
def ui_wait_for(b: Bridge, e, a):
    sel = a["selector"]
    pkg = a.get("pkg")
    if pkg is None:
        try:
            pkg = b.current_app()[0] or None
        except Exception:
            pkg = None
    gone = bool(a.get("gone"))
    timeout = float(a.get("timeout", 10))
    deadline = time.time() + timeout
    while True:
        hits = S.find_nodes(b.dump(), sel, default_pkg=pkg)
        if bool(hits) != gone:
            return {"ok": True, "condition_met": True, "present": bool(hits),
                    "node": _node_summary(hits[0]) if hits else None}
        if time.time() >= deadline:
            return {"ok": False, "condition_met": False, "present": bool(hits),
                    "error": f"等待{'消失' if gone else '出现'}超时({timeout}s): {sel}"}
        time.sleep(0.5)


@tool("ui.click", "节点级点击（选择器解析，非裸坐标）",
      {"type": "object", "properties": {
          "selector": {"type": "string"}, "pkg": {"type": "string"},
          "nth": {"type": "integer", "default": 0},
          "long_click": {"type": "boolean", "default": False},
          "timeout": {"type": "number", "default": 3,
                      "description": "uia 选择器服务端等待秒数"}},
       "required": ["selector"]})
def ui_click(b: Bridge, e, a):
    return b.click(a["selector"], a.get("pkg"), nth=a.get("nth", 0),
                   long_click=a.get("long_click", False), timeout=a.get("timeout", 3))


@tool("ui.long_click", "节点级长按",
      {"type": "object", "properties": {"selector": {"type": "string"}, "pkg": {"type": "string"}},
       "required": ["selector"]})
def ui_long_click(b: Bridge, e, a):
    return b.click(a["selector"], a.get("pkg"), long_click=True)


@tool("ui.set_text", "精确输入到指定组件（优先 ACTION_SET_TEXT；无 selector 时输入到聚焦框）",
      {"type": "object", "properties": {
          "text": {"type": "string"}, "selector": {"type": "string"},
          "pkg": {"type": "string"}, "clear": {"type": "boolean", "default": True}},
       "required": ["text"]})
def ui_set_text(b: Bridge, e, a):
    return b.set_text(a["text"], a.get("selector"), a.get("pkg"), clear=a.get("clear", True))


@tool("ui.scroll", "滚动（down=查看下方内容，手指上滑）",
      {"type": "object", "properties": {
          "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
          "container": {"type": "string", "description": "scrollable 容器选择器，缺省自动找"},
          "pkg": {"type": "string"}}})
def ui_scroll(b: Bridge, e, a):
    return b.scroll(a.get("direction", "down"), a.get("container"), a.get("pkg"))


@tool("ui.back", "系统返回键", {"type": "object"})
def ui_back(b: Bridge, e, a):
    b.back()
    return {"ok": True}


@tool("ui.home", "回到桌面", {"type": "object"})
def ui_home(b: Bridge, e, a):
    b.home()
    return {"ok": True}


# ---------------- app.* ----------------

@tool("app.launch", "启动应用并等待前台空闲", {"type": "object", "properties": {"pkg": {"type": "string"}}, "required": ["pkg"]})
def app_launch(b: Bridge, e, a):
    b.launch(a["pkg"], timeout=a.get("timeout", 15))
    return {"ok": True, "current": b.current_app()}


@tool("app.current", "当前前台应用", {"type": "object"})
def app_current(b: Bridge, e, a):
    pkg, act = b.current_app()
    return {"package": pkg, "activity": act}


@tool("app.wait_idle", "等待应用前台且稳定", {"type": "object", "properties": {
    "pkg": {"type": "string"}, "timeout": {"type": "number", "default": 15}}, "required": ["pkg"]})
def app_wait_idle(b: Bridge, e, a):
    b.wait_idle(a["pkg"], a.get("timeout", 15))
    return {"ok": True}


@tool("app.stop", "停止应用", {"type": "object", "properties": {"pkg": {"type": "string"}}, "required": ["pkg"]})
def app_stop(b: Bridge, e, a):
    b.stop(a["pkg"])
    return {"ok": True}


@tool("app.list", "列出已安装应用包名（third_party=true 仅用户应用）",
      {"type": "object", "properties": {
          "third_party": {"type": "boolean", "default": True},
          "filter": {"type": "string", "description": "包名子串过滤（大小写不敏感）"}}})
def app_list(b: Bridge, e, a):
    out = b.shell("pm list packages -3" if a.get("third_party", True) else "pm list packages",
                  timeout=30)
    pkgs = [ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("package:")]
    f = (a.get("filter") or "").lower()
    if f:
        pkgs = [p for p in pkgs if f in p.lower()]
    return {"ok": True, "count": len(pkgs), "packages": sorted(pkgs)}


# ---------------- vision.* ----------------

_LAST_SHOT = {"path": None}  # 供 vision.diff 缺省基线


@tool("vision.screenshot", "截屏存盘（视觉兜底/证据）；路径会记住，作为 vision.diff 缺省基线",
      {"type": "object", "properties": {"path": {"type": "string"}}})
def vision_screenshot(b: Bridge, e, a):
    from . import frames
    path = a.get("path") or os.path.join(tempfile.gettempdir(), f"shot_{int(time.time())}.png")
    t0 = time.time()
    frames.grab(b, path)
    _LAST_SHOT["path"] = path
    return {"ok": True, "path": path, "ms": int((time.time() - t0) * 1000)}


@tool("vision.diff", "界面变化检测：当前截图 vs 基线的像素差异（变化占比+差异框）；ocr=true 附文本增删",
      {"type": "object", "properties": {
          "baseline": {"type": "string", "description": "基线图路径；缺省用最近一次 vision.screenshot"},
          "current": {"type": "string", "description": "对比图路径；缺省当场截图"},
          "ocr": {"type": "boolean", "default": False,
                  "description": "true 时对两图各跑 OCR，返回文本集合差异"},
          "threshold": {"type": "integer", "default": 8,
                        "description": "像素差灰度阈值(0-255)，低于视为噪声"}}})
def vision_diff(b: Bridge, e, a):
    from PIL import Image, ImageChops
    base_p = a.get("baseline") or _LAST_SHOT["path"]
    if not base_p or not os.path.exists(base_p):
        return {"ok": False, "error": "无可用基线：先调用 vision.screenshot 或传 baseline 路径"}
    cur_p = a.get("current")
    if not cur_p:
        cur_p = os.path.join(tempfile.gettempdir(), f"diff_cur_{int(time.time()*1000)}.png")
        b.screenshot(cur_p)
    if not os.path.exists(cur_p):
        return {"ok": False, "error": f"对比图不存在: {cur_p}"}
    ia = Image.open(base_p).convert("RGB")
    ib = Image.open(cur_p).convert("RGB")
    if ia.size != ib.size:
        return {"ok": False, "error": f"截图尺寸不同 {ia.size} vs {ib.size}（分辨率变了？）",
                "baseline": base_p, "current": cur_p}
    diff = ImageChops.difference(ia, ib)
    bbox = diff.getbbox()
    thr = max(0, min(255, int(a.get("threshold", 8))))
    hist = diff.convert("L").histogram()
    total = sum(hist) or 1
    changed = sum(hist[thr + 1:]) / total
    out = {"ok": True, "identical": bbox is None, "changed_ratio": round(changed, 5),
           "diff_bbox": list(bbox) if bbox else None,
           "baseline": base_p, "current": cur_p}
    if a.get("ocr"):
        def _texts(p):
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                return None  # 与 vision.ocr 相同的明确报错语义
            import numpy as np
            res, _ = RapidOCR()(np.asarray(Image.open(p).convert("RGB")))
            return {x[1].strip() for x in (res or []) if x[1].strip()}
        ta, tb = _texts(base_p), _texts(cur_p)
        if ta is None or tb is None:
            out["ocr_error"] = "rapidocr_onnxruntime 未安装：pip install rapidocr-onnxruntime"
        else:
            out["ocr_gone"] = sorted(ta - tb)
            out["ocr_added"] = sorted(tb - ta)
    return out


@tool("vision.ocr", "OCR（懒加载 rapidocr_onnxruntime；未安装则明确报错）；path=本地图片离线 OCR，缺省截屏",
      {"type": "object", "properties": {"region": {"type": "array", "items": {"type": "integer"}},
                                       "path": {"type": "string", "description": "本地图片路径（离线模式，无需设备连接）"}}})
def vision_ocr(b: Bridge, e, a):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return {"ok": False, "error": "rapidocr_onnxruntime 未安装：pip install rapidocr-onnxruntime"}
    import numpy as np
    if a.get("path"):
        from PIL import Image
        try:
            img = Image.open(a["path"]).convert("RGB")
        except Exception as ex:  # noqa: BLE001
            return {"ok": False, "error": "打开图片失败: %r" % ex}
    else:
        img = b.d.screenshot()
    if a.get("region"):
        l, t, r, bt = a["region"]
        img = img.crop((l, t, r, bt))
    result, _ = RapidOCR()(np.asarray(img))
    texts = [{"text": x[1], "conf": float(x[2]), "box": x[0]} for x in (result or [])]
    return {"ok": True, "lines": texts, "source": a.get("path", "screen")}



# ---------------- vision.vlm（本地视觉小模型，可选层） ----------------

@tool("vision.vlm", "本地视觉小模型问图（crop-and-ask）：缺省整屏，传 box 只裁剪该区域提问"
      "（推荐：小图快且准）。需本地 OpenAI 兼容视觉端点：设 MAH_VLM_URL 与 MAH_VLM_MODEL"
      "（如 ollama：MAH_VLM_URL=http://127.0.0.1:11434/v1/chat/completions，"
      "MAH_VLM_MODEL=qwen2.5vl:3b）。定位：图集外未知图标的语义兜底层",
      {"type": "object", "properties": {
          "prompt": {"type": "string", "description": "问题；缺省=描述该界面元素及用途"},
          "box": {"type": "array", "items": {"type": "integer"},
                  "description": "[l,t,r,b] 只裁剪该区域"},
          "from": {"type": "string", "description": "截图路径，缺省最近一次 vision.screenshot"},
          "timeout": {"type": "number", "default": 60}}})
def vision_vlm(b: Bridge, e, a):
    url = os.environ.get("MAH_VLM_URL")
    model = os.environ.get("MAH_VLM_MODEL")
    if not url or not model:
        return {"ok": False, "error": "本地视觉模型未配置：ollama pull qwen2.5vl:3b 后设 "
                                      "MAH_VLM_URL=http://127.0.0.1:11434/v1/chat/completions "
                                      "MAH_VLM_MODEL=qwen2.5vl:3b（任意 OpenAI 兼容端点均可）"}
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests 未安装：pip install requests"}
    src = a.get("from") or _LAST_SHOT.get("path")
    if not src or not os.path.exists(src):
        return {"ok": False, "error": "无可用截图：先 vision.screenshot 或传 from 路径"}
    from PIL import Image
    img = Image.open(src).convert("RGB")
    crop_box = None
    if a.get("box"):
        l, t, r, bt = [int(v) for v in a["box"]]
        img = img.crop((l, t, r, bt))
        crop_box = [l, t, r, bt]
    import io
    import time as _t
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {"model": model, "stream": False,
               "messages": [{"role": "user", "content": [
                   {"type": "text",
                    "text": a.get("prompt") or "这个界面元素是什么？有什么用途？一句话回答，先给名称。"},
                   {"type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + b64}}]}]}
    t0 = _t.time()
    try:
        resp = requests.post(url, json=payload, timeout=float(a.get("timeout", 60)))
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}",
                "hint": "端点未就绪或模型不支持图片输入"}
    return {"ok": True, "text": text.strip()[:500], "model": model,
            "crop_box": crop_box, "ms": int((_t.time() - t0) * 1000)}



# ---------------- vision.ask（通用识图增强：配置化策略入口） ----------------

@tool("vision.ask", "通用识图增强入口（增强层，非主链路）。MAH_VISION_MODE 决定策略："
      "client=截图以 MCP image 内容块返回，由具备视觉能力的客户端模型自己判读（零额外部署）；"
      "endpoint=转发问题到外部部署的视觉端点（MAH_VLM_URL/MAH_VLM_MODEL，同 vision.vlm）；"
      "off=关闭（默认，仅用 ui.snapshot/ui2.*/图集等确定性证据层）",
      {"type": "object", "properties": {
          "prompt": {"type": "string", "description": "问题（endpoint 模式用）"},
          "box": {"type": "array", "items": {"type": "integer"},
                  "description": "[l,t,r,b] 只取该区域"},
          "from": {"type": "string", "description": "截图路径，缺省最近一次 vision.screenshot"}},
       })
def vision_ask(b: Bridge, e, a):
    mode = os.environ.get("MAH_VISION_MODE", "off")
    from .tools import _LAST_SHOT  # noqa: PLC0415
    src = a.get("from") or _LAST_SHOT.get("path")
    if not src or not os.path.exists(src):
        return {"ok": False, "error": "无可用截图：先 vision.screenshot 或传 from 路径"}
    from PIL import Image
    img = Image.open(src).convert("RGB")
    if a.get("box"):
        l, t, r, bt = [int(v) for v in a["box"]]
        img = img.crop((l, t, r, bt))
    if mode == "client":
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()
        note = a.get("prompt") or "判读这张屏幕截图：当前界面状态、关键元素及其用途"
        return {"ok": True, "mode": "client",
                "_mcp_content": [{"type": "text", "text": note},
                                 {"type": "image", "data": img_b64,
                                  "mimeType": "image/png"}]}
    if mode == "endpoint":
        if not os.environ.get("MAH_VLM_URL"):
            return {"ok": False, "error": "endpoint 模式需配置 MAH_VLM_URL/MAH_VLM_MODEL"}
        return vision_vlm(b, e, {**a, "prompt": a.get("prompt") or
                                  "判读这张屏幕截图：当前界面状态、关键元素及其用途"})
    return {"ok": False, "mode": mode,
            "hint": "MAH_VISION_MODE=off。可选：client（客户端自带视觉）/ endpoint（外部视觉端点）。"
                    "确定性证据层（ui.snapshot/ui2.state/ui2.screen_evidence/图集）始终可用"}


# ---------------- shell.* / state.* ----------------

_RISKY = re.compile(r"\b(rm\s+-rf|mkfs|dd\s+if=|flash|reboot)\b")


@tool("shell.run", "执行 shell（root=true 走 su，设备需已 root）；风险命令需 allow_risk。"
      "script=多行脚本模式：base64 推到手机 /data/local/tmp 执行（规避嵌套引号/管道符转义地狱），与 cmd 二选一",
      {"type": "object", "properties": {
          "cmd": {"type": "string"},
          "script": {"type": "string", "description": "多行 shell 脚本（推荐：可含管道/引号）"},
          "root": {"type": "boolean", "default": False},
          "allow_risk": {"type": "boolean", "default": False}}})
def shell_run(b: Bridge, e, a):
    if a.get("script"):
        body = a["script"]
        if _RISKY.search(body) and not a.get("allow_risk"):
            return {"ok": False, "error": "脚本命中风险名单且未给 allow_risk"}
        b64 = base64.b64encode(body.encode("utf-8")).decode()
        remote = "/data/local/tmp/_harness_script_%d.sh" % int(time.time() * 1000 % 100000)
        push_out = b.shell("echo %s | base64 -d > %s" % (b64, remote), root=a.get("root", False))
        if "not found" in push_out or "Permission denied" in push_out:
            return {"ok": False, "error": "脚本落盘失败: %s" % push_out[:200]}
        out = b.shell("sh %s; rm -f %s" % (remote, remote), root=a.get("root", False))
        return {"ok": True, "output": out[:16000], "mode": "script"}
    cmd = a.get("cmd", "")
    if not cmd:
        return {"ok": False, "error": "cmd 与 script 至少给一个"}
    if _RISKY.search(cmd) and not a.get("allow_risk"):
        return {"ok": False, "error": f"命令命中风险名单且未给 allow_risk: {cmd}"}
    return {"ok": True, "output": b.shell(cmd, root=a.get("root", False))[:8000]}


@tool("state.prefs", "root 读应用 shared_prefs XML 并解析",
      {"type": "object", "properties": {"pkg": {"type": "string"}, "file": {"type": "string"}}, "required": ["pkg", "file"]})
def state_prefs(b: Bridge, e, a):
    import xml.etree.ElementTree as ET
    xml = b.shell(f"cat /data/data/{a['pkg']}/shared_prefs/{a['file']}.xml", root=True)
    if "No such file" in xml or not xml.strip():
        return {"ok": False, "error": xml.strip()[:200] or "空文件"}
    root = ET.fromstring(xml)
    out = {}
    for el in root.iter():
        if el.tag in ("int", "long", "boolean", "string", "float") and el.get("name"):
            out[el.get("name")] = el.get("value")
    return {"ok": True, "prefs": out}


@tool("state.db", "root 拉取应用 sqlite 库到 PC 临时目录并执行只读 SQL",
      {"type": "object", "properties": {
          "pkg": {"type": "string"}, "db": {"type": "string"}, "sql": {"type": "string"}},
       "required": ["pkg", "db"]})
def state_db(b: Bridge, e, a):
    remote = a["db"] if a["db"].startswith("/") else f"/data/data/{a['pkg']}/databases/{a['db']}"
    local = os.path.join(tempfile.gettempdir(), f"hdb_{int(time.time()*1000)}_{os.path.basename(remote)}")
    data = b.shell(f"cat '{remote}'", root=True, timeout=60)
    # shell 输出可能损坏二进制（\r\n 转换），改用 exec-out 风格：走 base64 稳妥
    if data and not data.startswith("cat:"):
        b64 = b.shell(f"base64 '{remote}'", root=True, timeout=120)
        import base64
        raw = base64.b64decode("".join(b64.split()))
        with open(local, "wb") as f:
            f.write(raw)
    else:
        return {"ok": False, "error": data.strip()[:200]}
    con = sqlite3.connect(local)
    con.row_factory = sqlite3.Row
    try:
        sql = a.get("sql") or "SELECT name FROM sqlite_master WHERE type='table'"
        rows = [dict(r) for r in con.execute(sql).fetchmany(200)]
        return {"ok": True, "rows": rows, "local_copy": local}
    finally:
        con.close()


# ---------------- file.* ----------------

@tool("file.push", "PC→手机传文件：base64 分块管道（WiFi adb push 二进制不可靠的替代），MD5 校验",
      {"type": "object", "properties": {
          "local": {"type": "string", "description": "PC 侧源文件路径"},
          "remote": {"type": "string", "description": "手机侧目标绝对路径"},
          "root": {"type": "boolean", "default": False,
                   "description": "su 写入；仅 /data/local/tmp、/sdcard 免 allow_risk，其余路径需 allow_risk=true"},
          "chunk": {"type": "integer", "default": 32768,
                    "description": "base64 分块字节（上限受单条 shell 命令 128KB 限制）"}},
       "required": ["local", "remote"]})
def file_push(b: Bridge, e, a):
    local, remote = a["local"], a["remote"]
    root = bool(a.get("root"))
    chunk = max(1024, int(a.get("chunk", 32768)))
    with open(local, "rb") as f:
        data = f.read()
    md5 = hashlib.md5(data).hexdigest()
    b64 = base64.b64encode(data).decode()
    b.shell(f"rm -f '{remote}'", root=root)
    chunks = 0
    for i in range(0, len(b64), chunk):
        b.shell(f"echo {b64[i:i + chunk]} | base64 -d >> '{remote}'", root=root, timeout=60)
        chunks += 1
    out = b.shell(f"md5sum '{remote}'", root=root).strip()
    ok = md5 in out
    return {"ok": ok, "bytes": len(data), "chunks": chunks,
            "md5_local": md5, "md5_remote": out.split()[0] if out else "",
            **({} if ok else {"error": f"MD5 不一致，远端: {out[:80]}"})}


@tool("file.pull", "手机→PC 传文件：base64 读出解码（绕开 adb pull 的不可靠/权限问题），MD5 校验",
      {"type": "object", "properties": {
          "remote": {"type": "string", "description": "手机侧源文件绝对路径"},
          "local": {"type": "string", "description": "PC 侧目标路径"},
          "root": {"type": "boolean", "default": False,
                   "description": "源在 /data/data 等受限目录时 true（su 读）"},
          "max_mb": {"type": "number", "default": 50, "description": "超过则拒绝（大文件请 shell.run 分卷）"}},
       "required": ["remote", "local"]})
def file_pull(b: Bridge, e, a):
    remote, local = a["remote"], a["local"]
    root = bool(a.get("root"))
    max_mb = float(a.get("max_mb", 50))
    st = b.shell(f"stat -c %s '{remote}'", root=root).strip()
    if not st.isdigit():
        return {"ok": False, "error": f"无法读取文件大小（不存在或无权限）: {st[:120]}"}
    size = int(st)
    if size > max_mb * 1024 * 1024:
        return {"ok": False, "error": f"文件 {size}B 超过上限 {max_mb}MB"}
    b64 = b.shell(f"base64 '{remote}'", root=root, timeout=300)
    if not b64.strip():
        return {"ok": False, "error": "base64 输出为空"}
    raw = base64.b64decode("".join(b64.split()))
    os.makedirs(os.path.dirname(os.path.abspath(local)) or ".", exist_ok=True)
    with open(local, "wb") as f:
        f.write(raw)
    md5_remote = b.shell(f"md5sum '{remote}'", root=root).strip().split()
    md5_local = hashlib.md5(raw).hexdigest()
    ok = not md5_remote or md5_local == md5_remote[0]
    return {"ok": ok, "bytes": len(raw), "md5_local": md5_local,
            "md5_remote": md5_remote[0] if md5_remote else "",
            **({} if ok else {"error": "MD5 不一致（传输损坏）"})}


# ---------------- net.* ----------------

@tool("net.http", "发 HTTP 请求（proxy 参数可接 mitmproxy 等审计代理；verify 默认关闭以适配自签代理证书）",
      {"type": "object", "properties": {
          "url": {"type": "string"},
          "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "HEAD"], "default": "GET"},
          "headers": {"type": "object", "additionalProperties": {"type": "string"}},
          "body": {"type": "string", "description": "请求体（文本）"},
          "timeout": {"type": "number", "default": 15},
          "proxy": {"type": "string", "default": "http://127.0.0.1:8080",
                    "description": "置空字符串则直连"}},
       "required": ["url"]})
def net_http(b: Bridge, e, a):
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests 未安装：pip install requests"}
    proxy = a.get("proxy")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.request(a.get("method", "GET"), a["url"],
                             headers=a.get("headers") or None,
                             data=(a["body"].encode("utf-8") if isinstance(a.get("body"), str) else None),
                             proxies=proxies, timeout=float(a.get("timeout", 15)),
                             verify=False, allow_redirects=True)
    except Exception as ex:  # noqa: BLE001
        return {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    body = r.text[:8000]
    return {"ok": True, "status": r.status_code, "url": r.url,
            "headers": dict(list(r.headers.items())[:30]), "body": body,
            "body_truncated": len(r.text) > 8000}


# ---------------- device setup ----------------

@tool("device.wake_unlock", "亮屏 + 按需解除非安全锁屏 + 清理残留弹窗（agent 自身 setup，非人工干预）", {"type": "object"})
def device_wake(b: Bridge, e, a):
    b.shell("input keyevent KEYCODE_WAKEUP", root=True)
    time.sleep(0.8)
    # 仅在焦点窗口为锁屏/状态栏类时才按 MENU（否则会误触 app 内菜单）
    out = b.shell("dumpsys window | grep mCurrentFocus")
    focus = out.strip()
    lockish = any(k in focus for k in ("NotificationShade", "StatusBar", "Keyguard"))
    if lockish or not focus:
        b.shell("input keyevent 82")
        time.sleep(0.8)
    # 清理可能残留的 PopupWindow（如误开的应用内菜单）
    if "PopupWindow" in focus:
        b.back()
        time.sleep(0.6)
    return {"ok": True, "focus": focus[:120], "menu_pressed": bool(lockish or not focus)}


# ---------------- harness.* ----------------

@tool("harness.list", "列出已加载的 harness 与工具", {"type": "object"})
def harness_list(b: Bridge, e, a):
    if e is None:
        return {"ok": False, "error": "harness engine 未挂载"}
    return {"ok": True, "harnesses": e.list()}


@tool("harness.check_versions", "读取设备上各 harness 目标 app 的 versionName，与 versionRange(>=/<=/* /通配) 比对；不匹配 → stale 需重蒸馏",
      {"type": "object"})
def harness_check_versions(b: Bridge, e, a):
    import fnmatch
    if e is None:
        return {"ok": False, "error": "harness engine 未挂载"}

    def vtuple(v):
        parts = []
        for seg in v.split("."):
            m = re.match(r"\d+", seg)
            parts.append(int(m.group()) if m else 0)
        return tuple(parts)

    def vmatch(ver, vr):
        vr = vr.strip()
        if vr in ("*", ""):
            return True
        for op, cmp in ((">=", lambda a, b: a >= b), ("<=", lambda a, b: a <= b),
                        (">", lambda a, b: a > b), ("<", lambda a, b: a < b)):
            if vr.startswith(op):
                return cmp(vtuple(ver), vtuple(vr[len(op):].strip()))
        return fnmatch.fnmatch(ver, vr)

    out = []
    for h in e.list():
        pkg = h.get("package")
        if not pkg:
            out.append(h)
            continue
        ver = ""
        try:
            m = re.search(r"versionName=([\w.\-]+)", b.shell(f"dumpsys package {pkg} | grep versionName"))
            ver = m.group(1) if m else ""
        except Exception:
            pass
        vr = h.get("versionRange", "*")
        matched = bool(ver) and vmatch(ver, vr)
        out.append({"package": pkg, "installed": ver or None, "versionRange": vr,
                    "match": matched,
                    "stale": (not matched) if ver else None,
                    "note": None if ver else "无法读取设备版本（app 未安装或设备离线）"})
    return {"ok": True, "harnesses": out}


@tool("harness.run", "运行某 harness 工具（steps 由语义选择器组成）",
      {"type": "object", "properties": {
          "pkg": {"type": "string"}, "tool": {"type": "string"},
          "params": {"type": "object"}, "dry_run": {"type": "boolean", "default": False},
          "allow_untrusted": {"type": "boolean", "default": False}},
       "required": ["pkg", "tool"]})
def harness_run(b: Bridge, e, a):
    if e is None:
        return {"ok": False, "error": "harness engine 未挂载"}
    return e.run(a["pkg"], a["tool"], a.get("params") or {},
                 dry_run=a.get("dry_run", False), allow_untrusted=a.get("allow_untrusted", False))


# 统一注册表视图在 core/registry.py（本文件只保留工具定义与 handler）。

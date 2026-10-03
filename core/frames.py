# -*- coding: utf-8 -*-
"""screenrecord H.264 帧流：推流解码后缓存最新帧，供观测工具取帧。

MAH_FRAME_STREAM=1 启用；帧龄超 2s 或未启用时回退单帧截图。
screenrecord 每设备单实例，--time-limit 到期自动重启。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional, Tuple

_streams: Dict[str, "_FrameStream"] = {}
_mu = threading.Lock()

STREAM_TTL = 175          # screenrecord --time-limit（上限 180，留重启余量）
STALE_MS = 2000


class _FrameStream:
    def __init__(self, serial: str):
        self.serial = serial
        self._mu = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._frame = None            # 最新帧 (numpy RGB)
        self._ts = 0.0                # 帧到达时刻
        self._stop = threading.Event()
        self._th: Optional[threading.Thread] = None
        self._gen = 0
        self.start()

    # ---- 生命周期 ----
    def start(self):
        self._gen += 1
        self._th = threading.Thread(target=self._loop, args=(self._gen,), daemon=True)
        self._th.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass

    def fresh(self) -> bool:
        return (time.time() - self._ts) * 1000 < STALE_MS

    # ---- 推流循环 ----
    def _loop(self, gen: int):
        while not self._stop.is_set() and gen == self._gen:
            try:
                cmd = ["adb", "-s", self.serial, "exec-out",
                       f"screenrecord --output-format=h264 --time-limit {STREAM_TTL} -"]
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL)
                import av
                c = av.open(self._proc.stdout, format="h264")
                for frame in c.decode(video=0):
                    if self._stop.is_set() or gen != self._gen:
                        break
                    self._frame = frame.to_ndarray(format="rgb24")
                    self._ts = time.time()
                c.close()
            except Exception:
                pass
            finally:
                if self._proc:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            if not self._stop.is_set() and gen == self._gen:
                time.sleep(0.6)       # 重启空档


def enable() -> bool:
    return os.environ.get("MAH_FRAME_STREAM", "") == "1"


def get(serial: str) -> Optional["_FrameStream"]:
    if not enable():
        return None
    with _mu:
        fs = _streams.get(serial)
        if fs is None:
            try:
                fs = _FrameStream(serial)
                _streams[serial] = fs
            except Exception:
                return None
        return fs


def grab(br, path: Optional[str] = None) -> Tuple[object, Optional[str]]:
    """统一取帧：帧流新鲜→读缓存；否则回退设备截图。返回 (PIL.Image, path)。"""
    from PIL import Image
    fs = None
    try:
        serial = getattr(br, "serial", None)
        fs = get(serial) if serial else None
    except Exception:
        fs = None
    if fs is not None and fs.fresh() and fs._frame is not None:
        with fs._mu:
            img = Image.fromarray(fs._frame.copy())
        if path:
            _ensure_dir(path)
            img.save(path)
        return img, path
    if not path:
        p = _tmp_path()
        br.screenshot(p)
        return Image.open(p).convert("RGB"), p
    _ensure_dir(path)
    br.screenshot(path)
    return Image.open(path).convert("RGB"), path


def _tmp_path() -> str:
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"mah_frame_{int(time.time() * 1000)}.png")


def _ensure_dir(path: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)

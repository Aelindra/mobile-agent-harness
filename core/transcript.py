# -*- coding: utf-8 -*-
"""工具调用与事件的 JSONL transcript 记录器。"""
from __future__ import annotations

import datetime
import json
import os
import threading


class Transcript:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self._fh = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def log(self, kind: str, **data) -> dict:
        rec = {"ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
               "kind": kind, **data}
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        return rec

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass

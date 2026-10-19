"""订阅状态持久化（data_dir/state.json）。

结构：
{
  "version": 2,
  "subscriptions": {
    "<stream_id>": {
      "group_id": "12345" | null,
      "subscribed_at": "2026-06-13T00:00:00+00:00"
    }
  },
  "push_progress": {
    "<stream_id>": "2026-06-13"   # 每个推送目标最近一次成功推送的日期
  },
  "last_fetch": { ...Issue.to_dict()... } | null
}

说明：
- subscriptions 只放「自助订阅」的会话，判断是否订阅看这里。
- push_progress 覆盖所有推送目标（含静态群），用于防重复推送；
  静态群不在 subscriptions 里，所以推送进度必须独立于订阅表记录。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

DEFAULT_STATE = {
    "version": 2,
    "subscriptions": {},
    "push_progress": {},
    "last_fetch": None,
}


class Store:
    """线程安全的 JSON 状态存储，原子写盘（tmp + replace）。"""

    def __init__(self, path: str):
        self.path = path
        # RLock：subscribe() 等方法持锁期间会调用 save()，后者也需加锁，必须可重入
        self._lock = threading.RLock()
        self._data = json.loads(json.dumps(DEFAULT_STATE))
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged = json.loads(json.dumps(DEFAULT_STATE))
                merged.update({k: v for k, v in data.items() if k in DEFAULT_STATE})
                # v1 -> v2 迁移：把订阅表里的旧 push 进度搬到 push_progress
                for stream_id, sub in merged["subscriptions"].items():
                    old_date = (sub or {}).pop("last_pushed_date", None)
                    if old_date:
                        merged["push_progress"].setdefault(stream_id, old_date)
                self._data = merged
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError):
            # 文件损坏时保留现场备份再从头开始，不静默吞掉
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ---------- 订阅 ----------

    def subscribe(self, stream_id: str, group_id: str | None = None) -> bool:
        """返回 True 表示新增订阅，False 表示原本已订阅。"""
        with self._lock:
            subs = self._data["subscriptions"]
            if stream_id in subs:
                if group_id and not subs[stream_id].get("group_id"):
                    subs[stream_id]["group_id"] = group_id
                    self.save()
                return False
            subs[stream_id] = {
                "group_id": group_id,
                "subscribed_at": datetime.now(timezone.utc).isoformat(),
            }
            self.save()
            return True

    def unsubscribe(self, stream_id: str) -> bool:
        with self._lock:
            if stream_id in self._data["subscriptions"]:
                del self._data["subscriptions"][stream_id]
                self.save()
                return True
            return False

    def is_subscribed(self, stream_id: str) -> bool:
        with self._lock:
            return stream_id in self._data["subscriptions"]

    def subscriptions(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data["subscriptions"]))

    # ---------- 每群推送进度 ----------

    def last_pushed_date(self, stream_id: str) -> str | None:
        with self._lock:
            # 兼容 v1 数据：迁移遗漏时仍能从订阅表里读到旧进度
            progress = self._data.get("push_progress") or {}
            if stream_id in progress:
                return progress.get(stream_id) or None
            sub = self._data["subscriptions"].get(stream_id)
            return (sub or {}).get("last_pushed_date")

    def set_last_pushed(self, stream_id: str, date: str) -> None:
        with self._lock:
            self._data.setdefault("push_progress", {})[stream_id] = date
            self.save()

    # ---------- 最近一期缓存 ----------

    def set_last_fetch(self, issue_dict: dict) -> None:
        with self._lock:
            self._data["last_fetch"] = issue_dict
            self.save()

    def get_last_fetch(self) -> dict | None:
        with self._lock:
            return self._data.get("last_fetch")

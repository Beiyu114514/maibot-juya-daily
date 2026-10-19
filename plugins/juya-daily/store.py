"""订阅状态持久化（data_dir/state.json）。

结构（v3）：
{
  "version": 3,
  "subscriptions": {
    "<stream_id>": {
      "group_id": "12345" | null,
      "subscribed_at": "2026-06-13T00:00:00+00:00"
    }
  },
  "push_progress": {
    "<stream_id>": {"date": "2026-06-13", "page": 2, "done": false}
  },
  "last_fetch": { ...Issue.to_dict()... } | null
}

说明：
- subscriptions 只放「自助订阅」的会话，判断是否订阅看这里。
- push_progress 覆盖所有推送目标（含静态群），用于防重复推送与多页断点续传；
  静态群不在 subscriptions 里，所以推送进度必须独立于订阅表记录。
- page 表示「已成功发出的页数」；done=true 才表示整期发完。
- v2 的字符串进度（"2026-06-13"）在加载时迁移为 done=true 的 dict；
  v1 订阅表内的 last_pushed_date 也会搬到 push_progress。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

CURRENT_STATE_VERSION = 3

DEFAULT_STATE = {
    "version": CURRENT_STATE_VERSION,
    "subscriptions": {},
    "push_progress": {},
    "last_fetch": None,
}


class Store:
    """线程安全的 JSON 状态存储，原子写盘（tmp + replace）。"""

    def __init__(self, path: str, logger: logging.Logger | None = None):
        self.path = path
        self.logger = logger or logging.getLogger(__name__)
        # RLock：subscribe() 等方法持锁期间会调用 save()，后者也需加锁，必须可重入
        self._lock = threading.RLock()
        self._data = json.loads(json.dumps(DEFAULT_STATE))
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except OSError as exc:
            # 读盘瞬时失败（权限/占用/IO）：保留原文件，不要当损坏备份并重置
            self.logger.warning("读取状态文件失败，保留原文件: %s", exc)
            return
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.logger.warning("状态文件损坏，已备份为 %s.corrupt: %s", self.path, exc)
            self._backup(self.path, ".corrupt")
            return

        version = data.get("version") if isinstance(data, dict) else None
        if isinstance(version, int) and version > CURRENT_STATE_VERSION:
            # 未来版本（例如降级回旧插件）：不要静默降级，备份后重置，日志说明原因
            self.logger.warning(
                "状态版本 %s 过新（当前支持 %s），已备份为 %s.future 并重置",
                version,
                CURRENT_STATE_VERSION,
                self.path,
            )
            self._backup(self.path, ".future")
            return
        if not self._adopt(data):
            # 结构不认识（版本迁移期最常见）：备份现场，退回默认状态，
            # 插件继续能加载，只是丢掉旧的订阅/推送进度。
            self.logger.warning("状态文件结构无法识别，已备份为 %s.corrupt 并重置", self.path)
            self._backup(self.path, ".corrupt")

    def _backup(self, path: str, suffix: str) -> None:
        try:
            os.replace(path, path + suffix)
        except OSError as exc:
            self.logger.warning("备份状态文件失败（%s）: %s", suffix, exc)

    def _adopt(self, data: object) -> bool:
        """把读到的 JSON 规整成合法状态；返回 False 表示数据不可用（结构损坏）。"""
        if not isinstance(data, dict):
            return False
        version = data.get("version")
        if not isinstance(version, int) or not 1 <= version <= CURRENT_STATE_VERSION:
            return False

        raw_subs = data.get("subscriptions")
        raw_progress = data.get("push_progress")
        raw_fetch = data.get("last_fetch")
        # 各表都必须是 dict；任何一个是别的类型都视为结构损坏
        if raw_subs is not None and not isinstance(raw_subs, dict):
            return False
        if raw_progress is not None and not isinstance(raw_progress, dict):
            return False
        if raw_fetch is not None and not isinstance(raw_fetch, dict):
            return False

        merged = json.loads(json.dumps(DEFAULT_STATE))
        merged["version"] = CURRENT_STATE_VERSION
        if isinstance(raw_subs, dict):
            merged["subscriptions"] = {
                str(sid): (sub if isinstance(sub, dict) else {})
                for sid, sub in raw_subs.items()
            }
        if isinstance(raw_progress, dict):
            merged["push_progress"] = self._normalize_progress(raw_progress)
        if isinstance(raw_fetch, dict):
            merged["last_fetch"] = raw_fetch

        # v1 -> v2 迁移：把订阅表里的旧 push 进度搬到 push_progress
        for stream_id, sub in merged["subscriptions"].items():
            old_date = sub.pop("last_pushed_date", None)
            if old_date and stream_id not in merged["push_progress"]:
                merged["push_progress"][stream_id] = {
                    "date": str(old_date),
                    "page": 0,
                    "done": True,
                }
        self._data = merged
        return True

    @staticmethod
    def _normalize_progress(raw: dict) -> dict:
        """v2 字符串 / v3 dict 都规整为 {date, page, done}。"""
        progress: dict[str, dict] = {}
        for sid, value in raw.items():
            if isinstance(value, str):
                if value:
                    progress[str(sid)] = {"date": value, "page": 0, "done": True}
                continue
            if not isinstance(value, dict) or not value.get("date"):
                continue
            try:
                page = max(0, int(value.get("page") or 0))
            except (TypeError, ValueError):
                page = 0
            progress[str(sid)] = {
                "date": str(value["date"]),
                "page": page,
                "done": bool(value.get("done")),
            }
        return progress

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

    def push_state(self, stream_id: str) -> dict:
        """返回 {date, page, done}；page 表示已成功发出的页数。"""
        with self._lock:
            value = (self._data.get("push_progress") or {}).get(stream_id)
            if isinstance(value, dict):
                try:
                    page = max(0, int(value.get("page") or 0))
                except (TypeError, ValueError):
                    page = 0
                return {
                    "date": value.get("date") or None,
                    "page": page,
                    "done": bool(value.get("done")),
                }
            if isinstance(value, str):  # 兜底：v2 残留
                return {"date": value or None, "page": 0, "done": bool(value)}
            # 兼容 v1 数据：迁移遗漏时仍能从订阅表里读到旧进度
            sub = self._data["subscriptions"].get(stream_id)
            old_date = (sub or {}).get("last_pushed_date")
            return {"date": old_date or None, "page": 0, "done": bool(old_date)}

    def set_push_progress(
        self, stream_id: str, date: str, page: int = 0, done: bool = False
    ) -> None:
        with self._lock:
            self._data.setdefault("push_progress", {})[stream_id] = {
                "date": date,
                "page": max(0, int(page)),
                "done": bool(done),
            }
            self.save()

    def last_pushed_date(self, stream_id: str) -> str | None:
        """只有整期发完（done）才算「今天推过」；半截状态仍视为未推送。"""
        state = self.push_state(stream_id)
        return state["date"] if state["done"] else None

    def set_last_pushed(self, stream_id: str, date: str) -> None:
        self.set_push_progress(stream_id, date, page=0, done=True)

    # ---------- 最近一期缓存 ----------

    def set_last_fetch(self, issue_dict: dict) -> None:
        with self._lock:
            self._data["last_fetch"] = issue_dict
            self.save()

    def get_last_fetch(self) -> dict | None:
        with self._lock:
            # 深拷贝：调用方（fetcher）原地修改不能绕过 save() 写盘
            value = self._data.get("last_fetch")
            return json.loads(json.dumps(value)) if value is not None else None

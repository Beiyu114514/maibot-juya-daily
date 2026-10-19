"""store 单元测试：订阅、多页推送进度、v1/v2 迁移、版本与容错。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "juya-daily"))

from store import CURRENT_STATE_VERSION, DEFAULT_STATE, Store  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_subscribe_and_unsubscribe(self) -> None:
        self.assertTrue(self.store.subscribe("s1", "10001"))
        self.assertFalse(self.store.subscribe("s1", "10001"))  # 重复订阅返回 False
        self.assertTrue(self.store.is_subscribed("s1"))
        self.assertEqual(list(self.store.subscriptions()), ["s1"])
        self.assertTrue(self.store.unsubscribe("s1"))
        self.assertFalse(self.store.is_subscribed("s1"))
        self.assertFalse(self.store.unsubscribe("s1"))

    def test_subscriptions_return_copy(self) -> None:
        self.store.subscribe("s1", "10001")
        snapshot = self.store.subscriptions()
        snapshot.pop("s1")
        self.assertTrue(self.store.is_subscribed("s1"))  # 外部改动不影响内部状态

    def test_last_pushed_for_unsubscribed_stream(self) -> None:
        """静态群不订阅也要记推送进度，否则重启后会重复补推。"""
        self.store.set_last_pushed("static-1", "2026-06-15")
        self.assertEqual(self.store.last_pushed_date("static-1"), "2026-06-15")
        self.assertFalse(self.store.is_subscribed("static-1"))

    def test_push_state_tracks_page_and_done(self) -> None:
        self.store.set_push_progress("s1", "2026-06-15", page=2, done=False)
        self.assertEqual(
            self.store.push_state("s1"),
            {"date": "2026-06-15", "page": 2, "done": False},
        )
        # 半截状态不能算「今天推过」，否则重启会漏发剩余页
        self.assertIsNone(self.store.last_pushed_date("s1"))
        self.store.set_push_progress("s1", "2026-06-15", page=3, done=True)
        self.assertEqual(self.store.last_pushed_date("s1"), "2026-06-15")
        self.assertEqual(self.store.push_state("s1")["page"], 3)

    def test_set_last_pushed_marks_done(self) -> None:
        self.store.set_last_pushed("s1", "2026-06-15")
        state = self.store.push_state("s1")
        self.assertTrue(state["done"])
        self.assertEqual(state["page"], 0)

    def test_v3_progress_roundtrip(self) -> None:
        self.store.set_push_progress("s1", "2026-06-15", page=1, done=False)
        reloaded = Store(self.path)
        self.assertEqual(
            reloaded.push_state("s1"),
            {"date": "2026-06-15", "page": 1, "done": False},
        )

    def test_v2_string_progress_migrates_to_done(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "push_progress": {"s1": "2026-06-14"}}, fh)
        store = Store(self.path)
        self.assertEqual(
            store.push_state("s1"),
            {"date": "2026-06-14", "page": 0, "done": True},
        )
        store.save()
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], CURRENT_STATE_VERSION)
        self.assertEqual(
            data["push_progress"]["s1"],
            {"date": "2026-06-14", "page": 0, "done": True},
        )

    def test_future_version_backed_up_and_reset(self) -> None:
        """降级回旧插件时不能把新格式静默覆盖，先备份 .future。"""
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(
                {"version": 99, "push_progress": {"s1": {"date": "2026-06-15", "page": 1}}},
                fh,
            )
        store = Store(self.path)
        self.assertEqual(store.subscriptions(), {})
        self.assertEqual(store.push_state("s1")["page"], 0)
        self.assertTrue(os.path.exists(self.path + ".future"))
        self.assertFalse(os.path.exists(self.path))

    def test_read_oserror_keeps_file(self) -> None:
        """读盘瞬时失败不能当损坏备份并重置，否则会丢订阅。"""
        self.store.subscribe("s1", "10001")
        with open(self.path, encoding="utf-8") as fh:
            before = fh.read()
        with mock.patch("builtins.open", side_effect=OSError("disk busy")):
            store = Store(self.path)
        self.assertEqual(store.subscriptions(), {})
        self.assertFalse(os.path.exists(self.path + ".corrupt"))
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_invalid_progress_entries_dropped(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "version": 3,
                    "push_progress": {
                        "good": {"date": "2026-06-15", "page": "2", "done": 1},
                        "missing_date": {"page": 2},
                        "bad_page": {"date": "2026-06-15", "page": "?"},
                        "empty": "",
                    },
                },
                fh,
            )
        store = Store(self.path)
        self.assertEqual(
            store.push_state("good"),
            {"date": "2026-06-15", "page": 2, "done": True},
        )
        self.assertIsNone(store.push_state("missing_date")["date"])
        self.assertEqual(store.push_state("bad_page")["page"], 0)
        self.assertIsNone(store.push_state("empty")["date"])

    def test_get_last_fetch_returns_deep_copy(self) -> None:
        self.store.set_last_fetch(
            {"date": "2026-06-15", "items": [{"title": "原始标题"}]}
        )
        snapshot = self.store.get_last_fetch()
        assert snapshot is not None
        snapshot["items"][0]["title"] = "被外部改掉"
        self.assertEqual(
            self.store.get_last_fetch()["items"][0]["title"], "原始标题"
        )

    def test_persistence_roundtrip(self) -> None:
        self.store.subscribe("s1", "10001")
        self.store.set_last_pushed("s1", "2026-06-15")
        self.store.set_last_fetch({"date": "2026-06-15", "title": "t"})
        reloaded = Store(self.path)
        self.assertTrue(reloaded.is_subscribed("s1"))
        self.assertEqual(reloaded.last_pushed_date("s1"), "2026-06-15")
        self.assertEqual(reloaded.get_last_fetch()["title"], "t")

    def test_v1_migration_moves_last_pushed(self) -> None:
        legacy = {
            "version": 1,
            "subscriptions": {"s1": {"group_id": "10001", "last_pushed_date": "2026-06-14"}},
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(legacy, fh)
        store = Store(self.path)
        self.assertTrue(store.is_subscribed("s1"))
        self.assertEqual(store.subscriptions()["s1"]["group_id"], "10001")
        self.assertEqual(store.last_pushed_date("s1"), "2026-06-14")
        self.assertNotIn("last_pushed_date", store.subscriptions()["s1"])

    def test_null_subscriptions_does_not_crash(self) -> None:
        """{\"version\":1,\"subscriptions\":null} 曾让 v1→v2 迁移抛 AttributeError。"""
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "subscriptions": None}, fh)
        store = Store(self.path)  # 不应抛异常
        self.assertEqual(store.subscriptions(), {})
        store.save()
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], DEFAULT_STATE["version"])

    def test_corrupt_json_backed_up(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        store = Store(self.path)
        self.assertEqual(store.subscriptions(), {})
        self.assertTrue(os.path.exists(self.path + ".corrupt"))

    def test_wrong_shape_backed_up(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "subscriptions": "oops", "push_progress": 5}, fh)
        store = Store(self.path)
        self.assertEqual(store.subscriptions(), {})
        self.assertTrue(os.path.exists(self.path + ".corrupt"))

    def test_state_file_is_valid_json_after_writes(self) -> None:
        self.store.subscribe("s1", "10001")
        self.store.set_last_pushed("s1", "2026-06-15")
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["subscriptions"]["s1"]["group_id"], "10001")
        self.assertEqual(
            data["push_progress"]["s1"],
            {"date": "2026-06-15", "page": 0, "done": True},
        )


if __name__ == "__main__":
    unittest.main()

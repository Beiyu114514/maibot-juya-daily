"""scheduler 单元测试：下一次触发计算、时钟注入、启停。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "juya-daily"))

from scheduler import DailyScheduler  # noqa: E402

LOGGER = logging.getLogger("test-scheduler")
LOGGER.addHandler(logging.NullHandler())


class NextRunTest(unittest.TestCase):
    def test_later_today(self) -> None:
        scheduler = DailyScheduler("09:30", lambda: None, LOGGER)
        now = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(
            scheduler._next_run(now), datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc)
        )

    def test_already_passed_rolls_to_tomorrow(self) -> None:
        scheduler = DailyScheduler("09:30", lambda: None, LOGGER)
        now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(
            scheduler._next_run(now), datetime(2026, 6, 16, 9, 30, tzinfo=timezone.utc)
        )

    def test_invalid_time_falls_back(self) -> None:
        scheduler = DailyScheduler("not-a-time", lambda: None, LOGGER)
        self.assertEqual(scheduler._parse_push_time(), (9, 30))

    def test_now_fn_injection(self) -> None:
        """插件注入东八区时钟；此处验证时钟可被替换。"""
        fixed = datetime(2026, 6, 15, 23, 0, tzinfo=timezone(timedelta(hours=8)))
        scheduler = DailyScheduler("09:30", lambda: None, LOGGER, now_fn=lambda: fixed)
        target = scheduler._next_run()
        self.assertEqual(target, fixed.replace(day=16, hour=9, minute=30, second=0, microsecond=0))


class LifecycleTest(unittest.TestCase):
    def test_start_stop(self) -> None:
        async def scenario() -> None:
            fired = asyncio.Event()

            async def on_fire() -> None:
                fired.set()

            scheduler = DailyScheduler("23:59", on_fire, LOGGER, now_fn=lambda: datetime.now(timezone.utc))
            await scheduler.start()
            self.assertIsNotNone(scheduler.next_run())
            await scheduler.stop()
            self.assertIsNone(scheduler.next_run())
            self.assertFalse(fired.is_set())

        asyncio.run(scenario())

    def test_stop_is_idempotent(self) -> None:
        async def scenario() -> None:
            scheduler = DailyScheduler("23:59", lambda: None, LOGGER)
            await scheduler.start()
            await scheduler.stop()
            await scheduler.stop()  # 二次停止不应抛错

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

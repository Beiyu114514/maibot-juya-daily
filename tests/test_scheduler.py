"""scheduler 单元测试：下一次触发计算、时钟注入、启停。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "juya-daily"))

from scheduler import DailyScheduler, parse_hhmm  # noqa: E402

LOGGER = logging.getLogger("test-scheduler")
LOGGER.addHandler(logging.NullHandler())


class ParseHhmmTest(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(parse_hhmm("07:05"), (7, 5))
        self.assertEqual(parse_hhmm("23:59"), (23, 59))

    def test_invalid_falls_back(self) -> None:
        self.assertEqual(parse_hhmm("not-a-time", (9, 30)), (9, 30))
        self.assertEqual(parse_hhmm("24:00", (9, 30)), (9, 30))
        self.assertEqual(parse_hhmm("09:60", (9, 30)), (9, 30))

    def test_invalid_logs_warning(self) -> None:
        with self.assertLogs("test-scheduler", level="WARNING") as captured:
            parse_hhmm("oops", (12, 0), LOGGER)
        self.assertIn("回退 12:00", "\n".join(captured.output))


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


class _ImmediateScheduler(DailyScheduler):
    """把「下一次触发」固定为现在，让 _loop 不必真的等到 09:30。"""

    def _next_run(self, now: datetime | None = None) -> datetime:
        return self._now_fn()


class LoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_loop_fires(self) -> None:
        fired = asyncio.Event()
        calls = {"fire": 0}

        async def on_fire() -> None:
            calls["fire"] += 1
            fired.set()

        scheduler = _ImmediateScheduler("09:30", on_fire, LOGGER, check_interval=0.01)
        scheduler.POST_FIRE_SLEEP = 0.0  # 测试不等 61 秒
        await scheduler.start()
        await asyncio.wait_for(fired.wait(), timeout=2)
        await scheduler.stop()
        self.assertGreaterEqual(calls["fire"], 1)

    async def test_callback_exception_does_not_kill_loop(self) -> None:
        calls = {"fire": 0}
        fired_twice = asyncio.Event()

        async def on_fire() -> None:
            calls["fire"] += 1
            if calls["fire"] == 1:
                raise RuntimeError("回调炸了")
            fired_twice.set()

        scheduler = _ImmediateScheduler("09:30", on_fire, LOGGER, check_interval=0.01)
        scheduler.POST_FIRE_SLEEP = 0.0
        with self.assertLogs(LOGGER, level="ERROR"):
            await scheduler.start()
            try:
                await asyncio.wait_for(fired_twice.wait(), timeout=2)
            finally:
                await scheduler.stop()
        self.assertGreaterEqual(calls["fire"], 2)


if __name__ == "__main__":
    unittest.main()

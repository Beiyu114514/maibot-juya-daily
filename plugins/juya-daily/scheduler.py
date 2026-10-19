"""极简每日定时器：睡到 push_time 触发一次回调，可随时停止或换时间。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta


class DailyScheduler:
    """每天在 push_time 触发一次 on_fire；stop() 可随时取消。"""

    def __init__(
        self,
        push_time: str,
        on_fire,  # async callable，无参数
        logger: logging.Logger,
        check_interval: float = 30.0,
    ):
        self.push_time = push_time
        self.on_fire = on_fire
        self.logger = logger
        self.check_interval = check_interval
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---------- 公共接口 ----------

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="juya-daily-scheduler")
        self.logger.info("每日推送定时器已启动（push_time=%s）", self.push_time)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                self.logger.exception("定时任务停止时出错")
            self._task = None

    def next_run(self) -> datetime | None:
        """当前计划的下一次触发时间；未在运行时返回 None。"""
        if self._task and not self._task.done():
            return self._next_run()
        return None

    # ---------- 内部实现 ----------

    def _parse_push_time(self) -> tuple[int, int]:
        try:
            hour, minute = (int(part) for part in str(self.push_time).split(":", 1))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except (ValueError, IndexError):
            pass
        self.logger.warning("push_time 配置非法: %r，回退 09:30", self.push_time)
        return 9, 30

    def _next_run(self, now: datetime | None = None) -> datetime:
        hour, minute = self._parse_push_time()
        now = now or datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            target = self._next_run()
            self.logger.info("下次推送时间: %s", target.strftime("%Y-%m-%d %H:%M"))
            try:
                # 分片睡眠：既能及时响应 stop，也不会空转
                while not self._stop_event.is_set():
                    remaining = (target - datetime.now()).total_seconds()
                    if remaining <= 0:
                        break
                    timeout = min(remaining, self.check_interval)
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            if self._stop_event.is_set():
                break
            try:
                await self.on_fire()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("每日推送回调执行出错")
            await asyncio.sleep(61)  # 越过当前分钟，避免同一分钟重复触发

"""橘鸦AI早报 · MaiBot 每日资讯插件。

数据源：https://daily.juya.uk/rss.xml（每期全文内嵌在 RSS 条目里）
功能：群内自助订阅 + 每日定时推送（含启动补推、失败重试）+ /早报 即取即看。
"""

from __future__ import annotations

import asyncio
import os
import random
from datetime import time as dtime

from pydantic import Field

try:  # 以包形式加载（plugins.juya_daily.plugin）
    from .fetcher import Issue, JuyaFetcher, now_cst, today_cst
    from .formatter import DEFAULT_FOOTER, format_issue, paginate_issue
    from .scheduler import DailyScheduler
    from .store import Store
except ImportError:  # 以目录形式直接加载时的兜底
    import sys as _sys
    from pathlib import Path as _Path

    _HERE = str(_Path(__file__).resolve().parent)
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)
    from fetcher import Issue, JuyaFetcher, now_cst, today_cst
    from formatter import DEFAULT_FOOTER, format_issue, paginate_issue
    from scheduler import DailyScheduler
    from store import Store

try:
    from maibot_sdk import Command, MaiBotPlugin, PluginConfigBase
except ImportError:  # SDK 子模块布局不同时的兜底
    from maibot_sdk.plugin import Command, MaiBotPlugin, PluginConfigBase


# ---------- 配置模型（Runner 首载自动生成 config.toml + WebUI 表单） ----------


class PluginSection(PluginConfigBase):
    """插件基础配置（SDK/Host 约定必须存在的 ``[plugin]`` 节）。

    ``config_version`` 由 Host 用于配置迁移对齐，缺节或缺字段时
    Runner 会直接抛 PluginConfigVersionError，插件无法加载。
    """

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(
        "2.0.0",
        description="配置结构版本号（升级配置结构时递增，Host 据此迁移旧配置）",
        json_schema_extra={"label": "配置版本"},
    )


class ScheduleSection(PluginConfigBase):
    __ui_label__ = "定时"
    __ui_icon__ = "clock"
    __ui_order__ = 1

    push_time: str = Field(
        "09:30",
        description="每日推送时间（东八区，HH:MM）。站点一般在东八区 08:45 左右发布当天一期",
        json_schema_extra={"label": "推送时间", "placeholder": "09:30"},
    )
    catch_up_on_start: bool = Field(
        True,
        description="启动时若已过推送时间且今天还没推，自动补发今天一期",
        json_schema_extra={"label": "启动补推"},
    )


class SubscribeSection(PluginConfigBase):
    __ui_label__ = "订阅"
    __ui_icon__ = "bell"
    __ui_order__ = 3

    static_chats: list[str] = Field(
        default_factory=list,
        description="静态订阅群列表（填群号，兜底用；与群内自助订阅叠加）",
        json_schema_extra={"label": "静态订阅群"},
    )
    static_platform: str = Field(
        "qq",
        description="静态订阅群所属平台",
        json_schema_extra={"label": "静态订阅平台"},
    )
    admin_only: bool = Field(
        False,
        description="仅管理员可使用订阅/退订命令",
        json_schema_extra={"label": "仅管理员可订阅"},
    )


class PushSection(PluginConfigBase):
    __ui_label__ = "推送"
    __ui_icon__ = "send"
    __ui_order__ = 4

    max_items: int = Field(
        0,
        description="每期最多推送的资讯条数，0 表示全部",
        json_schema_extra={"label": "每期最多条数"},
    )
    footer: str = Field(
        DEFAULT_FOOTER,
        description="推送脚注模板，支持 {link} {date} {title} 占位",
        json_schema_extra={"label": "脚注模板"},
    )
    retry_minutes: int = Field(
        10,
        description="推送失败后的重试间隔（分钟）",
        json_schema_extra={"label": "重试间隔（分钟）"},
    )
    retry_times: int = Field(
        3,
        description="当天推送失败的最大重试轮数",
        json_schema_extra={"label": "重试轮数"},
    )
    stale_deadline: str = Field(
        "12:00",
        description="今天的早报到几点还没发布就放弃当天推送（HH:MM）",
        json_schema_extra={"label": "当日放弃时间"},
    )


class JuyaDailyConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection, description="插件基础配置")
    schedule: ScheduleSection = Field(default_factory=ScheduleSection, description="定时")
    subscribe: SubscribeSection = Field(default_factory=SubscribeSection, description="订阅")
    push: PushSection = Field(default_factory=PushSection, description="推送")


# ---------- 插件入口 ----------


class JuyaDailyPlugin(MaiBotPlugin):
    """入口：命令、生命周期与每日推送编排。"""

    config_model = JuyaDailyConfig

    _WAIT_INTERVAL_SECONDS = 30 * 60  # 今天的早报没发布时，隔多久再查一次

    # ---------- 生命周期 ----------

    async def on_load(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._static_stream_ids: dict[str, str] = {}

        paths = getattr(self.ctx, "paths", None)
        data_dir = getattr(paths, "data_dir", None) if paths is not None else None
        if not data_dir:
            data_dir = os.path.join(os.getcwd(), "data", "juya-daily")
            self.ctx.logger.warning(
                "ctx.paths.data_dir 不可用，状态将写入 %s（cwd=%s，可能不符合预期）",
                data_dir,
                os.getcwd(),
            )
        os.makedirs(data_dir, exist_ok=True)

        self._store = Store(os.path.join(data_dir, "state.json"))
        self._fetcher = JuyaFetcher(self.ctx.logger, cache=self._store.get_last_fetch())

        await self._resolve_static_chats()

        self._scheduler = DailyScheduler(
            push_time=self.config.schedule.push_time,
            on_fire=self._on_schedule_fire,
            logger=self.ctx.logger,
            now_fn=now_cst,
        )
        await self._scheduler.start()
        self._spawn(self._catch_up_on_start(), "catch-up")

        self.ctx.logger.info(
            "橘鸦AI早报插件已加载：订阅 %d 个会话，静态群 %d 个",
            len(self._store.subscriptions()),
            len(self._static_stream_ids),
        )

    async def on_unload(self) -> None:
        scheduler = getattr(self, "_scheduler", None)
        if scheduler:
            try:
                await scheduler.stop()
            except Exception:
                pass
        tasks = list(getattr(self, "_tasks", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.ctx.logger.info("橘鸦AI早报插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version) -> None:
        if scope != "self":
            return
        try:
            old_scheduler = getattr(self, "_scheduler", None)
            old_time = getattr(old_scheduler, "push_time", None)
            await self._resolve_static_chats()
            new_time = self.config.schedule.push_time
            if old_time != new_time:
                self.ctx.logger.info("推送时间变更 %s -> %s，重启定时器", old_time, new_time)
                if old_scheduler:
                    await old_scheduler.stop()
                self._scheduler = DailyScheduler(
                    push_time=new_time,
                    on_fire=self._on_schedule_fire,
                    logger=self.ctx.logger,
                    now_fn=now_cst,
                )
                await self._scheduler.start()
        except Exception:
            self.ctx.logger.exception("应用新配置失败")

    # ---------- 命令 ----------

    @Command("juya_daily", pattern=r"^/早报(?!订阅|退订|状态)")
    async def cmd_daily(self, **kwargs):
        """即时拉取最新一期早报。"""
        try:
            issue = await self._fetcher.fetch_latest()
        except Exception as exc:
            self.ctx.logger.exception("/早报 拉取失败")
            await self._reply(kwargs, f"拉取早报出错了：{exc}")
            return (False, str(exc), 2)
        if not issue:
            await self._reply(kwargs, "暂时抓不到早报（网络或站点异常），稍后再试试吧")
            return (False, "no issue", 2)
        self._store.set_last_fetch(issue.to_dict())
        stale = issue.date != today_cst()
        pages = paginate_issue(
            issue,
            max_items=int(self.config.push.max_items or 0),
            footer_template=self.config.push.footer or DEFAULT_FOOTER,
            stale=stale,
        )
        stream_id = kwargs.get("stream_id") or kwargs.get("session_id")
        if stream_id:
            for page in pages:
                await self._send_text(page, str(stream_id))
        else:
            self.ctx.logger.warning("/早报 缺少 stream_id，只能记录日志：%s", pages[0][:80])
        return (True, "ok", 2)

    @Command("juya_daily_sub", pattern=r"^/早报订阅")
    async def cmd_subscribe(self, **kwargs):
        if not self._check_admin(kwargs):
            await self._reply(kwargs, "当前配置为仅管理员可订阅，请联系 bot 管理员")
            return (False, "admin only", 2)
        stream_id = str(kwargs.get("stream_id") or kwargs.get("session_id") or "")
        if not stream_id:
            await self._reply(kwargs, "拿不到当前会话标识，订阅失败")
            return (False, "no stream_id", 2)
        group_id = self._extract_group_id(kwargs)
        if self._store.subscribe(stream_id, group_id):
            await self._reply(kwargs, "订阅成功！每天会在这里收到「橘鸦AI早报」，发 /早报退订 可取消")
        else:
            await self._reply(kwargs, "这个会话已经订阅过啦～")
        return (True, "ok", 2)

    @Command("juya_daily_unsub", pattern=r"^/早报退订")
    async def cmd_unsubscribe(self, **kwargs):
        if not self._check_admin(kwargs):
            await self._reply(kwargs, "当前配置为仅管理员可退订，请联系 bot 管理员")
            return (False, "admin only", 2)
        stream_id = str(kwargs.get("stream_id") or kwargs.get("session_id") or "")
        if not stream_id:
            await self._reply(kwargs, "拿不到当前会话标识，退订失败")
            return (False, "no stream_id", 2)
        if self._store.unsubscribe(stream_id):
            await self._reply(kwargs, "已退订，不再推送。想恢复随时发 /早报订阅")
        else:
            await self._reply(kwargs, "这个会话本来就没订阅哦")
        return (True, "ok", 2)

    @Command("juya_daily_status", pattern=r"^/早报状态")
    async def cmd_status(self, **kwargs):
        stream_id = str(kwargs.get("stream_id") or kwargs.get("session_id") or "")
        subs = self._store.subscriptions()
        last_fetch = self._store.get_last_fetch() or {}
        scheduler = getattr(self, "_scheduler", None)
        next_run = scheduler.next_run() if scheduler else None
        lines = [
            "🛰️ 橘鸦AI早报 · 状态",
            f"本会话订阅：{'是' if stream_id and stream_id in subs else '否'}",
            f"总订阅数：{len(subs)}（另含静态群 {len(self._static_stream_ids)} 个）",
            f"推送时间：每天 {self.config.schedule.push_time}",
        ]
        if next_run:
            lines.append(f"下次推送：{next_run.strftime('%m-%d %H:%M')}")
        if last_fetch:
            lines.append(f"最近一期：{last_fetch.get('date') or '未知'}《{last_fetch.get('title') or ''}》")
        await self._reply(kwargs, "\n".join(lines))
        return (True, "ok", 2)

    # ---------- 内部工具 ----------

    def _spawn(self, coro, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=f"juya-{name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _reply(self, kwargs: dict, text: str) -> None:
        stream_id = kwargs.get("stream_id") or kwargs.get("session_id")
        if not stream_id:
            self.ctx.logger.warning("缺少 stream_id，无法回复消息")
            return
        try:
            await self._send_text(text, str(stream_id))
        except Exception:
            self.ctx.logger.exception("回复消息失败")

    @staticmethod
    def _pick_stream_id(stream) -> str | None:
        if not stream:
            return None
        if isinstance(stream, str):
            return stream
        if isinstance(stream, dict):
            for key in ("stream_id", "session_id", "id"):
                if stream.get(key):
                    return str(stream[key])
            inner = stream.get("stream")
            if isinstance(inner, dict):
                return JuyaDailyPlugin._pick_stream_id(inner)
        for attr in ("stream_id", "session_id", "id"):
            value = getattr(stream, attr, None)
            if value:
                return str(value)
        return None

    @staticmethod
    def _extract_group_id(kwargs: dict) -> str | None:
        for key in ("group_id", "group_id_str"):
            if kwargs.get(key):
                return str(kwargs[key])
        for holder_key in ("stream", "chat_stream", "message"):
            holder = kwargs.get(holder_key)
            if not isinstance(holder, dict):
                continue
            for key in ("group_id", "group_id_str"):
                if holder.get(key):
                    return str(holder[key])
            info = holder.get("group_info") or holder.get("group")
            if isinstance(info, dict) and info.get("group_id"):
                return str(info["group_id"])
        return None

    def _check_admin(self, kwargs: dict) -> bool:
        if not self.config.subscribe.admin_only:
            return True
        # 适配器字段不统一，尽量兼容；判定不了就拒绝，避免越权
        for key in ("is_admin", "is_superuser", "is_bot_admin"):
            if key in kwargs:
                return bool(kwargs[key])
        for holder_key in ("message", "sender", "user"):
            holder = kwargs.get(holder_key)
            if not isinstance(holder, dict):
                continue
            for key in ("is_admin", "is_superuser"):
                if key in holder:
                    return bool(holder[key])
            role = holder.get("role")
            if role:
                return str(role).lower() in ("admin", "owner", "superuser", "administrator")
        self.ctx.logger.warning("admin_only=true 且无法判定发送者身份，已拒绝订阅操作")
        return False

    # ---------- 订阅与推送 ----------

    async def _resolve_static_chats(self) -> None:
        """把 config 里的静态群号解析成可发送的 stream_id。"""
        self._static_stream_ids = {}
        chats = [str(g) for g in (self.config.subscribe.static_chats or [])]
        if not chats:
            return
        chat_api = getattr(self.ctx, "chat", None)
        if chat_api is None or not hasattr(chat_api, "get_stream_by_group_id"):
            self.ctx.logger.warning("ctx.chat 不可用，静态订阅群 %s 无法解析", chats)
            return
        platform = self.config.subscribe.static_platform or "qq"
        for group_id in chats:
            try:
                stream = await chat_api.get_stream_by_group_id(group_id, platform=platform)
            except Exception as exc:
                stream = None
                self.ctx.logger.warning("解析群 %s(%s) 会话流失败: %s", group_id, platform, exc)
            stream_id = self._pick_stream_id(stream)
            if stream_id:
                self._static_stream_ids[group_id] = stream_id
            else:
                self.ctx.logger.warning("找不到群 %s(%s) 的会话流，跳过该静态订阅", group_id, platform)

    def _push_targets(self) -> list[str]:
        targets = list(self._store.subscriptions().keys())
        for stream_id in self._static_stream_ids.values():
            if stream_id not in targets:
                targets.append(stream_id)
        return targets

    async def _send_text(self, text: str, stream_id: str) -> bool:
        """发送文本并严格判定结果。

        SDK 的 ``send.text`` 失败时返回 False（不抛异常），
        也可能返回 ``SendResult{sent: bool, message_id}``。
        只看抛不抛异常会把失败当成功，导致 last_pushed 被写死。
        """
        try:
            result = await self.ctx.send.text(text, str(stream_id), return_details=True)
        except Exception as exc:
            self.ctx.logger.error("推送到 %s 失败: %s", stream_id, exc)
            return False
        if isinstance(result, dict):
            sent = bool(result.get("sent"))
            if not sent:
                self.ctx.logger.error("推送到 %s 失败: %r", stream_id, result)
            return sent
        if not bool(result):
            self.ctx.logger.error("推送到 %s 失败: 能力返回 %r", stream_id, result)
        return bool(result)

    async def _push_one(self, stream_id: str, issue: Issue) -> bool:
        """推送一期早报（自动分页）；全部页发送成功才算成功。"""
        pages = paginate_issue(
            issue,
            max_items=int(self.config.push.max_items or 0),
            footer_template=self.config.push.footer or DEFAULT_FOOTER,
        )
        for index, page in enumerate(pages, start=1):
            if not await self._send_text(page, stream_id):
                if index > 1:
                    self.ctx.logger.warning(
                        "推送到 %s 的第 %d/%d 页失败，前面已发出的页无法撤回",
                        stream_id,
                        index,
                        len(pages),
                    )
                return False
        self._store.set_last_pushed(stream_id, issue.date)
        return True

    async def _push_round(self, due: list[str], issue: Issue) -> None:
        static_values = set(self._static_stream_ids.values())
        failed: list[str] = []
        for stream_id in due:
            if not self._store.is_subscribed(stream_id) and stream_id not in static_values:
                continue  # 推送间隙被退订了
            if not await self._push_one(stream_id, issue):
                failed.append(stream_id)
        if failed:
            self.ctx.logger.warning("%d 个会话推送失败，稍后重试", len(failed))
            self._spawn(self._retry_failed(failed, issue), "push-retry")

    async def _retry_failed(self, failed: list[str], issue: Issue) -> None:
        rounds = max(0, int(self.config.push.retry_times or 0))
        interval = max(1, int(self.config.push.retry_minutes or 10)) * 60
        for round_no in range(1, rounds + 1):
            await asyncio.sleep(interval)
            still_failed: list[str] = []
            for stream_id in failed:
                if await self._push_one(stream_id, issue):
                    self.ctx.logger.info("重试第 %d 轮成功: %s", round_no, stream_id)
                else:
                    still_failed.append(stream_id)
            failed = still_failed
            if not failed:
                return
        self.ctx.logger.error("多轮重试后仍推送失败的会话: %s", ", ".join(failed))

    async def _catch_up_on_start(self) -> None:
        if not self.config.schedule.catch_up_on_start:
            return
        try:
            await asyncio.sleep(random.uniform(5, 15))  # 等连接稳定
            today = today_cst()
            due = [
                stream_id
                for stream_id in self._push_targets()
                if (self._store.last_pushed_date(stream_id) or "") < today
            ]
            if not due:
                return
            issue = await self._fetcher.fetch_latest()
            if not issue or issue.date != today:
                self.ctx.logger.info("启动补推：今天(%s)的一期还没抓到，等每日定时再推", today)
                return
            self.ctx.logger.info("启动补推：%d 个会话今天还没收到，错峰发送", len(due))

            async def _push_staggered(stream_id: str, index: int) -> bool:
                if index:
                    # 并发发起但各自随机延迟，群多时总耗时取决于最慢一个而非总和
                    await asyncio.sleep(random.uniform(30, 120))
                return await self._push_one(stream_id, issue)

            results = await asyncio.gather(
                *(_push_staggered(stream_id, index) for index, stream_id in enumerate(due)),
                return_exceptions=True,
            )
            failed = [sid for sid, result in zip(due, results) if result is not True]
            if failed:
                self.ctx.logger.warning("启动补推 %d 个会话失败: %s", len(failed), ", ".join(failed))
                self._spawn(self._retry_failed(failed, issue), "catch-up-retry")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ctx.logger.exception("启动补推失败")

    @staticmethod
    def _parse_hhmm(value: str, fallback: tuple[int, int]) -> tuple[int, int]:
        try:
            hour, minute = (int(part) for part in str(value).split(":", 1))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except (ValueError, IndexError):
            pass
        return fallback

    async def _on_schedule_fire(self) -> None:
        today = today_cst()
        due = [
            stream_id
            for stream_id in self._push_targets()
            if (self._store.last_pushed_date(stream_id) or "") < today
        ]
        if not due:
            self.ctx.logger.info("今日(%s)所有订阅均已推送，跳过", today)
            return
        issue = await self._fetcher.fetch_latest()
        if issue and issue.date == today:
            self._store.set_last_fetch(issue.to_dict())
            await self._push_round(due, issue)
            return
        self.ctx.logger.info("今日(%s)早报尚未发布或抓取失败，进入等待重试", today)
        self._spawn(self._wait_for_today_issue(today, due), "wait-today-issue")

    async def _wait_for_today_issue(self, today: str, due: list[str]) -> None:
        hour, minute = self._parse_hhmm(self.config.push.stale_deadline, (12, 0))
        deadline = dtime(hour, minute)

        def _now_cst_time() -> dtime:
            return now_cst().time()

        if _now_cst_time() >= deadline:
            self.ctx.logger.warning("已过当日放弃时间 %02d:%02d，今天的早报不再推送", hour, minute)
            return
        attempt = 0
        while _now_cst_time() < deadline:
            await asyncio.sleep(self._WAIT_INTERVAL_SECONDS)
            attempt += 1
            try:
                issue = await self._fetcher.fetch_latest(force=True)
            except Exception:
                self.ctx.logger.exception("等待今日早报：第 %d 次检查抓取失败", attempt)
                continue
            if issue and issue.date == today:
                self._store.set_last_fetch(issue.to_dict())
                still_due = [
                    stream_id
                    for stream_id in due
                    if (self._store.last_pushed_date(stream_id) or "") < today
                ]
                if still_due:
                    await self._push_round(still_due, issue)
                return
            self.ctx.logger.info(
                "第 %d 次检查：今日早报仍未发布（站点最新为 %s）",
                attempt,
                issue.date if issue else "无",
            )
        self.ctx.logger.warning("超过放弃时间，今日(%s)推送取消", today)


def create_plugin() -> JuyaDailyPlugin:
    """SDK 规定：插件必须提供模块级 create_plugin()。"""
    return JuyaDailyPlugin()

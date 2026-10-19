"""橘鸦AI早报 · MaiBot 每日资讯插件。

数据源：https://daily.juya.uk/rss.xml（每期全文内嵌在 RSS 条目里）
功能：群内自助订阅 + 每日定时推送（含启动补推、失败重试）+ /早报 即取即看。
"""

# 注意：不要在这里加 `from __future__ import annotations`。
# 那会让所有注解变成字符串，pydantic 需要靠 sys.modules[__name__] 才能解析
# 配置节（PluginSection/ScheduleSection/...）；若 Runner 用
# spec_from_file_location 加载插件却没把模块注册进 sys.modules，
# JuyaDailyConfig 会直接抛 "not fully defined; you should define PluginSection"。
# 本文件要求 Python 3.12+（同 MaiBot），原生就支持 list[str] / str | None。

import asyncio
import os
import random
import time
from datetime import time as dtime
from typing import Literal

from pydantic import Field

try:  # 以包形式加载（plugins.juya_daily.plugin）
    from .fetcher import Issue, JuyaFetcher, now_cst, today_cst
    from .formatter import (
        DEFAULT_FOOTER,
        markdown_to_plain,
        paginate_issue,
        paginate_issue_markdown,
        render_issue_html,
    )
    from .scheduler import DailyScheduler, parse_hhmm
    from .store import Store
except ImportError:  # 以目录形式直接加载时的兜底
    import sys as _sys
    from pathlib import Path as _Path

    _HERE = str(_Path(__file__).resolve().parent)
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)
    from fetcher import Issue, JuyaFetcher, now_cst, today_cst
    from formatter import (
        DEFAULT_FOOTER,
        markdown_to_plain,
        paginate_issue,
        paginate_issue_markdown,
        render_issue_html,
    )
    from scheduler import DailyScheduler, parse_hhmm
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

    message_format: Literal["text", "markdown", "image"] = Field(
        "text",
        description=(
            "消息格式：text=纯文本；markdown=QQ Markdown（需使用支持 qq_markdown "
            "自定义段的适配器，如 WhiteCloudOL/qq-official-adapter）；"
            "image=长图（需宿主开启浏览器渲染能力）"
        ),
        json_schema_extra={"label": "消息格式"},
    )
    image_width: int = Field(
        760,
        ge=480,
        le=1200,
        description="长图渲染宽度（CSS 像素），QQ 手机端建议 720–800",
        json_schema_extra={"label": "长图宽度（px）"},
    )
    image_scale: float = Field(
        2.0,
        ge=1.0,
        le=3.0,
        description="长图渲染清晰度（设备像素比），2.0 即 2 倍图",
        json_schema_extra={"label": "长图清晰度"},
    )
    image_max_height: int = Field(
        16000,
        ge=0,
        description="长图像素高度上限，超过则回退纯文本；0 表示不限制",
        json_schema_extra={"label": "长图最大高度（px）"},
    )
    image_timeout_ms: int = Field(
        30000,
        ge=3000,
        le=120000,
        description="长图渲染超时（毫秒）",
        json_schema_extra={"label": "长图渲染超时（ms）"},
    )
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
    stagger_min_seconds: int = Field(
        30,
        description="每个目标随机错峰的最小延迟（秒），仅对同一批的第 2 个及以后目标生效",
        json_schema_extra={"label": "错峰最小秒数"},
    )
    stagger_max_seconds: int = Field(
        120,
        description="每个目标随机错峰的最大延迟（秒）",
        json_schema_extra={"label": "错峰最大秒数"},
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
    _PUSH_CONCURRENCY = 8  # 同时在飞的发送数上限，防止瞬时打爆平台
    _MANUAL_COOLDOWN = 60.0  # /早报 同一会话的抓取冷却（秒）
    _STATIC_RETRY_SECONDS = 300  # 静态群解析失败后的重试冷却
    _ADMIN_ROLES = {"admin", "owner", "superuser", "administrator"}

    # ---------- 生命周期 ----------

    async def on_load(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._static_stream_ids: dict[str, str] = {}
        self._static_retry_at = 0.0
        self._pushing: set[str] = set()
        self._push_lock = asyncio.Lock()
        self._manual_fetch_at: dict[str, float] = {}
        self._fetch_task: asyncio.Task | None = None

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

        self._store = Store(os.path.join(data_dir, "state.json"), logger=self.ctx.logger)
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
        fetch_task = getattr(self, "_fetch_task", None)
        if fetch_task is not None and not fetch_task.done():
            fetch_task.cancel()
            tasks.append(fetch_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.ctx.logger.info("橘鸦AI早报插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version) -> None:
        if scope != "self":
            return
        try:
            old_scheduler = getattr(self, "_scheduler", None)
            old_time = getattr(old_scheduler, "push_time", None)
            self._static_retry_at = 0.0
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

    @Command("juya_daily", description="即时拉取并发送最新一期早报", pattern=r"^/早报(?!订阅|退订|状态)")
    async def cmd_daily(self, **kwargs):
        """即时拉取最新一期早报。"""
        stream_id = str(kwargs.get("stream_id") or kwargs.get("session_id") or "")
        now = time.monotonic()
        fetch_times = self.__dict__.setdefault("_manual_fetch_at", {})
        previous = fetch_times.get(stream_id)
        if stream_id and previous is not None and now - previous < self._MANUAL_COOLDOWN:
            await self._reply(kwargs, "刚拉过一次，稍等一分钟再试~")
            return (True, "throttled", 2)
        if stream_id:
            fetch_times[stream_id] = now
        try:
            issue = await self._fetch_latest_shared()
        except Exception as exc:
            self.ctx.logger.exception("/早报 拉取失败")
            await self._reply(kwargs, f"拉取早报出错了：{exc}")
            return (False, str(exc), 2)
        if not issue:
            await self._reply(kwargs, "暂时抓不到早报（网络或站点异常），稍后再试试吧")
            return (False, "no issue", 2)
        self._store.set_last_fetch(issue.to_dict())
        stale = issue.date != today_cst()
        if self._message_format() == "image":
            if stream_id:
                await self._send_image_issue(issue, stream_id, stale=stale)
            else:
                self.ctx.logger.warning("/早报 缺少 stream_id，无法推送长图")
            return (True, "ok", 2)
        message_format, pages = self._render_pages(issue, stale=stale)
        if stream_id:
            for page in pages:
                await self._send_page(page, stream_id, message_format)
        else:
            self.ctx.logger.warning("/早报 缺少 stream_id，只能记录日志：%s", pages[0][:80])
        return (True, "ok", 2)

    @Command("juya_daily_sub", description="订阅每日早报推送", pattern=r"^/早报订阅")
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

    @Command("juya_daily_unsub", description="退订每日早报推送", pattern=r"^/早报退订")
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

    @Command("juya_daily_status", description="查看本会话订阅状态与下次推送时间", pattern=r"^/早报状态")
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
            f"推送时间：每天 {self.config.schedule.push_time}（东八区 UTC+8）",
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

        def _done(finished: asyncio.Task) -> None:
            self._tasks.discard(finished)
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                self.ctx.logger.error(
                    "后台任务 %s 异常退出: %r", finished.get_name(), exc, exc_info=exc
                )

        task.add_done_callback(_done)
        return task

    async def _fetch_latest_shared(self):
        """合并同一时刻的抓取请求：并发 /早报 只打一次站点。"""
        task = getattr(self, "_fetch_task", None)
        if task is None or task.done():
            task = asyncio.create_task(self._fetcher.fetch_latest())
            # 若所有等待者都被取消，避免 "Task exception was never retrieved"
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            self._fetch_task = task
        # shield：某个命令被取消时，不连带取消其他会话共享的抓取
        return await asyncio.shield(task)

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
        # 适配器字段不统一：合并所有信号，任一为真即放行。
        # 不能用「首个命中即返回」——Host 可能在 message 里恒塞 is_admin=False，
        # 而真正的管理员标记在 sender.role。
        signals: list[bool] = []
        holders = [
            kwargs,
            kwargs.get("message"),
            kwargs.get("sender"),
            kwargs.get("user"),
            kwargs.get("member"),
        ]
        for holder in holders:
            if not isinstance(holder, dict):
                continue
            for key in ("is_admin", "is_superuser", "is_bot_admin"):
                if key in holder:
                    signals.append(bool(holder[key]))
            role = holder.get("role")
            if role is not None:
                signals.append(str(role).strip().lower() in self._ADMIN_ROLES)
        if any(signals):
            return True  # 任一信号为真即放行
        if signals:
            return False
        self.ctx.logger.warning("无法判定管理员身份（缺少 is_admin/role 字段），按非管理员处理")
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

    async def _ensure_static_chats(self) -> None:
        """推送前惰性补解析静态群；失败后 5 分钟内不再重试，避免每次推送打 RPC。"""
        if self._static_stream_ids or not (self.config.subscribe.static_chats or []):
            return
        now = time.monotonic()
        if now < getattr(self, "_static_retry_at", 0.0):
            return
        await self._resolve_static_chats()
        if not self._static_stream_ids:
            self._static_retry_at = now + self._STATIC_RETRY_SECONDS

    def _push_targets(self) -> list[str]:
        targets = list(self._store.subscriptions().keys())
        for stream_id in self._static_stream_ids.values():
            if stream_id not in targets:
                targets.append(stream_id)
        return targets

    def _message_format(self) -> str:
        """当前配置的消息格式（text / markdown / image）。"""
        return str(self.config.push.message_format or "text").strip().lower()

    def _render_pages(self, issue: Issue, stale: bool = False) -> tuple[str, list[str]]:
        """按 ``push.message_format`` 渲染文本分页，返回 (格式, 页列表)。"""
        message_format = self._message_format()
        max_items = int(self.config.push.max_items or 0)
        footer_template = self.config.push.footer or DEFAULT_FOOTER
        if message_format == "markdown":
            return "markdown", paginate_issue_markdown(
                issue,
                max_items=max_items,
                footer_template=footer_template,
                stale=stale,
            )
        return "text", paginate_issue(
            issue,
            max_items=max_items,
            footer_template=footer_template,
            stale=stale,
        )

    async def _send_markdown(self, text: str, stream_id: str) -> bool:
        """以 QQ 结构化消息发送一段 Markdown（qq-official-adapter 的 qq_markdown 段）。

        Host 会把 ``custom`` 包成 ``dict`` 消息段，适配器识别到
        ``qq_markdown`` / ``qq_official_markdown`` 后按 ``msg_type=2`` 发出。
        """
        try:
            result = await self.ctx.send.custom(
                "qq_markdown",
                {"markdown": {"content": text}},
                str(stream_id),
                return_details=True,
                # 让宿主把可读正文写进消息/上下文，避免历史里只剩「[qq_markdown消息]」
                processed_plain_text=markdown_to_plain(text),
            )
        except Exception as exc:
            self.ctx.logger.error("Markdown 推送到 %s 失败: %s", stream_id, exc)
            return False
        if isinstance(result, dict):
            sent = bool(result.get("sent"))
            if not sent:
                self.ctx.logger.warning("Markdown 推送到 %s 未成功: %r", stream_id, result)
            return sent
        return bool(result)

    async def _send_page(self, page: str, stream_id: str, message_format: str = "text") -> bool:
        """按格式发送一页；Markdown 失败时自动回退纯文本，避免整期推送失败。"""
        if message_format != "markdown":
            return await self._send_text(page, stream_id)
        if await self._send_markdown(page, stream_id):
            return True
        self.ctx.logger.warning("Markdown 发送失败，回退纯文本：%s", stream_id)
        return await self._send_text(markdown_to_plain(page), stream_id)

    async def _render_html_png(self, html_text: str) -> str:
        """调用宿主浏览器渲染能力，把整篇 HTML 截成一张长图的 base64。

        宿主 ``render.html2png`` 默认返回
        ``{"image_base64", "mime_type", "width", "height", "render_ms"}``；
        能力被禁用、Playwright 缺失或超时都会抛异常，这里统一降级。
        """
        try:
            result = await self.ctx.render.html2png(
                html_text,
                selector="body",
                viewport={"width": int(self.config.push.image_width or 760), "height": 800},
                device_scale_factor=float(self.config.push.image_scale or 2.0),
                full_page=True,
                render_timeout_ms=int(self.config.push.image_timeout_ms or 30000),
            )
        except Exception as exc:
            self.ctx.logger.error("长图渲染失败: %s", exc)
            return ""
        if not isinstance(result, dict):
            self.ctx.logger.error("长图渲染返回异常: %r", result)
            return ""
        image_base64 = str(result.get("image_base64") or "")
        if not image_base64:
            self.ctx.logger.error("长图渲染未返回图片: %r", result)
            return ""
        try:
            height = int(result.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        max_height = int(self.config.push.image_max_height or 0)
        if max_height and height > max_height:
            self.ctx.logger.error(
                "长图高度 %d 超过上限 %d，回退纯文本", height, max_height
            )
            return ""
        self.ctx.logger.info(
            "长图渲染完成：%dx%d，%s ms",
            int(result.get("width") or 0),
            height,
            int(result.get("render_ms") or 0),
        )
        return image_base64

    async def _send_image(self, image_base64: str, stream_id: str) -> bool:
        """发送一张 base64 图片，严格判定 ``send.image`` 结果。"""
        try:
            result = await self.ctx.send.image(
                image_base64, str(stream_id), return_details=True
            )
        except Exception as exc:
            self.ctx.logger.error("长图推送到 %s 失败: %s", stream_id, exc)
            return False
        if isinstance(result, dict):
            sent = bool(result.get("sent"))
            if not sent:
                self.ctx.logger.error("长图推送到 %s 失败: %r", stream_id, result)
            return sent
        return bool(result)

    def _text_pages(self, issue: Issue, stale: bool = False) -> list[str]:
        """长图失败时回退用的纯文本分页（与 ``message_format="text"`` 同源）。"""
        return paginate_issue(
            issue,
            max_items=int(self.config.push.max_items or 0),
            footer_template=self.config.push.footer or DEFAULT_FOOTER,
            stale=stale,
        )

    async def _send_image_issue(self, issue: Issue, stream_id: str, stale: bool = False) -> bool:
        """渲染整期长图并发送；渲染或发送失败时整期回退纯文本。

        供 ``/早报`` 手动命令使用，不写推送进度；定时推送与补推走
        :meth:`_push_image_issue`，回退文本时会逐页续传。
        """
        max_items = int(self.config.push.max_items or 0)
        footer_template = self.config.push.footer or DEFAULT_FOOTER
        html_text = render_issue_html(
            issue,
            max_items=max_items,
            footer_template=footer_template,
            stale=stale,
            width=int(self.config.push.image_width or 760),
        )
        image_base64 = await self._render_html_png(html_text)
        if image_base64 and await self._send_image(image_base64, stream_id):
            return True
        self.ctx.logger.warning("长图推送失败，回退纯文本：%s", stream_id)
        for page in self._text_pages(issue, stale=stale):
            if not await self._send_text(page, stream_id):
                return False
        return True

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

    async def _push_pages(
        self,
        stream_id: str,
        issue: Issue,
        pages: list[str],
        message_format: str,
        start: int = 0,
    ) -> bool:
        """按页发送并逐页落进度；失败时进度停在失败页，重试从该页续传。"""
        if start >= len(pages):
            start = 0  # 内容改版后页数变少，从头再发
        for index in range(start, len(pages)):
            if not await self._send_page(pages[index], stream_id, message_format):
                if index > 0:
                    self.ctx.logger.warning(
                        "推送到 %s 的第 %d/%d 页失败，已发出的页无法撤回，重试将从该页继续",
                        stream_id,
                        index + 1,
                        len(pages),
                    )
                return False  # 进度停在 index
            self._store.set_push_progress(
                stream_id,
                issue.date,
                page=index + 1,
                done=(index + 1 == len(pages)),
            )
        return True

    async def _push_image_issue(self, stream_id: str, issue: Issue, state: dict) -> bool:
        """长图模式推送：整期一张；渲染/发送失败时回退文本分页并支持断点续传。

        回退开始后 ``push_progress.page`` 会按已发出的文本页推进（> 0），
        重试时据此判断「已经进入回退」，跳过长图渲染、只补剩余页，
        避免每轮重渲染并从第 1 页重发造成刷屏。
        """
        pages = self._text_pages(issue)
        if state["date"] == issue.date and state["page"] > 0:
            start = state["page"]
            self.ctx.logger.warning(
                "检测到 %s 已回退纯文本（已发 %d/%d 页），本次跳过渲染，续发剩余页",
                stream_id,
                min(start, len(pages)),
                len(pages),
            )
            return await self._push_pages(stream_id, issue, pages, "text", start=start)
        html_text = render_issue_html(
            issue,
            max_items=int(self.config.push.max_items or 0),
            footer_template=self.config.push.footer or DEFAULT_FOOTER,
            stale=False,
            width=int(self.config.push.image_width or 760),
        )
        image_base64 = await self._render_html_png(html_text)
        if image_base64 and await self._send_image(image_base64, stream_id):
            self._store.set_push_progress(stream_id, issue.date, page=1, done=True)
            return True
        self.ctx.logger.warning("长图推送失败，回退纯文本：%s", stream_id)
        return await self._push_pages(stream_id, issue, pages, "text")

    async def _push_one(self, stream_id: str, issue: Issue) -> bool:
        """推送一期早报（自动分页）；按页记录进度，失败后从断点续传。"""
        pushing = self.__dict__.setdefault("_pushing", set())
        if stream_id in pushing:
            self.ctx.logger.info("%s 已有推送在飞，跳过重复任务", stream_id)
            return True
        pushing.add(stream_id)
        try:
            state = self._store.push_state(stream_id)
            if state["date"] == issue.date and state["done"]:
                return True
            if self._message_format() == "image":
                return await self._push_image_issue(stream_id, issue, state)
            message_format, pages = self._render_pages(issue)
            start = state["page"] if state["date"] == issue.date else 0
            return await self._push_pages(
                stream_id, issue, pages, message_format, start=start
            )
        finally:
            pushing.discard(stream_id)

    async def _push_batch(self, stream_ids: list[str], issue: Issue) -> list[str]:
        """并发推送一批目标；非首个目标在 [stagger_min, stagger_max] 秒内随机延迟。"""
        sem = asyncio.Semaphore(self._PUSH_CONCURRENCY)
        low = max(0, int(self.config.push.stagger_min_seconds or 0))
        high = max(low, int(self.config.push.stagger_max_seconds or low))
        static_values = set(self._static_stream_ids.values())

        async def run(index: int, stream_id: str) -> bool:
            if index:
                await asyncio.sleep(random.uniform(low, high))
            if not self._store.is_subscribed(stream_id) and stream_id not in static_values:
                return True  # 推送间隙被退订
            async with sem:
                return await self._push_one(stream_id, issue)

        results = await asyncio.gather(
            *(run(index, sid) for index, sid in enumerate(stream_ids)),
            return_exceptions=True,
        )
        failed: list[str] = []
        for sid, result in zip(stream_ids, results):
            if isinstance(result, BaseException):
                self.ctx.logger.error("推送 %s 异常: %r", sid, result)
                failed.append(sid)
            elif result is not True:
                failed.append(sid)
        return failed

    async def _push_round(self, due: list[str], issue: Issue) -> None:
        lock = self.__dict__.setdefault("_push_lock", asyncio.Lock())
        if lock.locked():
            self.ctx.logger.info("已有推送轮在进行，跳过本轮")
            return
        # 检查与 acquire 之间没有 await，单线程事件循环下是原子的
        async with lock:
            failed = await self._push_batch(due, issue)
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
            await self._ensure_static_chats()
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
            failed = await self._push_batch(due, issue)
            if failed:
                self.ctx.logger.warning("启动补推 %d 个会话失败: %s", len(failed), ", ".join(failed))
                self._spawn(self._retry_failed(failed, issue), "catch-up-retry")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.ctx.logger.exception("启动补推失败")

    async def _on_schedule_fire(self) -> None:
        await self._ensure_static_chats()
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
        hour, minute = parse_hhmm(
            self.config.push.stale_deadline, (12, 0), self.ctx.logger
        )
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

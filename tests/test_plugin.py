"""plugin 单元测试：命令分发、订阅鉴权、推送成败判定、定时编排。

这些测试不依赖真实 MaiBot 环境：
- 没有 pydantic 时注入一个最小桩（只支持 Field/默认值语义）；
- 没有 maibot_sdk 时注入 Command/MaiBotPlugin/PluginConfigBase 桩。
CI 里装了真包，走的就是真实实现。
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import logging
import os
import re
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(HERE, "..", "plugins", "juya-daily")
sys.path.insert(0, os.path.abspath(PLUGIN_DIR))
sys.path.insert(0, HERE)

# ---------- 依赖桩（仅在真实包缺失时注入） ----------


def _install_stubs() -> None:
    if "pydantic" not in sys.modules:
        try:
            import pydantic  # noqa: F401
        except ImportError:
            pydantic = types.ModuleType("pydantic")

            def Field(default=None, default_factory=None, **_kwargs):  # noqa: N802
                if default is not None:
                    return default
                if default_factory is not None:
                    return default_factory()
                return None

            class BaseModel:
                def __init__(self, **data):
                    for klass in reversed(type(self).__mro__):
                        for key, value in vars(klass).items():
                            if key.startswith("__") or isinstance(value, types.FunctionType):
                                continue
                            try:
                                object.__setattr__(self, key, copy.deepcopy(value))
                            except Exception:
                                object.__setattr__(self, key, value)
                    for key, value in data.items():
                        default = getattr(type(self), key, None)
                        if isinstance(value, dict) and isinstance(default, BaseModel):
                            object.__setattr__(self, key, type(default)(**value))
                        else:
                            object.__setattr__(self, key, value)

            pydantic.Field = Field
            pydantic.BaseModel = BaseModel
            sys.modules["pydantic"] = pydantic

    if "maibot_sdk" not in sys.modules:
        try:
            import maibot_sdk  # noqa: F401
        except ImportError:
            from pydantic import BaseModel as _BaseModel

            sdk = types.ModuleType("maibot_sdk")

            class _ComponentInfo:
                """对齐 SDK 的 CommandComponentInfo：只留测试用到的字段。"""

                def __init__(self, name, description="", command_pattern="", aliases=None, metadata=None):
                    self.name = name
                    self.description = description
                    self.command_pattern = command_pattern
                    self.aliases = aliases or []
                    self.metadata = metadata or {}

            def Command(name, description="", pattern="", aliases=None, **metadata):  # noqa: N802
                def decorator(func):
                    setattr(
                        func,
                        "__maibot_component_info__",
                        _ComponentInfo(name, description, pattern, aliases, metadata),
                    )
                    return func

                return decorator

            class PluginConfigBase(_BaseModel):
                pass

            class MaiBotPlugin:
                """对齐真实 SDK 的可见面：ctx/config 是只读属性，靠注入方法赋值。"""

                config_reload_subscriptions = ()
                config_model = None

                def __init__(self):
                    self._ctx = None
                    self._plugin_config_data = {}
                    self._plugin_config_instance = None

                def _set_context(self, ctx):
                    self._ctx = ctx

                def set_plugin_config(self, config):
                    self._plugin_config_data = dict(config or {})
                    config_class = type(self).get_config_model()
                    self._plugin_config_instance = (
                        config_class(**self._plugin_config_data) if config_class else None
                    )

                @classmethod
                def get_config_model(cls):
                    return cls.config_model

                @classmethod
                def has_config_model(cls):
                    return cls.get_config_model() is not None

                @property
                def ctx(self):
                    if self._ctx is None:
                        raise RuntimeError("插件上下文尚未初始化")
                    return self._ctx

                @property
                def config(self):
                    if not type(self).has_config_model():
                        raise RuntimeError("当前插件未声明 config_model")
                    if self._plugin_config_instance is None:
                        raise RuntimeError("当前插件配置尚未完成注入")
                    return self._plugin_config_instance

            sdk.Command = Command
            sdk.PluginConfigBase = PluginConfigBase
            sdk.MaiBotPlugin = MaiBotPlugin
            sys.modules["maibot_sdk"] = sdk


_install_stubs()

_spec = importlib.util.spec_from_file_location(
    "juya_plugin_under_test", os.path.join(PLUGIN_DIR, "plugin.py")
)
plugin_module = importlib.util.module_from_spec(_spec)
# 必须先注册进 sys.modules 再 exec：pydantic 等库解析注解时要靠
# sys.modules[cls.__module__] 拿模块命名空间（真实 import 会自动注册）。
sys.modules[_spec.name] = plugin_module
_spec.loader.exec_module(plugin_module)

from fetcher import Issue  # noqa: E402
from store import Store  # noqa: E402

JuyaDailyPlugin = plugin_module.JuyaDailyPlugin
JuyaDailyConfig = plugin_module.JuyaDailyConfig
create_plugin = plugin_module.create_plugin

LOGGER = logging.getLogger("test-plugin")
LOGGER.addHandler(logging.NullHandler())


def make_issue(date: str, title: str = "橘鸦AI早报") -> Issue:
    body = "\n\n".join(
        f"第 {i} 条标题 #{i}\n这里是第 {i} 条正文，" + "细节内容" * 60 for i in range(1, 8)
    )
    return Issue(
        title=f"{title} {date}",
        link=f"https://daily.juya.uk/issue-{date.replace('-', '')}/",
        date=date,
        pub_dt=datetime.now(timezone.utc),
        content=body,
        source="rss",
    )


class FakeSend:
    """记录发送内容，可按 stream_id 模拟失败/抛异常。"""

    def __init__(self, fail_streams=(), raise_streams=(), fail_pages=()):
        self.fail_streams = set(fail_streams)
        self.raise_streams = set(raise_streams)
        self.fail_pages = set(fail_pages)  # 第几页失败（1 起）
        self.calls: list[tuple[str, str]] = []

    async def text(self, text, stream_id, *, return_details=False, **_kwargs):
        index = len([c for c in self.calls if c[0] == stream_id]) + 1
        self.calls.append((str(stream_id), text))
        if stream_id in self.raise_streams:
            raise RuntimeError("发送通道炸了")
        if stream_id in self.fail_streams or index in self.fail_pages:
            return {"sent": False, "message_id": None} if return_details else False
        return {"sent": True, "message_id": f"m{len(self.calls)}"} if return_details else True


class FakeChat:
    def __init__(self, streams=None):
        self.streams = dict(streams or {})
        self.queries: list[tuple[str, str]] = []

    async def get_stream_by_group_id(self, group_id, platform="qq"):
        self.queries.append((str(group_id), platform))
        return self.streams.get(str(group_id))


class FakePaths:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir


class FakeCtx:
    def __init__(self, data_dir: str, send=None, chat=None):
        self.paths = FakePaths(data_dir)
        self.send = send or FakeSend()
        self.chat = chat if chat is not None else FakeChat()
        self.logger = LOGGER


def dump_config(config) -> dict:
    """把配置实例转成嵌套 dict（真 pydantic 与桩都要能转）。"""
    if hasattr(config, "model_dump"):
        return config.model_dump()
    dumped: dict = {}
    for key, value in vars(config).items():
        if hasattr(value, "model_dump"):
            dumped[key] = value.model_dump()
        else:
            dumped[key] = value
    return dumped


def wire(plugin, ctx, config) -> None:
    """按 SDK 的注入接口装配 ctx 与 config（兼容真实 SDK 与桩）。"""
    if hasattr(plugin, "_set_context"):
        plugin._set_context(ctx)
    else:  #  pragma: no cover - 桩之外的异常布局
        plugin.ctx = ctx
    if hasattr(plugin, "set_plugin_config"):
        plugin.set_plugin_config(dump_config(config))
    else:  # pragma: no cover
        plugin.config = config


def build_plugin(tmp: str, send=None, chat=None, **config_fields) -> JuyaDailyPlugin:
    """绕过 on_load 直接装配，避免测试里起真实定时器和网络请求。"""
    plugin = create_plugin()
    wire(plugin, FakeCtx(tmp, send=send, chat=chat), JuyaDailyConfig(**config_fields))
    plugin._tasks = set()
    plugin._static_stream_ids = {}
    plugin._store = Store(os.path.join(tmp, "state.json"))
    plugin._fetcher = plugin_module.JuyaFetcher(LOGGER)
    return plugin


class _NoStaggerRandom:
    """代理 random，把错峰用的 uniform 变成 0，避免测试真等 30~120 秒。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def uniform(self, a, b):
        return 0


def no_stagger():
    """上下文：临时关掉插件里的随机错峰等待。"""
    return _patch(plugin_module, "random", _NoStaggerRandom(plugin_module.random))


class _patch:
    def __init__(self, module, attr, value):
        self.module = module
        self.attr = attr
        self.value = value
        self.old = None

    def __enter__(self):
        self.old = getattr(self.module, self.attr)
        setattr(self.module, self.attr, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.module, self.attr, self.old)
        return False


class EntryPointTest(unittest.TestCase):
    def test_create_plugin(self) -> None:
        self.assertIsInstance(create_plugin(), JuyaDailyPlugin)

    def test_manifest_and_config_version_declared(self) -> None:
        """Runner 会读 config_data['plugin']['config_version']，缺失就报错。"""
        import json

        with open(os.path.join(PLUGIN_DIR, "_manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["manifest_version"], 2)
        for key in ("id", "version", "name", "sdk", "host_application", "capabilities"):
            self.assertIn(key, manifest)
        config = JuyaDailyConfig()
        self.assertIsInstance(config.plugin.config_version, str)
        self.assertTrue(config.plugin.config_version)


class CommandRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = build_plugin(tempfile.mkdtemp())

    def _commands(self) -> dict:
        """收集所有 @Command 声明的命令 -> 正则（真实 SDK 与桩用同一属性名）。"""
        found = {}
        for name in dir(JuyaDailyPlugin):
            attr = getattr(JuyaDailyPlugin, name)
            if not callable(attr):
                continue
            info = getattr(attr, "__maibot_component_info__", None)
            if info is not None:
                found[info.name] = info.command_pattern
        return found

    def test_all_commands_registered(self) -> None:
        self.assertEqual(
            set(self._commands()),
            {"juya_daily", "juya_daily_sub", "juya_daily_unsub", "juya_daily_status"},
        )

    def test_patterns_do_not_overlap(self) -> None:
        patterns = self._commands()
        self.assertIsNone(re.match(patterns["juya_daily"], "/早报订阅"))
        self.assertIsNone(re.match(patterns["juya_daily"], "/早报退订"))
        self.assertIsNone(re.match(patterns["juya_daily"], "/早报状态"))
        self.assertIsNotNone(re.match(patterns["juya_daily"], "/早报"))
        self.assertIsNotNone(re.match(patterns["juya_daily_sub"], "/早报订阅"))
        self.assertIsNotNone(re.match(patterns["juya_daily_unsub"], "/早报退订"))
        self.assertIsNotNone(re.match(patterns["juya_daily_status"], "/早报状态"))

    def test_handler_returns_triple(self) -> None:
        async def scenario() -> None:
            today = plugin_module.today_cst()

            async def fake_fetch(force=False):
                return make_issue(today)

            self.plugin._fetcher.fetch_latest = fake_fetch  # type: ignore[assignment]
            result = await self.plugin.cmd_daily(stream_id="s1")
            self.assertEqual(len(result), 3)
            self.assertIsInstance(result[0], bool)
            self.assertTrue(result[0])

        asyncio.run(scenario())


class SdkContractTest(unittest.TestCase):
    """锁定 SDK 注入契约：真实 SDK 与桩都必须经注入方法赋值，不能直接属性赋值。"""

    def test_ctx_and_config_are_read_only(self) -> None:
        plugin = create_plugin()
        with self.assertRaises(AttributeError):
            plugin.ctx = FakeCtx(tempfile.mkdtemp())
        with self.assertRaises(AttributeError):
            plugin.config = JuyaDailyConfig()

    def test_wire_injects_ctx_and_config(self) -> None:
        tmp = tempfile.mkdtemp()
        plugin = create_plugin()
        wire(plugin, FakeCtx(tmp), JuyaDailyConfig(schedule={"push_time": "06:00"}))
        self.assertEqual(plugin.config.schedule.push_time, "06:00")
        self.assertIs(plugin.ctx.paths.data_dir, tmp)

    def test_config_readable_before_injection_raises(self) -> None:
        plugin = create_plugin()
        with self.assertRaises(RuntimeError):
            _ = plugin.config

    def test_plugin_module_registered_in_sys_modules(self) -> None:
        # 记录加载方式的前提：pydantic 解析注解需要模块已注册
        self.assertIn("juya_plugin_under_test", sys.modules)
        self.assertIs(sys.modules["juya_plugin_under_test"], plugin_module)


class ConfigModelTest(unittest.TestCase):
    """配置模型必须在「真实 pydantic」下开箱可实例化。

    回归：plugin.py 曾有 `from __future__ import annotations`，注解全变字符串，
    加载器若没把模块注册进 sys.modules，pydantic 报
    "JuyaDailyConfig is not fully defined; you should define PluginSection"，
    插件在真 Runner 里根本起不来。CI 首跑就是因此 26 个 ERROR。
    """

    def test_default_instantiable(self) -> None:
        cfg = JuyaDailyConfig()
        self.assertTrue(cfg.plugin.enabled)
        self.assertEqual(cfg.schedule.push_time, "09:30")
        self.assertEqual(cfg.push.max_items, 0)

    def test_instantiable_with_partial_override(self) -> None:
        cfg = JuyaDailyConfig(schedule={"push_time": "07:15", "catch_up_on_start": False})
        self.assertEqual(cfg.schedule.push_time, "07:15")
        self.assertFalse(cfg.schedule.catch_up_on_start)

    def test_not_pending_model_rebuild(self) -> None:
        """真实 pydantic 下：未完全定义的模型一实例化就抛 PydanticUserError。"""
        try:
            JuyaDailyConfig(subscribe={"static_chats": ["123"]})
        except Exception as exc:  # pragma: no cover - 只在回归时触发
            self.fail(f"JuyaDailyConfig 无法实例化（注解未解析？）: {exc!r}")

    def test_plugin_source_has_no_future_annotations(self) -> None:
        with open(os.path.join(PLUGIN_DIR, "plugin.py"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        offenders = [
            f"{number}: {line}"
            for number, line in enumerate(lines, start=1)
            if re.match(r"^from __future__ import annotations\s*(#.*)?$", line)
        ]
        self.assertEqual(
            offenders,
            [],
            "plugin.py 不能启用 PEP 563：配置节注解必须能被 pydantic 直接解析",
        )


class SubscribeCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.plugin = build_plugin(self.tmp)

    def test_subscribe_and_unsubscribe(self) -> None:
        async def scenario() -> None:
            ok, _, _ = await self.plugin.cmd_subscribe(stream_id="s1", group_id="10001")
            self.assertTrue(ok)
            self.assertTrue(self.plugin._store.is_subscribed("s1"))
            ok, _, _ = await self.plugin.cmd_unsubscribe(stream_id="s1")
            self.assertTrue(ok)
            self.assertFalse(self.plugin._store.is_subscribed("s1"))

        asyncio.run(scenario())

    def test_subscribe_without_stream_id(self) -> None:
        async def scenario() -> None:
            ok, reason, _ = await self.plugin.cmd_subscribe()
            self.assertFalse(ok)
            self.assertEqual(reason, "no stream_id")

        asyncio.run(scenario())

    def test_admin_only(self) -> None:
        plugin = build_plugin(tempfile.mkdtemp(), subscribe={"admin_only": True})
        self.assertTrue(plugin._check_admin({"is_admin": True}))
        self.assertFalse(plugin._check_admin({"is_admin": False}))
        self.assertFalse(plugin._check_admin({}))  # 判定不了就拒绝
        self.assertTrue(plugin._check_admin({"message": {"role": "owner"}}))
        self.assertFalse(plugin._check_admin({"message": {"role": "member"}}))

    def test_admin_only_blocks_command(self) -> None:
        plugin = build_plugin(tempfile.mkdtemp(), subscribe={"admin_only": True})

        async def scenario() -> None:
            ok, reason, _ = await plugin.cmd_subscribe(stream_id="s1")
            self.assertFalse(ok)
            self.assertEqual(reason, "admin only")
            self.assertFalse(plugin._store.is_subscribed("s1"))

        asyncio.run(scenario())

    def test_status_command(self) -> None:
        async def scenario() -> None:
            self.plugin._store.subscribe("s1", "10001")
            ok, _, _ = await self.plugin.cmd_status(stream_id="s1")
            self.assertTrue(ok)
            sent = self.plugin.ctx.send.calls
            self.assertTrue(sent)
            self.assertIn("本会话订阅：是", sent[-1][1])

        asyncio.run(scenario())


class PushTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def test_success_marks_pushed(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")
            ok = await plugin._push_one("s1", make_issue("2026-10-19"))
            self.assertTrue(ok)
            self.assertEqual(plugin._store.last_pushed_date("s1"), "2026-10-19")
            text = plugin.ctx.send.calls[-1][1]
            self.assertIn("来源：橘鸦AI早报", text)
            self.assertIn("AI辅助生成", text)

        asyncio.run(scenario())

    def test_return_false_is_failure(self) -> None:
        """send.text 返回 False 不算成功，否则 last_pushed 会被写死。"""

        async def scenario() -> None:
            plugin = build_plugin(self.tmp, send=FakeSend(fail_streams={"s1"}))
            plugin._store.subscribe("s1", "10001")
            self.assertFalse(await plugin._push_one("s1", make_issue("2026-10-19")))
            self.assertIsNone(plugin._store.last_pushed_date("s1"))

        asyncio.run(scenario())

    def test_exception_is_failure(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp, send=FakeSend(raise_streams={"s1"}))
            self.assertFalse(await plugin._push_one("s1", make_issue("2026-10-19")))
            self.assertIsNone(plugin._store.last_pushed_date("s1"))

        asyncio.run(scenario())

    def test_partial_failure_does_not_mark_pushed(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp, send=FakeSend(fail_pages={2}))
            plugin._store.subscribe("s1", "10001")
            self.assertFalse(await plugin._push_one("s1", make_issue("2026-10-19")))
            self.assertIsNone(plugin._store.last_pushed_date("s1"))
            self.assertGreaterEqual(len(plugin.ctx.send.calls), 2)  # 第一页确实已发出

        asyncio.run(scenario())

    def test_static_group_gets_progress_without_subscription(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(
                self.tmp,
                chat=FakeChat({"10001": {"stream_id": "static-1"}}),
                subscribe={"static_chats": ["10001"]},
            )
            await plugin._resolve_static_chats()
            self.assertEqual(plugin._push_targets(), ["static-1"])
            issue = make_issue("2026-10-19")
            await plugin._push_round(["static-1"], issue)
            self.assertEqual(plugin._store.last_pushed_date("static-1"), "2026-10-19")
            self.assertFalse(plugin._store.is_subscribed("static-1"))

        asyncio.run(scenario())

    def test_push_round_skips_unsubscribed_midway(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")
            plugin._store.subscribe("s2", "10002")
            plugin._store.unsubscribe("s2")  # 推送前退订
            await plugin._push_round(["s1", "s2"], make_issue("2026-10-19"))
            pushed = {stream_id for stream_id, _ in plugin.ctx.send.calls}
            self.assertEqual(pushed, {"s1"})

        asyncio.run(scenario())

    def test_retry_failed_eventually_succeeds(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            slept: list[float] = []

            class _NoSleepAsyncio:
                """代理 asyncio，只把 sleep 变成记账，避免测试真等 10 分钟。"""

                def __init__(self, real):
                    self._real = real

                def __getattr__(self, name):
                    return getattr(self._real, name)

                async def sleep(self, delay):
                    slept.append(delay)

            real_asyncio = plugin_module.asyncio
            plugin_module.asyncio = _NoSleepAsyncio(real_asyncio)  # type: ignore[assignment]
            try:
                issue = make_issue("2026-10-19")
                calls = {"n": 0}

                async def flaky(stream_id, text):
                    calls["n"] += 1
                    return calls["n"] >= 2

                plugin._send_text = flaky  # type: ignore[assignment]
                await plugin._retry_failed(["s1"], issue)
            finally:
                plugin_module.asyncio = real_asyncio
            self.assertEqual(plugin._store.last_pushed_date("s1"), "2026-10-19")
            self.assertEqual(slept, [600, 600])  # 第 1 轮失败 → 第 2 轮重试成功

        asyncio.run(scenario())


class ScheduleOrchestrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def test_fire_skips_when_all_pushed(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")
            today = plugin_module.today_cst()
            plugin._store.set_last_pushed("s1", today)
            await plugin._on_schedule_fire()
            self.assertEqual(plugin.ctx.send.calls, [])

        asyncio.run(scenario())

    def test_fire_pushes_today_issue(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")
            today = plugin_module.today_cst()

            async def fake_fetch(force=False):
                return make_issue(today)

            plugin._fetcher.fetch_latest = fake_fetch  # type: ignore[assignment]
            await plugin._on_schedule_fire()
            self.assertEqual(plugin._store.last_pushed_date("s1"), today)
            self.assertTrue(plugin.ctx.send.calls)

        asyncio.run(scenario())

    def test_fire_waits_when_issue_not_today(self) -> None:
        """抓到的是旧一期时不能推送，要进入等待循环直到过期。"""

        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")
            plugin._WAIT_INTERVAL_SECONDS = 0  # type: ignore[misc]

            async def fake_fetch(force=False):
                return make_issue("2020-01-01")

            plugin._fetcher.fetch_latest = fake_fetch  # type: ignore[assignment]
            # 第一次看是早上 8 点，之后直接跳到下午 1 点（超过放弃时间）
            clock = iter(
                [
                    datetime(2026, 10, 19, 8, 0, tzinfo=timezone(timedelta(hours=8))),
                    datetime(2026, 10, 19, 13, 0, tzinfo=timezone(timedelta(hours=8))),
                    datetime(2026, 10, 19, 13, 0, tzinfo=timezone(timedelta(hours=8))),
                ]
            )
            original_now_cst = plugin_module.now_cst
            plugin_module.now_cst = lambda: next(clock)  # type: ignore[assignment]
            try:
                await plugin._on_schedule_fire()
            finally:
                plugin_module.now_cst = original_now_cst
            self.assertIsNone(plugin._store.last_pushed_date("s1"))
            self.assertEqual(plugin.ctx.send.calls, [])

        asyncio.run(scenario())

    def test_catch_up_pushes_when_today_available(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp, schedule={"catch_up_on_start": True})
            plugin._store.subscribe("s1", "10001")
            today = plugin_module.today_cst()

            async def fake_fetch(force=False):
                return make_issue(today)

            plugin._fetcher.fetch_latest = fake_fetch  # type: ignore[assignment]
            with no_stagger():
                await plugin._catch_up_on_start()
            self.assertEqual(plugin._store.last_pushed_date("s1"), today)

        asyncio.run(scenario())

    def test_catch_up_skips_old_issue(self) -> None:
        async def scenario() -> None:
            plugin = build_plugin(self.tmp)
            plugin._store.subscribe("s1", "10001")

            async def fake_fetch(force=False):
                return make_issue("2020-01-01")

            plugin._fetcher.fetch_latest = fake_fetch  # type: ignore[assignment]
            with no_stagger():
                await plugin._catch_up_on_start()
            self.assertIsNone(plugin._store.last_pushed_date("s1"))
            self.assertEqual(plugin.ctx.send.calls, [])

        asyncio.run(scenario())


class LifecycleTest(unittest.TestCase):
    def test_load_and_unload(self) -> None:
        tmp = tempfile.mkdtemp()

        async def scenario() -> None:
            plugin = create_plugin()
            wire(plugin, FakeCtx(tmp), JuyaDailyConfig(schedule={"catch_up_on_start": False}))
            await plugin.on_load()
            self.assertIsNotNone(plugin._scheduler.next_run())
            self.assertTrue(os.path.isdir(tmp))
            self.assertEqual(plugin._store.subscriptions(), {})
            await plugin.on_unload()
            self.assertIsNone(plugin._scheduler.next_run())

        asyncio.run(scenario())

    def test_config_update_restarts_scheduler_on_time_change(self) -> None:
        tmp = tempfile.mkdtemp()

        async def scenario() -> None:
            plugin = create_plugin()
            wire(
                plugin,
                FakeCtx(tmp),
                JuyaDailyConfig(schedule={"catch_up_on_start": False, "push_time": "08:30"}),
            )
            await plugin.on_load()
            old = plugin._scheduler
            plugin.config.schedule.push_time = "07:15"
            await plugin.on_config_update("self", {}, 1)
            self.assertIsNot(plugin._scheduler, old)
            self.assertEqual(plugin._scheduler.push_time, "07:15")
            self.assertIsNone(old.next_run())  # 旧定时器已停
            await plugin.on_unload()

        asyncio.run(scenario())

    def test_config_update_ignores_other_scopes(self) -> None:
        tmp = tempfile.mkdtemp()

        async def scenario() -> None:
            plugin = create_plugin()
            wire(plugin, FakeCtx(tmp), JuyaDailyConfig(schedule={"catch_up_on_start": False}))
            await plugin.on_load()
            old = plugin._scheduler
            await plugin.on_config_update("bot", {}, 1)
            self.assertIs(plugin._scheduler, old)
            await plugin.on_unload()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

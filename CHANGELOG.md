# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.1] - 2026-10-19

### 修复

- **Release 安装包多套一层目录，WebUI「从 ZIP 安装插件」被拒**：0.3.0 用 `zip -r juya-daily-0.3.0.zip plugins/juya-daily` 打成 `plugins/juya-daily/…`，而宿主只认「ZIP 根目录或唯一一层插件目录」里的 `_manifest.json`，于是报「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」。现在固定打成 `juya-daily/…`，解压即得插件目录内容

### 工程化

- 新增 `scripts/package_plugin.py`：纯标准库打包 + 按 MaiBot 规则校验（唯一一层插件目录、`_manifest.json`/`plugin.py` 位置、100 MB / 解压 300 MB / 10000 文件上限、拒绝 `../`、符号链接、加密条目、`.git`/`__MACOSX`），固定时间戳保证同内容 sha256 一致；本地与 CI 共用同一套规则
- CI 打包步骤改用该脚本并在发布前复核结构，旧的双层包会被直接拦下
- 新增 `tests/test_packaging.py`（20 项）：钉住包布局、确定性与各类拒绝路径；测试总数 145 → 165
- `USER_AGENT` 的兜底版本收敛为 `_FALLBACK_VERSION`，不再散落魔法字符串

## [0.3.0] - 2026-10-19

### 新增

- **并发推送 + 可配置错峰**：多目标用 `asyncio.Semaphore(8)` 并发发起，非首个目标随机等待 `stagger_min_seconds`～`stagger_max_seconds`（默认 30–120 秒），20 个群不再串行线性累加
- **多页断点续传（state v3）**：每群推送进度记录为 `{date, page, done}`，中途失败后重试只补剩余页；旧版 v2 的日期字符串会自动迁移为 `done=true`，v1 仍可迁移
- **并发去重**：进程内 `_pushing` 集合 + 轮级 `_push_lock`，同一目标同时只有一条推送在飞；发送前复查 done，避免定时任务与手动 `/早报` 撞车重复发
- **`/早报` 冷却与共享抓取**：每会话 60 秒冷却，并发请求共享同一个抓取 task，避免连点触发多次 RSS 请求与重复刷屏
- **静态群惰性补解析**：启动时解析失败的 `static_chats` 在后续定时/手动推送前重试，失败冷却 300 秒

### 修复

- **`_spawn` 吞掉后台任务异常**：done 回调现在取出异常并 `logger.error`，重试/补推任务崩溃不再无声无息
- **管理员判定只认第一个信号**：`_check_admin` 合并 `message.is_admin`、`sender.role`、`role` 等多个信号，任一为真即放行
- **状态文件写盘瞬时错误被当损坏重置**：`OSError` 现在保留原文件并记 warning，只有 JSON 解析失败才备份 `.corrupt`；未来版本备份为 `.future`，日志明确提示
- **`get_last_fetch()` 返回内部可变字典**：改为深拷贝，避免 fetcher 原地修改绕过 `save()`
- **超大响应无上限**：RSS/HTML 单次读取上限 10 MiB，超限抛 `ResponseTooLarge` 并立即失败，不再重试

### 改进

- 4 条命令补齐 `description`，WebUI/帮助列表不再显示空白
- `_parse_hhmm` 收敛到 `scheduler.parse_hhmm`，插件与定时器共用一套回退逻辑
- `USER_AGENT` 跟随 `_manifest.json` 版本，站点侧版本统计不再停留在 0.1
- 文档统一为「`push_time` 固定东八区（UTC+8）」，补充降级丢状态、信任边界与实测版本说明

### 工程化

- CI 最小权限（默认只读、package 单独 `contents: write`）、`concurrency` 取消重复运行、job 超时 10 分钟
- 新增 `ruff check --select E9,F63,F7,F82` 与 `coverage report --fail-under=80` 门禁
- 打包前断言 `v$manifest_version == $GITHUB_REF_NAME`，防止 tag 与 manifest 版本不一致
- 夜间真实站点冒烟失败时自动 `gh issue create`，不再永远绿色
- 依赖加上界（`pydantic<3`、`maibot-plugin-sdk>=2.14,<3`），`.gitignore` 忽略本地 `config.toml`
- 测试从 110 项增至 145 项：新增 scheduler 循环、fetcher HTML 兜底/缓存/体积上限、store v2/v3 迁移与容错、断点续传、并发去重、冷却、静态群恢复、`_wait_for_today_issue` 成功路径、`_push_round` 失败分支

### 升级注意

- 状态自动从 v1/v2 迁移到 v3；**降级回 0.2.x 会把 v3 状态当未来版本备份并重置订阅**，升级前请保留 `state.json`
- 去重与轮级锁是进程内状态：同一 `data_dir` 只允许一个 MaiBot 实例，多实例请分开数据目录

## [0.2.1] - 2026-10-19

### 修复

- **`max_items` 把导语算成第 1 条**：新增 `split_issue()` 把导语与编号条目分开，限条数只作用于条目；`split_items()` 保留旧签名（导语仍在首位）兼容调用方
- **分页用 `text[:1800]` 硬截断导致丢正文、丢脚注**：改为 `_pack_pages()` 预装页并预留页码预算，正文与脚注永不截断；超长标题只截标题本身，脚注全页合计恰好出现 1 次
- **概览与分组小标题残留**（如「概览」「要闻」「技术与洞察」）：`_strip_noise()` 改为两轮「先分组标题、再概览行」，并剥掉重复的站点标题行；短正文行/短链接不会被误删

### 改进

- 删除死代码 `format_issue()`（`plugin.py` 只导入未使用），渲染入口收敛为 `paginate_issue()`
- 新增真实一期 fixture（`tests/fixtures/rss_2026-10-19.txt`）与分页不变量测试，另加固定种子 200 例 fuzz（`tests/test_formatter_fuzz.py`）

## [0.2.0] - 2026-10-19

### 新增

- **单元测试套件**（`tests/`，91 项，`python3 -m unittest discover -s tests`）：
  - `test_fetcher`：RSS/Atom 宽容解析、ISO 8601 pubDate、噪音清洗、日期抽取、新旧两种 issue 链接格式
  - `test_formatter`：分条/限条数/分页不超 1800 字、脚注占位符安全替换、概览索引行与站点页脚剥离
  - `test_store`：订阅/退订、推送进度与订阅表分离、v1→v2 迁移、损坏文件备份
  - `test_plugin`：命令正则不粘连、订阅鉴权、推送成败严格判定（返回 False/抛异常/半程失败都不写进度）、静态群进度、定时编排（今日已推跳过 / 旧一期不推 / 启动补推）、配置热更新重启定时器、SDK 注入契约（`ctx`/`config` 只读、经 `_set_context` / `set_plugin_config` 注入）
  - `test_live_rss`：真实站点集成冒烟（`JUYA_LIVE=1` 开启，默认跳过）
  - 不依赖真实 MaiBot：缺 `pydantic` / `maibot_sdk` 时自动注入与真实 SDK 可见面对齐的最小桩，CI 装真包（见 `requirements-dev.txt`）
- **CI 重构**：`.github/workflows/ci.yml` 在 push/PR 跑「真实 SDK + 单元测试」，每晚定时跑一次真实站点冒烟；打包发 Release 改为 `needs: test`，测试不过不发版

### 修复

- **概览索引行剥离不完整**：站点实际格式是「标题 ↗ #N」（箭头在编号前），原正则只匹配「标题 #N ↗」，导致推送里混入与正文重复的概览、`split_items` 多切出一段
- **ISO 8601 pubDate 解析失败**：Atom 的 `published`/`updated`（含 `Z` 后缀）会回退成 1970，可能让旧条目抢占「最新一期」
- **正文清洗残留行首空格**：`\n[ \t]+` 未归一化，分页后出现行首缩进
- **适配站点链接改版**：详情页从 `/issue-N/` 改为 `/issues/YYYY-MM-DD/`，HTML 兜底的正则已两种都认（RSS 主链路不受影响）
- **分条前先剥离噪音**：原来只在切片后清理，分组小标题（如「要闻」）会残留在开头
- **CI 无法启动**：`actions/setup-python` 的 `cache: pip` 让「Setup Python 3.12」这一步直接失败（v0.2.0 首跑实证：test job 从没跑起来、打包被 `needs: test` 跳过、Release 未生成）；已去掉 `cache`，依赖收敛到 `requirements-dev.txt`
- **测试桩与真实 SDK 脱节**：桩把 `ctx`/`config` 当普通属性、`Command` 不写 `__maibot_component_info__`，本地跑过的代码 CI 里（真实 SDK）行为不同；桩改为对齐真实可见面（只读属性 + `_set_context` / `set_plugin_config` 注入），并加 `SdkContractTest` 锁死该契约
- **配置模型在真 pydantic 下无法实例化**：`plugin.py` 启用了 `from __future__ import annotations`，所有注解变成字符串，pydantic 要靠 `sys.modules[__name__]` 才能解析 `PluginSection`/`ScheduleSection` 等配置节；加载器若用 `spec_from_file_location` 却没把模块注册进 `sys.modules`，`JuyaDailyConfig(...)` 直接抛 `PydanticUserError: not fully defined; you should define PluginSection`，插件在真 Runner 里根本起不来（CI 首跑即 26 个 ERROR）。已移除 PEP 563，并加 `ConfigModelTest` 回归

## [0.1.1] - 2026-10-19

### 修复

- **静态订阅群重复推送**：推送进度（已推日期）独立于订阅表持久化，静态群（`static_chats` 配置）也会记录「今天已推过」，修复重启补推时同一期重复发送给静态群的问题
- **RSS 期号日期判定**：`date` 优先从标题/正文抽取，pubDate 仅作兜底，修复站点调整发布时间后「今天的一期」误判为未发布、白等到 `stale_deadline`
- **pubDate 解析失败回退值**改为过去时间（1970），避免异常条目抢占「最新一期」

### 改进

- 启动补推改为并发发起、各自随机延迟 30–120 秒（原来串行，群多时总耗时线性累加），失败的重试沿用 `retry_minutes × retry_times`
- 推送文本剥离纯噪音：概览索引行（与正文重复）、站点页脚、视频版行、分组小标题
- `Store` 所有读写加锁；损坏状态文件仍会备份 `.corrupt` 后重建
- 状态文件 v1 → v2 自动迁移（订阅表内的旧推送进度搬到 `push_progress`）
- `ctx.paths.data_dir` 不可用时打 warning 日志
- `_manifest.json`：作者改为 Beiyu114514，仓库地址指向 GitHub

[0.3.0]: https://github.com/Beiyu114514/maibot-juya-daily/releases/tag/v0.3.0
[0.2.1]: https://github.com/Beiyu114514/maibot-juya-daily/releases/tag/v0.2.1
[0.1.1]: https://github.com/Beiyu114514/maibot-juya-daily/releases/tag/v0.1.1
[0.2.0]: https://github.com/Beiyu114514/maibot-juya-daily/releases/tag/v0.2.0

## [0.1.0] - 2026-10-19

### 新增

- 橘鸦AI早报每日 AI 资讯推送（数据源 `https://daily.juya.uk/rss.xml`，HTML 解析仅兜底）
- `/早报` 命令：即时抓取最新一期并以纯文本发到当前会话
- 群内自助订阅：`/早报订阅`、`/早报退订`、`/早报状态`
- 每日定时推送（`push_time`，支持 WebUI 热修改）、启动补推（错峰 30–120 秒）
- 推送容错：失败按 `retry_minutes × retry_times` 重试；当天早报未发布时每 30 分钟重查至 `stale_deadline`
- 订阅状态、每群最后推送日期、最近一期缓存落盘（原子写），防止重启后重复推送
- GitHub Actions：tag `v*` 自动语法检查 + 打包 zip + 发布 Release（含 sha256 校验文件）

[0.1.0]: https://github.com/Beiyu114514/maibot-juya-daily/releases/tag/v0.1.0

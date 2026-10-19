# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-10-19

### 新增

- **单元测试套件**（`tests/`，83 项，`python3 -m unittest discover -s tests`）：
  - `test_fetcher`：RSS/Atom 宽容解析、ISO 8601 pubDate、噪音清洗、日期抽取、新旧两种 issue 链接格式
  - `test_formatter`：分条/限条数/分页不超 1800 字、脚注占位符安全替换、概览索引行与站点页脚剥离
  - `test_store`：订阅/退订、推送进度与订阅表分离、v1→v2 迁移、损坏文件备份
  - `test_plugin`：命令正则不粘连、订阅鉴权、推送成败严格判定（返回 False/抛异常/半程失败都不写进度）、静态群进度、定时编排（今日已推跳过 / 旧一期不推 / 启动补推）、配置热更新重启定时器
  - `test_live_rss`：真实站点集成冒烟（`JUYA_LIVE=1` 开启，默认跳过）
  - 不依赖真实 MaiBot：缺 `pydantic` / `maibot_sdk` 时自动注入最小桩，CI 装真包
- **CI 重构**：`.github/workflows/ci.yml` 在 push/PR 跑「真实 SDK + 单元测试」，每晚定时跑一次真实站点冒烟；打包发 Release 改为 `needs: test`，测试不过不发版

### 修复

- **概览索引行剥离不完整**：站点实际格式是「标题 ↗ #N」（箭头在编号前），原正则只匹配「标题 #N ↗」，导致推送里混入与正文重复的概览、`split_items` 多切出一段
- **ISO 8601 pubDate 解析失败**：Atom 的 `published`/`updated`（含 `Z` 后缀）会回退成 1970，可能让旧条目抢占「最新一期」
- **正文清洗残留行首空格**：`\n[ \t]+` 未归一化，分页后出现行首缩进
- **适配站点链接改版**：详情页从 `/issue-N/` 改为 `/issues/YYYY-MM-DD/`，HTML 兜底的正则已两种都认（RSS 主链路不受影响）
- **分条前先剥离噪音**：原来只在切片后清理，分组小标题（如「要闻」）会残留在开头

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

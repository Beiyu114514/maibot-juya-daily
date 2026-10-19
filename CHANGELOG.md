# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

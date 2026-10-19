# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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

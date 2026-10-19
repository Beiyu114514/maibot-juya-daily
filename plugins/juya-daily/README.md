# 橘鸦AI早报（juya-daily）

MaiBot 插件：订阅 [橘鸦AI早报](https://daily.juya.uk/)，每天定时把最新一期 AI 资讯以纯文本推送到订阅的群/会话，也支持 `/早报` 随时查看。

数据源为站点官方 RSS `https://daily.juya.uk/rss.xml`（每期全文内嵌），HTML 解析仅作兜底。

## 安装

1. 安装 SDK（如尚未安装）：

   ```bash
   pip install maibot-plugin-sdk
   ```

2. 把 `juya-daily/` 整个目录放到 MaiBot 的 `plugins/` 下：

   ```text
   plugins/
   └── juya-daily/
       ├── _manifest.json
       ├── plugin.py
       ├── fetcher.py
       ├── formatter.py
       ├── scheduler.py
       ├── store.py
       └── config.toml   # 首次启动自动生成，也可手动放置
   ```

3. 重启 Runner。首次加载会自动生成配置并注册 WebUI 表单。

## 使用

| 命令 | 说明 |
| --- | --- |
| `/早报` | 立即抓取最新一期并发到当前会话 |
| `/早报订阅` | 订阅每日推送（当前会话） |
| `/早报退订` | 取消订阅（当前会话） |
| `/早报状态` | 查看订阅状态、推送时间与最近一期 |

推送内容为纯文本：日期标题 + 编号条目 + 脚注（含原文链接与「AI 辅助生成，可能存在错误」提示）。

## 配置（config.toml）

```toml
[schedule]
push_time = "09:30"        # 每日推送时间（部署机本地时区，HH:MM）
catch_up_on_start = true   # 启动时补发今天还没推的一期

[subscribe]
static_chats = []          # 静态订阅群号列表（兜底，与自助订阅叠加）
static_platform = "qq"
admin_only = false         # 仅管理员可订阅/退订

[push]
max_items = 0              # 每期最多推几条，0 = 全部
footer = "…"               # 脚注模板，支持 {link} {date} {title}
retry_minutes = 10         # 推送失败重试间隔
retry_times = 3            # 失败最大重试轮数
stale_deadline = "12:00"   # 当天一期超过这个时间还没发布就放弃
```

修改配置后通过 WebUI 保存即可，插件会热应用（改 `push_time` 会自动重启定时器）。

## 设计要点

- **订阅状态、每群最后推送日期、最近一期缓存** 均持久化在 `data/plugins/com.juya.daily/state.json`（原子写），防止重启后重复推送。
- **无 SDK 定时组件**，插件用 asyncio 后台任务自建每日定时器；热重载（新建 Runner 进程）时任务随插件卸载而取消，状态由落盘保证。
- **容错**：RSS/Atom 宽容解析 + HTML 兜底；推送失败按 `retry_minutes × retry_times` 重试；当天早报未发布时每 30 分钟重查，到 `stale_deadline` 放弃。
- **风控友好**：启动补推与多群推送之间随机错峰 30–120 秒。

## 注意

- `push_time` 按部署机本地时区计算；站点（东八区）一般在早上 8 点前后发布当天一期，默认 09:30 可避开空窗。
- 站点声明资讯由 AI 辅助生成、可能存在错误，推送脚注默认保留该提示与原文链接。

## License

MIT

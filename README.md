# 橘鸦AI早报（juya-daily）

<p align="center">
  <a href="https://github.com/Beiyu114514/maibot-juya-daily/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Beiyu114514/maibot-juya-daily?label=%E7%89%88%E6%9C%AC"></a>
  <a href="https://github.com/Beiyu114514/maibot-juya-daily/actions/workflows/package.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Beiyu114514/maibot-juya-daily/package.yml?label=%E6%89%93%E5%8C%85"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <img alt="License" src="https://img.shields.io/github/license/Beiyu114514/maibot-juya-daily">
  <img alt="Platform" src="https://img.shields.io/badge/MaiBot-%E6%8F%92%E4%BB%B6-orange">
</p>

MaiBot 插件：订阅 [橘鸦AI早报](https://daily.juya.uk/)，每天定时把最新一期 AI 资讯以纯文本推送到订阅的群/会话，也支持 `/早报` 随时查看。

数据源为站点官方 RSS `https://daily.juya.uk/rss.xml`（每期全文内嵌），HTML 解析仅作兜底。

## ✨ 功能

- **每日定时推送**：每天 `push_time` 自动推送最新一期（纯文本，无需图片/LLM）
- **群内自助订阅**：`/早报订阅` `/早报退订` 即刻生效，状态落盘不怕重启
- **即取即看**：`/早报` 随时拉最新一期
- **可靠推送**：启动补推、失败重试、当天未发布则每 30 分钟重查
- **热改配置**：WebUI 修改 `push_time` 等配置即时生效，无需重启
- **风控友好**：多群推送随机错峰 30–120 秒

## 📦 安装

从 [Release](https://github.com/Beiyu114514/maibot-juya-daily/releases/latest) 下载 `juya-daily-x.y.z.zip`（或克隆本仓库），把 `juya-daily/` 整个目录放到 MaiBot 的 `plugins/` 下：

```text
MaiBot/
└── plugins/
    └── juya-daily/
        ├── _manifest.json
        ├── plugin.py
        ├── fetcher.py
        ├── formatter.py
        ├── scheduler.py
        ├── store.py
        └── config.toml   # 首次启动自动生成，也可手动放置
```

重启 Runner，首次加载会自动生成配置并注册 WebUI 表单。

## 🚀 使用

| 命令 | 说明 |
| --- | --- |
| `/早报` | 立即抓取最新一期并发到当前会话 |
| `/早报订阅` | 订阅每日推送（当前会话） |
| `/早报退订` | 取消订阅（当前会话） |
| `/早报状态` | 查看订阅状态、推送时间与最近一期 |

推送内容为纯文本：日期标题 + 编号条目 + 脚注（含原文链接与「AI 辅助生成，可能存在错误」提示）。

## ⚙️ 配置

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

## 🔧 设计要点

- **订阅状态、每群最后推送日期、最近一期缓存** 均持久化在 `data/plugins/com.juya.daily/state.json`（原子写），防止重启后重复推送。
- **无 SDK 定时组件**，插件用 asyncio 后台任务自建每日定时器；热重载（新建 Runner 进程）时任务随插件卸载而取消，状态由落盘保证。
- **容错**：RSS/Atom 宽容解析 + HTML 兜底；推送失败按 `retry_minutes × retry_times` 重试；当天早报未发布时每 30 分钟重查，到 `stale_deadline` 放弃。
- **风控友好**：启动补推与多群推送之间随机错峰 30–120 秒。

## 🏗️ 开发

```bash
# 语法检查
python3 -m py_compile plugins/juya-daily/*.py

# 单元测试（83 项：RSS 解析 / 排版分页 / 状态落盘 / 定时器 / 推送成败判定）
python3 -m unittest discover -s tests -v

# 真实站点集成测试（会请求 daily.juya.uk，默认跳过）
JUYA_LIVE=1 python3 -m unittest tests.test_live_rss -v

# 打包（或直接推 v* tag，Actions 自动跑测试、打包并发布 Release）
git tag v0.2.0 && git push origin v0.2.0
```

测试不需要真实 MaiBot 环境：缺 `pydantic` / `maibot_sdk` 时会自动注入最小桩（CI 里装真包，走真实实现）。
夜间定时任务（`.github/workflows/ci.yml`）会跑一次 `JUYA_LIVE=1`，站点改版能最早发现。

项目结构：

```text
.github/workflows/ci.yml      # CI：单元测试（+ 夜间真实站点冒烟）+ 打包 zip + 发布 Release
tests/                        # unittest：test_fetcher / test_formatter / test_store / test_plugin / test_live_rss
plugins/juya-daily/
├── plugin.py      # 入口：命令、生命周期、推送编排
├── fetcher.py     # RSS 抓取解析（HTML 兜底、重试、当日缓存）
├── formatter.py   # 纯文本排版（分条、限条数、分页、脚注）
├── scheduler.py   # 每日定时器（可热改时间）
└── store.py       # 订阅状态持久化（原子写）
```

## ⚠️ 注意

- `push_time` 按部署机本地时区计算；站点（东八区）一般在早上 8 点前后发布当天一期，默认 09:30 可避开空窗。
- 站点声明资讯由 AI 辅助生成、可能存在错误，推送脚注默认保留该提示与原文链接。
- 本插件与 MaiBot 主项目 [MaiM-with-u/MaiBot](https://github.com/MaiM-with-u/MaiBot) 的官方插件无数据源冲突。

## 📄 License

[MIT](LICENSE) © [Beiyu114514](https://github.com/Beiyu114514)

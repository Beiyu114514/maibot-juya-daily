# 橘鸦AI早报（juya-daily）

<p align="center">
  <a href="https://github.com/Beiyu114514/maibot-juya-daily/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Beiyu114514/maibot-juya-daily?label=%E7%89%88%E6%9C%AC"></a>
  <a href="https://github.com/Beiyu114514/maibot-juya-daily/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Beiyu114514/maibot-juya-daily/ci.yml?label=%E6%B5%8B%E8%AF%95"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <img alt="License" src="https://img.shields.io/github/license/Beiyu114514/maibot-juya-daily">
  <img alt="Platform" src="https://img.shields.io/badge/MaiBot-%E6%8F%92%E4%BB%B6-orange">
</p>

MaiBot 插件：订阅 [橘鸦AI早报](https://daily.juya.uk/)，每天定时把最新一期 AI 资讯推送到订阅的群/会话（默认纯文本，可选 QQ Markdown / 长图），也支持 `/早报` 随时查看。

数据源为站点官方 RSS `https://daily.juya.uk/rss.xml`（每期全文内嵌），HTML 解析仅作兜底。

## ✨ 功能

- **每日定时推送**：每天 `push_time` 自动推送最新一期（纯文本 / QQ Markdown / 长图，无需 LLM）
- **群内自助订阅**：`/早报订阅` `/早报退订` 即刻生效，状态落盘不怕重启
- **即取即看**：`/早报` 随时拉最新一期
- **可靠推送**：启动补推、失败重试（多页断点续传，只补失败页）、当天未发布则每 30 分钟重查
- **热改配置**：WebUI 修改 `push_time` 等配置即时生效，无需重启
- **风控友好**：多群并发推送、各自随机错峰 30–120 秒（可配置），单群重复触发会被去重

## 📦 安装

### 方式一：WebUI 上传 ZIP（推荐）

从 [Release](https://github.com/Beiyu114514/maibot-juya-daily/releases/latest) 下载 `juya-daily-x.y.z.zip`，在 MaiBot WebUI 打开「插件扩展 → 更多操作 → 从 ZIP 安装插件」上传即可。

安装包固定为「ZIP 内唯一一层 `juya-daily/`」：

```text
juya-daily-x.y.z.zip
└── juya-daily/
    ├── _manifest.json
    ├── plugin.py
    ├── fetcher.py
    ├── formatter.py
    ├── scheduler.py
    ├── store.py
    ├── config.example.toml
    └── README.md
```

安装后文件监听器会发现新插件，并按 `config_model` 生成 `plugins/juya-daily/config.toml`；`enabled` 默认是 `false`，到「管理插件」手动打开开关即可，正常不需要重启 MaiBot。同 ID 插件已装过时，先卸载再装。

> ⚠️ **包结构别多套一层**：`_manifest.json` 和 `plugin.py` 必须位于 ZIP 根目录或唯一一层插件目录里。打成 `plugins/juya-daily/…` 会被宿主拒绝，提示「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」。**v0.3.0 的 Release 包就是这种坏结构，请改用 v0.3.1 及以上**；自己打包请用 `python3 scripts/package_plugin.py`（会按同样规则校验）。

### 方式二：手动放目录

克隆本仓库，把 `plugins/juya-daily/` 整个目录拷到 MaiBot 的 `plugins/` 下：

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

推送内容为日期标题 + 编号条目 + 脚注（含原文链接与「AI 辅助生成，可能存在错误」提示）。默认纯文本；把 `[push] message_format` 设为 `"markdown"` 后使用 QQ Markdown（标题、加粗、可点击原文链接），需要 [WhiteCloudOL/qq-official-adapter](https://github.com/WhiteCloudOL/qq-official-adapter) 等支持 `qq_markdown` 结构化消息的适配器，发送失败会自动回退纯文本。设为 `"image"` 则整期渲染成一张竖版长图（走宿主 `render.html2png` + `send.image`），QQ 官方机器人与本地客户端路线都能显示，渲染/发送失败同样回退纯文本。

## ⚙️ 配置

```toml
[schedule]
push_time = "09:30"        # 每日推送时间（固定东八区 UTC+8，HH:MM）
catch_up_on_start = true   # 启动时补发今天还没推的一期

[subscribe]
static_chats = []          # 静态订阅群号列表（兜底，与自助订阅叠加）
static_platform = "qq"
admin_only = false         # 仅管理员可订阅/退订

[push]
message_format = "text"    # text=纯文本；markdown=QQ Markdown；image=长图
image_width = 760          # 长图宽度（CSS 像素）
image_scale = 2.0          # 长图清晰度（设备像素比）
image_max_height = 16000   # 长图像素高度上限，超过回退纯文本
image_timeout_ms = 30000   # 长图渲染超时（毫秒）
max_items = 0              # 每期最多推几条，0 = 全部
footer = "…"               # 脚注模板，支持 {link} {date} {title}
retry_minutes = 10         # 推送失败重试间隔
retry_times = 3            # 失败最大重试轮数
stagger_min_seconds = 30   # 多群推送错峰下限（秒）
stagger_max_seconds = 120  # 多群推送错峰上限（秒，须 ≥ 下限）
stale_deadline = "12:00"   # 当天一期超过这个时间还没发布就放弃
```

修改配置后通过 WebUI 保存即可，插件会热应用（改 `push_time` 会自动重启定时器）。

## 🔧 设计要点

- **订阅状态、每群推送进度（日期 + 页 + 是否完成）、最近一期缓存** 均持久化在 `data/plugins/com.juya.daily/state.json`（v3 结构，原子写），防止重启后重复推送；旧版 v1/v2 状态会自动迁移。
- **降级提醒**：0.3.0 的状态是 v3，降级回 0.2.x 会把状态备份为 `.future` / `.corrupt` 后重置订阅，升级前请保留 `state.json`。
- **单实例约束**：并发去重与轮级锁是进程内状态，同一 `data_dir` 只允许一个插件实例；多实例请分开数据目录。
- **无 SDK 定时组件**，插件用 asyncio 后台任务自建每日定时器；热重载（新建 Runner 进程）时任务随插件卸载而取消，状态由落盘保证。
- **容错**：RSS/Atom 宽容解析 + HTML 兜底；推送失败按 `retry_minutes × retry_times` 重试；当天早报未发布时每 30 分钟重查，到 `stale_deadline` 放弃。
- **风控友好**：启动补推与多群推送并发发起，各目标随机错峰 30–120 秒（`stagger_min_seconds` / `stagger_max_seconds`）。
- **分页与格式**：纯文本单条 ≤1800 字，Markdown 单条 ≤3800 字（适配器硬上限 4000），二者都自动分页并带页码；长图模式整期一张，超过 `image_max_height` 或渲染失败时回退纯文本。

## 🏗️ 开发

```bash
# 语法检查
python3 -m py_compile plugins/juya-daily/*.py

# 单元测试（185 项：RSS 解析 / 排版分页 / Markdown 渲染与回退 / 长图 HTML 与回退 / 状态落盘与迁移 / 定时器 / 推送成败与断点续传 / 并发去重 / SDK 注入契约 / 配置模型可实例化 / 安装包结构）
python3 -m unittest discover -s tests -v

# 真实站点集成测试（会请求 daily.juya.uk，默认跳过）
JUYA_LIVE=1 python3 -m unittest tests.test_live_rss -v

# 打包（产出 dist/juya-daily-x.y.z.zip + .sha256，并校验是否符合 MaiBot 的 ZIP 安装规则）
python3 scripts/package_plugin.py

# 发版：manifest 版本必须与 tag 一致，推 v* tag 后 Actions 自动测试、打包并发布 Release
git tag v0.5.0 && git push origin v0.5.0
```

测试不需要真实 MaiBot 环境：缺 `pydantic` / `maibot_sdk` 时会自动注入最小桩（CI 里装真包，走真实实现）。桩按真实 SDK 可见面对齐（`ctx`/`config` 只读、经 `_set_context` / `set_plugin_config` 注入、`Command` 写 `__maibot_component_info__`），本地与 CI 覆盖同一代码路径；要装真包：`pip install -r requirements-dev.txt`。
夜间定时任务（`.github/workflows/ci.yml`）会跑一次 `JUYA_LIVE=1`，失败会自动开 issue，站点改版能最早发现。

项目结构：

```text
.github/workflows/ci.yml      # CI：单元测试（+ 夜间真实站点冒烟）+ 打包 zip + 发布 Release
scripts/package_plugin.py     # 按 MaiBot 规则打包/校验安装包（唯一一层 juya-daily/）
tests/                        # unittest：test_fetcher / test_formatter / test_store / test_scheduler / test_plugin / test_packaging / test_live_rss + fuzz
plugins/juya-daily/
├── plugin.py      # 入口：命令、生命周期、推送编排
├── fetcher.py     # RSS 抓取解析（HTML 兜底、重试、当日缓存）
├── formatter.py   # 纯文本 / QQ Markdown / 长图 HTML 排版（分条、限条数、分页、脚注）
├── scheduler.py   # 每日定时器（可热改时间）
└── store.py       # 订阅状态持久化（原子写）
```

## ⚠️ 注意

- `push_time` 固定按东八区（UTC+8）计算，与部署机时区无关；站点一般在早上 8 点前后发布当天一期，默认 09:30 可避开空窗。
- 已在 MaiBot 1.3.x + 插件 SDK 2.14 上验证；manifest 允许的更高版本未逐一实测。
- 安全边界：RSS/HTML 来自站点，默认信任 `daily.juya.uk`；XML 用标准库解析（未引入 `defusedxml`），单次响应读取上限 10 MiB。
- 站点声明资讯由 AI 辅助生成、可能存在错误，推送脚注默认保留该提示与原文链接。
- 本插件与 MaiBot 主项目 [MaiM-with-u/MaiBot](https://github.com/MaiM-with-u/MaiBot) 的官方插件无数据源冲突。

## 📄 License

[MIT](LICENSE) © [Beiyu114514](https://github.com/Beiyu114514)

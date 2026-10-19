# 橘鸦AI早报（juya-daily）

MaiBot 插件：订阅 [橘鸦AI早报](https://daily.juya.uk/)，每天定时把最新一期 AI 资讯推送到订阅的群/会话（默认纯文本，可选 QQ Markdown / 长图），也支持 `/早报` 随时查看。

数据源为站点官方 RSS `https://daily.juya.uk/rss.xml`（每期全文内嵌），HTML 解析仅作兜底。

## 安装

1. 安装 SDK（如尚未安装）：

   ```bash
   pip install maibot-plugin-sdk
   ```

2. 放插件目录，二选一：
   - **WebUI 导入**：下载 Release 里的 `juya-daily-x.y.z.zip`，在「插件扩展 → 更多操作 → 从 ZIP 安装插件」上传。包内只有一层 `juya-daily/`，不要再套 `plugins/`（多一层会被拒：「ZIP 根目录或唯一一层插件目录中必须包含 _manifest.json」）。
   - **手动拷贝**：把 `juya-daily/` 整个目录放到 MaiBot 的 `plugins/` 下：

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

3. 重启 Runner（手动安装）或在「管理插件」里启用。首次加载会自动生成配置并注册 WebUI 表单。

## 使用

| 命令 | 说明 |
| --- | --- |
| `/早报` | 立即抓取最新一期并发到当前会话 |
| `/早报订阅` | 订阅每日推送（当前会话） |
| `/早报退订` | 取消订阅（当前会话） |
| `/早报状态` | 查看订阅状态、推送时间与最近一期 |

推送内容为日期标题 + 编号条目 + 脚注（含原文链接与「AI 辅助生成，可能存在错误」提示）。默认是纯文本；把 `[push] message_format` 设为 `"markdown"` 后改为 QQ Markdown（标题、加粗、可点击原文链接，单条上限 3800 字），设为 `"image"` 后改为整期长图。

## QQ Markdown 推送

Markdown 模式通过 **结构化自定义消息** 发送（宿主 `send.custom` → 适配器 `qq_markdown` 段 → QQ `msg_type=2`），需要适配器支持该消息段。目前验证过的组合是 [WhiteCloudOL/qq-official-adapter](https://github.com/WhiteCloudOL/qq-official-adapter) + QQ 官方机器人（群聊/单聊）。

```toml
[push]
message_format = "markdown"
```

- 适配器不需要打开「启用 Markdown 输出」开关：该开关只是把**普通文本回复**也按 Markdown 发送；本插件走的是显式结构化消息，与该开关相互独立。
- 该适配器对 Markdown 单条限制 4000 字符，插件按 3800 字分页并保留页码；发送失败会自动降级为同内容纯文本，不会让整期推送失败。
- 只使用 QQ 官方 Markdown 支持的语法（`#`/`##` 标题、`**加粗**`、`>` 引用、`[文字](url)` 链接），不使用代码块与表格。
- 其他适配器（如 NapCat/SnowLuma 统一连接器）没有 `qq_markdown` 段，会把它当未知段降级成 `[unsupported:qq_markdown]` 文本；这类环境请保持默认 `message_format = "text"`。

## 长图推送

`message_format = "image"` 会把整期渲染成一张竖版长图（标题、导语、条目、脚注），通过宿主 `render.html2png` 截图后用 `send.image` 发出。图片走 QQ 富媒体（`msg_type=7`），QQ 官方机器人和 NapCat/SnowLuma 等本地客户端路线都能正常显示。

```toml
[push]
message_format = "image"
image_width = 760         # 长图宽度（CSS 像素）
image_scale = 2.0         # 清晰度（设备像素比）
image_max_height = 16000  # 像素高度上限，超过则回退纯文本
image_timeout_ms = 30000  # 渲染超时
```

- 需要宿主开启浏览器渲染：MaiBot 配置 `plugin_runtime.render.enabled = true`（默认开启），并确保 Playwright/Chromium 可用。
- 渲染不可用、截图失败、图片发送失败或高度超限时，整期自动回退纯文本，不会让推送彻底失败。
- 长图模式按「整期一张」记录推送进度，失败重试会重发整张图。
- HTML 使用系统字体栈（Noto Sans CJK / 苹方 / 微软雅黑等），不加载任何外部资源，离线也能渲染。

## 配置（config.toml）

```toml
[schedule]
push_time = "09:30"        # 每日推送时间（固定东八区 UTC+8，HH:MM）
catch_up_on_start = true   # 启动时补发今天还没推的一期

[subscribe]
static_chats = []          # 静态订阅群号列表（兜底，与自助订阅叠加；防重复推送同样生效）
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

## 设计要点

- **订阅状态、每群推送进度（日期 + 页 + 是否完成）、最近一期缓存** 均持久化在 `data/plugins/com.juya.daily/state.json`（v3 结构，原子写），防止重启后重复推送；自助订阅与静态订阅群共用同一份推送进度，静态群即使未订阅也会记录「已推过今天」，多页失败时只补失败页。
- **无 SDK 定时组件**，插件用 asyncio 后台任务自建每日定时器；热重载（新建 Runner 进程）时任务随插件卸载而取消，状态由落盘保证。
- **容错**：RSS/Atom 宽容解析 + HTML 兜底；日期优先从标题/正文抽取（pubDate 仅兜底）；推送失败按 `retry_minutes × retry_times` 重试；当天早报未发布时每 30 分钟重查，到 `stale_deadline` 放弃。
- **风控友好**：启动补推与多群推送并发发起，各目标随机错峰 30–120 秒（`stagger_min_seconds` / `stagger_max_seconds`），单目标重复触发会被进程内去重。
- **分页发送**：整期内容按「条」打包成多条消息，纯文本单条不超过 1800 字、Markdown 单条不超过 3800 字，避免被 QQ 截断；末页带来源脚注。长图模式则整期渲染成一张图，失败回退纯文本分页。
- **链接格式兼容**：站点早期用 `/issue-N/`、现改为 `/issues/YYYY-MM-DD/`，RSS 与 HTML 兜底两种都认。

## 开发

```bash
# 在 MaiBot 仓库的 plugins/ 目录下（或任意位置）跑：
python3 -m py_compile plugins/juya-daily/*.py          # 语法检查
python3 -m unittest discover -s tests -v               # 单元测试（145 项）
JUYA_LIVE=1 python3 -m unittest tests.test_live_rss -v # 真实站点冒烟（默认跳过）
```

测试不依赖真实 MaiBot：缺 `pydantic` / `maibot_sdk` 时自动注入最小桩（与真实 SDK 可见面对齐），CI 里装真包；要装真包见仓库根目录 `requirements-dev.txt`。

## 注意

- `push_time` 固定按东八区（UTC+8）计算，与部署机时区无关；站点一般在早上 8 点前后发布当天一期，默认 09:30 可避开空窗。
- **降级会丢状态**：0.3.0 使用 v3 状态结构，降级回 0.2.x 会把它当未来版本备份并重置订阅；升级前请保留 `state.json`。
- **单实例约束**：并发去重与轮级锁是进程内状态，同一 `data_dir` 只允许一个插件实例；多实例请分开数据目录。
- 站点声明资讯由 AI 辅助生成、可能存在错误，推送脚注默认保留该提示与原文链接。
- 已在 MaiBot 1.3.x + 插件 SDK 2.14 上验证；manifest 允许的更高版本未逐一实测。
- 若站点改版导致 RSS 解析异常，可用上面的 `JUYA_LIVE=1` 冒烟最早发现；CI 每晚也会自动跑一次，失败会自动开 issue。
- 安全边界：RSS/HTML 来自站点，默认信任 `daily.juya.uk`；XML 用标准库解析，单次响应读取上限 10 MiB。

## License

MIT

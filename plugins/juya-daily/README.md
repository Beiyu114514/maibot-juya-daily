# 橘鸦AI早报（juya-daily）

MaiBot 插件：订阅 [橘鸦AI早报](https://daily.juya.uk/)，每天定时把最新一期 AI 资讯以纯文本推送到订阅的群/会话，也支持 `/早报` 随时查看。

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

推送内容为纯文本：日期标题 + 编号条目 + 脚注（含原文链接与「AI 辅助生成，可能存在错误」提示）。

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
- **分页发送**：整期内容按「条」打包成多条消息，单条不超过 1800 字，避免被 QQ 截断；末页带来源脚注。
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

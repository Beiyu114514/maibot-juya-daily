# 代码审查报告 · maibot-juya-daily（v0.6.0，commit 4f54fb3）

> 审查范围：`plugins/juya-daily/`（plugin.py / fetcher.py / formatter.py / scheduler.py / store.py）、`scripts/package_plugin.py`、测试目录通读。
> 说明：审查环境无 Python 解释器，约 2400 行测试未在本地执行，结论基于源码通读。

## 总体评价

质量明显高于平均水平的 MaiBot 插件项目：

- 模块职责分明：fetcher（抓取/解析）、formatter（渲染/分页）、scheduler（定时）、store（持久化）、plugin（编排）；
- 只依赖标准库，网络层用 `asyncio.to_thread` 包阻塞 `urlopen`，不卡事件循环；
- 注释大量记录「为什么」而非「是什么」（如 `plugin.py:7-12` 解释为何不能加 `from __future__ import annotations`）；
- 按页断点续传、长图失败回退文本、`_send_text` 严格判定返回值避免假成功等推送链路细节处理到位；
- 打包脚本复刻宿主 ZIP 校验规则并有测试覆盖，是防「宿主拒收」事故的最佳实践。

未发现阻断性缺陷。以下按严重程度列出问题与修复建议。

---

## 主要问题

### P1 · `push_time` 与 `stale_deadline` 无交叉校验，配错会静默永不推送

**位置**：`plugins/juya-daily/plugin.py:916-952`（`_wait_for_today_issue`）、`plugin.py:208-212`（`stale_deadline` 配置）

**现象**：`stale_deadline` 默认 `12:00`。若用户把 `push_time` 配置晚于 `stale_deadline`（如 `push_time=14:00`），`_on_schedule_fire` 触发时已过 deadline，`_wait_for_today_issue` 直接 return，当天推送被静默丢弃，只有一行 warning。用户视角是「永远不推」且难以排查。

**建议修复**：

1. 加载配置与 `on_config_update` 时校验：若 `parse_hhmm(push_time) >= parse_hhmm(stale_deadline)`，记一条明显的 warning，并将 deadline 生效值取 `max(push_time, stale_deadline)` 后再加缓冲（如 30 分钟）；
2. 或在配置 description 里明确写「stale_deadline 必须晚于 push_time」，并在 `/早报状态` 里展示两个时间便于自查。

### P2 · HTML 兜底假设「首页第一个 issue 链接即最新」

**位置**：`plugins/juya-daily/fetcher.py:322-350`（`_fetch_from_html`，`links[0]`）

**现象**：兜底路径取首页正则匹配到的第一个 issue 链接。该兜底本来就是为「站点改版、RSS 不可用」准备的，而改版恰恰容易连链接排列顺序一起变（置顶旧文、推荐位等），此时会抓错一期。

**建议修复**：

1. 从所有匹配链接里提取 `/issues/YYYY-MM-DD/` 日期，取日期最大者；`issue-N` 旧格式无法比日期时才回退 `links[0]`；
2. 抓到页面后同时校验 `_extract_date` 结果与链接日期一致，不一致记 warning。

### P3 · `_retry_failed` 串行重试，且绕过 `_push_round` 的锁

**位置**：`plugins/juya-daily/plugin.py:853-867`

**现象**：重试循环内对每个失败会话逐个 `await self._push_one(...)`，一个群卡住则整轮重试都卡住；重试不经过 `_push_lock`，与 `_push_round` 的互斥语义不一致（同一 stream_id 只靠 `_pushing` 集合兜底去重，实际安全，但日志会误导排查者以为全局互斥）。

**建议修复**：

1. 重试轮复用 `_push_batch`（自带信号量并发与 stagger），失败列表语义不变；
2. 若保留现实现，至少在注释里写明「重试与定时轮的互斥依赖 `_pushing` 集合而非 `_push_lock`」。

### P4 · `/早报` 命令冷却字典无限增长

**位置**：`plugins/juya-daily/plugin.py:326`（`_manual_fetch_at`）

**现象**：按 stream_id 记录上次拉取时间戳，永不清理。长期运行的 bot 是一个很慢的内存泄漏（每条目仅一个 str + float）。

**建议修复**：写入前清理过期项，一行即可：

```python
if len(fetch_times) > 1000:
    cutoff = now - self._MANUAL_COOLDOWN
    for sid, ts in list(fetch_times.items()):
        if ts < cutoff:
            del fetch_times[sid]
```

### P5 · `_check_admin` 信号合并策略偏宽松

**位置**：`plugins/juya-daily/plugin.py:485-513`

**现象**：「任一 holder 的任一信号为真即放行」是为兼容适配器字段不统一，但反向风险同样存在：某适配器若在某 holder 里误填 `is_admin=True`（如把「是管理员消息」误用为「发送者是管理员」），即被放行。因 `admin_only` 默认 `False`，默认配置下无风险。

**建议修复**：无法完美解决，建议：

1. 在 README/配置描述里注明「admin_only 依赖适配器提供 is_admin / role 字段，行为因适配器而异」；
2. 可选增强：当出现「部分 holder 显式为 False、部分为 True」的冲突信号时记 warning，方便定位适配器问题。

---

## 次要问题

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| M1 | `fetcher.py:29` | `_FALLBACK_VERSION = "0.3.1"` 已落后于 manifest 的 0.6.0；注释要求发版同步但未执行 | 兜底值改成 `"unknown"`，彻底免去同步负担 |
| M2 | `formatter.py:277`、`formatter.py:349` | 分页上限用 `assert` 兜底；宿主以 `python -O` 运行时断言被剥掉，保护消失 | 改为显式 `if any(...): raise ValueError(...)` |
| M3 | `store.py:197-199` | `subscriptions()` 每次 `json.dumps+loads` 深拷贝；`_push_targets` 每次推送都调用 | 订阅量小无实际影响，可不动；介意则返回浅拷贝或 `dict(...)` |
| M4 | `scheduler.py:91` | `now.replace(hour=...)` 在目标时刻无效（夏令时跳跃）时可能抛异常 | `now_fn` 固定为东八区 `now_cst`，无夏令时，实际安全；可加注释说明这一前提 |
| M5 | `plugin.py:342` | `/早报` 成功后手动 `set_last_fetch` 落盘；与 fetcher 内存缓存的一致性靠约定维持 | 可接受；如要收紧，可让 `fetch_latest` 成功后通过回调统一落盘 |

---

## 修复优先级建议

1. **先修 P1**（配置校验）：用户可感知、修复成本低、防「永不推送」的坏体验；
2. **再修 P2**（兜底链接按日期取最新）：兜底路径的正确性保险；
3. **P3 / P4** 顺手修（复用 `_push_batch` / 清理冷却字典）；
4. **M1 / M2** 低成本清理，可搭车任意一次发版；
5. P5 / M3–M5 记录在案即可，不必专门发版。

## 值得肯定的实现

- `store.py:60-81`：读盘瞬时失败保留原文件、结构损坏才备份重置、未来版本不静默降级——状态持久化策略成熟；
- `plugin.py:428-437`：`_fetch_latest_shared` 合并并发抓取 + `asyncio.shield`，处理干净；
- `plugin.py:717-736`：`_send_text` 严格判定 `sent` 字段，避免「只看抛异常把失败当成功」导致 `last_pushed` 写死；
- `scripts/package_plugin.py`：固定时间戳/权限保证 zip 可复现，校验规则与宿主一致并写入测试；
- `tests/test_plugin.py`：依赖桩注入使测试在「无 pydantic / 无 SDK」环境也能跑。

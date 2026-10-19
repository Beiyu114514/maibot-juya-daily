"""橘鸦AI早报 RSS 抓取与解析。

只用标准库：urllib 抓取、xml.etree 解析 RSS、正则兜底解析站点页面。
网络失败自动退避重试；最近一期缓存落盘，断网/站点异常时可兜底。
"""

from __future__ import annotations

import asyncio
import email.utils
import html
import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RSS_URL = "https://daily.juya.uk/rss.xml"
SITE_URL = "https://daily.juya.uk/"

# 站点是中文日更，日期一律按东八区归一化，避免受部署机时区影响
CST = timezone(timedelta(hours=8))


# manifest 读不到时的兜底版本号，发版时与 _manifest.json 一起改
_FALLBACK_VERSION = "0.3.1"


def _plugin_version() -> str:
    """从同目录 _manifest.json 读版本，避免 User-Agent 长年停在 0.1。"""
    try:
        with open(Path(__file__).with_name("_manifest.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or _FALLBACK_VERSION)
    except (OSError, ValueError, TypeError):
        return _FALLBACK_VERSION


USER_AGENT = f"MaiBot-juya-daily-plugin/{_plugin_version()} (+https://daily.juya.uk/)"
REQUEST_TIMEOUT = 15  # 秒
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # 第 n 次重试前等待 n*RETRY_BACKOFF 秒
MAX_RSS_BYTES = 10 * 1024 * 1024  # RSS/HTML 响应上限，防超大文件打爆内存

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")
# 站点改过版：早期链接是 /issue-N/，现在是 /issues/YYYY-MM-DD/，两种都要认
_ISSUE_LINK_RE = re.compile(
    r"""href=["']([^"']*(?:issue-\d+/|issues/20\d{2}-\d{2}-\d{2}/?))["']""", re.IGNORECASE
)
_DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")


@dataclass
class Issue:
    """一期的早报。"""

    title: str
    link: str
    date: str  # YYYY-MM-DD（东八区），拿不到可靠日期时为空串
    pub_dt: datetime  # aware datetime
    content: str
    source: str = "rss"  # rss / html / cache

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "link": self.link,
            "date": self.date,
            "pub_dt": self.pub_dt.isoformat(),
            "content": self.content,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Issue | None":
        if not data:
            return None
        try:
            pub_dt = datetime.fromisoformat(str(data.get("pub_dt")))
        except (TypeError, ValueError):
            pub_dt = datetime.now(timezone.utc)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        return cls(
            title=str(data.get("title") or ""),
            link=str(data.get("link") or ""),
            date=str(data.get("date") or ""),
            pub_dt=pub_dt,
            content=str(data.get("content") or ""),
            source=str(data.get("source") or "cache"),
        )


def now_cst() -> datetime:
    """当前时刻（东八区）。站点按东八区发布，所有「今天/到点了没」判断都以此为基准。"""
    return datetime.now(CST)


def today_cst(now: datetime | None = None) -> str:
    """今天的日期（东八区），如 2026-06-13。"""
    return (now or now_cst()).strftime("%Y-%m-%d")


class FetchError(Exception):
    """重试耗尽后仍抓取失败。"""


class ResponseTooLarge(Exception):
    """响应体超过 MAX_RSS_BYTES。"""


# ---------- 网络层 ----------


def _urlopen_read(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/html, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        body = resp.read(MAX_RSS_BYTES + 1)
    if len(body) > MAX_RSS_BYTES:
        raise ResponseTooLarge(f"{url} 响应超过 {MAX_RSS_BYTES} 字节")
    return body


async def _http_get(url: str, logger: logging.Logger) -> bytes:
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # urlopen 是阻塞调用，丢进线程池避免卡住事件循环
            return await asyncio.to_thread(_urlopen_read, url)
        except ResponseTooLarge as exc:
            # 响应过大重试也没用，直接按抓取失败处理
            logger.error("抓取 %s 失败: %s", url, exc)
            raise FetchError(str(exc)) from exc
        except Exception as exc:
            last_err = exc
            logger.warning("抓取 %s 第 %d/%d 次失败: %s", url, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF * attempt)
    raise FetchError(f"抓取失败（已重试 {MAX_RETRIES} 次）: {last_err}")


# ---------- 解析层 ----------


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _from_isoformat(raw: str) -> datetime | None:
    """ISO 8601 兜底（Atom 的 published/updated、以及带 Z 后缀的形式）。"""
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _parse_pub_date(raw: str) -> datetime:
    if raw:
        for parse in (email.utils.parsedate_to_datetime, _from_isoformat):
            try:
                dt = parse(raw)
            except (TypeError, ValueError, IndexError):
                dt = None
            if dt is not None:
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    # 解析失败时给一个明确的过去时间：避免 pubDate 异常的条目
    # 用「现在」抢占 max(issues, key=pub_dt) 误导「最新一期」判断
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def clean_text(text: str) -> str:
    return html.unescape(text or "").strip()


def clean_body(body: str) -> str:
    """轻度清洗：反转义、markdown 链接转纯文本、必要时剥掉 HTML 标签。"""
    text = html.unescape(body or "")
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", text)
    if _TAG_RE.search(text):
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        text = _TAG_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pick_latest(issues: list[Issue]) -> Issue | None:
    """从若干期里挑出最新的一期；列表为空返回 None。"""
    if not issues:
        return None
    return max(issues, key=lambda item: item.pub_dt)


def parse_rss(data: bytes) -> list[Issue]:
    """宽容解析 RSS/Atom，条目内嵌整期全文。"""
    root = ET.fromstring(data)
    issues: list[Issue] = []
    for node in root.iter():
        if _local_name(node.tag) not in ("item", "entry"):
            continue
        fields: dict[str, str] = {}
        for child in node:
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name == "link":
                href = child.get("href")
                if href and "link" not in fields:
                    fields["link"] = href.strip()
                elif text and "link" not in fields:
                    fields["link"] = text
            elif name in ("title", "pubdate", "published", "updated"):
                if name not in fields and text:
                    fields[name] = text
            elif name in ("encoded", "content"):
                if "encoded" not in fields and text:
                    fields["encoded"] = text
            elif name in ("description", "summary"):
                if "description" not in fields and text:
                    fields["description"] = text
        body = fields.get("encoded") or fields.get("description") or ""
        if not body and not fields.get("title"):
            continue
        raw_pub = fields.get("pubdate") or fields.get("published") or fields.get("updated") or ""
        pub_dt = _parse_pub_date(raw_pub)
        title = clean_text(fields.get("title", ""))
        body_clean = clean_body(body)
        # 日期优先从标题/正文抽取（站点「期号日期」以内容为准），
        # pubDate 仅作兜底——站点调整发布时间时不会把 date 带偏一天
        date = _extract_date(title) or _extract_date(body_clean[:500]) or pub_dt.astimezone(CST).strftime("%Y-%m-%d")
        issues.append(
            Issue(
                title=title,
                link=fields.get("link", ""),
                date=date,
                pub_dt=pub_dt,
                content=body_clean,
                source="rss",
            )
        )
    return issues


def _extract_page_text(page: str) -> tuple[str, str]:
    title = ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    if title_m:
        title = clean_text(title_m.group(1))
    container_m = re.search(r"<article[^>]*>(.*?)</article>", page, re.IGNORECASE | re.DOTALL)
    if not container_m:
        container_m = re.search(r"<main[^>]*>(.*?)</main>", page, re.IGNORECASE | re.DOTALL)
    segment = container_m.group(1) if container_m else page
    segment = re.sub(
        r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
        "",
        segment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return title, clean_body(segment)


def _extract_date(text: str) -> str | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CST).strftime("%Y-%m-%d")
    except ValueError:
        return None


# ---------- 对外入口 ----------


class JuyaFetcher:
    """抓取最新一期早报：当日内存缓存 + 落盘兜底 + RSS 失败走 HTML。"""

    def __init__(self, logger: logging.Logger, cache: dict | None = None):
        self.logger = logger
        self._cache = Issue.from_dict(cache)

    @property
    def cached_issue(self) -> Issue | None:
        return self._cache

    async def fetch_latest(self, force: bool = False) -> Issue | None:
        """返回最新一期；全部失败时退回最近缓存（可能是旧的，用 date 判断）。"""
        if not force and self._cache and self._cache.date == today_cst():
            return self._cache  # 当日已抓过，避免频繁请求站点
        try:
            raw = await _http_get(RSS_URL, self.logger)
            issues = parse_rss(raw)
            if issues:
                latest = pick_latest(issues)
                latest.source = "rss"
                self._cache = latest
                return latest
            self.logger.warning("RSS 里没有可用条目（站点可能改版），尝试 HTML 兜底")
        except FetchError as exc:
            self.logger.error("RSS 抓取失败: %s，尝试 HTML 兜底", exc)
        except ET.ParseError as exc:
            self.logger.error("RSS 解析失败: %s，尝试 HTML 兜底", exc)
        fallback = await self._fetch_from_html()
        if fallback:
            self._cache = fallback
            return fallback
        return self._cache

    async def _fetch_from_html(self) -> Issue | None:
        """兜底：从首页找最新一期链接，抓页面抽正文（质量不如 RSS）。"""
        try:
            home = (await _http_get(SITE_URL, self.logger)).decode("utf-8", "replace")
            links = _ISSUE_LINK_RE.findall(home)
            if not links:
                self.logger.warning("首页没有找到 issue 链接，HTML 兜底失败")
                return None
            first = links[0]
            if first.startswith("http"):
                link = first
            elif first.startswith("/"):
                link = SITE_URL.rstrip("/") + first
            else:
                link = SITE_URL.rstrip("/") + "/" + first
            page = (await _http_get(link, self.logger)).decode("utf-8", "replace")
            title, body = _extract_page_text(page)
            if not body:
                return None
            date = _extract_date(title) or _extract_date(body[:500]) or ""
            self.logger.info("HTML 兜底成功: %s (%s)", title or link, date or "日期未知")
            return Issue(
                title=title or f"橘鸦AI早报 {date}".strip(),
                link=link,
                date=date,
                pub_dt=datetime.now(timezone.utc),
                content=body,
                source="html",
            )
        except FetchError as exc:
            self.logger.error("HTML 兜底抓取也失败: %s", exc)
            return None

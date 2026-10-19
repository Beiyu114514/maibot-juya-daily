"""把一期早报渲染成群聊纯文本。"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查用，运行时不产生依赖
    from .fetcher import Issue

DEFAULT_FOOTER = (
    "— 来源：橘鸦AI早报 {link}\n"
    "⚠️ 资讯内容由AI辅助生成，可能存在错误，请以原文为准"
)

# 条目标题编号出现在行尾：「标题 ↗ #1」（概览）或「标题 #1」（正文）；
# 只认出现在行尾（后面到行尾没有别的实质内容）的编号，避免误切正文里的 # 字符。
_ITEM_HEAD_RE = re.compile(r"(?m)^.{0,120}?#[0-9]{1,3}\s*$")
# 概览索引行带 ↗ 且编号会在正文里重复出现，分条/限条数时都应剔除
_OVERVIEW_LINE_RE = re.compile(r"(?m)^.{0,120}↗\s*#[0-9]{1,3}\s*$")


# 站点一期内容的固定结构：标题/视频版 → 「概览」（带 ↗ 的索引行）→ 按分组的正文条目。
# 索引行和分组小标题在推送纯文本里属于噪音：索引会与正文重复，分组词单独成行没信息量。
_GROUP_TITLE_RE = re.compile(r"(?m)^要闻\s*$|^技术与洞察\s*$|^前瞻与传闻\s*$")
# 收尾的站点页脚（纯操作提示，无资讯内容）
_TAIL_NOISE_RE = re.compile(
    r"提示：内容由AI辅助创作.*?查看网页全文(?:\s*·\s*查看 Markdown)?\s*$", re.DOTALL
)
# 带「：」引导的视频版行
_VIDEO_LINE_RE = re.compile(r"(?m)^视频版：[^\n]*$")


def _strip_overview_lines(text: str) -> str:
    """删掉「标题 ↗ #N」概览索引行（含其后的空行），返回清理后的文本。"""
    return _OVERVIEW_LINE_RE.sub("", text).replace("\n\n\n", "\n\n").strip()


def _strip_noise(text: str) -> str:
    """剥掉推送场景下的纯噪音：概览索引行、收尾页脚、视频版行、分组小标题。"""
    text = _strip_overview_lines(text)
    text = _TAIL_NOISE_RE.sub("", text)
    text = _VIDEO_LINE_RE.sub("", text)
    text = _GROUP_TITLE_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_items(content: str) -> list[str]:
    """按行尾编号（标题 … #N）把整期内容拆成单条资讯；拆不开就整段返回。"""
    content = content or ""
    heads = [
        match
        for match in _ITEM_HEAD_RE.finditer(content)
        if "↗" not in match.group(0)
    ]
    if len(heads) < 2:
        return [content.strip()] if content.strip() else []
    parts: list[str] = []
    intro = _strip_noise(content[: heads[0].start()])
    if intro:
        parts.append(intro)
    for idx, head in enumerate(heads):
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(content)
        chunk = _strip_noise(content[head.start() : end])
        if chunk:
            parts.append(chunk)
    return parts


def _fill(template: str, **values: str) -> str:
    """安全的占位符替换：不认识的 {xxx} 原样保留，不会抛 KeyError。"""
    text = template or ""
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def format_issue(
    issue: "Issue",
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
) -> str:
    """渲染一条可直接发送的纯文本消息。max_items<=0 表示全部。"""
    title = issue.title or f"橘鸦AI早报 {issue.date}".strip()
    lines = [f"📰 {title}"]
    if stale:
        lines.append(f"（暂未抓到今天的一期，以下为最近缓存：{issue.date or '日期未知'}）")
    body = _strip_noise(issue.content or "")
    if max_items and max_items > 0:
        items = split_items(body)
        if len(items) > max_items:
            body = "\n\n".join(items[:max_items]) + f"\n\n……（仅展示前 {max_items} 条，完整内容见原文）"
    if body:
        lines.append(body)
    footer = _fill(footer_template or DEFAULT_FOOTER, link=issue.link, date=issue.date, title=title)
    if footer:
        lines.append(footer)
    return "\n\n".join(line for line in lines if line)

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
# 概览索引行形如「标题 ↗ #1」，也有站点反过来写成「标题 #1 ↗」；
# 编号会在正文里重复出现，分条/限条数时都应剔除
_OVERVIEW_LINE_RE = re.compile(
    r"(?m)^.{0,160}?(?:↗[ \t]*#[0-9]{1,3}|#[0-9]{1,3}[ \t]*↗)[ \t]*$"
)

# 分组小标题（「要闻」「开发生态」…）的识别不靠硬编码名单，靠形态：
# 行首、短行、前后空行、且紧接着的下一非空行是以「#N」结尾的条目标题行。
# 站点新增分组照样能剥；正文里恰好单独成行的同名短句（下一行是正文而非编号标题）不会误删。
_GROUP_TITLE_RE = re.compile(r"(?m)^([^\n#]{1,20})[ \t]*\n\s*\n(?=.{0,120}?#[0-9]{1,3}[ \t]*$)")
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
    content = _strip_noise(content or "")
    heads = [
        match
        for match in _ITEM_HEAD_RE.finditer(content)
        if "↗" not in match.group(0)
    ]
    if len(heads) < 2:
        return [content] if content else []
    parts: list[str] = []
    intro = content[: heads[0].start()].strip()
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


# 单条消息长度上限。QQ 群聊单条消息过长会被平台截断甚至发送失败，
# 因此按条切页而不是整期一条。
MESSAGE_CHAR_LIMIT = 1800


def _split_long_block(block: str, limit: int) -> list[str]:
    """把超长文本块按行/硬切分拆成不超过 limit 的若干段。"""
    if len(block) <= limit:
        return [block]
    segments: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in block.split("\n"):
        piece_len = len(line) + (1 if current else 0)
        while len(line) > limit:  # 单行本身就超限（少见），硬切
            if current:
                segments.append("\n".join(current))
                current, current_len = [], 0
            segments.append(line[:limit])
            line = line[limit:]
        piece_len = len(line) + (1 if current else 0)
        if current and current_len + piece_len > limit:
            segments.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += piece_len
    if current:
        segments.append("\n".join(current))
    return segments or [block]


def paginate_issue(
    issue: "Issue",
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
    limit: int = MESSAGE_CHAR_LIMIT,
) -> list[str]:
    """渲染整期早报为多条可发送的纯文本消息（分页）。

    打包策略：尽量按「条」成页（不把一条资讯拦腰截断），
    单条本身就超限时才按行硬拆；
    第一条带标题与（必要时）的缓存提示，最后一条带脚注，
    多页时每页标注「第 x/y 页」。
    """
    title = issue.title or f"橘鸦AI早报 {issue.date}".strip()
    footer = _fill(footer_template or DEFAULT_FOOTER, link=issue.link, date=issue.date, title=title)
    body = _strip_noise(issue.content or "")
    items = split_items(body)
    if max_items and max_items > 0 and len(items) > max_items:
        items = items[:max_items]
        items.append(f"……（仅展示前 {max_items} 条，完整内容见原文）")

    header = f"📰 {title}"
    if stale:
        header += f"\n（暂未抓到今天的一期，以下为最近缓存：{issue.date or '日期未知'}）"

    # 按条目打包；单条超限的再按行拆
    chunks: list[str] = []
    for item in items:
        if len(item) <= limit:
            chunks.append(item)
        else:
            chunks.extend(_split_long_block(item, limit))

    # 脚注尽量并进最后一个 chunk，装不下就单独成块
    if footer:
        if chunks:
            if len(chunks[-1]) + len(footer) + 2 <= limit:
                chunks[-1] = f"{chunks[-1]}\n\n{footer}"
            else:
                chunks.extend(_split_long_block(footer, limit))
        else:
            # 正文为空：脚注本身就是全部内容，不要再附加
            chunks = _split_long_block(footer, limit)

    # 把 chunk 序列打包进「页」，每页不超过 limit
    pages_body: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for chunk in chunks:
        add_len = len(chunk) + (2 if current else 0)
        if current and current_len + add_len > limit:
            pages_body.append(current)
            current, current_len = [], 0
            add_len = len(chunk)
        current.append(chunk)
        current_len += add_len
    if current:
        pages_body.append(current)

    pages: list[str] = []
    total = len(pages_body)
    for index, blocks in enumerate(pages_body):
        parts: list[str] = []
        if index == 0:
            parts.append(header)
        if total > 1:
            parts.append(f"（第 {index + 1}/{total} 页）")
        parts.append("\n\n".join(blocks))
        text = "\n".join(p for p in parts if p)
        if len(text) > limit:  # 兜底：绝不超限
            text = text[:limit]
        pages.append(text)
    return pages


def format_issue(
    issue: "Issue",
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
) -> str:
    """渲染一条可直接发送的纯文本消息（不分页，供测试与短内容使用）。"""
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

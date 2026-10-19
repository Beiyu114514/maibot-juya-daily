"""把一期早报渲染成群聊消息（纯文本或 QQ Markdown，均按单条长度自动分页）。

RSS 正文是站点页面转成的纯文本，结构大致是：

    AI 早报 2026-10-19
    视频版：...
    概览
    要闻
        标题 A ↗ #1
    技术与洞察
        标题 B ↗ #2
    要闻
        标题 A #1
        正文……
    技术与洞察
        标题 B #2
        正文……

渲染时要剥掉站点标题、视频版行、概览索引、分组小标题和页脚提示，
只留「导语（如有）+ 编号条目」。条目按消息长度分页，任何一页都不能超限。
"""

from __future__ import annotations

import html as _html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查用，运行时不引入 fetcher 依赖
    from .fetcher import Issue

DEFAULT_FOOTER = (
    "— 来源：橘鸦AI早报 {link}\n"
    "⚠️ 资讯内容由AI辅助生成，可能存在错误，请以原文为准"
)

# 单条消息的长度上限（QQ 群消息偏长也安全；按字符数算，不按字节）
MESSAGE_CHAR_LIMIT = 1800
# 多页时每页开头的「（第 x/y 页）」+ 换行占用的预算，装页时先预留，渲染后绝不超限
PAGE_MARKER_RESERVE = 16
# QQ 官方机器人 Markdown 单条上限 4000 字符（qq-official-adapter 的
# MAX_OUTBOUND_MARKDOWN_CHARS），这里留出余量，避免边界字符被平台拒绝
MARKDOWN_CHAR_LIMIT = 3800
# Markdown 多页时页码行 + 空行占用的预算
MARKDOWN_PAGE_MARKER_RESERVE = 24
# 条目被 max_items 截断时的提示；只截条目数量，不截正文
TRUNCATION_NOTE = "……（仅展示前 {n} 条，完整内容见原文）"

# 条目标题行：以 #N 结尾（概览索引行可能还带 ↗，后面单独过滤）
_ITEM_HEAD_RE = re.compile(r"(?m)^.{0,120}?#[0-9]{1,3}\s*$")
# 概览索引行：标题 ↗ #N 或 标题 #N ↗
_OVERVIEW_LINE_RE = re.compile(
    r"(?m)^.{0,160}?(?:↗[ \t]*#[0-9]{1,3}|#[0-9]{1,3}[ \t]*↗)[ \t]*$"
)
# 分组小标题：短行 + 空行 + 紧接着以 #N 结尾的条目标题行。
# 不硬编码「要闻 / 开发生态」等名单，站点改分组名也能剥掉。
_GROUP_TITLE_RE = re.compile(
    r"(?m)^([^\n#]{1,20})[ \t]*\n\s*\n(?=.{0,120}?#[0-9]{1,3}[ \t]*$)"
)
# 站点页脚提示（从「提示：内容由AI辅助创作」到文末的查看链接）
_TAIL_NOISE_RE = re.compile(
    r"提示：内容由AI辅助创作.*?查看网页全文(?:\s*·\s*查看 Markdown)?\s*$", re.DOTALL
)
# 视频版宣传行
_VIDEO_LINE_RE = re.compile(r"(?m)^视频版：[^\n]*$")
# 站点标题行（RSS 正文里会重复出现一次「AI 早报 YYYY-MM-DD」）
_SITE_TITLE_RE = re.compile(r"(?m)^AI 早报\s*\d{4}-\d{2}-\d{2}\s*$")

# 分组小标题是名词短语；以这些标点结尾或带链接的短行更像正文句子/URL，不能误删
_SENTENCE_END = tuple("。！？；：，、,.;:!?")


def _fill(template: str, **values: str) -> str:
    """安全替换 {name} 占位符：未知占位符原样保留，缺少的值不炸。"""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return str(values[key])
        return match.group(0)

    return re.sub(r"\{(\w+)\}", repl, template or "")


def _looks_like_group_title(name: str) -> bool:
    """短行是否像分组小标题（而不是恰好短一行的正文/URL）。"""
    name = name.strip()
    if not name or "://" in name:
        return False
    return not name.endswith(_SENTENCE_END)


def _strip_group_titles(text: str) -> str:
    return _GROUP_TITLE_RE.sub(
        lambda m: "" if _looks_like_group_title(m.group(1)) else m.group(0), text
    )


def _strip_overview_lines(text: str) -> str:
    return _OVERVIEW_LINE_RE.sub("", text).replace("\n\n\n", "\n\n").strip()


def _strip_noise(text: str) -> str:
    """反复剥噪音，直到概览索引和分组标题都清干净。

    先删概览行再找分组标题会漏掉「概览」和正文里的分组标题（P0-3）；
    这里交替跑两轮：先 group 再 overview，两轮后分组标题的 lookahead
    就不会被概览索引行挡住。
    """
    text = text or ""
    for _ in range(2):
        text = _strip_group_titles(text)
        text = _strip_overview_lines(text)
    text = _SITE_TITLE_RE.sub("", text)
    text = _TAIL_NOISE_RE.sub("", text)
    text = _VIDEO_LINE_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_issue(content: str | None) -> tuple[str, list[str]]:
    """把一期正文拆成（导语, 条目列表）。

    导语是第一条编号标题之前的文字；没有编号条目时导语为空、
    整段正文作为一个条目返回。`max_items` 只该约束条目数量，
    所以分页前必须先把导语和条目分开（P0-1）。
    """
    content = _strip_noise(content)
    if not content:
        return "", []
    heads = [m for m in _ITEM_HEAD_RE.finditer(content) if "↗" not in m.group(0)]
    if len(heads) < 2:
        return "", [content]

    intro = content[: heads[0].start()].strip()
    items: list[str] = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(content)
        item = _strip_noise(content[head.start() : end])
        if item:
            items.append(item)
    return intro, items


def split_items(content: str | None) -> list[str]:
    """兼容旧接口：导语（如有）放在第一位，后面是条目。"""
    intro, items = split_issue(content)
    return ([intro] if intro else []) + items


def _split_long_block(block: str, limit: int) -> list[str]:
    """把超长块按行拆成不超过 limit 的段；单行本身超限时硬切。"""
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current: list[str] = []
    used = 0
    for line in block.split("\n"):
        if len(line) > limit:
            if current:
                pieces.append("\n".join(current))
                current, used = [], 0
            for start in range(0, len(line), limit):
                pieces.append(line[start : start + limit])
            continue
        extra = len(line) + (1 if current else 0)
        if current and used + extra > limit:
            pieces.append("\n".join(current))
            current, used = [], 0
            extra = len(line)
        current.append(line)
        used += extra
    if current:
        pieces.append("\n".join(current))
    return pieces


def _pack_pages(
    chunks: list[str], limit: int, reserve: int = PAGE_MARKER_RESERVE
) -> list[list[str]]:
    """贪心装页：每页的块用空行连接后（含页码行）不超过 limit。"""
    budget = max(200, limit - reserve)
    pages: list[list[str]] = []
    current: list[str] = []
    used = 0
    for chunk in chunks:
        for piece in _split_long_block(chunk, budget):
            extra = len(piece) + (2 if current else 0)
            if current and used + extra > budget:
                pages.append(current)
                current, used = [], 0
                extra = len(piece)
            current.append(piece)
            used += extra
    if current:
        pages.append(current)
    return pages


def _prepare_issue(
    issue: Issue,
    max_items: int,
    footer_template: str,
) -> tuple[str, str, list[str], str, str]:
    """拆出标题、导语、条目、截断提示和脚注（纯文本 / Markdown 共用）。"""
    title = issue.title or f"橘鸦AI早报 {issue.date}".strip()
    footer = _fill(
        footer_template,
        title=title,
        link=issue.link or "",
        date=issue.date or "",
    )

    intro, items = split_issue(issue.content or "")
    note = ""
    if max_items and max_items > 0 and len(items) > max_items:
        items = items[:max_items]
        note = TRUNCATION_NOTE.format(n=max_items)
    return title, intro, items, note, footer


def _fit_header(header: str, budget: int) -> str:
    """超长标题只截标题本身，正文/脚注照常参与分页。"""
    if len(header) <= budget - 40:
        return header
    return header[: budget - 40].rstrip() + "…"


def paginate_issue(
    issue: Issue,
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
    limit: int = MESSAGE_CHAR_LIMIT,
) -> list[str]:
    """把一期渲染成若干条纯文本消息，每条都不超过 limit 个字符。

    - 导语独立成块，不占 `max_items` 的条数；
    - 条目数量截断只加提示，正文永不硬截断；
    - 脚注作为独立块参与分页，全页合计恰好出现一次。
    """
    title, intro, items, note, footer = _prepare_issue(issue, max_items, footer_template)

    header = f"📰 {title}"
    if stale:
        header += f"\n（暂未抓到今天的一期，以下为最近缓存：{issue.date or '日期未知'}）"
    budget = max(200, limit - PAGE_MARKER_RESERVE)
    header = _fit_header(header, budget)

    chunks: list[str] = [header]
    if intro:
        chunks.append(intro)
    chunks.extend(items)
    if note:
        chunks.append(note)
    if footer:
        chunks.append(footer)

    pages = _pack_pages(chunks, limit)
    if not pages:
        return []

    total = len(pages)
    rendered: list[str] = []
    for index, blocks in enumerate(pages):
        if index == 0:
            lines = [blocks[0]]
            if total > 1:
                lines.append(f"（第 1/{total} 页）")
            body = "\n\n".join(blocks[1:])
            if body:
                lines.append(body)
            rendered.append("\n".join(lines))
        else:
            rendered.append("\n".join([f"（第 {index + 1}/{total} 页）", "\n\n".join(blocks)]))
    # 装页时已预留页码预算；这里兜底，任何回归都会在测试里立刻暴露
    assert all(len(page) <= limit for page in rendered), "分页超过单条上限"
    return rendered


def _markdown_item(item: str) -> str:
    """条目转 Markdown：标题行加粗，正文另起一段。"""
    head, _, body = (item or "").partition("\n")
    head = head.strip()
    body = body.strip()
    if not head:
        return body
    if not body:
        return f"**{head}**"
    return f"**{head}**\n\n{body}"


def _markdown_footer(footer: str, link: str) -> str:
    """脚注转块引用，并把原文链接渲染成 QQ Markdown 可点击链接。"""
    link = (link or "").strip()
    if link:
        footer = footer.replace(link, f"[查看原文]({link})")
    lines = [line.strip() for line in (footer or "").splitlines() if line.strip()]
    return "\n".join(f"> {line}" for line in lines)


def paginate_issue_markdown(
    issue: Issue,
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
    limit: int = MARKDOWN_CHAR_LIMIT,
) -> list[str]:
    """把一期渲染成若干条 QQ Markdown 消息，每条都不超过 limit 个字符。

    只使用 QQ 官方 Markdown 支持的语法（`#`/`##` 标题、`**加粗**`、`>` 引用、
    `[文字](url)` 链接），不使用代码块/表格；分页与纯文本版一致。
    """
    title, intro, items, note, footer = _prepare_issue(issue, max_items, footer_template)

    header = f"## 📰 {title}"
    if stale:
        header += f"\n（暂未抓到今天的一期，以下为最近缓存：{issue.date or '日期未知'}）"
    budget = max(200, limit - MARKDOWN_PAGE_MARKER_RESERVE)
    header = _fit_header(header, budget)

    chunks: list[str] = [header]
    if intro:
        chunks.append(intro)
    chunks.extend(_markdown_item(item) for item in items)
    if note:
        chunks.append(f"> {note}")
    if footer:
        quoted_footer = _markdown_footer(footer, issue.link or "")
        if quoted_footer:
            chunks.append(quoted_footer)

    pages = _pack_pages(chunks, limit, reserve=MARKDOWN_PAGE_MARKER_RESERVE)
    if not pages:
        return []

    total = len(pages)
    rendered: list[str] = []
    for index, blocks in enumerate(pages):
        marker = f"（第 {index + 1}/{total} 页）" if total > 1 else ""
        if index == 0:
            parts = [blocks[0]]
            if marker:
                parts.append(marker)
            parts.extend(blocks[1:])
        else:
            parts = [marker, *blocks] if marker else list(blocks)
        rendered.append("\n\n".join(part for part in parts if part))
    assert all(len(page) <= limit for page in rendered), "Markdown 分页超过单条上限"
    return rendered


# 兜底降级：把本模块生成的 Markdown 还原成接近原样的纯文本
_MARKDOWN_HEADING_RE = re.compile(r"(?m)^#{1,6}\s*")
_MARKDOWN_QUOTE_RE = re.compile(r"(?m)^>\s?")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def markdown_to_plain(text: str) -> str:
    """把本模块生成的 Markdown 降级成纯文本（Markdown 发送失败时回退用）。"""
    if not text:
        return ""
    plain = _MARKDOWN_HEADING_RE.sub("", text)
    plain = _MARKDOWN_QUOTE_RE.sub("", plain)
    plain = _MARKDOWN_LINK_RE.sub(lambda match: f"{match.group(1)}（{match.group(2)}）", plain)
    plain = _MARKDOWN_BOLD_RE.sub(r"\1", plain)
    return plain


# ---------- 长图渲染（交给宿主 render.html2png 截图） ----------

IMAGE_CARD_WIDTH = 760
_IMAGE_FONT_STACK = (
    '"Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",'
    '"WenQuanYi Micro Hei",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
)
_IMAGE_CSS = """
:root{color-scheme:light only;}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:#eef1f7;scrollbar-width:none;-ms-overflow-style:none;}
body::-webkit-scrollbar{display:none;width:0;height:0;}
body{width:__WIDTH__px;font-family:__FONT__;color:#1b2330;-webkit-font-smoothing:antialiased;}
.card{width:__WIDTH__px;padding:26px 24px 24px;background:linear-gradient(180deg,#eef3fc 0%,#e9eef8 100%);}
.panel{background:#fff;border-radius:22px;padding:34px 32px 28px;box-shadow:0 12px 30px rgba(23,43,77,.08);}
.brand{display:flex;align-items:center;gap:9px;font-size:13px;letter-spacing:.14em;color:#64749a;text-transform:uppercase;}
.dot{width:9px;height:9px;border-radius:50%;background:#4f7cff;box-shadow:0 0 0 4px rgba(79,124,255,.14);}
h1{font-size:29px;line-height:1.4;font-weight:800;letter-spacing:-.01em;margin:14px 0 8px;color:#101b31;overflow-wrap:anywhere;}
.date{font-size:13px;color:#8492a8;margin:0 0 22px;}
.notice{font-size:13.5px;line-height:1.75;color:#8a5b12;background:#fff7e6;border:1px solid #ffe3ad;border-radius:12px;padding:10px 14px;margin:0 0 18px;white-space:pre-wrap;}
.intro{font-size:15px;line-height:1.85;color:#39445a;background:#f5f8ff;border-left:4px solid #4f7cff;border-radius:0 12px 12px 0;padding:14px 16px;margin:0 0 22px;white-space:pre-wrap;overflow-wrap:anywhere;}
.item{padding:20px 0;border-top:1px solid #edf0f6;}
.item:first-of-type{border-top:none;padding-top:4px;}
.item-title{font-size:18.5px;line-height:1.55;font-weight:700;color:#16233c;margin:0 0 10px;overflow-wrap:anywhere;}
.item-body{font-size:15px;line-height:1.9;color:#33405a;white-space:pre-wrap;overflow-wrap:anywhere;}
.footer{margin-top:26px;padding-top:18px;border-top:1px dashed #dfe5f0;font-size:12.5px;line-height:1.8;color:#8a94a6;white-space:pre-wrap;overflow-wrap:anywhere;}
"""


def _html_block(text: str) -> str:
    """转义一段要放进 HTML 的纯文本。"""
    return _html.escape(text or "", quote=False)


def render_issue_html(
    issue: Issue,
    max_items: int = 0,
    footer_template: str = DEFAULT_FOOTER,
    stale: bool = False,
    width: int = IMAGE_CARD_WIDTH,
) -> str:
    """把一期渲染成自包含 HTML，供宿主 ``render.html2png`` 截成长图。

    HTML 不引用任何外部资源（字体走系统字体栈，无图片/脚本），
    因此宿主浏览器不需要联网也能渲染；返回整页供 ``full_page`` 截图。
    """
    title, intro, items, note, footer = _prepare_issue(issue, max_items, footer_template)

    parts: list[str] = [
        '<div class="brand"><span class="dot"></span><span>JUYA DAILY · AI 早报</span></div>',
        f"<h1>{_html_block(title)}</h1>",
    ]
    if issue.date:
        parts.append(f'<p class="date">{_html_block(issue.date)}</p>')
    if stale:
        parts.append(
            '<div class="notice">暂未抓到今天的一期，以下为最近缓存：'
            f"{_html_block(issue.date or '日期未知')}</div>"
        )
    if intro:
        parts.append(f'<p class="intro">{_html_block(intro)}</p>')

    for item in items:
        head, _, body = (item or "").partition("\n")
        head = head.strip()
        body = body.strip()
        if not head and not body:
            continue
        article = ['<article class="item">']
        if head:
            article.append(f'<h2 class="item-title">{_html_block(head)}</h2>')
        if body:
            article.append(f'<div class="item-body">{_html_block(body)}</div>')
        article.append("</article>")
        parts.append("".join(article))

    if note:
        parts.append(f'<div class="notice">{_html_block(note)}</div>')
    if footer:
        parts.append(f'<div class="footer">{_html_block(footer)}</div>')

    css = (
        _IMAGE_CSS.replace("__WIDTH__", str(int(width)))
        .replace("__FONT__", _IMAGE_FONT_STACK)
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width={int(width)}">'
        '<meta name="color-scheme" content="light only">'
        f"<title>{_html_block(title)}</title>"
        f"<style>{css}</style></head>"
        '<body><main class="card"><section class="panel">'
        + "".join(parts)
        + "</section></main></body></html>"
    )

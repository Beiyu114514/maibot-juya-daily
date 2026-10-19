"""formatter 单元测试：噪音剥离、分条、分页不变量。"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "juya-daily"))

from fetcher import Issue  # noqa: E402
from formatter import (  # noqa: E402
    MARKDOWN_CHAR_LIMIT,
    MESSAGE_CHAR_LIMIT,
    _strip_noise,
    markdown_to_plain,
    paginate_issue,
    paginate_issue_markdown,
    render_issue_html,
    split_issue,
    split_items,
)

REAL_SHAPED_BODY = """概览
要闻速览 #1 ↗
生态更新 #2 ↗

要闻

某模型发布新版本 #1
这是第一条资讯的正文，包含一些细节。

开发生态

某框架发布 3.0 #2
这是第二条资讯的正文。

模型发布

某实验室放出新权重 #3
这是第三条资讯的正文。

视频版：https://example.com/video
提示：内容由AI辅助创作，仅供参考 · 查看网页全文 · 查看 Markdown"""

FIXTURE = os.path.join(HERE, "fixtures", "rss_2026-10-19.txt")
GROUP_NAMES = ("概览", "要闻", "技术与洞察", "前瞻与传闻")

INTRO_AND_ITEMS = (
    "导语：今天的早报有三条消息，我们逐条来看，先看模型发布。\n\n"
    "标题一 #1\n这里是第一条的正文，内容稍微长一点点，避免被当成分组标题。\n\n"
    "标题二 #2\n这里是第二条的正文，内容也稍微长一点点，避免被当成分组标题。\n\n"
    "标题三 #3\n这里是第三条的正文，内容同样长一点点，避免被当成分组标题。"
)


def load_real_body() -> str:
    with open(FIXTURE, encoding="utf-8") as handle:
        return handle.read()


def make_issue(content: str = REAL_SHAPED_BODY, **kwargs) -> Issue:
    base = dict(
        title="橘鸦AI早报 #123",
        link="https://daily.juya.uk/issue-123/",
        date="2026-06-15",
        pub_dt=None,
        content=content,
        source="rss",
    )
    base.update(kwargs)
    return Issue(**base)


def make_long_body(count: int = 11) -> str:
    return "\n\n".join(
        f"第 {i} 条标题 #{i}\n这里是正文，" + "很长的内容" * 100 for i in range(1, count + 1)
    )


class StripNoiseTest(unittest.TestCase):
    def test_overview_index_lines_removed(self) -> None:
        text = _strip_noise(REAL_SHAPED_BODY)
        self.assertNotIn("↗", text)

    def test_group_titles_removed_by_shape_not_hardcoded(self) -> None:
        """分组名不写死：换任何分组名（含站点新增的）都应被剥掉。"""
        text = _strip_noise(REAL_SHAPED_BODY)
        for name in ("要闻", "开发生态", "模型发布"):
            self.assertNotIn(f"\n{name}\n", text)
        # 换成没见过的分组名同样有效
        body = "全新分组\n\n某条目标题 #1\n正文"
        self.assertNotIn("全新分组", _strip_noise(body))

    def test_tail_noise_and_video_line_removed(self) -> None:
        text = _strip_noise(REAL_SHAPED_BODY)
        self.assertNotIn("视频版", text)
        self.assertNotIn("AI辅助创作", text)
        self.assertNotIn("查看网页全文", text)

    def test_body_short_line_not_mistaken_for_group_title(self) -> None:
        """单独成行的短句，下一行不是编号标题时不能误删。"""
        body = "要闻\n\n这是普通的正文段落，没有编号标题。"
        self.assertIn("要闻", _strip_noise(body))

    def test_site_title_and_real_group_names_removed(self) -> None:
        """真实一期：站点标题、概览索引、两组分组标题全部剥干净（P0-3）。"""
        text = _strip_noise(load_real_body())
        lines = {line.strip() for line in text.splitlines() if line.strip()}
        for name in GROUP_NAMES:
            self.assertNotIn(name, lines)
        self.assertNotIn("AI 早报 2026-", text)
        self.assertNotIn("↗", text)
        self.assertTrue(text.startswith("Anthropic 设公益基金，先投 5% 股份 #1"))

    def test_short_prose_and_url_lines_preserved(self) -> None:
        """两轮剥离不能把短正文行/短链接误当成分组标题删掉。"""
        body = (
            "开篇正文句子。\n\n"
            "某条目标题 #1\n正文\n\n"
            "这是第二段正文。\n\n"
            "另一条目标题 #2\n正文"
        )
        text = _strip_noise(body)
        self.assertIn("开篇正文句子。", text)
        self.assertIn("这是第二段正文。", text)

        with_url = "某条目标题 #1\n正文\n\nhttps://a.cn/x\n\n另一条目标题 #2\n正文"
        self.assertIn("https://a.cn/x", _strip_noise(with_url))


class SplitIssueTest(unittest.TestCase):
    def test_intro_separated_from_items(self) -> None:
        intro, items = split_issue(INTRO_AND_ITEMS)
        self.assertTrue(intro.startswith("导语："))
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0].startswith("标题一 #1"))
        self.assertTrue(items[2].startswith("标题三 #3"))

    def test_split_items_keeps_intro_first_for_compat(self) -> None:
        intro, items = split_issue(INTRO_AND_ITEMS)
        self.assertEqual(split_items(INTRO_AND_ITEMS), [intro] + items)

    def test_no_numbered_items_returns_empty_intro(self) -> None:
        self.assertEqual(split_issue("没有编号的整段内容"), ("", ["没有编号的整段内容"]))
        self.assertEqual(split_issue(""), ("", []))
        self.assertEqual(split_issue(None), ("", []))


class SplitItemsTest(unittest.TestCase):
    def test_splits_into_items_without_group_titles(self) -> None:
        items = split_items(_strip_noise(REAL_SHAPED_BODY))
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertIn("#", item)

    def test_unmatched_content_returned_whole(self) -> None:
        self.assertEqual(split_items("没有编号的整段内容"), ["没有编号的整段内容"])

    def test_empty_content(self) -> None:
        self.assertEqual(split_items(""), [])
        self.assertEqual(split_items(None), [])


class RealIssueTest(unittest.TestCase):
    """用真实 2026-10-19 一期（RSS 正文经 clean_body）做端到端断言。"""

    def setUp(self) -> None:
        self.body = load_real_body()
        self.issue = make_issue(
            self.body,
            title="2026-10-19",
            link="https://daily.juya.uk/issues/2026-10-19/",
            date="2026-10-19",
        )

    def test_four_items_and_empty_intro(self) -> None:
        intro, items = split_issue(self.body)
        self.assertEqual(intro, "")
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("Anthropic 设公益基金，先投 5% 股份 #1"))

    def test_output_has_no_overview_or_group_titles(self) -> None:
        rendered = "\n".join(paginate_issue(self.issue))
        self.assertNotIn("↗", rendered)
        self.assertNotIn("AI 早报 2026-", rendered)
        lines = {line.strip() for line in rendered.splitlines() if line.strip()}
        for name in GROUP_NAMES:
            self.assertNotIn(name, lines)
        self.assertNotIn("查看网页全文", rendered)
        self.assertNotIn("视频版", rendered)

    def test_first_line_is_header_then_first_item(self) -> None:
        pages = paginate_issue(self.issue)
        lines = pages[0].splitlines()
        self.assertTrue(lines[0].startswith("📰 2026-10-19"))
        # header 之后是页码行（多页时）或第一条资讯，绝不能是空导语块
        self.assertTrue(lines[1].strip())
        rest = lines[2:] if lines[1].startswith("（第 ") else lines[1:]
        self.assertTrue(rest and rest[0].startswith("Anthropic 设公益基金，先投 5% 股份 #1"))

    def test_max_items_one_counts_items_not_intro(self) -> None:
        rendered = "\n".join(paginate_issue(self.issue, max_items=1))
        self.assertIn("Anthropic 设公益基金", rendered)
        self.assertNotIn("开源模型背后的训练算力", rendered)
        self.assertNotIn("OpenAI 据称出现 3 个新模型 ID", rendered)
        self.assertIn("仅展示前 1 条", rendered)

    def test_small_limit_keeps_pages_and_content_intact(self) -> None:
        pages = paginate_issue(self.issue, limit=300)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), 300)
        rendered = "\n".join(pages)
        self.assertEqual(rendered.count("来源：橘鸦AI早报"), 1)
        self.assertIn("Anthropic 设公益基金，先投 5% 股份 #1", rendered)
        tail = _strip_noise(self.body).rstrip().splitlines()[-1]
        self.assertIn(tail, rendered)


class PaginateTest(unittest.TestCase):
    def test_long_issue_is_split_and_within_limit(self) -> None:
        pages = paginate_issue(make_issue(make_long_body()))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), MESSAGE_CHAR_LIMIT)

    def test_header_on_first_page_and_page_markers(self) -> None:
        pages = paginate_issue(make_issue(make_long_body()))
        self.assertTrue(pages[0].startswith("📰 橘鸦AI早报 #123"))
        self.assertIn("（第 1/", pages[0])
        for index, page in enumerate(pages, start=1):
            self.assertIn(f"（第 {index}/{len(pages)} 页）", page)

    def test_footer_only_on_last_page(self) -> None:
        pages = paginate_issue(make_issue(make_long_body()))
        for page in pages[:-1]:
            self.assertNotIn("来源：橘鸦AI早报", page)
        self.assertIn("来源：橘鸦AI早报 https://daily.juya.uk/issue-123/", pages[-1])
        self.assertIn("AI辅助生成", pages[-1])

    def test_all_items_and_footer_exactly_once(self) -> None:
        pages = paginate_issue(make_issue(make_long_body()))
        rendered = "\n".join(pages)
        self.assertEqual(rendered.count("来源：橘鸦AI早报"), 1)
        for i in range(1, 12):
            self.assertEqual(rendered.count(f"第 {i} 条标题 #{i}"), 1)

    def test_items_not_split_across_pages(self) -> None:
        """条目边界完整：标题行与其正文必须在同一页。"""
        body = "\n\n".join(
            f"第 {i} 条标题 #{i}\n这里是第 {i} 条的正文，" + "很长的内容" * 60
            for i in range(1, 12)
        )
        for page in paginate_issue(make_issue(body)):
            for marker in (f"第 {i} 条标题 #{i}" for i in range(1, 12)):
                if marker in page:
                    tail = page.split(marker, 1)[1]
                    self.assertTrue(
                        tail.lstrip().startswith("这里是"),
                        f"{marker} 的标题与正文被分到了两页",
                    )

    def test_max_items_truncation_note(self) -> None:
        pages = paginate_issue(make_issue(), max_items=2)
        self.assertIn("仅展示前 2 条", pages[-1])

    def test_max_items_does_not_count_intro(self) -> None:
        rendered = "\n".join(paginate_issue(make_issue(INTRO_AND_ITEMS), max_items=1))
        self.assertIn("导语：", rendered)
        self.assertIn("标题一 #1", rendered)
        self.assertNotIn("标题二 #2", rendered)
        self.assertIn("仅展示前 1 条", rendered)

    def test_max_items_not_smaller_than_item_count_has_no_note(self) -> None:
        for max_items in (3, 10):
            pages = paginate_issue(make_issue(), max_items=max_items)
            self.assertNotIn("仅展示前", "\n".join(pages))

    def test_custom_limit_pages_all_within_limit(self) -> None:
        pages = paginate_issue(make_issue(make_long_body()), limit=400)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), 400)

    def test_empty_link_footer_still_renders(self) -> None:
        pages = paginate_issue(make_issue(link=""))
        self.assertEqual(pages[-1].count("来源：橘鸦AI早报"), 1)

    def test_empty_body_no_duplicated_footer(self) -> None:
        pages = paginate_issue(make_issue(""))
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].count("来源：橘鸦AI早报"), 1)

    def test_site_title_and_video_only_body(self) -> None:
        body = "AI 早报 2026-10-19\n\n视频版：YouTube ｜ 哔哩哔哩\n\n" \
               "提示：内容由AI辅助创作，仅供参考 · 查看网页全文"
        pages = paginate_issue(make_issue(body))
        self.assertEqual(len(pages), 1)
        self.assertNotIn("视频版", pages[0])
        self.assertNotIn("AI 早报 2026-", pages[0])
        self.assertEqual(pages[0].count("来源：橘鸦AI早报"), 1)

    def test_custom_footer_template(self) -> None:
        pages = paginate_issue(make_issue(), footer_template="来源 {link} · {date}")
        self.assertIn("来源 https://daily.juya.uk/issue-123/ · 2026-06-15", pages[-1])

    def test_stale_notice(self) -> None:
        pages = paginate_issue(make_issue(), stale=True)
        self.assertIn("暂未抓到今天的一期", pages[0])

    def test_single_line_over_limit_is_hard_split(self) -> None:
        pages = paginate_issue(make_issue("x" * (MESSAGE_CHAR_LIMIT * 2 + 10)))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), MESSAGE_CHAR_LIMIT)

    def test_long_title_only_truncates_title(self) -> None:
        """超长标题只截标题，正文与脚注完整保留（P0-2 死点）。"""
        title = "超长标题" * 600  # 2400 字
        body = "第 1 条标题 #1\n" + "正文内容" * 100  # 400 字
        pages = paginate_issue(make_issue(body, title=title))
        rendered = "\n".join(pages)
        for page in pages:
            self.assertLessEqual(len(page), MESSAGE_CHAR_LIMIT)
        self.assertIn("…", pages[0])
        self.assertNotIn(title, rendered)
        self.assertIn("正文内容" * 100, rendered)
        self.assertEqual(rendered.count("来源：橘鸦AI早报"), 1)

    def test_1780_char_body_keeps_footer(self) -> None:
        """1600+ 字正文曾把脚注挤出消息；现在正文完整、脚注仍在。"""
        body = "第 1 条标题 #1\n" + "正" * 1780
        rendered = "\n".join(paginate_issue(make_issue(body, title="橘鸦AI早报 #123")))
        self.assertIn("正" * 1780, rendered)
        self.assertEqual(rendered.count("来源：橘鸦AI早报"), 1)


class FooterTemplateTest(unittest.TestCase):
    def test_footer_placeholder_filled(self) -> None:
        text = "\n".join(paginate_issue(make_issue()))
        self.assertIn("来源：橘鸦AI早报 https://daily.juya.uk/issue-123/", text)
        self.assertTrue(text.startswith("📰 橘鸦AI早报 #123"))

    def test_unknown_placeholder_kept(self) -> None:
        text = "\n".join(paginate_issue(make_issue(), footer_template="{unknown} {link}"))
        self.assertIn("{unknown}", text)
        self.assertIn("https://daily.juya.uk/issue-123/", text)


class MarkdownFormatterTest(unittest.TestCase):
    """QQ Markdown 渲染：只用平台支持的语法，长度按适配器上限兜底。"""

    def test_render_uses_supported_markdown(self) -> None:
        pages = paginate_issue_markdown(make_issue())
        text = "\n\n".join(pages)
        self.assertTrue(pages[0].startswith("## 📰 橘鸦AI早报 #123"))
        self.assertIn("**某模型发布新版本 #1**", text)
        self.assertIn("[查看原文](https://daily.juya.uk/issue-123/)", text)
        self.assertIn("> — 来源：橘鸦AI早报", text)
        self.assertEqual(text.count("来源：橘鸦AI早报"), 1)

    def test_each_page_within_limit(self) -> None:
        body = "第 1 条标题 #1\n" + "正" * (MARKDOWN_CHAR_LIMIT * 2)
        for limit in (300, 400, MARKDOWN_CHAR_LIMIT):
            pages = paginate_issue_markdown(make_issue(body), limit=limit)
            self.assertGreater(len(pages), 1)
            for page in pages:
                self.assertLessEqual(len(page), limit)
            # 正文不丢字：分页只插页码，不截断内容
            self.assertEqual("\n".join(pages).count("正"), MARKDOWN_CHAR_LIMIT * 2)

    def test_page_marker_only_when_multiple_pages(self) -> None:
        single = paginate_issue_markdown(make_issue())
        self.assertEqual(len(single), 1)
        self.assertNotIn("（第 1/", single[0])

        pages = paginate_issue_markdown(make_issue("第 1 条标题 #1\n" + "正" * 2000), limit=300)
        self.assertGreater(len(pages), 1)
        self.assertIn("（第 1/", pages[0])
        self.assertIn(f"（第 {len(pages)}/{len(pages)} 页）", pages[-1])

    def test_stale_and_max_items(self) -> None:
        pages = paginate_issue_markdown(make_issue(), max_items=1, stale=True)
        text = "\n".join(pages)
        self.assertIn("暂未抓到今天的一期", text)
        self.assertIn("仅展示前 1 条", text)
        self.assertIn("**某模型发布新版本 #1**", text)
        self.assertNotIn("某框架发布 3.0", text)

    def test_custom_footer_linkified(self) -> None:
        pages = paginate_issue_markdown(
            make_issue(), footer_template="来源：{title} {link}"
        )
        self.assertIn(
            "> 来源：橘鸦AI早报 #123 [查看原文](https://daily.juya.uk/issue-123/)",
            pages[-1],
        )

    def test_markdown_to_plain_strips_syntax(self) -> None:
        md = "## 📰 标题\n\n**条目 #1**\n\n> — 来源：[查看原文](https://x.test/a)"
        plain = markdown_to_plain(md)
        self.assertNotIn("##", plain)
        self.assertNotIn("**", plain)
        self.assertNotIn("](", plain)
        self.assertIn("📰 标题", plain)
        self.assertIn("来源：查看原文（https://x.test/a）", plain)

    def test_markdown_limit_leaves_room_for_adapter_cap(self) -> None:
        self.assertEqual(MARKDOWN_CHAR_LIMIT, 3800)
        self.assertLess(MARKDOWN_CHAR_LIMIT, 4000)  # qq-official-adapter 的硬上限


class ImageHtmlTest(unittest.TestCase):
    """长图 HTML：自包含、转义、宽度与结构正确。"""

    def test_render_issue_html_structure(self) -> None:
        html = render_issue_html(make_issue(REAL_SHAPED_BODY))
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("橘鸦AI早报 #123", html)
        self.assertIn("某模型发布新版本 #1", html)
        self.assertIn('class="item-title"', html)
        self.assertIn('class="item-body"', html)
        self.assertIn("来源：橘鸦AI早报", html)
        self.assertIn("width:760px", html)
        # 不引用外部资源，浏览器无需联网
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("<img", html)

    def test_render_issue_html_escapes_markup(self) -> None:
        issue = make_issue("<script>alert(1)</script> & 标题 #1\n正文 <b>加粗</b>")
        html = render_issue_html(issue)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)
        self.assertIn("&lt;b&gt;", html)

    def test_render_issue_html_stale_and_max_items(self) -> None:
        html = render_issue_html(make_issue(), max_items=1, stale=True, width=720)
        self.assertIn("暂未抓到今天的一期", html)
        self.assertIn("仅展示前 1 条", html)
        self.assertIn("width:720px", html)


if __name__ == "__main__":
    unittest.main()

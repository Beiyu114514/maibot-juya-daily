"""formatter 单元测试：噪音剥离、分条、分页。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "juya-daily"))

from fetcher import Issue  # noqa: E402
from formatter import (  # noqa: E402
    MESSAGE_CHAR_LIMIT,
    _strip_noise,
    format_issue,
    paginate_issue,
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


class PaginateTest(unittest.TestCase):
    def test_long_issue_is_split_and_within_limit(self) -> None:
        body = "\n\n".join(
            f"第 {i} 条标题 #{i}\n这里是正文，" + "很长的内容" * 100 for i in range(1, 12)
        )
        pages = paginate_issue(make_issue(body))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), MESSAGE_CHAR_LIMIT)

    def test_header_on_first_page_and_page_markers(self) -> None:
        body = "\n\n".join(
            f"第 {i} 条标题 #{i}\n这里是正文，" + "很长的内容" * 100 for i in range(1, 12)
        )
        pages = paginate_issue(make_issue(body))
        self.assertTrue(pages[0].startswith("📰 橘鸦AI早报 #123"))
        self.assertIn("（第 1/", pages[0])
        for index, page in enumerate(pages, start=1):
            self.assertIn(f"（第 {index}/{len(pages)} 页）", page)

    def test_footer_only_on_last_page(self) -> None:
        body = "\n\n".join(
            f"第 {i} 条标题 #{i}\n这里是正文，" + "很长的内容" * 100 for i in range(1, 12)
        )
        pages = paginate_issue(make_issue(body))
        for page in pages[:-1]:
            self.assertNotIn("来源：橘鸦AI早报", page)
        self.assertIn("来源：橘鸦AI早报 https://daily.juya.uk/issue-123/", pages[-1])
        self.assertIn("AI辅助生成", pages[-1])

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

    def test_empty_body_no_duplicated_footer(self) -> None:
        pages = paginate_issue(make_issue(""))
        self.assertEqual(len(pages), 1)
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


class FormatIssueTest(unittest.TestCase):
    def test_footer_placeholder_filled(self) -> None:
        text = format_issue(make_issue())
        self.assertIn("https://daily.juya.uk/issue-123/", text)
        self.assertTrue(text.startswith("📰 橘鸦AI早报 #123"))

    def test_unknown_placeholder_kept(self) -> None:
        text = format_issue(make_issue(), footer_template="{unknown} {link}")
        self.assertIn("{unknown}", text)


if __name__ == "__main__":
    unittest.main()

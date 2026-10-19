"""真实站点集成测试（默认跳过，需显式打开）。

    JUYA_LIVE=1 python3 -m unittest discover -s tests -v

会真的请求 https://daily.juya.uk/rss.xml，用来在发布前验证：
解析、噪音剥离、分页长度上限、脚注占位符，全部走真实数据。
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "juya-daily"))

from fetcher import RSS_URL, JuyaFetcher, parse_rss, today_cst  # noqa: E402
from formatter import (  # noqa: E402
    MARKDOWN_CHAR_LIMIT,
    MESSAGE_CHAR_LIMIT,
    paginate_issue,
    paginate_issue_markdown,
)

LIVE = os.environ.get("JUYA_LIVE") == "1"


@unittest.skipUnless(LIVE, "设置 JUYA_LIVE=1 才请求真实站点")
class LiveRssTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_and_render_latest(self) -> None:
        import logging

        logger = logging.getLogger("live-rss")
        logger.addHandler(logging.NullHandler())
        fetcher = JuyaFetcher(logger)
        issue = await fetcher.fetch_latest(force=True)
        self.assertIsNotNone(issue, "真实站点应能抓到至少一期")
        assert issue is not None
        self.assertTrue(issue.title)
        self.assertRegex(issue.link, r"^https://daily\.juya\.uk/issue")
        self.assertIn(issue.source, ("rss", "html"))

        pages = paginate_issue(issue, max_items=3)
        self.assertTrue(pages)
        for page in pages:
            self.assertLessEqual(len(page), MESSAGE_CHAR_LIMIT)
            self.assertTrue(page.strip(), "每一页都应有内容")
        self.assertIn("📰", pages[0], "首页应带标题")
        self.assertIn("https://daily.juya.uk/", pages[-1])

        # 同一期也走一遍 Markdown 渲染：真实数据下的语法与长度上限
        md_pages = paginate_issue_markdown(issue, max_items=3)
        self.assertTrue(md_pages)
        for page in md_pages:
            self.assertLessEqual(len(page), MARKDOWN_CHAR_LIMIT)
            self.assertTrue(page.strip(), "每一页都应有内容")
        self.assertTrue(md_pages[0].startswith("## 📰"), "Markdown 首页应以二级标题开头")
        self.assertIn("[查看原文](https://daily.juya.uk/", md_pages[-1])
        self.assertNotIn("↗", "\n".join(pages))  # 概览索引行已剥离
        self.assertNotIn("AI辅助创作", "\n".join(pages))  # 站点页脚噪音已剥离

        print(f"[live] 抓到《{issue.title}》 date={issue.date} 共 {len(pages)} 页")
        print(f"[live] 今天（东八区）是 {today_cst()}")

    async def test_rss_parses_many_issues(self) -> None:
        import urllib.request

        req = urllib.request.Request(RSS_URL, headers={"User-Agent": "juya-daily-test"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        issues = parse_rss(raw)
        self.assertGreater(len(issues), 3, "RSS 应包含多期历史")
        dates = [i.date for i in issues if i.date]
        self.assertTrue(dates, "至少有一期能抽出日期")


if __name__ == "__main__":
    unittest.main()

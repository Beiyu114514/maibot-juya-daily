"""fetcher 单元测试：RSS 解析、日期抽取、正文清洗（全部离线）。"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "juya-daily"))

from fetcher import (  # noqa: E402
    _ISSUE_LINK_RE,
    _extract_date,
    _parse_pub_date,
    clean_body,
    parse_rss,
    pick_latest,
)


def _issue_links(html: str) -> list:
    return _ISSUE_LINK_RE.findall(html)

LOGGER = logging.getLogger("test-fetcher")
LOGGER.addHandler(logging.NullHandler())

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>橘鸦AI早报</title>
    <item>
      <title>橘鸦AI早报 #200</title>
      <link>https://daily.juya.uk/issue-200/</link>
      <pubDate>Mon, 15 Jun 2026 00:45:53 +0000</pubDate>
      <description><![CDATA[<p>第一条标题 <a href="https://example.com/a">链接</a> #1</p><p>正文内容。</p>]]></description>
    </item>
    <item>
      <title>橘鸦AI早报 #199</title>
      <link>https://daily.juya.uk/issue-199/</link>
      <pubDate>Sun, 14 Jun 2026 00:45:53 +0000</pubDate>
      <description>较早的一期</description>
    </item>
  </channel>
</rss>
"""


class ParseRssTest(unittest.TestCase):
    def test_parse_items(self) -> None:
        issues = parse_rss(RSS_XML)
        self.assertEqual(len(issues), 2)
        first = issues[0]
        self.assertEqual(first.title, "橘鸦AI早报 #200")
        self.assertEqual(first.link, "https://daily.juya.uk/issue-200/")
        self.assertEqual(first.pub_dt, datetime(2026, 6, 15, 0, 45, 53, tzinfo=timezone.utc))
        self.assertEqual(first.source, "rss")

    def test_date_prefers_title_over_pubdate(self) -> None:
        """标题带 #日期 时以标题为准，pubDate 只作兜底。"""
        issues = parse_rss(RSS_XML.replace("橘鸦AI早报 #200", "橘鸦AI早报 #2026-06-14"))
        self.assertEqual(issues[0].date, "2026-06-14")

    def test_date_falls_back_to_pubdate(self) -> None:
        issues = parse_rss(RSS_XML)
        self.assertEqual(issues[0].date, "2026-06-15")

    def test_bad_pubdate_falls_back_to_epoch(self) -> None:
        """解析失败的 pubDate 不能回退 now()，否则会抢占最新一期。"""
        xml = RSS_XML.replace("Mon, 15 Jun 2026 00:45:53 +0000", "昨天中午")
        issues = parse_rss(xml)
        self.assertEqual(issues[0].pub_dt, datetime(1970, 1, 1, tzinfo=timezone.utc))

    def test_pick_latest(self) -> None:
        latest = pick_latest(parse_rss(RSS_XML))
        self.assertEqual(latest.link, "https://daily.juya.uk/issue-200/")

    def test_pick_latest_empty(self) -> None:
        self.assertIsNone(pick_latest([]))

    def test_bad_pubdate_does_not_win_latest(self) -> None:
        xml = RSS_XML.replace("Mon, 15 Jun 2026 00:45:53 +0000", "坏日期")
        latest = pick_latest(parse_rss(xml))
        self.assertEqual(latest.link, "https://daily.juya.uk/issue-199/")


class IssueLinkRegexTest(unittest.TestCase):
    """站点换过链接格式：/issue-N/ → /issues/YYYY-MM-DD/，两种都要能兜底抓到。"""

    def test_legacy_issue_number_links(self) -> None:
        html = '<a href="https://daily.juya.uk/issue-123/">#123</a>'
        self.assertEqual(_issue_links(html), ["https://daily.juya.uk/issue-123/"])

    def test_date_based_links(self) -> None:
        html = '<link rel="canonical" href="https://daily.juya.uk/issues/2026-10-19/">'
        self.assertEqual(
            _issue_links(html), ["https://daily.juya.uk/issues/2026-10-19/"]
        )

    def test_relative_date_based_links(self) -> None:
        html = "<a href='/issues/2026-10-18/'>上一期</a>"
        self.assertEqual(_issue_links(html), ["/issues/2026-10-18/"])

    def test_other_links_ignored(self) -> None:
        html = '<a href="https://example.com/">外链</a><a href="/about/">关于</a>'
        self.assertEqual(_issue_links(html), [])


class ExtractDateTest(unittest.TestCase):
    def test_iso_date(self) -> None:
        self.assertEqual(_extract_date("橘鸦AI早报 #2026-06-14"), "2026-06-14")

    def test_cn_date(self) -> None:
        self.assertEqual(_extract_date("2026年6月14日 早报"), "2026-06-14")

    def test_invalid_month_day(self) -> None:
        self.assertIsNone(_extract_date("2026-13-40 不存在"))

    def test_none_input(self) -> None:
        self.assertIsNone(_extract_date(None))


class CleanBodyTest(unittest.TestCase):
    def test_markdown_link_to_text_plus_url(self) -> None:
        self.assertEqual(clean_body("看 [这个](https://example.com) 吧"), "看 这个 https://example.com 吧")

    def test_html_tags_stripped(self) -> None:
        self.assertEqual(clean_body("<p>正文<b>加粗</b></p>"), "正文加粗")

    def test_escaped_html_tags_stripped(self) -> None:
        """RSS CDATA 里的整期正文是转义后的 HTML，反转义后要连标签一起去掉。"""
        self.assertEqual(clean_body("&lt;p&gt;正文&lt;/p&gt;"), "正文")

    def test_entities_unescaped(self) -> None:
        self.assertEqual(clean_body("a &amp; b &quot;c&quot;"), 'a & b "c"')

    def test_whitespace_normalized(self) -> None:
        self.assertEqual(clean_body("  a \n\n\n b  "), "a\n\nb")


class ParsePubDateTest(unittest.TestCase):
    def test_rfc822(self) -> None:
        self.assertEqual(
            _parse_pub_date("Sat, 13 Jun 2026 00:45:53 +0000"),
            datetime(2026, 6, 13, 0, 45, 53, tzinfo=timezone.utc),
        )

    def test_iso8601(self) -> None:
        parsed = _parse_pub_date("2026-06-13T00:45:53+08:00")
        self.assertEqual(parsed, datetime(2026, 6, 12, 16, 45, 53, tzinfo=timezone.utc))

    def test_naive_datetime_treated_as_utc(self) -> None:
        parsed = _parse_pub_date("2026-06-13T00:45:53")
        self.assertIsNotNone(parsed.tzinfo)


if __name__ == "__main__":
    unittest.main()

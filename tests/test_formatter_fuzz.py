"""分页不变量 fuzz：固定种子随机生成 200 期，检查输出永不越界/不丢内容。

不变量（CODE_REVIEW_FIX_PLAN.md §1.2）：
1. 每一页长度 ≤ limit；
2. 脚注全页合计恰好出现 1 次；
3. 所有条目的首行都出现在输出中；
4. 多页时页码行齐全且编号正确。
"""

from __future__ import annotations

import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "juya-daily"))

from fetcher import Issue  # noqa: E402
from formatter import MESSAGE_CHAR_LIMIT, paginate_issue  # noqa: E402

SEED = 20261019
CASES = 200
LIMITS = (300, 600, 1200, MESSAGE_CHAR_LIMIT)
TEXT_POOL = "资讯正文内容测试数据模型发布更新行业动态产品应用生态观察"
TITLE_POOL = "橘鸦AI早报标题"


def make_issue(content: str, title: str) -> Issue:
    return Issue(
        title=title,
        link="https://daily.juya.uk/issues/fuzz/",
        date="2026-10-19",
        pub_dt=None,
        content=content,
        source="rss",
    )


def random_paragraph(rng: random.Random) -> str:
    # 30~250 字，长于分组标题上限 20，且短于最小分页预算，保证整段不被拆开
    length = rng.randint(30, 250)
    return "".join(rng.choice(TEXT_POOL) for _ in range(length))


class PaginationFuzzTest(unittest.TestCase):
    def test_random_issues_keep_page_invariants(self) -> None:
        rng = random.Random(SEED)
        for case in range(CASES):
            with self.subTest(case=case):
                item_count = rng.randint(2, 10)
                limit = rng.choice(LIMITS)
                title_len = rng.choice((5, 60, 200, 600, 1800, 3000))
                title = "".join(rng.choice(TITLE_POOL) for _ in range(title_len))

                intro = ""
                if rng.random() < 0.5:
                    intro = "这是本期的导语段落，" + random_paragraph(rng) + "。"

                item_titles: list[str] = []
                paragraphs: list[str] = []
                blocks: list[str] = []
                if intro:
                    blocks.append(intro)
                for index in range(1, item_count + 1):
                    item_title = f"第 {index} 条测试标题 #{index}"
                    item_titles.append(item_title)
                    body = "\n\n".join(
                        random_paragraph(rng) for _ in range(rng.randint(1, 3))
                    )
                    paragraphs.extend(body.split("\n\n"))
                    blocks.append(f"{item_title}\n\n{body}")
                content = "\n\n".join(blocks)

                pages = paginate_issue(make_issue(content, title), limit=limit)
                rendered = "\n".join(pages)

                self.assertTrue(pages)
                for page in pages:
                    self.assertLessEqual(len(page), limit)
                self.assertEqual(rendered.count("来源：橘鸦AI早报"), 1)
                for item_title in item_titles:
                    self.assertEqual(rendered.count(item_title), 1, item_title)
                for paragraph in paragraphs:
                    self.assertIn(paragraph, rendered)
                if intro:
                    self.assertIn(intro, rendered)

                if len(pages) > 1:
                    self.assertIn(f"（第 1/{len(pages)} 页）", pages[0])
                    for number, page in enumerate(pages, start=1):
                        self.assertIn(f"（第 {number}/{len(pages)} 页）", page)
                else:
                    self.assertNotIn("页）", rendered)


if __name__ == "__main__":
    unittest.main()

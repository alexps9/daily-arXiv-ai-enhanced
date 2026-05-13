"""
数据处理 Pipeline
- arXiv 来源：通过 arxiv 库补全论文详情（标题、作者、摘要等）
- Semantic Scholar 来源：字段已由爬虫直接填充，跳过 arxiv 补全
"""
import arxiv
import json
import os
import sys
from datetime import datetime, timedelta


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        self.client = arxiv.Client(self.page_size)
        self._seen_ids: set = set()

    def open_spider(self, spider):
        self._seen_ids = set()

    def process_item(self, item: dict, spider):
        paper_id = item.get("id", "")

        # 全局去重（同一次运行内）
        if paper_id in self._seen_ids:
            spider.logger.debug(f"[pipeline] Duplicate skipped: {paper_id}")
            return None
        self._seen_ids.add(paper_id)

        source = item.get("source", "arxiv")

        if source == "arxiv":
            return self._enrich_arxiv(item, spider)
        else:
            return self._enrich_conference(item, spider)

    def _enrich_arxiv(self, item: dict, spider) -> dict:
        """通过 arxiv Python 库补全 arXiv 论文字段"""
        arxiv_id = item["id"]
        try:
            search = arxiv.Search(id_list=[arxiv_id])
            paper = next(self.client.results(search))
            item["pdf"] = f"https://arxiv.org/pdf/{arxiv_id}"
            item["abs"] = f"https://arxiv.org/abs/{arxiv_id}"
            item["authors"] = [a.name for a in paper.authors]
            item["title"] = paper.title
            item["categories"] = paper.categories
            item["comment"] = paper.comment
            item["summary"] = paper.summary
            item["year"] = paper.published.year if paper.published else None
            item["venue"] = "arXiv"
            item.setdefault("source_display", "arXiv")
        except StopIteration:
            spider.logger.warning(f"[pipeline] arxiv paper not found: {arxiv_id}")
            return None
        except Exception as e:
            spider.logger.error(f"[pipeline] Failed to fetch arxiv paper {arxiv_id}: {e}")
            return None
        return item

    def _enrich_conference(self, item: dict, spider) -> dict:
        """会议论文字段已由 Semantic Scholar 爬虫填充，仅做补全验证"""
        required = ["title", "summary", "abs"]
        for field in required:
            if not item.get(field):
                spider.logger.warning(
                    f"[pipeline] Conference paper missing '{field}': {item.get('id')}"
                )
                if field == "summary":
                    item["summary"] = "(No abstract available)"
                elif field == "title":
                    item["title"] = item.get("id", "Unknown Title")

        item.setdefault("categories", [item.get("source", "unknown")])
        item.setdefault("pdf", "")
        item.setdefault("comment", None)
        item.setdefault("year", None)
        return item

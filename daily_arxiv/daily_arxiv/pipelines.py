"""
数据处理 Pipeline
- arXiv 来源：通过 arxiv 库补全论文详情（标题、作者、摘要等）
- Semantic Scholar 来源：字段已由爬虫直接填充，跳过 arxiv 补全
- 所有论文的 categories[0] 统一设置为 topic 名称，供前端分类渲染使用
"""
import arxiv


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
            result = self._enrich_arxiv(item, spider)
        else:
            result = self._enrich_conference(item, spider)

        if result is not None:
            # 关键：将 categories[0] 设置为 topic，使前端"Category"过滤按 topic 分组
            topic = result.get("topic", "unknown")
            arxiv_cats = result.get("categories") or []
            # topic 作为第一个分类，后面保留原始 arXiv 分类供参考
            result["categories"] = [topic] + [c for c in arxiv_cats if c != topic]

        return result

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
            item["categories"] = list(paper.categories)
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
        required_fields = {"title": item.get("id", "Unknown Title"),
                           "summary": "(No abstract available)"}
        for field, fallback in required_fields.items():
            if not item.get(field):
                spider.logger.warning(
                    f"[pipeline] Conference paper missing '{field}': {item.get('id')}"
                )
                item[field] = fallback

        item.setdefault("categories", [])
        item.setdefault("pdf", "")
        item.setdefault("comment", None)
        item.setdefault("year", None)
        return item

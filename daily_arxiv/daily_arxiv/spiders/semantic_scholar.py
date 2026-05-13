"""
Semantic Scholar 会议论文爬虫
覆盖安全四大会、AI 顶会、软工顶会
按 topic 关键词搜索，并根据 venue 字段过滤目标会议
"""
import scrapy
import json
import os
import yaml
from datetime import datetime, timedelta
from urllib.parse import quote_plus


def load_config():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.yaml"),
        os.path.join(os.getcwd(), "config.yaml"),
        "config.yaml",
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("config.yaml not found")


# Semantic Scholar 字段列表
S2_FIELDS = "paperId,title,abstract,year,venue,authors,externalIds,openAccessPdf,publicationDate"

# Semantic Scholar 搜索 API
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def match_venue(venue_str: str, match_keywords: list) -> bool:
    """检查 venue 字段是否匹配目标会议关键词（大小写不敏感）"""
    if not venue_str:
        return False
    venue_lower = venue_str.lower()
    for kw in match_keywords:
        if kw.lower() in venue_lower:
            return True
    return False


class SemanticScholarSpider(scrapy.Spider):
    name = "semantic_scholar"
    allowed_domains = ["api.semanticscholar.org"]

    custom_settings = {
        # Semantic Scholar 免费 API: 1 req/s
        "DOWNLOAD_DELAY": 1.2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": False,
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = load_config()
        self.topics = config.get("topics", [])
        s2_cfg = config.get("sources", {}).get("semantic_scholar", {})
        self.max_results = int(s2_cfg.get("max_results", 50))
        self.days_back = int(s2_cfg.get("days_back", 365))
        self.venues = s2_cfg.get("venues", [])
        self.enabled = s2_cfg.get("enabled", True)

        # 计算年份窗口
        cutoff = datetime.utcnow() - timedelta(days=self.days_back)
        self.year_from = cutoff.year

    def start_requests(self):
        if not self.enabled:
            self.logger.info("[s2] Semantic Scholar spider disabled in config")
            return

        for topic in self.topics:
            topic_name = topic["name"]
            keywords = topic.get("keywords", [])
            if not keywords:
                continue

            # 用 topic 关键词构造搜索查询（取前 3 个关键词以保持精度）
            query_terms = keywords[:3]
            query = " ".join(query_terms)

            url = (
                f"{S2_SEARCH_URL}"
                f"?query={quote_plus(query)}"
                f"&fields={S2_FIELDS}"
                f"&limit={self.max_results}"
                f"&offset=0"
            )
            self.logger.info(f"[s2] topic='{topic_name}' url={url}")
            yield scrapy.Request(
                url,
                callback=self.parse,
                meta={"topic": topic_name},
                headers={"Accept": "application/json"},
            )

    def parse(self, response):
        topic = response.meta["topic"]
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error(f"[s2] JSON decode error for topic='{topic}'")
            return

        papers = data.get("data", [])
        self.logger.info(f"[s2] topic='{topic}' got {len(papers)} papers from S2")

        for paper in papers:
            venue = paper.get("venue") or ""
            year = paper.get("year") or 0

            # 过滤年份
            if year and year < self.year_from:
                continue

            # 过滤是否属于目标会议
            matched_venue = None
            for v in self.venues:
                if match_venue(venue, v.get("match_keywords", [])):
                    matched_venue = v
                    break

            if not matched_venue:
                continue

            paper_id = paper.get("paperId", "")
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            authors = [a.get("name", "") for a in paper.get("authors", [])]
            external_ids = paper.get("externalIds") or {}

            # 优先使用 arXiv ID（若有），否则用 S2 paperId
            arxiv_id = external_ids.get("ArXiv", "")
            use_id = arxiv_id if arxiv_id else f"s2:{paper_id}"

            # 构建 abs URL
            if arxiv_id:
                abs_url = f"https://arxiv.org/abs/{arxiv_id}"
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
            else:
                abs_url = f"https://www.semanticscholar.org/paper/{paper_id}"
                oa_pdf = paper.get("openAccessPdf") or {}
                pdf_url = oa_pdf.get("url", "")

            item = {
                "id": use_id,
                "source": matched_venue["id"],
                "source_display": matched_venue["display"],
                "topic": topic,
                "title": title,
                "authors": authors,
                "summary": abstract or "(No abstract available)",
                "categories": [matched_venue["id"]],
                "abs": abs_url,
                "pdf": pdf_url,
                "year": year,
                "venue": venue,
                "comment": None,
            }
            self.logger.debug(
                f"[s2] topic='{topic}' venue='{matched_venue['display']}' paper='{title[:60]}'"
            )
            yield item

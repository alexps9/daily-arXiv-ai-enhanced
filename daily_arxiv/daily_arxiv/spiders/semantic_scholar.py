"""
Semantic Scholar 会议论文爬虫
覆盖安全四大会、AI 顶会、软工顶会
- 5 个 topic 请求严格串行（链式 parse），杜绝并发触发 429
- 429 指数退避重试
"""
import scrapy
import json
import os
import yaml
import time
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


S2_FIELDS = "paperId,title,abstract,year,venue,authors,externalIds,openAccessPdf,publicationDate"
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def match_venue(venue_str: str, match_keywords: list) -> bool:
    if not venue_str:
        return False
    venue_lower = venue_str.lower()
    return any(kw.lower() in venue_lower for kw in match_keywords)


class SemanticScholarSpider(scrapy.Spider):
    name = "semantic_scholar"
    allowed_domains = ["api.semanticscholar.org"]

    custom_settings = {
        # 严格串行：一次只发 1 个请求，固定 8 秒延迟
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 8,
        "RANDOMIZE_DOWNLOAD_DELAY": False,
        "ROBOTSTXT_OBEY": False,
        "AUTOTHROTTLE_ENABLED": False,          # 关闭自动限速，避免干扰固定延迟
        # 429 退避重试
        "RETRY_HTTP_CODES": [429, 500, 502, 503, 504],
        "RETRY_TIMES": 6,
        "RETRY_BACKOFF_ENABLED": True,           # 指数退避
        "RETRY_BACKOFF_BASE": 10.0,              # 第1次重试等 10s，第2次 20s，...
        "RETRY_BACKOFF_MAX": 120.0,              # 最长等 120s
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

        cutoff = datetime.utcnow() - timedelta(days=self.days_back)
        self.year_from = cutoff.year

        # 将 topic 队列转成列表，串行消费
        self._topic_queue = list(self.topics) if self.enabled else []

    def start_requests(self):
        if not self._topic_queue:
            self.logger.info("[s2] disabled or no topics")
            return
        # 只发第 1 个请求，后续通过 parse 链式触发
        yield self._build_request(self._topic_queue[0], queue_index=0)

    def _build_request(self, topic: dict, queue_index: int) -> scrapy.Request:
        topic_name = topic["name"]
        keywords = topic.get("keywords", [])[:3]
        query = " ".join(keywords)
        url = (
            f"{S2_SEARCH_URL}"
            f"?query={quote_plus(query)}"
            f"&fields={S2_FIELDS}"
            f"&limit={self.max_results}"
            f"&offset=0"
        )
        self.logger.info(f"[s2] ({queue_index+1}/{len(self._topic_queue)}) topic='{topic_name}' url={url}")
        return scrapy.Request(
            url,
            callback=self.parse,
            errback=self.on_error,
            meta={"topic": topic_name, "queue_index": queue_index},
            headers={"Accept": "application/json"},
            dont_filter=True,
        )

    def parse(self, response):
        topic = response.meta["topic"]
        queue_index = response.meta["queue_index"]

        if response.status == 429:
            # 理论上由 RetryMiddleware 处理，若仍到达此处则手动重试
            self.logger.warning(f"[s2] 429 reached parse for topic='{topic}', re-queuing with delay")
            req = self._build_request(self._topic_queue[queue_index], queue_index)
            req.meta["download_latency"] = 30
            yield req
            return

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error(f"[s2] JSON decode error for topic='{topic}'")
            data = {}

        papers = data.get("data", [])
        self.logger.info(f"[s2] topic='{topic}' got {len(papers)} papers")

        for paper in papers:
            venue = paper.get("venue") or ""
            year = paper.get("year") or 0
            if year and year < self.year_from:
                continue

            matched_venue = next(
                (v for v in self.venues if match_venue(venue, v.get("match_keywords", []))),
                None,
            )
            if not matched_venue:
                continue

            paper_id = paper.get("paperId", "")
            external_ids = paper.get("externalIds") or {}
            arxiv_id = external_ids.get("ArXiv", "")
            use_id = arxiv_id if arxiv_id else f"s2:{paper_id}"

            if arxiv_id:
                abs_url = f"https://arxiv.org/abs/{arxiv_id}"
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
            else:
                abs_url = f"https://www.semanticscholar.org/paper/{paper_id}"
                oa_pdf = paper.get("openAccessPdf") or {}
                pdf_url = oa_pdf.get("url", "")

            self.logger.debug(
                f"[s2] topic='{topic}' venue='{matched_venue['display']}' "
                f"paper='{paper.get('title','')[:60]}'"
            )
            yield {
                "id": use_id,
                "source": matched_venue["id"],
                "source_display": matched_venue["display"],
                "topic": topic,
                "title": paper.get("title", ""),
                "authors": [a.get("name", "") for a in paper.get("authors", [])],
                "summary": paper.get("abstract") or "(No abstract available)",
                "categories": [matched_venue["id"]],
                "abs": abs_url,
                "pdf": pdf_url,
                "year": year,
                "venue": venue,
                "comment": None,
            }

        # 链式触发下一个 topic 请求
        next_index = queue_index + 1
        if next_index < len(self._topic_queue):
            yield self._build_request(self._topic_queue[next_index], next_index)

    def on_error(self, failure):
        topic = failure.request.meta.get("topic", "unknown")
        self.logger.error(f"[s2] Request failed for topic='{topic}': {failure}")

"""
arXiv 关键词搜索爬虫
通过 arXiv Atom API 按安全 topic 关键词搜索最近提交的论文
"""
import scrapy
import os
import yaml
from urllib.parse import quote_plus
from datetime import datetime, timedelta, timezone


def load_config():
    """加载 config.yaml，兼容从不同 cwd 运行"""
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


class ArxivSpider(scrapy.Spider):
    name = "arxiv"
    allowed_domains = ["export.arxiv.org"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = load_config()
        self.topics = config.get("topics", [])
        arxiv_cfg = config.get("sources", {}).get("arxiv", {})
        self.max_results = int(arxiv_cfg.get("max_results", 100))
        self.days_back = int(arxiv_cfg.get("days_back", 2))

    def start_requests(self):
        # 计算日期窗口（UTC）
        now_utc = datetime.now(timezone.utc)
        date_from = (now_utc - timedelta(days=self.days_back)).strftime("%Y%m%d") + "0000"
        date_to = now_utc.strftime("%Y%m%d") + "2359"

        for topic in self.topics:
            topic_name = topic["name"]
            keywords = topic.get("keywords", [])
            if not keywords:
                continue

            # 构造 OR 查询：any keyword in title/abstract
            kw_parts = [f'all:"{kw}"' for kw in keywords]
            kw_query = " OR ".join(kw_parts)
            date_filter = f"submittedDate:[{date_from} TO {date_to}]"
            full_query = f"({kw_query}) AND {date_filter}"

            url = (
                "http://export.arxiv.org/api/query"
                f"?search_query={quote_plus(full_query)}"
                f"&sortBy=submittedDate&sortOrder=descending"
                f"&max_results={self.max_results}"
                f"&start=0"
            )
            self.logger.info(f"[arxiv] topic='{topic_name}' url={url}")
            yield scrapy.Request(
                url,
                callback=self.parse,
                meta={"topic": topic_name},
            )

    def parse(self, response):
        topic = response.meta["topic"]
        response.selector.remove_namespaces()

        for entry in response.css("entry"):
            # arXiv 标准 ID URL: http://arxiv.org/abs/2401.12345v1
            id_url = entry.css("id::text").get("").strip()
            if not id_url:
                continue

            # 提取纯 ID（去掉版本号）
            arxiv_id = id_url.split("/abs/")[-1].split("v")[0] if "/abs/" in id_url else id_url.split("/")[-1]
            if not arxiv_id:
                continue

            yield {
                "id": arxiv_id,
                "source": "arxiv",
                "source_display": "arXiv",
                "topic": topic,
            }
            self.logger.debug(f"[arxiv] topic='{topic}' paper={arxiv_id}")

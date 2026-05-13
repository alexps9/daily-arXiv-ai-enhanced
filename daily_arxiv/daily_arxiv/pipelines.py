import arxiv
import os
import yaml


def _load_topics():
    """从 config.yaml 加载 topic 分类规则"""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "config.yaml"),
        "config.yaml",
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            return cfg.get("topics", [])
    return []


def _classify_topic(title: str, summary: str, topics: list) -> str:
    """
    按 title + abstract 关键词将论文归入第一个匹配的 topic。
    关键词不区分大小写；无匹配时返回 'Model'（兜底）。
    """
    text = (title + " " + summary).lower()
    for topic in topics:
        for kw in topic.get("keywords", []):
            if kw.lower() in text:
                return topic["name"]
    return topics[-1]["name"] if topics else "Other"


class DailyArxivPipeline:
    def __init__(self):
        self.page_size = 100
        self.client = arxiv.Client(self.page_size)
        self.topics = _load_topics()

    def process_item(self, item: dict, spider):
        item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        item["abs"] = f"https://arxiv.org/abs/{item['id']}"

        search = arxiv.Search(id_list=[item["id"]])
        paper = next(self.client.results(search))

        item["authors"] = [a.name for a in paper.authors]
        item["title"] = paper.title
        item["comment"] = paper.comment
        item["summary"] = paper.summary

        # 按关键词分类，topic 名作为第一个 category 供前端展示
        topic_name = _classify_topic(paper.title, paper.summary or "", self.topics)
        arxiv_cats = list(paper.categories)
        item["categories"] = [topic_name] + [c for c in arxiv_cats if c != topic_name]

        return item

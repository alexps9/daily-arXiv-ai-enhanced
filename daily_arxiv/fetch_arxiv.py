"""
arXiv 关键词爬取脚本（替代 Scrapy spider + Pipeline）
使用 arxiv Python 库，自带速率限制，严格串行处理每个 topic
直接输出完整字段，无需额外 Pipeline 补全
"""
import arxiv
import json
import yaml
import sys
import os
import argparse
import time
from datetime import datetime, timedelta, timezone


def load_config():
    candidates = [
        os.path.join(os.path.dirname(__file__), "config.yaml"),
        "config.yaml",
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
    raise FileNotFoundError("config.yaml not found")


def build_query(topic: dict) -> str:
    """把 topic 关键词组合成 arXiv 查询字符串"""
    keywords = topic.get("keywords", [])
    if not keywords:
        return ""
    parts = []
    for kw in keywords:
        words = kw.strip()
        if len(words.split()) <= 2:
            parts.append(f'ti:"{words}"')
        else:
            parts.append(f'all:"{words}"')
    return " OR ".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Output JSONL file path")
    args = parser.parse_args()

    config = load_config()
    topics = config.get("topics", [])
    arxiv_cfg = config.get("sources", {}).get("arxiv", {})
    max_results = int(arxiv_cfg.get("max_results", 200))
    days_back = int(arxiv_cfg.get("days_back", 3))

    # 日期截止（本地时区）
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    # arxiv 客户端（page_size=50, delay_seconds=3 内置限速避免 429）
    client = arxiv.Client(page_size=50, delay_seconds=3, num_retries=5)

    seen_ids: set = set()
    total = 0

    # 以追加模式打开（工作流在此之前已清理旧文件）
    with open(args.output, "a", encoding="utf-8") as out_f:
        for i, topic in enumerate(topics):
            topic_name = topic["name"]
            query = build_query(topic)
            if not query:
                continue

            print(
                f"[arxiv] ({i+1}/{len(topics)}) topic='{topic_name}' "
                f"query={query[:80]}...",
                file=sys.stderr,
            )

            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )

            count = 0
            try:
                for paper in client.results(search):
                    pub_date = paper.published
                    if pub_date:
                        # 统一转为 UTC aware 再比较
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                        if pub_date < cutoff:
                            break

                    arxiv_id = paper.entry_id.split("/abs/")[-1].split("v")[0]

                    if arxiv_id in seen_ids:
                        continue
                    seen_ids.add(arxiv_id)

                    item = {
                        "id": arxiv_id,
                        "source": "arxiv",
                        "source_display": "arXiv",
                        "topic": topic_name,
                        "title": paper.title,
                        "authors": [a.name for a in paper.authors],
                        "summary": paper.summary or "",
                        "categories": [topic_name] + list(paper.categories),
                        "abs": f"https://arxiv.org/abs/{arxiv_id}",
                        "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
                        "comment": paper.comment,
                        "year": paper.published.year if paper.published else None,
                        "venue": "arXiv",
                    }
                    out_f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    count += 1

            except Exception as e:
                print(f"[arxiv] Error for topic='{topic_name}': {e}", file=sys.stderr)

            print(f"[arxiv] topic='{topic_name}' → {count} papers", file=sys.stderr)
            total += count

            # topic 间额外等待，避免连续请求触发限速
            if i < len(topics) - 1:
                print("[arxiv] Waiting 8s before next topic...", file=sys.stderr)
                time.sleep(8)

    print(f"[arxiv] Total: {total} papers written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

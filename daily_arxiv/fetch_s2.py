"""
Semantic Scholar 会议论文爬取脚本（替代 Scrapy spider）
使用 requests 库直接调用，完全自控速率，严格串行
"""
import requests
import json
import yaml
import sys
import os
import time
import argparse
from datetime import datetime, timedelta


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


def match_venue(venue_str: str, match_keywords: list) -> bool:
    if not venue_str:
        return False
    venue_lower = venue_str.lower()
    return any(kw.lower() in venue_lower for kw in match_keywords)


def s2_search(query: str, limit: int, max_retries: int = 8) -> list:
    """带指数退避的 S2 搜索，429 时最长等 5 分钟"""
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "fields": "paperId,title,abstract,year,venue,authors,externalIds,openAccessPdf",
        "limit": limit,
        "offset": 0,
    }
    headers = {"Accept": "application/json"}

    wait = 15  # 初始等待秒数
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("data", [])
            elif resp.status_code == 429:
                # 尊重 Retry-After 头部（如果有）
                retry_after = int(resp.headers.get("Retry-After", wait))
                actual_wait = max(retry_after, wait)
                print(
                    f"[s2] 429 rate limit (attempt {attempt}/{max_retries}), "
                    f"waiting {actual_wait}s...",
                    file=sys.stderr,
                )
                time.sleep(actual_wait)
                wait = min(wait * 2, 300)  # 指数退避，最大 5 分钟
            else:
                print(f"[s2] HTTP {resp.status_code}, skipping", file=sys.stderr)
                return []
        except requests.RequestException as e:
            print(f"[s2] Request error: {e}, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
            wait = min(wait * 2, 300)

    print(f"[s2] Gave up after {max_retries} attempts", file=sys.stderr)
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Output JSONL file (append)")
    args = parser.parse_args()

    config = load_config()
    topics = config.get("topics", [])
    s2_cfg = config.get("sources", {}).get("semantic_scholar", {})
    max_results = int(s2_cfg.get("max_results", 100))
    days_back = int(s2_cfg.get("days_back", 365))
    venues = s2_cfg.get("venues", [])
    enabled = s2_cfg.get("enabled", True)

    if not enabled:
        print("[s2] disabled in config", file=sys.stderr)
        return

    year_from = (datetime.utcnow() - timedelta(days=days_back)).year
    total = 0

    with open(args.output, "a", encoding="utf-8") as out_f:
        for i, topic in enumerate(topics):
            topic_name = topic["name"]
            keywords = topic.get("keywords", [])[:3]
            query = " ".join(keywords)

            print(
                f"[s2] ({i+1}/{len(topics)}) topic='{topic_name}' query='{query}'",
                file=sys.stderr,
            )

            # 请求前先等待，避免连续请求触发限速
            if i > 0:
                print("[s2] Waiting 20s before next topic...", file=sys.stderr)
                time.sleep(20)

            papers = s2_search(query, max_results)
            count = 0

            for paper in papers:
                venue = paper.get("venue") or ""
                year = paper.get("year") or 0
                if year and year < year_from:
                    continue

                matched_venue = next(
                    (v for v in venues if match_venue(venue, v.get("match_keywords", []))),
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
                    oa = paper.get("openAccessPdf") or {}
                    pdf_url = oa.get("url", "")

                item = {
                    "id": use_id,
                    "source": matched_venue["id"],
                    "source_display": matched_venue["display"],
                    "topic": topic_name,
                    "title": paper.get("title", ""),
                    "authors": [a.get("name", "") for a in paper.get("authors", [])],
                    "summary": paper.get("abstract") or "(No abstract available)",
                    # categories[0] 必须是 topic，前端按此字段分类渲染
                    "categories": [topic_name, matched_venue["id"]],
                    "abs": abs_url,
                    "pdf": pdf_url,
                    "year": year,
                    "venue": venue,
                    "comment": None,
                }
                out_f.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1

            print(f"[s2] topic='{topic_name}' → {count} matched papers", file=sys.stderr)
            total += count

    print(f"[s2] Total: {total} papers written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

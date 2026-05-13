"""
会议论文爬取脚本 —— 使用 OpenAlex API（替代 Semantic Scholar）
OpenAlex 完全免费，无需 API Key，速率宽松（10 req/s），有完整摘要
"""
import requests
import json
import yaml
import sys
import os
import time
import argparse
from datetime import datetime, timedelta, timezone

OPENALEX_BASE = "https://api.openalex.org/works"
# 礼貌池：在请求中声明 email，获得更高优先级（非必须）
MAILTO = "arxiv-sec-bot@example.com"


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


def openalex_search(query: str, year_from: int, max_results: int) -> list:
    """调用 OpenAlex 搜索，支持简单重试"""
    params = {
        "search": query,
        "filter": f"publication_year:>{year_from - 1}",
        "per-page": min(max_results, 200),
        "sort": "publication_date:desc",
        "mailto": MAILTO,
        "select": (
            "id,doi,title,abstract_inverted_index,"
            "authorships,publication_year,primary_location,"
            "best_oa_location,ids"
        ),
    }

    for attempt in range(1, 5):
        try:
            resp = requests.get(
                OPENALEX_BASE, params=params, timeout=30,
                headers={"User-Agent": f"ArxivSecBot/1.0 (mailto:{MAILTO})"},
            )
            if resp.status_code == 200:
                return resp.json().get("results", [])
            elif resp.status_code == 429:
                wait = 10 * attempt
                print(
                    f"[oa] 429 rate limit (attempt {attempt}/4), waiting {wait}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                print(
                    f"[oa] HTTP {resp.status_code}, skipping query='{query[:60]}'",
                    file=sys.stderr,
                )
                return []
        except requests.RequestException as e:
            print(f"[oa] Request error: {e}, attempt {attempt}/4", file=sys.stderr)
            time.sleep(5 * attempt)

    return []


def reconstruct_abstract(inverted_index: dict) -> str:
    """OpenAlex 摘要以倒排索引存储，需还原为原文"""
    if not inverted_index:
        return "(No abstract available)"
    try:
        # {word: [pos1, pos2, ...]}
        pairs = []
        for word, positions in inverted_index.items():
            for pos in positions:
                pairs.append((pos, word))
        pairs.sort(key=lambda x: x[0])
        return " ".join(w for _, w in pairs)
    except Exception:
        return "(No abstract available)"


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
        print("[oa] disabled in config", file=sys.stderr)
        return

    year_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).year
    seen_ids: set = set()
    total = 0

    with open(args.output, "a", encoding="utf-8") as out_f:
        for i, topic in enumerate(topics):
            topic_name = topic["name"]
            keywords = topic.get("keywords", [])[:4]
            query = " ".join(keywords)

            print(
                f"[oa] ({i+1}/{len(topics)}) topic='{topic_name}' query='{query}'",
                file=sys.stderr,
            )

            works = openalex_search(query, year_from, max_results)
            count = 0

            for work in works:
                # 获取 venue 名称
                primary_loc = work.get("primary_location") or {}
                source = primary_loc.get("source") or {}
                venue_name = source.get("display_name", "")

                # 检查是否匹配目标会议
                matched_venue = next(
                    (v for v in venues if match_venue(venue_name, v.get("match_keywords", []))),
                    None,
                )
                if not matched_venue:
                    continue

                year = work.get("publication_year") or 0
                if year and year < year_from:
                    continue

                # 获取 ID
                work_ids = work.get("ids") or {}
                arxiv_id = ""
                doi = work.get("doi", "") or ""

                # 尝试从 best_oa_location 或 ids 中提取 arXiv ID
                oa_loc = work.get("best_oa_location") or {}
                oa_url = oa_loc.get("url", "") or ""
                if "arxiv.org" in oa_url:
                    arxiv_id = oa_url.split("arxiv.org/abs/")[-1].split("v")[0].strip()
                if not arxiv_id:
                    openalex_id = work_ids.get("arxiv", "") or ""
                    if openalex_id:
                        arxiv_id = openalex_id.replace("https://arxiv.org/abs/", "").split("v")[0]

                use_id = arxiv_id if arxiv_id else f"oa:{work.get('id', '').split('/')[-1]}"

                if use_id in seen_ids:
                    continue
                seen_ids.add(use_id)

                # URL
                if arxiv_id:
                    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
                elif doi:
                    abs_url = f"https://doi.org/{doi.replace('https://doi.org/', '')}"
                    pdf_url = oa_url if oa_url else ""
                else:
                    abs_url = work.get("id", "")
                    pdf_url = oa_url if oa_url else ""

                # 作者列表
                authors = [
                    (a.get("author") or {}).get("display_name", "")
                    for a in work.get("authorships", [])
                    if (a.get("author") or {}).get("display_name")
                ]

                # 摘要还原
                abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

                item = {
                    "id": use_id,
                    "source": matched_venue["id"],
                    "source_display": matched_venue["display"],
                    "topic": topic_name,
                    "title": work.get("title", ""),
                    "authors": authors,
                    "summary": abstract,
                    # categories[0] 必须是 topic，供前端分类渲染
                    "categories": [topic_name, matched_venue["id"]],
                    "abs": abs_url,
                    "pdf": pdf_url,
                    "year": year,
                    "venue": venue_name,
                    "comment": None,
                }
                out_f.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1

            print(f"[oa] topic='{topic_name}' → {count} matched papers", file=sys.stderr)
            total += count

            # topic 间等待，避免触发限速
            if i < len(topics) - 1:
                time.sleep(2)

    print(f"[oa] Total: {total} papers written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

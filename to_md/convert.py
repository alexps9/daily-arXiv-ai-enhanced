"""
将 AI 增强后的 JSONL 数据转换为 Markdown
- 按安全 topic 分组（agent sec / agent for sec / infra sec / model sec / frontier sec）
- 在每篇论文中显示来源（arXiv / IEEE S&P / ACM CCS / ...）
"""
import json
import argparse
import os
from itertools import count

# topic 排序（与 config.yaml 保持一致）
TOPIC_ORDER = [
    "agent sec",
    "agent for sec",
    "infra sec",
    "model sec",
    "frontier sec",
]


def get_topic_rank(topic_name: str) -> int:
    try:
        return TOPIC_ORDER.index(topic_name)
    except ValueError:
        return len(TOPIC_ORDER)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, help="Path to the jsonline file")
    args = parser.parse_args()

    data = []
    with open(args.data, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))

    template = open("paper_template.md", "r", encoding="utf-8").read()

    # 收集所有 topic
    topics = sorted(
        set(item.get("topic", "unknown") for item in data),
        key=get_topic_rank,
    )

    # 统计每个 topic 的论文数
    cnt = {t: 0 for t in topics}
    for item in data:
        t = item.get("topic", "unknown")
        if t in cnt:
            cnt[t] += 1

    # 生成目录
    markdown = "<div id=toc></div>\n\n# Table of Contents\n\n"
    for topic in topics:
        anchor = topic.replace(" ", "-")
        markdown += f"- [{topic}](#{anchor}) [Total: {cnt[topic]}]\n"

    idx_counter = count(1)
    required_fields = ["tldr", "motivation", "method", "result", "conclusion"]

    for topic in topics:
        anchor = topic.replace(" ", "-")
        markdown += f"\n\n<div id='{anchor}'></div>\n\n"
        markdown += f"# {topic} [[Back]](#toc)\n\n"

        papers = []
        for item in data:
            if item.get("topic", "unknown") != topic:
                continue

            ai_data = item.get("AI", {})
            if not ai_data or not isinstance(ai_data, dict):
                print(f"Skipping '{item.get('title', 'Unknown')}': missing AI data")
                continue
            if not all(field in ai_data for field in required_fields):
                print(f"Skipping '{item.get('title', 'Unknown')}': incomplete AI fields")
                continue

            # 来源信息
            source_display = item.get("source_display", item.get("source", "Unknown"))
            venue = item.get("venue") or source_display
            topic_label = item.get("topic", "unknown")

            papers.append(
                template.format(
                    title=item.get("title", ""),
                    authors=", ".join(item.get("authors", [])),
                    summary=item.get("summary", ""),
                    url=item.get("abs", ""),
                    tldr=ai_data.get("tldr", ""),
                    motivation=ai_data.get("motivation", ""),
                    method=ai_data.get("method", ""),
                    result=ai_data.get("result", ""),
                    conclusion=ai_data.get("conclusion", ""),
                    source_display=source_display,
                    venue=venue,
                    topic=topic_label,
                    idx=next(idx_counter),
                )
            )
        markdown += "\n\n".join(papers)

    out_path = args.data.split("_AI_enhanced")[0] + ".md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"Markdown written to: {out_path}")

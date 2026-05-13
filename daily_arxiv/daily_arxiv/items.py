import scrapy


class DailyArxivItem(scrapy.Item):
    # 论文唯一标识（arXiv ID 或 Semantic Scholar paperId）
    id = scrapy.Field()
    # 来源：'arxiv' | 'sp' | 'ccs' | 'usenix' | 'ndss' | 'neurips' | 'icml' | 'iclr' | 'aaai' | 'ijcai' | 'icse' | 'fse' | 'ase' | 'issta'
    source = scrapy.Field()
    # 来源展示名称，如 "arXiv" / "IEEE S&P" / "NeurIPS"
    source_display = scrapy.Field()
    # 所属安全 topic
    topic = scrapy.Field()
    # 以下字段由 pipeline 填充（arXiv 通过 arxiv 库，Semantic Scholar 由爬虫直接返回）
    title = scrapy.Field()
    authors = scrapy.Field()
    summary = scrapy.Field()
    categories = scrapy.Field()
    abs = scrapy.Field()
    pdf = scrapy.Field()
    comment = scrapy.Field()
    # 发表年份
    year = scrapy.Field()
    # 发表期刊 / 会议名（非 arXiv 来源）
    venue = scrapy.Field()

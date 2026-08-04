"""
GEO 第二阶段：把 index.json 里 type=case / type=knowledge 的公众号文章，
批量转成站内独立的纯内容页面（面向 AI/搜索引擎抓取，不追求视觉设计），
并重新生成 cases.html / knowledge.html 列表页与 sitemap.xml。

重新运行方式：index.json 更新后，在项目根目录下直接 `python scripts/build_geo_pages.py`
（或在任意目录 `python /path/to/scripts/build_geo_pages.py`）即可增量重新生成全部产物。
"""
import html
import json
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SITE_ROOT = "https://home.corner-stone.cn"
WECHAT_DIR = os.path.join(ROOT_DIR, "source_data", "wechat_articles")
INDEX_JSON = os.path.join(ROOT_DIR, "index.json")
CASES_DIR = os.path.join(ROOT_DIR, "cases")
KNOWLEDGE_DIR = os.path.join(ROOT_DIR, "knowledge")
CASES_LIST_HTML = os.path.join(ROOT_DIR, "cases.html")
KNOWLEDGE_LIST_HTML = os.path.join(ROOT_DIR, "knowledge.html")
SITEMAP_PATH = os.path.join(ROOT_DIR, "sitemap.xml")
URL_SLUG_MAP_PATH = os.path.join(ROOT_DIR, "_url_slug_map.json")

CASE_LABELS = [
    ("degree_type", "学位类型"),
    ("school", "录取院校"),
    ("program", "专业方向"),
    ("country_region", "国家/地区"),
    ("home_background", "本科背景"),
    ("gpa", "GPA"),
    ("toefl_score", "语言成绩"),
]

BOILERPLATE_LINES = {
    "点击上方蓝色字体关注科石公众号，这里有科石（面向海外的理工科博士硕士申请）的活动预告、干货分享、战报等信息",
    "科石留学战报来袭",
    "END",
    "扫描二维码",
}

PAGE_STYLE = """
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:720px;margin:32px auto;padding:0 20px 60px;line-height:1.85;color:#222;font-size:16px}
h1{font-size:22px;margin:0 0 6px;color:#111}
.meta{color:#888;font-size:13px;margin-bottom:22px}
.back{font-size:13px;margin-bottom:24px}
.back a{color:#0a84c8;text-decoration:none;margin-right:16px}
.back a:hover{text-decoration:underline}
table.fact-table{width:100%;border-collapse:collapse;margin:18px 0 26px;font-size:14px}
table.fact-table th,table.fact-table td{border:1px solid #e5e5e5;padding:8px 12px;text-align:left}
table.fact-table th{background:#f7f7f7;width:110px;color:#555;font-weight:600}
.tags{color:#0a84c8;font-size:13px;margin-bottom:22px}
.hook{font-size:16px;color:#444;background:#f7f9fb;padding:14px 16px;border-left:3px solid #0a84c8;margin-bottom:24px}
.article-body p{margin:0 0 14px}
.source{margin-top:32px;padding-top:16px;border-top:1px solid #eee;color:#888;font-size:13px}
.source a{color:#0a84c8;text-decoration:none}
"""


def load_index():
    with open(INDEX_JSON, encoding="utf-8") as f:
        return json.load(f)


def resolve_txt_path(date, title):
    candidates = [title, re.sub(r'[\\/:*?"<>|]', "_", title)]
    for cand in candidates:
        path = os.path.join(WECHAT_DIR, f"{date}_{cand}.txt")
        if os.path.exists(path):
            return path
    return None


def clean_paragraphs(raw_text):
    paragraphs = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line or line in BOILERPLATE_LINES:
            continue
        paragraphs.append(line)
    return paragraphs


def ascii_slug(title):
    tokens = re.findall(r"[A-Za-z0-9]+", title)
    slug = "-".join(t.lower() for t in tokens)
    return slug[:60]


def make_slug(date, title, used_slugs):
    base = f"{date}-{ascii_slug(title) or 'article'}"[:90]
    slug = base
    i = 2
    while slug in used_slugs:
        slug = f"{base}-{i}"
        i += 1
    used_slugs.add(slug)
    return slug


def render_back_links(list_page, list_label):
    return (
        f'<p class="back"><a href="../{list_page}">← 返回{list_label}</a>'
        f'<a href="../index.html">科石教育首页</a></p>'
    )


def render_json_ld(headline, description, date, canonical_url, extra=None):
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "description": description,
        "datePublished": date,
        "author": {"@type": "Organization", "name": "科石教育", "url": SITE_ROOT + "/"},
        "publisher": {"@type": "Organization", "name": "科石教育", "url": SITE_ROOT + "/"},
        "mainEntityOfPage": canonical_url,
    }
    if extra:
        data.update(extra)
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_page(title, description, date, url, canonical_url, back_links_html, lead_html, fact_html, paragraphs, json_ld):
    body_html = "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{html.escape(title)} - 科石教育</title>
<meta name="description" content="{html.escape(description)}" />
<link rel="canonical" href="{canonical_url}" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<script type="application/ld+json">
{json_ld}
</script>
<style>{PAGE_STYLE}</style>
</head>
<body>
{back_links_html}
<h1>{html.escape(title)}</h1>
<p class="meta">发布日期：{date}</p>
{lead_html}
{fact_html}
<div class="article-body">
{body_html}
</div>
<p class="source">原文首发于科石教育微信公众号（{date}）。<a href="{html.escape(url)}" target="_blank" rel="noopener">查看微信原文</a></p>
</body>
</html>
"""


def build_case_page(entry, slug, used_paths):
    date, title, url = entry["date"], entry["title"], entry["url"]
    case = entry["case"] or {}
    canonical_url = f"{SITE_ROOT}/cases/{slug}.html"

    rows = []
    for key, label in CASE_LABELS:
        val = (case.get(key) or "").strip()
        if val:
            rows.append(f"<tr><th>{html.escape(label)}</th><td>{html.escape(val)}</td></tr>")
    fact_html = f'<table class="fact-table">\n{chr(10).join(rows)}\n</table>' if rows else ""

    extra_paragraphs = []
    for key in ("research_highlights", "narrative_summary"):
        val = (case.get(key) or "").strip()
        if val:
            extra_paragraphs.append(val)

    txt_path = resolve_txt_path(date, title)
    body_paragraphs = clean_paragraphs(open(txt_path, encoding="utf-8").read()) if txt_path else []

    description = entry.get("reason") or title
    json_ld_extra = {}
    if case.get("school"):
        json_ld_extra["about"] = case.get("school")
    json_ld = render_json_ld(title, description, date, canonical_url, json_ld_extra)

    back_links_html = render_back_links("cases.html", "成功案例列表")
    html_out = render_page(
        title=title,
        description=description,
        date=date,
        url=url,
        canonical_url=canonical_url,
        back_links_html=back_links_html,
        lead_html="",
        fact_html=fact_html,
        paragraphs=extra_paragraphs + body_paragraphs,
        json_ld=json_ld,
    )

    out_path = os.path.join(CASES_DIR, f"{slug}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    used_paths.append(out_path)
    return txt_path is not None


def build_knowledge_page(entry, slug, used_paths):
    date, title, url = entry["date"], entry["title"], entry["url"]
    knowledge = entry["knowledge"] or {}
    canonical_url = f"{SITE_ROOT}/knowledge/{slug}.html"

    hook = (knowledge.get("hook") or "").strip()
    lead_html = f'<p class="hook">{html.escape(hook)}</p>' if hook else ""

    tags = knowledge.get("tags") or []
    tags_html = f'<p class="tags">标签：{html.escape("、".join(tags))}</p>' if tags else ""

    txt_path = resolve_txt_path(date, title)
    body_paragraphs = clean_paragraphs(open(txt_path, encoding="utf-8").read()) if txt_path else []

    description = hook or title
    json_ld_extra = {"keywords": "、".join(tags)} if tags else {}
    json_ld = render_json_ld(title, description, date, canonical_url, json_ld_extra)

    back_links_html = render_back_links("knowledge.html", "知识库列表")
    html_out = render_page(
        title=title,
        description=description,
        date=date,
        url=url,
        canonical_url=canonical_url,
        back_links_html=back_links_html,
        lead_html=lead_html,
        fact_html=tags_html,
        paragraphs=body_paragraphs,
        json_ld=json_ld,
    )

    out_path = os.path.join(KNOWLEDGE_DIR, f"{slug}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    used_paths.append(out_path)
    return txt_path is not None


def build_list_page(template_path, entries, render_item):
    with open(template_path, encoding="utf-8") as f:
        html_content = f.read()

    items_html = "\n".join(render_item(e) for e in entries)
    new_ul = f"<ul>\n{items_html}\n\n                    </ul>"

    html_content, count = re.subn(
        r'(<div class="knowledge-list">\s*)<ul>.*?</ul>',
        lambda m: m.group(1) + new_ul,
        html_content,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError(f"未能在 {template_path} 中定位到 knowledge-list 的 <ul> 区块")
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def case_list_item(entry):
    case = entry["case"] or {}
    tag_parts = []
    if case.get("home_background"):
        tag_parts.append(case["home_background"])
    if case.get("gpa"):
        tag_parts.append(f"GPA {case['gpa']}")
    if case.get("toefl_score"):
        tag_parts.append(f"T {case['toefl_score']}")
    tag_text = ", ".join(tag_parts) if tag_parts else (case.get("school") or "")
    href = f"cases/{entry['_slug']}.html"
    return f"""                        <li>
                            <a class="title" href="{href}">{html.escape(entry['title'])}</a>
                            <p class="tag">
                                <a><i class="i-1"></i>{html.escape(tag_text)}</a>
                            </p>
                        </li>"""


def knowledge_list_item(entry):
    knowledge = entry["knowledge"] or {}
    tags = knowledge.get("tags") or []
    tag_text = "、".join(tags)
    href = f"knowledge/{entry['_slug']}.html"
    return f"""                        <li>
                            <a class="title" href="{href}">{html.escape(entry['title'])}</a>
                            <p class="tag">
                                <a><i class="i-1"></i>{html.escape(tag_text)}</a>
                            </p>
                        </li>"""


def build_sitemap(cases, knowledges):
    # 注意：services.html 及其子页面（pricing/fit/comparison/process/ai-system/faq/
    # failure-cases/testimonials）刻意不出现在 sitemap 里——这批页面只想让 AI
    # 助手通过 llms.txt 里的直链读取，不希望被主流搜索引擎索引后出现在普通用户的
    # 搜索结果里（对应的 robots.txt 里也对这些路径做了同样的 Disallow 处理）。
    static_urls = [
        (f"{SITE_ROOT}/", None, "1.0", "weekly"),
        (f"{SITE_ROOT}/about.html", None, "0.8", "monthly"),
        (f"{SITE_ROOT}/cases.html", None, "0.9", "weekly"),
        (f"{SITE_ROOT}/knowledge.html", None, "0.9", "weekly"),
    ]
    entries = []
    for e in cases:
        entries.append((f"{SITE_ROOT}/cases/{e['_slug']}.html", e["date"], "0.6", "monthly"))
    for e in knowledges:
        entries.append((f"{SITE_ROOT}/knowledge/{e['_slug']}.html", e["date"], "0.6", "monthly"))

    url_blocks = []
    for loc, lastmod, priority, changefreq in static_urls + entries:
        lastmod_tag = f"\n    <lastmod>{lastmod}</lastmod>" if lastmod else ""
        url_blocks.append(
            f"  <url>\n    <loc>{loc}</loc>{lastmod_tag}\n    "
            f"<changefreq>{changefreq}</changefreq>\n    <priority>{priority}</priority>\n  </url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(url_blocks)
        + "\n</urlset>\n"
    )
    with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
        f.write(xml)


def main():
    os.makedirs(CASES_DIR, exist_ok=True)
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

    data = load_index()
    cases = [e for e in data if e["type"] == "case"]
    knowledges = [e for e in data if e["type"] == "knowledge"]

    cases.sort(key=lambda e: e["date"], reverse=True)
    knowledges.sort(key=lambda e: e["date"], reverse=True)

    used_slugs = set()
    written_paths = []
    missing_txt = []

    for e in cases:
        e["_slug"] = make_slug(e["date"], e["title"], used_slugs)
        ok = build_case_page(e, e["_slug"], written_paths)
        if not ok:
            missing_txt.append(("case", e["date"], e["title"]))

    for e in knowledges:
        e["_slug"] = make_slug(e["date"], e["title"], used_slugs)
        ok = build_knowledge_page(e, e["_slug"], written_paths)
        if not ok:
            missing_txt.append(("knowledge", e["date"], e["title"]))

    build_list_page(CASES_LIST_HTML, cases, case_list_item)
    build_list_page(KNOWLEDGE_LIST_HTML, knowledges, knowledge_list_item)
    build_sitemap(cases, knowledges)

    url_map = {e["url"]: f"cases/{e['_slug']}.html" for e in cases}
    url_map.update({e["url"]: f"knowledge/{e['_slug']}.html" for e in knowledges})
    with open(URL_SLUG_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(url_map, f, ensure_ascii=False, indent=2)

    print(f"生成案例页: {len(cases)} 篇，知识页: {len(knowledges)} 篇")
    print(f"写入文件总数: {len(written_paths)}")
    if missing_txt:
        print(f"\n缺少原文 txt，页面已生成但正文为空，需要人工核实（{len(missing_txt)} 篇）：")
        for t, d, title in missing_txt:
            print(f"  [{t}] {d} {title}")


if __name__ == "__main__":
    main()

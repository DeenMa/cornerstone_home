"""
Phase 3 补充：把 source_data/transcripts/ 里的讲座转写稿 PDF，用 Gemini 总结成结构化 FAQ 问答对，
并自动合并进 faq.html 的可见正文与 FAQPage JSON-LD（按已有的分类小节归类，找不到匹配分类时新建一个）。

运行方式：
1. 在项目根目录新建 .env 文件，写入一行 GEMINI_APIKEY=你的key
2. 在项目根目录下 `python scripts/transcript_to_faq.py`
   （每份 PDF 的中间产物会落在 source_data/faq_output/<文件名>.json，方便调试和重跑；faq.html 会被直接更新）
"""
import glob
import html
import json
import os
import re
import time

import fitz
from dotenv import load_dotenv
from google import genai

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

TRANSCRIPT_DIR = os.path.join(ROOT_DIR, "source_data", "transcripts")
OUTPUT_DIR = os.path.join(ROOT_DIR, "source_data", "faq_output")
FAQ_HTML_PATH = os.path.join(ROOT_DIR, "faq.html")
MODEL = "gemini-3.1-pro-preview"
RETRY_TIMES = 3
SLEEP_SECONDS = 15

EXISTING_CATEGORIES = ["套磁与暑期研究", "港新提前批", "选校与专业方向", "签证与其他"]

SYSTEM_PROMPT = f"""你是科石教育（一家专注美国/港新理工科博士申请辅导的教育机构）的内容编辑。
你会收到一份内部讲座的转写稿全文，讲座面向准备留学申请的本科生。你的任务是把讲座里对申请者
有实际参考价值的内容，提炼成若干条独立的FAQ问答对，供发布在官网FAQ页面。

要求：
1. 只保留讲师本人讲解的实质性内容。彻底剔除：具体的手机号、微信号、邮箱等联系方式；
   听众的匿名网名/花名提问（如"某某同学问："这类归属信息可以删掉，只留问题本身和回答）；
   "关注公众号""扫码"之类的推广话术；与申请建议无关的开场寒暄、致谢、活动通知。
2. 每条FAQ的问题（question）要独立、通用、像用户会主动搜索的问题，不要出现"刚才提到的""上一个问题"
   这种依赖上下文的表述；回答（answer）要基于转写稿内容改写成完整、自然的书面语段落，不是逐字摘抄，
   但不能编造转写稿里没有的事实或数字。
3. 每条FAQ标注一个 category 字段。优先复用以下已有分类（原样使用这几个字符串之一）：
   {json.dumps(EXISTING_CATEGORIES, ensure_ascii=False)}
   如果内容明显不属于以上任何一类，才新建一个简短的分类名（4~8个汉字，例如"文书与推荐信"）。
4. 一份转写稿通常能提炼出 5~15 条FAQ，具体数量取决于内容丰富度，宁缺毋滥，不要为了凑数编造问题。
5. 严格只输出JSON，不要输出任何解释文字或markdown代码块标记。输出格式为一个JSON数组，
   每个元素形如：{{"question": "...", "answer": "...", "category": "..."}}
"""


def extract_pdf_text(pdf_path):
    doc = fitz.open(pdf_path)
    return "\n".join(page.get_text() for page in doc)


def strip_json_fence(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def query_gemini(client, transcript_text):
    user_prompt = f"以下是讲座转写稿全文：\n\n{transcript_text}"
    retry_time = RETRY_TIMES
    while retry_time > 0:
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=f"{SYSTEM_PROMPT}\n{user_prompt}",
            )
            content = response.text
            if not content:
                raise ValueError("Empty response content")
            data = json.loads(strip_json_fence(content))
            if not isinstance(data, list):
                raise ValueError("Expected a JSON list of FAQ items")
            return data
        except Exception as e:
            retry_time -= 1
            print(f"  出错: {e}，{SLEEP_SECONDS}秒后重试（剩余{retry_time}次）")
            if retry_time > 0:
                time.sleep(SLEEP_SECONDS)
    raise RuntimeError("Gemini 调用多次失败，放弃该文件")


def process_transcripts():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    api_key = os.getenv("GEMINI_APIKEY")
    if not api_key:
        raise ValueError("未找到 GEMINI_APIKEY，请在项目根目录 .env 文件里设置")
    client = genai.Client(api_key=api_key)

    pdf_paths = sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.pdf")))
    if not pdf_paths:
        print(f"{TRANSCRIPT_DIR} 下没有找到 PDF 文件")
        return []

    all_items = []
    for pdf_path in pdf_paths:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        out_path = os.path.join(OUTPUT_DIR, f"{stem}.json")

        if os.path.exists(out_path):
            print(f"[跳过已处理] {stem}")
            with open(out_path, encoding="utf-8") as f:
                items = json.load(f)
            all_items.extend(items)
            continue

        print(f"[处理中] {stem}")
        text = extract_pdf_text(pdf_path)
        items = query_gemini(client, text)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"  生成 {len(items)} 条FAQ -> {out_path}")
        all_items.extend(items)

    return all_items


# ---------- 合并进 faq.html ----------

CATEGORY_BLOCK_RE = re.compile(
    r'<h2 style="font-size: 20px; margin-bottom: 15px; color: #333;">(.*?)</h2>(.*?)'
    r'<hr style="margin: 30px 0; border: 0; border-top: 1px solid #eee;">',
    re.S,
)
QA_PAIR_RE = re.compile(
    r'<h3 style="font-size: 16px; margin: 18px 0 6px; color: #07111b;">(.*?)</h3>\s*'
    r'<p style="font-size: 15px; line-height: 1.8; color: #555;">(.*?)</p>',
    re.S,
)


def unescape(s):
    return html.unescape(s).strip()


def parse_existing_faq(content):
    """从 faq.html 正文里解析出 [(category, [(question, answer), ...]), ...]，保持原有顺序。"""
    categories = []
    for cat_name, body in CATEGORY_BLOCK_RE.findall(content):
        pairs = [(unescape(q), unescape(a)) for q, a in QA_PAIR_RE.findall(body)]
        categories.append([unescape(cat_name), pairs])
    return categories


def merge_new_items(categories, new_items):
    """把新条目按 category 名称合并进已有分类；不存在的分类追加到末尾。已存在完全相同问题的跳过。"""
    cat_index = {name: idx for idx, (name, _) in enumerate(categories)}
    added, skipped = 0, 0
    for item in new_items:
        cat_name = (item.get("category") or "其他").strip()
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if not question or not answer:
            continue

        if cat_name not in cat_index:
            cat_index[cat_name] = len(categories)
            categories.append([cat_name, []])

        idx = cat_index[cat_name]
        existing_questions = {q for q, _ in categories[idx][1]}
        if question in existing_questions:
            skipped += 1
            continue
        categories[idx][1].append((question, answer))
        added += 1
    return added, skipped


def render_visible_html(categories):
    blocks = []
    for cat_name, pairs in categories:
        qa_html = "\n\n".join(
            f'                <h3 style="font-size: 16px; margin: 18px 0 6px; color: #07111b;">{html.escape(q)}</h3>\n'
            f'                <p style="font-size: 15px; line-height: 1.8; color: #555;">{html.escape(a)}</p>'
            for q, a in pairs
        )
        blocks.append(
            f'                <h2 style="font-size: 20px; margin-bottom: 15px; color: #333;">{html.escape(cat_name)}</h2>\n\n'
            f'{qa_html}\n\n'
            f'                <hr style="margin: 30px 0; border: 0; border-top: 1px solid #eee;">'
        )
    return "\n\n".join(blocks)


def render_json_ld(categories):
    main_entity = []
    for _, pairs in categories:
        for q, a in pairs:
            main_entity.append(
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
            )
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def update_faq_html(new_items):
    with open(FAQ_HTML_PATH, encoding="utf-8") as f:
        content = f.read()

    categories = parse_existing_faq(content)
    if not categories:
        raise RuntimeError("未能从 faq.html 解析出已有的分类结构，请检查页面结构是否变化")

    added, skipped = merge_new_items(categories, new_items)

    # 先替换正文里所有分类小节（用第一个分类块的起点到最后一个分类块的终点整体替换），
    # 再替换 JSON-LD，避免两次替换互相影响彼此的匹配位置。
    matches = list(CATEGORY_BLOCK_RE.finditer(content))
    new_body = render_visible_html(categories)
    content = content[: matches[0].start()] + new_body + content[matches[-1].end():]

    new_json_ld = render_json_ld(categories)
    content, n_ld = re.subn(
        r'<script type="application/ld\+json">\n\t\{.*?\n\t\}\n\t</script>',
        lambda _: f'<script type="application/ld+json">\n{new_json_ld}\n\t</script>',
        content,
        count=1,
        flags=re.S,
    )
    if n_ld != 1:
        raise RuntimeError("未能定位到 faq.html 里的 FAQPage JSON-LD 区块")

    with open(FAQ_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"faq.html 已更新：新增 {added} 条，跳过重复 {skipped} 条，当前分类数 {len(categories)}，"
          f"总FAQ数 {sum(len(p) for _, p in categories)}")


def main():
    new_items = process_transcripts()
    print(f"\n本次共提炼出 {len(new_items)} 条候选FAQ，开始合并进 {FAQ_HTML_PATH} ...")
    update_faq_html(new_items)


if __name__ == "__main__":
    main()

# corner_stone_client — 核心 Overview

> 本文档是 `corner_stone_client` 的架构总览，用途是作为大模型（以及新加入的开发者）理解本仓库的入口上下文。
> 具体的重构规范见同目录 `docs/data_io_processing_split_rules.md`。

---

## 1. 这是什么

**科石智能系统**（Keshi Intelligent System）是一套面向**留学申请咨询**（主要是理工科 PhD / Master 海外申请）的内部 SaaS。它把顾问原本手工做的重活——查教授、查项目申请要求、写 CV/SOP/RL、写套磁信、发套磁邮件、模拟面试、签证材料把关——变成可以在后台排队跑的 AI 任务。

系统分三层，本仓库是**算法端**：

| 层 | 仓库 | 技术栈 | 职责 |
|---|---|---|---|
| 前端 | `keshi_vue` | Vue 2.6 + Element UI（vue-admin-template） | 顾问/学生操作台，直传文件到 COS，提交任务，轮询任务记录 |
| 后端 | `keshi_api` | **Laravel 8 / PHP 8** | 唯一对浏览器暴露的 API；鉴权、科石币校验、下发 COS 临时凭证、把任务 RPUSH 进 Redis |
| **算法端** | **`corner_stone_client`** | **Python 3.9/3.12** | **本仓库。消费 Redis 任务队列，跑 AI/爬虫算法，产出文件回传 COS，写结果回 MySQL** |

算法端**不做鉴权、不直接服务浏览器**（个别 Flask 端点例外），它是一个**异步 worker + 若干常驻服务**。

### 跨层契约（最重要的一张图）

```
浏览器 ──直传──> 腾讯云 COS (bucket: corner-1318431500)
   │                        ▲          │
   │ POST /admin/admin/task/create-task│ 下载输入
   ▼                        │          ▼
keshi_api (Laravel)         │   corner_stone_client
   │ TaskRecords::create()  │   server/task.py (常驻 worker)
   │ RPUSH task_queue_new ──┼──> LPOP corner:task_queue_new
   │                        │        │ 分发 task_name → modules/task/*.py
   └──> MySQL task_records  │        │ 调 src/tools/*  业务逻辑
        (status 1 待执行)    │        ▼
              ▲             └── 上传产物到 COS
              └──────── update_task(status 2/3/4) + insert_task_content
```

**关键细节：** PHP 写的 key 是 `task_queue_new`，Python 读的是 `corner:task_queue_new`。差的 `corner:` 来自 Laravel 的 `REDIS_PREFIX`（`keshi_api/config/database.php:146`）。**`.env` 里必须设 `REDIS_PREFIX=corner:`，否则两端永远对不上，且不会报错。**

`task_records.status` 语义（表注释是错的，以模型常量为准）：`1` 待执行 → `2` 执行中 → `3` 已完成 / `4` 失败。

---

## 2. 运行时进程（4 个独立入口）

本仓库不是一个服务，而是 4 个各自 supervisor 托管的进程：

| 入口 | 类型 | 端口 | 作用 |
|---|---|---|---|
| `server/task.py` | 常驻 worker | — | **主力**。轮询 Redis 任务队列，分发 17 种 `task_name`。文件末尾直接 `service.run()`，即 `python -m server.task` 就是启动命令 |
| `server/api.py` | Flask | 8008（PHP 侧硬编码调 8009，中间应有反代） | 同步 HTTP：`/v1/chat/completion`（SSE 科石小助手）、`/search`、`/task_research`（申请区间）、`/csrankings_url`、`/config/translations`、`/mock_interview_with_cos` |
| `server/work.py` | Flask | 8005 | 企业微信**微信客服**回调（学生端问答机器人） |
| `server/aibot.py` | 常驻 WSS 客户端 | 无（出站长连接） | 企业微信**智能机器人**（内部员工助手）。纯外拨，不需要公网端口 |
| `src/tools/mock_interview_voice/main.py` | **FastAPI** + gunicorn | 6006 | 语音模拟面试，独立子应用（外购），由 `shell/check_voice_interview_service.sh` 看门狗保活 |

`server/task_development.py` 是 `task.py` 的本地调试孪生体：不轮询队列，文件末尾直接 `handle_task(task_str)` 跑一条硬编码 payload。**里面注释掉的 payload 是各任务入参最可靠的参考样例**（`task_development.py:99-110`、`task.py:119-124`）。

---

## 3. 核心分层约定（读代码前必须先懂这个）

整个仓库围绕一条规则组织，见 `docs/data_io_processing_split_rules.md`：

**业务逻辑与数据 I/O 彻底分离，且 I/O 内联在各自入口里，不允许单独的 `data_access.py`。**

```
src/tools/<tool>/<tool>.py     ← 纯业务逻辑。不知道文件路径/数据库/COS/ENVIRONMENT
                                  入参出参统一是 list[dict] / dict
                                  返回 {"results": [...], "token_used": N, "query_used": N}
                                  ✅ 允许：调 AI、调 web search、读 API key
                                  ❌ 禁止：open()、SQL、COS、PROJECT_ROOT、source_type

modules/task/<task>.py         ← 服务端编排 + 服务端 I/O（COS 下载/上传、MySQL、扣费、改任务状态）
test/<tool>/<file>.py          ← 本地编排 + 本地 I/O（读写本地 xlsx/docx、写 logs/）
```

`modules/task/*.py` 的 `handle()` 几乎都是同一副骨架，注释都直接抄自规范文档：

```python
def handle(self, form_data):
    # 1. Server Data Access: 下载/读取输入
    # 2. Process using pure business logic
    # 3. Server Data Access: 保存产物并上传 COS
    # 4. Handle task completion（改状态、写内容表、扣科石币）
```

**推论（对改代码很重要）：**
- `test/` 目录**不是单元测试**，而是**顾问本地手动跑算法的操作入口**，里面硬编码着真实学生案例的参数字典。
- 同一份 `src/tools` 业务逻辑被 `modules/task`（服务端）和 `test`（本地）两个入口复用，两边的 `_load_*` / `_save_*` 方法长得很像，这是规范刻意接受的重复。
- 加新工具时，改动点是 3 处：`src/tools/新工具/`、`modules/task/新任务.py`、`server/task.py` 的分发 `elif`。

---

## 4. 目录地图

```
corner_stone_client/
├── server/                  # 4 个运行时入口 + MySQL/Redis 连接封装
│   ├── task.py              #   ★ 任务 worker（主入口）
│   ├── task_development.py  #   本地单任务调试
│   ├── api.py               #   Flask 同步 API
│   ├── work.py              #   企业微信客服回调
│   ├── aibot.py             #   企业微信智能机器人
│   └── components/          #   mysql.py（PyMySQL 裸 SQL）、redis.py
│
├── modules/
│   ├── user_service.py      # ★ UserService 基类：所有任务的公共 DB 操作
│   │                        #   update_task / insert_task_content / update_user_coin
│   │                        #   insert_check_research_result / insert_check_requirement_result
│   ├── task/                # ★ 服务端任务处理器，一个 task_name 一个文件
│   └── service/             #   同步服务（generate_overall_database.py = CasesDatabase）
│
├── src/
│   ├── config.py            # ★ 非敏感配置：enroll_year、PROJECT_ROOT、source_type、默认模型
│   ├── common/              # ★ 公共基础设施（见第 7 节）
│   │   └── data/            #   静态数据资产（见第 8 节）
│   └── tools/               # ★ 业务逻辑，一个能力一个包（见第 5 节）
│
├── test/                    # 本地运行入口（非自动化测试）
├── docs/                    # 本文档 + 分层重构规范
├── logs/                    # 服务端日志（按日期+任务类型分文件）
├── shell/                   # 运维脚本
├── .env                     # 敏感配置（不入库）
└── requirements.txt
```

---

## 5. 任务目录（task_name → 实现 → 干什么）

这是全仓库最重要的一张对照表。分发逻辑在 `server/task.py:71-101`。

| `task_name` | 中文 | 任务处理器 `modules/task/` | 业务逻辑 `src/tools/` | 产物 | 扣费 `func_name` |
|---|---|---|---|---|---|
| `check-research` | 查教授研究方向 | `check_research.py` | `find_faculties_research_area/faculty_finder.py` | Excel → COS + 写 `task_check_research` | **不扣费**（最贵的任务免费） |
| `check-requirement` | 查项目申请要求 | `check_requirement.py` | `find_program_requirement/requirement_finder.py` | Excel → COS + 写 `task_check_requirement` | `check_requirement` / `check_requirement_customized` |
| `generate-cv` | CV 生成 | `generate_cv.py` | `paraphrase_essays/cv_generator.py` + `cv_content_generator.py` | `.docx` 或 `.tex` + CV JSON 存 DB | `generate_cv` |
| `generate-rl` | 推荐信起草 | `generate_rl.py` | `paraphrase_essays/generate_rl.py` | `.docx` | `generate_rl` |
| `generate-taoci` | 海套邮件起草 | `generate_taoci.py` | `paraphrase_essays/generate_taoci.py` | `.docx` | `generate_taoci` |
| `generate-taoci-fine` | 精套邮件起草 | `generate_taoci_fine.py` | `paraphrase_essays/generate_taoci_detail.py` | `.txt` | `analyze` |
| `polish-rp` / `polish-sop` / `polish-rl` / `polish-phs` | 文书润色 | `polish_essays.py`（**4 合 1**，配置表驱动） | `paraphrase_essays/essay_paraphraser.py` | `.docx` | `paraphrase_in_whole`（RL/PhS）/ `paraphrase_in_whole2`（RP/SoP） |
| `review-visa` | 签证材料把关 | `review_visa.py` | `paraphrase_essays/moderate_visa_document.py` | `.txt` | `visaDocumentModerator` |
| `answer-application-essay` | 网申问题拆分 | `answer_application_essay.py` | `paraphrase_essays/complete_qa.py` | `.docx` | `complete_qa` |
| `send-emails` | 套磁信批量发送 | `send_emails.py` | `send_email_automatically/email_processor.py` | 回填 `Sent` 的 xlsx → COS | **不扣费** |
| `match_mentor` | 连接学长学姐 | `match_mentor.py` | `connect_mentors/connect_mentors.py` | 写 `match_mentor_detail` | — |
| `match_mentor_send_email` | 学长学姐邀请邮件 | `mentor_send_email.py` | `send_email_automatically/send_email_to_users.py` | 发邮件 + 改状态 | — |
| `apply-interval` | 申请区间确定 | `apply_interval.py`（**已废弃**） | `determine_objectives/check_sbr.py` | Excel → COS | `apply-interval`（PHP 侧扣） |
| `generate-customized-sop` | 自定义 SoP | `generate_customized_sop.py`（**空壳 `pass`**） | `paraphrase_essays/customize_sop.py` | — | 无 |

**注意 `generate-task` / `polish-task` 不是真任务名。** 前端只提交 8 种 `task_name`，其中这两个是伞形名，Laravel 侧根据 `generate_select` / `polish_select` 字段改写成真实任务名（`TaskController.php:81` 和 `:119`——两处改写时机不同，一个在去重检查前、一个在后）。

**同步（非队列）路径：** 申请区间用 `POST /task_research` → `CasesDatabase.analyze_similar_cases`；模拟面试用 `POST /mock_interview_with_cos`；科石小助手用 SSE `/v1/chat/completion`。

---

## 6. 两大核心搜索算法

这两个是仓库里最复杂、最值钱的部分，也是唯一严格遵循分层规范重构过的两个工具。

### 6.1 查教授 `find_faculties_research_area/faculty_finder.py`（832 行）

**目标：** 给定「专业 + 研究兴趣 + 国家 + QS 排名区间」，产出一张「这些学校里有哪些教授做你想做的方向、值得套磁」的表。

**流水线：**

1. **准入锁** — Redis key `corner:check_research_task:{admin_id}`，TTL 3 小时。同一用户并发请求被静默丢弃（`check_research.py:224-233`）。
2. **组学校列表** — 从 `src/common/data/rankings/qs/{专业}.csv` 读 QS 榜，按最多 3 组 `(国家, 排名起, 排名止)` 做笛卡尔积筛选。
3. **读缓存 + 译兴趣** — 查 `task_check_research_common WHERE major=?`；研究兴趣若含中文则花 1 次 AI 调用译成英文。
4. **缓存复算**（`_process_cached_faculties`）— 已缓存的教授只需 1 次 AI 调用，把存好的 `full_research` 对新兴趣重新打分，省掉全套发现流程。
5. **新增发现**（`_process_universities_for_faculties`）— 每所学校：同一个「列出该系所有做 X 方向的教授」的 web search **重复问 3 次**（`web_query_repetition_times = 3`）再取并集，加 1 次 Scholar/ResearchGate 补充；然后逐个教授 → 1 次 web search 抓信息 → 1 次 AI 补全（打分 / 是否华人 / 匹配到的兴趣点）→ Scholar 查引用数。
6. **落库 + 出表** — 双表分离写入，Excel 上传 COS。

**打分制（`57cdc49` 之后，已不是 true/false）：**

- **方向匹配分** `match_score` ∈ [0,1]，由 LLM 按六档 rubric 打（`prompts.py:88-98`），例如 1.00 = 同一子领域，0.60–0.79 = 学科重叠但侧重不同，0.00–0.19 = 完全无关。
- **门槛** `MATCH_SCORE_THRESHOLD = 0.6`，且 `related_interest` 非空。
- **综合推荐分** `_calculate_recommendation_score`（上限 100）：
  ```
  (match_score − 0.6) × 100          # 实际 0–40
  + 10 × log10(citations + 1)         # 引用影响力
  + 意愿分 0–25                        # 华人 +10、assistant prof +5、contact_policy=Welcome +10
  ```
  实践上引用数权重偏大：0.65 匹配 + 5000 引用 ≈ 42 分，会压过 1.00 匹配 + 50 引用 ≈ 57 分之外的多数情况。

**双表设计（缓存的精髓）：**

| 表 | 常量 | 内容 | 为什么 |
|---|---|---|---|
| 学生交付表 | `STUDENT_TABLE_COLUMNS` | 含 `research`、`citation_recent`、`recommendation_score`、`related_paper_url` | 与本次查询兴趣绑定，会过期 |
| 主表/缓存表 `task_check_research_common` | `MASTER_TABLE_COLUMNS` | 只存 `is_chinese`、`contact_policy`、`full_research` 等**与兴趣无关**的字段 | 可跨不同研究兴趣复用 |

代码里 master 记录**故意写在"是否招生"和"是否相关"两道过滤之前**（`faculty_finder.py:568-584`）——不招生、不匹配的教授也要进缓存，否则缓存就不通用了。

**并发：** 任务内**基本是串行的**。`ThreadPoolExecutor(max_workers=4)` 只用于把整个 `handle` 丢后台，即最多 4 个**任务**并行；学校、教授、API 调用全串行，外加每次 Serper 后 sleep 1–2s、每页 Scholar 后 sleep 3–5s。所以前端提示"查 1 所学校的教授需要 5 小时"是真的。

**去重 4 层：** 内存 set → 缓存名单剔除 → DB `(major, university, faculty)` 存在性检查（只插不更新）→ Excel 先按 `(university, faculty)` 再按 `(university, email)` 去重。

### 6.2 查项目申请要求 `find_program_requirement/requirement_finder.py`（314 行）

**目标：** 给定「国家 + 专业 + 学位 + QS 排名区间 + 要查哪几项要求」，产出各校该项目的申请要求表。

支持 10 种 `query_type`：**Deadline / GPA / TOEFL / IELTS / GRE / WES / Spring Admission / GRE Subject / Application Fee / Master Program**。

**两个关键设计：**

1. **每校只查一次院系**（`lookup_division`，`:196-197`）。查要求需要知道该专业挂在哪个 department/school，这个结果在同一所学校的所有 query_type 之间复用——查 4 项要求就省掉 3 次 Gemini 调用。
2. **`smart_query` 双引擎降级**（`:75`）。先用便宜的 Serper 取 Google answerBox；answerBox 为空才 fallback 到贵的 Gemini grounded search。原始文本最后统一交给 GPT 按 query_type 专属 prompt 抽成 `{"answer": ...}`。

**三级放宽 fallback**（`check_requirement_single_program`，`:125`）：`(学校, 系, 学位)` → `(学校, 学院, 学位)` → `(学校, "", "graduate")`，三级全空才填 `N/A`。

**输出统一 4 键**（不管查什么都一样，刻意的归一化）：`query_type` / `query_type_value` / `query_type_note`（原始出处文本）/ `query_type_link`（参考链接）。

**Prompt 防幻觉：** 每个 `web` prompt 都以「不确定就返回 N/A，不要猜、不要编」结尾；每个 `ai` prompt 都约束输出类型（GRE 只能 Required/Not Required，Deadline 只能 MM/DD）。GPA prompt 有 4 条歧义消解规则（要 required 不要 recommended/average、要国际生不要本地、要入学不要毕业、冲突时取主校区）。

---

## 7. 文书生成家族 `src/tools/paraphrase_essays/`

9 个能力共用一个 `prompts.py`（334 行 prompt 库）。

**术语：套磁（taoci）** = 申请前/申请中主动给外国教授发邮件建立联系、探探对方是否招人、争取面试或 funding。**海套** = 广泛群发；**精套** = 针对某位教授的某篇论文深度定制。

| 能力 | 说明 |
|---|---|
| `generate_taoci` | 只用学生自己的材料写通用套磁信。`taoci_type` 区分 summer（额外强调"我方自带经费"）/ application（申 PhD）。prompt 刻意压低 GPA（>3.5 或前 20% 才提），且要求"渐进式请求"（先请对方评估潜力、给论文读，而不是直接要求加入组） |
| `generate_taoci_detail`（= 服务端 `generate-taoci-fine`） | 拿上一步的草稿 + 教授的一篇 PDF，**两次串行调用**：先总结论文（创新点/解决了什么/缺陷与未来工作），再把总结织进信里，让教授看出学生真读过 |
| `cv_generator` + `cv_content_generator` | **渲染编排 vs 内容抽取**的分工。中文 CV 问卷 docx → 按 8 个中文小标题硬切分 → 每段 1 次 GPT 抽成结构化 JSON（科研经历特殊：先 1 次调用切分成 N 段，再每段 1 次）→ 渲染。共 5–8 + N 次调用，全串行 |
| `essay_paraphraser` | 4 种润色（RP/SoP/RL/PhS）走同一个 `PolishTask` 配置表；业务逻辑内部还多支持 `email`/`taoci` 两种仅本地可用的类型 |
| `moderate_visa_document` | **用 Claude Opus 而非 GPT**。扮演美国 Technology Alert List 合规审查员：识别敏感技术（AI/航天/量子/半导体/核/激光/加密）与敏感单位，改写成民用/基础研究表述。两条硬约束：论文/专利/项目名等**可被检索到的内容必须原样保留**（太敏感就整条删掉，不能改名）；输出必须是「原文 / 英文替换 / 中文说明为什么改」三段式 |
| `customize_sop` | **完全不用 AI**。顾问把各校定制句子写成 Excel 列，工具只做 docxtpl 模板合并。附带一套 `concatenate_customized_paragraph` 逻辑修复渲染后段落被拆散的问题 |
| `ai_customize_sop` | **用 Gemini Pro**。每个项目查 1–3 位目标教授的研究方向，再写一段定制结尾。核心 prompt 规则是「Gap-Filling」：不要罗列教授，而要把每位教授的工作写成申请人前文提出的某个技术难题的解法。注释明确标注「适合博士文书，硕士暂不用」 |

**模型选择汇总：** 本家族全部用 `_ADVANCED` 档。默认 GPT（`gpt-5.6-sol`），签证审查用 Claude Opus，AI 定制 SoP 用 Gemini Pro。

---

## 8. 沟通 / 面试 / 外联

### 8.1 邮件（3 个互相独立的系统）

| 系统 | 身份 | 说明 |
|---|---|---|
| `send_email_automatically` | **以学生本人邮箱发信** | 套磁主力。SMTP host 从学生邮箱域名查白名单（USTC/WHU/Wisconsin/UIC），一律 `SMTP_SSL:465`。**每所学校只发 1 位教授**（`groupby(University).head(1)`），只发 `Status` 为空的行，发完把该行标 `Sent` 并写回 xlsx——**那张 xlsx 就是"发过谁"的状态存储**。每 5 封重连一次 SMTP |
| `check_email.py` | 学生邮箱 | 回信分类。IMAP 拉信 → 启发式筛外国教授（含 `.edu` 且不含 `.edu.cn`）→ 剥引用历史 → LLM 分类成 `invite_interview`/`positive`/`negative`/`irrelevant` → **生成的回复只存进 IMAP 草稿箱，绝不自动发送**，人工过一遍 |
| `send_email_to_users.py` | 科石公司邮箱 | 群发通知/提醒/学长学姐邀请的共用传输层 |

`send_reminder_emails/remind_early_admission.py` 是**港新提前批**监控：7 所固定港新校 × 订阅者的专业，Serper 搜 → WebPilot 读页 → LLM 抽 deadline，过滤掉晚于入学年 10/1 或已过期的，按主题「【科石】港新提前批提醒」推送。

**坚果云（Nutstore）是公司真正的文件系统**（WebDAV）。`src/common/nutstore_operation.py` 封装，路径规范 `/Cornerstone/科石/学员/Fall {year}/{学员}`。COS 只是 Web 系统的传输通道；顾问日常材料在坚果云。

### 8.2 企业微信（3 条完全不同的路径，别搞混）

| 路径 | 方向 | 对象 | 说明 |
|---|---|---|---|
| **微信客服** `server/work.py` + `src/common/work/` | 入站 HTTP（8005） | **学生** | 回调只是"门铃"：解密 XML 拿 `Token` 后**主动拉取**消息。落 `work_messages` 表，交 20 线程池异步处理。走完整的**token 配额校验与扣减** |
| **智能机器人** `src/tools/aibot/` + `server/aibot.py` | **出站 WSS** | **内部员工（马老师）** | 无需公网端口。用法是员工把学生群消息转发进来，再**引用**它并附上指令——企业微信会把被引用原文一起带过来，所以服务端不需要上下文缓存。支持 text/voice/image/mixed/file 引用。模型 `gpt-5.6-terra`，**不计费** |
| `src/tools/wechat_work/` | 不是传输层 | — | 上面客服路径调用的**命令路由与业务大脑** |

`wechat_work` 的命令：`-help` / `-gpt` / `-web` / `-keshi`（RAG）/ `-learn`（管理员追加问答对）/ `-file`（让 LLM 从坚果云文件树里挑 3 个相关文件）/ `-gpa`（成绩单分析），其余默认走网络搜索。

**`data/train/embedding_qa.txt` 是什么：** 6762 行 × 768 维的**预计算 embedding 矩阵**，对应 `qa_pairs.xlsx` 里每个问题，用 `shibing624/text2vec-base-chinese-paraphrase` 编码，服务 `-keshi` 命令。检索是**语义 + 关键词双闸门**混合设计：先用 `raw_keywords.txt`（~345 个同义词组，如 `CV, resume, 个人简历, 简历`）把查询词归一化，语义召回 top-5，然后每个候选还必须与查询的关键词集合有交集才放行——这样「DS-160 的 deadline」就不会命中另一种表格的语义近邻问题。注意 `-learn` 只追加 CSV、**不重算 embedding**，新学的问答对要离线重编码后才能被检索到。

**GPA 分析（`-gpa`）** 是 USTC 专用：纯正则解析成绩单 PDF（按 `20\d\d(FA|SP|SU)` 学期码定位课程行，一行可能有两门课），然后**双标度换算**——USTC 4.3 制 + **Scholaro 4.0 制**（美国招生办实际采用的第三方认证换算），并算出总 GPA / 专业 GPA / 高年级（upper-division）GPA。专业 GPA 靠 LLM 逐年判断哪些课计入，返回长度与课程数不符则整体放弃计算而不是错位对齐。

### 8.3 模拟面试

- **文本版** `mock_interview_text`：**无状态、按轮调用**——客户端每轮把完整 `message_list` POST 上来，返回一轮教授回复；`end_str == 'end'` 时不再回复，转而生成逐条点评的反馈报告并上传 COS。刻意用两个模型：多轮对话用 BASIC（便宜），单次反馈用 ADVANCED。
- **语音版** `mock_interview_voice`：**独立 FastAPI 应用**（2025-03-14 外购，非本团队原创）。ASR = ElevenLabs `scribe_v1`，TTS = Minimax `speech-01-hd`。鉴权是从主系统一次性 SSO 交接：Redis key `corner:<token>` 取出后**立即删除**，再发 3 小时 cookie 并**绑定客户端 IP**。30 分钟硬性时长上限 + 20 分钟空闲清理。所有会话状态在进程内存 dict 里，所以 gunicorn 必须 `workers=1`。

### 8.4 学长学姐匹配 `connect_mentors`

**"mentor" 指科石自己的往届学员**（已出国在读、愿意被咨询），不是教授。名册是 `src/common/data/mentor_data.xlsx`。按咨询主题分三套算法：

| 主题 | 算法 |
|---|---|
| `方向选择` | LLM 重。先让模型从数据里的目标专业集合中挑出覆盖学生兴趣的那些，再按六档 rubric 给 0–1 打分，阈值 0.8。**排序先看活跃度再看匹配度**——对一个"失败模式是邮件没人回"的系统，这是对的 |
| `实验室选择` | **刻意不用 LLM**。手写最长公共子序列相似度，阈值 0.33。且排除资历超过约博三的人（离校太久不了解现在的实验室情况） |
| `落地事宜` | 三次串联 LLM：定国家 → 把学生给的任意学校写法归一成单个 QS 可检索关键词（`普渡 → Purdue`、`UCSB → Barbara`）→ 在候选中消歧。美国要求学校精确匹配，其他国家国家匹配即可 |

**表结构是"请求 → 结果"对**：`match_mentor` 一行一个学生请求；`match_mentor_detail` 一行一个匹配到的 mentor，`mentor` 列存整个 dict 的 JSON，另有随机 32 位 `token`——那是邀请邮件里免登录同意/拒绝链接的凭证。detail 的 `status` 记的是**外联生命周期**（0 已匹配 / 1 发送失败 / 2 已发送…），**匹配分本身不落库**，只体现在插入顺序上。

### 8.5 其他

- `finalize_application_list/` — **选校清单终稿**，粒度是"每个（项目, 研究方向）一行"。作用是把三个独立渠道来的教授推荐合并成一张决策表：`套磁积极回复的教授` / `学长学姐推荐` / `科石推荐`。它的 `readme.md` 比代码更有价值——记录了完整 8 步人工流程，包括生成"科石推荐"列所用的 Gemini Deep Research 逐轮 prompt 原文。
- `determine_objectives/` — `determine_research_interest.py` 是多轮中文对话帮学生收敛出英文研究关键词；`check_sbr.py`（SBR = Similar Background Result）按院系 + GPA ±0.15 筛历史案例。`constants.py` 是 USTC 领域词表（院系缩写/代码映射、小专业归并表）。
- `modules/service/generate_overall_database.py` 的 `CasesDatabase` — **申请区间**的现行实现。合并人员表/今年申请情况/资源表，按 GPA(±0.2)/国家/年份/专业/学位筛相似案例；**若不足 5 例则以 0.1 步长逐步放宽 GPA 窗口最多到 ±1.0**，然后按 QS 专排排序返回 **30/50/70 百分位**三档案例——学生看到的是 reach/target/safety 三个层次而不是一个数字。
- `src/tools/chat/` — `/v1/chat/completion` 的两个 SSE 生成器，按 `driver` 字段选 `chat`（gpt-5，失败时往企业微信群 webhook 告警）或 `chat_ghyx`（gpt-4.1）。
- `src/tools/application_todo/` — **已空**，只剩空 `__init__.py` 和一个 `remind_single_student` 的残留 `.pyc`。相关功能现在在 `src/common/data/timeline/`（`corner_process_list.csv` 是申请任务图的唯一真源，`sync_process_list.py` 字段级 diff 同步进 DB 且**从不删行**）。

---

## 9. 公共基础设施 `src/common/`

| 文件 | 作用 |
|---|---|
| **`ai_query.py`** | ★ 所有 LLM 调用的统一入口。`AIQueryManager(engine, model, return_details)` 工厂，4 个引擎：`gpt` / `claude` / `gemini` / `deepseek`。模型常量分 BASIC/ADVANCED 两档（见下）。`return_details=True` 时返回 `{"content", "token_used"}` 而非裸字符串，这是全系统计费的来源 |
| **`web_query.py`** | ★ 联网搜索统一入口。`WebQueryManager(default_engine="gemini")`，4 个引擎：`gemini`（Google Search grounding，**当前默认**）/ `serper` / `webpilot` / `perplexity`。`GoogleSerper.do_scholar_query` 额外负责 Google Scholar 引用数统计与代表作匹配 |
| `cos_operation.py` | 腾讯云 COS 上传/下载。**下载用裸 `requests.get`（不签名）→ bucket 必须公读** |
| `nutstore_operation.py` | 坚果云 WebDAV：读写、递归列目录、MKCOL 建目录、生成公开分享链接 |
| `operate_file.py` | `read_file` 按扩展名分派 pdf/docx/txt/**tex**；`create_docx_from_string`；`ensure_directory_exists`；`read_qs_departments` |
| `extract_json.py` | ★ LLM 返回值解析。剥 ``` 围栏、截取首 `{` 到末 `}`、Python 字面量转 JSON。**解析失败返回 `{}` 而不抛异常** |
| `log.py` | `save_log(content, suffix)` → `logs/{日期}_{suffix}.log`。**`ENVIRONMENT == "local"` 时直接 return，本地不写日志** |
| `database_operation.py` | 名字叫 database，实际是**读 QS CSV**（`get_research_data` / `get_requirement_data`）并统一列名 |
| `user_service.py` | token/query 配额校验与扣减（企业微信客服路径用） |
| `translations.py` | 国家/专业中英翻译表，供 `/config/translations` 端点 |
| `normalize_institution_name.py`、`string_operation.py`、`extract_urls.py`、`error_handler.py` | 工具函数 |
| `crawl_csranking.py` | Selenium + undetected_chromedriver 抓 CSRankings（JS 渲染），会动态抬高最小发表数阈值直到名单 < 300 人 |
| `transcribe.py`、`send_sms.py` | 语音转写、Twilio 短信 |
| `work/` | 企业微信客服 SDK：`service.py`（CorpApi，access_token 与游标都存 Redis）、`message.py`（业务闸门）、`WXBizMsgCrypt.py`（官方加解密） |

**当前模型常量**（`ai_query.py:15-22`，注意是自定义/内部命名，随 commit 变动频繁）：

```python
GPT_MODEL_BASIC    = "gpt-5.6-luna"      GPT_MODEL_ADVANCED    = "gpt-5.6-sol"
CLAUDE_MODEL_BASIC = "claude-sonnet-5"   CLAUDE_MODEL_ADVANCED = "claude-opus-5"
GEMINI_MODEL_BASIC = "gemini-3.6-flash"  GEMINI_MODEL_ADVANCED = "gemini-3.1-pro-preview"
DEEPSEEK_MODEL_BASIC = "deepseek-chat"   DEEPSEEK_MODEL_ADVANCED = "deepseek-reasoner"
```

> `AIQueryManager.do_query` 会**把完整 prompt 和完整响应 print 到 stdout**（`:240-245`）。CV 生成一次请求会刷 12+ 段完整 prompt。
> `GPT.do_query` 捕获所有异常并返回字符串 `"OpenAI API Error"` 而**不抛出**——这个字符串会一路流进生成的文档；在 CV 路径里它进 `extract_json` 变成 `{}`，结果是"缺了几个 section 的 CV"而不是一个报错。Gemini/Claude 引擎**没有**这层兜底，会正常抛异常。**所以错误行为因能力而异。**

---

## 10. 静态数据资产 `src/common/data/`

| 路径 | 内容 | 状态 |
|---|---|---|
| `rankings/qs/` | 22 个 CSV：`Overall.csv` + 21 个专业榜。表头 `Rank_Global,Rank_Domestic,Institution,中文名,Location,国家,Score` | ★ **在用**。QS 2026 版。同时有全球排名和**国内排名**，这是"某国第 1–10 名"筛选能成立的前提 |
| `rankings/usnews/` | 19 个 CSV，表头只有 `Rank,School` | **死数据**，全仓库零引用 |
| `translations/` | `universities.csv` / `majors.csv` / `countries.csv` | 在用 |
| `timeline/` | `corner_process_list.csv` + `sync_process_list.py` | 在用，申请流程任务图 |
| `background_and_result/` | `background_and_result.xlsx`（+ `_private`） | 在用，历史案例库（申请区间 / SBR） |
| `mentor_data.xlsx` | 学长学姐名册 | 在用。**含真实姓名/学校/邮箱且已入 git** |

各工具包下的 `data/` 目录（`src/tools/*/data/`）已在 `.gitignore`，里面是历年跑出来的产物与真实学生案例，**不是代码依赖**。

---

## 11. 数据库表（算法端读写的）

MySQL 裸 SQL，封装在 `server/components/mysql.py`。**权威 schema 在 `corner_stone_ops/corner.sql`**（`keshi_api/database/migrations/` 只有 4 个迁移文件，绝大多数表是手工建的）。

| 表 | 谁写 | 关键列 |
|---|---|---|
| `task_records` | 两端 | 任务总账。`input`(JSON payload) / `status` / `output`(`{"output_file": cos_url}`) / `response`(错误文本) |
| `task_generate_content` | **仅 Python** | 生成的文书正文。`content`(longtext) / `output_file_extension` / `cv_json_data` |
| `task_check_research` | 仅 Python | 查教授的逐条结果（本次任务快照） |
| `task_check_research_common` | 仅 Python | ★ **跨任务缓存主表**，唯一键 `major+university+faculty` |
| `task_check_requirement` | 仅 Python | 查要求的逐条结果 |
| `task_check_requirement_common` | 仅 Python | ★ 跨任务缓存 |
| `admin_user_token` | 两端 | 4 组独立配额：`token/token_used`、`query/query_used`、`match_times/*`、`total_coin/coin_used`（**只有科石币是现行货币**，余额恒为 `total_coin - coin_used` 实时算，不落库） |
| `admin_user_token_logs` | 两端 | 扣费流水。PHP 写中文描述、Python 写裸 `func_name`，**格式不统一** |
| `task_coin_config` | PHP 后台维护 | `func_name → cost`。**cost 数值只存在生产库里**，仓库里没有 seeder |
| `match_mentor` / `match_mentor_detail` | 两端 | 学长学姐请求 / 结果 |
| `work_messages` / `wx_name_map` | 仅 Python(`server/work.py`) | 企业微信会话归档 / 微信身份↔平台账号映射 |
| `admin_users` | PHP | 学生与管理员共用一表。`nickname` = 中文名（就是 payload 里的 `realname`）、`cos_path` = 个人 COS 根目录 |

**计费机制（三处分离、无事务）：**
1. PHP `TokenService::checkUserToken` 只**校验**余额 ≥ 5 币，**不扣**；
2. Python 在任务**跑完之后**读 `task_coin_config.cost` 才扣。

所以实际花费从未与余额比对过：5 币的用户可以触发 200 币的任务，`coin_used` 直接超过 `total_coin`，余额变负数。且 `check-research`、`send-emails`、`generate-customized-sop` 三个 handler **根本不扣费**——查教授是系统里最贵的操作，却是免费的（前端还照样显示价格）。`update_user_coin` 遇到不存在的 `func_name` 只记日志然后 return，**任务照样成功且免费**。

---

## 12. 环境变量（`.env`，不入库）

```
# LLM / 搜索
OPENAI_APIKEY, OPENAI_APIKEY_FRONTIER, CLAUDE_APIKEY, DEEPSEEK_APIKEY, GEMINI_APIKEY
WEBPILOT_APIKEY, PERPLEXITY_APIKEY, SERPER_API_KEY, WEB_SEARCH_ENGINE
GLM_API_KEY, QWEN_API_KEY               # 语音面试备选
ELEVENLABS_API_KEY, MINIMAX_API_KEY     # 语音面试 ASR/TTS
# 存储 / 中间件
COS_APPID, COS_SECRET_ID, COS_SECRET_KEY, COS_REGION, COS_BUCKET, COS_HOST
NUTSTORE_APIKEY
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_AUTH
# 邮件 / 通讯
QQ_MAIL_SMTP_PASSWORD, USTC_MAIL_SMTP_PASSWORD, TWILIO_TOKEN
WORK_TOKEN, WORK_AES_KEY, WORK_CORP_ID, WORK_CORP_SECRET   # 企业微信客服
AIBOT_BOT_ID, AIBOT_SECRET                                 # 企业微信智能机器人
# 运行环境
ENVIRONMENT            # "local" | 其他 → 决定 PROJECT_ROOT、source_type、是否写日志
PROJECT_ROOT_LOCAL, PROJECT_ROOT_SERVER, TOKEN_LIMIT, QUERY_LIMIT
```

`src/config.py` 存**非敏感**配置（刻意不进 `.gitignore`，方便多端同步）：`enroll_year`、`PROJECT_ROOT`、`source_type`、`GPT_DEFAULT_MODEL`。

---

## 13. 约定与陷阱

### 编码约定
- **函数注释一行足矣**，不写参数/返回类型说明（团队规范）。
- 业务逻辑类的 docstring 统一是 `"""Pure business logic for X. ... agnostic to data source types."""`，看到这句就知道它归 `src/tools/` 层。
- 层间数据交换统一 `list[dict]`；返回统一带 `token_used` / `query_used`。
- 大量 prompt 要求 LLM **找不到就删掉该 key**（而不是填 `"N/A"`），下游才能用 `if 'x' in data` 和 Jinja `{% if %}` 判空。

### 每年/每期要手工改的硬编码
| 位置 | 值 |
|---|---|
| `src/config.py:11` | `enroll_year = 2027` |
| `src/tools/wechat_work/manage_message.py:118` | `entry_year = 2021`（代码里就写着"每一年记得换一下"） |
| `manage_message.py:29` | 管理员白名单 `["马德恩", "小羊"]` |
| `send_reminder_emails/remind_early_admission.py:44` | 3 个管理员邮箱 |
| `src/common/data/rankings/qs/` | QS 榜单需换版 |

### 已知坑（读到相关代码时注意，本次未修改任何代码）

**跨层不一致**
- `rank_source` 拼写有三套并存：`check_requirement.py:48,164` 比的是中文 `"QS专排"`，但同文件 docstring 和 `task_development.py:109` 用的是 `"qs-subject"`/`"qs-overall"`。用 ASCII 写法会**静默回落到 Overall 榜，并完全跳过缓存**，每行都重新联网查。
- `CSRankings` 在服务端不可达：`handle` 只接受 `qs*` 和 `customized`，但 `_save_results` 还在判断 `rank_source == 'cs-ranking'`，示例 payload 又写 `"csrankings"`——三种拼写。
- COS 路径 tag 用的不是 `task_name`：`CosHandleService::getSavePathByTaskName` 里是 `send_emails`（下划线），任务名是 `send-emails`（连字符）。
- `modules/user_service.py:63-71` 的 `insert_check_requirement_result_customized` 往 `query_type_note` / `query_type_link` 插值，而同文件 `:73-82` 的非 customized 版本用的是 `query_remark` / `query_link`——两者必有一个列名是错的。

**静默失效**
- `modules/task/check_research.py:15` 从 `src.common.log` 导入 `save_log`，但 `:530` **又在模块级重定义了同名函数**（只 print）。Python 取后者，所以该文件里 24 处调试日志全都只打屏、进不了日志文件。
- `requirement_finder.py:19,32` 判断的是小写 `"toefl"` / `"gre"`，但调用方 `:138` 传的是大写 query_type。**两个分支永远不可达**，TOEFL 的"分数 >120 判为纸考、置 N/A"保护和 GRE 取值白名单校验从未生效。
- `cv_content_generator.py:18` 的 `escape_chars` 替换顺序反了：`data.replace('%','\\%').replace('\\','\\\\')` 让 `10%` → `10\%` → `10\\%`（LaTeX 里是换行+注释，不是百分号）。而且它在选择输出格式**之前**无条件执行，Word 版 CV 也会带上 LaTeX 转义符。
- `server/api.py:45-51` 一次性 secret 的**校验被注释掉了**——key 照删但从不检查是否存在，任何值都能通过。`/v1/chat/completion` 目前是个无鉴权的 LLM 代理。

**可靠性**
- `server/task.py:41-51` 是 `while True` + **`LPOP`（不是 `BLPOP`）+ sleep 30s**，无 ack、无重试、无死信队列。worker 中途崩溃消息就丢了，而且 `:67` 已经把状态改成 2（执行中），那行会永远卡在"执行中"。
- 各 handler 又把 `handle` 提交给 `ThreadPoolExecutor(max_workers=2)` 后立即返回，所以主循环会继续取下一条，第 3 条起在 executor 队列里静默排队。
- `task_records.output` 是 `varchar(1000)`，装的是含 URL 编码中文路径的 COS 链接（每个汉字约 9 字节），深路径 + 长文件名会**静默截断**。
- COS object key 直接复用原始文件名、不加随机后缀，两个学生往同一任务目录传 `CV.pdf` 会互相覆盖。

**未完成 / 死代码**
- `modules/task/generate_customized_sop.py` — `handle` 是 `pass`，文件第 1 行写着 `# seems not used`。两个 SoP 定制工具目前**只能本地跑**。
- `modules/task/apply_interval.py` — 第 1 行标 `# seems deprecated`，现行实现是 `CasesDatabase`。`modules/service/apply_interval.py` 是孤立残片（模块级函数带 `self` 参数）。
- `src/tools/find_faculties_research_area/experimental/` — 已 gitignore。设计方向是对的（先抓完整 roster 再判相关性，批量 20 人一次 LLM 调用），但 `57cdc49` 重命名了基类方法后没同步，`faculty_finder_v2.py:140,159,201` 引用的方法已不存在，**跑起来必 AttributeError**；`compare_v1_v2.py` 又用 try/except 吞掉了，A/B 结果会显示 v2 找到 0 个教授。
- `test/find_faculties_research_area/data_access.py` — 规范明令禁止的文件，且 `test_faculty_finder.py:14` 导入它之后又在 `:21` 重定义同名类把它遮蔽了。纯死代码。
- `send_email_from_student_using_nutstore.py:7` 导入的 `send_email_from_student` 已被删除，**当前直接 ImportError**。
- `obtain_final_application_list.py:218` 在遍历 `recommended_faculty` 的循环里用了 `contacted_faculty["name"]`——复制粘贴 bug。
- `prompts.py` 中 `prompt_customized_sop_shorten`、`prompt_paraphrase_by_paragraph_sop`、`prompt_paraphrase_by_paragraph_rp` 定义了但无调用方（逐段润色策略已被整篇润色取代）。

**安全**
- `src/common/work/message.py:12-15` 硬编码了企业微信的 4 个凭证（且是多余的，`server/work.py:16-19` 已从环境变量读同样的值）；`src/tools/chat/chat_ghyx.py:15,74` 硬编码了两个 OpenAI key。**这些都已入 git。**
- 套磁流程需要学生**本人邮箱的明文密码**：服务端从 task JSON 的 `userinfo['email-password']` 取（而 task payload 会被完整写进日志），本地模式则明文存在 `credentials.xlsx`。

---

## 14. 快速上手：我该改哪里？

| 我想… | 去改 |
|---|---|
| 调某个算法的 prompt | `src/tools/<工具>/prompts.py` |
| 换模型 | 调用处的 `AIQueryManager(engine=..., model=...)`；常量在 `src/common/ai_query.py:15-22` |
| 换搜索引擎 | `WebQueryManager(default_engine=...)` 或 `do_query(engine=...)` |
| 改产物文件名 / 落库字段 | `modules/task/<任务>.py` 的 `_save_and_upload_results` |
| 本地手动跑一次算法 | `test/<工具>/` 下对应入口，改顶部参数字典后直接 `python -m test.xxx.yyy` |
| 本地调一条服务端任务全链路 | `server/task_development.py`，改末尾 `task_str` |
| 加一个新任务 | ① `src/tools/新工具/` 写纯逻辑 ② `modules/task/新任务.py` 写 I/O 编排 ③ `server/task.py` 加 `elif` ④ 让后端加 `task_name` 与 `task_coin_config` 行 |
| 排查线上任务失败 | `logs/{日期}_server_task_error.log`；注意 `ENVIRONMENT=local` 时 `save_log` 直接 return |
| 查任务入参有哪些字段 | `server/task_development.py:99-110` 和 `server/task.py:119-124` 的注释样例，最可靠 |

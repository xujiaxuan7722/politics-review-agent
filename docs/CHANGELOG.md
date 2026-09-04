# 2026-09-04 第六至十轮：学习管家升级为唯一编排层、模型端点分组、多轮对话、判卷评测

1. **学习管家工具 4 → 8**：新增 `get_due_cards` / `get_mistakes_by_topic` / `explain_topic` / `grade_answer`，讲解与判卷作为工具被编排；复习规划主路改走学习管家（规划专用规则），未取数或收尾失败时回退确定性规划模块。
2. **循环护栏四道**：同参数重复调用拦截（两次强制收尾）；步数用尽时不带工具再问一次，凭已取数据收尾；单工具异常回灌模型继续；只描述计划不执行时推一把（最多一次）。
3. **SSE 进度流** `POST /api/agents/manager-stream`：思考 / 工具开始 / 完成 / 失败 / 推一把 逐条推送，前端步骤标签实时变色，显示实际模型与是否降级。
4. **多轮对话**：新表 `conversations` / `conversation_messages`（Alembic `0097716b701c`），只存用户话与最终答复，带最近 12 条且总长 6000 字内；`GET/DELETE /api/agents/conversations[/{id}]`；前端对话记录、新对话、回到历史对话。
5. **模型端点分组与降级链**：向量组 / 通用对话组 / 判卷专用组 / 学习管家专用组，各自可指向不同平台；思考开关按平台翻译（`enable_thinking` vs `reasoning_effort`）；请求直连不走系统代理；通用组 2s/4s 退避重试后降级到基础组，学习管家三级链 专用 → 通用 → 基础；`CHAT_THINKING` 全局总闸；LaTeX 清洗兜底。当前配置：管家 / 判卷 = 商汤 DeepSeek-V4-pro，通用 = DeepSeek-V4-flash，向量 = SiliconFlow bge-m3。
6. **判卷准确率评测**：`evals/grading/questions.jsonl` 46 题 + `scripts/eval_grading.py`（评测只重试不降级、429 退避、`--rescore` 重算、`--report` 汇总）。pro / flash 判断与答案准确率均 100%，Qwen3-8B 判断 84.8%。
7. **题型由拍题的人指定**：`question_type` = single / multi / unknown 贯穿判卷接口、拍题流水线与管家工具；前端两处按钮组；学生多选作答可解析。不按题号或试卷惯例推断。
8. 清理：一次性数据修复脚本删除，`修改说明.md` 迁入 `docs/CHANGELOG.md`，README 重写学习管家章节、加界面截图与评测表。pytest 19 → 62。

---

# 2026-08-21 第五轮：README、Docker Compose、学习数据看板

1. **README.md**（项目根）：功能概览、架构图、两种多智能体形态说明、技术选型与取舍表、Compose / 本地两种启动方式、环境变量、知识库构建、迁移、测试、API 一览、目录结构、已知限制。
2. **Docker Compose**：`docker-compose.yml` + `backend/Dockerfile`（启动自动 `alembic upgrade head`）+ `frontend/Dockerfile`（多阶段：Node 构建 → nginx 托管，`/api` 反代到后端并关闭缓冲以支持 SSE）。`docker compose up -d --build` 后访问 http://localhost:8080；后端调试口 127.0.0.1:8003。`request.js` 支持 `VITE_API_BASE=""` 表示同源。
3. **学习数据看板**：新增 `GET /api/stats/learning`，基于 `review_logs` 聚合：累计复习次数 / 整体记住率 / 连续学习天数 / 已掌握卡片、最近 14 天每日复习量与记住率、**间隔保持率**（按上次间隔分桶的记住比例，近似遗忘曲线）、最常遗忘的知识点（按卡片 lapses）。总览页新增对应统计卡与三张单系列柱状图（带悬浮提示）。pytest 增至 19 个。
4. 拍题卡片规则：拍题来源的复习卡卡面为完整原题、背面为"正确答案及解析"；默认标题取题干前 20 字；`scripts/rebuild_pipeline_cards.py` 已对旧数据对齐。

---

# 2026-08-21 第四轮：PostgreSQL + Alembic、测试、三处缺陷修复

## 数据库：SQLite → PostgreSQL（独立容器，端口 15434）
- 容器：`docker run -d --name politics-postgres -e POSTGRES_USER=politics -e POSTGRES_PASSWORD=politics_dev -e POSTGRES_DB=politics -p 127.0.0.1:15434:5432 -v politics_pgdata:/var/lib/postgresql/data postgres:17`
- `.env` 的 `DATABASE_URL` 改为 `postgresql+psycopg://politics:politics_dev@127.0.0.1:15434/politics`；仍兼容 SQLite（测试即用 SQLite）。
- **表结构由 Alembic 管理**（`alembic/`），不再在启动时 `create_all`。建库：`alembic upgrade head`；改模型后：`alembic revision --autogenerate -m "说明"` → `alembic upgrade head`。
- 旧 SQLite 数据已用 `scripts/migrate_sqlite_to_pg.py` 搬入（保留原 id）。`politics.db` 留作备份。

## 数据结构重设计（`app/models.py`）
- 全部业务表加外键指向 users，数据库层 `ON DELETE CASCADE`；复习卡的 `mistake_id` 可空（学习管家直建的卡）并随错题级联删除。
- `mistakes` 新增 `source`（manual/pipeline/chat/agents）、`student_answer`、`judgement`、`updated_at`。
- `review_cards` 新增 `lapses`（累计忘记次数）、`last_reviewed_at`；新增 **`review_logs`** 表记录每次打分（quality、前后间隔、EF），供学习曲线分析。
- 复合索引：今日待复习 `(user_id, next_review_date)`、记录列表 `(user_id, created_at)`、错题 `(user_id, created_at)`。

## 测试（`tests/`，`pytest` 18 个用例全绿）
- SM-2 调度 6 个：失败重置并当日重现、1→3→round(3×EF) 间隔推进、EF 下限、quality 越界夹取、答对 EF 上升。
- 解析 5 个：判断解析三种结果与缺失、复习卡 Q/A/K 解析、模板回显卡片拒收、学生选项提取。
- 接口 7 个：记录接口鉴权/列表/搜索/来源筛选/删除/清空/用户隔离、今日队列与失败卡当日重现、答对出队并写 review_logs、跨用户越权 404、登录与 token 校验。
- 测试用独立 SQLite 文件库，不碰开发库：`cd backend && .venv/bin/python -m pytest tests -q`。

## 缺陷修复
1. **易错提醒经常为空**：`clean_grade_output` 里残留的旧规则会删掉所有含"关键词"三个字的行（易错提醒几乎必写"抓题干关键词"），另一条正则还会吃掉中文之间的字母 D。已把该函数精简为纯空白归一化。
2. **同题两次判答不一致**：根因是该题实为多选（"政治/思想/理论/情感认同"四个全对）而 OCR 文本丢了多选标记 + 知识库不含这段时政 + 思考模式采样随机。修复：批改前先检索知识库作为依据、温度降为 0、提示词要求"题干未标单/多选且多项成立时明确写出疑为多选，不要硬选"、时政不确定时声明以官方材料为准。
3. **复习卡标题显示"复习问题"**：制卡提示词占位符被照抄。改为「」占位骨架 + 解析层拒收模板回显卡片（退回到基于错题内容构卡）。
4. UI：复习打分按钮改为实色大号；判题结果标签不再拉伸成整行。

---

# 2026-08-20 第三轮：提速、搜索记录、学习管家

1. **流式输出提速**：智能问答改为 SSE 流式接口 `/api/chat/ask-stream`，先推送知识库检索元信息，再逐段推送回答。实测首包约 3 秒开始出字（原来要白屏等 60 秒生成完才显示）。批改/分析保留思考模式（准确率优先），其余场景关闭以提速。
2. **搜索记录板块**：新增 `search_records` 表和 `/api/records` 接口（列表/关键词搜索/来源筛选/单条删除/清空），智能问答、拍题判卷（判题+解析）、出题、批改、规划、学习管家的结果由**后端自动落库**，跨设备不丢。前端新增「搜索记录」页（支持查看详情、重新提问、删除）；删除了原来只存浏览器 localStorage 的记录逻辑（`searchHistory.js`）。
3. **学习管家智能体**（`services/manager_agent.py` + `/api/agents/manager`）：基于 function calling 的自主调度智能体，模型自行决定调用哪些工具——检索知识库 / 查用户真实学习状态 / 生成练习题 / 创建复习卡——循环执行直到完成任务，返回最终答复和执行轨迹。这是系统中真正意义上的自主多智能体协作：实测"找我最薄弱的考点→讲解→做背诵卡"任务，管家自主完成了查状态→检索→建卡三步。前端在「练习与规划」页首个页签。
4. **高数痕迹清零**：删除 `mathRender.js`（98 行 LaTeX/KaTeX 管线）换成纯 markdown 渲染 `markdown.js`；卸载 katex、markdown-it-katex 依赖；CSS 类 `math-answer` 全部改名 `answer-body`；删除 Vite 模板残留 `HelloWorld.vue`。全项目 grep 确认无 gaoshu/高数/katex 残留。

---

# 2026-08-20 第二轮：免费条件下的整体升级

（当天下午完成，接在第一轮安全修复之后）

## 密钥
- `.env` 已换成项目所有者本人的 SiliconFlow 与百度 OCR 密钥（旧密钥属于他人账号且已随压缩包泄露，应通知原持有者作废）。

## 升级内容
1. **模型**：Qwen2.5-7B-Instruct → **Qwen3-8B**（免费档），全局加 `frequency_penalty=0.4` 抗复读；`call_qwen` 新增 `thinking` 参数，**批改和错题分析走思考模式**（先推理再作答，判题准确率显著提高）。删除了最伤内容的清洗正则（整句删数字、删连续字母）。
2. **内置 RAG 知识库**（替代未部署的 RAGFlow）：`scripts/build_rag_index.py` 把 `data/politics_cleaned.md` 切成 384 块、用免费 bge-m3 嵌入建索引（`data/rag_index.json`）；`rag_service.py` 余弦检索。问答与讲解自动带教材上下文，回答标注知识库引用。**语料更新后重跑该脚本即可**。
3. **解除深度枷锁**：删掉所有"260-420字""500字以内"类限制和 `build_fallback_explainer` 万能空话模板；问答/讲解改为"结论→要点→深挖（易混辨析+考法）"三层结构，错题分析新增"举一反三"。
4. **产品闭环重构**：
   - "知识库问答"+"概念讲解"合并为**智能问答**单一入口（有知识库走 RAG，检索不到自动降级直答并明示）。
   - "图片识别"改为**拍题判卷**：识别 → 文本可编辑校正 → 先自己作答 → 一键"判题并归档"。
   - 新增**多智能体流水线** `/api/pipeline/grade-archive`：批改 → 答错自动写入错题本（批改解析即错题分析，不重复调用）→ 自动生成复习卡 → 进入今日队列。
   - 错题本新增**手动录题**；保存问答内容不再往 `knowledge_points` 塞"RAGFlow 知识库"之类的 UI 标签（改由后端提取真实考点）。
   - 复习打分改为**忘了/有点模糊/记得牢**三档；"忘了"的卡**当天重新出现**直到记住（原来要等到明天）；同一错题重复生成复习卡改为替换，不再堆积。
   - **复习规划智能体自动读取数据库**里的真实错题分布、最近错题和薄弱知识点，不再要求用户手动打字描述自己的错题。
5. 后端固定跑在 **8002** 端口（本机 8001 被其他服务占用），前端 API 地址见 `frontend/.env.development`。
6. **提示词工程修正**：问答格式指令中被引号强调的成分描述（"知识解释 + 考研政治答题角度"）曾被模型当作字面模板逐条照抄为标题。根治方式是把"描述式格式要求"改写为"骨架示范 + 占位符"（「……」标注填充位，要求每条标题用该条自己的考点短语），并对"深挖"部分做同样处理。经三个不同主题实测无照抄、无示例泄漏。教训：给中小模型写格式指令，宁可给骨架示例，不要用引号短语描述成分。

---

# 2026-08-20 第一轮：安全与质量修复说明

## 如何本地运行

后端（端口 8002，因本机 8001 已被其他服务占用）：

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002
```

前端（开发模式，端口 5173，API 地址在 `frontend/.env.development` 里配置）：

```bash
cd frontend
npm install
npx vite
```

## 修复内容

### 安全（高优先级）
1. **删除了随包分发的 `gaoshu.db`**：里面有真实用户的密码哈希（其中一个是弱口令 123456 的无盐 SHA-256，可被秒破）和一个仍然有效的登录 token。现在启动时自动新建空库 `politics.db`。
2. **新增 `.env.example` 与 `.gitignore`**：`.env`（真实密钥）和 `*.db` 不再随包分发。⚠️ 旧压缩包里的 SiliconFlow / 百度 OCR / RAGFlow 密钥已经泄露，**请尽快在对应控制台作废并重新生成**，然后填入本地 `.env`。
3. **密码哈希从无盐 SHA-256 升级为 PBKDF2-SHA256（12 万次迭代、随机盐）**；旧哈希登录成功后自动升级。登录 token 增加 7 天有效期（`token_expires_at` 列，启动时自动迁移）。
4. **补齐鉴权**：`/api/chat/ask` 和全部 4 个 `/api/ocr/*` 接口原来无需登录即可调用（任何人都能消耗 OCR 和大模型额度），现在统一要求登录。
5. **OCR 上传增加限制**：仅接受图片类型、最大 5MB、拒绝空文件。
6. **CORS 修正**：原来 `allow_origins=["*"]` 且 `allow_credentials=True`（规范不允许的组合），改为仅放行 `localhost:5173 / 127.0.0.1:5173`。

### 正确性
7. **删除了批改智能体中的硬编码判题规则**（`grade_choice_locally` / `infer_politics_correct_choice` 等）：这些规则只覆盖 5 种关键词组合，遇到否定式题干（"下列**不属于**……"）会给出错误答案并配上自信的错误解析，且其中一条规则明显是照着演示题定制的。现在批改统一走大模型（保留本地解析学生所选选项，作为提示传给模型）。
8. **接口错误语义统一**：错题/复习卡不存在时返回 HTTP 404 + detail，不再返回 200 + `{"error": ...}`；前端相应更新。
9. **问答接口不再返回 `raw` 字段**（原来把 RAGFlow 完整原始响应回传给前端）。
10. **RAGFlow 不可用时优雅降级**：`/api/chat/ask` 检测到知识库服务连不上时改为大模型直答，响应中标注 `mode: "llm"` 和"未经知识库校验"的提示，而不是直接 502。

### 工程质量
11. **出题智能体减少大模型调用**：原来每道题固定"生成+包装"两次调用（最多 4 次），现在先校验原始输出，合格就直接返回，不合格才追加包装调用。
12. **清理"高数系统"改造残留**：`math_prompt_rules.py` → `prompt_rules.py`、`DEFAULT_MATH_SYSTEM` → `DEFAULT_SYSTEM`、删除无引用的 `clean_math_ocr_text`、数据库改名 `politics.db`。
13. **requirements.txt 精简**：删除未使用的 easyocr/torch/opencv 等重依赖（实际 OCR 走百度 API），后端依赖从 47 项减到 7 项；删除了前端目录下误放的 Python requirements.txt。
14. **前端 API 地址可配置**：`baseURL` 改为读取 `VITE_API_BASE` 环境变量（见 `frontend/.env.development`），不再写死。
15. 删除随包的 `__pycache__/` 与 `dist/`。

## 尚未处理（建议）
- PPT 与代码不一致：PPT 宣称的"调度引擎、知识优化 Agent、知识图谱、管理员权限、问答记录同步"代码中不存在，建议改 PPT 措辞或补实现；"艾宾浩斯曲线"实际实现是 SM-2 算法，如实写 SM-2 更专业。
- Qwen2.5-7B 输出退化（乱字、重复）仍靠约 500 行正则清洗兜底，且清洗规则较激进（如会删除 "ABD" 这类连续字母、含 5 位以上数字的整句）。根治办法是换更大的模型或调整解码参数。
- 聊天问答未落库，历史只存浏览器 localStorage。

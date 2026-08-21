# politics-review-agent · 考研政治抗遗忘复习系统

> 面向考研政治的 AI 辅助复习平台：**自建 RAG 知识库**、**Function Calling 学习管家智能体**、**拍题判卷多智能体流水线**、**SM-2 间隔重复**。
> 后端 FastAPI + SQLAlchemy + PostgreSQL（Alembic 迁移），前端 Vue 3 + Element Plus，大模型走 SiliconFlow（免费档 Qwen3-8B），OCR 走百度通用文字识别。

---

## 目录

- [功能概览](#功能概览)
- [系统架构](#系统架构)
- [多智能体设计](#多智能体设计)
- [技术选型与取舍](#技术选型与取舍)
- [快速开始](#快速开始)
- [知识库构建](#知识库构建)
- [数据库与迁移](#数据库与迁移)
- [测试](#测试)
- [API 一览](#api-一览)
- [目录结构](#目录结构)
- [已知限制与路线图](#已知限制与路线图)

---

## 功能概览

| 模块 | 说明 |
|---|---|
| 智能问答 | 先在内置政治知识库做向量检索，再由大模型按"结论 → 要点 → 深挖"三层作答；**SSE 流式输出**，首字约 3 秒；回答标注知识库引用 |
| 拍题判卷 | 上传题目截图 → OCR → **可编辑校正** → 先自己作答 → AI 判题（思考模式、检索依据、识别疑似多选）→ 答错自动归档错题本并生成复习卡 |
| 错题本 | 拍题归档 / 手动录题 / 问答保存；AI 错因分析、关键词索引、详情页关联复习卡 |
| 今日复习 | SM-2 间隔调度；"忘了 / 有点模糊 / 记得牢"三档打分；**忘了的卡当天重现**直到记住；拍题来源的卡以**完整原题**为卡面 |
| 练习与规划 | **学习管家**（自主调度工具的智能体）、生成练习题、批改答案、基于真实错题数据的复习规划 |
| 学习数据看板 | 基于每次打分日志：14 天复习量与记住率、间隔保持率（近似遗忘曲线）、最常遗忘知识点、连续学习天数 |
| 搜索记录 | 问答 / 判卷 / 练习的历史服务端落库，支持关键词搜索、来源筛选、重新提问、删除 |

## 系统架构

```
┌──────────────────────────┐      ┌────────────────────────────────────────────┐
│   Vue 3 + Element Plus   │ HTTP │                FastAPI                      │
│   智能问答 / 拍题判卷     │─────▶│  routers: auth · chat(SSE) · ocr · pipeline │
│   错题本 / 今日复习       │ SSE  │           mistakes · review · agents        │
│   练习与规划 / 看板        │◀─────│           records · stats                  │
└──────────────────────────┘      │  services:                                  │
                                  │   ├ llm_service   SiliconFlow 统一出口       │
                                  │   ├ rag_service   切块索引 + 余弦检索         │
                                  │   ├ agents        讲解 / 出题 / 批改 / 规划   │
                                  │   ├ manager_agent function-calling 调度循环   │
                                  │   ├ card_agent    制卡                        │
                                  │   └ review_service SM-2                      │
                                  └────────┬──────────────┬────────────┬─────────┘
                                           │              │            │
                                  PostgreSQL (Alembic)  SiliconFlow   百度 OCR
                                  users/mistakes/        Qwen3-8B +
                                  review_cards/          bge-m3 嵌入
                                  review_logs/
                                  search_records
```

## 多智能体设计

系统里有两种形态的"多智能体"，答辩或面试时建议分开讲：

**1. 确定性流水线（`/api/pipeline/grade-archive`）**
`批改智能体（思考模式 + 知识库依据）→ 判断结果决定是否归档 → 归档（批改解析即错题分析）→ 制卡（拍题来源直接用原题做卡面）→ 进入今日复习队列`。上游输出是下游输入，控制流由代码编排。

**2. 自主调度智能体（学习管家，`services/manager_agent.py`）**
基于 OpenAI 兼容的 function calling：注册 4 个工具——`search_knowledge`（检索知识库）、`get_study_status`（查用户真实错题 / 复习数据）、`generate_quiz`（出题）、`create_review_card`（建卡）。模型自行决定调用哪些工具、按什么顺序，循环执行（上限 6 步）直到给出最终答复；接口返回答复 + 执行轨迹。例如"找出我最薄弱的考点，讲解后做成背诵卡"会自主走 查状态 → 检索 → 建卡 三步。

其余的出题 / 批改 / 规划智能体各自独立，出题内含"生成 → 程序校验 → 不合格才唤起包装智能体修复"的反馈环。

## 技术选型与取舍

| 决策 | 选择 | 为什么 |
|---|---|---|
| 知识库 | **自建轻量 RAG**（Markdown 切块 → bge-m3 嵌入 → JSON 索引 → 内存余弦检索） | 语料仅数百块，向量数据库 / RAGFlow 是过度设计；零部署、全免费，检索逻辑完全可控。规模上万块再迁 pgvector |
| 模型 | SiliconFlow 免费档 Qwen3-8B，批改 / 错题分析开思考模式，其余关闭 | 免费前提下质量最好；思考模式只用在准确率优先的场景，换取可接受的延迟 |
| 问答链路 | SSE 流式 | 8B 模型生成上千字需数十秒，流式把"白屏等 60s"变成"3s 出字"；单向推送用 SSE 比 WebSocket 更简单 |
| 间隔复习 | SM-2 | 经典、可解释、状态只有三个字段；失败卡当天重现是对原算法的产品化补充 |
| 数据库 | PostgreSQL + Alembic（测试用 SQLite） | 生产级、可迁移；SQLAlchemy 抽象方言，测试零依赖 |
| 密码 / 会话 | PBKDF2-SHA256（12 万次迭代 + 随机盐）、token 7 天过期 | 标准库即可实现，无额外依赖 |
| 提示词 | 骨架示范 + 占位符，而非描述式指令 | 中小模型会把引号里的"成分描述"当模板照抄（踩过坑：要点标题全变成"知识解释 + 答题角度"） |

## 快速开始

### 方式一：Docker Compose（推荐，一条命令）

```bash
cp backend/.env.example backend/.env   # 填入 SiliconFlow 与百度 OCR 密钥
docker compose up -d --build
# 前端 http://localhost:8080 ；API 调试 http://127.0.0.1:8003/docs
```

compose 会启动 PostgreSQL、后端（启动时自动 `alembic upgrade head`）、nginx 托管的前端（`/api` 反代到后端，流式已关闭缓冲）。

### 方式二：本地开发

```bash
# 1. 数据库（任选其一）
docker run -d --name politics-postgres -e POSTGRES_USER=politics -e POSTGRES_PASSWORD=politics_dev \
  -e POSTGRES_DB=politics -p 127.0.0.1:15434:5432 -v politics_pgdata:/var/lib/postgresql/data postgres:17
# 或在 .env 里用 SQLite：DATABASE_URL=sqlite:///./politics.db

# 2. 后端
cd backend
cp .env.example .env            # 填密钥；DATABASE_URL 默认指向上面的容器
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload

# 3. 前端
cd ../frontend
npm install
npx vite                         # http://localhost:5173，API 地址见 .env.development
```

### 环境变量（`backend/.env`）

| 变量 | 说明 |
|---|---|
| `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` | SiliconFlow 密钥与地址 |
| `SILICONFLOW_CHAT_MODEL` | 对话模型，默认 `Qwen/Qwen3-8B` |
| `SILICONFLOW_EMBED_MODEL` | 嵌入模型，默认 `BAAI/bge-m3` |
| `BAIDU_OCR_API_KEY` / `BAIDU_OCR_SECRET_KEY` | 百度通用文字识别 |
| `DATABASE_URL` | SQLAlchemy 连接串（PostgreSQL 或 SQLite） |
| `RAG_INDEX_PATH` | 向量索引文件，默认 `data/rag_index.json` |

## 知识库构建

语料放在 `backend/data/*.md`（用 `#` / `##` 标题分篇章），运行：

```bash
cd backend && .venv/bin/python scripts/build_rag_index.py
```

脚本按标题切块（700 字、120 字重叠），批量调用 bge-m3 生成向量，写入 `data/rag_index.json`。**扩充语料（真题解析、时政汇编）是提升回答深度和判题准确率最直接的杠杆**，当前语料只有一本基础预热宝典。

## 数据库与迁移

表：`users`、`mistakes`（含来源 / 学生答案 / 判定）、`review_cards`（SM-2 状态 + lapses）、`review_logs`（每次打分）、`search_records`。全部外键级联删除，高频查询建复合索引。

```bash
cd backend
.venv/bin/alembic upgrade head                          # 建表 / 升级
.venv/bin/alembic revision --autogenerate -m "说明"     # 改了 models.py 之后生成迁移
.venv/bin/python scripts/migrate_sqlite_to_pg.py ./politics.db   # 旧 SQLite 数据搬迁
```

## 测试

```bash
cd backend && .venv/bin/python -m pytest tests -q
```

19 个用例，使用独立 SQLite 文件库，不依赖大模型：SM-2 调度、判卷 / 制卡解析、记录接口、复习接口、学习看板聚合、鉴权与越权。

## API 一览

| 路径 | 说明 |
|---|---|
| `POST /api/auth/register` `login` `logout` `change-password` · `GET /api/auth/me` | 账号 |
| `POST /api/chat/ask-stream`（SSE）· `POST /api/chat/ask` | 知识库问答 |
| `POST /api/ocr/recognize` · `solve-raw-text` | OCR 识别 / 直接解析 |
| `POST /api/pipeline/grade-archive` | 判题 → 归档 → 制卡 流水线 |
| `GET/POST/DELETE /api/mistakes` · `GET/DELETE /api/mistakes/{id}` | 错题本 |
| `POST /api/review/generate-from-mistake/{id}` · `GET /api/review/today` · `POST /api/review/submit` | 复习 |
| `POST /api/agents/manager` · `quiz` · `grade` · `plan` · `explain` | 智能体 |
| `GET /api/records` · `DELETE /api/records[/{id}]` | 搜索记录 |
| `GET /api/stats/overview` · `charts` · `learning` | 统计与学习看板 |

完整交互文档：后端启动后访问 `/docs`。

## 目录结构

```
politics-review-system/
├─ docker-compose.yml
├─ backend/
│  ├─ app/
│  │  ├─ main.py  config.py  database.py  models.py
│  │  ├─ routers/   auth chat ocr pipeline mistakes review agents records stats
│  │  └─ services/  llm_service rag_service agents manager_agent card_agent
│  │                mistake_agent review_service ocr_service text_clean_service prompt_rules
│  ├─ alembic/          迁移脚本
│  ├─ data/             语料 *.md 与 rag_index.json
│  ├─ scripts/          build_rag_index / migrate_sqlite_to_pg / 数据修复脚本
│  ├─ tests/            pytest
│  ├─ Dockerfile  requirements.txt  .env.example
└─ frontend/
   ├─ src/views/   Dashboard Chat OcrUpload Mistakes MistakeDetail Review Agents Records Settings Login Register
   ├─ src/api/request.js  src/utils/markdown.js  src/router
   ├─ Dockerfile  nginx.conf  vite.config.js
```

## 已知限制与路线图

- 免费 8B 模型在**知识库未覆盖**的时政题上仍可能自信地判错；缓解手段是扩充语料（已支持多文件）。
- 问答暂为单轮，无会话记忆。
- 测试覆盖非 LLM 逻辑；LLM 边界的 mock 测试、RAG 评测集（量化有 / 无检索的准确率）在计划中。
- 尚无按用户限流与 LLM 调用耗时 / token 统计。

---

本项目由课程小组项目重构而来，重构记录见 [`修改说明.md`](./修改说明.md)。

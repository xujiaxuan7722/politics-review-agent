# 知识库语料目录

把考研政治资料整理成 Markdown 放在本目录（可多份），用 `#` 表示篇、`##` 表示章，正文按段落书写。例如：

```
# 第一篇 马克思主义基本原理
## 第四章 唯物辩证法
矛盾的普遍性是指……
```

然后在 `backend/` 目录执行：

```bash
.venv/bin/python scripts/build_rag_index.py
```

脚本会把所有 `*.md` 切块（约 700 字、120 字重叠）、调用 SiliconFlow 的 `BAAI/bge-m3` 生成向量，写入 `rag_index.json`。语料文件与索引均已在 `.gitignore` 中（教材文本涉及版权，请勿提交到公开仓库）。

"""把 data/ 目录下所有 .md 语料切块并调用嵌入模型，生成 data/rag_index.json。

扩充知识库：把新的 Markdown 资料（教材、时政汇编、真题解析等，用 # / ## 标题分篇章）
放进 backend/data/ 后重新运行即可：
    .venv/bin/python scripts/build_rag_index.py
"""

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDEX_PATH = Path(__file__).resolve().parent.parent / settings.rag_index_path

CHUNK_SIZE = 700
CHUNK_OVERLAP = 120
EMBED_BATCH = 32


def split_corpus(text: str) -> list[dict]:
    chunks = []
    part = ""
    chapter = ""
    buffer: list[str] = []

    def flush():
        content = "\n".join(buffer).strip()
        buffer.clear()
        if not content:
            return
        heading = "，".join(item for item in (part, chapter) if item)
        for piece in split_long_text(content):
            chunks.append({"heading": heading, "text": piece})

    for line in text.splitlines():
        h1 = re.match(r"^#\s+(.+)$", line)
        h2 = re.match(r"^##\s+(.+)$", line)
        if h1:
            flush()
            part = h1.group(1).strip()
            chapter = ""
            continue
        if h2:
            flush()
            chapter = h2.group(1).strip()
            continue
        buffer.append(line)

    flush()
    return chunks


def split_long_text(content: str) -> list[str]:
    if len(content) <= CHUNK_SIZE:
        return [content]

    pieces = []
    start = 0
    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))
        if end < len(content):
            # 尽量在句号处断开
            cut = content.rfind("。", start + CHUNK_SIZE // 2, end)
            if cut != -1:
                end = cut + 1
        pieces.append(content[start:end].strip())
        if end >= len(content):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [piece for piece in pieces if len(piece) >= 30]


def embed_batch(client: httpx.Client, texts: list[str]) -> list[list[float]]:
    resp = client.post(
        f"{settings.embed_endpoint.base_url}/embeddings",
        headers={"Authorization": f"Bearer {settings.embed_endpoint.api_key}"},
        json={"model": settings.embed_endpoint.model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda item: item["index"])
    return [item["embedding"] for item in data]


def main():
    corpus_files = sorted(DATA_DIR.glob("*.md"))
    if not corpus_files:
        raise SystemExit(f"{DATA_DIR} 下没有 .md 语料文件")

    chunks = []
    for path in corpus_files:
        file_chunks = split_corpus(path.read_text(encoding="utf-8"))
        for chunk in file_chunks:
            chunk["heading"] = f"{path.stem}，{chunk['heading']}" if chunk["heading"] else path.stem
        chunks.extend(file_chunks)
        print(f"{path.name}: {len(file_chunks)} 块")
    print(f"切块完成：共 {len(chunks)} 块（{len(corpus_files)} 个文件）")

    vectors: list[list[float]] = []
    with httpx.Client(trust_env=False) as client:
        for i in range(0, len(chunks), EMBED_BATCH):
            batch = [f"{c['heading']}\n{c['text']}" if c["heading"] else c["text"] for c in chunks[i:i + EMBED_BATCH]]
            vectors.extend(embed_batch(client, batch))
            print(f"已嵌入 {min(i + EMBED_BATCH, len(chunks))}/{len(chunks)}")

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(
            {
                "model": settings.embed_endpoint.model,
                "chunks": chunks,
                "vectors": [[round(v, 6) for v in vec] for vec in vectors],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"索引已写入 {INDEX_PATH}（{INDEX_PATH.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()

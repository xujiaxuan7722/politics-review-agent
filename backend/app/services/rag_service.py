"""内置知识库检索：加载预构建的向量索引，对用户问题做余弦相似度召回。"""

import json
import math
from pathlib import Path

import httpx

from app.config import settings

_INDEX_PATH = Path(__file__).resolve().parent.parent.parent / settings.rag_index_path
_index_cache: dict | None = None


def _load_index() -> dict | None:
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    if not _INDEX_PATH.exists():
        return None

    raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    norms = [math.sqrt(sum(v * v for v in vec)) or 1.0 for vec in raw["vectors"]]
    _index_cache = {"chunks": raw["chunks"], "vectors": raw["vectors"], "norms": norms}
    return _index_cache


def index_available() -> bool:
    return _load_index() is not None


async def embed_query(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.siliconflow_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.siliconflow_api_key}"},
            json={"model": settings.siliconflow_embed_model, "input": [text]},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def retrieve(question: str, top_k: int = 5, min_score: float = 0.35) -> list[dict]:
    index = _load_index()
    if not index:
        return []

    query_vec = await embed_query(question)
    query_norm = math.sqrt(sum(v * v for v in query_vec)) or 1.0

    scored = []
    for i, vec in enumerate(index["vectors"]):
        dot = sum(a * b for a, b in zip(query_vec, vec))
        score = dot / (query_norm * index["norms"][i])
        scored.append((score, i))

    scored.sort(reverse=True)
    results = []
    for score, i in scored[:top_k]:
        if score < min_score:
            continue
        chunk = index["chunks"][i]
        results.append({
            "heading": chunk.get("heading", ""),
            "text": chunk["text"],
            "score": round(score, 4),
        })
    return results


def build_context(passages: list[dict], max_chars: int = 3600) -> str:
    parts = []
    used = 0
    for idx, item in enumerate(passages, start=1):
        block = f"【资料{idx}】{item['heading']}\n{item['text']}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)

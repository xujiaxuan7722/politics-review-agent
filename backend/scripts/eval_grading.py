"""判卷准确率评测：对指定端点/模型跑一遍评测集，输出准确率并落盘。

用法（在 backend/ 下）：
  .venv/bin/python scripts/eval_grading.py --group grader            # 用 .env 里判卷组的模型
  .venv/bin/python scripts/eval_grading.py --group chat --thinking    # 通用对话组 + 开思考
  .venv/bin/python scripts/eval_grading.py --group base               # 基础组（SiliconFlow）
  .venv/bin/python scripts/eval_grading.py --provider sensenova --base-url https://token.sensenova.cn/v1 \
        --api-key-env CHAT_API_KEY --model kimi-k3
  .venv/bin/python scripts/eval_grading.py --report                   # 汇总 results/ 下所有结果
结果写到 evals/grading/results/<label>-<时间>.json，可反复跑做前后对比。
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import LLMEndpoint, settings  # noqa: E402
from app.services.agents import grader_agent  # noqa: E402
from app.services.grading_eval import load_questions, score_item, summarize  # noqa: E402

QUESTIONS = ROOT / "evals" / "grading" / "questions.jsonl"
RESULTS = ROOT / "evals" / "grading" / "results"


def pick_endpoint(args) -> LLMEndpoint:
    if args.model:
        key = os.environ.get(args.api_key_env or "", "") or settings.chat_endpoint.api_key
        return LLMEndpoint(
            provider=(args.provider or settings.chat_endpoint.provider).lower(),
            base_url=(args.base_url or settings.chat_endpoint.base_url).rstrip("/"),
            api_key=key,
            model=args.model,
        )
    return {"grader": settings.grader_endpoint, "chat": settings.chat_endpoint, "base": settings.base_chat_endpoint}[args.group]


async def grade_with_backoff(item: dict, endpoint: LLMEndpoint, thinking: bool, max_attempts: int) -> str:
    """评测只重试不降级：共享池限流（429）时按 10s、20s、40s… 退避，直到拿到指定模型的结果。"""
    delay = 10.0
    for attempt in range(1, max_attempts + 1):
        try:
            return await grader_agent(
                item["question"], item["student_answer"],
                endpoint=endpoint, thinking=thinking, allow_fallback=False,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == max_attempts:
                raise
        except httpx.TransportError:
            if attempt == max_attempts:
                raise
        wait = delay + random.uniform(0, 3)
        print(f"    · {item['id']} 限流/超时，{wait:.0f}s 后第 {attempt + 1} 次", flush=True)
        await asyncio.sleep(wait)
        delay = min(delay * 2, 90.0)
    raise RuntimeError("unreachable")


async def run(args) -> Path:
    endpoint = pick_endpoint(args)
    thinking = bool(args.thinking)
    items = load_questions(QUESTIONS)
    if args.limit:
        items = items[: args.limit]
    label = f"{endpoint.label.replace('/', '_')}{'-think' if thinking else ''}"
    print(f"评测 {label}，{len(items)} 题，并发 {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = [None] * len(items)  # type: ignore[list-item]

    async def one(i: int, item: dict) -> None:
        async with sem:
            t0 = time.perf_counter()
            try:
                output = await grade_with_backoff(item, endpoint, thinking, args.max_attempts)
                row = score_item(item, output)
                row["latency_s"] = round(time.perf_counter() - t0, 2)
                row["output"] = output
            except Exception as exc:
                row = {"id": item["id"], "module": item["module"], "error": f"{type(exc).__name__}: {exc}"[:200]}
            rows[i] = row
            mark = "✓" if row.get("both_ok") else ("!" if row.get("error") else "✗")
            print(f"  {mark} {item['id']} 判={row.get('judged')}/{row.get('expected_judgement')} 答={row.get('answered')}/{item['answer']} {row.get('latency_s', '')}s", flush=True)

    await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
    summary = summarize(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{label}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({
        "label": label, "endpoint": endpoint.label, "thinking": thinking, "questions": str(QUESTIONS.name),
        "run_at": datetime.now().isoformat(timespec="seconds"), "summary": summary, "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(label, summary)
    print(f"结果：{out.relative_to(ROOT)}")
    return out


def print_summary(label: str, s: dict) -> None:
    print(f"\n{label}: 判到 {s['graded']}/{s['n']} 题 | 判断准确率 {s['judgement_acc']}% | 答案准确率 {s['answer_acc']}% | 双对 {s['both_acc']}% "
          f"| 失败 {s['errors']} | 未解析 判{s['unparsed_judgement']}/答{s['unparsed_answer']} | 延迟 p50 {s['latency_p50_s']}s max {s['latency_max_s']}s")
    for mod, m in s["by_module"].items():
        print(f"    {mod}: {m['both_ok']}/{m['n']} 双对（判 {m['judgement_ok']} 答 {m['answer_ok']}）")


def report() -> None:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        print("results/ 下还没有结果")
        return
    print(f"{'标签':<40}{'判到/总':>9}{'判断%':>8}{'答案%':>8}{'双对%':>8}{'p50s':>7}  时间")
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        s = d["summary"]
        graded = s.get("graded", s["n"] - s["errors"])
        print(f"{d['label']:<40}{f'{graded}/{s[chr(110)]}':>9}{str(s['judgement_acc']):>8}{str(s['answer_acc']):>8}{str(s['both_acc']):>8}{str(s['latency_p50_s']):>7}  {d['run_at']}")


def rescore(paths: list[str]) -> None:
    """用当前解析器对已保存的模型输出重新打分（不再调模型），解析器改了以后跑一遍。"""
    items = {it["id"]: it for it in load_questions(QUESTIONS)}
    for path in paths:
        f = Path(path)
        d = json.loads(f.read_text(encoding="utf-8"))
        rows = []
        for r in d["rows"]:
            if r.get("error") or "output" not in r:
                rows.append(r)
                continue
            new = score_item(items[r["id"]], r["output"])
            new["latency_s"] = r.get("latency_s")
            new["output"] = r["output"]
            rows.append(new)
        d["rows"] = rows
        d["summary"] = summarize(rows)
        d["rescored_at"] = datetime.now().isoformat(timespec="seconds")
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print_summary(d["label"], d["summary"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--group", choices=["grader", "chat", "base"], default="grader")
    ap.add_argument("--provider")
    ap.add_argument("--base-url")
    ap.add_argument("--api-key-env", help="从哪个环境变量读密钥；缺省用通用对话组的密钥")
    ap.add_argument("--model")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-attempts", type=int, default=6, help="单题限流/超时最多尝试次数（退避 10s 起倍增）")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--rescore", nargs="+", metavar="RESULT_JSON", help="用当前解析器重算已保存结果")
    args = ap.parse_args()
    if args.report:
        report()
        return
    if args.rescore:
        rescore(args.rescore)
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

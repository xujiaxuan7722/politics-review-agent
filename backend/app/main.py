from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import agents, auth, chat, mistakes, ocr, pipeline, records, review, stats

# 表结构由 Alembic 迁移管理：alembic upgrade head

app = FastAPI(title="政治抗遗忘复习系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(ocr.router)
app.include_router(mistakes.router)
app.include_router(pipeline.router)
app.include_router(records.router)
app.include_router(review.router)
app.include_router(stats.router)
app.include_router(agents.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "message": "后端启动成功",
    }

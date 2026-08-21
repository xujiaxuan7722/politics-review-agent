"""把旧 SQLite 库（politics.db）里的数据搬到 .env 指定的 PostgreSQL。

用法（backend 目录下，先 alembic upgrade head 建好表）：
    .venv/bin/python scripts/migrate_sqlite_to_pg.py [旧库路径，默认 ./politics.db]
只复制两边都有的列，新列用默认值；保留原 id 并重置自增序列。
"""

import sys
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, insert, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401

TABLE_ORDER = ["users", "mistakes", "review_cards", "review_logs", "search_records"]


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else "./politics.db"
    if not Path(src_path).exists():
        print(f"旧库不存在：{src_path}，无需迁移")
        return

    src = create_engine(f"sqlite:///{src_path}")
    dst = create_engine(settings.database_url)
    src_meta = MetaData()
    src_meta.reflect(bind=src)

    with src.connect() as sconn, dst.begin() as dconn:
        for name in TABLE_ORDER:
            if name not in src_meta.tables or name not in Base.metadata.tables:
                continue
            src_table: Table = src_meta.tables[name]
            dst_table: Table = Base.metadata.tables[name]
            common = [c.name for c in src_table.columns if c.name in dst_table.columns]

            rows = sconn.execute(select(*[src_table.c[c] for c in common])).mappings().all()
            if not rows:
                print(f"{name}: 0 行")
                continue

            payload = []
            for row in rows:
                item = dict(row)
                # 旧库里 user_id 为空的孤儿数据无法满足外键，跳过
                if "user_id" in item and item["user_id"] is None:
                    continue
                payload.append(item)

            if payload:
                dconn.execute(insert(dst_table), payload)
            if settings.database_url.startswith("postgresql"):
                dconn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {name}), 1))"
                    )
                )
            print(f"{name}: 迁移 {len(payload)} 行（跳过 {len(rows) - len(payload)} 行孤儿数据）")

    print("迁移完成")


if __name__ == "__main__":
    main()

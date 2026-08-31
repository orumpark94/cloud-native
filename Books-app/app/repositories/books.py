"""books 테이블 조회.

03-data-model.md 의 스키마를 쓴다
  books(id, title, price, stock)

여기서 재는 지표
  db_query_duration_seconds{query, offset_bucket}

offset_bucket 을 라벨로 두는 이유                        ★ 01·05 문서
  OFFSET 이 커지면 DB 가 앞의 행을 세면서 지나간다 → 느려진다
    OFFSET 40      앞의 40행을 읽고 버린다      빠르다
    OFFSET 50000   앞의 5만 행을 읽고 버린다     느리다

  이걸 지표로 보려면 라벨이 필요한데 offset 값은 무한하다
  → 구간으로 묶으면 유한해진다

  6단계에서 "왜 특정 요청만 느린가" 를 이 라벨로 찾는다
  → /debug/slow-query 같은 인위적 지연이 아니라 진짜 원인이 있는 지연이다
"""

from __future__ import annotations

import time
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app import metrics


async def list_books(
    conn: AsyncConnection,
    *,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """책 목록과 전체 개수를 돌려준다.

    COUNT(*) 를 매번 하는 것에 대해                       (01 문서)
      전체를 세므로 행이 많으면 이것만으로도 느리다
      실무 해법은 대략치 / 캐시 / 아예 안 주기다
      → 지금은 그냥 센다. 느려지는 걸 본 뒤에 판단한다
      → "미리 최적화하지 않는다"
    """
    bucket = metrics.offset_bucket(offset)

    started = time.perf_counter()
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, title, price, stock
              FROM books
             ORDER BY id
             LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        rows = await cur.fetchall()
    metrics.db_query_duration_seconds.labels(
        query="list_books", offset_bucket=bucket
    ).observe(time.perf_counter() - started)

    started = time.perf_counter()
    async with conn.cursor() as cur:
        await cur.execute("SELECT COUNT(*) FROM books")
        row = await cur.fetchone()
        total = int(row[0]) if row else 0
    metrics.db_query_duration_seconds.labels(
        query="count_books", offset_bucket="n/a"
    ).observe(time.perf_counter() - started)

    return [dict(r) for r in rows], total


async def get_book(conn: AsyncConnection, book_id: int) -> dict[str, Any] | None:
    started = time.perf_counter()
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, title, price, stock FROM books WHERE id = %s",
            (book_id,),
        )
        row = await cur.fetchone()
    metrics.db_query_duration_seconds.labels(
        query="get_book", offset_bucket="n/a"
    ).observe(time.perf_counter() - started)

    return dict(row) if row else None

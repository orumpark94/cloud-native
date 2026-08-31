"""경로 1 — 읽기.

00-architecture.md 의 흐름

  1. Redis 에서 찾는다
  2. 있으면 그대로 준다                    캐시 적중
  3. 없으면 PostgreSQL 에서 읽는다          캐시 미스
  4. Redis 에 저장한다 (실패해도 무시)
  5. 준다

이 경로가 특별한 이유
  DB 가 죽어도 캐시에 있으면 응답할 수 있다
  → 04 문서에서 readiness 에 DB 를 안 넣기로 한 근거가 이것이다
  → 부분 장애를 전체 장애로 만들지 않는다
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request

from app import cache as cache_keys
from app.db import acquire, maybe_slow_query
from app.errors import AppError, ErrorCode
from app.repositories import books as books_repo

router = APIRouter(tags=["books"])


def _ctx(request: Request):
    """요청에서 필요한 것들을 꺼낸다."""
    app_state = request.app.state
    return app_state.runtime, app_state.cache, app_state.faults


async def _apply_request_faults(faults) -> None:
    """확률적 장애 주입.                                  ★ 06 문서

    지연과 에러를 라우터 층에서 넣는 이유
      미들웨어에 넣으면 /health 까지 느려진다
      → probe 가 실패해서 Pod 가 빠진다 → 실험이 오염된다
      → 실제 서비스 경로에만 적용한다
    """
    delay = faults.should_inject_latency()
    if delay > 0:
        await asyncio.sleep(delay)

    if faults.should_inject_error():
        raise AppError(
            ErrorCode.INJECTED_ERROR,
            status_code=faults.error_status(),
            detail={"injected": True},
        )


@router.get("/books")
async def list_books(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    """책 목록.

    limit 을 100 으로 제한하는 이유
      제한이 없으면 limit=1000000 요청 하나가 DB 를 마비시킨다
      → 밖에서 들어오는 값에는 항상 상한을 둔다

    offset 에는 상한을 두지 않는다
      큰 offset 이 느려지는 걸 6단계에서 관찰해야 하기 때문이다 (01 문서)
      → 실무라면 커서 기반으로 바꾸거나 상한을 둔다
    """
    runtime, cache, faults = _ctx(request)
    await _apply_request_faults(faults)

    key = cache_keys.book_list_key(limit, offset)

    cached = await cache.get_json(key)
    if cached is not None:
        return cached

    async with acquire(runtime.deps) as conn:
        # 느린 쿼리 주입. 커넥션을 실제로 붙잡는다 (06 문서)
        await maybe_slow_query(conn, faults.db_slow_seconds())
        rows, total = await books_repo.list_books(conn, limit=limit, offset=offset)

    payload = {
        "items": rows,
        "limit": limit,
        "offset": offset,
        "total": total,
    }

    # 캐시 저장이 실패해도 응답은 정상이다 (cache.py 가 삼킨다)
    await cache.set_json(key, payload)
    return payload


@router.get("/books/{book_id}")
async def get_book(request: Request, book_id: int) -> dict:
    runtime, cache, faults = _ctx(request)
    await _apply_request_faults(faults)

    key = cache_keys.book_key(book_id)

    cached = await cache.get_json(key)
    if cached is not None:
        return cached

    async with acquire(runtime.deps) as conn:
        await maybe_slow_query(conn, faults.db_slow_seconds())
        book = await books_repo.get_book(conn, book_id)

    if book is None:
        # 없는 책은 캐시하지 않는다                        (01 문서)
        #   계속 요청이 오면 매번 DB 를 친다 (cache penetration)
        #   → 구현이 복잡해지고 학습 대상이 아니라 지금은 안 한다
        #   → 6단계에서 없는 ID 로 부하를 넣어 DB 부하가 오르는 걸 본 뒤 판단한다
        raise AppError(ErrorCode.BOOK_NOT_FOUND, detail={"book_id": book_id})

    await cache.set_json(key, book)
    return book

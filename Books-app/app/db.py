"""DB 커넥션을 빌려주는 곳.

왜 풀에서 직접 안 꺼내고 이 파일을 거치는가
  커넥션을 얻는 지점이 한 곳이어야 계측이 한 곳에 모인다
  → db_pool_wait_seconds 를 여기서만 재면 된다
  → 라우터마다 재면 빠뜨리는 곳이 생긴다

여기서 재는 값이 왜 중요한가                             ★ 05·06 문서
  연쇄 장애의 순서
    Redis 죽음 → 캐시 미스 → DB 로 몰림 → 풀 고갈
    → db_pool_wait_seconds 가 먼저 오른다
    → 그다음 503 이 나온다
  즉 503 보다 먼저 경고할 수 있는 지표다
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg import AsyncConnection

from app import metrics
from app.deps import Dependencies
from app.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)


@asynccontextmanager
async def acquire(deps: Dependencies) -> AsyncIterator[AsyncConnection]:
    """커넥션을 빌린다. 실패하면 AppError(DB_UNAVAILABLE) 로 바꿔 던진다.

    왜 예외를 바꾸는가
      psycopg 예외가 라우터까지 올라오면 라우터마다 잡아야 한다
      → 여기서 우리 예외로 바꾸면 처리기 한 곳에서 응답과 지표를 처리한다
      → 에러 코드가 저절로 통일된다 (errors.py 의 목록)
    """
    started = time.perf_counter()
    try:
        async with deps.pool.connection() as conn:
            # 커넥션을 얻는 데 걸린 시간.
            # 풀이 비어 있으면 여기서 기다린다 → 그 시간이 잡힌다
            waited = time.perf_counter() - started
            metrics.db_pool_wait_seconds.observe(waited)
            _publish_pool_stats(deps)

            if waited > 1.0:
                # 1초 넘게 기다렸으면 풀이 마르고 있다는 뜻이다
                # 지표로도 보이지만 로그에 남겨두면 사후 조사가 쉽다
                logger.warning(
                    "커넥션 대기가 길다",
                    extra={"ctx_wait_seconds": round(waited, 3)},
                )

            deps.postgres.mark_up()
            yield conn
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        deps.postgres.mark_down(exc)
        metrics.dependency_errors_total.labels(
            name="postgres",
            kind=metrics.classify_dependency_error(exc),
        ).inc()
        metrics.set_dependency_up("postgres", False)
        _publish_pool_stats(deps)
        logger.error(
            "DB 커넥션 획득 실패",
            extra={"ctx_error": str(exc), "ctx_error_type": type(exc).__name__},
        )
        raise AppError(ErrorCode.DB_UNAVAILABLE) from exc


def _publish_pool_stats(deps: Dependencies) -> None:
    stats = deps.pool_stats()
    metrics.set_pool_stats(
        size=stats["size"],
        available=stats["available"],
        waiting=stats["waiting"],
    )


async def maybe_slow_query(conn: AsyncConnection, seconds: float) -> None:
    """느린 쿼리를 흉내낸다.                              ★ 06 문서

    앱에서 asyncio.sleep 을 하지 않고 pg_sleep 을 쓰는 이유
      sleep 은 커넥션을 안 잡는다 → 풀이 안 찬다
      pg_sleep 은 커넥션을 실제로 붙잡는다
      → "DB 가 느려서 풀이 고갈되는" 연쇄를 재현할 수 있다
    """
    if seconds <= 0:
        return
    async with conn.cursor() as cur:
        await cur.execute("SELECT pg_sleep(%s)", (seconds,))


def wrap_db_error(exc: Exception) -> AppError:
    """psycopg 예외를 우리 에러로 바꾼다.

    무결성 위반 같은 건 우리 잘못(500)이고
    연결 문제는 밖의 문제(503)다. 나눠야 원인 판단이 빨라진다 (01 문서)
    """
    if isinstance(exc, psycopg.OperationalError):
        return AppError(ErrorCode.DB_UNAVAILABLE)
    if isinstance(exc, psycopg.errors.IntegrityError):
        return AppError(
            ErrorCode.INVALID_REQUEST,
            message="데이터 제약 조건을 위반했습니다",
        )
    return AppError(ErrorCode.INTERNAL_ERROR)

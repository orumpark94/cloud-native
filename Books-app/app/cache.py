"""Redis 캐시.

이 파일의 원칙 한 줄
  실패해도 예외를 던지지 않는다. 대신 반드시 지표로 남긴다

왜 예외를 안 던지는가                                    ★ 00·11 문서
  캐시는 "있으면 좋은 것" 이다
  없으면 느려질 뿐 동작은 해야 한다
  → 예외를 던지면 캐시 장애가 조회 장애가 된다
  → 부분 장애를 전체 장애로 만드는 셈이다

왜 지표는 반드시 남기는가
  "무시한다" 와 "모른 척한다" 는 다르다
  기록이 없으면 "왜 갑자기 느려졌나" 에 답할 수 없다
  → 2단계 내내 겪은 조용한 실패가 그것이다

연쇄 장애가 여기서 시작된다
  Redis 죽음 → cache miss 급증 → DB 로 몰림 → 풀 고갈 → 503
  → 이 파일의 지표가 그 사슬의 첫 칸이다
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app import metrics
from app.deps import Dependencies
from app.faults import FaultRegistry

logger = logging.getLogger(__name__)


class InjectedRedisFailure(Exception):
    """장애 주입으로 만든 Redis 실패. 진짜 장애와 구분한다."""


class Cache:
    def __init__(
        self,
        deps: Dependencies,
        faults: FaultRegistry,
        *,
        ttl_seconds: int,
    ) -> None:
        self.deps = deps
        self.faults = faults
        self.ttl_seconds = ttl_seconds

    # ── 주입된 장애 흉내 ────────────────────────────────

    async def _apply_fault(self) -> None:
        """06 문서의 break-redis 를 여기서 흉내낸다.

        mode 를 둘로 나눈 이유
          error  즉시 실패한다 → 캐시 미스로 DB 로 간다
          slow   응답이 느리다  → 캐시를 기다리다 전체가 느려진다

        slow 가 더 위험하다
          죽으면 바로 우회하는데, 느리면 계속 붙잡힌다
          → "죽은 것보다 느린 것이 더 나쁘다" 를 직접 만들어본다
        """
        mode = self.faults.redis_mode()
        if mode is None:
            return
        if mode == "slow":
            await asyncio.sleep(self.faults.redis_slow_seconds())
            return
        raise InjectedRedisFailure("break-redis 주입")

    # ── 조회 ────────────────────────────────────────────

    async def get_json(self, key: str) -> Any | None:
        """캐시에서 읽는다. 없거나 실패하면 None.

        호출하는 쪽은 None 하나만 보면 된다
          → "미스인지 장애인지" 를 라우터가 신경 쓰지 않게 한다
          → 그 구분은 지표에 남긴다
        """
        started = time.perf_counter()
        try:
            await self._apply_fault()
            raw = await self.deps.redis_client.get(key)
        except Exception as exc:  # noqa: BLE001
            self._record_error("get", exc, started)
            return None

        metrics.cache_operation_duration_seconds.labels(operation="get").observe(
            time.perf_counter() - started
        )
        self.deps.redis.mark_up()
        metrics.set_dependency_up("redis", True)

        if raw is None:
            metrics.cache_operations_total.labels(result="miss").inc()
            return None

        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # 캐시에 깨진 값이 들어 있다. 미스로 처리하고 지운다
            #   → 형식이 바뀐 배포 직후에 생길 수 있다
            metrics.cache_operations_total.labels(result="error").inc()
            logger.warning("캐시 값이 깨졌다", extra={"ctx_cache_key": key})
            await self.delete(key)
            return None

        metrics.cache_operations_total.labels(result="hit").inc()
        return value

    async def set_json(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        """캐시에 쓴다. 실패하면 조용히 넘어간다(지표는 남긴다).

        쓰기 실패는 조회에 영향이 없다
          다음 요청이 다시 DB 를 읽을 뿐이다
        """
        started = time.perf_counter()
        try:
            await self._apply_fault()
            await self.deps.redis_client.set(
                key,
                json.dumps(value, ensure_ascii=False, default=str),
                ex=ttl if ttl is not None else self.ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_error("set", exc, started)
            return

        metrics.cache_operation_duration_seconds.labels(operation="set").observe(
            time.perf_counter() - started
        )

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            await self.deps.redis_client.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            self._record_error("delete", exc, time.perf_counter())

    # ── 오류 기록 ───────────────────────────────────────

    def _record_error(self, operation: str, exc: BaseException, started: float) -> None:
        """실패를 지표와 로그에 남긴다.

        result="error" 를 miss 와 따로 세는 이유
          hit/miss 만 보면 "Redis 는 살아 있는데 느리거나 일부 실패" 를 못 본다
          → 적중률만 보면 정상으로 보인다
        """
        metrics.cache_operations_total.labels(result="error").inc()
        metrics.cache_operation_duration_seconds.labels(operation=operation).observe(
            time.perf_counter() - started
        )

        if isinstance(exc, InjectedRedisFailure):
            # 주입한 장애는 의존성 상태를 바꾸지 않는다
            #   → Redis 는 실제로 멀쩡하다. dependency_up 을 0으로 만들면 거짓말이다
            return

        self.deps.redis.mark_down(exc)
        metrics.set_dependency_up("redis", False)
        metrics.dependency_errors_total.labels(
            name="redis",
            kind=metrics.classify_dependency_error(exc),
        ).inc()
        logger.warning(
            "캐시 작업 실패",
            extra={
                "ctx_operation": operation,
                "ctx_error": str(exc),
                "ctx_error_type": type(exc).__name__,
            },
        )


# ── 키 규칙 ─────────────────────────────────────────────
#
# 한 곳에 모아두는 이유
#   키를 여기저기서 문자열로 만들면 오타가 나도 모른다
#   → 캐시가 영원히 미스인데 아무도 모르는 상황이 된다
#   → 그것도 조용한 실패다


def book_key(book_id: int) -> str:
    return f"book:{book_id}"


def book_list_key(limit: int, offset: int) -> str:
    return f"books:list:{limit}:{offset}"

"""의존 서비스(PostgreSQL, Redis) 연결을 관리한다.

이 파일이 하는 일
  1. 기동할 때 연결을 시도한다. 실패하면 지수 백오프로 재시도한다
  2. "한 번이라도 성공했는가" 를 기록한다  → 04 문서의 readiness 판단 기준
  3. 지금 살아 있는지를 지표로 노출한다     → 05 문서의 dependency_up
  4. 종료할 때 연결을 정리한다

여기서 지키는 설계 판단

  [02 문서] 연결 실패 시 지수 백오프로 재시도한다
            RDS 페일오버는 30초~2분 걸린다. 한 번 실패로 포기하면 안 된다

  [04 문서] "한 번이라도 초기화에 성공했는가" → readiness 에 넣는다
            "지금 이 순간 DB 가 살아 있는가"  → 넣지 않는다
            → 그래서 initialized 와 up 을 따로 둔다

  [00 문서] Redis 가 죽어도 조회는 계속된다
            → Redis 실패는 예외를 던지지 않고 None 을 돌려준다
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import psycopg
import redis.asyncio as redis_async
from psycopg_pool import AsyncConnectionPool

from app import metrics
from app.config import Settings, redact_url

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 의존 서비스의 상태
#
# 왜 "초기화 성공" 과 "지금 살아있음" 을 나누는가          ★ 04 문서
#
#   initialized   한 번이라도 붙었나
#                 → readiness 에 넣는다
#                 → 기동 실패는 "이 Pod 만의 문제" 이므로 배포를 멈춰야 한다
#
#   up            지금 이 순간 붙어 있나
#                 → readiness 에 넣지 않는다
#                 → DB 가 죽으면 모든 Pod 가 같이 죽는다. 빼봐야 갈 곳이 없다
#                 → 대신 지표로 내보내 알람을 건다
# ─────────────────────────────────────────────────────────────


@dataclass
class DependencyState:
    name: str
    initialized: bool = False
    up: bool = False
    last_error: str | None = None
    last_change: float = field(default_factory=time.time)

    def mark_up(self) -> None:
        if not self.up:
            logger.info(
                "의존 서비스 연결 회복",
                extra={"ctx_dependency": self.name},
            )
        self.up = True
        self.initialized = True
        self.last_error = None
        self.last_change = time.time()

    def mark_down(self, error: BaseException | str) -> None:
        message = str(error)
        if self.up or self.last_error != message:
            logger.warning(
                "의존 서비스 연결 실패",
                extra={"ctx_dependency": self.name, "ctx_error": message},
            )
        self.up = False
        self.last_error = message
        self.last_change = time.time()


class Dependencies:
    """DB 와 Redis 연결을 들고 있는 객체.

    앱 전체에서 하나만 만든다. main.py 가 만들어 app.state 에 넣는다.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.postgres = DependencyState("postgres")
        self.redis = DependencyState("redis")

        self._pool: AsyncConnectionPool | None = None

        # ★★ Redis 클라이언트를 둘 둔다                    (2026-08-26 수정)
        #
        # 왜 하나로는 안 되는가
        #   두 경로가 타임아웃에 대해 정반대를 원한다
        #
        #   [캐시 경로]   빨리 포기해야 한다
        #     Redis 가 먹통일 때 오래 기다리면 사용자 요청이 멈춘다
        #     → 3초 안에 포기하고 DB 로 간다 (00 문서의 경로 1)
        #
        #   [큐 대기]     오래 기다리는 게 정상이다
        #     BRPOP 은 값이 올 때까지 블록한다
        #     그동안 서버는 아무것도 안 보낸다
        #     → 소켓이 먼저 끊기면 "정상 대기" 가 "장애" 로 둔갑한다
        #
        # 하나로 두고 3초를 쓰면 Worker 가 큐를 영원히 못 읽는다
        # 하나로 두고 8초를 쓰면 캐시 조회가 8초 멈춘다
        # → 나눈다
        self._redis: redis_async.Redis | None = None            # 빠른 명령용
        self._redis_blocking: redis_async.Redis | None = None   # BRPOP 전용

    # ── 기동 ────────────────────────────────────────────

    async def startup(self) -> None:
        """연결을 만들고 첫 확인까지 한다.

        여기서 실패해도 예외를 던지지 않는다.
          던지면 앱이 안 뜬다 → 왜 안 뜨는지 로그도 못 남긴다
          대신 initialized 를 false 로 두면 readiness 가 실패한다
          → 04 문서: "기동 실패는 readiness 에 넣는다"
        """
        logger.info(
            "의존 서비스 연결 시작",
            extra={
                "ctx_database_url": redact_url(self.settings.database_url),
                "ctx_redis_url": redact_url(self.settings.redis_url),
            },
        )

        # 커넥션 풀을 만든다. open=False 로 두고 우리가 직접 연다
        #   → 열리는 시점을 우리가 통제해야 재시도 로직을 붙일 수 있다
        self._pool = AsyncConnectionPool(
            conninfo=self.settings.database_url,
            min_size=self.settings.db_pool_min,
            max_size=self.settings.db_pool_max,
            timeout=self.settings.db_connect_timeout,
            open=False,
        )

        self._redis = redis_async.from_url(
            self.settings.redis_url,
            decode_responses=True,          # bytes 가 아니라 str 로 받는다
            socket_connect_timeout=3,
            socket_timeout=3,               # 빨리 포기한다. 캐시는 없어도 된다
            health_check_interval=30,
        )

        # ★ BRPOP 전용. Worker 만 쓴다
        #
        #   socket_timeout 은 반드시 BRPOP 의 대기 시간보다 길어야 한다
        #   같으면 경계에서 간헐적으로 끊긴다 → 재현이 어려운 버그가 된다
        #   → 여유를 5초 둔다
        #
        #   이 관계가 깨지면 무슨 일이 생기는가
        #     Worker 는 Running, Redis 도 정상, probe 도 통과
        #     그런데 큐를 한 건도 못 꺼낸다 → 주문이 영원히 pending
        #     → 2026-08-26 첫 실행에서 실제로 겪은 문제다
        self._redis_blocking = redis_async.from_url(
            self.settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=self.settings.worker_poll_timeout + 5,
            health_check_interval=30,
        )

        # 둘을 동시에 시도한다. 순서대로 하면 앞의 것이 늦을 때 뒤가 밀린다
        await asyncio.gather(
            self._connect_postgres_with_retry(),
            self._connect_redis_with_retry(),
        )

    async def _connect_postgres_with_retry(self) -> None:
        """지수 백오프로 재시도한다.

        간격을 늘리는 이유                                    (13 편의 그 원리)
          같은 실패를 1초 간격으로 반복하는 건 낭비다
          DB 주소가 틀렸다면 1초 뒤에 해도 똑같이 실패한다
        """
        assert self._pool is not None
        delays = [0, 1, 2, 4, 8, 15]

        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._pool.open(wait=True, timeout=self.settings.db_connect_timeout)
                async with self._pool.connection() as conn:
                    await conn.execute("SELECT 1")
                self.postgres.mark_up()
                logger.info(
                    "PostgreSQL 연결 성공",
                    extra={"ctx_attempt": attempt},
                )
                return
            except Exception as exc:          # noqa: BLE001 — 어떤 예외든 재시도한다
                self.postgres.mark_down(exc)
                logger.warning(
                    "PostgreSQL 연결 재시도",
                    extra={"ctx_attempt": attempt, "ctx_error": str(exc)},
                )

        logger.error("PostgreSQL 연결 실패. readiness 가 실패 상태로 남는다")

    async def _connect_redis_with_retry(self) -> None:
        assert self._redis is not None
        delays = [0, 1, 2, 4]

        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._redis.ping()
                self.redis.mark_up()
                logger.info("Redis 연결 성공", extra={"ctx_attempt": attempt})
                return
            except Exception as exc:          # noqa: BLE001
                self.redis.mark_down(exc)
                logger.warning(
                    "Redis 연결 재시도",
                    extra={"ctx_attempt": attempt, "ctx_error": str(exc)},
                )

        # Redis 는 실패해도 계속 간다                          ★ 00 문서
        #   캐시는 "있으면 좋은 것" 이다
        #   큐는 없으면 주문을 못 받지만, 조회는 계속돼야 한다
        logger.error("Redis 연결 실패. 캐시 없이 동작한다")

    # ── 종료 ────────────────────────────────────────────

    async def shutdown(self) -> None:
        """연결을 정리한다. SIGTERM 을 받으면 main.py 가 부른다  (02 문서)"""
        if self._pool is not None:
            try:
                await self._pool.close()
                logger.info("PostgreSQL 커넥션 풀 종료")
            except Exception as exc:          # noqa: BLE001
                logger.warning("커넥션 풀 종료 중 오류", extra={"ctx_error": str(exc)})

        for name, client in (
            ("Redis", self._redis),
            ("Redis(blocking)", self._redis_blocking),
        ):
            if client is None:
                continue
            try:
                await client.aclose()
                logger.info(f"{name} 연결 종료")
            except Exception as exc:          # noqa: BLE001
                logger.warning(
                    f"{name} 종료 중 오류", extra={"ctx_error": str(exc)}
                )

    # ── 사용 ────────────────────────────────────────────

    @property
    def pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("커넥션 풀이 아직 없다. startup 을 먼저 불러야 한다")
        return self._pool

    @property
    def redis_client(self) -> redis_async.Redis:
        """빠른 명령용. GET / SET / LPUSH / LLEN."""
        if self._redis is None:
            raise RuntimeError("Redis 클라이언트가 아직 없다. startup 을 먼저 불러야 한다")
        return self._redis

    @property
    def redis_blocking_client(self) -> redis_async.Redis:
        """블로킹 명령용. BRPOP 만 여기를 쓴다.

        ★ 여기에 GET/SET 을 쓰면 안 된다
          Redis 가 먹통일 때 poll_timeout+5 초를 기다리게 된다
          → 그러려고 나눈 게 아니다
        """
        if self._redis_blocking is None:
            raise RuntimeError(
                "블로킹용 Redis 클라이언트가 아직 없다. startup 을 먼저 불러야 한다"
            )
        return self._redis_blocking

    # ── 상태 확인 ───────────────────────────────────────

    async def check_postgres(self) -> bool:
        """지금 붙는지 확인한다. /health/deps 와 지표 갱신에 쓴다.

        readinessProbe 는 이걸 부르지 않는다.                 ★ 04 문서
        """
        try:
            async with self._pool.connection() as conn:       # type: ignore[union-attr]
                await conn.execute("SELECT 1")
            self.postgres.mark_up()
            return True
        except Exception as exc:                              # noqa: BLE001
            self.postgres.mark_down(exc)
            return False

    async def check_redis(self) -> bool:
        try:
            await self._redis.ping()                          # type: ignore[union-attr]
            self.redis.mark_up()
            return True
        except Exception as exc:                              # noqa: BLE001
            self.redis.mark_down(exc)
            return False

    def pool_stats(self) -> dict[str, int]:
        """커넥션 풀 상태. 05 문서의 db_pool_* 지표로 나간다.

        Redis 가 죽어 DB 로 몰릴 때 이 값이 503 보다 먼저 오른다
        → 터지기 전에 경고할 수 있다
        """
        if self._pool is None:
            return {"size": 0, "available": 0, "waiting": 0}
        stats = self._pool.get_stats()
        return {
            "size": int(stats.get("pool_size", 0)),
            "available": int(stats.get("pool_available", 0)),
            "waiting": int(stats.get("requests_waiting", 0)),
        }


# ─────────────────────────────────────────────────────────────
# 주기적으로 상태를 확인하는 배경 작업
#
# 왜 필요한가
#   요청이 없으면 아무도 DB 를 안 건드린다
#   → DB 가 죽어도 모른다 → 지표가 옛날 값 그대로다
#   → 주기적으로 확인해서 dependency_up 을 갱신한다
#
# 05 문서의 "없는 것을 잡는 설계" 와 같은 발상이다
# ─────────────────────────────────────────────────────────────


async def dependency_watcher(deps: Dependencies, interval: float = 10.0) -> None:
    """주기적으로 의존 서비스 상태를 확인하고 지표에 반영한다.

    ★★ 지표 갱신을 여기서 하는 이유                        (2026-08-26 수정)

      처음에는 check_postgres() / check_redis() 가
      DependencyState 만 갱신하고 Prometheus 게이지는 안 건드렸다

      그 결과
        redis     cache.py 가 요청마다 갱신해줘서 값이 유지됐다
        postgres  db.py 가 "실패했을 때만" 0 으로 만든다
                  → 살아나도 1 로 안 돌아온다
                  → 한 번도 실패한 적 없으면 지표가 아예 없다   ★

      "지표가 아예 없으면 알람이 안 울린다" — 05 문서에 적어둔 함정을
      코드에서 그대로 밟았다. 첫 실행에서 발견했다

      → 확인하는 곳에서 지표까지 같이 갱신한다
      → 요청이 하나도 없어도 10초마다 값이 살아 있다
    """
    while True:
        try:
            pg_ok, redis_ok = await asyncio.gather(
                deps.check_postgres(),
                deps.check_redis(),
            )
            metrics.set_dependency_up("postgres", pg_ok)
            metrics.set_dependency_up("redis", redis_ok)
        except asyncio.CancelledError:
            raise
        except Exception as exc:              # noqa: BLE001
            # 여기서 죽으면 감시가 멈춘다. 무슨 일이 있어도 계속 돈다
            logger.warning("의존 서비스 확인 중 오류", extra={"ctx_error": str(exc)})

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

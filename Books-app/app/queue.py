"""Redis 큐.

cache.py 와 정반대 원칙                                  ★★

  cache.py   실패를 삼킨다. 예외를 안 던진다
             캐시는 없어도 되는 것이니까

  queue.py   실패를 던진다. 반드시 위로 올린다
             큐 등록 실패 = 주문 유실이니까

같은 Redis 인데 실패를 다루는 방식이 반대다
  "무엇이 걸려 있는가" 로 정한다

00-architecture.md 에서 일부러 남긴 모순
  캐시로서의 Redis   잃어도 된다
  큐로서의 Redis     잃으면 안 된다
  그런데 같은 Redis 다

  → 미리 없애지 않기로 했다
  → 6단계에서 Redis 를 죽여 "접수된 주문이 사라지는" 걸 직접 겪는다
  → 그다음 영속성을 켜거나 큐를 DB 로 옮기고 비교한다
"""

from __future__ import annotations

import logging

from app import metrics
from app.deps import Dependencies
from app.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)


class Queue:
    def __init__(self, deps: Dependencies, *, name: str) -> None:
        self.deps = deps
        self.name = name

    async def enqueue(self, order_id: int) -> None:
        """큐에 넣는다. 실패하면 AppError 를 던진다.

        던지는 이유                                       ★ 01 문서
          호출하는 쪽(라우터)이 트랜잭션 안에서 부른다
          → 여기서 예외가 나면 트랜잭션이 롤백된다
          → 재고가 원래대로 돌아간다
          → 사용자는 503 을 받고 "실패했다" 를 안다

        삼켰다면
          주문은 접수됐는데 영원히 처리되지 않는다
          → 조용한 실패다. 2단계 내내 그것 때문에 고생했다
        """
        try:
            await self.deps.redis_client.lpush(self.name, str(order_id))
        except Exception as exc:  # noqa: BLE001
            self.deps.redis.mark_down(exc)
            metrics.set_dependency_up("redis", False)
            metrics.dependency_errors_total.labels(
                name="redis",
                kind=metrics.classify_dependency_error(exc),
            ).inc()
            logger.error(
                "큐 등록 실패. 주문을 롤백한다",
                extra={"ctx_order_id": order_id, "ctx_error": str(exc)},
            )
            raise AppError(ErrorCode.QUEUE_UNAVAILABLE) from exc

        metrics.queue_enqueued_total.inc()

    async def dequeue(self, timeout: float) -> int | None:
        """큐에서 하나 꺼낸다. 없으면 timeout 만큼 기다리다 None.

        BRPOP 을 쓰는 이유
          RPOP 을 반복하면 큐가 비어 있을 때 CPU 를 태운다 (바쁜 대기)
          BRPOP 은 값이 들어올 때까지 블록한다
          → 그런데 무한히 기다리면 종료 신호에 반응을 못 한다
          → timeout 을 줘서 주기적으로 깨어난다

        깨어나는 주기가 곧 worker_last_poll_timestamp_seconds 의 갱신 주기다
          → 큐가 비어 있어도 이 값이 갱신된다
          → "대기 중" 과 "멈춤" 을 구분할 수 있다 (05 문서)

        ★★ redis_blocking_client 를 쓴다. redis_client 가 아니다
                                                          (2026-08-26 수정)
          BRPOP 이 timeout 초 동안 기다리는 사이 서버는 아무것도 안 보낸다
          → 소켓 타임아웃이 그보다 짧으면 소켓이 먼저 끊긴다
          → 정상적인 대기가 "Timeout reading from redis" 장애로 둔갑한다

          처음에 redis_client(socket_timeout=3)를 썼고
          poll_timeout 이 5초라 매번 3초에 끊겼다
          → Worker 가 큐를 한 건도 못 꺼냈다
          → 그런데 Redis 는 멀쩡했다. ping 은 계속 성공했다
          → 컨테이너도 Running, probe 도 통과
          → "성공했지만 일을 안 하는" 상태였다
        """
        try:
            result = await self.deps.redis_blocking_client.brpop(
                [self.name], timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001
            self.deps.redis.mark_down(exc)
            metrics.set_dependency_up("redis", False)
            metrics.dependency_errors_total.labels(
                name="redis",
                kind=metrics.classify_dependency_error(exc),
            ).inc()
            raise

        self.deps.redis.mark_up()
        metrics.set_dependency_up("redis", True)

        if result is None:
            return None

        _key, raw = result
        metrics.queue_dequeued_total.inc()
        try:
            return int(raw)
        except (TypeError, ValueError):
            # 큐에 이상한 값이 들어 있다. 버리고 계속 간다
            #   → 여기서 죽으면 Worker 가 멈춘다
            #   → 나쁜 데이터 하나가 전체를 세우면 안 된다
            logger.warning("큐에 이상한 값", extra={"ctx_raw": str(raw)})
            return None

    async def length(self) -> int | None:
        """큐 길이. 실패하면 None.

        이건 지표용이라 실패해도 던지지 않는다
          → 길이를 못 읽는다고 서비스가 멈출 이유가 없다
        """
        try:
            return int(await self.deps.redis_client.llen(self.name))
        except Exception:  # noqa: BLE001
            return None


async def publish_queue_length(queue: Queue) -> None:
    """큐 길이를 지표로 내보낸다. 배경 작업이 주기적으로 부른다.

    왜 주기적으로 재는가                                  ★ 05 문서
      요청이 없으면 아무도 큐를 안 본다
      → 쌓이고 있어도 지표가 옛날 값 그대로다

    그리고 길이만 보면 안 된다
      100개가 쌓였다 → 문제인가?
      Worker 가 초당 1000개를 처리하면 0.1초면 없어진다
      → 입력 속도 / 소비 속도 / 대기 시간을 같이 본다
    """
    length = await queue.length()
    if length is not None:
        metrics.queue_length.set(length)

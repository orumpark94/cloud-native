"""경로 3 — 큐 소비자(Worker).

00-architecture.md 의 흐름

  1. 큐에서 주문 ID 를 꺼낸다              (없으면 잠깐 기다린다)
  2. pending → processing 으로 바꾼다
  3. 결제 처리를 흉내낸다                   (WORKER_PROCESS_SECONDS)
  4. 성공 → completed
     실패 → failed + 재고 복구             (보상 트랜잭션)

★★ 이 파일을 짜는 관점 — "아무도 안 보는 곳에서 도는 프로세스"

  API 는 실패하면 사용자가 안다. 503 을 받으니까
  Worker 는 멈춰도 아무도 모른다
    큐만 조용히 쌓인다
    Pod 는 Running, 프로세스도 살아 있다
    → 12편의 "DESIRED 0인데 지표는 정상" 과 같은 성격이다

  그래서 이 프로세스는 스스로 계속 말해야 한다
    "나 살아 있고, 방금 큐를 봤다"
    → worker_last_poll_timestamp_seconds 를 매 회전마다 갱신한다
    → 큐가 비어서 놀고 있어도 갱신된다
    → 그래야 "대기 중" 과 "멈춤" 을 구분할 수 있다 (05 문서)

여기서 절대 하면 안 되는 것
  예외가 났을 때 루프를 빠져나오는 것
    → 주문 하나가 잘못돼서 Worker 전체가 서면 안 된다
    → 모든 예외를 잡고, 세고, 다음 건으로 간다
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid

from app import metrics
from app.db import acquire
from app.errors import AppError
from app.logging_setup import request_id_var
from app.queue import Queue
from app.repositories import orders as orders_repo
from app.runtime import RuntimeState

logger = logging.getLogger(__name__)


class OrderWorker:
    def __init__(self, runtime: RuntimeState, queue: Queue, faults) -> None:
        self.runtime = runtime
        self.queue = queue
        self.faults = faults
        self._backoff = 0.0

    # ─────────────────────────────────────────────────────────
    # 바깥 루프
    # ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        """종료 신호가 올 때까지 계속 돈다."""
        settings = self.runtime.settings
        logger.info(
            "worker 시작",
            extra={
                "ctx_queue": self.queue.name,
                "ctx_poll_timeout": settings.worker_poll_timeout,
            },
        )

        while not self.runtime.shutting_down:
            # ★ 큐를 보기 "전" 에 찍는다
            #   꺼낸 뒤에 찍으면 큐가 비어 있을 때 영원히 갱신되지 않는다
            #   → "일이 없어서 조용한 것" 이 "죽은 것" 처럼 보인다
            metrics.worker_last_poll_timestamp_seconds.set(time.time())

            try:
                order_id = await self.queue.dequeue(settings.worker_poll_timeout)
            except Exception as exc:  # noqa: BLE001
                # Redis 가 죽었다. 여기서 죽으면 Pod 가 재시작되고
                # 재시작해도 Redis 는 여전히 죽어 있다 → CrashLoopBackOff
                # → 죽지 말고 물러났다가 다시 시도한다
                await self._sleep_backoff(exc)
                continue

            self._backoff = 0.0

            if order_id is None:
                # 타임아웃. 큐가 비었다는 뜻이지 오류가 아니다
                continue

            # 요청 ID 를 여기서 새로 만든다                  ★ logging_setup
            #   API 요청에서 시작된 흐름이지만 프로세스가 다르다
            #   → 큐에 request_id 를 같이 넣으면 끝까지 이을 수 있다
            #   → 지금은 안 한다. 6단계에서 "로그가 안 이어진다" 를 겪고 넣는다
            token = request_id_var.set(f"wk-{uuid.uuid4().hex[:12]}")
            try:
                await self._handle(order_id)
            except Exception as exc:  # noqa: BLE001
                # 여기까지 온 예외는 처리 로직의 버그다.
                # 그래도 루프는 계속 간다
                metrics.orders_processed_total.labels(result="error").inc()
                logger.exception(
                    "주문 처리 중 예상치 못한 오류",
                    extra={"ctx_order_id": order_id, "ctx_error": str(exc)},
                )
            finally:
                request_id_var.reset(token)

        logger.info("worker 종료")

    async def _sleep_backoff(self, exc: BaseException) -> None:
        """Redis 장애 시 물러나는 간격을 늘린다.

        왜 지수적으로 늘리는가
          1초마다 재시도하면 죽은 Redis 에 초당 N번씩 붙는다
          → Pod 가 여러 개면 Redis 가 살아나는 순간 몰려서 또 죽는다
          → 간격을 늘려 붐비지 않게 한다

        상한을 두는 이유
          무한히 늘리면 Redis 가 살아나도 한참 뒤에야 알아챈다
        """
        self._backoff = min(max(self._backoff * 2, 1.0), 15.0)
        logger.error(
            "큐를 읽지 못했다. 잠시 후 재시도",
            extra={"ctx_error": str(exc), "ctx_backoff": self._backoff},
        )
        await asyncio.sleep(self._backoff)

    # ─────────────────────────────────────────────────────────
    # 한 건 처리
    # ─────────────────────────────────────────────────────────

    async def _handle(self, order_id: int) -> None:
        settings = self.runtime.settings

        # 1. 선점 — pending 이었던 것만 가져온다
        try:
            async with acquire(self.runtime.deps) as conn:
                async with conn.transaction():
                    order = await orders_repo.mark_processing(conn, order_id)
        except AppError as exc:
            # DB 가 죽었다. 이 주문은 아직 pending 이다
            #
            # ★ 큐에 되돌리지 않는다
            #   되돌리면 DB 가 죽어 있는 동안 같은 건이 무한 순환한다
            #   → 큐 지표가 계속 돌아 "일하고 있는 것처럼" 보인다
            #   → DB 에 pending 으로 남아 있으므로 잃어버린 건 아니다
            #   → 재처리는 별도 복구 Job 의 몫이다 (5단계 이후)
            metrics.orders_processed_total.labels(result="deferred").inc()
            logger.error(
                "DB 를 쓸 수 없어 처리를 미룬다. 주문은 pending 으로 남는다",
                extra={"ctx_order_id": order_id, "ctx_error": exc.code.value},
            )
            return

        if order is None:
            # 갱신된 행이 0개다. 두 가지 경우가 섞여 있다
            await self._handle_missing(order_id)
            return

        # 2. 큐에서 기다린 시간                              ★ 05 문서
        #   created_at → started_at 이 "대기"
        #   started_at → finished_at 이 "처리"
        #   둘을 합치면 왜 느린지 알 수 없다
        wait = (order["started_at"] - order["created_at"]).total_seconds()
        metrics.order_queue_wait_seconds.observe(max(0.0, wait))

        # 3. 처리
        metrics.worker_in_flight.inc()
        started = time.perf_counter()
        try:
            await self._process_payment(order)
        finally:
            metrics.order_process_duration_seconds.observe(time.perf_counter() - started)
            metrics.worker_in_flight.dec()

        _ = settings  # (설정은 _process_payment 안에서 쓴다)

    async def _handle_missing(self, order_id: int) -> None:
        """mark_processing 이 아무것도 못 바꾼 경우.

        두 가지가 섞여 있어서 구분해야 한다

          [a] 주문이 아예 없다 — orphaned                    ★ 라우터 주석의 그 구멍
              큐에는 들어갔는데 커밋 직전에 API 프로세스가 죽은 경우다
              → 조용히 버린다. 대신 세어서 지표로 남긴다
              → 이 값이 오르면 "커밋과 큐 등록이 어긋나고 있다" 는 신호다

          [b] 이미 processing/completed 다 — duplicate
              Worker 가 둘 이상일 때 같은 건을 집은 경우다
              → WHERE status='pending' 이 막아준 것이다. 정상 동작이다
        """
        try:
            async with acquire(self.runtime.deps) as conn:
                existing = await orders_repo.get_order(conn, order_id)
        except AppError:
            metrics.orders_processed_total.labels(result="deferred").inc()
            return

        if existing is None:
            metrics.orders_processed_total.labels(result="orphaned").inc()
            logger.error(
                "큐에는 있는데 DB 에 없는 주문. 버린다",
                extra={"ctx_order_id": order_id},
            )
        else:
            metrics.orders_processed_total.labels(result="duplicate").inc()
            logger.warning(
                "이미 처리 중이거나 끝난 주문. 건너뛴다",
                extra={
                    "ctx_order_id": order_id,
                    "ctx_status": existing["status"],
                },
            )

    async def _process_payment(self, order: dict) -> None:
        """결제 처리 흉내.

        실제 결제를 붙이지 않는 이유                         (00 문서)
          이 프로젝트의 목표는 결제 로직이 아니다
          "시간이 걸리는 외부 작업" 이라는 성질만 있으면 된다
          → 그 성질이 큐를 밀리게 하고, 그게 관측 대상이다

        시간과 실패율을 환경변수로 뺀 이유                    ★ 06 문서
          6단계에서 이 값을 올려 큐가 쌓이는 걸 만든다
          → 같은 이미지로 실험한다. 코드를 안 고친다
        """
        settings = self.runtime.settings
        order_id = order["id"]

        # 기본 처리 시간 + 장애 주입으로 더한 시간
        seconds = settings.worker_process_seconds + self.faults.worker_extra_seconds()
        if seconds > 0:
            await asyncio.sleep(seconds)

        # 확률적 실패. 보상 트랜잭션을 시험하기 위한 것이다
        failed = (
            settings.is_worker_failure_enabled
            and random.random() < settings.worker_failure_rate
        )

        try:
            async with acquire(self.runtime.deps) as conn:
                async with conn.transaction():
                    if failed:
                        # ★ 상태 변경과 재고 복구를 한 트랜잭션에 넣는다
                        #   나누면 "failed 인데 재고는 안 돌아온" 상태가 생긴다
                        #   → 한 트랜잭션이면 둘 다 되거나 둘 다 안 된다
                        await orders_repo.mark_failed(conn, order_id, "payment_failed")
                        await orders_repo.restore_stock(
                            conn, order["book_id"], order["quantity"]
                        )
                    else:
                        await orders_repo.mark_completed(conn, order_id)
        except AppError as exc:
            # ★★ 여기가 이 앱에서 가장 나쁜 상태다
            #
            #   주문은 processing 인데 끝내지 못했다
            #   큐에서는 이미 꺼냈다 → 아무도 다시 안 본다
            #   → 영원히 processing 으로 남는다
            #
            #   이걸 잡는 방법은 하나뿐이다
            #     "processing 인데 started_at 이 오래된 주문" 을 세는 것
            #     → 05 문서의 "없는 것을 잡는" 지표와 같은 발상이다
            #     → 5단계에서 정합성 검증 Job 으로 만든다
            metrics.orders_processed_total.labels(result="stuck").inc()
            logger.error(
                "처리 결과를 기록하지 못했다. 주문이 processing 으로 남는다",
                extra={"ctx_order_id": order_id, "ctx_error": exc.code.value},
            )
            return

        result = "failed" if failed else "completed"
        metrics.orders_processed_total.labels(result=result).inc()
        logger.info(
            "주문 처리 완료",
            extra={"ctx_order_id": order_id, "ctx_result": result},
        )


# ─────────────────────────────────────────────────────────────
# 배경 작업
# ─────────────────────────────────────────────────────────────


async def queue_length_reporter(queue: Queue, runtime: RuntimeState, interval: float = 5.0) -> None:
    """큐 길이를 주기적으로 지표에 반영한다.

    왜 별도 작업인가                                        ★ 05 문서
      큐 길이는 "요청이 올 때" 재면 안 된다
      요청이 없는 동안에도 쌓일 수 있기 때문이다
      → 주기적으로 스스로 재야 한다

    API 쪽에도 이 작업을 띄운다
      Worker 가 죽으면 이 지표도 같이 멈춘다
      → 그러면 "큐가 쌓이는지" 조차 알 수 없다
      → 관측하는 쪽과 관측당하는 쪽을 분리한다
    """
    from app.queue import publish_queue_length

    while not runtime.shutting_down:
        try:
            await publish_queue_length(queue)
        except Exception:  # noqa: BLE001
            # 지표 수집 실패로 프로세스가 죽으면 안 된다
            pass
        await asyncio.sleep(interval)

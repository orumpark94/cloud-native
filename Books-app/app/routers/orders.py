"""경로 2 + 3 — 주문 접수.

01-api-spec.md 의 처리 순서

  [경로 2 — 동기]
    1. 요청 형식을 검증한다                       실패 → 400
    2. 트랜잭션을 연다
    3. 책을 조회하고 재고를 확인한다                없음 → 404 / 부족 → 409
    4. 재고를 차감한다
    5. 주문을 pending 으로 저장한다

  [경로 3 — 비동기]
    6. Redis 큐에 주문 ID 를 넣는다               실패 → 롤백 + 503
    7. 커밋한다
    8. 202 를 준다

★★ 6번과 7번의 순서가 이 파일에서 가장 중요한 판단이다

  [만약 커밋을 먼저 하면]
    재고는 이미 차감됐는데 큐 등록에 실패한다
    → 주문은 받았는데 영원히 처리되지 않는다
    → 조용한 실패다. 2단계 내내 그것 때문에 고생했다

  [커밋을 큐 등록 뒤로 옮기면]
    큐 등록이 실패하면 트랜잭션이 롤백된다
    → 재고가 원래대로 돌아간다
    → 사용자는 503 을 받고 "실패했다" 를 안다

  [그런데 이것도 완벽하지 않다]
    큐에는 들어갔는데 커밋 직전에 프로세스가 죽으면?
    → 큐에는 있는데 DB 에는 주문이 없다
    → Worker 가 그 ID 를 찾다가 실패한다

    분산 트랜잭션의 근본 문제다. 여기서 해결하지 않는다
    → Worker 가 "없는 주문" 을 만나면 조용히 버리고 지표를 올린다
    → 6단계에서 이 상황을 일부러 만들어본다
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from app import metrics
from app.db import acquire, maybe_slow_query
from app.errors import AppError, ErrorCode
from app.repositories import orders as orders_repo

router = APIRouter(tags=["orders"])


class CreateOrderRequest(BaseModel):
    book_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)
    # 수량 상한을 두는 이유
    #   없으면 quantity=999999999 로 재고를 한 번에 털 수 있다
    #   밖에서 들어오는 값에는 항상 상한을 둔다


def _ctx(request: Request):
    app_state = request.app.state
    return app_state.runtime, app_state.queue, app_state.faults


def _user_id(raw: str | None) -> int:
    """X-User-Id 헤더를 읽는다.

    인증을 만들지 않기로 했다 (01 문서)
      로그인·회원가입을 만들면 3단계가 길어진다
      이 프로젝트에서 "사용자" 는 k6 다

    실제 서비스라면 여기에 인증이 들어간다
    """
    if not raw or not raw.strip():
        raise AppError(ErrorCode.MISSING_USER)
    try:
        value = int(raw)
    except ValueError as exc:
        raise AppError(
            ErrorCode.MISSING_USER,
            message="X-User-Id 는 숫자여야 합니다",
        ) from exc
    if value <= 0:
        raise AppError(ErrorCode.MISSING_USER)
    return value


async def _apply_request_faults(faults) -> None:
    delay = faults.should_inject_latency()
    if delay > 0:
        await asyncio.sleep(delay)
    if faults.should_inject_error():
        raise AppError(
            ErrorCode.INJECTED_ERROR,
            status_code=faults.error_status(),
            detail={"injected": True},
        )


@router.post("/orders", status_code=202)
async def create_order(
    request: Request,
    body: CreateOrderRequest,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict:
    """주문 접수.

    201 이 아니라 202 를 주는 이유                        (01 문서)
      201 Created  "만들었고 처리도 끝났다"
      202 Accepted "접수했다. 처리는 아직이다"

      우리는 재고만 확보하고 결제 처리는 Worker 에게 넘긴다
      → 201 로 주면 클라이언트가 "끝났다" 고 오해한다
    """
    runtime, queue, faults = _ctx(request)
    await _apply_request_faults(faults)

    user_id = _user_id(x_user_id)
    started = time.perf_counter()

    try:
        async with acquire(runtime.deps) as conn:
            await maybe_slow_query(conn, faults.db_slow_seconds())

            # 트랜잭션 블록. 예외가 나면 자동으로 롤백된다
            async with conn.transaction():
                stock = await orders_repo.reserve_stock(
                    conn,
                    book_id=body.book_id,
                    quantity=body.quantity,
                    strategy=runtime.settings.stock_strategy,
                )

                if not stock.ok:
                    # 409 — 요청은 맞는데 지금 상태와 충돌한다      (01 문서)
                    #   400 이면 "고쳐서 다시 보내라" 인데
                    #   재고는 시간이 지나면 바뀔 수 있다
                    metrics.orders_created_total.labels(result="out_of_stock").inc()
                    raise AppError(
                        ErrorCode.OUT_OF_STOCK,
                        detail={
                            "book_id": body.book_id,
                            "requested": body.quantity,
                            "available": stock.remaining,
                        },
                    )

                order = await orders_repo.insert_order(
                    conn,
                    user_id=user_id,
                    book_id=body.book_id,
                    quantity=body.quantity,
                    unit_price=stock.price,
                )

                # ★★ 커밋 전에 큐에 넣는다
                #    실패하면 예외가 나고 트랜잭션이 롤백된다
                #    → 재고가 원래대로 돌아간다
                await queue.enqueue(order["id"])

            # 여기서 커밋됐다
    except AppError as exc:
        if exc.code not in (ErrorCode.OUT_OF_STOCK,):
            metrics.orders_created_total.labels(result="error").inc()
        raise
    finally:
        metrics.db_transaction_duration_seconds.observe(time.perf_counter() - started)

    metrics.orders_created_total.labels(result="accepted").inc()

    payload = orders_repo.serialize_order({**order, "status": "pending"})
    # Location 헤더로 조회 주소를 알려준다
    #   상대 경로를 쓴다 → 프록시 뒤에서 https/http 를 신경 안 써도 된다 (02 문서)
    request.state.location = f"/orders/{order['id']}"
    return payload


@router.get("/orders/{order_id}")
async def get_order(
    request: Request,
    order_id: int,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict:
    """주문 처리 상태.

    캐시하지 않는다                                       (01 문서)
      주문 상태는 계속 바뀐다
      낡은 값을 주면 사용자가 "처리 중" 을 계속 본다
      → 잃어도 되는 데이터가 아니다
    """
    runtime, _queue, faults = _ctx(request)
    await _apply_request_faults(faults)
    user_id = _user_id(x_user_id)

    async with acquire(runtime.deps) as conn:
        await maybe_slow_query(conn, faults.db_slow_seconds())
        order = await orders_repo.get_order(conn, order_id)

    if order is None:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, detail={"order_id": order_id})

    # 남의 주문을 못 보게 한다
    #   인증이 없으므로 완전한 보호는 아니다. 헤더를 바꾸면 볼 수 있다
    #   그래도 "실제 서비스라면 여기서 검사한다" 를 코드로 남겨둔다
    if order["user_id"] != user_id:
        raise AppError(ErrorCode.ORDER_NOT_FOUND, detail={"order_id": order_id})

    return orders_repo.serialize_order(order)


@router.get("/orders")
async def list_orders(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(default=None),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict:
    """내 주문 목록.

    status 로 거를 수 있게 한 이유                        (01 문서)
      6단계에서 "pending 이 몇 개나 밀려 있나" 를 API 로도 확인한다
      → 지표(queue_length)와 DB 의 pending 수가 어긋나면
         큐에는 있는데 DB 에 없거나 그 반대다 → 정합성 문제를 잡는다
    """
    runtime, _queue, faults = _ctx(request)
    await _apply_request_faults(faults)
    user_id = _user_id(x_user_id)

    if status is not None and status not in (
        "pending",
        "processing",
        "completed",
        "failed",
    ):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            message="status 값이 올바르지 않습니다",
            detail={"allowed": ["pending", "processing", "completed", "failed"]},
        )

    async with acquire(runtime.deps) as conn:
        await maybe_slow_query(conn, faults.db_slow_seconds())
        rows, total = await orders_repo.list_orders(
            conn, user_id=user_id, limit=limit, offset=offset, status=status
        )

    return {
        "items": [orders_repo.serialize_order(r) for r in rows],
        "limit": limit,
        "offset": offset,
        "total": total,
    }

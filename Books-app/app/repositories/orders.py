"""orders 테이블과 재고 차감.

03-data-model.md 의 "주문 SQL 을 세 번에 걸쳐 발전시킨다" 를 구현한다.
어느 방식을 쓸지는 환경변수 STOCK_STRATEGY 로 정한다.

  none         잠금 없이. 동시 주문에서 재고가 음수가 된다     ← 기본값
  for_update   SELECT ... FOR UPDATE. 안전하지만 직렬화된다
  conditional  UPDATE ... WHERE stock >= n. 잠금 구간이 짧다

왜 기본값이 "none" 인가                                  ★ 03 문서
  1차   잠금 없이 만든다 → 부하를 넣어 재고를 음수로 만든다
  2차   잠금을 넣는다 → 음수가 안 나오는지 확인한다
        대신 처리량이 얼마나 떨어지는지 잰다

  "실패한 출력 자체가 학습 대상" 이라는 원칙 그대로다
  실무라면 처음부터 안전한 방식을 쓴다. 그것도 문서에 남겼다

왜 환경변수로 고르게 했는가
  코드를 고쳐 재빌드하면 "같은 이미지" 비교가 아니게 된다 (02 문서)
  → 같은 이미지로 세 방식을 비교할 수 있어야 실험이 깨끗하다
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app import metrics
from app.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)


class StockResult:
    """재고 차감 결과."""

    def __init__(self, *, ok: bool, price: int, remaining: int | None) -> None:
        self.ok = ok
        self.price = price
        self.remaining = remaining


# ─────────────────────────────────────────────────────────────
# 재고 차감 — 세 가지 방식
# ─────────────────────────────────────────────────────────────


async def reserve_stock(
    conn: AsyncConnection,
    *,
    book_id: int,
    quantity: int,
    strategy: str,
) -> StockResult:
    if strategy == "for_update":
        return await _reserve_for_update(conn, book_id=book_id, quantity=quantity)
    if strategy == "conditional":
        return await _reserve_conditional(conn, book_id=book_id, quantity=quantity)
    return await _reserve_no_lock(conn, book_id=book_id, quantity=quantity)


async def _reserve_no_lock(
    conn: AsyncConnection, *, book_id: int, quantity: int
) -> StockResult:
    """1차 — 잠금 없이. 의도적으로 안전하지 않다.

    무슨 일이 일어나는가
      두 요청이 동시에 SELECT 하면 둘 다 stock=1 을 본다
      둘 다 "1권 있으니 팔아도 된다" 고 판단한다
      둘 다 UPDATE 한다 → stock = -1

    그리고 그 상황이 지표에 안 잡힌다
      Pod    Running    정상
      probe  통과       정상
      응답    202        정상
      그런데 데이터가 깨졌다

      → books_stock_negative_total 이 그걸 잡는 유일한 장치다 (05 문서)
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, price, stock FROM books WHERE id = %s",
            (book_id,),
        )
        book = await cur.fetchone()

    if book is None:
        raise AppError(ErrorCode.BOOK_NOT_FOUND, detail={"book_id": book_id})

    # 앱에서 판단한다. 이 판단과 아래 UPDATE 사이에 다른 요청이 끼어들 수 있다
    if book["stock"] < quantity:
        return StockResult(ok=False, price=book["price"], remaining=book["stock"])

    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE books SET stock = stock - %s WHERE id = %s RETURNING stock",
            (quantity, book_id),
        )
        row = await cur.fetchone()
        remaining = int(row[0]) if row else 0

    _check_negative(book_id, remaining)
    return StockResult(ok=True, price=book["price"], remaining=remaining)


async def _reserve_for_update(
    conn: AsyncConnection, *, book_id: int, quantity: int
) -> StockResult:
    """2차 — 행 잠금.

    FOR UPDATE 를 붙이면
      첫 요청이 그 행을 잠근다
      두 번째 요청은 커밋될 때까지 기다린다
      → 순서대로 처리된다. 음수가 안 나온다

    대가
      같은 책 주문이 직렬화된다 → 처리량이 떨어진다
      인기 있는 책 하나에 주문이 몰리면 그 행이 병목이 된다 (hot row)
      → "Pod 를 늘렸는데 왜 안 빨라지나" 를 직접 겪는다 (03 문서)

    db_lock_wait 은 별도 지표가 없다
      → db_transaction_duration_seconds 가 늘어나는 것으로 관찰한다
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, price, stock FROM books WHERE id = %s FOR UPDATE",
            (book_id,),
        )
        book = await cur.fetchone()

    if book is None:
        raise AppError(ErrorCode.BOOK_NOT_FOUND, detail={"book_id": book_id})

    if book["stock"] < quantity:
        return StockResult(ok=False, price=book["price"], remaining=book["stock"])

    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE books SET stock = stock - %s WHERE id = %s RETURNING stock",
            (quantity, book_id),
        )
        row = await cur.fetchone()
        remaining = int(row[0]) if row else 0

    _check_negative(book_id, remaining)
    return StockResult(ok=True, price=book["price"], remaining=remaining)


async def _reserve_conditional(
    conn: AsyncConnection, *, book_id: int, quantity: int
) -> StockResult:
    """3차 — 조건부 UPDATE.

    판단과 차감을 한 문장으로 합친다
      UPDATE ... WHERE id = %s AND stock >= %s

      갱신된 행이 0개면 재고가 부족했던 것이다
      → SELECT 로 확인하고 UPDATE 하는 사이의 틈이 없다
      → FOR UPDATE 보다 잠금 구간이 짧다

    가격을 따로 읽어야 하는 점이 단점이다
      → RETURNING 으로 같이 가져와 쿼리를 하나로 줄인다
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            UPDATE books
               SET stock = stock - %s
             WHERE id = %s AND stock >= %s
            RETURNING price, stock
            """,
            (quantity, book_id, quantity),
        )
        row = await cur.fetchone()

    if row is not None:
        _check_negative(book_id, int(row["stock"]))
        return StockResult(ok=True, price=int(row["price"]), remaining=int(row["stock"]))

    # 갱신이 안 됐다. 책이 없는 건지 재고가 부족한 건지 구분해야 한다
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT price, stock FROM books WHERE id = %s", (book_id,))
        book = await cur.fetchone()

    if book is None:
        raise AppError(ErrorCode.BOOK_NOT_FOUND, detail={"book_id": book_id})
    return StockResult(ok=False, price=int(book["price"]), remaining=int(book["stock"]))


def _check_negative(book_id: int, remaining: int) -> None:
    """재고가 음수가 됐는지 확인한다.                     ★★ 05 문서

    "성공했지만 잘못된" 경우를 세는 유일한 장치다
    요청은 202 를 주고, 다른 모든 지표는 정상이다
    → 이 카운터가 0보다 크면 즉시 알람
    """
    if remaining < 0:
        metrics.books_stock_negative_total.inc()
        logger.error(
            "재고가 음수가 됐다",
            extra={"ctx_book_id": book_id, "ctx_stock": remaining},
        )


# ─────────────────────────────────────────────────────────────
# orders 테이블
# ─────────────────────────────────────────────────────────────


async def insert_order(
    conn: AsyncConnection,
    *,
    user_id: int,
    book_id: int,
    quantity: int,
    unit_price: int,
) -> dict[str, Any]:
    """주문을 pending 으로 저장한다.

    unit_price 를 저장하는 이유                           (03 문서)
      나중에 책값이 바뀌어도 과거 주문 금액이 바뀌면 안 된다
      → 영수증이 나중에 바뀌는 셈이 된다
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO orders (user_id, book_id, quantity, unit_price, status)
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING id, user_id, book_id, quantity, unit_price, status, created_at
            """,
            (user_id, book_id, quantity, unit_price),
        )
        row = await cur.fetchone()
    return dict(row) if row else {}


async def get_order(
    conn: AsyncConnection, order_id: int
) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, user_id, book_id, quantity, unit_price, status,
                   created_at, started_at, finished_at, failure_reason
              FROM orders
             WHERE id = %s
            """,
            (order_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def list_orders(
    conn: AsyncConnection,
    *,
    user_id: int,
    limit: int,
    offset: int,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """내 주문 목록.

    인덱스가 없다                                         ★ 03 문서
      orders (user_id, created_at DESC) 가 필요해 보이지만 지금은 안 만든다
      → 데이터를 늘려 느려지는 걸 EXPLAIN 으로 확인한 뒤
         인덱스를 추가하고 몇 배 빨라졌는지 잰다 (5단계)
      → 만들고 시작하면 "인덱스 덕분에 빠르다" 를 확인할 수 없다
    """
    conditions = ["user_id = %s"]
    params: list[Any] = [user_id]
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = " AND ".join(conditions)

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            SELECT id, user_id, book_id, quantity, unit_price, status,
                   created_at, started_at, finished_at, failure_reason
              FROM orders
             WHERE {where}
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        rows = await cur.fetchall()

    async with conn.cursor() as cur:
        await cur.execute(f"SELECT COUNT(*) FROM orders WHERE {where}", tuple(params))
        row = await cur.fetchone()
        total = int(row[0]) if row else 0

    return [dict(r) for r in rows], total


# ─────────────────────────────────────────────────────────────
# Worker 가 쓰는 것 (조각 7 에서 사용)
# ─────────────────────────────────────────────────────────────


async def mark_processing(conn: AsyncConnection, order_id: int) -> dict[str, Any] | None:
    """pending → processing 으로 바꾸고 started_at 을 찍는다.

    WHERE status = 'pending' 을 붙이는 이유
      Worker 가 둘 이상이면 같은 주문을 두 번 집을 수 있다
      → 이미 processing 인 것은 갱신되지 않는다 → 중복 처리를 막는다
      → 갱신된 행이 0개면 다른 Worker 가 가져간 것이다
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            UPDATE orders
               SET status = 'processing', started_at = now()
             WHERE id = %s AND status = 'pending'
            RETURNING id, user_id, book_id, quantity, unit_price,
                      created_at, started_at
            """,
            (order_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def mark_completed(conn: AsyncConnection, order_id: int) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE orders SET status='completed', finished_at=now() WHERE id = %s",
            (order_id,),
        )


async def mark_failed(
    conn: AsyncConnection, order_id: int, reason: str
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE orders
               SET status='failed', finished_at=now(), failure_reason=%s
             WHERE id = %s
            """,
            (reason, order_id),
        )


async def restore_stock(conn: AsyncConnection, book_id: int, quantity: int) -> None:
    """실패한 주문의 재고를 되돌린다 (보상 트랜잭션).

    여기에 위험이 하나 있다                               ★ 6단계 실험거리
      되돌리는 도중에 Worker 가 죽으면?
      → 재고가 안 돌아온 채로 남는다
      → 주문은 failed 인데 재고는 줄어 있다
      → 정합성 검증 Job 이 있어야 잡을 수 있다 (5단계 이후)
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE books SET stock = stock + %s WHERE id = %s",
            (quantity, book_id),
        )


def serialize_order(order: dict[str, Any]) -> dict[str, Any]:
    """API 응답 형식으로 바꾼다.

    total_price 를 저장하지 않고 계산하는 이유             (03 문서)
      중복 저장하면 둘이 어긋날 수 있다
      할인·쿠폰이 없으므로 단순 곱이면 충분하다
    """
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    return {
        "order_id": order["id"],
        "user_id": order["user_id"],
        "book_id": order["book_id"],
        "quantity": order["quantity"],
        "unit_price": order["unit_price"],
        "total_price": order["unit_price"] * order["quantity"],
        "status": order.get("status"),
        "created_at": _iso(order.get("created_at")),
        "started_at": _iso(order.get("started_at")),
        "finished_at": _iso(order.get("finished_at")),
        "failure_reason": order.get("failure_reason"),
    }

"""에러 코드와 응답 형식.

01-api-spec.md 의 규칙
  에러 응답 형식을 하나로 통일한다
  { "error": { "code": ..., "message": ..., "detail": {...} } }

왜 코드를 열거형으로 고정하는가                          ★ 05 문서
  error_code 는 지표의 라벨로 쓴다
  라벨 값이 무한히 늘어나면 시계열이 폭발한다 (카디널리티 폭발)

  [나쁜 예]
    error_code = str(exception)      → 메시지마다 다른 시계열이 생긴다
    "connection to 10.0.1.5:5432 failed"
    "connection to 10.0.1.6:5432 failed"     ← IP 만 달라도 다른 라벨이다

  [좋은 예]
    error_code = "DB_UNAVAILABLE"    → 값이 유한하다. 여기 적힌 것뿐이다

  구체적인 내용은 detail 과 로그로 보낸다. 지표에는 안 넣는다
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """지표 라벨로 쓰이는 값. 여기 없는 코드를 쓰면 안 된다.

    새 코드를 추가할 때는
      1. 여기에 넣는다
      2. 05 문서의 알람 대상인지 판단한다
      3. 대시보드에 반영할지 정한다
    """

    # ── 요청이 잘못됨 (4xx) ─────────────────────────────
    INVALID_REQUEST = "INVALID_REQUEST"          # 400 형식이 틀렸다
    BOOK_NOT_FOUND = "BOOK_NOT_FOUND"            # 404
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"          # 404
    MISSING_USER = "MISSING_USER"                # 400 X-User-Id 가 없다
    OUT_OF_STOCK = "OUT_OF_STOCK"                # 409 재고 부족

    # ── 우리 잘못 (500) ─────────────────────────────────
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # ── 밖의 문제 (503) ─────────────────────────────────
    # 500 과 503 을 나누는 이유                           (01 문서)
    #   500  우리 코드의 버그. 재시도해도 똑같다
    #   503  의존 서비스 장애. 잠시 뒤 재시도하면 될 수 있다
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
    NOT_READY = "NOT_READY"                      # 기동이 아직 안 끝났다

    # ── 장애 주입으로 만든 것 (06 문서) ──────────────────
    # 진짜 장애와 구분하려고 따로 둔다
    #   → 실험 중 발생한 에러가 지표에서 섞이면 해석이 어렵다
    INJECTED_ERROR = "INJECTED_ERROR"


# 코드마다 기본 HTTP 상태를 정해둔다.
# 코드와 상태를 각각 넘기면 실수로 어긋난다 (404 인데 코드는 OUT_OF_STOCK 같은)
STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.MISSING_USER: 400,
    ErrorCode.BOOK_NOT_FOUND: 404,
    ErrorCode.ORDER_NOT_FOUND: 404,
    ErrorCode.OUT_OF_STOCK: 409,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.DB_UNAVAILABLE: 503,
    ErrorCode.QUEUE_UNAVAILABLE: 503,
    ErrorCode.NOT_READY: 503,
    ErrorCode.INJECTED_ERROR: 500,
}

DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "요청 형식이 올바르지 않습니다",
    ErrorCode.MISSING_USER: "X-User-Id 헤더가 필요합니다",
    ErrorCode.BOOK_NOT_FOUND: "책을 찾을 수 없습니다",
    ErrorCode.ORDER_NOT_FOUND: "주문을 찾을 수 없습니다",
    ErrorCode.OUT_OF_STOCK: "재고가 부족합니다",
    ErrorCode.INTERNAL_ERROR: "서버 오류가 발생했습니다",
    ErrorCode.DB_UNAVAILABLE: "데이터베이스에 연결할 수 없습니다",
    ErrorCode.QUEUE_UNAVAILABLE: "처리 대기열에 연결할 수 없습니다",
    ErrorCode.NOT_READY: "아직 준비되지 않았습니다",
    ErrorCode.INJECTED_ERROR: "장애 주입으로 발생한 오류입니다",
}


class AppError(Exception):
    """앱이 의도적으로 던지는 예외.

    main.py 에 등록한 예외 처리기가 이걸 잡아
    통일된 형식의 응답으로 바꾸고 지표를 올린다.

    → 각 라우터에서 매번 응답을 조립하지 않아도 된다
    → 형식이 저절로 통일된다
    """

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGE.get(code, "오류가 발생했습니다")
        self.detail = detail
        self.status_code = status_code or STATUS_BY_CODE.get(code, 500)
        super().__init__(f"{code}: {self.message}")

    def to_response(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": {
                "code": str(self.code),
                "message": self.message,
            }
        }
        if self.detail:
            body["error"]["detail"] = self.detail
        return body

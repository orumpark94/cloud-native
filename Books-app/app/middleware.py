"""모든 요청이 반드시 거치는 곳.

하는 일
  1. request_id 를 만들거나 이어받는다        → 전 구간 추적의 키 (01 문서)
  2. 응답 시간을 잰다                         → RED 의 Duration
  3. 지표를 센다                              → RED 의 Rate / Errors
  4. 접속 로그를 남긴다                        → JSON 한 줄 (02 문서)

이 파일을 읽는 관점
  여기서 빠뜨리면 어떤 지표도 안 채워진다
  여기서 실수하면 모든 요청이 느려진다
  → 최소한만 한다. 무거운 일은 하지 않는다

  한 요청에 1ms 를 더하면 초당 1000요청에서 초당 1초를 쓴다
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app import metrics
from app.logging_setup import request_id_var

logger = logging.getLogger("app.access")

REQUEST_ID_HEADER = "X-Request-Id"


def classify_route(path_template: str, method: str) -> str:
    """경로를 세 갈래로 나눈다.                            ★ 00 문서

    이 라벨 하나가 만드는 차이
      없으면   "요청이 느려졌다"
      있으면   "조회는 멀쩡한데 주문만 느려졌다"

    internal 을 따로 두는 이유
      Prometheus 가 15초마다 /metrics 를 긁는다
      → 그것도 요청이다. 섞이면 실제 트래픽 지표가 왜곡된다
      → 그렇다고 아예 안 세면 "스크레이프가 타임아웃난다" 를 못 본다
      → 분류만 해두고 대시보드에서 걸러 쓴다
    """
    if path_template.startswith(("/health", "/metrics", "/debug")):
        return "internal"
    if method == "POST" and path_template.startswith("/orders"):
        return "write"
    if path_template == "unmatched":
        return "unknown"
    return "read"


def _path_template(request: Request) -> str:
    """실제 URL 이 아니라 라우트 패턴을 얻는다.           ★★ 05 문서

    [나쁨]  /books/1  /books/2  /books/3 ...   → 책 수만큼 시계열이 생긴다
    [좋음]  /books/{id}                        → 하나

    매칭이 안 된 요청(404)은 "unmatched" 로 묶는다
      공격자가 /aaa /bbb /ccc 를 무작위로 두드리면
      → 그대로 라벨에 넣으면 시계열이 무한히 늘어난다
      → Prometheus 를 밖에서 죽일 수 있다
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return "unmatched"
    return str(template)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ── 요청 ID ─────────────────────────────────────
        # 클라이언트가 보냈으면 그대로 쓴다
        #   → 앞단(Ingress, 다른 서비스)에서 시작된 추적을 이어받는다
        # 없으면 만든다
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)

        # 라우터와 예외 처리기가 여기에 값을 심는다
        request.state.request_id = request_id
        request.state.error_code = None

        started = time.perf_counter()
        status_code = 500
        response: Response | None = None

        # in_flight 는 라우팅 전이라 경로를 모른다.
        # 그래서 method 만으로 대충 나누지 않고, 응답 후에 정확한 분류로 센다.
        #   → 대신 지금 처리 중인 수는 "전체" 로만 본다
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # 처리기가 못 잡은 예외. 여기서도 지표는 남겨야 한다
            #   → 안 남기면 "요청은 들어왔는데 아무 기록이 없는" 구멍이 생긴다
            request.state.error_code = request.state.error_code or "INTERNAL_ERROR"
            raise
        finally:
            elapsed = time.perf_counter() - started
            path = _path_template(request)
            route_class = classify_route(path, request.method)
            error_code = getattr(request.state, "error_code", None)

            metrics.http_requests_total.labels(
                method=request.method,
                path=path,
                status=str(status_code),
                route_class=route_class,
            ).inc()

            metrics.http_request_duration_seconds.labels(
                method=request.method,
                path=path,
                route_class=route_class,
            ).observe(elapsed)

            if error_code:
                metrics.http_errors_total.labels(
                    error_code=str(error_code),
                    route_class=route_class,
                ).inc()

            # 응답 헤더에도 넣는다
            #   → 사용자가 "이 요청이 안 됐어요" 라고 할 때
            #     그 id 로 로그를 바로 찾을 수 있다
            if response is not None:
                response.headers[REQUEST_ID_HEADER] = request_id

            # 접속 로그.  uvicorn 의 access 로그는 꺼뒀다 (logging_setup.py)
            #   → request_id 를 붙이려면 우리가 남겨야 한다
            _log_access(
                method=request.method,
                path=path,
                raw_path=request.url.path,
                status=status_code,
                elapsed=elapsed,
                route_class=route_class,
                error_code=error_code,
            )

            request_id_var.reset(token)


def _log_access(
    *,
    method: str,
    path: str,
    raw_path: str,
    status: int,
    elapsed: float,
    route_class: str,
    error_code: str | None,
) -> None:
    """접속 로그를 남긴다.

    지표와 로그의 역할 분담                              ★ 05 문서
      지표   집계다. 라벨에 무한한 값을 못 넣는다 → path 는 패턴
      로그   개별 건이다 → 실제 URL(raw_path)을 여기 남긴다

      "어느 책 조회가 느렸나" 는 지표로 못 찾는다. 로그로 찾는다
    """
    # 상태 코드로 로그 수준을 나눈다
    #   5xx 를 info 로 남기면 에러만 걸러 보기가 어렵다
    if status >= 500:
        level = logging.ERROR
    elif status >= 400:
        level = logging.WARNING
    else:
        level = logging.INFO

    logger.log(
        level,
        "request",
        extra={
            "ctx_method": method,
            "ctx_path": path,               # 패턴  /books/{id}
            "ctx_raw_path": raw_path,       # 실제  /books/1
            "ctx_status": status,
            "ctx_duration_ms": round(elapsed * 1000, 2),
            "ctx_route_class": route_class,
            "ctx_error_code": error_code,
        },
    )

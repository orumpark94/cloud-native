"""조립 — 시작과 끝.

지금까지 만든 파일들은 서로를 거의 모른다.
이 파일이 유일하게 전부를 안다. 그리고 다른 파일은 이 파일을 모른다.

  왜 그렇게 나눴는가
    서로 참조하기 시작하면 순환 참조가 생긴다
    (health.py → debug.py → health.py 같은)
    → 상태는 runtime.py / faults.py 에 두고
    → 조립만 여기서 한다


★★ 이 파일의 관점 — "뜨는 순간" 과 "죽는 순간"

  지금까지의 코드는 전부 "도는 중" 의 코드였다
  여기는 그 앞뒤를 다룬다. Kubernetes 와 앱이 실제로 만나는 지점이다

  [뜨는 순간]
    설정이 틀렸으면 즉시 죽는다
    → CrashLoopBackOff 가 되고 이유가 로그에 남는다
    → 반대로 "일단 뜨고 요청이 올 때 실패" 하면
       Pod 는 Running, probe 도 통과, 사용자만 500 을 받는다

  [죽는 순간]                                            ★ 02·04 문서
    SIGTERM 을 받으면 두 가지가 동시에 일어난다
      kubelet 이 신호를 보낸다
      EndpointSlice 에서 이 Pod 를 뺀다

    그런데 두 번째가 모든 노드의 iptables 에 퍼지는 데 시간이 걸린다
    → 그 사이 들어온 요청이 죽어가는 Pod 로 간다
    → 즉시 종료하면 그 요청들이 끊긴다

    그래서 순서가 이렇다
      1. readiness 를 먼저 끈다        (다음 probe 부터 빠진다)
      2. SHUTDOWN_GRACE_SECONDS 기다린다 (규칙이 퍼지는 시간)
      3. 그다음 서버를 닫는다
      4. 커넥션을 정리한다

    → 롤링 업데이트에서 요청이 끊기느냐 마느냐가 여기서 갈린다


포트를 둘로 나눈다                                       ★ 06·07 문서
  8000  서비스     /books /orders
  9000  관리       /health/* /metrics /debug/*

  Service 에 9000 을 안 넣으면 클러스터 밖에서 못 닿는다
  kubelet 은 Pod IP 로 직접 부르므로 probe 는 정상 동작한다
  Prometheus 도 Pod IP 로 긁으므로 문제없다
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response

from app import debug as debug_module
from app import metrics
from app.cache import Cache
from app.config import ConfigError, Settings, load_settings, redact_url
from app.deps import Dependencies, dependency_watcher
from app.errors import AppError, ErrorCode
from app.faults import FaultRegistry
from app.health import router as health_router
from app.logging_setup import setup_logging
from app.middleware import ObservabilityMiddleware
from app.queue import Queue
from app.routers.books import router as books_router
from app.routers.orders import router as orders_router
from app.runtime import RuntimeState
from app.worker import OrderWorker, queue_length_reporter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 예외 처리기
#
# 각 라우터에서 응답을 조립하지 않는 이유                  ★ 01 문서
#   형식이 저절로 어긋난다. 한 군데는 {"error": ...}, 다른 데는 {"detail": ...}
#   → 클라이언트가 에러를 파싱할 수 없다
#   → 한 곳에서 만들면 형식이 하나로 유지된다
#
# 그리고 여기서 error_code 를 request.state 에 심는다
#   → 미들웨어가 그 값으로 http_errors_total 을 센다 (middleware.py)
# ─────────────────────────────────────────────────────────────


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> Response:
        request.state.error_code = str(exc.code)
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> Response:
        """Pydantic 이 걸러낸 요청.

        FastAPI 기본 형식(422 + detail)을 쓰지 않고 우리 형식으로 바꾼다
          → 다른 에러와 형식이 같아야 클라이언트가 하나로 처리한다
          → 400 으로 내린다. 422 는 클라이언트 라이브러리들이 잘 다루지 못한다
        """
        error = AppError(
            ErrorCode.INVALID_REQUEST,
            detail={"fields": _summarize_validation(exc)},
        )
        request.state.error_code = str(error.code)
        return JSONResponse(status_code=400, content=error.to_response())

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        """예상하지 못한 예외.

        ★ 예외 내용을 응답에 넣지 않는다
          스택 트레이스에는 파일 경로, 쿼리, 때로는 값까지 들어 있다
          → 공격자에게 내부 구조를 알려주는 셈이다
          → 응답에는 코드만, 자세한 건 로그로 (request_id 로 찾는다)
        """
        request.state.error_code = str(ErrorCode.INTERNAL_ERROR)
        logger.exception(
            "처리되지 않은 예외",
            extra={"ctx_path": request.url.path, "ctx_error": str(exc)},
        )
        error = AppError(ErrorCode.INTERNAL_ERROR)
        return JSONResponse(status_code=500, content=error.to_response())


def _summarize_validation(exc: RequestValidationError) -> list[dict[str, str]]:
    """검증 오류를 짧게 줄인다.

    Pydantic 원본에는 입력값이 그대로 들어 있다
      → 비밀번호 같은 게 섞이면 로그와 응답에 남는다
      → 필드 이름과 사유만 남긴다
    """
    result = []
    for item in exc.errors()[:10]:      # 10개까지만. 무한히 늘어나지 않게
        location = ".".join(str(p) for p in item.get("loc", []) if p != "body")
        result.append({"field": location or "body", "reason": item.get("type", "invalid")})
    return result


# ─────────────────────────────────────────────────────────────
# 서비스 앱 (8000)
# ─────────────────────────────────────────────────────────────


def create_service_app(runtime: RuntimeState, cache: Cache, queue: Queue,
                       faults: FaultRegistry) -> FastAPI:
    app = FastAPI(
        title="서점 API",
        version=runtime.settings.app_version,
        # 문서 자동생성을 끄지 않는다. 학습 중에는 /docs 로 직접 눌러보는 게 낫다
        #   → 실무에서 외부 공개 API 라면 끄거나 인증 뒤에 둔다
    )

    app.state.runtime = runtime
    app.state.cache = cache
    app.state.queue = queue
    app.state.faults = faults

    app.add_middleware(ObservabilityMiddleware)
    _install_handlers(app)

    @app.middleware("http")
    async def _location_header(request: Request, call_next):
        """POST /orders 가 심어둔 Location 을 응답 헤더로 옮긴다.

        라우터가 직접 Response 를 만들지 않게 하려는 것이다
          → 라우터는 dict 만 돌려주고 형식 조립은 밖에서 한다
        """
        response = await call_next(request)
        location = getattr(request.state, "location", None)
        if location:
            response.headers["Location"] = location
        return response

    app.include_router(books_router)
    app.include_router(orders_router)
    return app


# ─────────────────────────────────────────────────────────────
# 관리 앱 (9000)
#
# 왜 별도 FastAPI 인스턴스인가                             ★ 06 문서
#   같은 앱에 두고 경로로만 나누면
#   Service 에 8000 을 여는 순간 /debug 도 같이 열린다
#   → 포트 자체를 나눠야 "Service 에 안 넣는다" 가 실제 방어가 된다
# ─────────────────────────────────────────────────────────────


def create_admin_app(runtime: RuntimeState, faults: FaultRegistry) -> FastAPI:
    app = FastAPI(title="서점 관리 포트", docs_url=None, redoc_url=None)

    app.state.runtime = runtime
    app.state.faults = faults

    app.add_middleware(ObservabilityMiddleware)
    _install_handlers(app)

    app.include_router(health_router)

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        # 긁힐 때마다 풀 상태를 갱신한다
        #   별도 배경 작업을 두지 않으려는 것이다
        #   → 아무도 안 긁으면 값이 낡지만, 안 긁으면 볼 사람도 없다
        metrics.set_pool_stats(**runtime.deps.pool_stats())
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    # ★ 1겹 — 꺼져 있으면 라우터를 아예 등록하지 않는다
    #   경로가 존재하되 403 을 주는 방식이 아니다
    #   → 존재하지 않으면 실수로 열릴 여지 자체가 없다
    if runtime.settings.enable_debug_endpoints:
        app.include_router(debug_module.router)
        logger.warning(
            "장애 주입 엔드포인트가 켜져 있다. 프로덕션이면 즉시 꺼야 한다",
            extra={"ctx_port": runtime.settings.admin_port},
        )

    return app


# ─────────────────────────────────────────────────────────────
# 기동
# ─────────────────────────────────────────────────────────────


async def _serve(app: FastAPI, port: int, stop: asyncio.Event) -> None:
    """uvicorn 을 프로그램 안에서 띄운다.

    왜 `uvicorn app.main:app` 명령을 쓰지 않는가
      포트가 둘이다. 명령 하나로는 하나만 띄운다
      SIGTERM 을 우리가 직접 받아 순서를 통제해야 한다
      → uvicorn 의 기본 종료 절차만으로는 readiness 를 먼저 끌 수 없다
    """
    config = uvicorn.Config(
        app,
        host="0.0.0.0",     # noqa: S104 — 컨테이너 안이다. 밖에서 Pod IP 로 온다
        port=port,
        log_config=None,    # 우리 로깅 설정을 덮어쓰지 않게 한다
        access_log=False,   # 접속 로그는 미들웨어가 남긴다
        # 워커를 늘리지 않는다                              ★ 05 문서
        #   prometheus_client 가 프로세스마다 따로 센다
        #   → 값이 나뉜다. Pod 수로 늘린다
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None      # 신호는 우리가 받는다

    serve_task = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await serve_task


async def _run(settings: Settings) -> int:
    faults = FaultRegistry(default_ttl_seconds=settings.debug_default_ttl_seconds)
    deps = Dependencies(settings)
    runtime = RuntimeState(settings=settings, deps=deps, component=settings.component)

    metrics.init_app_info(
        version=settings.app_version,
        pod=settings.pod_name,
        node=settings.node_name,
        namespace=settings.namespace,
        component=settings.component,
        debug_enabled=settings.enable_debug_endpoints,
    )

    # ── 종료 신호 ───────────────────────────────────────
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    # 첫 신호를 받은 시각. 중복 전달을 걸러내는 데 쓴다
    #   dict 로 두는 이유는 중첩 함수에서 값을 바꾸기 위해서다 (nonlocal 대신)
    signal_state = {"first_at": 0.0}

    # 중복으로 볼 시간 창
    #   사람이 Ctrl+C 를 두 번 누르는 간격보다는 짧고
    #   시스템이 중복 전달하는 간격(수백 ms)보다는 길게 잡는다
    DUPLICATE_WINDOW = 2.0

    def _on_signal(name: str) -> None:
        now = time.monotonic()

        if runtime.shutting_down:
            # ★★ 같은 SIGTERM 이 여러 번 전달될 수 있다      (2026-08-26 발견)
            #
            #   Kubernetes 에 올려서 실측하니 SIGTERM 한 번에
            #   이 핸들러가 세 번 불렸다
            #
            #     08:29:42.088  종료 신호 수신
            #     08:29:42.088  readiness 를 껐다. grace_seconds: 5.0
            #     08:29:42.211  종료 신호 재수신. 즉시 종료한다   ← 123ms 뒤
            #     08:29:42.415  종료 완료
            #
            #   원래 의도는 "사람이 Ctrl+C 를 두 번 누르면 안 기다린다" 였다
            #   그런데 중복 전달이 그 조건에 걸려 대기가 통째로 사라졌다
            #   → SHUTDOWN_GRACE_SECONDS 를 0으로 두든 5로 두든 결과가 같았다
            #
            #   Compose 에서는 안 드러났다
            #   종료 중에 요청을 계속 보내며 재보지 않았기 때문이다
            #
            #   추정 원인은 uvicorn 이 자체 신호 처리를 설치하는 것과 겹친 것인데
            #   확인하지 못했다. 다만 고치는 방법은 원인과 무관하다
            #   → 짧은 간격의 재수신은 중복으로 보고 무시한다
            if now - signal_state["first_at"] < DUPLICATE_WINDOW:
                logger.debug(
                    "종료 신호 중복 전달. 무시한다",
                    extra={"ctx_signal": name},
                )
                return

            logger.warning("종료 신호 재수신. 즉시 종료한다")
            stop.set()
            return

        signal_state["first_at"] = now
        logger.info("종료 신호 수신", extra={"ctx_signal": name})
        runtime.shutting_down = True          # ★ readiness 가 즉시 false 가 된다
        metrics.app_ready.set(0)
        loop.create_task(_graceful(runtime, stop))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal, sig.name)
        except NotImplementedError:
            # Windows 에서는 add_signal_handler 를 못 쓴다
            #   개발 PC 가 Windows 다. 여기서 죽으면 로컬 실행이 안 된다
            #   컨테이너(리눅스)에서는 정상 동작한다
            signal.signal(sig, lambda *_: _on_signal(sig.name))

    # ── 연결 ────────────────────────────────────────────
    await deps.startup()

    cache = Cache(deps, faults, ttl_seconds=settings.cache_ttl_seconds)
    queue = Queue(deps, name=settings.queue_name)

    background = [
        asyncio.create_task(dependency_watcher(deps)),
        asyncio.create_task(debug_module.fault_janitor(faults, runtime)),
        asyncio.create_task(queue_length_reporter(queue, runtime)),
    ]

    admin_app = create_admin_app(runtime, faults)
    tasks = [asyncio.create_task(_serve(admin_app, settings.admin_port, stop))]

    if settings.component == "api":
        service_app = create_service_app(runtime, cache, queue, faults)
        tasks.append(asyncio.create_task(_serve(service_app, settings.app_port, stop)))
        logger.info(
            "API 기동",
            extra={
                "ctx_app_port": settings.app_port,
                "ctx_admin_port": settings.admin_port,
                "ctx_stock_strategy": settings.stock_strategy,
            },
        )
    else:
        worker = OrderWorker(runtime, queue, faults)
        tasks.append(asyncio.create_task(worker.run()))
        logger.info(
            "Worker 기동",
            extra={
                "ctx_admin_port": settings.admin_port,
                "ctx_queue": settings.queue_name,
                "ctx_process_seconds": settings.worker_process_seconds,
            },
        )

    await asyncio.gather(*tasks, return_exceptions=True)

    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await deps.shutdown()

    logger.info("종료 완료")
    return 0


async def _graceful(runtime: RuntimeState, stop: asyncio.Event) -> None:
    """readiness 를 끈 뒤 기다렸다가 서버를 닫는다.        ★★ 02·04 문서

    이 대기가 왜 필요한가
      Pod 를 지울 때 "SIGTERM 전달" 과 "Endpoint 제거" 는 동시에 시작된다
      그런데 Endpoint 제거는 모든 노드의 kube-proxy 로 퍼져야 한다
      → 그 전파에 시간이 걸린다 (04편에서 실측했다)
      → 그 사이 들어온 요청은 아직 이 Pod 로 온다

      즉시 닫으면 그 요청들이 connection refused 를 받는다
      → 롤링 업데이트할 때마다 소수의 요청이 실패한다
      → 그런데 지표에 안 잡힌다. 앱이 이미 죽어서 셀 수가 없다   ★ 조용한 실패

    terminationGracePeriodSeconds 와의 관계
      SHUTDOWN_GRACE_SECONDS 는 그보다 반드시 작아야 한다
      크면 기다리다가 SIGKILL 을 맞는다 → 정리 절차가 안 돈다
    """
    grace = runtime.settings.shutdown_grace_seconds
    logger.info(
        "readiness 를 껐다. Endpoint 에서 빠질 시간을 기다린다",
        extra={"ctx_grace_seconds": grace},
    )
    await asyncio.sleep(grace)
    stop.set()


def main() -> int:
    # ── 1. 설정을 먼저 읽는다 ───────────────────────────
    #   로깅보다 먼저다. 로그 수준이 설정에 들어 있기 때문이다
    #   → 그래서 여기서 나는 오류는 print 로 낸다
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)      # noqa: T201
        # ★ 즉시 죽는다
        #   기본값으로 대충 뜨면 "왜 DB 에 안 붙지?" 를 나중에 헤맨다
        #   지금 죽으면 CrashLoopBackOff 로 즉시 보인다
        return 78      # EX_CONFIG. 설정 문제임을 종료 코드로도 남긴다

    setup_logging(
        level=settings.log_level,
        service=f"bookstore-{settings.component}",
        pod=settings.pod_name,
        node=settings.node_name,
        version=settings.app_version,
    )

    logger.info(
        "기동 시작",
        extra={
            "ctx_component": settings.component,
            "ctx_version": settings.app_version,
            "ctx_database": redact_url(settings.database_url),
            "ctx_redis": redact_url(settings.redis_url),
        },
    )

    try:
        return asyncio.run(_run(settings))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

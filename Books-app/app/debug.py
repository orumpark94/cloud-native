"""장애 주입 엔드포인트.

06-fault-injection.md 의 안전장치 5겹을 코드로 구현한다.
상태는 faults.py 가 들고 있다. 여기는 HTTP 문(門)만 낸다.

★★ 이 파일의 관점 — 이건 고의로 만든 취약점이다

  "요청을 실패시키는 API" 는 그 자체로 공격 도구다
  누군가 이 주소를 알면 서비스를 마음대로 망가뜨릴 수 있다

  그런데도 만드는 이유
    장애를 못 만들면 관측 체계가 맞는지 확인할 수 없다
    5단계에서 대시보드를 만들어도 "잘 보이는지" 를 검증할 방법이 없다
    → 부수는 능력이 있어야 고치는 능력을 검증할 수 있다

  대신 5겹으로 막는다

    1겹  기본 꺼짐        ENABLE_DEBUG_ENDPOINTS=false 가 기본
                          → 켜는 건 명시적 행동이어야 한다
    2겹  관리 포트에만     Service 에 9000 을 안 넣는다
                          → 클러스터 밖에서 닿지 않는다
    3겹  TTL 자동 만료     사람은 잊는다. 시간이 대신 꺼준다
    4겹  지표로 노출       켜져 있으면 대시보드에 보인다 → 알람
    5겹  Pod 로컬         전체가 아니라 일부만 고장낼 수 있다

  이 다섯이 각각 다른 실패 방식을 막는다
    1겹  실수로 배포되는 것
    2겹  외부에서 닿는 것
    3겹  실험 후 잊는 것
    4겹  잊은 걸 아무도 모르는 것
    5겹  실험이 전체 장애가 되는 것
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.errors import AppError, ErrorCode
from app.faults import FaultRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])


# 주입 종류를 여기 적힌 것으로 고정한다
#   임의의 문자열을 받으면 오타가 조용히 통과한다
#   → "break-redis" 를 "break_redis" 로 쳐도 200 이 나온다
#   → 실험이 안 걸렸는데 걸린 줄 안다
KNOWN_KINDS = (
    "break-redis",     # Redis 를 죽인 것처럼
    "slow-query",      # DB 쿼리를 느리게 (pg_sleep)
    "latency",         # 확률적 지연
    "error-rate",      # 확률적 에러
    "worker-slow",     # Worker 처리 시간 증가
    "ready",           # readiness 강제 실패
)


def _registry(request: Request) -> FaultRegistry:
    return request.app.state.faults


def _log(action: str, **fields: Any) -> None:
    """장애 주입 조작은 반드시 남긴다.

    왜 로그가 중요한가
      3개월 뒤 이상한 지표를 보고 "장애인가?" 할 때
      로그에 "누가 언제 무엇을 주입했다" 가 있으면 5초 만에 끝난다
      없으면 몇 시간을 태운다
    """
    logger.warning(f"장애 주입 {action}", extra={f"ctx_{k}": v for k, v in fields.items()})


# ─────────────────────────────────────────────────────────────
# 요청 형식
# ─────────────────────────────────────────────────────────────


class InjectRequest(BaseModel):
    # ttl_seconds 를 반드시 받는다 (기본값은 설정에서 온다)
    #   0 은 "무한" 이지만 명시적으로 0 을 써야만 된다
    #   → 실수로 무한이 되는 경우를 없앤다
    ttl_seconds: int | None = Field(default=None, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────────────────────


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    """지금 이 Pod 에 무엇이 주입돼 있는가.

    ★ "이 Pod 에" 가 핵심이다                              (5겹 중 5겹)
      상태가 Pod 로컬 메모리에 있으므로
      Pod 3개면 각각 다른 상태일 수 있다

      → 실험할 때는 어느 Pod 에 넣었는지 알아야 한다
      → 그래서 응답에 pod 이름을 같이 넣는다
    """
    runtime = request.app.state.runtime
    registry = _registry(request)
    return {
        "pod": runtime.settings.pod_name,
        "node": runtime.settings.node_name,
        "component": runtime.component,
        "ready_override": runtime.ready_override,
        "injections": registry.snapshot(),
        "known_kinds": list(KNOWN_KINDS),
    }


# ─────────────────────────────────────────────────────────────
# 주입
# ─────────────────────────────────────────────────────────────


@router.post("/inject/{kind}")
async def inject(kind: str, body: InjectRequest, request: Request) -> dict[str, Any]:
    """장애를 주입한다.

    각 kind 가 무엇을 만드는가

      break-redis    params: {mode: "error"|"slow", seconds: N}
                     캐시 미스가 급증한다 → DB 로 몰린다 → 풀이 찬다
                     ★ 연쇄 장애를 만드는 스위치다 (00·05 문서)
                     → 큐도 같이 죽으므로 주문이 503 이 된다

      slow-query     params: {seconds: N}
                     pg_sleep 으로 커넥션을 실제로 붙잡는다
                     → 앱에서 sleep 하면 풀이 안 찬다. 그건 다른 실험이다

      latency        params: {ms: N, ratio: 0.0~1.0}
                     10% 만 느리게 → p50 은 멀쩡한데 p95 만 나쁜 상황
                     → 평균만 보는 대시보드가 못 잡는다 (05 문서)

      error-rate     params: {ratio: 0.0~1.0, status: 500}
                     확률적 실패. SLO 소진 속도를 관찰한다

      worker-slow    params: {seconds: N}
                     처리 시간만 늘린다 → 큐가 쌓인다
                     → wait 과 process 를 나눈 이유를 확인한다

      ready          params: {}
                     readiness 를 강제로 끈다
                     → Endpoint 에서 빠지는 걸 관찰한다
                     → 04편에서 실측한 "반영 지연" 을 다시 잰다
    """
    if kind not in KNOWN_KINDS:
        # 400 을 준다. 조용히 무시하지 않는다
        #   무시하면 실험이 안 걸린 줄 모른다
        return _unknown(kind)

    registry = _registry(request)
    runtime = request.app.state.runtime

    if kind == "ready":
        # readiness 는 FaultRegistry 가 아니라 RuntimeState 가 들고 있다
        #   is_ready() 가 그 값을 보기 때문이다 (runtime.py)
        runtime.ready_override = False
        injection = registry.set(kind, {}, ttl_seconds=body.ttl_seconds)
        _log("설정", kind=kind, ttl=body.ttl_seconds)
        return {
            "injected": kind,
            "expires_in": _expires_in(injection),
            "note": "readiness 가 강제로 꺼졌다. /health/ready 가 503 을 준다",
        }

    injection = registry.set(kind, body.params, ttl_seconds=body.ttl_seconds)
    _log("설정", kind=kind, params=str(body.params), ttl=body.ttl_seconds)

    return {
        "injected": kind,
        "params": injection.params,
        "expires_in": _expires_in(injection),
        "pod": runtime.settings.pod_name,
    }


@router.delete("/inject/{kind}")
async def clear(kind: str, request: Request) -> dict[str, Any]:
    if kind not in KNOWN_KINDS:
        return _unknown(kind)

    registry = _registry(request)
    runtime = request.app.state.runtime

    if kind == "ready":
        runtime.ready_override = None

    removed = registry.clear(kind)
    _log("해제", kind=kind, removed=removed)
    return {"cleared": kind, "was_active": removed}


@router.post("/reset")
async def reset(request: Request) -> dict[str, Any]:
    """전부 끈다.

    왜 이게 따로 있어야 하는가                              ★ 06 문서
      실험이 끝났을 때 하나씩 지우면 빠뜨린다
      "다 껐다" 를 한 번에 확인할 수 있어야 한다

      실험 절차의 마지막 줄은 항상 이것이다
        POST /debug/reset  →  GET /debug/state  로 비었는지 확인
    """
    registry = _registry(request)
    runtime = request.app.state.runtime

    runtime.ready_override = None
    count = registry.clear_all()

    _log("전체 해제", count=count)
    return {"cleared": count, "state": registry.snapshot()}


# ─────────────────────────────────────────────────────────────
# 배경 작업 — 만료된 주입 정리
# ─────────────────────────────────────────────────────────────


async def fault_janitor(registry: FaultRegistry, runtime, interval: float = 10.0) -> None:
    """만료된 주입을 치우고 지표를 맞춘다.                   ★ 3겹 + 4겹

    FaultRegistry.get() 도 만료를 치우지만 그것만으로는 부족하다
      요청이 아예 안 오면 get() 이 안 불린다
      → 만료됐는데 debug_injection_active 지표가 1로 남는다
      → "장애 주입 중" 이라는 잘못된 알람이 계속 울린다

    그리고 여기서만 할 수 있는 일이 하나 있다               ★
      ready 주입은 runtime.ready_override 에 값을 남긴다
      registry 에서 만료돼도 그 값은 안 지워진다
      → readiness 가 영원히 꺼진 채로 남는다
      → TTL 로 자동 복구되게 하려면 여기서 같이 되돌려야 한다
    """
    while not runtime.shutting_down:
        try:
            registry.purge_expired()
            # registry 에서 사라졌는데 override 가 남아 있으면 되돌린다
            if runtime.ready_override is not None and registry.get("ready") is None:
                runtime.ready_override = None
                _log("만료로 자동 해제", kind="ready")
        except Exception:  # noqa: BLE001
            # 청소 작업이 프로세스를 죽이면 안 된다
            pass
        await asyncio.sleep(interval)


# ─────────────────────────────────────────────────────────────
# 도우미
# ─────────────────────────────────────────────────────────────


def _unknown(kind: str) -> dict[str, Any]:
    raise AppError(
        ErrorCode.INVALID_REQUEST,
        message=f"'{kind}' 는 알 수 없는 주입 종류다",
        detail={"known_kinds": list(KNOWN_KINDS)},
    )


def _expires_in(injection) -> float | None:
    return injection.remaining(time.time())

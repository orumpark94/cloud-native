"""헬스체크 엔드포인트.

04-health-check.md 의 판단을 그대로 옮긴 것이다.

  live    프로세스가 살아 있고 응답하는가. 그게 전부다
  ready   이 Pod 만의 문제만 본다
  deps    운영자용. 항상 200. probe 로 쓰지 않는다

이 파일을 읽는 관점
  "이 판단이 틀리면 무엇이 잘못되는가"

    live 를 잘못 판단   → 멀쩡한 컨테이너를 죽인다
    ready 를 잘못 판단  → 서비스가 통째로 Endpoint 에서 빠진다

  그래서 판단에 넣는 것을 최소한으로 줄였다

관리 포트(9000)에 붙는다. 서비스 포트(8000)가 아니다      (06·07 문서)
  → Service 에 9000 을 안 넣으면 클러스터 밖에서 못 닿는다
  → kubelet 은 Pod IP 로 직접 부르므로 probe 는 정상 동작한다
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response

from app import metrics
from app.runtime import RuntimeState

router = APIRouter(prefix="/health", tags=["health"])


def _state(request: Request) -> RuntimeState:
    return request.app.state.runtime


@router.get("/live")
async def live() -> dict[str, str]:
    """livenessProbe 용.

    확인하는 것
      프로세스가 살아 있고 HTTP 요청에 응답하는가

    확인하지 않는 것
      DB / Redis / 큐 — 어떤 외부 의존성도 보지 않는다      ★★ 04 문서

    왜 의존성을 넣으면 안 되는가
      liveness 실패 → 컨테이너 재시작

      DB 가 죽었는데 앱을 재시작하면
        1. 모든 Pod 가 동시에 실패한다
        2. 모두 재시작한다
        3. DB 는 여전히 죽어 있으니 또 실패한다 → CrashLoopBackOff
        4. DB 가 살아나는 순간 모든 Pod 가 동시에 커넥션을 만든다
        5. 커넥션 폭주로 DB 가 다시 죽는다

      재시작이 해결할 수 없는 문제에 재시작을 붙이면 상황만 나빠진다

    이 핸들러 안에서 아무것도 조회하지 않는다
      DB 커넥션도 잡지 않고 Redis 도 부르지 않는다
      → 이 핸들러 자체가 지연 요인이 되면 안 된다
      → 응답이 느려지는 것만으로도 liveness 는 실패한다
         (이벤트 루프가 막히면 여기도 응답을 못 한다. 그게 잡아야 할 상황이다)
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, object]:
    """readinessProbe 용.

    판단은 runtime.RuntimeState.is_ready() 가 한다. 거기 근거를 적어뒀다

      들어가는 것    초기화가 끝났는가 / 종료 절차에 들어갔는가
      안 들어가는 것  지금 DB·Redis 가 살아 있는가

    DB 가 죽어도 200 을 준다
      → 이상해 보이지만 의도한 것이다
      → 캐시로 처리할 수 있는 조회를 살리기 위해서다 (00 문서 경로 1)
      → DB 장애는 dependency_up 지표로 알린다 (05 문서)
    """
    state = _state(request)
    is_ready = state.is_ready()

    # readiness 를 지표로도 낸다
    #   probe 결과는 Kubernetes 안에만 남는다
    #   지표로 내보내면 "언제부터 안 준비됐나" 를 시계열로 볼 수 있다
    metrics.app_ready.set(1 if is_ready else 0)

    if is_ready:
        return {"status": "ok"}

    response.status_code = 503
    return {
        "status": "unavailable",
        "reason": state.not_ready_reason(),
    }


@router.get("/deps")
async def deps(request: Request) -> dict[str, object]:
    """운영자와 디버깅용. 의존 서비스 상태를 보여준다.

    항상 200 을 준다                                     ★ 04 문서
      이건 "상태 보고" 지 "판단" 이 아니다
      503 을 주면 누군가 probe 에 갖다 쓴다
      → 그러면 3절에서 막으려던 문제가 그대로 생긴다

    Kubernetes 는 이 엔드포인트를 모른다. 사람이 본다
    """
    state = _state(request)
    now = time.time()

    async def _probe(name: str, checker) -> dict[str, object]:
        started = time.perf_counter()
        ok = await checker()
        elapsed = time.perf_counter() - started
        metrics.dependency_check_duration_seconds.labels(name=name).observe(elapsed)
        metrics.set_dependency_up(name, ok)
        return {"latency_ms": round(elapsed * 1000, 2)}

    pg_extra = await _probe("postgres", state.deps.check_postgres)
    redis_extra = await _probe("redis", state.deps.check_redis)

    pg = state.deps.postgres
    rd = state.deps.redis

    return {
        "component": state.component,
        "uptime_seconds": round(state.uptime_seconds, 1),
        "ready": state.is_ready(),
        "shutting_down": state.shutting_down,
        "dependencies": {
            "postgres": {
                "up": pg.up,
                "initialized": pg.initialized,
                "last_error": pg.last_error,
                "seconds_since_change": round(now - pg.last_change, 1),
                **pg_extra,
            },
            "redis": {
                "up": rd.up,
                "initialized": rd.initialized,
                "last_error": rd.last_error,
                "seconds_since_change": round(now - rd.last_change, 1),
                **redis_extra,
            },
        },
        "db_pool": state.deps.pool_stats(),
    }

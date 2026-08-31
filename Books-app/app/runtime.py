"""프로세스가 살아 있는 동안 유지되는 상태.

왜 따로 파일을 두는가
  health.py 와 debug.py 가 같은 값을 봐야 한다
    /debug/ready 로 readiness 를 강제로 끄면 → /health/ready 가 실패해야 한다
  서로를 직접 import 하면 순환 참조가 생긴다
  → 둘 다 이 파일만 본다

왜 전역 변수가 아니라 객체인가
  전역이면 테스트할 때 초기화가 어렵다
  main.py 가 만들어 app.state 에 넣고, 필요한 곳이 꺼내 쓴다
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import Settings
from app.deps import Dependencies


@dataclass
class RuntimeState:
    settings: Settings
    deps: Dependencies
    component: str                       # "api" | "worker"
    started_at: float = field(default_factory=time.time)

    # SIGTERM 을 받으면 True 가 된다                      ★ 02·04 문서
    #
    # 왜 필요한가
    #   Pod 를 지울 때 두 가지가 동시에 일어난다
    #     kubelet 이 SIGTERM 을 보낸다
    #     EndpointSlice 에서 그 Pod 를 뺀다
    #   그런데 규칙이 모든 노드에 퍼지는 데 시간이 걸린다
    #   → 그 사이에 들어온 요청이 죽어가는 Pod 로 간다
    #
    #   → SIGTERM 을 받자마자 readiness 를 끄고 잠시 기다린다
    #   → 04편에서 실측한 "반영 지연" 을 그 대기 시간으로 흡수한다
    shutting_down: bool = False

    # 장애 주입으로 readiness 를 강제로 끈 상태            (06 문서)
    #   None   정상 판단
    #   False  강제 실패
    ready_override: bool | None = None

    def is_ready(self) -> bool:
        """readiness 판단.                                ★★ 04 문서

        여기 들어가는 것
          초기화가 끝났는가          한 번이라도 DB 에 붙었는가
          종료 절차에 들어갔는가

        여기 들어가지 않는 것
          지금 이 순간 DB 가 살아 있는가
          지금 이 순간 Redis 가 살아 있는가

        왜 런타임 의존성 상태를 안 넣는가
          DB 는 모든 Pod 가 공유한다
          → 죽으면 모든 Pod 가 동시에 readiness 실패
          → 전부 Endpoint 에서 빠진다 → 보낼 곳이 없다
          → 캐시로 처리할 수 있던 조회까지 못 하게 된다
          → 부분 장애를 전체 장애로 만드는 셈이다

          대신 dependency_up 지표로 알린다 (05 문서)
        """
        if self.ready_override is not None:
            return self.ready_override
        if self.shutting_down:
            return False
        # 기동 실패는 "이 Pod 만의 문제" 다 → 롤링업데이트를 멈춰야 한다
        return self.deps.postgres.initialized

    def not_ready_reason(self) -> str | None:
        if self.ready_override is False:
            return "debug_override"
        if self.shutting_down:
            return "shutting_down"
        if not self.deps.postgres.initialized:
            return "not_initialized"
        return None

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

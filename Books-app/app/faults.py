"""장애 주입 상태를 담는 곳.

06-fault-injection.md 의 설계를 상태 모델로 옮긴 것이다.
HTTP 엔드포인트(/debug/*)는 debug.py 가 만든다. 여기는 상태만 관리한다.

왜 나눴는가
  cache.py, db.py, 라우터가 이 상태를 참조해야 한다
  debug.py 를 직접 import 하면 순환 참조가 생긴다
  → 상태만 별도 파일로 뺀다

핵심 설계 두 가지                                        ★ 06 문서

  [Pod 로컬 메모리에만 둔다]
    Redis 같은 공유 저장소에 두면 한 번 켜서 모든 Pod 가 고장난다
    → 전체 장애만 만들 수 있다
    Pod 로컬이면 3개 중 하나만 고장낼 수 있다
    → "일부 요청만 느리다" 를 만들 수 있다
    → 실무에서 제일 흔하고 제일 찾기 어려운 상황이다

  [TTL 로 자동 만료시킨다]
    실험하다 잊는다. 사람은 잊는다
    → 만료가 없으면 다음 실험 결과가 오염된다
    → 최악은 그대로 배포되는 것이다
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from app import metrics


@dataclass
class Injection:
    kind: str
    params: dict[str, Any]
    expires_at: float | None      # None 이면 무한. 명시적으로만 가능하다

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def remaining(self, now: float) -> float | None:
        if self.expires_at is None:
            return None
        return max(0.0, self.expires_at - now)


class FaultRegistry:
    """지금 무엇이 주입돼 있는지 들고 있다.

    조회는 매 요청마다 일어난다. 그래서 아주 가벼워야 한다
      → 딕셔너리 조회 한 번 + 만료 시각 비교뿐이다
      → 잠금(lock)을 쓰지 않는다. 단일 이벤트 루프라 경합이 없다
    """

    def __init__(self, *, default_ttl_seconds: int) -> None:
        self._items: dict[str, Injection] = {}
        self.default_ttl_seconds = default_ttl_seconds

    # ── 조작 ────────────────────────────────────────────

    def set(
        self,
        kind: str,
        params: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> Injection:
        # ttl_seconds 를 안 주면 기본값을 쓴다. 0 이면 무한(명시적 선택)
        if ttl_seconds is None:
            ttl_seconds = self.default_ttl_seconds
        expires_at = None if ttl_seconds == 0 else time.time() + ttl_seconds

        injection = Injection(kind=kind, params=params, expires_at=expires_at)
        self._items[kind] = injection
        metrics.debug_injection_active.labels(kind=kind).set(1)
        return injection

    def clear(self, kind: str) -> bool:
        if kind in self._items:
            del self._items[kind]
            metrics.debug_injection_active.labels(kind=kind).set(0)
            return True
        return False

    def clear_all(self) -> int:
        count = len(self._items)
        for kind in list(self._items):
            self.clear(kind)
        return count

    # ── 조회 ────────────────────────────────────────────

    def get(self, kind: str) -> Injection | None:
        injection = self._items.get(kind)
        if injection is None:
            return None
        if injection.is_expired(time.time()):
            # 만료된 건 조회할 때 치운다.
            # 별도 청소 작업을 두지 않으려는 것이다
            #   → 다만 요청이 아예 안 오면 지표가 1로 남는다
            #   → 그래서 purge_expired() 를 배경 작업에서도 부른다
            self.clear(kind)
            return None
        return injection

    def purge_expired(self) -> int:
        """만료된 것을 치운다. 배경 작업이 주기적으로 부른다.

        요청이 없는 동안에도 debug_injection_active 지표가
        정확하도록 유지하기 위한 것이다
        """
        now = time.time()
        expired = [k for k, v in self._items.items() if v.is_expired(now)]
        for kind in expired:
            self.clear(kind)
        return len(expired)

    def snapshot(self) -> list[dict[str, Any]]:
        """GET /debug/state 응답용."""
        now = time.time()
        result = []
        for injection in self._items.values():
            if injection.is_expired(now):
                continue
            result.append(
                {
                    "kind": injection.kind,
                    "params": injection.params,
                    "expires_in": injection.remaining(now),
                }
            )
        return result

    # ── 각 주입의 해석 ───────────────────────────────────
    #
    # 소비하는 쪽(cache.py, db.py, 미들웨어)이 쓰기 쉽게
    # 여기서 의미 있는 값으로 바꿔준다

    def redis_mode(self) -> str | None:
        """Redis 장애 흉내.  None | "error" | "slow" """
        injection = self.get("break-redis")
        if injection is None:
            return None
        mode = injection.params.get("mode", "error")
        return mode if mode in ("error", "slow") else "error"

    def redis_slow_seconds(self) -> float:
        injection = self.get("break-redis")
        if injection is None:
            return 0.0
        return float(injection.params.get("seconds", 1.0))

    def db_slow_seconds(self) -> float:
        """느린 쿼리 흉내. pg_sleep 에 넘길 초.

        앱에서 time.sleep 을 하지 않는 이유                ★ 06 문서
          그건 DB 커넥션을 안 잡는다 → 풀이 안 찬다
          → "DB 가 느려서 풀이 고갈되는" 연쇄를 못 만든다
        """
        injection = self.get("slow-query")
        if injection is None:
            return 0.0
        return float(injection.params.get("seconds", 1.0))

    def should_inject_latency(self) -> float:
        """확률적 지연. 반환값은 잘 시간(초). 0 이면 안 함.

        10% 의 요청만 느리게 만들 수 있다
          → p50 은 멀쩡한데 p95 만 나쁜 상황을 재현한다
          → 평균만 보는 대시보드가 못 잡는 상황이다
          → Histogram 을 고른 이유를 검증한다 (05 문서)
        """
        injection = self.get("latency")
        if injection is None:
            return 0.0
        ratio = float(injection.params.get("ratio", 1.0))
        if random.random() >= ratio:
            return 0.0
        return float(injection.params.get("ms", 0)) / 1000.0

    def should_inject_error(self) -> bool:
        """확률적 에러. 5% 만 실패하는 상황을 만든다."""
        injection = self.get("error-rate")
        if injection is None:
            return False
        ratio = float(injection.params.get("ratio", 0.0))
        return random.random() < ratio

    def error_status(self) -> int:
        injection = self.get("error-rate")
        if injection is None:
            return 500
        return int(injection.params.get("status", 500))

    def worker_extra_seconds(self) -> float:
        injection = self.get("worker-slow")
        if injection is None:
            return 0.0
        return float(injection.params.get("seconds", 0.0))


# main.py 가 만들어 app.state 에 넣는다.
# 전역 변수로 두지 않는 이유는 runtime.py 와 같다 — 테스트에서 초기화가 어렵다

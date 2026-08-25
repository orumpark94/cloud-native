"""환경변수를 읽고 검증한다.

02-cloud-portability.md 의 두 규칙을 코드로 옮긴 것이다.

  1. 모든 설정을 환경변수로 받는다. 필수 값에는 기본값을 두지 않는다
  2. 기동 시점에 검증하고, 틀리면 즉시 죽는다

왜 즉시 죽는 게 나은가
  Kubernetes 는 죽으면 다시 띄운다 → CrashLoopBackOff 가 되고 이유가 로그에 남는다
  반대로 "일단 뜨고 요청이 올 때 실패" 하면
    Pod 는 Running, readiness 도 통과할 수 있다
    사용자만 500 을 받는다  ← 2단계에서 네 번 겪은 "조용한 실패" 다
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """설정이 잘못됐을 때. 기동을 멈춘다."""


# ─────────────────────────────────────────────────────────────
# 환경변수를 읽는 도우미
#
# 에러를 하나씩 던지지 않고 모아서 한 번에 보고한다.
#   하나씩 던지면 → 고치고 재시작 → 또 다른 에러 → 반복
#   모아서 보고하면 → 한 번에 다 고친다
# ─────────────────────────────────────────────────────────────


class _Loader:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def required(self, name: str) -> str:
        """없으면 기록하고 빈 문자열을 돌려준다. 기본값을 두지 않는다."""
        value = os.getenv(name, "").strip()
        if not value:
            self.errors.append(f"{name} 이(가) 없다. 필수 값이다")
            return ""
        return value

    def string(self, name: str, default: str) -> str:
        return os.getenv(name, default).strip() or default

    def integer(self, name: str, default: int, *, minimum: int | None = None) -> int:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            self.errors.append(f"{name}='{raw}' 는 정수가 아니다")
            return default
        if minimum is not None and value < minimum:
            self.errors.append(f"{name}={value} 는 {minimum} 이상이어야 한다")
            return default
        return value

    def number(self, name: str, default: float, *, minimum: float | None = None) -> float:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            self.errors.append(f"{name}='{raw}' 는 숫자가 아니다")
            return default
        if minimum is not None and value < minimum:
            self.errors.append(f"{name}={value} 는 {minimum} 이상이어야 한다")
            return default
        return value

    def boolean(self, name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        normalized = raw.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        self.errors.append(
            f"{name}='{raw}' 는 참/거짓이 아니다 (true|false|1|0|yes|no|on|off)"
        )
        return default


@dataclass(frozen=True)
class Settings:
    """읽기 전용 설정.

    frozen=True 로 둔 이유
      기동 후에 누가 설정을 바꾸면 그 시점부터 동작이 달라진다
      → 재현이 안 되는 버그가 된다
      → 아예 못 바꾸게 막는다
    """

    # ── 필수. 기본값이 없다 ─────────────────────────────
    # URL 을 통째로 받는다. sslmode / rediss 로 TLS 여부까지 환경변수가 결정한다
    # → 로컬(평문)과 RDS(TLS)에서 같은 코드가 돈다  (02 문서)
    database_url: str
    redis_url: str

    # ── 서버 ────────────────────────────────────────────
    app_port: int          # 서비스 포트. Service 와 Ingress 가 연결된다
    admin_port: int        # 관리 포트. /metrics, /health/*, /debug/*  (Service 에 안 넣는다)
    log_level: str

    # ── 캐시 ────────────────────────────────────────────
    cache_ttl_seconds: int

    # ── DB 커넥션 풀 ────────────────────────────────────
    # 05 문서의 db_pool_* 지표가 여기서 나온다
    db_pool_min: int
    db_pool_max: int
    db_connect_timeout: float

    # ── 큐 / Worker ─────────────────────────────────────
    queue_name: str
    worker_poll_timeout: float        # 큐를 기다리는 시간. 이 주기로 살아있음을 갱신한다
    worker_process_seconds: float     # 결제 처리 흉내. 6단계에서 늘려 큐를 밀리게 한다
    worker_failure_rate: float        # 실패 확률 흉내. 보상 트랜잭션을 시험한다

    # ── 종료 ────────────────────────────────────────────
    shutdown_grace_seconds: float     # SIGTERM 후 readiness 를 끄고 기다리는 시간

    # ── 장애 주입 ───────────────────────────────────────
    enable_debug_endpoints: bool      # 기본 꺼짐 (06 문서 안전장치 겹 1)
    debug_default_ttl_seconds: int    # 주입 자동 만료 (겹 3)

    # ── 자기 신원. Downward API 로 받는다 ────────────────
    # 05 문서 — app_info 지표에만 담는다. 모든 지표의 라벨로 넣지 않는다
    pod_name: str
    node_name: str
    namespace: str
    app_version: str

    @property
    def is_worker_failure_enabled(self) -> bool:
        return self.worker_failure_rate > 0.0


def load_settings() -> Settings:
    """환경변수를 읽어 Settings 를 만든다. 문제가 있으면 ConfigError 를 던진다."""
    loader = _Loader()

    settings = Settings(
        # 필수
        database_url=loader.required("DATABASE_URL"),
        redis_url=loader.required("REDIS_URL"),
        # 서버
        app_port=loader.integer("APP_PORT", 8000, minimum=1),
        admin_port=loader.integer("ADMIN_PORT", 9000, minimum=1),
        log_level=loader.string("LOG_LEVEL", "info").lower(),
        # 캐시
        cache_ttl_seconds=loader.integer("CACHE_TTL_SECONDS", 60, minimum=0),
        # 커넥션 풀
        db_pool_min=loader.integer("DB_POOL_MIN", 2, minimum=0),
        db_pool_max=loader.integer("DB_POOL_MAX", 10, minimum=1),
        db_connect_timeout=loader.number("DB_CONNECT_TIMEOUT", 5.0, minimum=0.1),
        # 큐 / Worker
        queue_name=loader.string("QUEUE_NAME", "order_queue"),
        worker_poll_timeout=loader.number("WORKER_POLL_TIMEOUT", 5.0, minimum=0.1),
        worker_process_seconds=loader.number("WORKER_PROCESS_SECONDS", 1.0, minimum=0.0),
        worker_failure_rate=loader.number("WORKER_FAILURE_RATE", 0.0, minimum=0.0),
        # 종료
        shutdown_grace_seconds=loader.number("SHUTDOWN_GRACE_SECONDS", 5.0, minimum=0.0),
        # 장애 주입
        enable_debug_endpoints=loader.boolean("ENABLE_DEBUG_ENDPOINTS", False),
        debug_default_ttl_seconds=loader.integer("DEBUG_DEFAULT_TTL_SECONDS", 300, minimum=1),
        # 신원
        pod_name=loader.string("POD_NAME", "unknown"),
        node_name=loader.string("NODE_NAME", "unknown"),
        namespace=loader.string("POD_NAMESPACE", "unknown"),
        app_version=loader.string("APP_VERSION", "dev"),
    )

    # 값 사이의 관계도 검증한다. 개별 값만 보면 못 잡는 것들이다
    if settings.db_pool_min > settings.db_pool_max:
        loader.errors.append(
            f"DB_POOL_MIN({settings.db_pool_min}) 이 DB_POOL_MAX({settings.db_pool_max}) 보다 크다"
        )
    if settings.worker_failure_rate > 1.0:
        loader.errors.append(
            f"WORKER_FAILURE_RATE({settings.worker_failure_rate}) 는 0.0~1.0 이어야 한다"
        )
    if settings.app_port == settings.admin_port:
        loader.errors.append(
            f"APP_PORT 와 ADMIN_PORT 가 같다({settings.app_port}). "
            "관리 포트는 따로 열어야 한다 (04·06 문서)"
        )

    if loader.errors:
        lines = "\n".join(f"  - {message}" for message in loader.errors)
        raise ConfigError(f"설정이 잘못됐다. 기동을 멈춘다:\n{lines}")

    return settings


def redact_url(url: str) -> str:
    """URL 에서 비밀번호를 가린다. 로그에 그대로 찍으면 안 된다.

    postgresql://user:secret@host:5432/db  →  postgresql://user:***@host:5432/db
    """
    if "@" not in url:
        return url
    scheme_sep = "://"
    if scheme_sep not in url:
        return url
    scheme, rest = url.split(scheme_sep, 1)
    credentials, _, location = rest.partition("@")
    if ":" not in credentials:
        return url
    user, _, _password = credentials.partition(":")
    return f"{scheme}{scheme_sep}{user}:***@{location}"

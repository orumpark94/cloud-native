"""Prometheus 지표.

05-metrics.md 에서 설계한 것을 실제로 만든다.

이 파일을 읽는 관점
  "무엇을 재는가" 가 아니라 "무엇을 봐야 문제를 알 수 있는가" 로 짰다
  3개월 뒤 새벽에 장애를 보는 사람이 이 지표로 원인을 좁힐 수 있어야 한다

여기서 지키는 규칙

  [카디널리티]  라벨에 무한히 늘어나는 값을 넣지 않는다
                book_id / order_id / request_id / IP / 에러 메시지 원문 → 금지
                path 는 반드시 패턴으로 ("/books/{id}")
                → 개별 건은 로그의 몫이다 (logging_setup.py)

  [Histogram]   Summary 를 쓰지 않는다
                Pod 가 여러 개면 앱이 계산한 분위수를 합칠 수 없다
                → 앱은 구간별 개수만 내고 Prometheus 가 계산한다

  [pod 라벨]    모든 지표에 넣지 않는다
                Prometheus 가 스크레이프할 때 자동으로 붙인다
                → app_info 에만 담고 필요할 때 조인한다

  [없는 것]     "마지막 성공 시각" 을 지표로 둔다
                지표가 0이면 알 수 있지만, 지표가 아예 없으면 알람이 안 울린다
                → time() - 값 으로 "안 돌고 있음" 을 잡는다

[주의 — uvicorn 워커를 여러 개 두면]
  prometheus_client 는 프로세스마다 따로 센다
  → 워커 2개면 값이 반씩 나뉜다
  → 우리는 워커를 1로 두고 Pod 수로 늘린다 (07 문서)
     여러 워커가 필요하면 multiprocess 모드를 켜야 한다
"""

from __future__ import annotations

import time

# ★ 본문을 만드는 함수와 Content-Type 을 같은 곳에서 가져온다   (5단계 Phase 3)
#
#   전에는 이랬다
#     from prometheus_client import generate_latest                        평문 본문
#     from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST
#                                                          OpenMetrics 이름표
#
#   → 본문은 평문인데 "나는 OpenMetrics 다" 라고 알려주고 있었다
#   → OpenMetrics 규격은 본문이 반드시 "# EOF" 로 끝나야 한다
#     (잘린 응답을 구별하기 위한 종료 표시다. 평문에는 없다)
#   → Prometheus 가 이름표를 믿고 OpenMetrics 파서를 켰다가 실패했다
#        scrape 실패:  data does not end with # EOF
#
#   사람이 curl 로 볼 때는 안 드러난다. Content-Type 을 안 보니까
#   → Prometheus 를 처음 붙인 지금에서야 발견됐다
#
#   [OpenMetrics 로 가려면 둘 다 openmetrics.exposition 에서 가져온다]
#     exemplar(트레이스 ID 연결)를 쓸 수 있다
#     대신 카운터마다 _created 시계열이 하나씩 더 생긴다
#     → 지금은 그 기능이 필요 없어서 평문으로 통일한다
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

# 기본 레지스트리를 그대로 쓴다.
# 별도 레지스트리를 만들면 파이썬 런타임 지표(GC, 메모리 등)가 빠진다
# → 그것도 6단계에서 필요하다 (메모리 누수 실험)
REGISTRY = CollectorRegistry(auto_describe=True)


# ─────────────────────────────────────────────────────────────
# Histogram 구간
#
# 기본값을 그대로 쓰지 않는다. 우리 응답 시간 분포에 맞춰 잡는다
#   캐시 적중은 수 ms       → 앞쪽이 촘촘해야 한다
#   DB 조회는 수십 ms
#   느린 쿼리는 수 초        → 뒤쪽도 있어야 한다
#
# 버킷 하나가 시계열 하나다. 많이 잡으면 저장 비용이 는다
# ─────────────────────────────────────────────────────────────

HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# 큐 대기는 훨씬 길 수 있다. 적체되면 분 단위로 간다
QUEUE_WAIT_BUCKETS = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)

# 처리 시간. WORKER_PROCESS_SECONDS 로 조절하며 실험한다
PROCESS_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

DEP_CHECK_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)


# ─────────────────────────────────────────────────────────────
# 1. 공통 — RED (Rate / Errors / Duration)
#
# 업계에서 RED 메서드라고 부르는 것이다 (Grafana 의 Tom Wilkie)
#   Rate      초당 요청 수      → http_requests_total 의 rate()
#   Errors    실패 비율         → status / error_code 로 나눈다
#   Duration  응답 시간 분포     → Histogram
#
# route_class 라벨이 이 앱의 핵심이다                    ★ 00 문서
#   없으면   "요청이 느려졌다"
#   있으면   "조회는 멀쩡한데 주문만 느려졌다"
# ─────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 요청 수",
    ["method", "path", "status", "route_class"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 응답 시간",
    ["method", "path", "route_class"],
    buckets=HTTP_BUCKETS,
    registry=REGISTRY,
)

http_errors_total = Counter(
    "http_errors_total",
    "에러 코드별 실패 수",
    ["error_code", "route_class"],
    registry=REGISTRY,
)

http_requests_in_flight = Gauge(
    "http_requests_in_flight",
    "지금 처리 중인 요청 수",
    ["route_class"],
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 2. 경로 1 — 읽기와 캐시
#
# 연쇄 장애가 여기서 시작된다                            ★ 00 문서
#   Redis 죽음 → cache miss 급증 → DB 로 몰림 → 풀 고갈 → 503
#   이 다섯 개를 한 화면에 놓는 게 5단계 대시보드의 목표다
# ─────────────────────────────────────────────────────────────

cache_operations_total = Counter(
    "cache_operations_total",
    "캐시 조회 결과",
    ["result"],          # hit | miss | error
    registry=REGISTRY,
)

cache_operation_duration_seconds = Histogram(
    "cache_operation_duration_seconds",
    "캐시 응답 시간",
    ["operation"],       # get | set
    buckets=DEP_CHECK_BUCKETS,
    registry=REGISTRY,
)

# offset 을 구간으로 묶어 라벨에 넣는다                   ★ 01 문서
#   OFFSET 이 커지면 DB 가 앞의 행을 세면서 지나간다 → 느려진다
#   그런데 offset 값을 그대로 라벨에 넣으면 무한하다
#   → 구간으로 묶으면 유한해진다
db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "DB 쿼리 시간",
    ["query", "offset_bucket"],
    buckets=HTTP_BUCKETS,
    registry=REGISTRY,
)


def offset_bucket(offset: int) -> str:
    """offset 을 유한한 구간 이름으로 바꾼다."""
    if offset < 100:
        return "0-100"
    if offset < 1_000:
        return "100-1k"
    if offset < 10_000:
        return "1k-10k"
    return "10k+"


# ─────────────────────────────────────────────────────────────
# 3. 경로 2 — 동기 쓰기와 재고
# ─────────────────────────────────────────────────────────────

orders_created_total = Counter(
    "orders_created_total",
    "주문 접수 결과",
    ["result"],          # accepted | out_of_stock | error
    registry=REGISTRY,
)

db_transaction_duration_seconds = Histogram(
    "db_transaction_duration_seconds",
    "주문 트랜잭션 시간",
    buckets=HTTP_BUCKETS,
    registry=REGISTRY,
)

# ★★ "성공했지만 잘못된" 것을 세는 지표
#
# 재고가 -1 이 되어도 요청은 200 을 준다. 지표에는 성공으로 찍힌다
#   Pod        Running    정상
#   probe      통과       정상
#   응답        200 OK     정상
#   그런데 데이터가 깨졌다
#
# 아무 층도 이걸 자동으로 안 잡아준다. 우리가 세야 한다
# → 이 값이 0보다 크면 즉시 알람 (05 문서)
books_stock_negative_total = Counter(
    "books_stock_negative_total",
    "재고 차감 후 음수가 된 횟수. 0이어야 한다",
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 4. 경로 3 — 큐와 Worker
#
# 큐 길이만 보면 안 된다                                 ★ 05 문서
#   100개가 쌓였다 → 문제인가?
#   Worker 가 초당 1000개를 처리하면 0.1초면 없어진다
#   Worker 가 초당 1개면 100초 걸린다
#
#   → 입력 속도 / 소비 속도 / 대기 시간을 같이 본다
# ─────────────────────────────────────────────────────────────

queue_length = Gauge(
    "queue_length",
    "큐에 쌓인 작업 수",
    registry=REGISTRY,
)

queue_enqueued_total = Counter(
    "queue_enqueued_total",
    "큐에 넣은 수",
    registry=REGISTRY,
)

queue_dequeued_total = Counter(
    "queue_dequeued_total",
    "큐에서 꺼낸 수",
    registry=REGISTRY,
)

# 대기 시간과 처리 시간을 반드시 나눈다                   ★ 03 문서
#   하나로 뭉치면 "5초 걸렸다" 만 안다
#   큐에서 4.9초 기다린 건지, 처리가 4.9초 걸린 건지 모른다
#
#   wait 이 길다     → Worker 가 부족하다. 늘려야 한다
#   process 가 길다  → 처리 로직이나 DB 가 느리다. 늘려도 소용없다
#   → 대응이 정반대다
order_queue_wait_seconds = Histogram(
    "order_queue_wait_seconds",
    "큐에서 기다린 시간 (created → started)",
    buckets=QUEUE_WAIT_BUCKETS,
    registry=REGISTRY,
)

order_process_duration_seconds = Histogram(
    "order_process_duration_seconds",
    "실제 처리 시간 (started → finished)",
    buckets=PROCESS_BUCKETS,
    registry=REGISTRY,
)

orders_processed_total = Counter(
    "orders_processed_total",
    "Worker 처리 결과",
    ["result"],          # completed | failed | orphaned
    registry=REGISTRY,
)

# ★★ "없는 것" 을 잡는 지표
#
# Worker 프로세스는 살아 있는데 큐를 안 본다 → 큐만 쌓인다
# Kubernetes 는 정상으로 본다 (12편의 "DESIRED 0인데 지표 정상" 과 같은 성격)
#
# 절대값이 아니라 "마지막으로 확인한 시각" 을 둔다
#   time() - 값 > 60   → 60초 넘게 큐를 안 봤다
#   absent(...)        → Worker Pod 가 아예 없다
#
# 큐가 비어서 대기 중인 것과 멈춘 것을 구분할 수 있다
#   대기 중이어도 주기적으로 확인은 하므로 이 값이 계속 갱신된다
worker_last_poll_timestamp_seconds = Gauge(
    "worker_last_poll_timestamp_seconds",
    "Worker 가 마지막으로 큐를 확인한 시각 (unix time)",
    registry=REGISTRY,
)

worker_in_flight = Gauge(
    "worker_in_flight",
    "Worker 가 지금 처리 중인 작업 수",
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 5. 의존 서비스
#
# 04 문서에서 readiness 에 DB 를 넣지 않기로 했다
# → 그럼 DB 장애를 무엇으로 아는가 → 이 지표다
# ─────────────────────────────────────────────────────────────

dependency_up = Gauge(
    "dependency_up",
    "의존 서비스가 살아 있는가 (1=정상, 0=장애)",
    ["name"],            # postgres | redis
    registry=REGISTRY,
)

dependency_check_duration_seconds = Histogram(
    "dependency_check_duration_seconds",
    "의존 서비스 확인에 걸린 시간",
    ["name"],
    buckets=DEP_CHECK_BUCKETS,
    registry=REGISTRY,
)

dependency_errors_total = Counter(
    "dependency_errors_total",
    "의존 서비스 오류 수",
    ["name", "kind"],    # kind = timeout | refused | other
    registry=REGISTRY,
)

# 커넥션 풀
#
# 연쇄 장애에서 이게 핵심이다                            ★ 05·06 문서
#   Redis 죽음 → 캐시 미스 → DB 로 몰림 → 풀 고갈
#   → db_pool_wait_seconds 가 먼저 오른다
#   → 그다음 503 이 나온다
#   → 즉 503 보다 먼저 경고할 수 있다
db_pool_size = Gauge("db_pool_size", "커넥션 풀 크기", registry=REGISTRY)
db_pool_available = Gauge("db_pool_available", "지금 쓸 수 있는 커넥션 수", registry=REGISTRY)
db_pool_waiting = Gauge("db_pool_waiting", "커넥션을 기다리는 요청 수", registry=REGISTRY)

db_pool_wait_seconds = Histogram(
    "db_pool_wait_seconds",
    "커넥션을 기다린 시간",
    buckets=DEP_CHECK_BUCKETS,
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 6. 앱 자신에 대한 정보
#
# pod / node 를 여기에만 담는다                          ★ 05 문서
#   모든 지표에 pod 라벨을 넣으면 카디널리티가 배로 는다
#   Prometheus 가 스크레이프할 때 자동으로 붙여주기도 한다
#   → 필요할 때 app_info 와 조인한다
# ─────────────────────────────────────────────────────────────

app_info = Info(
    "app",
    "애플리케이션 정보",
    registry=REGISTRY,
)

app_start_time_seconds = Gauge(
    "app_start_time_seconds",
    "프로세스 시작 시각 (unix time)",
    registry=REGISTRY,
)

app_ready = Gauge(
    "app_ready",
    "readiness 상태 (1=준비됨, 0=아님)",
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 7. 장애 주입 상태
#
# 06 문서의 안전장치 겹 4
#   "실수로 켠 채 두면 사고다" 를 지표로 잡는다
#   → 프로덕션에서 이 값이 1이면 즉시 알람
# ─────────────────────────────────────────────────────────────

debug_endpoints_enabled = Gauge(
    "debug_endpoints_enabled",
    "장애 주입 엔드포인트가 켜져 있는가 (1=켜짐)",
    registry=REGISTRY,
)

debug_injection_active = Gauge(
    "debug_injection_active",
    "지금 주입 중인 장애 (1=활성)",
    ["kind"],
    registry=REGISTRY,
)


# ─────────────────────────────────────────────────────────────
# 도우미
# ─────────────────────────────────────────────────────────────


def init_app_info(
    *,
    version: str,
    pod: str,
    node: str,
    namespace: str,
    component: str,
    debug_enabled: bool,
) -> None:
    """기동할 때 한 번 부른다."""
    app_info.info(
        {
            "version": version,
            "pod": pod,
            "node": node,
            "namespace": namespace,
            "component": component,     # api | worker
        }
    )
    app_start_time_seconds.set(time.time())
    debug_endpoints_enabled.set(1 if debug_enabled else 0)


def set_dependency_up(name: str, up: bool) -> None:
    dependency_up.labels(name=name).set(1 if up else 0)


def set_pool_stats(*, size: int, available: int, waiting: int) -> None:
    db_pool_size.set(size)
    db_pool_available.set(available)
    db_pool_waiting.set(waiting)


def classify_dependency_error(exc: BaseException) -> str:
    """예외를 유한한 종류로 분류한다.

    예외 클래스 이름을 그대로 라벨에 넣으면 종류가 계속 늘어난다
    → 세 가지로 줄인다
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text:
        return "timeout"
    if "refused" in text or "connect" in name:
        return "refused"
    return "other"


def render() -> tuple[bytes, str]:
    """/metrics 응답 본문과 Content-Type 을 만든다."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

# 06. 장애 주입 — 지표가 실제로 움직이는지 확인할 수단

로드맵 3단계 결과물의 마지막 항목이다.

```text
"장애 유발용 테스트 Endpoint 또는 설정"
```

---

## 0. 왜 필요한가

**05 문서에서 지표를 잔뜩 설계했다. 그런데 그게 맞는지 모른다.**

```text
[검증 없이 5단계로 가면]
  Prometheus 를 깔고 대시보드를 만든다
  화면에 선이 그려진다
  → 그게 맞는 값인지 알 수 없다

  장애가 났을 때 그 화면으로 원인을 찾을 수 있는지도 모른다
```

```text
[장애를 만들어보면]
  Redis 를 끊는다 → cache miss 가 정말 오르나
  커넥션을 잡는다 → db_pool_wait_seconds 가 정말 오르나
  → 지표가 쓸모 있는지 3단계에서 확인할 수 있다
```

**"관측 없는 장애 실험은 무의미하다" 의 역도 성립한다.** 검증 없는 관측도 무의미하다.

---

## 1. 두 종류의 장애를 구분한다 ★

**전부 엔드포인트로 만들 필요는 없다.**

```text
[앱 밖에서 만들 수 있는 것 — 엔드포인트 불필요]
  API Pod 장애        kubectl delete pod
  Redis 장애          Redis Pod 를 지운다
  PostgreSQL 장애     StatefulSet 을 지운다
  노드 장애           VM 전원을 내린다 (10편에서 해봤다)
  Queue 적체          Worker replica 를 줄이고 부하를 넣는다
  잘못된 환경변수      ConfigMap 을 고친다 (06편에서 본 그것)
  Worker 처리 지연     환경변수 WORKER_PROCESS_SECONDS 를 늘린다
```

```text
[앱 안에서만 만들 수 있는 것 — 엔드포인트 필요]
  readiness 만 실패시키기         Pod 는 살아 있는데 트래픽만 안 받게
  DB 응답 지연                   DB 는 멀쩡한데 이 쿼리만 느리게
  커넥션 풀 고갈                  DB 는 멀쩡한데 풀만 차게
  메모리 누수                    천천히 늘어나는 상황
  CPU 과부하                     이 Pod 만
  Redis 연결만 끊기               Redis 는 살아 있는데 이 Pod 만 못 붙게
  확률적 에러 / 지연              10% 만 실패하는 상황
```

```text
[구분 기준]
  "일부만 고장난 상태" 를 만들려면 앱 안에서 해야 한다
  통째로 죽이는 건 밖에서 하면 된다
```

**부분 장애가 이 앱의 목표 중 하나다.** 00 문서에서 경로를 셋으로 나눈 이유가 여기서도 쓰인다.

---

## 2. 안전 장치를 먼저 정한다 ★★

**기능보다 이게 먼저다.** 실수로 켠 채 두면 그 자체가 사고다.

### 겹 1 — 환경변수로 끈다

```text
ENABLE_DEBUG_ENDPOINTS = false        기본값
```

```text
[꺼져 있으면]
  라우트 자체를 등록하지 않는다
  → 404 가 난다. "있는데 막혔다" 가 아니라 "없다"
```

```text
[왜 라우트를 아예 안 만드나]
  403 을 주면 "이런 게 있구나" 를 알려주는 셈이다
  → 없는 것처럼 보이는 게 낫다
```

### 겹 2 — 별도 포트로 분리한다

```text
8000   서비스 포트. Service 와 Ingress 가 연결된다
9000   관리 포트. /metrics, /health/deps, /debug/*
```

```text
[Service 에 9000 을 넣지 않는다]
  → 클러스터 밖에서 못 닿는다
  → Prometheus 는 Pod IP 로 직접 긁으므로 문제없다
  → 사람은 kubectl port-forward 로 접근한다
```

**05 문서에서 "/metrics 를 외부에 노출하지 않는다" 고 한 것과 같은 처리다.**

### 겹 3 — 자동 만료

```text
POST /debug/leak { "mb": 100, "ttl_seconds": 300 }
→ 5분 뒤 자동으로 해제된다
```

```text
[왜]
  실험하다 잊는다. 사람은 잊는다
  → 만료가 없으면 다음 실험 결과가 오염된다
  → 최악은 그대로 배포되는 것이다
```

```text
[기본 TTL 을 둔다]
  ttl_seconds 를 안 주면 300초로 잡는다
  무한은 명시적으로만 (ttl_seconds: 0)
```

### 겹 4 — 켜져 있으면 지표로 드러난다

```text
debug_injection_active{kind}     Gauge   1 = 주입 중
debug_endpoints_enabled          Gauge   1 = 엔드포인트가 켜져 있음
```

```text
[알람]
  debug_endpoints_enabled == 1  이 프로덕션 네임스페이스에서 보이면 즉시 알림
  debug_injection_active 가 30분 넘게 1이면 알림
```

**"실수로 켠 채 두면 사고다" 를 지표로 잡는다.** 05 문서의 "없는 것을 잡는 설계" 와 같은 발상이다.

### 겹 5 — 상태 조회와 일괄 해제

```text
GET  /debug/state     지금 무엇이 주입돼 있나
POST /debug/reset     전부 해제한다
```

```text
[왜 필요한가]
  여러 개를 켜놓고 잊는다
  실험 전에 state 를 보고, 실험 후에 reset 을 한다
  → 절차로 만들어두면 사고가 준다
```

---

## 3. Pod 로컬 상태 — 부분 장애의 열쇠 ★

**주입 상태는 각 Pod 의 메모리에만 둔다.**

```text
[공유 저장소(Redis)에 두면]
  한 번 켜면 모든 Pod 가 고장난다
  → 전체 장애만 만들 수 있다

[Pod 로컬이면]
  Pod 3개 중 하나만 고장낼 수 있다
  → "일부 요청만 느리다" 를 만들 수 있다
  → 실무에서 제일 흔하고 제일 찾기 어려운 상황이다
```

### 그런데 특정 Pod 를 어떻게 지목하나

```text
[문제]
  Service 로 요청하면 어느 Pod 에 갈지 모른다
  → 부하분산되므로 지목이 안 된다
```

```text
[방법]
  kubectl port-forward pod/api-xxxxx 9000:9000
  curl -X POST localhost:9000/debug/latency -d '{"ms":500,"ttl_seconds":300}'

  → 그 Pod 에만 적용된다
```

**03편에서 본 그 구조다.** Service 는 대표 주소이고, Pod 를 직접 지목하려면 다른 경로가 필요하다.

```text
[확인]
  적용 후 Service 로 요청을 여러 번 보낸다
  → 3분의 1만 느려야 한다
  → 05 문서의 지표에서 pod 라벨로 구분되는지도 확인한다
```

---

## 4. 엔드포인트 목록

### 4-1. readiness 강제 실패

```text
POST /debug/ready { "ready": false, "ttl_seconds": 300 }
```

```text
[무엇을 확인하나]
  04 문서에서 설계한 readiness 가 실제로 동작하나
  Endpoint 에서 빠지는 데 얼마나 걸리나
  → 04편에서 실측한 "failureThreshold × periodSeconds" 를 다시 확인한다

[움직여야 할 지표]
  kube_endpoint_address_available 감소
  이 Pod 로 오는 요청이 0이 된다
```

```text
[이걸로 만드는 6단계 실험]
  Pod 3개 중 하나만 ready 를 끈다
  → 트래픽이 2개로 몰린다
  → 나머지 둘의 응답 시간이 오르는지 본다
```

### 4-2. DB 응답 지연

```text
POST /debug/slow-query { "seconds": 2, "ttl_seconds": 300 }
```

```text
[구현]
  주입이 켜져 있으면 쿼리 전에 pg_sleep(n) 을 실행한다
  → DB 커넥션을 실제로 붙잡는다        ★ 이게 중요하다
```

```text
[왜 앱에서 time.sleep 하면 안 되나]
  그건 커넥션을 안 잡는다
  → 커넥션 풀이 안 찬다
  → "DB 가 느려서 풀이 고갈되는" 연쇄를 못 만든다
```

```text
[움직여야 할 지표]
  db_transaction_duration_seconds 뒤쪽 버킷
  db_pool_in_use 증가
  http_request_duration_seconds{route_class="read"} 증가
```

### 4-3. 커넥션 풀 고갈

```text
POST /debug/hold-connections { "count": 8, "ttl_seconds": 120 }
```

```text
[구현]
  커넥션 n개를 잡고 안 놓는다
  → 풀 크기가 10이면 8개를 잡아 2개만 남긴다
```

```text
[움직여야 할 지표]
  db_pool_in_use 가 상한 근처
  db_pool_wait_seconds 급증          ← 503 보다 먼저 오른다
  db_pool_timeouts_total 증가
```

**05 문서에서 "503 보다 먼저 경고할 수 있다" 고 한 주장을 여기서 검증한다.**

```text
[04 문서에서 미룬 판단을 여기서 정한다]
  커넥션 풀 고갈을 readiness 에 넣을 것인가
  → 이 엔드포인트로 고갈시켜 보고 6단계에서 결정한다
```

### 4-4. Redis 연결만 끊기

```text
POST /debug/break-redis { "mode": "error" | "slow", "ttl_seconds": 300 }
```

```text
[Redis Pod 를 지우는 것과 다르다]
  Pod 를 지우면 모든 Pod 가 못 붙는다
  이건 이 Pod 만 못 붙는다 → 부분 장애
```

```text
[mode 를 나누는 이유]
  error  즉시 실패한다 → 캐시 미스로 DB 로 간다
  slow   응답이 느리다  → 캐시를 기다리다 전체가 느려진다     ★ 이게 더 위험하다

  "죽은 것보다 느린 것이 더 나쁘다" 를 직접 만들어본다
```

```text
[움직여야 할 지표]
  cache_operations_total{result="error"} 또는 miss 급증
  cache_operation_duration_seconds (slow 모드)
  db_queries_total 증가 → 연쇄 시작
```

### 4-5. 응답 지연 주입 (확률적)

```text
POST /debug/latency { "ms": 500, "ratio": 0.1, "ttl_seconds": 300 }
```

```text
[10% 의 요청만 500ms 느리게]
  → p50 은 멀쩡한데 p95 만 나쁜 상황을 만든다
  → "평균만 보면 문제없어 보이는" 함정을 재현한다
```

```text
[왜 중요한가]
  평균 응답시간만 보는 대시보드는 이걸 못 잡는다
  → 05 문서에서 Histogram 을 고른 이유가 여기서 증명된다
```

### 4-6. 에러율 주입 (확률적)

```text
POST /debug/error-rate { "ratio": 0.05, "status": 500, "ttl_seconds": 300 }
```

```text
[5% 만 실패하는 상황]
  → 사용자 스무 명 중 한 명만 실패한다
  → 로그를 대충 보면 안 보인다. 지표로만 보인다

[움직여야 할 지표]
  http_requests_total{status="500"} 비율
  → 알람 임계값을 정하는 데 쓴다
```

### 4-7. 메모리 누수

```text
POST /debug/leak { "mb": 100, "ttl_seconds": 300 }
```

```text
[구현]
  지정한 크기만큼 메모리를 잡고 전역 리스트에 담아둔다
  → 가비지 컬렉션이 못 가져간다
```

```text
[6단계에서 확인할 것]
  memory limit 을 넘으면 OOMKilled 가 된다
  → Pod 가 재시작된다
  → 00편에서 본 종료 절차와 다르게 SIGKILL 이다. 정리할 시간이 없다
  → 처리 중이던 주문은 어떻게 되나
```

### 4-8. CPU 과부하

```text
POST /debug/burn { "seconds": 30, "threads": 1 }
```

```text
[6단계에서 확인할 것]
  CPU limit 에 닿으면 스로틀링된다. 죽지는 않는다
  → 메모리와 다르다. 메모리는 죽고 CPU 는 느려진다
  → container_cpu_cfs_throttled_seconds_total 이 오른다
```

```text
[Python 주의 — GIL]
  스레드를 늘려도 CPU 코어를 다 못 쓴다
  → 실제로 코어를 태우려면 프로세스를 늘리거나 C 확장을 써야 한다
  → 이것도 실험 결과로 기록한다 (언어 선택의 대가)
```

### 4-9. 상태 조회와 해제

```text
GET  /debug/state
POST /debug/reset
```

```json
{
  "pod": "api-6d4f8b-x7k2p",
  "injections": [
    { "kind": "latency", "params": {"ms":500,"ratio":0.1}, "expires_in": 240 },
    { "kind": "leak",    "params": {"mb":100},             "expires_in": 120 }
  ]
}
```

---

## 5. 로드맵의 12개 장애와 대조

| 로드맵이 요구한 장애 | 만드는 방법 | 엔드포인트 |
|---|---|---|
| API Pod 장애 | `kubectl delete pod` | 불필요 |
| Redis 연결 장애 | Redis Pod 삭제 / `/debug/break-redis` | 부분 장애용으로 필요 |
| PostgreSQL 연결 장애 | StatefulSet 삭제 | 불필요 |
| Worker 처리 지연 | `WORKER_PROCESS_SECONDS` 환경변수 | 불필요 |
| Queue 적체 | Worker replica 축소 + 부하 | 불필요 |
| DB 응답 지연 | `/debug/slow-query` | 필요 |
| Connection Pool 고갈 | `/debug/hold-connections` | 필요 |
| 메모리 누수 | `/debug/leak` | 필요 |
| CPU 과부하 | `/debug/burn` | 필요 |
| 잘못된 환경변수 | ConfigMap 수정 | 불필요 |
| Readiness 실패 | `/debug/ready` | 필요 |
| 부분 장애 | Pod 하나에만 주입 | 위 조합 |

**엔드포인트는 여섯 개면 충분하다.** 나머지는 Kubernetes 조작으로 만든다.

---

## 6. 6단계 시나리오 초안

**연쇄 장애를 순서대로 만든다.**

```text
[시나리오 A — 캐시가 죽어 DB 가 무너진다]
  1. k6 로 정상 부하를 넣는다. 지표를 기록한다        ← 기준선
  2. Redis Pod 를 지운다
  3. 관찰
       cache miss 100%
       db_queries 급증
       db_pool_wait_seconds 상승
       503 증가
  4. 복구하고 회복 곡선을 본다
```

```text
[시나리오 B — 부분 장애를 찾을 수 있는가]
  1. Pod 3개 중 하나에만 /debug/latency ratio=1.0 을 준다
  2. 전체 p95 만 보면 애매하다
  3. pod 라벨로 나눠 보면 하나만 나쁘다
  → "어느 Pod 가 문제인가" 를 지표로 찾는 연습
```

```text
[시나리오 C — 재고가 음수가 된다]
  1. 재고 1권짜리 책에 동시 주문을 넣는다 (03 문서의 1차 SQL)
  2. books_stock_negative_total 이 오르는지 본다
  3. 잠금을 넣고 다시 한다
  4. 처리량이 얼마나 떨어졌는지 비교한다
```

```text
[시나리오 D — 큐가 밀린다]
  1. Worker 를 1개로 줄인다
  2. 부하를 넣는다
  3. 관찰
       주문 접수는 계속 202 를 준다        ← API 는 정상이다
       queue_length 증가
       order_queue_wait_seconds 증가
  → "API 는 정상입니다" 라고 말할 수 있는가?
```

**각 시나리오를 Incident Report 형식으로 남긴다.**

```text
개요 / 영향 범위 / 탐지·복구 시각 / Metrics 변화 / 근본 원인 / 재발 방지
```

---

## 7. 외부 도구와의 관계

```text
[Chaos Mesh / LitmusChaos]
  Kubernetes 수준에서 장애를 주입하는 도구다
    Pod 삭제 / 네트워크 지연 / 패킷 손실 / 디스크 압박 / DNS 오류
```

```text
[역할이 다르다]
  외부 도구   인프라 층 장애        네트워크 / 노드 / Pod
  우리 엔드포인트  애플리케이션 층 장애   커넥션 풀 / 캐시 로직 / 응답 확률
```

```text
[네트워크 지연은 외부 도구가 낫다]
  앱에서 흉내내면 "진짜 네트워크 문제" 가 아니다
  → 6단계에서 여유가 되면 Chaos Mesh 를 붙인다
  → 로드맵에는 없다. 필수는 아니다
```

**우리 엔드포인트가 대체하는 게 아니라 못 하는 영역이 있다는 걸 명시한다.**

---

## 8. 구현 시 주의할 것

```text
[1] 주입 로직이 정상 경로를 느리게 하면 안 된다
    if injection_active: 한 줄이면 된다
    → 매 요청마다 딕셔너리를 뒤지거나 잠금을 잡으면 안 된다

[2] TTL 만료를 백그라운드로 처리한다
    요청이 안 들어오면 만료가 안 되는 구조면 곤란하다

[3] Worker 에도 주입 엔드포인트를 둔다
    04 문서에서 만든 관리 포트에 붙인다
    → 처리 지연 / 실패율 주입

[4] 주입 중에는 로그를 남긴다
    "이 응답이 왜 느렸나" 를 나중에 알 수 있게
    log: {"event":"fault_injected","kind":"latency","request_id":"..."}
```

---

## 정리 — 이 문서에서 내린 판단

```text
1. 전부 엔드포인트로 만들지 않는다 ★
   앱 밖에서 되는 건 kubectl 로 한다
   "일부만 고장난 상태" 만 앱 안에서 만든다
   → 엔드포인트는 여섯 개면 충분하다

2. 안전 장치를 다섯 겹으로 둔다 ★★
   환경변수로 끈다 (라우트를 아예 등록 안 함)
   별도 포트로 분리한다 (Service 에 안 넣는다)
   TTL 로 자동 만료시킨다 (사람은 잊는다)
   켜져 있으면 지표로 드러난다 → 알람
   state 조회와 reset 을 둔다

3. 주입 상태는 Pod 로컬 메모리에 둔다 ★
   공유하면 전체 장애만 만들 수 있다
   Pod 로컬이면 부분 장애를 만들 수 있다
   → port-forward 로 특정 Pod 를 지목한다

4. slow-query 는 앱에서 sleep 하지 않고 pg_sleep 을 쓴다
   커넥션을 실제로 붙잡아야 풀 고갈 연쇄가 재현된다

5. Redis 장애를 error 와 slow 로 나눈다
   "죽은 것보다 느린 것이 더 나쁘다" 를 직접 만들어본다

6. 확률적 주입을 넣는다 (latency ratio / error ratio)
   평균만 보는 대시보드가 못 잡는 상황을 만든다
   → Histogram 을 고른 이유를 검증한다

7. 각 엔드포인트마다 "움직여야 할 지표" 를 미리 적어둔다
   → 05 문서의 설계가 맞는지 3단계에서 검증한다

8. Python GIL 때문에 CPU 주입이 기대와 다를 수 있다
   그것도 실험 결과로 기록한다 (언어 선택의 대가)

9. 외부 Chaos 도구가 못 하는 영역만 우리가 만든다
   네트워크·노드 장애는 도구가 낫다
```

## 다음

```text
07-dockerfile.md   이미지를 만든다
                   ca-certificates 를 넣는다 (02 문서)
                   크기와 빌드 시간을 실측해 기록한다
                   비-root 실행 / 읽기 전용 루트 파일시스템 준비
```

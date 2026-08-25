# 05. Metrics — 무엇을 재야 문제를 알 수 있는가

**2단계에서 네 번 겪은 문제를 앱에서 반복하지 않기 위한 설계다.**

```text
[PV/PVC 편]      볼륨은 Bound 인데 데이터가 없었다
[StatefulSet 편]  Pod 가 6분째 멈춰 있는데 이벤트가 없었다
[DaemonSet 편]    DESIRED 0인데 모든 지표가 정상이었다
[Job 편]          백업이 실패했는데 아무도 안 알려줬다

공통점은 "에러가 안 난다" 는 것이다
```

```text
[이 문서의 목표]
  "무엇을 봐야 이 상황을 알 수 있는가" 를 미리 정한다
  5단계에서 Prometheus 를 깔았을 때 볼 게 있도록
```

---

## 0. 지표의 네 종류

```text
Counter     계속 늘어나기만 한다. 줄지 않는다
            요청 수, 에러 수, 처리한 주문 수

Gauge       올라갔다 내려갔다 한다
            큐 길이, 커넥션 수, 재고

Histogram   값의 분포를 구간별로 센다
            응답 시간, 큐 대기 시간

Summary     비슷하지만 분위수를 앱에서 계산한다
            → 이 프로젝트에서는 쓰지 않는다 (뒤에서 이유)
```

### Histogram 을 쓰고 Summary 를 안 쓰는 이유

```text
[Summary]
  앱이 p95 를 계산해서 내보낸다
  → Pod 3개의 p95 를 합칠 수 없다
     "각각 100ms, 120ms, 90ms 였다" 로는 전체 p95 를 못 구한다

[Histogram]
  앱은 구간별 개수만 낸다
  → Prometheus 가 전체를 합쳐서 p95 를 구한다
```

**Pod 가 여러 개인 이상 Histogram 이어야 한다.**

---

## 1. 라벨 설계가 제일 위험하다 ★★

지표를 만들기 전에 이것부터 정한다. **잘못하면 Prometheus 가 터진다.**

### 시계열 수는 라벨 값의 곱이다

```text
http_requests_total{method, path, status}

  method  5개    (GET, POST, ...)
  path    10개
  status  6개
  → 5 × 10 × 6 = 300개 시계열
```

```text
여기에 book_id 를 넣으면?

  book_id 10만개
  → 300 × 100,000 = 3천만 개 시계열
  → Prometheus 가 메모리를 다 쓰고 죽는다
```

**이걸 카디널리티 폭발(cardinality explosion)이라고 한다.**

### 규칙 — 라벨에 넣으면 안 되는 것

```text
[금지]
  book_id / order_id / user_id      값이 무한히 늘어난다
  request_id                        요청마다 다르다
  이메일 / IP 주소
  에러 메시지 원문                   내용이 매번 다르다
  타임스탬프
```

```text
[허용]
  method / status / path 패턴       값이 유한하고 미리 안다
  error_code                        우리가 정의한 목록 (01 문서)
  경로 분류 (read / write / async)   셋뿐이다
  dependency 이름                    postgres / redis
```

### path 는 반드시 패턴으로 넣는다

```text
[나쁨]  path="/books/1"  /books/2  /books/3 ...    → 책 수만큼 시계열
[좋음]  path="/books/{id}"                          → 하나
```

**FastAPI 는 라우트 패턴을 알고 있으므로 그걸 쓴다.** 실제 URL 을 그대로 넣으면 안 된다.

### 그럼 "책 1번만 느리다" 는 어떻게 아나

```text
지표로는 못 찾는다. 그게 맞다

지표    "어디가 느린가" 를 좁힌다        경로 / 상태 / 의존성 단위
로그    "무엇이 느렸는가" 를 찾는다      request_id / book_id 포함
```

```text
[역할 분담]
  지표는 언제나 집계다. 개별 건은 로그와 추적의 몫이다
  → 01 문서에서 X-Request-Id 를 넣기로 한 이유가 이것이다
```

### 예외 — offset 은 구간으로 넣는다

01 문서에서 "OFFSET 이 커지면 느려진다" 를 실험 재료로 남겼다. 그걸 지표로 보려면 라벨이 필요하다.

```text
[나쁨]  offset="40000"        값이 무한하다
[좋음]  offset_bucket="0-100" | "100-1k" | "1k-10k" | "10k+"      4개
```

**구간으로 묶으면 카디널리티가 유한해진다.**

---

## 2. 공통 지표 — 세 경로를 구분해서 잰다

00 문서에서 경로를 셋으로 나눈 이유가 여기서 쓰인다.

```text
http_requests_total{method, path, status, route_class}
http_request_duration_seconds{method, path, route_class}      Histogram
http_errors_total{error_code, route_class}
```

```text
route_class 값
  read    GET /books, GET /books/{id}, GET /orders/{id}
  write   POST /orders 의 동기 구간
  async   Worker 가 처리하는 구간
```

### 이 라벨 하나가 만드는 차이 ★

```text
[없으면]
  "요청이 느려졌다"

[있으면]
  "조회는 멀쩡한데 주문만 느려졌다"
  "주문 접수는 빠른데 처리가 안 된다"
```

**부분 장애를 구분할 수 있게 된다.** 00 문서에서 경로를 나눈 목적이 지표에서 완성된다.

### Histogram 구간을 어떻게 잡나

```text
[기본값을 그대로 쓰지 않는다]
  prometheus_client 기본 버킷은 웹 API 에 안 맞을 수 있다

[우리 구간 — 초 단위]
  0.005  0.01  0.025  0.05  0.1  0.25  0.5  1  2.5  5  10
```

```text
[왜 이렇게]
  캐시 적중은 수 ms 다        → 앞쪽이 촘촘해야 한다
  DB 조회는 수십 ms
  느린 쿼리는 수 초           → 뒤쪽도 있어야 한다
```

```text
[주의]
  버킷 하나가 시계열 하나다
  11개 구간 × method × path × route_class → 금방 늘어난다
  → path 를 패턴으로 넣는 게 그래서 더 중요하다
```

---

## 3. 경로 1 — 읽기와 캐시

```text
cache_operations_total{result}          result = hit | miss | error
cache_operation_duration_seconds        Histogram
```

### 무엇을 알 수 있나

```text
[캐시 적중률]
  hit / (hit + miss)
  → 평소 90% 였는데 0% 가 됐다면 Redis 가 죽은 것이다

[cache error 가 늘어난다]
  Redis 는 살아 있는데 응답이 느리거나 일부 실패한다
  → hit/miss 만 보면 안 보인다. error 를 따로 세야 한다
```

### 연쇄 장애를 미리 보는 지표

```text
Redis 가 죽는다
→ cache_operations_total{result="miss"} 급증
→ db_queries_total 급증
→ db_pool_in_use 가 상한에 닿는다
→ http_request_duration_seconds 가 뒤쪽 버킷으로 몰린다
→ http_requests_total{status="503"} 증가
```

**이 다섯 개를 한 화면에 놓으면 연쇄 장애가 순서대로 보인다.** 5단계 대시보드의 목표다.

---

## 4. 경로 2 — 동기 쓰기와 재고

```text
orders_created_total{result}       result = accepted | out_of_stock | error
db_transaction_duration_seconds    Histogram
db_lock_wait_seconds               Histogram
```

### 재고 경쟁을 지표로 본다 ★

03 문서에서 주문 SQL 을 세 단계로 발전시키기로 했다. **그 효과를 지표로 비교한다.**

```text
[1차 — 잠금 없음]
  db_lock_wait_seconds        거의 0
  orders_created_total        높다
  그런데 stock 이 음수가 된다  ← 지표로는 안 보인다. 그게 문제다

[2차 — FOR UPDATE]
  db_lock_wait_seconds        올라간다
  orders_created_total        떨어진다
  stock 음수 없음

[3차 — 조건부 UPDATE]
  db_lock_wait_seconds        2차보다 낮다
  orders_created_total        2차보다 높다
```

**"안전한 대신 얼마나 느려졌나" 를 숫자로 말할 수 있게 된다.**

### 재고 음수는 지표로 못 잡는다 — 별도 장치가 필요하다

```text
[문제]
  stock = -1 이 되어도 요청은 200 을 준다
  지표에는 성공으로 찍힌다
  → 2단계에서 겪은 "조용한 실패" 다
```

```text
[대책]
  books_stock_negative_total    Counter
  → 재고를 차감한 뒤 결과가 음수면 올린다
  → 이 값이 0보다 크면 즉시 알람
```

**"성공했지만 잘못된" 경우를 세는 지표가 따로 있어야 한다.**

---

## 5. 경로 3 — 큐와 Worker

```text
queue_length                              Gauge
queue_enqueued_total                      Counter
queue_dequeued_total                      Counter
order_queue_wait_seconds                  Histogram   created → started
order_process_duration_seconds            Histogram   started → finished
orders_processed_total{result}            result = completed | failed
worker_last_poll_timestamp_seconds        Gauge
```

### 적체를 판단하는 방법

```text
[queue_length 만 보면 안 된다]
  100개가 쌓였다 → 문제인가?
  Worker 가 초당 1000개를 처리하면 0.1초면 없어진다
  Worker 가 초당 1개를 처리하면 100초 걸린다
```

```text
[세 개를 같이 본다]
  입력 속도   rate(queue_enqueued_total[1m])
  소비 속도   rate(queue_dequeued_total[1m])
  대기 시간   order_queue_wait_seconds 의 p95

  입력 > 소비 가 지속되면 → 언젠가 터진다
  대기 시간이 계속 늘어나면 → 이미 밀리고 있다
```

**로드맵 3단계가 요구한 "Queue 입력 속도 / 소비 속도" 가 이것이다.**

### 대기 시간과 처리 시간을 반드시 나눈다

03 문서에서 시각을 셋 담기로 한 이유가 여기서 쓰인다.

```text
[하나로 뭉치면]
  "주문 처리가 5초 걸렸다"
  큐에서 4.9초 기다린 건지, 처리가 4.9초 걸린 건지 모른다

[나누면]
  wait 이 길다     → Worker 가 부족하다. 늘려야 한다
  process 가 길다  → 처리 로직이나 DB 가 느리다. 늘려도 소용없다
```

**대응이 정반대다.** 13편의 `DURATION` 문제와 같은 이야기다.

### Worker 가 조용히 멈춘 것을 잡는다 ★

```text
worker_last_poll_timestamp_seconds
```

```text
[상황]
  Worker 프로세스는 살아 있는데 큐를 안 본다
  → 큐만 계속 쌓인다
  → Kubernetes 는 정상으로 본다
```

**12편의 "DESIRED 0인데 지표가 정상" 과 같은 성격이다.**

```text
[알람]
  time() - worker_last_poll_timestamp_seconds > 60
  → "60초 넘게 큐를 안 봤다"
```

```text
[주의 — 큐가 비어 있는 것과 구분해야 한다]
  큐가 비어서 대기 중인 것은 정상이다
  대기 중이어도 주기적으로 확인은 하므로 이 값은 계속 갱신된다
  → 갱신이 멈췄다는 건 진짜 멈춘 것이다
```

04 문서의 Worker liveness 와 같은 판단 기준을 쓴다.

---

## 6. 의존성 상태

```text
dependency_up{name}                     Gauge    1 = 정상, 0 = 장애
dependency_check_duration_seconds{name} Histogram
dependency_errors_total{name, kind}     Counter  kind = timeout | refused | other
```

```text
name = postgres | redis
```

### 04 문서에서 넘어온 것

```text
readiness 에 DB 를 넣지 않기로 했다
→ 그럼 DB 장애를 무엇으로 아는가
→ 이 지표다
```

```text
[알람]
  dependency_up{name="postgres"} == 0  이 1분 이상 지속
```

### 커넥션 풀

```text
db_pool_size            Gauge   풀 크기
db_pool_in_use          Gauge   지금 쓰는 수
db_pool_wait_seconds    Histogram  커넥션을 기다린 시간
db_pool_timeouts_total  Counter    기다리다 포기한 수
```

```text
[04 문서에서 미룬 판단]
  커넥션 풀 고갈을 readiness 에 넣을지 6단계에서 정한다
  → 그러려면 먼저 재야 한다. 그게 이 지표다
```

```text
[연쇄 장애에서 이게 핵심이다]
  Redis 죽음 → 캐시 미스 → DB 로 몰림 → 풀 고갈
  → db_pool_wait_seconds 가 먼저 오른다
  → 그다음 503 이 나온다
  → 즉 503 보다 먼저 경고할 수 있다
```

---

## 7. 앱 자신에 대한 정보

```text
app_info{version, commit, pod, node, namespace}   Gauge, 항상 1
app_start_time_seconds                            Gauge
```

### 02 문서에서 넘어온 것

```text
Downward API 로 받는다
  POD_NAME / POD_NAMESPACE / NODE_NAME
```

```text
[왜 필요한가]
  "어느 Pod 가 느린가" "어느 노드에서만 실패하는가"
  → EKS 에서는 노드가 자주 바뀌므로 더 중요하다
```

```text
[주의 — pod 를 모든 지표의 라벨로 넣지 않는다]
  Prometheus 가 스크레이프할 때 자동으로 pod / instance 라벨을 붙인다
  앱이 또 넣으면 중복이고 카디널리티만 늘어난다
  → app_info 하나에만 담고 필요할 때 조인한다
```

**이걸 모르면 모든 지표에 pod 라벨을 넣는 실수를 한다.**

---

## 8. 2단계의 "조용한 실패" 를 어떻게 잡을 것인가 ★★★

**이 문서의 핵심이다.** 네 가지를 그대로 옮긴다.

```text
[09편 — 볼륨은 Bound 인데 데이터가 없다]

  오브젝트 상태로는 알 수 없었다
  → 앱 수준에서 확인해야 한다

  우리 앱에서
    Worker 가 DB 에 쓰고 나서 읽어보는 게 아니라
    books_stock_negative_total 처럼 "결과가 잘못됐음" 을 세는 지표를 둔다
```

```text
[10편 — Pod 가 멈췄는데 이벤트가 없다]

  READY 2/3 이라는 숫자를 사람이 직접 봐야 했다
  → 5단계에서 kube_statefulset_status_replicas_ready 를 감시한다
  → 앱 쪽에서는 app_info 로 "몇 개가 살아 있나" 를 셀 수 있다
```

```text
[12편 — DESIRED 0인데 지표가 정상]

  "부족한가" 가 아니라 "애초에 얼마여야 하는가" 를 물어야 했다
  → 우리 앱에서 같은 함정
     worker_last_poll_timestamp_seconds 가 아예 없으면?
     → Worker Pod 가 0개라는 뜻이다
     → "지표가 없는 것" 자체를 알람으로 잡아야 한다   absent()
```

```text
[13편 — 백업이 실패했는데 아무도 모른다]

  → kube_job_status_failed 를 감시한다
  → 그리고 "예정 시각에 안 돌았을 때" 도 잡아야 한다
     backup_last_success_timestamp_seconds 를 백업 Job 이 남기게 한다
     time() - 그 값 > 26시간 → 알람
```

### 공통 원칙 — "없는 것" 을 잡아야 한다

```text
지표가 0이다        → 문제를 알 수 있다
지표가 아예 없다     → 아무 알람도 안 울린다        ★ 이게 함정이다
```

```text
[대책]
  절대값이 아니라 "마지막 성공 시각" 을 지표로 둔다
    worker_last_poll_timestamp_seconds
    backup_last_success_timestamp_seconds

  → time() - 값 으로 판단하면 "안 돌고 있음" 이 잡힌다
  → 지표 자체가 사라지면 absent() 로 잡는다
```

**2단계에서 네 번 겪은 문제의 해답이 이 두 줄이다.**

---

## 9. 알람 초안 — 5단계에서 확정한다

```text
[즉시]
  books_stock_negative_total > 0                    데이터가 깨졌다
  dependency_up{name="postgres"} == 0   1분 지속
  absent(worker_last_poll_timestamp_seconds)        Worker 가 하나도 없다

[경고]
  cache hit rate < 50%              5분 지속        Redis 이상
  db_pool_wait_seconds p95 > 1s                     풀 고갈이 다가온다
  queue 입력 > 소비                  10분 지속       적체 중
  order_queue_wait_seconds p95 > 60s                이미 밀렸다
  time() - worker_last_poll > 60                    Worker 가 멈췄다
  time() - backup_last_success > 26h                백업이 안 돌았다

[관찰]
  http_request_duration p95         route_class 별
  http_requests_total{status="5xx"} 비율
```

### 임계값을 지금 확정하지 않는다

```text
[왜]
  아직 정상 상태의 분포를 모른다
  "p95 1초" 가 평소보다 나쁜 건지 좋은 건지 알 수 없다

[언제 정하나]
  5단계에서 k6 로 정상 부하를 넣고 분포를 먼저 잰다
  그다음 "이 값을 넘으면 이상하다" 를 정한다
```

**측정이 목표보다 먼저다.** 01 문서에서 응답 시간 목표를 안 정한 것과 같은 태도다.

---

## 10. 구현 시 주의할 것

### /metrics 는 인증이 없다

```text
4단계에서 Ingress 로 외부에 노출하지 않는다
→ 지표에 내부 구조가 드러난다
→ Service 로만 열고 Prometheus 가 클러스터 안에서 긁게 한다
```

### Worker 도 /metrics 를 내야 한다

```text
Worker 는 HTTP 서버가 아니지만 지표를 내려면 필요하다
→ 04 문서에서 liveness 용으로 작은 서버를 띄우기로 했다
→ 같은 서버에 /metrics 를 붙인다
```

### Pod 가 여러 개면 지표도 여러 벌이다

```text
Prometheus 는 Pod 마다 긁는다
→ 합산은 Prometheus 가 한다  sum(rate(...))
→ 앱이 합치려 하면 안 된다
```

```text
[Counter 는 Pod 재시작 시 0으로 돌아간다]
  그게 정상이다. rate() 가 리셋을 알아서 처리한다
  → 절대값이 아니라 증가율을 보는 이유다
```

### 지표를 만드는 비용

```text
[라벨 값을 매 요청마다 만들지 않는다]
  f"{method}:{path}" 같은 문자열 조합이 요청마다 일어나면 부담이다
  → 라우트별로 미리 만들어둔다
```

```text
[Histogram 은 Counter 보다 비싸다]
  버킷 수만큼 카운터가 있는 셈이다
  → 꼭 필요한 곳에만 쓴다
```

---

## 정리 — 이 문서에서 내린 판단

```text
 1. Summary 를 안 쓰고 Histogram 을 쓴다
    Pod 가 여러 개면 분위수를 앱에서 계산하면 합칠 수 없다

 2. 라벨에 무한히 늘어나는 값을 넣지 않는다 ★★
    book_id / order_id / request_id / IP / 에러 메시지 원문
    path 는 반드시 패턴으로 ("/books/{id}")
    → 카디널리티 폭발은 Prometheus 를 죽인다

 3. 개별 건은 지표가 아니라 로그의 몫이다
    지표는 "어디가" 를 좁히고, 로그는 "무엇이" 를 찾는다
    → X-Request-Id 를 넣기로 한 이유

 4. offset 은 구간으로 라벨을 만든다
    무한한 값을 유한한 구간으로 바꾸면 지표로 볼 수 있다

 5. route_class 라벨로 세 경로를 구분한다 ★
    "조회는 멀쩡한데 주문만 느리다" 를 말할 수 있게 된다

 6. 큐는 길이만 보면 안 된다
    입력 속도 / 소비 속도 / 대기 시간을 같이 본다

 7. 대기 시간과 처리 시간을 나눈다
    wait 이 길면 Worker 를 늘린다 / process 가 길면 늘려도 소용없다
    → 대응이 정반대다

 8. "성공했지만 잘못된" 것을 세는 지표를 따로 둔다
    books_stock_negative_total
    → 재고가 음수여도 요청은 200 이다. 그게 조용한 실패다

 9. pod 라벨을 모든 지표에 넣지 않는다
    Prometheus 가 자동으로 붙인다. app_info 에만 담는다

10. "없는 것" 을 잡는 설계를 한다 ★★★
    마지막 성공 시각을 지표로 두고 time() - 값 으로 판단한다
    지표 자체가 사라지면 absent() 로 잡는다
    → 2단계에서 네 번 겪은 조용한 실패의 해답이다

11. 임계값을 지금 확정하지 않는다
    5단계에서 정상 분포를 먼저 재고 그다음 정한다
```

## 다음

```text
06-fault-injection.md   위 지표들이 실제로 움직이는지 확인하려면
                        장애를 일부러 일으킬 수 있어야 한다
                        /debug/* 엔드포인트를 설계한다
```

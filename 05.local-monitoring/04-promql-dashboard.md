# Phase 4. PromQL / 알람 / 대시보드

작업일: 2026-09-07

## 목적

Phase 3 을 끝냈을 때 지표 55종이 들어왔다. **그런데 아무도 안 봤다.**

```text
  숫자가 있어도 볼 줄 모르면 장애 때 못 쓴다
```

이 Phase 는 세 가지를 한다.

```text
  1. PromQL 로 그 숫자를 읽는다
  2. ★ 정상일 때의 값을 재둔다 (기준선)
  3. 그 기준선으로 알람 임계값을 정하고, 대시보드에 배치한다
```

```text
★ 순서가 중요하다

  기준선 → 임계값 → 알람
  거꾸로 하면 숫자를 지어내게 된다
```

## 0. 5일 만에 돌아오니 데이터가 없었다

```text
  retention: 2d 로 뒀다
  → 9월 2일 데이터는 이미 지워졌다
```

```text
★ "장애 났을 때 그래프를 보자" 는 보존 기간 안에서만 된다
  → 실무에서 장기 보존(Thanos, Mimir)을 따로 붙이는 이유다
```

상태는 그대로였다.

```bash
kubectl get pod -n bookstore
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "..."
```

```text
전체: 25 / up: 25
  up api-6c5c799d6-kv8fw
  up api-6c5c799d6-7lb9k
  up worker-6c989db87d-9xkl5

api / worker  5일 20시간, 재시작 0
→ Phase 2 에서 고친 bind-address 와 kube-proxy ConfigMap 이 안정적이라는 뜻이다
```

## 1. 부하 넣기 — 그리고 카디널리티 처리 확인

지표가 안 움직이면 볼 게 없다.

```bash
cat > /tmp/load.sh <<'EOF'
#!/bin/bash
while true; do
  curl -s -o /dev/null http://192.168.8.143:30800/books
  curl -s -o /dev/null "http://192.168.8.143:30800/books?limit=5"
  curl -s -o /dev/null http://192.168.8.143:30800/books/1
  curl -s -o /dev/null http://192.168.8.143:30800/nothing
  sleep 1
done
EOF
chmod +x /tmp/load.sh
nohup /tmp/load.sh > /dev/null 2>&1 &
```

```text
[일부러 이렇게 섞었다]
  /books          목록          → read
  ?limit=5        같은 경로      → 하나로 합쳐지는지
  /books/1        경로 파라미터  → 패턴으로 묶이는지
  /nothing        404           → unmatched 로 묶이는지
```

```text
/books               200  read       api-…-7lb9k      62
/books               200  read       api-…-kv8fw      68
/books/{book_id}     200  read       api-…-7lb9k      32
/books/{book_id}     200  read       api-…-kv8fw      24
unmatched            404  unknown    api-…-7lb9k      35
unmatched            404  unknown    api-…-kv8fw      19
```

```text
★ 셋 다 확인됐다

  /books/1 이 아니라 /books/{book_id}
  /nothing 이 unmatched
  ?limit=5 가 /books 와 합쳐짐

  → 3단계에서 "라벨에 무한히 늘어나는 값을 넣지 않는다" 고 정한 처리가
    실제로 동작하고 있다
```

## 2. 카운터를 그냥 보면 안 된다

```bash
Q='sum(http_requests_total{route_class="read"})'
for i in 1 2 3; do
  V=$(curl -s ... --data-urlencode "query=$Q" | ...)
  echo "$(date +%H:%M:%S)  $V"; sleep 30
done
```

```text
08:34:31   566
08:35:01   650      +84
08:35:31   734      +84
```

```text
[문제 세 가지 — 전부 "누적값" 이라서 생긴다]

  1. 많은 건지 적은 건지 모른다
     62 는 Pod 가 뜬 뒤로 4일 16시간의 누적이다

  2. Pod 마다 시작 시각이 다르다
     새 Pod 는 0 부터 센다 → "일을 안 한다" 로 보인다

  3. 재시작하면 0 으로 돌아간다
     그래프가 절벽처럼 떨어진다 → "요청 급감" 으로 보인다
```

```text
★ 정보는 "숫자" 가 아니라 "차이" 에 있다
  (650 - 566) / 30초 = 초당 2.8건
```

## 3. `rate()` — 손 계산과 일치했다

```bash
curl -s ... --data-urlencode 'query=rate(http_requests_total{route_class!="internal"}[5m])'
```

```text
/books               api-…-7lb9k      0.8741
/books               api-…-kv8fw      1.0037
/books/{book_id}     api-…-7lb9k      0.4815
/books/{book_id}     api-…-kv8fw      0.4556
                                      ───────
read 합계                             2.8149
```

```text
★ 손으로 뺀 2.8 과 rate() 의 2.8149 가 일치한다
  → rate 가 "구간의 초당 증가율" 을 계산한다는 게 눈으로 확인됐다
```

```text
[unmatched 는 따로]
  0.4963 + 0.4407 = 0.937
  → route_class 가 "unknown" 이라 read 필터에 안 걸린다
  → 스크립트가 1초에 1번 보내니 맞는 값이다
```

### `[5m]` 을 쓰는 이유

```text
  rate 는 구간 안의 데이터 포인트들로 기울기를 낸다
  scrape_interval 이 30초 → [5m] 이면 약 10개 점

  [30s]  점이 1개 → 계산 불가
  [1m]   2개 → 불안정
  [5m]   10개 → 안정적

★ 구간은 scrape_interval 의 4배 이상으로 잡는다
```

### `[1m]` vs `[5m]` 실측

부하를 08:31 에 켜고 08:46 에 껐다.

```text
  [1m]   08:31 즉시 2.8 로 점프
         2.5~3.0 사이를 계속 출렁임
         08:46 즉시 0 으로 낙하

  [5m]   08:31→08:36 완만히 상승
         매끈하게 평평
         08:46→08:51 완만히 하강
```

```text
★ 같은 데이터인데 모양이 완전히 다르다

  알람에는 [5m]      순간 출렁임으로 안 울리게
  장애 순간에는 [1m]  정밀하게 보려고

  → [5m] 은 5분 늦다. 08:46 에 멈췄는데 08:51 에야 0 이 된다
  → 알람에 for: 를 걸면 그만큼 더 늦어진다
```

```text
[카운터를 그냥 그리면]
  트래픽이 멈춰도 그래프는 높은 값을 유지한다
  → 무심코 보면 정상처럼 보인다
  → rate 는 0 으로 떨어진다
```

## 4. `sum by()`

```text
sum(rate(...))                     {} 3.756          전체
sum by (path) (rate(...))          /books 1.881
                                   unmatched 0.930
                                   /books/{id} 0.944
                                   ─────────────────
                                   합계 3.755        ★ 맞는다
sum by (pod) (rate(...))           7lb9k 1.759  (46.8%)
                                   kv8fw 1.996  (53.2%)
```

```text
  by       남길 라벨을 적는다 → 나머지는 합쳐진다
  without  버릴 라벨을 적는다 → 나머지는 남는다
```

```text
★ Pod 분배 47:53 은 4단계의 실측이다

  그때는 iptables 확률 분기 규칙을 보고 "50/50" 이라고 계산만 했다
  → 매 연결이 독립적으로 주사위를 굴리니 짧게 보면 안 고르다
```

### 나눗셈은 라벨이 맞아야 한다

```promql
# ✗ 결과가 빈다 — 왼쪽은 {pod=…}, 오른쪽은 {}
sum by (pod) (rate(...)) / sum(rate(...))

# ✓ scalar() 로 숫자 하나로 바꾸면 라벨 매칭이 필요 없다
sum by (pod) (rate(...)) / scalar(sum(rate(...)))
```

## 5. ★ `histogram_quantile()` — 버킷이 안 맞으면 가짜 숫자가 나온다

```bash
curl -s ... --data-urlencode 'query=http_request_duration_seconds_bucket{path="/books"}'
```

```text
le=0.005    965      ← 98.57%
le=0.01     978
le=0.025    979
le=0.05     979
...
le=inf      979
```

```text
★ 버킷 11개 중 10개가 놀고 있다
```

```text
p50   0.0025207     = 0.005 × 0.50
p95   0.0047894     = 0.005 × 0.96
p99   0.0049910     = 0.005 × 1.00
```

```text
★ 전부 0.005 에 분위수를 곱한 값이다

  Prometheus 가 아는 것은 "979건 중 965건이 5ms 이하" 뿐이다
  그 965건이 1ms 인지 4ms 인지는 모른다
  → "0~5ms 구간에 고르게 퍼져 있다" 고 가정하고 선형 보간한다
  → 실제 데이터가 아니라 가정에서 나온 숫자다
```

```text
[경로별 값이 전부 같은 것도 그 때문이다]
  /books        0.004820
  /books/{id}   0.004787
  unmatched     0.004750
  /health/live  0.004750
  → 실제로 같은 게 아니라 구별할 해상도가 없는 것이다
```

### 버킷을 잘못 잡았다

```python
# metrics.py
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
#   캐시 적중은 수 ms       → 앞쪽이 촘촘해야 한다
```

```text
  "수 ms" 를 예상하고 5ms 부터 시작했다
  실제로는 1~2ms 였다 → 첫 버킷보다 빠르다
```

### p99.9 부터 의미가 생긴다

```text
p99     0.00499      5ms
p99.9   0.02010     20ms      ★ 네 배로 뛴다
```

```text
  1000명 중 1명은 20ms 를 기다린다
  → 평균이나 p50(2.5ms)만 봤으면 못 봤을 값이다
  → p99 까지는 첫 버킷 안이라 가짜, p99.9 부터 진짜 경계를 넘는다
```

### 그래서 분위수를 볼 때 같이 봐야 할 것

```bash
run 'sum(rate(http_request_duration_seconds_bucket{le="0.005"}[5m]))
     / sum(rate(http_request_duration_seconds_count[5m]))'
# 0.9895059029296022
```

```text
★ 1.0 에 가까우면 분위수 값이 의미가 없다는 뜻이다
  → 값을 믿기 전에 버킷 분포를 먼저 봐야 한다
```

```text
[지금 고치지 않기로 했다]
  버킷을 바꾸면 과거 데이터와 비교가 안 된다
  지금은 응답이 너무 빨라 구별할 게 없다
  → 6단계에서 장애를 주입하면 뒤쪽 버킷들이 일한다
  → 오히려 "평소엔 전부 5ms 이하" 가 좋은 기준선이다
```

### `le` 를 버리면 결과가 통째로 빈다

```bash
run 'histogram_quantile(0.95, sum by (path) (rate(http_request_duration_seconds_bucket[5m])))'
# === 결과 없음 ===
```

```text
  에러도 안 난다. 그냥 빈다
  → 대시보드에 넣으면 "데이터 없음" 만 뜬다. 원인 찾기가 어렵다
```

```text
[반면 sum 을 아예 안 하면 동작은 한다]
  histogram_quantile(0.95, rate(..._bucket[5m]))
  → rate 는 le 를 유지하므로 Pod × path 조합마다 따로 계산된다
  → 틀린 게 아니라 "안 합쳐진" 것이다
  → 평소엔 합쳐서 보고, 이상하면 쪼개서 본다
```

## 6. 4단계에서 못 잰 것 재기

### ★ 48Mi 사건의 검증

```bash
run 'container_memory_working_set_bytes{namespace="bookstore", container="api"} / (48*1024*1024)'
```

```text
api-…-7lb9k   0.9937
api-…-kv8fw   0.9931
```

```text
  지금 쓰는 양   47.70 MiB
  그때 준 limits  48 MiB
  ────────────────────────
  여유            0.3 MiB      ★ 0.6%
```

```text
★ 4단계에서 "뜨고 나서 죽었다" 의 답이다

  기동은 된다. 47.7 < 48 이니까
  요청이 들어와 버퍼를 조금만 잡으면 넘는다 → OOMKilled (exit 137)

  → "48Mi 로 뜨는 건 봤다" 에서
    "여유가 300KB 였다" 로 바꿔 적을 수 있다
```

```text
[working_set 이 무엇인가]
  RSS 가 아니다. "지금 당장 회수할 수 없는 메모리" 다
  → 커널이 OOM 판단에 쓰는 값이다
  → usage 나 rss 가 아니라 이걸 봐야 한다
```

```text
[현재 limits 256Mi 대비 18.6%]
  줄일 수 있지만 지금은 두는 게 낫다
  → 초당 4건은 진짜 부하가 아니다
  → 6단계에서 부하를 올린 뒤 정하는 게 순서다
```

### 라벨이 다른 두 지표를 나누기

```promql
container_memory_working_set_bytes{namespace="bookstore", container="api"}
  / on(pod, container)
kube_pod_container_resource_limits{namespace="bookstore", container="api", resource="memory"}
```

```text
{container: api, pod: api-…-7lb9k}  0.1863
{container: api, pod: api-…-kv8fw}  0.1862
```

```text
★ 두 지표의 출처가 완전히 다르다

  working_set   커널(cgroup) → cAdvisor        라벨: id, image, name, node …
  limits        선언 → API 서버 → kube-state   라벨: uid, service …

  → 공통 라벨(pod, container)만 짝짓고 나머지는 무시한다
  → on() 을 안 쓰면 짝을 못 찾아 결과가 빈다
```

### CPU throttle — 지표 이름이 달랐다

```bash
run 'rate(container_cpu_cfs_throttled_seconds_total{...}[5m])'
# === 결과 없음 ===

run 'count by (__name__) ({__name__=~"container_cpu_cfs.*"})'
# container_cpu_cfs_periods_total            10
# container_cpu_cfs_throttled_periods_total  10
```

```text
  _seconds_total 이 이 클러스터에 없다. _periods_total 두 개만 있다
  → 최근 Kubernetes 에서 cAdvisor 지표 일부가 정리된 것으로 보인다
  → 확실하지 않다. 다만 남은 두 개로 더 나은 형태를 만들 수 있다
```

```promql
rate(container_cpu_cfs_throttled_periods_total[5m])
  / rate(container_cpu_cfs_periods_total[5m])
```

```text
[CFS 가 하는 일]
  limits: 500m → 100ms 주기마다 50ms 까지

  주기 1   30ms 쓰고 끝     정상
  주기 2   50ms 다 씀       ★ 남은 50ms 강제로 재움 (throttled)

  periods / throttled_periods 로 "몇 %의 주기가 걸렸나" 가 나온다
```

```text
★ CPU limits 는 메모리와 동작이 다르다
  메모리 초과   즉시 죽인다 (OOMKilled)
  CPU 초과      안 죽인다. 조용히 느려진다
  → Pod 는 Running 이고 probe 도 통과한다
  → 이 지표 없이는 "왜 느리지" 를 못 찾는다
```

```text
[시계열이 10개뿐인 이유]
  CPU limits 를 안 건 컨테이너에는 이 지표가 없다
  → quota 가 없으면 CFS 주기 자체가 안 돈다
```

## 7. 기준선

```text
  항목                기준선            질의
  ────────────────────────────────────────────────────────────
  요청량              3.76 /초          rate(http_requests_total)
  Pod 분배            47% : 53%         sum by (pod)
  ────────────────────────────────────────────────────────────
  응답 p50            2.5 ms            ★ 첫 버킷 안. 보간값
       p95            4.8 ms            ★ 보간값
       p99.9          20 ms             진짜 경계를 넘은 값
  5ms 이하 비율        98.95%
  ────────────────────────────────────────────────────────────
  메모리              47.7 MiB / 18.6%  (limits 256Mi)
  48Mi 였다면          99.4%
  CPU                 14 밀리코어 / 2.9% (limits 500m)
  throttle            0
  ────────────────────────────────────────────────────────────
  db_pool_size        2                 ★ MAX 10 이 아니다. 현재 열린 수
  db_pool_available   2
  db_pool_waiting     0                 ★ 평소 0
  dependency_up       전부 1
  cache hit           5.58 /초
  cache miss          0.063 /초         적중률 98.9%
  queue_length        0
  worker 마지막 폴링   21.9초
  ────────────────────────────────────────────────────────────
  5xx                 0
  재시작              api 0, worker 0, postgres 1, redis 1 (11일 전)
  OOM 이력            없음
```

```text
★ db_pool_size 가 2 인 이유
  DB_POOL_MIN 2 / MAX 10 인데 부하가 없어 최소치만 유지한다
  → size 는 동적이다. "available / size" 로 비율을 재면 해석이 흔들린다
  → db_pool_waiting > 0 으로 거는 게 낫다
```

```text
[곁가지]
  read 요청이 초당 2.82건인데 캐시 조회는 5.58건
  → 요청 하나당 캐시를 두 번 본다 (목록 + 개수를 따로 캐시)
```

### ★ 여기서 문제를 하나 발견했다

```bash
run 'time() - worker_last_poll_timestamp_seconds'
```

```text
worker Pod    21.88            정상
api Pod       1788740415       ★ 유닉스 타임 그 자체
```

```text
  worker_last_poll_timestamp_seconds 가 api Pod 에서 0 이다
  → time() - 0 = time()
```

```text
[왜]
  metrics.py 가 지표를 전역으로 선언한다
  api 도 worker 도 같은 이미지를 쓴다
  → api Pod 도 이 게이지를 노출한다. 아무도 갱신을 안 할 뿐이다
```

```text
★ 알람에 그대로 쓰면 api Pod 때문에 항상 울린다
  → container="worker" 로 반드시 걸러야 한다
  → 근본 해결은 component 에 따라 등록할 지표를 나누는 것. 나중에
```

## 8. PrometheusRule 작성

### 게이트 확인 — PodMonitor 와 같은 구조다

```bash
kubectl get prometheus kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{.spec.ruleSelector}{"\n"}{.spec.ruleNamespaceSelector}{"\n"}'
```

```text
{"matchLabels":{"release":"kube-prom-stack"}}
{}
```

### 첫 시도에서 나온 오타

```text
  틀린 것        맞는 것        몇 곳
  ────────────────────────────────────
  exor           expr           5
  annotation     annotations    6
  serverity      severity       6
  interval: 60   interval: 60s  1
```

```yaml
# ✗ labels 에 selector 문법을 썼다
  labels:
    matchLabels:
      - ruleSelector: kube-prom-stack

# ✓ 라벨은 평평한 키:값 이다
  labels:
    release: kube-prom-stack
```

```text
★ 전부 조용히 실패한다
  annotation 은 스키마에 없어 무시된다 → 알람은 뜨는데 설명이 빈다
  serverity 는 라벨이 하나 더 생길 뿐 → Alertmanager 라우팅에 안 걸린다
```

```text
[group 을 6개로 나눴다가 하나로 합쳤다]
  group 은 "같이 평가되는 묶음" 이다. interval 이 group 단위다
  → rule 마다 다른 주기가 필요한 게 아니면 나눌 이유가 없다
  → 찾고 수정하는 건 alert 이름으로 한다
```

### 최종 — `k8s/11-app-alerts.yaml`

```yaml
groups:
  - name: bookstore
    interval: 30s
    rules:
      - alert: BookstoreDependencyDown
        expr: dependency_up == 0
        for: 1m
        labels: { severity: critical }

      - alert: BookstoreDBPoolSaturated
        expr: db_pool_waiting > 0
        for: 2m
        labels: { severity: warning }

      - alert: BookstoreHighErrorRate
        expr: |
          sum(rate(http_requests_total{route_class!="internal", status=~"5.."}[5m]))
            / sum(rate(http_requests_total{route_class!="internal"}[5m])) > 0.05
        for: 5m
        labels: { severity: critical }

      - alert: BookstoreSlowResponse
        expr: |
          histogram_quantile(0.95, sum by (le) (
            rate(http_request_duration_seconds_bucket{route_class!="internal"}[5m]))) > 0.05
        for: 5m
        labels: { severity: warning }

      - alert: BookstoreWorkerStalled
        expr: time() - worker_last_poll_timestamp_seconds{container="worker"} > 120
        for: 1m
        labels: { severity: critical }

      - alert: BookstoreStockNegative
        expr: increase(books_stock_negative_total[5m]) > 0
        for: 0m
        labels: { severity: critical }
```

### 임계값을 정한 근거

```text
  5xx 5%          기준선 0%. 1% 면 어쩌다 한 건에도 울린다
  p95 50ms        기준선 4.8ms 의 10배. 1초로 잡으면 200배 — 이미 늦다
  worker 120초     기준선 21.9초. 30초면 폴링이 조금 늦어도 울린다
  pool waiting 0   기준선이 0이라 임계값이 명확하다
```

```text
★ 남의 기본값을 베끼면 안 되는 이유

  "메모리 80% 넘으면 알람" 은 흔한 기본값이다
  우리 평소는 18.6% 다 → 80% 면 네 배로 늘어난 뒤다. 늦다
```

```text
[increase() 를 쓴 이유 — 재고 음수]
  카운터는 한 번 오르면 계속 그 값이다
  books_stock_negative_total > 0 으로 걸면 한 번 발생한 뒤 영원히 울린다
  → increase(...[5m]) 는 "최근 5분간 늘었나" 다
```

### 적용 전에 expr 을 먼저 실행했다

```bash
run 'dependency_up == 0'                                     # (비어 있음)
run 'db_pool_waiting > 0'                                    # (비어 있음)
run 'time() - worker_last_poll_timestamp_seconds{container="worker"} > 120'
run 'increase(books_stock_negative_total[5m]) > 0'
run '... 5xx 비율 ... > 0.05'
run '... p95 ... > 0.05'
```

```text
  여섯 개 전부 비어야 정상이다
★ 알람을 만들기 전에 expr 을 실행해보는 게 습관이 돼야 한다
  → 문법 오류, 라벨 오타, 항상 참인 조건을 여기서 걸러낸다
```

### 적용

```bash
kubectl apply -f 11-app-alerts.yaml --dry-run=server
kubectl apply -f 11-app-alerts.yaml
```

```bash
kubectl get cm prometheus-…-rulefiles-0 -n monitoring -o jsonpath='{.data}' | ... | grep bookstore
# bookstore-bookstore-alerts-8541f4a7-….yaml
```

```bash
curl -s http://192.168.8.143:30090/api/v1/rules | python3 -c "..."
```

```text
group: bookstore | /etc/prometheus/rules/…-rulefiles-0/bookstore-bookstore-alerts-….yaml
   inactive BookstoreDependencyDown   | health: ok
   inactive BookstoreDBPoolSaturated  | health: ok
   inactive BookstoreHighErrorRate    | health: ok
   inactive BookstoreSlowResponse     | health: ok
   inactive BookstoreWorkerStalled    | health: ok
   inactive BookstoreStockNegative    | health: ok
```

```text
★ health 를 꼭 봐야 한다

  expr 문법이 틀려도 apply 는 성공한다
  → 스키마는 "문자열" 인지만 본다. PromQL 을 검사하지 않는다
  → 실제 오류는 Prometheus 가 규칙을 돌릴 때 드러난다
```

```text
[반영에 시간이 걸렸다]
  PodMonitor  Secret  → config-reloader 가 파일 감시 → 즉시
  알람 규칙    ConfigMap → kubelet 볼륨 동기화(최대 60초) → reload

  → 오브젝트가 생겼다고 즉시 동작하는 게 아니다
  → 층마다 전파 시간이 다르다
```

## 9. 일부러 울려봤다

만들어놓고 안 울려보면 진짜 동작하는지 모른다. 앱에 장애 주입 기능이 있다.

```bash
grep -n "ENABLE_DEBUG_ENDPOINTS" k8s/01-configmap.yaml
# 102:  ENABLE_DEBUG_ENDPOINTS: "true"
```

```text
[사용 가능한 주입]
  break-redis  {mode, seconds}     연쇄 장애 스위치
  slow-query   {seconds}           pg_sleep 으로 커넥션을 붙잡음
  latency      {ms, ratio}         응답 지연         ★ 이걸 씀
  error-rate   {ratio, status}     확률적 실패
  worker-slow  {seconds}           큐 적체
  ready        {}                  readiness 강제 실패
```

```bash
POD=$(kubectl get pod -n bookstore -l app.kubernetes.io/name=api -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n bookstore $POD 9000:9000 &

curl -s -X POST http://127.0.0.1:9000/debug/inject/latency \
  -H 'Content-Type: application/json' \
  -d '{"ttl_seconds": 600, "params": {"ms": 300, "ratio": 1.0}}'
```

```text
[안전장치]
  9000 은 Service 에 없다 → port-forward 로만 닿는다
  주입 상태는 Pod 로컬 메모리 → Pod 하나에만 들어간다
  ttl_seconds 600 → 10분 뒤 앱이 스스로 해제한다
```

### 결과

```text
  inactive → pending → firing        확인
  해제 후    firing → inactive        확인
```

```text
★ 두 방향의 속도가 다르다

  켜질 때   rate([5m]) 창이 채워지는 시간 + for: 5m  → 7~10분
  꺼질 때   조건이 거짓이 되는 순간 즉시

  → for 는 켜질 때만 적용된다
  → 이 비대칭이 의도된 것이다
     켜질 때 느리게(오탐 방지), 꺼질 때 빠르게(복구 확인)
```

```text
[firing 이 돼도 아무데도 안 간다]
  values.yaml 에서 alertmanager.enabled: false 로 껐다
  → Prometheus 화면(30090)에 빨갛게 표시되는 게 전부다
  → Phase 6 에서 Alertmanager 를 켠다
```

## 10. 대시보드

### 목적을 먼저 정했다

```text
  (가) 상시 감시용    벽에 띄워놓고 보는 화면
  (나) 장애 조사용    ★ 택함. 알람이 울린 뒤 원인을 찾는 화면
```

```text
★ 배치 원칙 — 증상에서 원인으로 내려간다

  사람은 "503 이 났다" 에서 시작해 아래로 찾는다
  거꾸로 배치하면 맨 위에 Redis 상태가 있어도 그걸 먼저 안 본다
```

```text
  [일어나는 순서]  Redis 죽음 → 미스 급증 → DB 몰림 → 풀 고갈 → 503
  [읽는 순서]      503 → 풀 → DB → 미스 → Redis
```

```text
[요약 행만 예외]
  맨 위에 Stat 패널 5개를 둔다
  → "자원부터 크게 배제하고 들어간다" 는 접근도 유효하다
  → 숫자 하나로 3초에 판단, 상세는 맨 아래 그래프로
```

### 구성 — 7행 22패널

```text
  요약        Ready / 재시작 / CPU / 메모리 / 알람
  1. 증상      요청수(Pod별) / 상태코드 / 5xx율 / p50·p95·p99
  2. 의존      dependency_up / 캐시결과 / 적중률 / 의존오류
  3. 포화      ★ DB 풀 / 커넥션 대기 / 쿼리시간
  4. 큐        큐길이 / 입력vs소비 / wait vs process / Worker 폴링
  5. 비즈니스   주문접수 / 처리결과 / ★ 재고음수
  6. 자원      메모리 / CPU / throttle / 재시작   (Pod별)
```

### ★ UI 로 만들면 사라진다

```bash
kubectl get deploy kube-prom-stack-grafana -n monitoring \
  -o jsonpath='{.spec.template.spec.volumes}' | python3 -m json.tool
```

```text
  "emptyDir": {}, "name": "storage"       ← /var/lib/grafana/grafana.db
  "emptyDir": {}, "name": "sc-dashboard-volume"
  PVC 없음
```

```text
★ Phase 2 에서 "Grafana 는 저장할 게 없다" 고 판단했다
  지표를 안 저장하는 건 맞았다
  → 그런데 대시보드는 저장 대상이었다
  → UI 로 만든 건 Pod 재시작하면 사라진다
```

```text
[그래서 ConfigMap 으로 간다]
  ConfigMap(etcd)
    → 사이드카가 watch → Pod 안 /tmp/dashboards 에 파일로 씀
    → Grafana 가 읽음
  → Pod 안 파일은 사본이다. 날아가도 다시 만들어진다
  → 진짜 원본은 git 이다
```

```text
  라벨이 게이트다
    grafana_dashboard: "1"
  → PodMonitor 의 release, PrometheusRule 의 release 와 같은 구조다
```

### 확인

```bash
kubectl rollout restart deploy/kube-prom-stack-grafana -n monitoring
```

```text
  손으로 만든 "Bookstore"              → 사라진다
  ConfigMap 의 "Bookstore / Incident"  → 남는다
```

## 11. 겪은 문제

### Grafana v13 이 새 스키마로 내보낸다

```text
  "apiVersion": "dashboard.grafana.app/v2"
  "elements": { "panel-1": … }
  "layout": { "kind": "RowsLayout" … }
```

```text
  사이드카가 읽는 건 고전 형식(v1)이다
    "panels": [ … ], "gridPos": {…}
  → 차트가 넣어둔 31개가 전부 v1 이다
  → UI 에서 내보낸 JSON 을 그대로 ConfigMap 에 넣을 수 없다
  → v1 으로 직접 작성했다
```

### state-timeline 이 전부 빨강이었다

```text
  값이 1(정상)인데 빨갛게 나왔다
  → 색 모드를 명시하지 않으면 thresholds 를 안 쓴다
```

```json
"color": { "mode": "thresholds" },
"mappings": [
  { "type": "value", "options": { "0": { "text": "DOWN", "color": "red" } } },
  { "type": "value", "options": { "1": { "text": "UP",   "color": "green" } } } ]
```

### 5xx 에러율이 "No data" 였다

```text
  500 이 한 번도 없어서 분자 시계열 자체가 없다
  → 없는 것 ÷ 3.7 = 빈 결과
```

```promql
(sum(rate(...{status=~"5.."}[5m])) or vector(0)) / sum(rate(...[5m]))
```

```text
★ 알람은 안 울리는 게 맞다. 대시보드는 0 을 보여줘야 한다
  → 같은 질의라도 목적에 따라 다르게 써야 한다
```

```text
[y축도 이상했다]
  값이 계속 0 이라 Grafana 가 축을 0~10000% 로 잡았다
  → "axisSoftMax": 0.1 로 최소 범위를 정해줬다
  → 임계선 5% 가 보인다
```

### ★ 고쳤는데 반영이 안 됐다 — 구간별로 세어 찾았다

```bash
grep -c "or vector(0)" /home/sjpark/k8s/12-grafana-dashboard.yaml          # 5
kubectl get cm bookstore-incident-dashboard -n monitoring \
  -o jsonpath='{.data.bookstore-incident\.json}' | grep -c "or vector(0)"  # 3
kubectl exec … -c grafana-sc-dashboard -- \
  grep -c "or vector(0)" /tmp/dashboards/bookstore-incident.json           # 3
```

```text
  파일 5 / ConfigMap 3 → ★ 여기서 끊겼다. apply 가 안 됐다
```

```text
★ 진단 방법이 metrics.py 때와 같다

  구간을 나누고 각 구간에서 같은 것을 센다
    파일 → 이미지            (Phase 3)
    파일 → ConfigMap → Pod → Grafana   (Phase 4)
  → 어디서 끊겼는지가 숫자로 나온다. 추측이 필요 없다
```

```text
[사이드카 로그도 판정 도구다]
  "Writing /tmp/dashboards/bookstore-incident.json"
  "Dashboards config reloaded  200 OK"
  → apply 후에 이 두 줄이 나와야 한다
```

### `grep -c` 는 줄 수를 센다

```bash
kubectl exec … -c grafana -- curl -s -u admin:admin \
  http://localhost:3000/api/dashboards/uid/bookstore-incident | grep -c "or vector(0)"
# 1
```

```text
  ConfigMap 은 JSON 이 여러 줄 → 5줄에 하나씩 → 5
  Grafana API 는 한 줄로 압축 → 1줄에 5개 → 1

  → grep -o "…" | wc -l 로 세야 한다
  → 하마터면 "Grafana 가 안 읽었다" 로 오진할 뻔했다
```

## 12. 대시보드에서 읽은 것

```text
  12:00 에 세 패널이 동시에 움직였다

  응답시간 p99        5ms → 30ms
  DB 쿼리 시간 p95    5ms → 45ms
  CPU throttle        0 → 0.3%
```

```text
★ 세로로 쌓아뒀기 때문에 한눈에 보인다
  질의를 하나씩 쳤으면 이 동시성을 못 봤다
  → 대시보드를 만든 이유다
```

```text
  그리고 p50/p95 는 안 움직였다. p99 만 튀었다
  → 소수의 요청만 느렸다는 뜻이다
  → 평균만 봤으면 못 봤다
```

```text
[다른 관찰]
  DB 풀    11:40 에 size 2 → 1 → 2. psycopg 가 유휴 커넥션을 닫았다 여는 것
  Worker   폴링이 4~8초 톱니 모양. 임계값 120초와 비교하면 여유가 크다
  재시작    postgres/redis 가 1 로 평평. 11일 전 값이 누적으로 남아 있다
```

## Phase 4 결과

```text
[만든 것]
  k8s/11-app-alerts.yaml          알람 6개
  k8s/12-grafana-dashboard.yaml   대시보드 22패널 (ConfigMap)

[확인한 것]
  rate() 의 계산이 손 계산과 일치 (2.8 vs 2.8149)
  [1m] 과 [5m] 의 반응 속도 차이
  ★ 히스토그램 버킷이 분포에서 벗어나면 분위수가 가짜다
  ★ 48Mi 였다면 사용률 99.4% — 4단계 판단의 검증
  CPU throttle 지표 이름이 _periods_total 로 바뀐 것
  알람 inactive → pending → firing → inactive 전 과정
  UI 대시보드는 Pod 재시작에 사라지고 ConfigMap 것은 남는다
```

## 남은 것 / 알고 넘어가는 것

```text
[6단계 전에 고칠 것]
  HTTP_BUCKETS 앞쪽이 성기다 (0.005 부터)
    → 실제 응답이 1~2ms 라 버킷 11개 중 10개가 논다
    → 바꾸면 과거 데이터와 비교가 안 된다. 부하 실험 후 판단

  worker_last_poll_timestamp_seconds 를 api Pod 도 0 으로 노출한다
    → 알람에서 container="worker" 로 거르고 있다
    → 근본 해결은 component 별로 등록할 지표를 나누는 것

  REGISTRY 가 런타임 지표(process_*, python_*)를 안 담는다  → Phase 3 에서 발견
  _created 시계열 13종                                      → disable_created_metrics()

[미해결]
  psycopg 풀 재시도 버그 — 닫힌 풀을 재사용한다              → 6단계 실험 소재
  Grafana 에 PVC 가 없다 — UI 로 만든 건 휘발
  build01 도커 브리지 네트워크                              → Phase 3 에서 발견

[비어 있는 패널]
  주문 접수 / Worker 처리 / 큐 / 의존 서비스 오류
  → 주문을 안 넣고 오류가 없어서다
  → 6단계에서 break-redis, worker-slow 를 주입하면 채워진다
  → 지금은 "정상일 때 비어 있다" 가 기준선이다
```

## 확인 명령

```bash
run() {
  echo "=== $1 ==="
  curl -s http://192.168.8.143:30090/api/v1/query --data-urlencode "query=$1" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d['status'] != 'success': print('  ERROR:', d.get('error')); raise SystemExit
r = d['data']['result']
if not r: print('  (비어 있음)')
for x in r: print(' ', x['metric'], x['value'][1])
"
}

# 기준선 다시 재기
run 'sum by (path) (rate(http_requests_total{namespace="bookstore", route_class!="internal"}[5m]))'
run 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route_class!="internal"}[5m])))'
run 'sum(rate(http_request_duration_seconds_bucket{le="0.005"}[5m])) / sum(rate(http_request_duration_seconds_count[5m]))'
run 'max(container_memory_working_set_bytes{namespace="bookstore", container=~"api|worker"} / on(pod, container) kube_pod_container_resource_limits{namespace="bookstore", resource="memory"})'
run 'db_pool_waiting{namespace="bookstore"}'
run 'time() - worker_last_poll_timestamp_seconds{namespace="bookstore", container="worker"}'

# 알람 상태와 health
curl -s http://192.168.8.143:30090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    if g['name'] == 'bookstore':
        for r in g['rules']: print(r['state'], r['name'], '| health:', r['health'])
"

# 대시보드 전파 구간 확인 (끊긴 곳 찾기)
grep -c "or vector(0)" /home/sjpark/k8s/12-grafana-dashboard.yaml
kubectl get cm bookstore-incident-dashboard -n monitoring \
  -o jsonpath='{.data.bookstore-incident\.json}' | grep -c "or vector(0)"
kubectl exec -n monitoring deploy/kube-prom-stack-grafana -c grafana-sc-dashboard -- \
  grep -c "or vector(0)" /tmp/dashboards/bookstore-incident.json
kubectl exec -n monitoring deploy/kube-prom-stack-grafana -c grafana -- \
  curl -s -u admin:admin http://localhost:3000/api/dashboards/uid/bookstore-incident \
  | grep -o "or vector(0)" | wc -l

# 사이드카가 다시 썼는가
kubectl logs -n monitoring deploy/kube-prom-stack-grafana -c grafana-sc-dashboard --tail=10

# 장애 주입 (알람 검증용)
POD=$(kubectl get pod -n bookstore -l app.kubernetes.io/name=api -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n bookstore $POD 9000:9000 &
curl -s http://127.0.0.1:9000/debug/state | python3 -m json.tool
curl -s -X POST http://127.0.0.1:9000/debug/inject/latency \
  -H 'Content-Type: application/json' -d '{"ttl_seconds": 600, "params": {"ms": 300, "ratio": 1.0}}'
curl -s -X DELETE http://127.0.0.1:9000/debug/inject/latency
```

## 다음

```text
Phase 5  Loki + Promtail
         지표는 "언제 이상해졌나" 를 알려준다
         로그는 "그때 무슨 일이 있었나" 를 알려준다
         → request_id 가 둘을 잇는 다리다
```

# 04. Health Check — 무엇을 보고 판단할 것인가

**이 문서의 핵심 질문 하나.**

```text
DB 가 죽었을 때 API 의 readiness 를 실패시켜야 하는가?
```

답부터 적으면 **아니다.** 다만 예외가 하나 있다. 그 이유를 정리한다.

> **주의 — 이건 "DB Pod 에 probe 를 안 붙인다" 는 말이 아니다.**
> DB Pod 에도 붙인다. 0절에서 그 둘을 먼저 나눈다.

---

## 0. 먼저 — 헷갈리기 쉬운 두 가지를 나눈다 ★

이 문서를 읽을 때 반드시 구분해야 하는 것이 있다.

```text
[A] DB Pod 자체에 probe 를 붙일 것인가
    → PostgreSQL StatefulSet 의 yaml 에 livenessProbe / readinessProbe

[B] API 의 readiness 핸들러가 DB 를 조회할 것인가
    → GET /health/ready 안에서 SELECT 1 을 할 것인가
```

**이 문서에서 "넣지 않는다" 고 하는 것은 [B] 다. [A] 는 당연히 붙인다.**

### [A] DB Pod — readiness 만 붙이고 liveness 는 붙이지 않는다 ★

> **2026-08-25 정정.** 이 문서 초판에는 DB Pod 에도 liveness 를 붙이는 예시를 넣었다.
> **틀렸다.** 관행을 따라 썼을 뿐 근거가 약했다. 아래가 수정된 판단이다.

```yaml
# postgres-statefulset.yaml — 4단계에서 쓴다

        # livenessProbe 를 두지 않는다        ★

        readinessProbe:
          exec: { command: ["pg_isready", "-U", "books_app"] }
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3        # 30초간 실패해야 뺀다
          successThreshold: 1
```

#### liveness 를 빼는 이유 다섯

```text
[1] 프로세스가 죽으면 liveness 없이도 재시작된다      ← 결정적이다
    postgres 프로세스가 죽는다 → 컨테이너가 종료된다
    → restartPolicy: Always 가 재시작한다

    liveness 가 필요한 건 "프로세스는 살아 있는데 응답을 안 하는" 경우뿐이다
```

```text
[2] DB 가 그 상태면 재시작이 답이 아니다
    디스크가 꽉 찼다 / 긴 트랜잭션이 락을 잡았다 / 커넥션이 한도를 넘었다
    → 전부 재시작해도 원인이 남는다 → 다시 멈춘다 → CrashLoopBackOff
```

```text
[3] 재시작의 대가가 API 와 비교가 안 된다
    진행 중이던 트랜잭션이 전부 롤백된다
    모든 커넥션이 끊긴다 → 앱들이 동시에 재연결한다
    shared_buffers 캐시가 날아간다 → 한동안 디스크를 직접 친다
    비정상 종료였다면 crash recovery 로 WAL 을 재생한다
```

```text
[4] 오탐의 대가가 크다
    DB 가 부하로 느려진다 → pg_isready 가 timeout → liveness 실패 → 재시작
    → 커넥션 폭주 + 캐시 콜드 → 더 느려진다
    → 살릴 수 있었던 DB 를 죽인 것이다
```

```text
[5] 원인 조사를 방해한다
    재시작하면 pg_stat_activity / pg_locks / 메모리 상태가 사라진다
    → "왜 멈췄나" 를 알 수 없다
    → 6단계 Incident Report 를 쓸 근거가 사라진다. 재발 방지도 못 한다
```

**로드맵이 요구하는 "원인 분석 → 재발 방지" 와 정면으로 어긋난다.**

#### 그럼 DB 가 멈추면 어떻게 하나

```text
readiness 가 실패한다 → Endpoint 에서 빠진다 → Pod 는 살아 있다
→ 지표와 알람이 사람을 부른다
→ 사람이 접속해서 원인을 조사한다
→ 판단해서 재시작하거나 다른 조치를 한다
```

**자동 조치 대신 사람의 판단을 남긴다.** 상태를 가진 것에는 그게 맞다.

#### readiness 는 왜 남기나

```text
liveness 실패   컨테이너를 죽인다        ← 되돌릴 수 없다
readiness 실패  트래픽만 안 보낸다        ← 되돌릴 수 있다
```

```text
[기동할 때 필요하다]
  PostgreSQL 기동에 시간이 걸린다 (crash recovery 면 더)
  readiness 가 없으면 Endpoint 에 바로 들어간다
  → 앱이 연결을 시도한다 → 실패한다 → 로그가 에러로 도배된다
```

```text
[빠른 실패가 낫다]
  readiness 없이 DB 가 멈추면
    앱이 연결은 된다 → 쿼리를 보낸다 → 응답이 없다 → 타임아웃까지 기다린다
    → 커넥션 풀이 대기로 막힌다 → 조회 요청까지 막힌다

  readiness 가 있으면
    Endpoint 에서 빠짐 → 연결 거부 → 즉시 503
    → 커넥션 풀이 안 막힌다 → 캐시된 조회는 계속 나간다
```

**3절에서 "캐시된 조회를 살린다" 고 한 이득이 여기에 달려 있다.**

```text
[다만 아주 느슨하게 잡는다]
  DB 가 하나뿐이다. 빼면 갈 곳이 없다
  → 잠깐 느린 것으로 빼면 안 된다
  → 30초간 실패해야 빠지게 한다. 진동을 막는다
```

#### 원칙 — 11편의 구분이 probe 에도 적용된다

```text
                          liveness              readiness
  상태 없는 것 (API)        적극적으로 쓴다        적극적으로 쓴다
                          재시작해도 잃는 게 없다

  상태 가진 것 (DB)         쓰지 않는다 ★          쓰되 보수적으로
                          재시작이 답이 아니고
                          잃는 게 많다
```

```text
[한 줄로]
  재시작이 해결책인 경우에만 liveness 를 쓴다
  DB 는 재시작이 해결책인 경우가 거의 없다
```

---

## 0-C. 이 판단은 소수 의견이다 — 조사 기록 ★★

**위 결론은 실무 주류와 다르다.** 그래서 조사해서 근거를 확인했다. 판단이 세 번 바뀌었고, 무엇을 새로 알아서 바뀌었는지를 남긴다.

### 조사 결과 — 두 질문이 갈렸다

```text
[B] API 의 readiness 가 DB 를 확인할 것인가
  우리 판단   확인하지 않는다
  실무 다수   확인하지 않는다        ← 일치
  공식 문서   "확인해도 된다"        ← 어긋남

[A] DB Pod 에 liveness 를 붙일 것인가
  우리 판단   붙이지 않는다
  실무 다수   붙인다 (느슨하게)      ← 어긋남
```

### [B] 는 우리 판단이 다수 의견이었다

Zalando 의 Kubernetes 플랫폼을 운영한 Henning Jacobs.

> "do not depend on external dependencies (like data stores) for your
>  Readiness/Liveness checks as this might lead to cascading failures"
>
> 열 개 Pod 가 전부 DB 헬스체크에 의존하면
> "a single DB hiccup will restart all your containers"

anti-pattern 정리 자료.

> readiness probe 가 공유 DB 를 확인하면 DB 가 죽을 때 모든 Pod 가 동시에
> Endpoint 에서 빠진다 → graceful degradation 대신 전체 장애가 된다
> "Readiness probes should test only what the pod itself controls"

**그런데 공식 문서는 반대로 말한다.**

> "When your app has a strict dependency on back-end services, you can implement
>  both a liveness and a readiness probe. ... the readiness probe additionally
>  checks that each required back-end service is available."

```text
[어느 쪽이 맞나 — 전제가 다르다]
  공식 문서의 전제
    "strict dependency" 이고
    "다른 Pod 는 정상" 이어야 뺀 의미가 있다

  우리 앱은 그 전제에 안 맞는다
    모든 Pod 가 같은 DB 를 본다 → 빼도 갈 곳이 없다
    strict dependency 가 아니다 → 캐시로 조회를 처리할 수 있다
```

Colin Breck 이 이 구분을 짚는다 — **private dependency 면 공격적으로, shared dependency 면 보수적으로.** 공유 의존성을 probe 에 넣으면 단일 장애점이 된다.

```text
→ [B] 는 우리 판단 유지. 근거가 더 단단해졌다
```

### [A] 는 우리 판단이 소수 의견이었다

```text
[Bitnami PostgreSQL 차트 기본값 — 가장 널리 쓰인다]
  livenessProbe:
    enabled: true          ← 기본으로 켜져 있다
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 6    ← 60초간 실패해야 재시작
```

```text
[CloudNativePG — CNCF PostgreSQL Operator]
  "The liveness probe is used to detect if the PostgreSQL instance is
   in a broken state and needs to be restarted."

  1.27 에서 isolationCheck 를 추가해 오히려 더 적극적으로 갔다
  primary 가 API Server 와 다른 인스턴스 양쪽에 못 닿으면 죽인다 → split-brain 방지
```

### 그런데 CNPG 의 근거는 전부 HA 전제였다 ★

```text
[CloudNativePG 가 liveness 를 쓰는 이유]
  복제본이 있다 → primary 를 죽여도 replica 가 승격한다 → 서비스가 안 멈춘다
  오히려 고립된 primary 를 죽여야 데이터가 갈라지지 않는다

[우리 상황]
  단일 인스턴스다. 승격할 대상이 없다. 죽이면 그냥 멈춘다
```

검색 결과도 이 구분을 짚는다.

> "For single-instance deployments without replicas, liveness probe restarts
>  will cause measurable downtime and should be configured conservatively."

```text
[Bitnami 는 단일 인스턴스인데도 켠다]
  다만 그건 차트 전반의 기본값이다
  모든 구성요소에 probe 를 켜두는 관행이지,
  "단일 postgres 에 왜 필요한가" 를 따진 결과로 보긴 어렵다
```

### 공식 문서는 우리 근거를 지지한다

> "If the process in your container is able to crash on its own whenever it
>  encounters an issue or becomes unhealthy, you do not necessarily need a
>  liveness probe; the kubelet will automatically perform the correct action
>  in accordance with the Pod's restartPolicy."

> "Liveness probes must be configured carefully to ensure that they truly
>  indicate unrecoverable application failure, for example a deadlock.
>  Incorrect implementation of liveness probes can lead to cascading failures."

### 결정적인 사실 — pg_isready 가 잡는 범위가 매우 좁다 ★★

PostgreSQL 공식 문서.

> "pg_isready never actually connects to the database and does not require
>  a valid database, user or password to be provided to check the server's response."

```text
[반환값]
  0  연결을 정상적으로 받아들인다
  1  연결을 거부한다 (기동 중 등)
  2  응답이 없다
  3  시도 자체를 못 했다
```

**연결을 받아들이는지만 본다. 실제로 붙지도 않는다.**

```text
                              pg_isready   liveness 가 잡나
  프로세스가 죽었다              응답 없음     잡는다
                                          (restartPolicy 가 이미 처리한다)
  디스크가 꽉 찼다               성공        못 잡는다
  긴 트랜잭션이 락을 잡았다       성공        못 잡는다
  vacuum 이 밀려 느려졌다        성공        못 잡는다
  쿼리가 전부 타임아웃난다        성공        못 잡는다
  커넥션 한도 초과               거부        잡는다
                                          (재시작하면 앱들이 다시 몰려온다)
```

```text
[남는 것]
  "프로세스는 살아 있는데 연결 수락을 못 하는" 상태뿐이다
  postmaster 는 단순한 프로세스다. 그렇게 되는 경우가 드물다
```

**"새벽 3시 hang 을 자동 복구한다" 는 근거가 여기서 무너진다.** `pg_isready` 로는 그 hang 대부분을 못 잡는다.

### 판단이 바뀐 과정

```text
[1차]  붙임        관행을 따랐다. 근거가 약했다
[2차]  뺌          restartPolicy / 재시작 대가 / 조사 방해
[3차]  붙임 권고    조사 — 주류가 붙인다. hang 대응 논리
[4차]  뺌          새로 안 사실 둘
                   CNPG 의 근거는 HA 전용이다
                   pg_isready 가 잡는 범위가 매우 좁다
```

**3차의 "hang 대응" 근거가 4차에서 무너졌다.**

### 조건이 바뀌면 판단도 바뀐다

```text
                        liveness   이유
  ────────────────────────────────────────────────────────────
  단일 인스턴스            ✗       재시작 = 다운타임
                                  pg_isready 가 잡는 범위가 좁다
                                  원인 조사가 더 중요하다

  HA (primary + replica)  ✓       재시작해도 replica 가 승격한다
                                  고립된 primary 는 죽여야 한다

  Operator 사용            —       Operator 가 알아서 판단한다

  RDS / 관리형             —       문제 자체가 없다. AWS 가 운영한다
```

**10단계에서 RDS 로 가면 이 논쟁 자체가 사라진다.** 그것도 "AWS 가 대신 처리해주는 영역" 의 하나다.

### liveness 를 뺀 대가는 알림으로 갚는다

```text
5단계에서 postgres_exporter 를 붙인다
  readiness 실패가 지속되면 알람
  연결 수 / 락 대기 / 가장 오래된 트랜잭션 나이를 지표로

liveness 가 하던 "자동 복구" 를 사람의 판단으로 대체한다
→ 그러려면 사람이 알아야 한다
```

### 참고한 자료

```text
Kubernetes 공식 — Liveness, Readiness, and Startup Probes
  https://kubernetes.io/docs/concepts/workloads/pods/probes/
PostgreSQL 공식 — pg_isready
  https://www.postgresql.org/docs/current/app-pg-isready.html
Henning Jacobs — Liveness Probes are Dangerous
  https://srcco.de/posts/kubernetes-liveness-probes-are-dangerous.html
Colin Breck — How to Avoid Shooting Yourself in the Foot
  https://blog.colinbreck.com/kubernetes-liveness-and-readiness-probes-how-to-avoid-shooting-yourself-in-the-foot/
CloudNativePG — Postgres instance manager
  https://cloudnative-pg.io/documentation/1.27/instance_manager/
CNPG Recipe 21 — Finer Control with Liveness Probes
  https://www.gabrielebartolini.it/articles/2025/08/cnpg-recipe-21-finer-control-of-postgres-clusters-with-liveness-probes/
bitnami/postgresql values.yaml
  https://github.com/bitnami/charts/blob/main/bitnami/postgresql/values.yaml
```

### 6단계에서 검증한다

```text
[실험]  postgres 를 SIGSTOP 으로 멈춘다

  A. liveness 있음   pg_isready 가 응답을 못 받는다 → 60초 뒤 재시작
                     서비스는 복구되고 원인은 지표에만 남는다
  B. liveness 없음   계속 멈춤. readiness 만 실패. 사람이 개입한다

[실험]  디스크를 채운다 / 긴 락을 만든다
  → pg_isready 가 성공하는지 확인한다
  → 성공한다면 "liveness 가 못 잡는다" 가 실측된다
```

**두 번째 실험이 이 절의 핵심 주장을 검증한다.**

### [B] API 의 readiness 안에서 DB 를 조회하지 않는다

```python
# 이걸 하지 말라는 것이다
@app.get("/health/ready")
def ready():
    db.execute("SELECT 1")      # ← 이 부분
    redis.ping()                # ← 이 부분
    return {"status": "ok"}
```

### 원칙 한 줄

```text
probe 는 "나 자신" 에 대한 판단이다. "남" 에 대한 판단이 아니다
```

```text
DB Pod 의 probe    DB 가 자기 자신을 판단한다                    ✓
API Pod 의 probe   API 가 자기 자신을 판단한다                   ✓
API 가 DB 를 판단해서 자기 readiness 에 반영한다                 ✗
```

### 전체 그림

```text
   요청 ──→ [API Pod]   probe: 나 자신만 본다
                │
                │ 연결 시도 → 실패하면 503 + 지표
                ▼
            [DB Pod]     probe: 자기 자신을 본다
```

```text
[각자 자기 상태를 책임진다]
  DB Pod    "나 죽었어" → readiness 실패 → Endpoint 에서 빠진다
  API Pod   "나 멀쩡해" → readiness 유지

[API 는 DB 장애를 어떻게 알리나]
  연결 실패 → 그 요청에 503
  → 지표를 올린다  dependency_up{name="postgres"} = 0
  → 5단계에서 그 지표에 알람을 건다
```

### DB 가 죽으면 실제로 일어나는 순서

```text
[1] PostgreSQL 이 멈춘다 (프로세스는 살아 있을 수도 있다)
[2] DB Pod 의 readiness 실패 → Endpoint 에서 빠짐        ← [A] 가 일한다
    Pod 는 죽지 않는다. liveness 를 안 붙였으므로 사람이 조사할 수 있다
[3] API 가 쿼리 시도 → 연결 실패
[4] API 가 판단한다
      조회 → 캐시에 있으면 200                            ★ 살아 있다
             캐시에 없으면 503 (DB_UNAVAILABLE)
      주문 → 503 (DB_UNAVAILABLE)
[5] API 는 readiness 를 유지한다                          ← [B] 의 판단
[6] 지표가 올라간다 → 알람
```

```text
[만약 [5] 에서 readiness 를 실패시켰다면]
  API Pod 가 전부 Endpoint 에서 빠진다 → 요청이 API 에 도달하지 못한다

  캐시된 조회도 못 한다        ← 살릴 수 있던 걸 죽인다
  503 응답도 못 준다. 연결 자체가 안 된다
  로그도 안 남는다. 요청이 안 왔으니까
  몇 건이 영향받았는지도 모른다
```

### 역할 정리

```text
DB Pod 의 readiness    DB 로 트래픽을 보낼지         (죽이지는 않는다)
API Pod 의 probe       API 를 재시작할지 / 트래픽을 보낼지
지표와 알람             "무엇이 왜 고장났나" 를 사람에게 알린다
사람                   DB 를 재시작할지 판단한다      ★ 자동화하지 않는다
```

---

## 0-B. 세 가지 probe 의 역할

```text
livenessProbe    "이 컨테이너를 죽이고 다시 만들어야 하나"
                 실패 → 컨테이너 재시작

readinessProbe   "이 Pod 에 트래픽을 보내도 되나"
                 실패 → Service 의 Endpoint 에서 빠진다. Pod 는 안 죽는다

startupProbe     "아직 기동 중인가"
                 성공할 때까지 liveness / readiness 를 미룬다
```

**04편에서 EndpointSlice 를 통해 본 그 동작이다.** readiness 가 실패하면 `ready: false` 가 되고 kube-proxy 가 그 Pod 를 규칙에서 뺀다.

---

## 1. 04편에서 저지른 실수를 먼저 본다

```text
[그때 한 것]
  postStart 로 /healthz 파일을 만들었다
  readinessProbe 가 그 파일을 GET 했다

[실험 A]  /healthz 를 지웠다 → probe 실패 → Endpoint 에서 빠졌다   정상
[실험 B]  index.html 을 지웠다
          → probe 는 계속 성공한다 (healthz 는 살아 있으니까)
          → 그런데 실제 요청은 403 을 받는다
          → 어떤 도구로도 이 상황이 안 보였다. 이벤트도 없었다
```

```text
[교훈]
  probe 가 실제 서비스 경로를 대표하지 못하면 무의미하다
  "살아 있음" 을 확인하는 게 아니라 "일할 수 있음" 을 확인해야 한다
```

### 그런데 반대 방향으로 과하게 가면 다른 사고가 난다

```text
"그럼 DB 도 Redis 도 다 확인하자"
→ 이게 이 문서에서 다룰 함정이다
```

---

## 2. livenessProbe — 의존성을 절대 보지 않는다

```text
GET /health/live  →  200 { "status": "ok" }
```

```text
[확인하는 것]
  프로세스가 살아 있고 HTTP 요청에 응답하는가. 그게 전부다

[확인하지 않는 것]
  DB / Redis / 큐 / 그 밖의 모든 외부 의존성
```

### 왜 의존성을 넣으면 안 되나 ★

```text
liveness 실패 → 컨테이너 재시작
```

```text
[DB 가 죽었을 때 liveness 에 DB 가 들어 있으면]

  1. 모든 Pod 의 liveness 가 동시에 실패한다
  2. 모든 Pod 가 재시작된다
  3. DB 는 여전히 죽어 있으니 또 실패한다
  4. CrashLoopBackOff 로 들어간다
  5. DB 가 살아난다
  6. 모든 Pod 가 동시에 기동하며 커넥션 풀을 새로 만든다
  7. 커넥션이 폭주한다 → DB 가 다시 죽는다
```

**앱을 재시작해서 DB 가 살아나지 않는다.** 재시작이 해결할 수 없는 문제에 재시작을 붙이면 상황만 나빠진다.

```text
[liveness 를 실패시켜야 하는 진짜 경우]
  이벤트 루프가 막혀 응답을 못 한다
  데드락에 빠졌다
  메모리를 다 써서 아무것도 못 한다

  → 전부 "재시작하면 해결되는" 문제다
```

### 구현 주의

```text
[핸들러 안에서 아무것도 조회하지 않는다]
  DB 커넥션을 잡지 않는다
  Redis 를 부르지 않는다
  → 그 자체가 지연 요인이 되면 안 된다

[응답이 느려도 실패다]
  이벤트 루프가 막히면 이 핸들러도 응답을 못 한다
  → 그게 liveness 가 잡아야 할 상황이다
```

---

## 3. readinessProbe — 여기가 판단이 필요한 곳 ★★

```text
GET /health/ready
  200  { "status": "ok" }
  503  { "status": "unavailable", "reason": "not_initialized" }
```

### 판단 — 런타임 의존성 상태를 넣지 않는다

```text
[넣는다]      이 Pod 만의 상태
  초기화가 끝났는가 (한 번이라도 DB 에 붙었는가)
  종료 절차에 들어갔는가 (SIGTERM 을 받았는가)
  커넥션 풀이 완전히 고갈됐는가                (넣을지는 뒤에서 논의)

[넣지 않는다]  모든 Pod 가 공유하는 것의 상태
  지금 이 순간 DB 가 살아 있는가
  지금 이 순간 Redis 가 살아 있는가
```

### 근거 1 — readiness 의 목적을 생각하면 답이 나온다

```text
readiness 는 "이 Pod 에 보낼까 말까" 를 정하는 것이다
→ 보낼 만한 Pod 와 아닌 Pod 를 구분하는 게 목적이다
```

```text
[Pod 마다 다른 문제]     readiness 가 유용하다
  이 Pod 만 초기화 중이다 → 다른 Pod 로 보내면 된다

[모든 Pod 가 같은 문제]  readiness 가 무의미하다
  DB 가 죽었다 → 모든 Pod 가 똑같이 못 쓴다
  → 뺄 대상이 전부다. 보낼 곳이 없다
```

**DB 는 모든 Pod 가 공유한다.** 빼봐야 나아지는 게 없다.

### 근거 2 — 우리 앱에서는 빼면 오히려 손해다 ★

**경로가 셋이라는 게 여기서 결정적이다.**

```text
DB 가 죽었을 때

  경로 1 (읽기)     Redis 캐시에 있으면 응답할 수 있다    ← 살아 있다
  경로 2 (주문)     못 한다
  경로 3 (Worker)   못 한다
```

```text
[readiness 를 실패시키면]
  Pod 가 전부 Endpoint 에서 빠진다
  → 캐시된 조회까지 못 하게 된다
  → 살릴 수 있었던 기능을 스스로 죽인다
```

**부분 장애를 전체 장애로 만드는 셈이다.**

### 근거 3 — 진동이 생긴다

```text
DB 가 느려진다
→ readiness 실패 → 모든 Pod 가 빠진다
→ 트래픽 0 → DB 부하가 사라진다
→ DB 회복 → readiness 성공 → 모든 Pod 복귀
→ 트래픽이 한꺼번에 몰린다 → DB 가 다시 죽는다
→ 반복
```

**스스로 만든 장애 루프다.** 회복이 더 어려워진다.

### 근거 4 — 롤링업데이트가 멈춘다

```text
Deployment 는 "새 Pod 가 ready 여야" 다음 Pod 를 교체한다
→ 모든 Pod 가 ready 실패면 배포가 중간에 멈춘다
→ DB 장애 때문에 배포까지 막힌다
```

02편에서 본 `maxUnavailable` / `maxSurge` 동작이 여기에 걸린다.

### 예외 — 기동 시점은 다르다 ★

```text
[런타임 장애]  잘 돌던 앱인데 DB 가 죽었다
              → readiness 유지. 503 을 정직하게 준다

[기동 실패]    Pod 가 처음 뜨는데 DB 에 한 번도 못 붙었다
              → readiness 실패시킨다
```

```text
[왜 다른가]
  롤링업데이트 중이라고 하자
  새 Pod 가 DB 주소 오타 때문에 못 붙었다. 기존 Pod 는 멀쩡하다

  → 새 Pod 가 ready 면 트래픽을 받아 전부 실패시킨다
  → ready 를 실패시키면 배포가 멈춘다. 기존 Pod 가 계속 처리한다
```

**"이 Pod 만의 문제" 이므로 readiness 가 제 역할을 한다.**

```text
[규칙으로 정리하면]
  "한 번이라도 초기화에 성공했는가"   → readiness 에 넣는다
  "지금 이 순간 DB 가 살아 있는가"    → 넣지 않는다
```

```text
[구현]
  전역 플래그 하나
    initialized = False
    기동 시 DB 연결에 성공하면 True 로 바꾼다
    한 번 True 가 되면 다시 False 로 안 돌린다

  readiness 는 이 플래그와 종료 여부만 본다
```

### 종료 시에는 반드시 실패시킨다

```text
SIGTERM 을 받으면 → readiness 를 즉시 실패로 바꾼다
```

```text
[왜]
  Kubernetes 가 Pod 를 지울 때 두 가지가 동시에 일어난다
    kubelet 이 SIGTERM 을 보낸다
    EndpointSlice 에서 그 Pod 를 뺀다

  그런데 규칙이 모든 노드에 퍼지는 데 시간이 걸린다
  → 그 사이에 들어온 요청이 죽어가는 Pod 로 간다
```

```text
[해결]
  SIGTERM → readiness 실패 → 잠시 기다린다 → 그다음 종료 절차
  → 04편에서 본 "반영 지연" 을 그 대기 시간으로 흡수한다
```

**02 문서의 Graceful Shutdown 과 같은 이야기다.**

---

## 4. 그럼 DB 장애를 무엇으로 알리나

**probe 가 아니라 응답·지표·전용 엔드포인트로 알린다.**

### 요청 응답

```json
503  { "error": { "code": "DB_UNAVAILABLE", "message": "..." } }
```

```text
클라이언트가 원인을 안다
로그와 지표에 남는다. 몇 건이 실패했는지 셀 수 있다
```

```text
[핵심]
  DB 가 죽었을 때 503 을 주는 것도 "정상 동작" 이다
  앱이 고장난 게 아니라, 고장을 정직하게 보고하는 것이다
```

### 지표

```text
dependency_up{name="postgres"} 0
dependency_up{name="redis"}    1
```

```text
5단계에서 여기에 알람을 건다
→ "readiness 로 알린다" 보다 훨씬 정확하다
   readiness 는 "트래픽을 보낼까" 이지 "무엇이 고장났나" 가 아니다
```

### 운영자용 엔드포인트

```text
GET /health/deps   →  항상 200. 내용에 상태를 담는다
```

```json
{
  "postgres": { "up": true,  "latency_ms": 3 },
  "redis":    { "up": false, "error": "connection refused", "since": "2026-08-25T10:00:00+09:00" }
}
```

```text
[probe 로 쓰지 않는다]
  사람이 보거나 디버깅할 때 쓴다
  Kubernetes 는 이 엔드포인트를 모른다
```

```text
[왜 항상 200 인가]
  이건 "상태 보고" 다. "판단" 이 아니다
  503 을 주면 누군가 probe 에 갖다 쓸 수 있다
```

---

## 5. 커넥션 풀 고갈은 어떻게 할 것인가

**여기는 답을 확정하지 않는다.**

```text
[상황]
  Redis 가 죽어 모든 요청이 DB 로 몰렸다
  이 Pod 의 커넥션 풀이 다 찼다
  → 새 요청은 커넥션을 기다리다 타임아웃난다
```

```text
[이건 Pod 마다 다를 수 있다]
  A Pod 는 풀이 찼고 B Pod 는 여유가 있을 수 있다
  → readiness 에 넣을 근거가 된다
```

```text
[그런데 반대 위험이 있다]
  모든 Pod 가 동시에 찰 가능성이 높다 (같은 원인이니까)
  → 전부 빠지면 3절의 문제가 그대로 재현된다
```

```text
[지금 정하는 것]
  넣지 않는다. 대신 지표로만 노출한다
    db_pool_in_use / db_pool_size / db_pool_wait_seconds

  6단계에서 실제로 고갈시켜 보고 그때 판단한다
```

**측정 전에 정하지 않는다.** 01 문서의 "응답 시간 목표를 지금 안 정한다" 와 같은 태도다.

---

## 6. Worker 의 Health Check

**Worker 는 HTTP 서버가 아니다.** 그런데도 probe 가 필요하다.

```text
[문제]
  Worker 가 큐를 소비하다 멈췄다 (데드락, 무한 대기)
  → 프로세스는 살아 있다
  → Kubernetes 는 정상으로 본다
  → 큐만 계속 쌓인다
```

**12편의 "DESIRED 0인데 지표가 정상" 과 같은 성격이다.** 조용히 아무 일도 안 한다.

```text
[해결]
  Worker 에도 작은 HTTP 서버를 띄운다 (별도 포트)

  GET /health/live
    마지막으로 큐를 확인한 시각이 N초 이내인가
    → 아니면 실패 → 재시작한다
```

```text
[readiness 는?]
  Worker 는 Service 뒤에 없다. 트래픽을 안 받는다
  → readiness 가 의미 없다. liveness 만 둔다
```

```text
[주의 — 큐가 비어 있는 것과 멈춘 것을 구분한다]
  큐가 비어서 대기 중인 것은 정상이다
  "마지막으로 큐를 확인한 시각" 을 보면 둘이 구분된다
  → 대기 중이어도 주기적으로 확인은 하니까
```

---

## 7. probe 설정값

**Manifest 는 4단계에서 쓰지만 설계는 여기서 한다.**

```yaml
startupProbe:
  httpGet: { path: /health/live, port: 8000 }
  periodSeconds: 2
  failureThreshold: 30          # 최대 60초까지 기동을 기다린다

livenessProbe:
  httpGet: { path: /health/live, port: 8000 }
  periodSeconds: 10
  timeoutSeconds: 2
  failureThreshold: 3           # 30초간 실패해야 재시작

readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  periodSeconds: 2
  timeoutSeconds: 1
  failureThreshold: 2           # 4초 만에 뺀다
  successThreshold: 1           # 1초 만에 넣는다
```

### 값을 이렇게 잡은 이유

```text
[startupProbe 가 필요한 이유]
  Python 기동이 1~3초 걸린다 (02 문서에서 예상)
  startupProbe 가 없으면 liveness 가 기동 중에 실패해 재시작을 반복할 수 있다
  → 기동이 느린 언어를 골랐으므로 이 probe 가 더 중요하다
```

```text
[readiness 를 liveness 보다 촘촘히 보는 이유]
  readiness 실패는 싸다 — 트래픽만 안 간다
  liveness 실패는 비싸다 — 컨테이너를 죽인다
  → 빼는 건 빠르게, 죽이는 건 신중하게
```

```text
[04편에서 실측한 것]
  나갈 때   failureThreshold × periodSeconds
  들어올 때 successThreshold × periodSeconds
  EndpointSlice 반영은 1초 미만이었다
```

```text
[이 값들은 확정이 아니다]
  5단계에서 실제 기동 시간과 응답 분포를 재고 조정한다
```

---

## 8. 정리하면 이렇게 나뉜다

```text
                        live      ready     지표      비고
  프로세스가 응답하나      ✓         ✓         —
  초기화가 끝났나          ✗         ✓         —       한 번이라도 붙었는가
  종료 절차에 들어갔나      ✗         ✓         —       SIGTERM 후 즉시 실패
  DB 가 살아 있나          ✗         ✗         ✓       dependency_up
  Redis 가 살아 있나       ✗         ✗         ✓       dependency_up
  커넥션 풀이 찼나         ✗         보류       ✓       6단계에서 판단
```

```text
[한 줄로]
  live    재시작하면 해결되는 문제만
  ready   이 Pod 만의 문제만
  지표     그 밖의 모든 것
```

---

## 9. 6단계에서 검증할 것 ★

**위 판단은 근거 있는 추론이지 실측이 아니다.** 반대 방향도 해보고 비교한다.

```text
[실험 A]  DB 를 readiness 에 넣고 DB 를 죽인다
  관찰    Endpoint 가 0개가 되는가
          캐시된 조회도 안 되는가
          회복 시 진동이 생기는가
          롤링업데이트가 멈추는가

[실험 B]  안 넣고 DB 를 죽인다
  관찰    조회는 캐시로 되고 주문만 503 인가
          지표로 원인이 보이는가
          회복이 매끄러운가
```

```text
[결과물]
  두 Incident Report 를 나란히 놓는다
  → "왜 이렇게 설계했는가" 를 데이터로 설명할 수 있게 된다
```

**틀렸다면 문서에 정정 표시와 함께 남긴다.** 2단계에서 네 번 그렇게 했다.

---

## 정리 — 이 문서에서 내린 판단

```text
0. 두 가지를 나눈다 ★
   [A] DB Pod 자체의 probe        → readiness 만. liveness 는 안 붙인다
   [B] API 의 readiness 가 DB 조회 → 안 한다

   원칙 한 줄: probe 는 "나 자신" 에 대한 판단이다. "남" 이 아니다

0-B. DB 에 liveness 를 안 붙이는 이유 ★ (초판 정정)
   프로세스가 죽으면 restartPolicy 가 이미 재시작한다
   liveness 가 필요한 건 "살아 있는데 멈춘" 경우인데
   DB 가 그 상태면 재시작이 답이 아니다 (원인이 남는다)
   그리고 재시작하면 진행 중 트랜잭션·캐시·조사 근거가 전부 사라진다
   → 자동 조치 대신 사람의 판단을 남긴다

   [11편의 구분이 여기에도 적용된다]
     상태 없는 것   재시작해도 잃는 게 없다  → liveness 적극적으로
     상태 가진 것   잃는 게 많다            → liveness 안 쓴다

0-C. 이건 실무 주류와 다른 소수 의견이다. 조사해서 근거를 확인했다 ★★
   Bitnami / CloudNativePG 는 붙인다. 공식 문서도 조건부로 허용한다
   그런데 CNPG 의 근거는 전부 HA 전제였다 (replica 승격 / split-brain)
   그리고 pg_isready 는 "연결을 받아들이는지" 만 본다
     디스크 full / 락 대기 / 느린 쿼리 → 전부 성공으로 나온다
   → liveness 가 잡는 범위가 매우 좁다
   → 단일 인스턴스에서는 이득보다 대가가 크다

   조건이 바뀌면 판단도 바뀐다
     HA 구성이면 붙인다 / Operator 를 쓰면 Operator 가 판단한다
     RDS 로 가면 문제 자체가 사라진다 (10단계)

1. liveness 는 의존성을 절대 보지 않는다 ★
   재시작이 해결할 수 없는 문제에 재시작을 붙이면 상황만 나빠진다
   CrashLoopBackOff + 회복 시 커넥션 폭주

2. readiness 에 런타임 의존성 상태를 넣지 않는다 ★★
   모든 Pod 가 공유하는 것이 죽으면 빼봐야 보낼 곳이 없다
   우리 앱은 경로가 셋이라 캐시된 조회가 살아 있을 수 있다
   → 부분 장애를 전체 장애로 만들지 않는다

3. 다만 "한 번이라도 초기화에 성공했는가" 는 넣는다
   기동 실패는 그 Pod 만의 문제다. 롤링업데이트를 멈춰야 한다

4. SIGTERM 을 받으면 readiness 를 즉시 실패시킨다
   규칙 전파 지연 동안 죽어가는 Pod 로 요청이 가는 걸 막는다

5. DB 장애는 지표와 503 응답으로 알린다
   503 을 주는 것도 "정상 동작" 이다. 고장을 정직하게 보고하는 것이다

6. /health/deps 를 따로 둔다. 항상 200. probe 로 쓰지 않는다
   상태 보고와 판단을 섞지 않는다

7. 커넥션 풀 고갈은 지금 정하지 않는다. 지표만 낸다
   6단계에서 실제로 고갈시켜 보고 판단한다

8. Worker 에도 liveness 를 둔다
   "마지막으로 큐를 확인한 시각" 을 본다
   조용히 멈춘 Worker 를 잡기 위해서다

9. startupProbe 를 쓴다. Python 기동이 느리다
   readiness 는 촘촘하게(빼는 건 싸다), liveness 는 느슨하게(죽이는 건 비싸다)

10. 이 판단들은 6단계에서 반대 방향도 해보고 검증한다
```

## 다음

```text
05-metrics.md   세 경로별로 무엇을 잴 것인가
                dependency_up / db_pool_* 은 여기서 설계한다
                그리고 2단계의 "조용한 실패" 네 개를 감시 항목으로 옮긴다
```

# 02. 클라우드 이식성 — 같은 이미지가 EKS 에서도 돌아야 한다

**이 문서는 뒤의 모든 문서에 걸리는 제약이다.** 설계 판단을 할 때마다 여기로 돌아온다.

---

## 0. 목표

```text
3~6단계   VMware 로컬 클러스터
10단계    AWS EKS

같은 이미지가 양쪽에서 돈다
바뀌는 것은 환경변수와 Manifest 뿐이다
```

```text
코드를 고쳐야 한다면 그건 설계가 잘못된 것이다
```

### 왜 이게 학습에 중요한가

```text
[로드맵 원칙 3]
  로컬에서 원리를 이해한 뒤 AWS 로 확장한다
  AWS 가 대신 처리해주는 영역과 Kubernetes 자체 기능을 구분하는 것이 목적이다
```

**앱이 환경에 묶여 있으면 그 구분이 불가능하다.** "EKS 로 옮기니 안 되네" 가 되면 무엇이 AWS 때문이고 무엇이 우리 코드 때문인지 알 수 없다.

```text
앱을 이식 가능하게 만들면
  → 10단계에서 바뀐 것이 순수하게 인프라 뿐이다
  → "무엇이 달라졌는가" 를 정확히 말할 수 있다
```

---

## 1. 무엇이 바뀌고 무엇이 안 바뀌는가

| 구성요소 | 3~6단계 (로컬) | 10단계 (EKS) | 앱 코드가 알아야 하나 |
|---|---|---|---|
| PostgreSQL | StatefulSet + local PV | RDS | **몰라야 한다** — 접속 주소만 |
| Redis | Deployment | ElastiCache | **몰라야 한다** — 접속 주소만 |
| 볼륨 | local PV + nodeAffinity | EBS CSI | 앱은 볼륨을 안 쓴다 |
| Ingress | Ingress Controller | ALB Ingress Controller | **몰라야 한다** |
| 이미지 저장소 | 로컬 / 사설 레지스트리 | ECR | 앱은 모른다 |
| 로그 수집 | 5단계에서 구성 | CloudWatch 또는 같은 스택 | stdout 으로만 쓴다 |
| Secret | Kubernetes Secret | Secrets Manager / Kubernetes Secret | **환경변수나 파일로 받기만** |
| 노드 | 우리가 안 죽이면 안 죽는다 | 오토스케일링 / Spot 으로 자주 바뀐다 | **SIGTERM 을 처리해야 한다** |

```text
"몰라야 한다" 가 이 문서의 핵심이다
```

---

## 2. 반드시 지킬 것

### 2-1. 모든 설정을 환경변수로 받는다

```text
[금지]
  코드에 IP, 호스트명, 포트, 비밀번호, 경로를 쓴다

[규칙]
  기본값도 코드에 두지 않는다. 없으면 기동에 실패한다
```

```python
# 나쁜 예
DATABASE_URL = "postgresql://user:pass@postgres:5432/books"

# 나쁜 예 — 기본값이 있으면 오타를 눈치채지 못한다
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/books")

# 좋은 예 — 없으면 기동 시점에 죽는다
DATABASE_URL = require_env("DATABASE_URL")
```

```text
[기본값을 두면 안 되는 이유]
  ConfigMap 에 오타를 냈다
  → 환경변수가 안 들어간다
  → 기본값(localhost)으로 붙는다
  → 붙을 리가 없으니 실패하는데, 원인이 "오타" 라는 걸 모른다

  06편에서 본 그것이다. 조용한 실패를 만들지 않는다
```

### 2-2. 기동 시점에 설정을 검증하고, 틀리면 즉시 죽는다

```text
[순서]
  1. 필요한 환경변수를 전부 읽는다
  2. 하나라도 없거나 형식이 틀리면 에러를 찍고 종료한다
  3. 그다음에 서버를 띄운다
```

```text
[왜 즉시 죽는 게 나은가]
  Kubernetes 는 죽으면 다시 띄운다
  → CrashLoopBackOff 가 되고 이벤트에 이유가 남는다
  → 운영자가 kubectl logs 로 바로 본다

  반대로 "일단 뜨고 요청이 올 때 실패" 하면
  → Pod 는 Running 이다
  → readiness 도 통과할 수 있다
  → 사용자만 500 을 받는다     ← 조용한 실패
```

**2단계 내내 겪은 그 문제를 앱에서 반복하지 않는다.**

### 2-3. 로그는 stdout 으로만 쓴다

```text
[금지]
  파일에 쓴다 (/var/log/app.log)
  로그 회전(rotation)을 앱이 한다

[규칙]
  stdout / stderr 로만 쓴다
  구조화(JSON)한다
```

```text
[왜]
  컨테이너의 파일시스템은 사라진다 (00편에서 확인)
  로컬이든 EKS 든 로그 수집은 stdout 을 읽는 방식이다
  → 파일로 쓰면 양쪽에서 다 사라진다
```

```text
[JSON 으로 쓰는 이유]
  5단계에서 로그를 모을 때 필드로 거를 수 있다
  { "level":"error", "request_id":"...", "code":"DB_UNAVAILABLE", ... }
  → "code=DB_UNAVAILABLE 인 로그만" 같은 검색이 된다
```

### 2-4. 파일시스템에 상태를 저장하지 않는다

```text
[금지]
  세션을 파일로
  캐시를 파일로
  업로드 파일을 로컬 디스크에

[규칙]
  세션·캐시  → Redis
  파일        → 지금은 기능 없음. 필요해지면 S3 (10단계)
  임시 파일   → /tmp 만. 그것도 프로세스 안에서 정리
```

```text
[왜]
  Pod 는 언제든 다른 노드에서 다시 뜬다 (10편에서 확인)
  → 파일이 따라가지 않는다

  그리고 나중에 readOnlyRootFilesystem 을 켜려면
  → 처음부터 /tmp 외에 안 써야 한다
```

### 2-5. SIGTERM 을 처리한다 — Graceful Shutdown

```text
[받으면 해야 할 일]
  1. 새 요청을 그만 받는다 (readiness 를 실패로 바꾼다)
  2. 처리 중인 요청을 끝낸다
  3. DB / Redis 연결을 닫는다
  4. 종료 코드 0 으로 끝난다
```

```text
[왜 EKS 에서 더 중요한가]
  로컬   우리가 죽이지 않으면 Pod 가 안 바뀐다
  EKS    오토스케일링으로 노드가 줄어든다
         Spot 인스턴스는 2분 전 통보 후 회수된다
         노드 그룹 업데이트로 통째로 교체된다
  → Pod 가 훨씬 자주 죽는다
```

```text
[안 하면]
  처리 중이던 주문이 중간에 끊긴다
  재고는 깎였는데 큐에 안 들어간 상태로 죽을 수 있다
```

```text
[13편에서 본 것]
  종료 신호를 처리 안 하면 종료 코드가 0이 아니다 → Error 로 기록된다
  정상 삭제와 진짜 오류가 구분이 안 된다
```

**Worker 는 더 조심해야 한다.**

```text
Worker 가 주문 하나를 처리하는 중에 SIGTERM 을 받았다
  → 지금 것은 끝내고 큐에서 새로 안 꺼낸다
  → 중간에 죽으면 그 주문은 processing 인 채로 남는다
  → 6단계에서 이 상황을 일부러 만들어본다
```

### 2-6. 의존 서비스 연결은 재시도한다

```text
[상황]
  RDS 페일오버가 일어나면 연결이 끊긴다 (30초~2분)
  ElastiCache 도 마찬가지다
  로컬에서도 StatefulSet 이 재시작하면 같다
```

```text
[규칙]
  연결 실패 시 지수 백오프로 재시도한다
  재시도 중에는 readiness 를 실패로 둔다
  일정 시간 넘으면 포기하고 503 을 준다
```

```text
[13편에서 본 지수 백오프와 같은 발상이다]
  같은 실패를 1초 간격으로 반복하는 건 낭비다
```

### 2-7. TLS 옵션을 처음부터 지원한다 ★

```text
[로컬]   PostgreSQL / Redis 를 평문으로 붙인다
[EKS]    RDS 와 ElastiCache 는 TLS 를 쓰는 경우가 많다
```

```text
[나중에 붙이면]
  코드를 고쳐야 한다 → "같은 이미지" 가 깨진다

[규칙]
  접속 설정을 통째로 환경변수로 받는다

  DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=disable   로컬
  DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require   EKS

  REDIS_URL=redis://host:6379/0        로컬
  REDIS_URL=rediss://host:6379/0       EKS (s 가 하나 더 있다)
```

**URL 하나로 받으면 TLS 여부까지 환경변수로 결정된다.** 코드는 안 바뀐다.

#### 로컬에서 TLS 를 못 하는 것 아닌가

```text
[정정]
  "TLS 를 못 한다" 가 아니라 "공인 인증서를 못 받는다" 다

  공인 인증서   Let's Encrypt / ACM. 도메인 소유 증명이 필요하다
                사설 IP(192.168.8.x)로는 못 받는다      ← 이게 안 되는 것
  자체 서명     openssl 로 직접 만든다. 언제든 가능하다
  사설 CA       mkcert 등으로 CA 를 만들어 로컬에 신뢰 등록. 경고도 안 뜬다
```

```text
[그리고 TLS 가 두 군데 있다 — 성격이 다르다]

  [1] 외부 → Ingress      사용자가 https 로 접속
  [2] 앱 → DB / Redis      내부 통신
```

**[1] 은 앱이 모른다.**

```text
로컬   사용자 → Ingress Controller (TLS 종료) → 앱   평문 HTTP
EKS    사용자 → ALB (TLS 종료) → Ingress → 앱        평문 HTTP

양쪽 다 앱은 평문만 말한다. 인증서가 뭔지도 모른다
```

```text
[바뀌는 것은 Manifest 뿐이다]
  로컬   spec.tls.secretName: books-tls          자체 서명 인증서를 Secret 으로
  EKS    alb.ingress.../certificate-arn: arn:... ACM 인증서
```

**[2] 는 라이브러리가 URL 옵션으로 처리한다.** 위에 적은 대로다.

#### 다만 함정이 하나 있다 ★

```text
sslmode 의 단계

  disable      암호화 안 함
  require      암호화만. 서버가 진짜인지는 확인 안 함     ← CA 불필요
  verify-ca    CA 로 서버 인증서를 검증                 ← CA 필요
  verify-full  CA 검증 + 호스트명 확인                  ← CA 필요
```

```text
[verify-full 을 쓰려면]
  이미지 안에 CA 인증서가 있어야 한다
  → Alpine 계열에는 ca-certificates 가 기본으로 없다
  → 없으면 10단계에서 처음 켤 때 실패한다
```

```text
[그래서 지금 할 일]
  Dockerfile 에 ca-certificates 를 넣어둔다   → 07-dockerfile.md
  → 나중에 verify-full 로 바꿔도 이미지를 다시 안 만들어도 된다
```

#### 언제 검증할 것인가

```text
3단계   평문으로 간다. TLS 는 관심사가 아니다
4단계   Ingress 에 자체 서명 인증서를 붙여본다
        → Secret 으로 인증서를 넣는 법을 익힌다 (06편 Secret 과 이어진다)
        → EKS 에서 ACM 을 붙일 때 "무엇이 달라졌나" 를 말할 수 있게 된다
5단계 이후  여유가 되면 PostgreSQL 에 자체 서명 TLS 를 켜본다
        → sslmode=require 경로를 실제로 통과시켜본다
        → 10단계에서 처음 겪지 않게 된다
```

```text
[안 해도 되는 이유]  코드가 안 바뀐다. URL 만 바뀐다
[해보면 좋은 이유]   "될 것이다" 를 "확인했다" 로 바꾼다
                    10단계에서 문제가 생기면 원인 후보가 하나 줄어든다
```

### 2-8. 프록시 뒤를 가정한다

```text
[EKS]    ALB → Ingress Controller → Pod
[로컬]   Ingress Controller → Pod

어느 쪽이든 클라이언트가 직접 오지 않는다
```

```text
[규칙]
  클라이언트 IP 는 X-Forwarded-For 에서 읽는다
  프로토콜은 X-Forwarded-Proto 에서 읽는다
  신뢰할 프록시 대역을 환경변수로 받는다
```

```text
[안 하면]
  모든 요청의 출처가 Ingress Controller 의 IP 로 보인다
  → 5단계에서 "어디서 온 요청인가" 를 알 수 없다
  → 6단계에서 특정 클라이언트만 막는 실험도 못 한다
```

### 2-9. 자기 신원을 지표에 남긴다

```text
[받아야 할 것 — Downward API]
  POD_NAME       metadata.name
  POD_NAMESPACE  metadata.namespace
  NODE_NAME      spec.nodeName
```

```text
[왜]
  "어느 Pod 가 느린가" "어느 노드에서만 실패하는가" 를 봐야 한다
  → 12편에서 kube-proxy 가 쓰던 그 방식이다
```

```text
[EKS 에서 더 중요하다]
  노드가 자주 바뀐다
  "특정 가용영역의 노드에서만 DB 지연이 크다" 같은 걸 볼 수 있어야 한다
```

### 2-10. 시각은 UTC 로 다루고 표시만 바꾼다

```text
[규칙]
  DB 에 저장   timestamptz (UTC)
  로그         UTC + 오프셋 표기
  API 응답     ISO 8601 + 오프셋
```

```text
[13편에서 겪은 것]
  CronJob 이 UTC 로 돌아 "새벽 3시" 가 낮 12시가 됐다
  컨테이너 안 date 는 타임존 데이터가 없어 UTC 로 찍혔다
```

```text
[EKS 에서]
  노드의 시간대는 보통 UTC 다
  → 로컬(KST 노드)과 다르게 동작하면 안 된다
  → 앱이 시스템 시간대에 의존하면 환경마다 달라진다
```

---

## 3. 하지 말 것 — 정리

```text
1. IP / 호스트명 / 포트 / 경로를 코드에 쓴다
2. 환경변수에 기본값을 둔다                    ← 오타를 숨긴다
3. 파일에 로그를 쓴다
4. 파일시스템에 세션·캐시·업로드를 둔다
5. 컨테이너 안에서 스케줄러(cron)를 돌린다      ← CronJob 을 쓴다
6. localhost 로 다른 서비스를 부른다           ← 사이드카가 아니면 틀렸다
7. 특정 노드·경로·볼륨이 있다고 가정한다
8. 시스템 시간대에 의존한다
9. SIGTERM 을 무시한다
10. 기동 시 설정 검증 없이 일단 뜬다
```

---

## 4. 환경변수 목록 (초안)

```text
[필수 — 없으면 기동 실패]
  DATABASE_URL              postgresql://...?sslmode=...
  REDIS_URL                 redis:// 또는 rediss://

[선택 — 기본값을 코드에 둘 수 있는 것]
  APP_PORT                  8000
  LOG_LEVEL                 info
  CACHE_TTL_SECONDS         60
  DB_POOL_SIZE              10
  DB_POOL_MAX_OVERFLOW      5
  QUEUE_NAME                order_queue
  WORKER_CONCURRENCY        1
  WORKER_PROCESS_SECONDS    1.0        처리 시간 흉내
  WORKER_FAILURE_RATE       0.0        실패 확률 흉내
  SHUTDOWN_GRACE_SECONDS    20
  TRUSTED_PROXIES           10.244.0.0/16

[디버그 — 기본 꺼짐]
  ENABLE_DEBUG_ENDPOINTS    false

[Downward API 로 주입]
  POD_NAME / POD_NAMESPACE / NODE_NAME
```

```text
[필수와 선택을 나누는 기준]
  환경마다 반드시 달라지는 것        → 필수. 기본값 없음
  대부분 그대로 써도 되는 것         → 선택. 기본값 허용

  DATABASE_URL 에 기본값을 두면 오타 시 조용히 엉뚱한 곳에 붙는다
  CACHE_TTL 은 틀려도 티가 난다
```

---

## 5. 이식성을 확인하는 방법

**"EKS 에서도 될 것이다" 는 믿음이 아니라 확인해야 한다.**

```text
[3단계에서]
  Docker Compose 로 띄운다
  → 환경변수만 바꿔 다른 DB 주소로 붙여본다

[4단계에서]
  Kubernetes 에 올린다
  → ConfigMap / Secret 으로 같은 환경변수를 주입한다
  → 이미지는 3단계 것 그대로다               ★ 재빌드하면 실패다

[10단계에서]
  RDS / ElastiCache 로 바꾼다
  → 환경변수만 바꾼다
  → 이미지가 그대로인지 확인한다             ★ 이게 최종 검증이다
```

```text
[10단계 결과물로 남길 것]
  "무엇이 바뀌었나" 표
    바뀐 것    Manifest / 환경변수 / IAM / 보안그룹
    안 바뀐 것  이미지 다이제스트                  ← 같아야 한다
```

**이미지 다이제스트가 같으면 이식성이 증명된다.**

---

## 6. 지금 정하지 않는 것

```text
1. 멀티 아키텍처 이미지 (amd64 / arm64)
   EKS 에서 Graviton 노드를 쓰면 arm64 가 필요하다
   → 8단계 CI 에서 buildx 로 처리한다. 지금은 amd64 만

2. IAM 기반 DB 인증 (RDS IAM Authentication)
   → 10단계. 지금은 비밀번호

3. Secrets Manager 연동
   → 10단계. 지금은 Kubernetes Secret
   → 어느 쪽이든 앱은 "환경변수로 받는다" 만 안다

4. 읽기 전용 루트 파일시스템
   → 4단계에서 켜본다. 지금은 /tmp 외에 안 쓰는 것만 지킨다

5. 내부 통신 TLS 를 실제로 켜보는 것
   → 4단계(Ingress) / 5단계 이후(DB)
   → 지금은 URL 을 통째로 받는 것과 ca-certificates 를 넣어두는 것까지
```

**지금 안 하더라도 나중에 코드를 안 고쳐도 되게 길만 열어둔다.**

```text
[구체적으로 열어두는 길 세 가지]
  DATABASE_URL / REDIS_URL 을 통째로 받는다   → sslmode / rediss 로 켤 수 있다
  이미지에 ca-certificates 를 넣는다          → verify-full 로 갈 수 있다
  X-Forwarded-Proto 를 본다                  → TLS 종료 위치가 바뀌어도 된다
```

---

## 정리

```text
[목표]
  같은 이미지가 로컬 클러스터와 EKS 양쪽에서 돈다
  바뀌는 것은 환경변수와 Manifest 뿐

[지킬 것 10가지]
 1. 모든 설정을 환경변수로. 필수 값에는 기본값을 두지 않는다
 2. 기동 시 설정을 검증하고 틀리면 즉시 죽는다
 3. 로그는 stdout 으로만. JSON 으로 구조화
 4. 파일시스템에 상태를 저장하지 않는다
 5. SIGTERM 을 처리한다 (EKS 는 Pod 가 훨씬 자주 죽는다)
 6. 의존 서비스 연결을 지수 백오프로 재시도한다
 7. 접속 설정을 URL 하나로 받아 TLS 여부까지 환경변수로 결정한다 ★
    로컬에서 TLS 를 안 켜도 된다. 코드가 안 바뀌기 때문이다
    다만 이미지에 ca-certificates 를 넣어둔다 (verify-full 대비)
 8. 프록시 뒤를 가정한다 (X-Forwarded-For / X-Forwarded-Proto)
    TLS 종료는 Ingress 나 ALB 가 한다. 앱은 평문만 말한다
 9. 자기 신원(Pod/Node)을 지표에 남긴다
10. 시각은 UTC 로 다루고 표시만 바꾼다

[검증]
  10단계에서 이미지 다이제스트가 안 바뀌면 성공이다
```

## 다음

```text
03-data-model.md    이 제약 아래에서 테이블을 설계한다
                    특히 시각 컬럼을 timestamptz 로
```

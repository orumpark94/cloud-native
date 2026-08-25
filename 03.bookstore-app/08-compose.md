# 08. Docker Compose — 로컬 개발 환경

로드맵 3단계 결과물의 하나다.

```text
"로컬 Docker Compose 개발 환경"
```

**그런데 이 문서의 절반은 "Compose 에 의존하지 않는 법" 이다.**

---

## 0. 함정을 먼저 본다 ★★

Compose 는 편한 기능을 여럿 준다. **그중 일부는 Kubernetes 에 없다.**

```text
[Compose 에는 있는데 Kubernetes 에는 없는 것]

  depends_on: condition: service_healthy
    "DB 가 준비된 뒤에 앱을 띄운다"
    → Kubernetes 에는 이런 게 없다. Pod 는 순서 없이 뜬다

  restart: on-failure:3
    Kubernetes 는 restartPolicy 에 횟수 제한이 없다

  자동 DNS 로 서비스 이름 해석
    → 이건 Kubernetes 에도 있다 (Service). 다만 이름 규칙이 다르다
```

```text
[Compose 에 의존하면]
  로컬에서는 잘 된다
  → 4단계에서 Kubernetes 에 올리면 깨진다
  → "로컬에서는 됐는데요" 가 나온다
```

**02 문서에서 "앱이 의존 서비스 연결을 재시도해야 한다" 고 정한 이유가 이것이다.**

### 그럼 `depends_on` 을 쓸 것인가

```text
[쓴다. 다만 의존하지 않는다]

  쓰는 이유    개발할 때 로그가 덜 지저분하다
              DB 가 뜨기 전에 앱이 연결 실패를 100번 찍지 않는다

  의존하지 않는다는 뜻
    depends_on 을 지워도 앱이 정상적으로 떠야 한다
    → 앱이 스스로 재시도하기 때문이다
```

```text
[검증 방법]
  depends_on 을 지운 Compose 파일로 한 번 띄워본다
  → 앱이 재시도하다가 결국 뜨는지 확인한다
  → 안 뜨면 앱 코드가 잘못된 것이다. Compose 문제가 아니다
```

**이 검증을 3단계에서 해두면 4단계에서 안 깨진다.**

---

## 1. 구성

```text
postgres    데이터베이스
redis       캐시 + 큐
api         FastAPI. 8000 (서비스) / 9000 (관리)
worker      같은 이미지, 다른 명령
```

```text
[선택적으로]
  k6          부하 테스트 (프로파일로 분리)
  adminer     DB 를 눈으로 보는 도구 (프로파일로 분리)
```

---

## 2. compose.yaml

```yaml
name: books-app

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: books
      POSTGRES_USER: books_owner
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?required}
      TZ: UTC
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./sql:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U books_owner -d books"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      dockerfile: Dockerfile
    image: books-app:dev
    environment:
      DATABASE_URL: postgresql://books_app:${APP_DB_PASSWORD:?required}@postgres:5432/books?sslmode=disable
      REDIS_URL: redis://redis:6379/0
      LOG_LEVEL: debug
      ENABLE_DEBUG_ENDPOINTS: "true"
      POD_NAME: api-local
      NODE_NAME: local
      TZ: UTC
    ports:
      - "8000:8000"
      - "9000:9000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    image: books-app:dev
    command: ["python", "-m", "app.worker"]
    environment:
      DATABASE_URL: postgresql://books_app:${APP_DB_PASSWORD:?required}@postgres:5432/books?sslmode=disable
      REDIS_URL: redis://redis:6379/0
      LOG_LEVEL: debug
      WORKER_PROCESS_SECONDS: "1.0"
      WORKER_FAILURE_RATE: "0.0"
      POD_NAME: worker-local
      NODE_NAME: local
      TZ: UTC
    ports:
      - "9001:9000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  pgdata:
```

---

## 3. 판단들

### 3-1. `image: books-app:dev` 를 명시한다

```text
[build 만 쓰면]
  Compose 가 알아서 이름을 짓는다  (books-app-api 같은)
  → worker 가 같은 이미지를 못 쓴다
```

```text
[image 를 명시하면]
  api 가 빌드한 것을 worker 가 그대로 쓴다
  → 07 문서의 "같은 이미지, 다른 명령" 이 Compose 에서도 성립한다
  → 빌드가 한 번이다
```

### 3-2. `${POSTGRES_PASSWORD:?required}`

```text
[문법]
  ${VAR}              없으면 빈 문자열
  ${VAR:-기본값}       없으면 기본값
  ${VAR:?메시지}       없으면 에러를 내고 멈춘다      ← 이걸 쓴다
```

```text
[왜]
  02 문서의 규칙 — 필수 값에 기본값을 두지 않는다
  → .env 를 안 만들었으면 즉시 멈춘다
  → 빈 비밀번호로 뜨는 것보다 낫다
```

**"조용히 잘못된 상태로 뜨지 않는다" 를 Compose 층에서도 지킨다.**

### 3-3. `.env` 파일과 커밋 금지 ★

```text
[.env]
  POSTGRES_PASSWORD=localdev
  APP_DB_PASSWORD=localdev
```

```text
[.gitignore 와 .dockerignore 양쪽에 넣는다]
  .env
  .env.*
  !.env.example
```

```text
[.env.example 은 커밋한다]
  POSTGRES_PASSWORD=
  APP_DB_PASSWORD=
  → 무엇이 필요한지 알려주되 값은 비운다
```

**07 문서에서 `.dockerignore` 를 보안 항목으로 다룬 이유가 여기서도 같다.**

### 3-4. 관리 포트를 노출한다

```text
api      8000 (서비스) / 9000 (관리)
worker   9001 → 컨테이너의 9000
```

```text
[왜 로컬에서는 노출하나]
  /metrics 를 브라우저로 확인한다
  /debug/* 로 장애를 주입해본다
  → 06 문서의 엔드포인트를 여기서 시험한다
```

```text
[Kubernetes 에서는 다르다]
  Service 에 9000 을 넣지 않는다
  → 클러스터 밖에서 못 닿게 한다
  → 로컬 편의와 배포 정책을 구분한다
```

### 3-5. worker 의 포트가 9001 인 이유

```text
같은 9000 을 두 서비스가 호스트에 노출할 수 없다
→ 컨테이너 안은 둘 다 9000, 호스트 쪽만 다르게 한다
```

```text
[Kubernetes 에서는 이 문제가 없다]
  Pod 마다 자기 IP 가 있다 (hostNetwork 가 아니면)
  → 둘 다 9000 을 써도 충돌하지 않는다
  → 12편에서 본 그 차이다
```

### 3-6. `TZ: UTC` 를 명시한다

```text
[02 문서의 규칙]
  시각은 UTC 로 다루고 표시만 바꾼다
```

```text
[안 쓰면]
  로컬(KST)과 클러스터(UTC)가 다르게 동작한다
  → 13편에서 CronJob 이 9시간 어긋난 그 문제다
  → 로컬에서도 UTC 로 맞춰 차이를 없앤다
```

### 3-7. 초기 데이터 주입

```yaml
volumes:
  - ./sql:/docker-entrypoint-initdb.d:ro
```

```text
[postgres 이미지의 동작]
  데이터 디렉터리가 비어 있을 때만 이 디렉터리의 .sql 을 실행한다
  → 두 번째 기동부터는 안 한다
```

```text
[03 문서에서 정한 것]
  스키마 적용을 앱 코드에 넣지 않는다
  → 여기서는 postgres 이미지 기능으로
  → 4단계에서는 Job 으로 (Pod 3개가 동시에 만들려는 문제)
```

```text
[다시 초기화하려면]
  docker compose down -v      ← 볼륨까지 지운다
```

### 3-8. 소스를 볼륨으로 마운트할 것인가

**개발 편의와 "같은 이미지" 원칙이 부딪히는 지점이다.**

```yaml
# 개발용으로 얹는다면
    volumes:
      - ./app:/app:ro
    command: ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--reload"]
```

```text
[얻는 것]
  코드를 고치면 자동으로 다시 로드된다
  → 매번 빌드를 안 해도 된다
```

```text
[주의 — 이건 "다른 이미지" 가 아니다]
  같은 이미지에 볼륨을 얹고 명령을 바꾸는 것이다
  → 02 문서의 원칙을 깨지 않는다
```

```text
[다만 함정이 있다]
  볼륨으로 얹은 소스는 이미지 안의 소스를 가린다
  → "이미지에 소스가 안 들어갔는데도 잘 도는" 상황이 생긴다
  → 빌드가 깨진 걸 모르고 지나칠 수 있다
```

```text
[대책]
  기본 compose.yaml 에는 볼륨을 안 넣는다
  개발용은 compose.override.yaml 로 분리한다
  → 배포 전에는 override 없이 한 번 띄워본다
```

### 3-9. 프로파일로 선택적 서비스를 분리한다

```yaml
  k6:
    image: grafana/k6:latest
    profiles: ["load"]
    volumes:
      - ./loadtest:/scripts:ro
    command: ["run", "/scripts/order.js"]

  adminer:
    image: adminer:latest
    profiles: ["tools"]
    ports:
      - "8080:8080"
```

```text
docker compose up                      기본 서비스만
docker compose --profile tools up      adminer 도 같이
docker compose --profile load run k6   부하 테스트만 실행
```

```text
[왜 나누나]
  평소에 안 쓰는 것까지 매번 띄우면 메모리를 먹는다
  → 우리 노트북 자원은 유한하다
```

---

## 4. Compose 와 Kubernetes 대응표 ★

**4단계 예습이다.** 지금 만드는 것이 무엇으로 바뀌는지 미리 본다.

| Compose | Kubernetes | 주의 |
|---|---|---|
| `services.api` | Deployment + Service | 하나가 둘로 나뉜다 |
| `image` | `spec.containers[].image` | 같다 |
| `command` | `spec.containers[].command` | 같다 |
| `environment` | ConfigMap + Secret | 비밀은 반드시 Secret 으로 |
| `ports` | Service 의 port / targetPort | Compose 는 호스트 포트, K8s 는 클러스터 안 |
| `volumes` (이름 있는) | PVC | 09~10편에서 본 그것 |
| `volumes` (바인드) | hostPath / ConfigMap 볼륨 | hostPath 는 노드에 묶인다 (09편) |
| `healthcheck` | livenessProbe / readinessProbe | **역할이 하나에서 둘로 나뉜다** (04 문서) |
| `depends_on` | **없다** | 앱이 재시도해야 한다 ★ |
| `restart: always` | `restartPolicy: Always` | K8s 는 횟수 제한이 없다 |
| `deploy.replicas` | `spec.replicas` | Compose 에서는 잘 안 쓴다 |
| 서비스 이름으로 DNS | Service 이름으로 DNS | K8s 는 `이름.네임스페이스.svc.cluster.local` |
| `profiles` | 없음 | 다른 Manifest 나 Kustomize 로 |

### 특히 조심할 두 가지

```text
[1] depends_on 이 없다
  → 앱이 재시도해야 한다 (02 문서)
  → 0절에서 검증 방법을 정했다

[2] healthcheck 가 둘로 나뉜다
  Compose      healthcheck 하나
  Kubernetes   liveness (죽일까) / readiness (트래픽을 보낼까)
  → 04 문서에서 판단한 그대로 나눈다
  → Compose 의 healthcheck 를 그대로 옮기면 안 된다
```

---

## 5. 사용 흐름

```bash
# 처음 한 번
cp .env.example .env
# .env 를 채운다

# 띄운다
docker compose up -d --build

# 확인
curl localhost:8000/health/live
curl localhost:9000/health/ready
curl localhost:9000/metrics | head -30

# 책 조회
curl "localhost:8000/books?limit=5"

# 주문
curl -X POST localhost:8000/orders \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: 1' \
  -d '{"book_id":1,"quantity":1}'

# 로그
docker compose logs -f api worker

# 장애 주입 (06 문서)
curl -X POST localhost:9000/debug/latency \
  -d '{"ms":500,"ratio":1.0,"ttl_seconds":60}'
curl localhost:9000/debug/state

# 정리
docker compose down          # 컨테이너만
docker compose down -v       # 볼륨까지 (DB 초기화)
```

---

## 6. 여기서 검증할 것 ★

**Compose 는 "돌려보는 곳" 이 아니라 "설계가 맞는지 확인하는 곳" 이다.**

```text
[01 문서 — API 명세]
  큐 등록이 실패하면 롤백되는가
  → Redis 를 멈추고 주문을 넣어본다
     docker compose stop redis
  → 503 이 오고 재고가 안 깎였는지 확인한다

[02 문서 — 이식성]
  필수 환경변수를 빼면 즉시 죽는가
  → DATABASE_URL 을 비우고 띄워본다
  SIGTERM 을 받으면 정상 종료하는가
  → docker compose stop 하고 종료 코드를 본다 (0 이어야 한다)

[03 문서 — 데이터 모델]
  재고가 음수가 되는가 (1차 SQL 이면 되어야 한다)
  → 동시 주문을 넣어본다

[04 문서 — Health Check]
  DB 를 멈춰도 ready 가 유지되는가
  → docker compose stop postgres
  → /health/ready 가 200 인지 확인한다        ← 이게 핵심 검증이다
  → /health/deps 에는 postgres 가 down 으로 나오는지

[05 문서 — 지표]
  Redis 를 멈추면 cache miss 가 오르는가
  → curl localhost:9000/metrics | grep cache

[06 문서 — 장애 주입]
  TTL 이 지나면 자동으로 해제되는가
  → 주입하고 기다렸다가 /debug/state 를 다시 본다

[07 문서 — 이미지]
  비-root 로 도는가
  → docker compose exec api id
  → uid=10001 이어야 한다
```

**각 문서에서 "이렇게 될 것이다" 라고 쓴 것을 여기서 확인한다.** 틀린 게 있으면 정정 표시와 함께 문서를 고친다.

---

## 7. 하지 않는 것

```text
1. Compose 를 배포에 쓰지 않는다
   로컬 개발 전용이다. 4단계부터는 Kubernetes 다

2. Compose 로 여러 환경을 만들지 않는다
   compose.prod.yaml 같은 건 안 만든다
   → 02 문서의 "환경별 이미지를 만들지 않는다" 와 같은 이유

3. Compose 의 depends_on 에 의존하는 코드를 쓰지 않는다
   → 0절에서 검증한다

4. Compose 의 healthcheck 를 Kubernetes probe 로 그대로 옮기지 않는다
   → 04 문서에서 따로 판단했다
```

---

## 정리 — 이 문서에서 내린 판단

```text
1. Compose 의 편한 기능에 의존하지 않는다 ★★
   depends_on 은 Kubernetes 에 없다
   → 쓰되, 지워도 앱이 떠야 한다. 그걸 검증한다

2. image 를 명시해 api 와 worker 가 같은 이미지를 쓰게 한다
   07 문서의 "같은 이미지, 다른 명령" 을 Compose 에서도 유지

3. 필수 환경변수에 ${VAR:?} 를 쓴다
   없으면 즉시 멈춘다. 02 문서의 규칙을 Compose 층에서도

4. .env 는 커밋하지 않는다. .env.example 만 커밋한다

5. 관리 포트를 로컬에서는 노출한다
   Kubernetes 에서는 Service 에 안 넣는다
   → 로컬 편의와 배포 정책을 구분한다

6. TZ: UTC 를 명시한다
   로컬과 클러스터의 동작을 같게 만든다 (13편의 그 문제)

7. 소스 볼륨 마운트는 override 파일로 분리한다
   볼륨이 이미지 안 소스를 가려 빌드 실패를 못 볼 수 있다

8. 선택적 서비스는 프로파일로 분리한다
   노트북 자원이 유한하다

9. Compose 는 "설계가 맞는지 확인하는 곳" 이다 ★
   각 문서의 주장을 여기서 검증한다
   특히 04 문서의 "DB 를 멈춰도 ready 가 유지되는가"
```

## 다음

```text
09-implementation.md   실제로 만든다
                       측정값을 기록한다 (07 문서의 표)
                       6절의 검증 결과를 기록한다
                       예상과 다른 것은 정정 표시와 함께 남긴다
```

# 09. 구현과 첫 실행

설계 문서 00~08 을 코드로 옮기고, 빌드 환경을 만들고, 처음 실행한 기록이다.
**시간순으로 적는다.** 겪은 순서 그대로여야 나중에 재현할 수 있다.

작업일: 2026-08-25 ~ 2026-08-26

---

## 1. 구현 순서 — 조각 9개로 나눴다

설계 문서를 다 쓴 뒤 코드를 한 번에 만들지 않고 조각으로 나눴다.
한 조각마다 "이 조각이 무엇을 하는가 / 어느 설계 판단이 코드가 됐는가 / 장애가 나면 여기를 본다" 를 정리하며 진행했다.

| 조각 | 파일 | 무엇을 정하는가 |
|---|---|---|
| 1 | `config.py` `logging_setup.py` | 설정이 틀리면 즉시 죽는다 / JSON 로그 |
| 2 | `deps.py` `errors.py` | 연결과 재시도 / 유한한 에러 코드 |
| 3 | `metrics.py` `runtime.py` | 지표 정의 / readiness 판단 |
| 4 | `middleware.py` `health.py` `faults.py` | RED 수집 / probe / 주입 상태 |
| 5 | `db.py` `cache.py` `repositories/books.py` `routers/books.py` | 경로 1 (읽기) |
| 6 | `queue.py` `repositories/orders.py` `routers/orders.py` | 경로 2 (주문) |
| 7 | `worker.py` | 경로 3 (비동기 처리) |
| 8 | `debug.py` | 장애 주입 엔드포인트 |
| 9 | `main.py` | 조립 / SIGTERM 처리 |

최종 파일 수

```bash
find Books-app/app -name "*.py" ! -name "__init__.py" | wc -l
```

```text
19
```

```text
app/ 직속 15개 + repositories 2 + routers 2
```

---

## 2. 빌드 환경 — build01 VM 을 만들었다

### 왜 별도 VM 인가

개발 PC 는 Windows 이고 Docker 가 없었다. 세 가지를 검토했다.

| 방법 | 문제 |
|---|---|
| Windows 에 Docker Desktop | WSL2/Hyper-V 를 켜야 한다 → VMware 로 돌리는 1단계 클러스터가 느려지거나 깨질 수 있다 |
| master01 에 Docker 설치 | Docker 가 자기 containerd 를 따로 띄운다 → k8s 의 containerd 와 두 개 공존 |
| **별도 build VM** | vCPU 오버커밋 (6/6 → 8/6). 빌드는 짧고 간헐적이라 감수 |

세 번째를 골랐다. **"빌드하는 곳" 과 "실행하는 곳" 이 분리되는 구조가 8단계의 CI 러너 역할과 그대로 대응된다.**

### 사양

| 항목 | 값 |
|---|---|
| hostname | `build01` |
| IP | 192.168.8.144 (고정) |
| vCPU / RAM | 2 / 4GB |
| 디스크 | 68.35GB |
| OS | Ubuntu 24.04.3 LTS, 커널 6.8.0-138 |

> 6단계 k6 부하 테스트 중에는 build01 을 반드시 끈다.
> 안 그러면 "응답이 느려진 것" 이 호스트 CPU 경합인지 Kubernetes 문제인지 구분이 안 된다.

### Docker 설치

Ubuntu 저장소의 `docker.io` 를 쓰지 않았다. 버전이 오래됐고 `compose` v2 플러그인이 없다.

```bash
# 저장소 등록
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
     -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

설치된 버전

```text
Docker Engine    29.7.2
containerd       v2.3.3
runc             1.4.3
Compose          v5.5.0
buildx           0.36.1
```

### 겪은 문제 1 — `$USER` 가 root 였다

```bash
root@build01:/home/sjpark# sudo usermod -aG docker $USER
```

```text
root 셸이라 $USER 가 root 로 치환됐다
→ root 를 docker 그룹에 넣었다. sjpark 은 그대로다
→ 명령은 성공했고 출력도 없어서 눈치채지 못했다
```

증상

```bash
sjpark@build01:~$ docker ps
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock

sjpark@build01:~$ id
uid=1000(sjpark) gid=1000(sjpark) groups=1000(sjpark),4(adm),...,101(lxd)
                                                        ← docker 가 없다
```

수정

```bash
sudo usermod -aG docker sjpark
# SSH 를 완전히 끊고 재접속한다. 그룹은 로그인 시점에 정해진다
```

**worker01 의 sudoers 설정에서도 같은 실수를 했다.**

```bash
root@worker01# echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/ctr" | sudo tee /etc/sudoers.d/ctr-import
# → "root ALL=(ALL) NOPASSWD: ..." 로 들어갔다. root 는 sudo 가 필요 없으므로 무의미하다
```

```text
재발 방지
  root 셸에서 $USER / $HOME / ~ 는 전부 root 를 가리킨다
  대상 사용자가 있는 명령은 이름을 직접 쓴다
```

### 코드 전달 방식

GitHub 을 거치지 않고 Windows 에서 직접 보낸다.

```powershell
scp -r d:\SJPARK\cloud-native\Books-app sjpark@192.168.8.144:~/
```

```text
[이 방식을 고른 이유]
  8단계에서 GitHub Actions 를 쓸 예정이다
  지금 git 기반 배포 흐름을 만들면 그 학습이 앞당겨진다
  → 3단계에서는 손으로 옮긴다

[대가]
  git sha 태그를 못 쓴다 → 날짜시간 태그로 대체한다
  "이 이미지가 어느 코드인가" 를 이 문서에 손으로 기록해야 한다
```

---

## 3. 첫 빌드

```bash
cd ~/Books-app
TAG=$(date +%Y%m%d-%H%M)
time docker build -t bookstore:$TAG .
```

### 결과

```text
real    0m23.711s
```

```text
DISK USAGE   265MB      노드에서 풀어놨을 때
CONTENT SIZE 62.3MB     전송되는 압축 크기
```

### 출력에서 확인한 것 3가지

**1. 두 스테이지가 병렬로 돌았다**

```text
=> [builder 2/4]  WORKDIR /build                        0.4s
=> [stage-1 2/6]  RUN apt-get update ...                6.8s     ← 2스테이지가 먼저
=> [builder 3/4]  COPY requirements.txt .               0.0s
=> [builder 4/4]  RUN pip install ...                   8.2s
=> [stage-1 3/6]  RUN groupadd ...                      0.5s
=> [stage-1 4/6]  COPY --from=builder /install ...      0.4s      ← 여기서 합류
```

```text
BuildKit 은 Dockerfile 을 위에서 아래로 실행하지 않는다
의존 그래프를 만들고 무관한 것은 동시에 돌린다
→ COPY --from=builder 에서 처음 의존이 생기고, 거기서만 기다린다

멀티스테이지의 이점이 크기 절감만이 아니다. 병렬성도 같이 얻는다
```

**2. `.dockerignore` 가 동작했다**

```text
=> [internal] load build context
=> => transferring context: 167.81kB
```

**3. attestation manifest 가 붙었다**

```text
=> => exporting attestation manifest sha256:4b4542e...
=> => exporting manifest list sha256:ba65ff7e...
```

```text
Docker 29 의 BuildKit 은 기본으로 provenance attestation 을 붙인다
→ 이미지가 단일 manifest 가 아니라 manifest list 가 된다

★ 아직 확인하지 못한 것
  docker save → ctr import 할 때 이 구조가 문제를 일으키는지 모른다
  containerd 가 "unknown/unknown" 플랫폼 항목을 만나게 된다
  → 4단계에서 Pod 를 띄울 때 확인한다
  → 문제가 되면 --provenance=false 로 다시 빌드하고 로그를 비교한다
```

### 설계 문서(07)와 달라진 점 2가지

문서대로 쓰면 동작하지 않아서 구현하며 바로잡았다.

| 07 문서 | 실제 | 이유 |
|---|---|---|
| `WORKDIR /app` + `COPY ./app /app` | `WORKDIR /srv` + `COPY ./app /srv/app` | `/app` 에 코드를 두고 WORKDIR 도 `/app` 이면 python 이 `app` 패키지를 못 찾는다. `import app.main` 이 되려면 `app/` 의 부모가 작업 경로여야 한다 |
| `CMD ["python","-m","uvicorn","app.main:app",...]` | `CMD ["python","-m","app.main"]` | 포트가 둘(8000/9000)이라 uvicorn 명령 하나로는 하나만 뜬다. SIGTERM 순서도 직접 통제해야 한다 |

---

## 4. 첫 실행 — 장애 두 개

```bash
docker compose up --build
```

### 정상 동작한 것들

```text
{"msg": "PostgreSQL 연결 재시도", "attempt": 1}       ← depends_on 조건 없이 버텼다
{"msg": "PostgreSQL 연결 성공", "attempt": 1}

"database": "postgresql://bookstore:***@db:5432/bookstore"   ← 비밀번호 가려짐
{"msg": "장애 주입 엔드포인트가 켜져 있다"}                   ← 1겹이 스스로 경고
"stock_strategy": "none"                                    ← 의도한 기본값
```

```text
db-1 | /docker-entrypoint-initdb.d/01_schema.sql
db-1 | CREATE TABLE
db-1 | CREATE TABLE
db-1 | INSERT 0 1000
```

접속 로그에 `route_class` 가 붙는 것도 확인했다.

```json
{"logger": "app.access", "path": "/health/live", "raw_path": "/health/live",
 "status": 200, "duration_ms": 0.34, "route_class": "internal"}
```

---

### 장애 1 — Worker 가 큐를 한 건도 못 읽었다 ★★

#### 증상

```text
컨테이너        4개 전부 Up
/health/live   200
/health/ready  200
POST /orders   202 Accepted
Redis          정상 (ping 성공)
PostgreSQL     정상

그런데 주문이 영원히 pending 이었다
```

#### 로그

```text
worker 시작                                          01:30:32.805
의존 서비스 연결 실패 Timeout reading from redis     01:30:35.831
큐를 읽지 못했다. 잠시 후 재시도  backoff 1.0        01:30:35.832
큐를 읽지 못했다. 잠시 후 재시도  backoff 2.0        01:30:39.836
의존 서비스 연결 회복 (redis)                        01:30:42.833
큐를 읽지 못했다. 잠시 후 재시도  backoff 4.0        01:30:44.840
큐를 읽지 못했다. 잠시 후 재시도  backoff 8.0        01:30:51.842
큐를 읽지 못했다. 잠시 후 재시도  backoff 15.0       01:31:02.845
```

```text
32.805 → 35.831 = 정확히 3.026초
→ 우연이 아니다. 3초라는 설정값을 의심할 수 있었다

그리고 "실패 → 회복 → 실패" 가 반복된다
→ dependency_watcher 의 10초 주기 ping 은 성공하고 있다
→ Redis 자체는 멀쩡하고 BRPOP 만 실패한다는 뜻이다
```

#### 원인

```python
# deps.py
socket_timeout=3            # Redis 클라이언트 설정

# compose.yaml
WORKER_POLL_TIMEOUT: "5"    # BRPOP 대기 시간
```

```text
BRPOP  "5초 동안 값이 올 때까지 기다려라"
       → 값이 없으면 서버는 5초간 아무 바이트도 안 보낸다

소켓   "3초 넘게 아무것도 안 오면 죽은 연결이다" → 끊는다

→ 정상적인 대기가 장애로 둔갑했다
```

```text
★ 소켓 수준에서는 "정상적으로 침묵 중" 과 "죽었다" 가 구분되지 않는다
   둘 다 아무것도 안 온다. 사람이 시간으로 구분해줘야 한다
```

#### 개별 값은 둘 다 옳았다

```text
socket_timeout = 3       캐시는 빨리 포기해야 하니 맞다
poll_timeout   = 5       5초마다 살아있음을 갱신하니 맞다

→ 각각 보면 맞고, 같이 놓으면 틀리다
→ config.py 가 DB_POOL_MIN > DB_POOL_MAX 같은 "값 사이의 관계" 를
   검사하는 이유가 이것인데, 한 값이 코드에 박혀 있어 검사망 밖이었다
```

#### 수정 — 클라이언트를 둘로 나눴다

`socket_timeout` 을 8초로 올리는 방법은 쓰지 않았다.

```text
그러면 캐시 조회도 8초를 기다린다
→ 00 문서의 "캐시는 빨리 포기하고 DB 로 간다" 가 깨진다

두 경로의 요구가 정반대다
  캐시 경로   빨리 실패해야 한다      3초
  큐 대기     오래 기다려야 정상이다   poll_timeout + 5
```

```python
# deps.py
self._redis = redis_async.from_url(..., socket_timeout=3)                 # 빠른 명령용
self._redis_blocking = redis_async.from_url(
    ..., socket_timeout=self.settings.worker_poll_timeout + 5)            # BRPOP 전용
```

```python
# queue.py — dequeue 만 바꿨다. enqueue / length 는 그대로
result = await self.deps.redis_blocking_client.brpop([self.name], timeout=timeout)
```

여유를 `+5` 로 둔 이유

```text
poll_timeout 과 socket_timeout 을 5로 똑같이 맞추면
BRPOP 응답이 5.001초에 오면 소켓이 5.000초에 끊는다
→ 대부분 되는데 가끔 실패한다 → 재현 안 되는 버그
```

#### 수정 후

```text
{"msg": "주문 처리 완료", "order_id": 1, "result": "completed"}
```

```json
{
    "order_id": 1, "status": "completed",
    "created_at":  "2026-08-26T01:39:13.538756+00:00",
    "started_at":  "2026-08-26T01:39:13.548297+00:00",
    "finished_at": "2026-08-26T01:39:14.558452+00:00"
}
```

---

### 장애 2 — `dependency_up{postgres}` 지표가 아예 없었다 ★★

#### 증상

```bash
curl -s http://localhost:9000/metrics | grep dependency_up
```

```text
dependency_up{name="redis"} 1.0
                                    ← postgres 가 없다
```

#### 원인

`set_dependency_up` 호출 지점을 전부 찾아봤다.

```text
[redis]     cache.py / queue.py 가 성공·실패 모두 갱신한다  → 트래픽이 유지해준다
[postgres]  db.py 가 실패했을 때만 False 로 만든다
            health.py 는 사람이 /health/deps 를 부를 때만
```

```text
→ 한 번도 실패한 적이 없으면 지표가 아예 존재하지 않는다
→ 한 번 실패하면 0 이 된 채로 영원히 1 로 안 돌아온다
   → 거짓 알람이 계속 울린다 → 사람이 알람을 끈다 → 다음 장애를 놓친다
```

`dependency_watcher` 는 10초마다 확인은 하고 있었다. 그런데 `DependencyState`(=`/health/deps` 의 JSON) 만 갱신하고 Prometheus 게이지는 건드리지 않았다.

```text
★ 05 문서에 이렇게 적어놓고 코드에서 그대로 밟았다
   "지표가 0이면 알 수 있지만, 지표가 아예 없으면 알람이 안 울린다"
```

#### 수정

```python
# deps.py — dependency_watcher
pg_ok, redis_ok = await asyncio.gather(
    deps.check_postgres(),
    deps.check_redis(),
)
metrics.set_dependency_up("postgres", pg_ok)
metrics.set_dependency_up("redis", redis_ok)
```

`from app import metrics` import 도 추가했다.

#### 수정 후

```text
dependency_up{name="postgres"} 1.0
dependency_up{name="redis"} 1.0
```

Worker(9001)에서도 동일하게 나온다.

---

## 5. 08 문서 6절 검증

### 기동

```bash
curl -s http://localhost:9000/health/live     # {"status":"ok"}
curl -s http://localhost:9000/health/ready    # {"status":"ok"}
```

### 캐시 — 미스 후 적중

```bash
curl -s "http://localhost:8000/books?limit=3" > /dev/null
curl -s "http://localhost:8000/books?limit=3" > /dev/null
curl -s http://localhost:9000/metrics | grep cache_operations_total
```

```text
cache_operations_total{result="miss"} 1.0
cache_operations_total{result="hit"} 1.0
```

### 에러 응답 형식 — 4가지 모두 통일된 형태

```bash
# 재고 부족 (id % 97 == 0 인 책은 재고 1권)
curl -s -X POST localhost:8000/orders -H "X-User-Id: 1" \
  -H "Content-Type: application/json" -d '{"book_id":97,"quantity":5}'
```

```json
{"error": {"code": "OUT_OF_STOCK", "message": "재고가 부족합니다",
           "detail": {"book_id": 97, "requested": 5, "available": 1}}}
```

```json
{"error": {"code": "BOOK_NOT_FOUND", "detail": {"book_id": 999999}}}
{"error": {"code": "MISSING_USER", "message": "X-User-Id 헤더가 필요합니다"}}
```

### 장애 주입 — Redis 가 죽어도 조회가 된다 ★

```bash
curl -s -X POST localhost:9000/debug/inject/break-redis \
  -H "Content-Type: application/json" \
  -d '{"ttl_seconds": 30, "params": {"mode": "error"}}'
```

```json
{"injected":"break-redis","params":{"mode":"error"},
 "expires_in":29.999836,"pod":"api-local"}
```

```bash
curl -s "http://localhost:8000/books?limit=3" > /dev/null    # 200 이 나온다
curl -s http://localhost:9000/metrics | grep -E "cache_operations|dependency_up"
```

```text
cache_operations_total{result="miss"} 2.0
cache_operations_total{result="hit"} 1.0
cache_operations_total{result="error"} 2.0     ← 캐시는 실패했다
dependency_up{name="redis"} 1.0                ← 의도한 대로 안 내려갔다
```

```text
★ 04 문서의 주장이 검증됐다
  Redis 가 죽어도 /books 는 200 을 준다
  → readiness 에 의존성을 안 넣은 이유가 이것이다

★ dependency_up 이 1 로 남은 것은 의도한 동작이다
  cache.py 가 InjectedRedisFailure 를 구분해 "진짜 장애가 아니다" 로 처리한다
  실험 중임은 debug_injection_active 지표가 알린다

  [다시 볼 여지]
  5단계에서 "Redis 장애 알람" 을 만들면 그 알람을 시험할 수 없다
  → 그때 주입 시 dependency_up 도 내릴지 다시 정한다
```

```bash
curl -s -X POST localhost:9000/debug/reset
```

```json
{"cleared":1,"state":[]}
```

---

## 6. 이미지를 워커 노드로 전달

Registry 가 없으므로 build01 에서 만든 이미지를 노드에 직접 밀어넣는다.

### 대상은 워커 2대뿐이다

```bash
kubectl describe node master01 | grep -i taint
```

```text
control-plane 에 NoSchedule taint 가 걸려 있다
→ 앱 Pod 가 master01 에 안 뜬다
→ 이미지를 보낼 이유가 없다. 3대가 아니라 2대다
```

### 사전 조건 — 노드에 NOPASSWD 설정

```bash
# worker01, worker02 각각에서 (sjpark 계정 기준)
echo "sjpark ALL=(ALL) NOPASSWD: /usr/bin/ctr" | sudo tee /etc/sudoers.d/ctr-import
sudo chmod 440 /etc/sudoers.d/ctr-import
```

```text
왜 필요한가
  docker save 의 출력을 파이프로 ssh 에 넘긴다
  → ssh 의 stdin 이 tar 스트림이다
  → 원격 sudo 가 비밀번호를 물으려 해도 물을 곳이 없다
  → "sudo: no tty present" 로 실패한다

ctr 하나만 허용한다. ALL 이 아니다 (최소 권한)
별도 파일에 둔다 → rm 한 줄로 되돌린다
```

> worker01 에서 이 명령을 root 셸로 실행해 `$USER` 가 `root` 로 치환되는 실수를 했다.
> 2절의 `usermod` 실수와 같은 종류다.

### 태그 — 날짜시간을 붙인다

```bash
docker tag bookstore:dev bookstore:20260826-0301
```

```text
compose 가 만든 이름은 bookstore:dev 다. 고정 태그라 노드에 보내면 안 된다

[dev 로 보내면]
  worker01 과 worker02 에 다른 내용이 같은 이름으로 들어갈 수 있다
  → "이 Pod 만 이상해요" 가 된다. 원인 파악이 매우 어렵다
```

이 프로젝트에서 만든 이미지 이력

| 태그 | 이미지 ID | 내용 |
|---|---|---|
| `20260826-0128` | `ba65ff7e4d92` | 최초 빌드. Redis 타임아웃 버그 있음 |
| `20260826-0301` | `074008a841d6` | `deps.py` / `queue.py` 수정 후. **노드에 배포한 것** |

```text
docker tag 는 이미지를 복사하지 않는다. 이름표를 하나 더 다는 것이다
→ bookstore:dev 와 bookstore:20260826-0301 은 같은 ID 를 가리킨다
→ docker images 에 두 줄로 보이지만 이미지는 하나다
```

### 전송

```bash
docker save bookstore:20260826-0301 \
  | ssh sjpark@worker01 "sudo ctr -n k8s.io images import -"
```

```text
[임시 파일을 안 만드는 이유]
  save → scp → import → rm 은 build01 과 노드 양쪽에 tar 를 남긴다
  지우는 걸 잊으면 디스크가 찬다
  → 파이프로 바로 보내면 정리할 게 없다

[ssh 비밀번호는 정상 동작한다]
  ssh 는 비밀번호를 stdin 이 아니라 /dev/tty 에서 읽는다
  → stdin 이 tar 스트림이어도 터미널에 물어본다
```

실제 출력

```text
docker.io/library/bookstore:20260826-0301        saved
application/vnd.oci.image.index.v1+json sha256:074008a841d6...
Importing       elapsed: 51.8s
```

### 검증 — 세 번 확인한다

```bash
# 1. k8s.io 네임스페이스
ssh sjpark@worker01 "sudo ctr -n k8s.io images ls | grep bookstore"
```

```text
docker.io/library/bookstore:20260826-0301
  application/vnd.oci.image.index.v1+json
  sha256:074008a841d6...  59.4 MiB  linux/amd64
  io.cri-containerd.image=managed
```

```bash
# 2. 기본 네임스페이스 — 비어 있어야 정상 ★★
ssh sjpark@worker01 "sudo ctr images ls | grep bookstore"
```

```text
(출력 없음)

★ 같은 노드, 같은 명령, -n 하나 차이로 결과가 완전히 뒤바뀐다
  -n k8s.io 를 빼고 import 했다면
  → 2번에 보이고 1번에는 안 보인다
  → import 는 성공했다고 나오는데 Pod 는 ImagePullBackOff
  → 둘 다 정상처럼 보이는 조용한 실패다
```

```bash
# 3. kubelet 과 같은 시야
ssh -t sjpark@worker01 "sudo crictl images | grep bookstore"
```

```text
docker.io/library/bookstore   20260826-0301   6b75be20ea395   62.3MB
```

```text
-t 가 필요한 이유
  crictl 은 NOPASSWD 목록에 없다 (ctr 만 넣었다)
  → 원격 sudo 가 비밀번호를 물어야 한다
  → -t 로 가상 터미널을 만들어주면 물을 수 있다

  전송할 때는 -t 를 쓸 수 없다. stdin 이 tar 라 입력이 섞인다
  → 그래서 ctr 만 NOPASSWD 로 뺐다
```

master01 에는 없는 것도 확인했다.

```bash
ssh -t sjpark@master01 "sudo crictl images | grep bookstore"
# (출력 없음)
```

### 도구마다 다른 값을 보여준다 ★

같은 이미지인데 세 도구의 출력이 다르다.

| 도구 | ID | 크기 |
|---|---|---|
| `docker images` | `074008a841d6` | 265MB |
| `ctr -n k8s.io images ls` | `sha256:074008a841d6...` | 59.4 MiB |
| `crictl images` | `6b75be20ea395` | 62.3MB |

```text
[ID 가 다른 이유]
  OCI 이미지는 세 층 구조다

    index      여러 플랫폼(+attestation)을 묶은 목록   ← 074008a841d6
      └ manifest   linux/amd64 용 레이어 목록
          └ config  환경변수, CMD, 레이어 순서 등      ← 6b75be20ea395

  docker / ctr   index 의 digest 를 보여준다
  crictl (CRI)   실제로 실행할 config 의 digest 를 보여준다

  → 둘 다 맞다. 보는 층이 다르다

[크기가 다른 이유]
  265MB    풀어놓았을 때 (docker 의 DISK USAGE)
  62.3MB   압축된 상태 (전송량. crictl / ctr 이 보여주는 값)
```

### 스크립트로 묶기

손으로 한 대를 보내본 뒤 `scripts/push-image.sh` 로 묶었다.

```text
1. docker images bookstore 목록을 번호로 고르게 한다
2. dev / latest 면 경고한다
3. 대상과 이미지를 확인받는다
4. 노드마다 순차로 전송 + 확인
5. Manifest 에 붙여넣을 두 줄을 출력한다
```

```text
[병렬(&)로 안 하는 이유]
  비밀번호 인증이라 프롬프트가 서로 엉킨다
  → 어느 노드 비밀번호를 묻는지 알 수 없게 된다

[set -o pipefail 을 켜는 이유]
  파이프의 성공/실패는 기본적으로 마지막 명령만으로 판단한다
  → docker save 가 실패해도 ssh 가 성공하면 "성공" 으로 보인다

[배포(kubectl apply)는 안 한다]
  빌드/전달은 스크립트, 배포는 사람
  → CI 와 CD 의 책임 분리를 스크립트 범위로도 지킨다
```

실행 결과

```text
── worker01 ──   완료 (39초)
── worker02 ──   완료 (39초)
```

### 겪은 문제 — 비밀번호가 화면에 새어나온다

```text
sjpark@worker02's password:
sjpark                      ← 화면에 그대로 찍혔다
...
$ sjpark
sjpark: command not found   ← 셸 버퍼로도 흘러갔다
```

```text
원인
  stdin 이 tar 스트림인 상태에서 ssh 가 /dev/tty 로 비밀번호를 읽는다
  이때 터미널 에코를 끄고 켜는 처리가 완전하지 않다

해결
  SSH 키를 쓰면 프롬프트 자체가 사라진다
  ssh-keygen 한 번 + ssh-copy-id 두 번
  → 지금은 그대로 두고, 반복이 잦아지면 전환한다
```

---

## 7. 측정값

### 빌드

| 항목 | 값 |
|---|---|
| 첫 빌드 | 23.7초 |
| 코드만 변경 후 재빌드 | 약 2초 |
| 이미지 (디스크) | 265MB |
| 이미지 (전송) | 62.3MB |
| 빌드 컨텍스트 전송량 | 167.81kB |

```text
재빌드가 2초인 이유
  requirements.txt 를 따로 COPY 했으므로 pip install 레이어가 캐시된다
  COPY ./app 레이어만 다시 만든다
  → 07 문서의 레이어 순서 판단이 숫자로 증명됐다
```

### 기동

| 구간 | 시간 |
|---|---|
| 프로세스 시작 → PostgreSQL 연결 | 약 0.02초 (DB 가 준비된 경우) |
| 첫 실행 시 DB 대기 | 약 1.1초 (재시도 2회) |
| 컨테이너 시작 → uvicorn 대기 | 약 0.35초 |

### 요청 처리 ★

| 구간 | 시간 |
|---|---|
| `POST /orders` 응답 | 17.93ms |
| 큐 대기 (created → started) | 9.5ms |
| 실제 처리 (started → finished) | 1.010초 |

```text
사용자가 기다린 시간 17.93ms 대 실제 처리 1.02초 → 57배
→ 201 이 아니라 202 를 준 근거가 이 숫자다

큐 대기가 9.5ms 인 것도 의미가 있다
  BRPOP 이 이미 블록한 채로 기다리고 있어서 LPUSH 즉시 깨어났다
  RPOP 을 1초 주기로 폴링했다면 평균 500ms 를 낭비했을 것이다
```

| 경로 | 응답 시간 |
|---|---|
| `/health/live` | 0.27 ~ 0.46ms |
| `/books` (캐시 적중) | 측정 예정 |
| `/books` (캐시 미스) | 측정 예정 |

### 이미지 전달

| 항목 | 값 |
|---|---|
| 노드 1대 전송 + import | **39초** |
| 노드 2대 (순차) | 약 78초 |
| 전송량 | 62.3MB |

```text
첫 측정에서 1분 10초가 나왔는데 이 값은 버린다
  time (docker save ... | ssh ...) 이
  "yes" 를 치고 비밀번호를 입력하는 시간까지 포함했다
  → 대화형 입력이 섞인 측정은 신뢰할 수 없다

★ 코드 한 줄을 고칠 때마다 약 78초가 든다
  이 숫자가 4단계 이후 로컬 Registry 를 세우는 근거가 된다
```

---

## 8. 아직 확인하지 못한 것

**남겨두는 이유까지 적는다.** 나중에 "빠뜨린 것" 인지 "일부러 안 한 것" 인지 구분하기 위해서다.

| 항목 | 왜 아직 안 했나 |
|---|---|
| 재고 음수 실제 발생 | 동시 요청을 넣어야 한다. k6 부하 테스트가 6단계 주제다 |
| `STOCK_STRATEGY` 세 방식 비교 | 위와 같음. 음수를 관찰한 뒤에 비교해야 의미가 있다 |
| SIGTERM 종료 순서 검증 | Compose 에서는 EndpointSlice 전파가 없어 확인할 게 없다. 4단계에서 롤링 업데이트로 검증한다 |
| `slow-query` / `latency` 주입 | 지표 변화를 볼 대시보드가 없다. 5단계 이후 |
| 커넥션 풀 고갈 연쇄 | 부하가 필요하다. 6단계 |
| Pod 가 실제로 이 이미지로 뜨는가 | import 는 성공했고 `crictl` 에도 보인다. 그런데 attestation 이 붙은 OCI index 를 kubelet 이 문제없이 쓰는지는 Pod 를 띄워봐야 안다. 4단계 첫 작업 |
| 인덱스 성능 비교 | 데이터를 늘려야 한다. 5단계 |

---

## 9. 이번 구현에서 배운 것

```text
1. 개별 값이 옳아도 관계가 틀릴 수 있다
   socket_timeout=3 과 poll_timeout=5 는 각각 맞다
   → config.py 가 "값 사이의 관계" 를 따로 검사하는 이유

2. 같은 의존 서비스라도 경로마다 요구가 다를 수 있다
   캐시는 빨리 포기해야 하고 큐는 오래 기다려야 한다
   → 클라이언트를 나누는 것이 답이었다
   → cache.py 는 실패를 삼키고 queue.py 는 던지는 것과 같은 구조

3. 설계 문서에 적은 함정을 코드에서 밟을 수 있다
   "지표가 없으면 알람이 안 울린다" 를 적어놓고 그대로 밟았다
   → 문서를 쓰는 것과 코드에 반영하는 것은 별개의 작업이다

4. 로그에 무엇을 담느냐가 진단 속도를 결정한다
   backoff 값을 로그에 넣어둔 것이 결정적이었다
   1.0 → 2.0 → 4.0 이 커지는 걸 보고 "계속 실패 중" 을 알았다
   시각 정밀도(밀리초)가 있어서 "정확히 3초" 를 짚을 수 있었다

5. Compose 는 배포 수단이 아니라 검증 수단이다
   변수를 최소로 줄인 상태에서 앱 자체를 먼저 검증했다
   k8s 에 바로 올렸다면 원인 후보가 대여섯 개였을 것이다
   (Service DNS / NetworkPolicy / Calico / 앱 버그 ...)
```

---

```text
6. 도구가 보여주는 값은 "어느 층을 보는가" 에 달려 있다
   같은 이미지를 docker / ctr / crictl 이 다른 ID 로 보여준다
   → 값이 다르다고 다른 이미지가 아니다
   → OCI 의 index → manifest → config 구조를 알아야 읽힌다

7. 대화형 입력이 섞인 측정은 버려야 한다
   첫 전송 70초에는 타이핑 시간 약 30초가 섞여 있었다
```

---

## 10. 다음 작업

```text
4단계 — 순수 Manifest 로 배포

  가장 먼저 확인할 것
    Pod 가 bookstore:20260826-0301 로 실제로 뜨는가
    → attestation 이 붙은 OCI index 를 kubelet 이 쓸 수 있는지
    → 실패하면 --provenance=false 로 재빌드하고 로그를 비교한다

  Manifest 에 반드시 넣을 것
    image: bookstore:20260826-0301
    imagePullPolicy: IfNotPresent      ← Always 면 ErrImagePull

  Compose 의 각 줄이 무엇으로 바뀌는지 대응시킨다
  depends_on 에 대응물이 없다는 것이 핵심이다
```

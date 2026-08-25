# 07. Dockerfile — 이미지를 만든다

앞 문서들에서 넘어온 제약을 여기서 전부 반영한다.

```text
[02 클라우드 이식성]
  ca-certificates 를 넣어둔다        나중에 관리형 DB 에 TLS 로 붙을 때
  로그는 stdout 으로만
  /tmp 외에 쓰지 않는다              읽기 전용 루트 파일시스템 대비
  SIGTERM 을 받아야 한다

[04 Health Check]
  관리 포트를 따로 연다

[06 장애 주입]
  관리 포트에 /debug/* 를 붙인다. Service 에는 안 넣는다

[00 아키텍처]
  API 와 Worker 가 같은 이미지를 쓴다. 실행 명령만 다르다
```

---

## 1. 기반 이미지 — slim 을 고른다

```text
[후보]
  python:3.12                 완전판. 컴파일러와 헤더까지 있다. ~1GB
  python:3.12-slim            데비안 최소 구성. ~130MB
  python:3.12-alpine          Alpine 기반. ~50MB
  distroless / scratch        셸조차 없다
```

### alpine 을 안 쓰는 이유 ★

```text
[Alpine 은 musl libc 를 쓴다]
  대부분의 리눅스 배포판은 glibc 를 쓴다
  파이썬 패키지의 미리 컴파일된 배포판(wheel)은 보통 glibc 기준이다
  → Alpine 에서는 그 wheel 을 못 쓴다
  → 소스에서 컴파일한다 → 빌드가 몇 배 느려진다
```

```text
[크기 이득도 생각보다 작다]
  기반은 작지만 컴파일한 결과물과 빌드 의존성이 붙는다
  → 최종 크기가 slim 과 비슷해지는 경우가 흔하다
```

```text
[그리고 디버깅이 어렵다]
  6단계에서 컨테이너에 들어가 확인할 일이 많다
  Alpine 은 기본 도구가 다르다 (busybox 기반)
```

**"Alpine 이 작으니 좋다" 는 언어에 따라 다르다.** Go 는 맞고 Python 은 대체로 아니다.

### distroless / scratch 를 안 쓰는 이유

```text
셸이 없다 → kubectl exec 로 들어가서 확인할 수 없다
→ 6단계 장애 실험에서 불리하다
```

```text
[운영에서는 좋은 선택이다]
  공격 표면이 최소다
  → 다만 학습 단계에서는 관찰이 우선이다
  → 문서에 "운영이면 distroless 를 고려한다" 를 남긴다
```

---

## 2. Dockerfile

```dockerfile
# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────
# 스테이지 1 — 의존성 설치
# ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 의존성 목록만 먼저 복사한다 — 캐시를 위해
COPY requirements.txt .

RUN pip install --prefix=/install -r requirements.txt


# ─────────────────────────────────────────────
# 스테이지 2 — 실행
# ─────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    APP_PORT=8000 \
    ADMIN_PORT=9000

# TLS 로 관리형 DB 에 붙을 때 필요하다 (02 문서)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 비-root 사용자
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /install /usr/local
COPY --chown=10001:10001 ./app /app

WORKDIR /app
USER 10001:10001

EXPOSE 8000 9000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 3. 한 줄씩 — 왜 이렇게 썼나

### `# syntax=docker/dockerfile:1`

```text
BuildKit 의 최신 문법을 쓴다는 선언이다
→ 캐시 마운트, 시크릿 마운트 같은 기능을 쓸 수 있다
→ 지금은 안 쓰지만 8단계 CI 에서 필요해진다
```

### 스테이지 1 — `requirements.txt` 만 먼저 복사한다 ★

```dockerfile
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt
```

```text
[소스를 같이 복사하면 안 되는 이유]
  COPY ./app /app 을 먼저 하면
  → 소스를 한 글자만 고쳐도 그 레이어가 바뀐다
  → 뒤따르는 pip install 레이어의 캐시가 깨진다
  → 매번 의존성을 다시 설치한다
```

```text
[분리하면]
  requirements.txt 가 안 바뀌면 pip install 은 캐시에서 나온다
  → 소스만 고쳤을 때 빌드가 몇 초 만에 끝난다
```

**캐시가 잘 걸리는 순서 = 잘 안 바뀌는 것을 앞에, 자주 바뀌는 것을 뒤에.**

### `--prefix=/install`

```text
패키지를 /install 아래에 모아 설치한다
→ 스테이지 2 에서 COPY --from=builder /install /usr/local 로 통째로 옮긴다
→ 옮길 대상이 한 디렉터리라 단순하다
```

### `PYTHONUNBUFFERED=1` ★

```text
[안 넣으면]
  파이썬이 stdout 을 버퍼에 모았다가 한꺼번에 내보낸다
  → 컨테이너 로그가 늦게 나오거나
  → 죽을 때 버퍼에 있던 로그가 사라진다

[장애 조사에서 치명적이다]
  "죽기 직전 로그" 가 제일 중요한데 그게 없어진다
```

**02 문서의 "로그는 stdout 으로만" 을 실제로 동작하게 만드는 설정이다.**

### `PYTHONDONTWRITEBYTECODE=1`

```text
.pyc 파일을 안 만든다
```

```text
[왜]
  읽기 전용 루트 파일시스템을 켜면 .pyc 를 못 만들어 에러가 난다
  → 미리 꺼둔다 (02 문서에서 "길만 열어둔다" 고 한 것)

  그리고 컨테이너는 한 번 돌고 버려지므로 캐시 이득이 거의 없다
```

### `PYTHONFAULTHANDLER=1`

```text
치명적 오류(세그폴트 등)가 났을 때 파이썬 스택을 출력한다
→ 6단계에서 원인을 찾을 때 도움이 된다
```

### `ca-certificates` ★

```text
[02 문서에서 정한 것]
  나중에 RDS 나 ElastiCache 에 TLS 로 붙을 때
  sslmode=verify-full 을 쓰려면 CA 인증서가 필요하다
  → 없으면 그때 이미지를 다시 만들어야 한다
```

```text
[지금 넣는 비용]
  몇 MB. 그게 전부다

[나중에 넣는 비용]
  이미지 재빌드 + 재배포 + "왜 안 되지" 를 찾는 시간
```

**`--no-install-recommends` 와 `rm -rf /var/lib/apt/lists/*` 를 같은 `RUN` 안에 둔다.** 다른 `RUN` 이면 apt 캐시가 레이어에 남는다.

### 비-root 사용자 ★

```dockerfile
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001:10001
```

```text
[기본은 root 다]
  컨테이너가 뚫리면 컨테이너 안에서 root 권한을 갖는다
  → 커널 취약점과 조합되면 노드로 나갈 수 있다
```

```text
[숫자 UID 를 쓰는 이유]
  USER app 이라고 이름으로 쓰면 Kubernetes 가 이게 root 인지 판단 못 한다
  → runAsNonRoot: true 를 쓰려면 숫자여야 한다
```

```text
[--no-create-home]
  홈 디렉터리가 필요 없다. 안 만든다
[--shell /usr/sbin/nologin]
  로그인 셸을 안 준다
```

```text
[4단계에서 이렇게 쓴다]
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities: { drop: ["ALL"] }
```

### `COPY --chown=10001:10001 ./app /app`

```text
복사하면서 소유자를 바꾼다
→ 나중에 RUN chown -R 을 하면 레이어가 하나 더 생기고 파일이 통째로 복사된다
→ 복사할 때 한 번에 처리하는 게 싸다
```

### `EXPOSE 8000 9000`

```text
[실제로 포트를 여는 게 아니다]
  "이 이미지는 이 포트를 쓴다" 는 문서화일 뿐이다
  Kubernetes 는 이 값을 안 본다
```

```text
[그래도 쓰는 이유]
  Dockerfile 만 봐도 포트 구성을 알 수 있다
  Compose 에서는 참고한다
```

```text
8000   서비스 포트. Service 와 Ingress 가 연결된다
9000   관리 포트. /metrics, /health/*, /debug/*      ← Service 에 안 넣는다
```

### `CMD` 는 반드시 exec 형식으로 ★★

```dockerfile
# 좋다
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# 나쁘다
CMD python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```text
[shell 형식으로 쓰면]
  /bin/sh -c "명령" 으로 실행된다
  → sh 가 PID 1 이 되고 uvicorn 은 자식 프로세스가 된다
  → kubelet 이 보내는 SIGTERM 은 sh 가 받는다
  → sh 는 그걸 자식에게 전달하지 않는다
  → uvicorn 은 신호를 못 받고 유예 시간 뒤 SIGKILL 로 죽는다
```

**12편에서 본 그 현상이다.**

```text
[그때 실측한 것]
  nginx 를 그대로 두면              Completed  (종료 코드 0)
  command 를 sh -c "sleep ..." 으로 덮으면   Error   (신호 처리 없음)
```

```text
[결과]
  Graceful Shutdown 이 동작하지 않는다 (02 문서의 요구사항)
  처리 중이던 요청이 끊긴다
  종료 코드가 0이 아니라 로그가 Error 로 도배된다
```

### PID 1 문제 — tini 가 필요한가

```text
[PID 1 은 특별하다]
  신호 핸들러를 등록하지 않으면 SIGTERM 이 기본 무시된다
  좀비 프로세스를 거둬야 할 책임이 있다
```

```text
[uvicorn 은 신호 핸들러를 등록한다]
  → SIGTERM 을 받아 정상 종료 절차를 밟는다
  → tini 같은 init 이 없어도 동작한다

[자식 프로세스를 만드는 구조라면 필요할 수 있다]
  uvicorn --workers N 처럼 프로세스를 여러 개 띄우면
  → 좀비 처리와 신호 전달을 누가 할지 따져야 한다
```

```text
[우리 선택]
  Kubernetes 에서는 워커를 1로 두고 Pod 수로 늘린다
  → 프로세스 트리가 단순하다 → init 이 필요 없다
  → 그래도 문제가 보이면 그때 tini 를 넣는다. 문서에 남긴다
```

**"Pod 를 늘릴 것인가 프로세스를 늘릴 것인가" 는 4단계에서 다시 다룬다.**

---

## 4. `.dockerignore`

```text
.git
.gitignore
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/
.mypy_cache/
tests/
docs/
*.md
.env
.env.*
docker-compose*.yml
Dockerfile*
```

### 왜 필요한가

```text
[없으면]
  빌드할 때 현재 디렉터리 전체가 Docker 데몬으로 전송된다
  .git 이 수백 MB 면 그것도 같이 간다
  → 빌드가 느려진다
```

```text
[더 중요한 것 — 보안]
  .env 에 비밀번호가 있으면 이미지에 들어간다
  COPY . . 을 안 썼어도 빌드 컨텍스트에는 올라간다
  → 실수 하나로 시크릿이 이미지에 박힌다
```

```text
[캐시도 깨진다]
  .git 안의 파일이 바뀌면 컨텍스트가 바뀐다
  → 불필요하게 캐시가 무효화된다
```

**`.dockerignore` 를 안 쓰는 건 사고를 기다리는 것이다.**

---

## 5. `HEALTHCHECK` 를 쓸 것인가

```dockerfile
# 이 프로젝트에서는 쓰지 않는다
# HEALTHCHECK --interval=10s CMD curl -f http://localhost:8000/health/live || exit 1
```

```text
[Kubernetes 는 Dockerfile 의 HEALTHCHECK 를 무시한다]
  probe 로 대체한다 (04 문서에서 설계했다)
```

```text
[Docker Compose 에서는 동작한다]
  depends_on 의 condition: service_healthy 와 함께 쓰면
  "DB 가 준비된 뒤에 앱을 띄운다" 가 된다
```

```text
[우리 선택]
  Dockerfile 에는 안 넣는다
  → 어차피 Kubernetes 에서는 무시된다
  → 두 곳에 헬스체크 정의가 있으면 헷갈린다

  Compose 파일에 필요하면 거기에 쓴다 (08 문서)
```

```text
[그리고 curl 을 이미지에 넣지 않는다]
  HEALTHCHECK 때문에 curl 을 설치하는 경우가 흔하다
  → 공격 표면이 는다. 안 넣는다
```

---

## 6. API 와 Worker 는 같은 이미지를 쓴다

```text
[Dockerfile 의 CMD]  API 를 띄운다. 기본값이다

[Worker 는 Kubernetes 에서 덮어쓴다]
  command: ["python", "-m", "app.worker"]
```

```text
[왜 이미지를 나누지 않나]
  빌드가 한 번이면 된다
  노드가 받는 레이어를 공유한다 → 디스크와 기동 시간이 준다
  버전이 어긋날 일이 없다        ← 이게 제일 크다
```

```text
[버전이 어긋나면]
  API 는 새 스키마를 쓰는데 Worker 는 옛 코드다
  → 처리 중인 주문이 깨진다
```

---

## 7. 태그 전략 — `latest` 를 쓰지 않는다 ★

```text
[latest 의 문제]
  imagePullPolicy 기본값이 태그에 따라 달라진다
    latest       → Always (매번 받는다)
    그 외         → IfNotPresent (있으면 안 받는다)

  그리고 "지금 도는 게 어느 코드인가" 를 알 수 없다
  롤백할 대상도 특정할 수 없다
```

```text
[쓸 태그]
  books-app:<git-commit-sha>       불변. 이게 기준이다
  books-app:v1.2.3                 릴리스용
  books-app:dev                    로컬 개발용. 클러스터에 안 올린다
```

```text
[8단계 CI 에서 확정한다]
  커밋 SHA 로 태그를 붙이고
  9단계 GitOps 에서 그 태그를 Manifest 에 반영한다
  → "Git 커밋 하나가 이미지 하나에 대응한다"
```

**02 문서의 "10단계에서 이미지 다이제스트가 같은지 확인한다" 도 여기에 달려 있다.**

---

## 8. 측정 계획

**만들고 나서 반드시 잰다.** 02 문서에서 "Python 을 고른 대가를 수치로 남긴다" 고 했다.

```text
[측정 1 — 이미지 크기]
  단일 스테이지 vs 멀티스테이지
  docker images
  docker history 로 레이어별 크기

[측정 2 — 빌드 시간]
  캐시 없이 처음부터              docker build --no-cache
  소스만 고쳤을 때                 (의존성 캐시 적중)
  requirements.txt 를 고쳤을 때    (의존성 재설치)

[측정 3 — 기동 시간]
  컨테이너 시작부터 /health/live 가 200 을 줄 때까지
  → 04 문서의 startupProbe 값을 정하는 근거가 된다

[측정 4 — 빌드 캐시 용량]
  docker system df
  → 최종 이미지는 작은데 캐시가 얼마나 쌓이는지
```

```text
[기록할 표]
                        단일 스테이지   멀티스테이지
  이미지 크기
  첫 빌드 시간
  소스만 고쳤을 때
  requirements 고쳤을 때
  기동 시간
```

```text
[예상과 다를 수 있다]
  "Python 은 멀티스테이지 효과가 작다" 가 우리 가정이다
  실제로 얼마나 작은지는 재봐야 안다
  → "얼마 안 줄었다" 도 결과다. 그대로 기록한다
```

---

## 9. 이 파일은 8단계에서 그대로 쓰인다 ★

**"이건 손으로 빌드하는 테스트용이고 CI 는 따로 만든다" 가 아니다.**

```text
Dockerfile   무엇을 어떻게 만들지 정의한다        설계도
CI           언제 누가 그걸 실행할지 자동화한다     실행 체계
```

```text
CI 파이프라인이 하는 일의 핵심 한 줄
  docker build -f Dockerfile .
```

### 무엇이 바뀌고 무엇이 그대로인가

```text
                     3단계 (지금)        8단계 (CI)
  ────────────────────────────────────────────────────────
  Dockerfile 내용     우리가 쓴 그대로     거의 그대로      ← 안 바뀐다
  누가 실행하나        사람이 손으로        push 하면 자동
  태그                손으로 정함          커밋 SHA 자동
  캐시                로컬 디스크          레지스트리 캐시
  푸시                안 함                레지스트리로 자동
  검증                없음                 테스트 / 린트 / 취약점 스캔
  아키텍처            amd64 하나           buildx 로 amd64 + arm64
```

### 순서가 이런 이유

```text
[Dockerfile 없이 8단계로 가면]
  CI 를 만드는데 실행할 대상이 없다
  → 캐시가 왜 안 걸리는지, 이미지가 왜 큰지를 그때 처음 배운다
  → CI 문제인지 Dockerfile 문제인지 구분이 안 된다
```

**로드맵 원칙 1 과 같다 — 자동화하기 전에 직접 구성한다.** 1단계에서 kubeadm 을 손으로 돌린 것과 같은 순서다.

### 실무에서 DevOps 가 Dockerfile 에서 보는 것

```text
[흔한 역할 분담]
  개발자         앱에 맞게 초안을 쓴다
  플랫폼/DevOps   표준 템플릿과 기반 이미지를 제공한다
                 리뷰한다
```

```text
[리뷰에서 잡는 것들 — 이 문서에서 판단한 그것들이다]
  root 로 실행하고 있다                    → 7절
  latest 태그를 쓴다                       → 7절
  .dockerignore 가 없어 .env 가 들어간다    → 4절
  캐시 순서가 잘못돼 매번 오래 걸린다        → 3절
  CMD 가 shell 형식이라 SIGTERM 이 안 간다  → 3절
  기반 이미지에 알려진 취약점이 있다
```

### Dockerfile 을 안 쓰는 방식도 있다

```text
Cloud Native Buildpacks (Paketo 등)   소스만 주면 이미지를 만들어준다
ko                                    Go 전용
Jib                                   Java 전용. Docker 데몬도 불필요
```

```text
[플랫폼 팀이 이걸 제공하면]
  개발자는 Dockerfile 을 안 쓴다
  그런데 그걸 도입하고 운영하는 게 DevOps 의 일이다
```

```text
[그리고 문제가 나면 결국 원리를 봐야 한다]
  왜 이미지가 800MB 인가 / 왜 비-root 로 안 도나 / 왜 이 파일이 들어갔나
  → 레이어와 빌드 과정을 모르면 답을 못 한다
```

**7단계에서 Helm 전에 순수 Manifest 를 먼저 하는 것과 같은 이유다.**

### 8단계에서 추가될 것

```text
트리거          push / tag 에 반응
태그 자동화      커밋 SHA. 이 문서 7절에서 미룬 것
캐시 전략        레지스트리 캐시 (--cache-to / --cache-from)
                → 이 문서 3절의 캐시 순서가 여기서 효과를 낸다
검증 게이트      테스트 / 린트 / 취약점 스캔 → 실패하면 이미지를 안 만든다
멀티 아키텍처    buildx. 02 문서에서 미룬 것
푸시            레지스트리 / ECR

[9단계]
  만들어진 태그를 GitOps 저장소의 Manifest 에 반영한다
  → "커밋 하나 = 이미지 하나 = 배포 하나"
```

---

## 10. 지금 하지 않는 것

```text
1. 멀티 아키텍처 이미지 (amd64 / arm64)
   → 8단계 CI 에서 buildx 로. EKS Graviton 노드를 쓸 때 필요하다

2. distroless 전환
   → 셸이 없으면 6단계 장애 조사가 어렵다. 운영이면 고려한다

3. 이미지 서명 / SBOM
   → 공급망 보안. 이 프로젝트 범위 밖이다. 문서에만 남긴다

4. 빌드 시크릿 마운트
   → 사설 저장소에서 패키지를 받을 때 필요하다. 지금은 없다

5. tini / dumb-init
   → uvicorn 이 신호를 처리한다. 문제가 보이면 그때 넣는다
```

---

## 정리 — 이 문서에서 내린 판단

```text
 1. 기반은 python:3.12-slim
    alpine 은 musl libc 라 wheel 호환성 문제가 있다. Python 에서는 손해다
    distroless 는 셸이 없어 6단계 조사에 불리하다

 2. 멀티스테이지로 간다
    크기 절감보다 CI 캐시와 확장성이 이유다

 3. requirements.txt 를 소스보다 먼저 복사한다 ★
    캐시가 걸리는 순서 = 잘 안 바뀌는 것을 앞에

 4. PYTHONUNBUFFERED=1 ★
    없으면 죽기 직전 로그가 사라진다. 장애 조사에서 치명적이다

 5. PYTHONDONTWRITEBYTECODE=1
    읽기 전용 루트 파일시스템 대비

 6. ca-certificates 를 지금 넣는다
    지금은 몇 MB, 나중이면 이미지 재빌드다 (02 문서)

 7. 비-root 로 실행한다. UID 를 숫자로 지정한다
    이름으로 쓰면 runAsNonRoot 가 판단을 못 한다

 8. CMD 를 exec 형식으로 쓴다 ★★
    shell 형식이면 sh 가 PID 1 이 되어 SIGTERM 이 전달되지 않는다
    → 12편에서 실측한 Error vs Completed 가 이 문제다

 9. .dockerignore 를 반드시 쓴다
    .env 가 빌드 컨텍스트에 올라가면 시크릿 사고다

10. HEALTHCHECK 를 Dockerfile 에 안 넣는다
    Kubernetes 가 무시한다. 정의가 두 곳에 있으면 헷갈린다

11. API 와 Worker 가 같은 이미지를 쓴다
    버전이 어긋날 일이 없고 레이어를 공유한다

12. latest 태그를 쓰지 않는다
    커밋 SHA 로 태그한다. 8단계에서 확정한다

13. 만들고 나서 잰다
    단일 vs 멀티, 빌드 시간, 기동 시간
    "얼마 안 줄었다" 도 결과다

14. 이 파일은 8단계 CI 에서 그대로 쓰인다 ★
    CI 가 Dockerfile 을 대체하는 게 아니라 실행한다
    바뀌는 건 "누가 언제 실행하나" 와 그 주변 검증이다
    → 위 판단들이 그대로 DevOps 의 리뷰 항목이 된다
```

## 다음

```text
08-compose.md   로컬 개발 환경
                PostgreSQL / Redis / API / Worker 를 한 번에 띄운다
                여기서 HEALTHCHECK 와 depends_on 을 쓴다
                코드를 볼륨으로 마운트해 자동 재시작을 붙인다
                → 다만 "다른 이미지" 를 만드는 게 아니다
```

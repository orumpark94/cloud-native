# 서점 APP 이미지
#
# 07-dockerfile.md 의 설계를 그대로 옮긴 것이다.
# 이 파일은 8단계 CI 에서 그대로 쓰인다. GitHub Actions 가 이걸 실행할 뿐이다.
#
# 문서와 두 군데 다르다. 실제로 짜면서 바로잡은 것이다        ★
#   1. WORKDIR 을 /app 이 아니라 /srv 로 둔다
#      /app 에 코드를 두고 WORKDIR 도 /app 이면 python 이 app 패키지를 못 찾는다
#      → import app.main 이 되려면 app/ 의 "부모" 가 작업 경로여야 한다
#   2. CMD 가 uvicorn 이 아니라 python -m app.main 이다
#      포트가 둘이고 SIGTERM 순서를 우리가 통제해야 하기 때문이다 (main.py)


# ═════════════════════════════════════════════
# 스테이지 1 — 의존성 설치
#
# 여기서 하는 일은 전부 버려진다. 결과물만 다음 스테이지로 넘어간다
#   → 컴파일러, 헤더, pip 캐시가 최종 이미지에 안 남는다
#   → 공격자가 컨테이너에 들어와도 빌드 도구가 없다
# ═════════════════════════════════════════════
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# ★ requirements.txt 만 먼저 복사한다                     (레이어 캐시)
#   코드와 같이 복사하면 코드 한 줄만 고쳐도 레이어가 무효화된다
#   → 매번 pip install 을 다시 한다 (수십 초~수 분)
#
#   따로 복사하면 requirements.txt 가 안 바뀌는 한 캐시가 산다
#   → 코드만 고친 빌드는 몇 초로 끝난다
COPY requirements.txt .

# --prefix=/install 로 한곳에 모은다
#   그래야 다음 스테이지가 디렉터리 하나만 가져가면 된다
RUN pip install --prefix=/install -r requirements.txt


# ═════════════════════════════════════════════
# 스테이지 2 — 실행
# ═════════════════════════════════════════════
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# PYTHONUNBUFFERED=1 이 없으면 로그가 안 보인다              ★★ 02 문서
#   파이썬은 stdout 이 터미널이 아니면 버퍼링한다
#   컨테이너의 stdout 은 파이프다 → 버퍼가 찰 때까지 안 나온다
#   → kubectl logs 에 아무것도 안 뜬다
#   → 그리고 프로세스가 죽으면 버퍼 내용이 통째로 사라진다
#   → 장애 직전 로그가 가장 중요한데 그게 없어진다

# ca-certificates 를 넣는다                                ★ 02 문서
#   로컬(평문)에서는 필요 없다. AWS 로 가면 필요하다
#   RDS 는 TLS, ElastiCache 도 TLS 를 쓸 수 있다
#   없으면 "certificate verify failed" 가 난다
#   → 로컬에서는 아무 문제 없다가 EKS 에서만 터진다
#   → 그런 종류의 문제를 미리 없앤다
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# rm -rf 를 같은 RUN 에 붙이는 이유
#   레이어는 append-only 다. 다음 RUN 에서 지우면
#   앞 레이어에는 그대로 남아 이미지 크기가 안 줄어든다

# 비-root 사용자                                           ★ 07 문서
#   숫자 UID 로 만든다. USER app 처럼 이름으로 쓰면
#   Kubernetes 가 이게 root 인지 판단하지 못한다
#   → runAsNonRoot: true 를 쓰려면 숫자여야 한다
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# 스테이지 1의 결과물만 가져온다
COPY --from=builder /install /usr/local

WORKDIR /srv
COPY --chown=10001:10001 ./app /srv/app

USER 10001:10001

# EXPOSE 는 문서다. 실제로 포트를 여는 게 아니다
#   8000  서비스   /books /orders
#   9000  관리     /health/* /metrics /debug/*
EXPOSE 8000 9000

# ★★ exec 형식(JSON 배열)으로 쓴다. 셸 형식이 아니다      (07 문서)
#
#   [셸 형식]  CMD python -m app.main
#     → /bin/sh -c "python -m app.main" 로 실행된다
#     → PID 1 은 sh 다. 파이썬은 자식이다
#     → kubelet 이 SIGTERM 을 보내면 sh 가 받는다
#     → sh 는 그걸 자식에게 안 넘긴다
#     → 앱은 신호를 못 받는다 → graceful shutdown 이 안 돈다
#     → terminationGracePeriod 를 다 기다린 뒤 SIGKILL 을 맞는다
#
#   [exec 형식]  CMD ["python", "-m", "app.main"]
#     → 파이썬이 직접 PID 1 이다
#     → SIGTERM 이 그대로 온다 → main.py 의 종료 절차가 돈다
#
#   main.py 에 그렇게 공들여 종료 순서를 짜놓고
#   여기서 셸 형식을 쓰면 그 코드가 한 줄도 안 돈다
CMD ["python", "-m", "app.main"]

# APP_COMPONENT 로 API 와 Worker 를 가른다                 ★
#   같은 이미지를 두 Deployment 가 쓴다
#     API     APP_COMPONENT=api
#     Worker  APP_COMPONENT=worker
#   → 이미지를 둘로 나누면 버전이 어긋난다

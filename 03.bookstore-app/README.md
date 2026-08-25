# 3단계. 장애 실험을 위한 애플리케이션 개발 — 서점 APP

`cloud-native-learning-roadmap.md` **3단계**의 작업 기록이다.

## 이 단계의 목적

**앱을 잘 만드는 것이 아니다.** 로드맵 3단계의 제목이 그것을 말한다 — "장애 실험을 위한".

```text
이 앱은 결과물이 아니라 실험 대상이다

  5단계   무엇을 관측할 것인가를 실험할 재료
  6단계   장애를 일으킬 대상
  8~9단계 CI/CD 와 GitOps 가 배포할 물건
```

```text
[그래서 하지 않는 것]
  회원가입 / 로그인 / 소셜 로그인
  장바구니 / 위시리스트 / 리뷰 / 추천
  실제 결제 연동 / 배송 조회
  화면(프론트엔드)

  → 이걸 다 만들면 3단계에서 두 달이 간다
  → 이 프로젝트의 목적은 DevOps 다
```

```text
[대신 반드시 만드는 것]
  Health Check 두 종류와 그 판단 기준
  Prometheus Metrics
  장애를 일부러 일으키는 엔드포인트
```

## 왜 서점 APP 인가

로드맵 원안은 추상적인 "작업 처리 시스템"(`POST /tasks`)이었다. **구조는 그대로 두고 도메인만 서점으로 바꿨다.**

```text
[로드맵 원안]         [서점 APP]
POST /tasks      →    POST /orders        주문 접수
GET  /tasks      →    GET  /orders        주문 목록
GET  /tasks/:id  →    GET  /orders/{id}   주문 처리 상태
Worker           →    주문 처리기
```

```text
[바꿔서 얻는 것]
  1. 책 조회라는 "읽기 경로" 가 생긴다
     → 캐시가 자연스럽게 붙는다 → 연쇄 장애 실험이 가능해진다
  2. 재고라는 "동시성 문제" 가 생긴다
     → Pod 를 늘리면 재고가 음수가 되는 상황을 만들 수 있다
  3. 추상적인 "task" 보다 이해하기 쉽다
     → 장애가 났을 때 "사용자에게 무슨 일이 일어났는가" 를 말할 수 있다

[유지하는 것]
  Redis Queue 와 Worker
  → 6단계가 이 구조를 전제한다
    "요청은 폭증했는데 CPU 는 안 오른다" 는 상황을 만들려면
    전 구간이 I/O 중심이어야 한다
```

## 기술 선택

| 항목 | 선택 | 이유 |
|---|---|---|
| 언어 / 프레임워크 | Python 3.12 / FastAPI | 앱 개발에 시간을 덜 쓴다. 목적이 DevOps 이므로 |
| 데이터베이스 | PostgreSQL | 2단계에서 StatefulSet 실습을 그대로 이어간다 |
| 캐시 / 큐 | Redis | 캐시·큐·세션을 한 번에. 연쇄 장애 실험의 주인공 |
| 메트릭 | prometheus-client | 5단계에서 Prometheus 가 긁어간다 |
| 로컬 개발 | Docker Compose | 로드맵 3단계 결과물 |

```text
[Python 을 고른 대가 — 문서에 실측해 남긴다]
  이미지가 크다 (150MB 내외 예상)
  기동이 느리다 (1~3초)
  → 4단계 롤링업데이트, 6단계 readinessProbe 설정에서 이 값이 영향을 준다
  → Go 였다면 어땠을지를 수치로 비교해두면 나중에 판단 근거가 된다
```

## 폴더 구조

```text
d:\SJPARK\cloud-native\
├─ 03.bookstore-app\      설계와 작업 기록 (이 폴더)
│   README.md
│   00-architecture.md  ...
│
└─ Books-app\             실제 코드
```

```text
[왜 나눴나]
  AGENTS.md 에 저장소 분리 계획이 있다
    규모가 커지면 app / infra / gitops / kubernetes-lab 으로 나눈다

  Books-app 이 그대로 app 저장소가 된다
  03.bookstore-app 은 "그때 무엇을 왜 이렇게 정했는지" 의 기록으로 남는다
```

## 진행 순서

**설계 문서를 먼저 쓰고 코드를 짠다.** 로드맵 3단계 결과물이 "아키텍처 문서 / API 명세 / Health Check 설계 / Metrics 설계"를 요구하기 때문이다.

```text
코드를 먼저 짜면 설계가 코드에 묻힌다
"왜 이렇게 만들었는가" 를 설명할 수 없게 된다
```

| 문서 | 내용 | 상태 |
|---|---|---|
| [00-architecture.md](00-architecture.md) | 세 경로의 구조와 그렇게 나눈 이유 | ✅ |
| [01-api-spec.md](01-api-spec.md) | 엔드포인트, 요청·응답, 상태 코드 | ✅ |
| [02-cloud-portability.md](02-cloud-portability.md) | **같은 이미지가 EKS 에서도 돌게 하는 제약** ★ | ✅ |
| [03-data-model.md](03-data-model.md) | books / orders 두 테이블 | ✅ |
| [04-health-check.md](04-health-check.md) | live 와 ready 를 무엇으로 판단할 것인가 ★ | ✅ |
| [05-metrics.md](05-metrics.md) | 세 경로별로 무엇을 잴 것인가 ★ | ✅ |
| [06-fault-injection.md](06-fault-injection.md) | 장애를 일부러 일으키는 방법 | ✅ |
| [07-dockerfile.md](07-dockerfile.md) | 이미지 빌드. 크기와 시간 실측 | ✅ |

> 이미지 레이어와 멀티스테이지 빌드의 원리는 블로그 원고로 먼저 정리했다.
> [작업다이어리/03.bookstore-app/2026-08-25 작업노트](../작업다이어리/03.bookstore-app/)
> — 지웠는데 크기가 안 주는 이유, overlayfs 의 lower/upper, 컨테이너 데이터가 사라지는 이유
| [08-compose.md](08-compose.md) | 로컬 개발 환경 | ✅ |
| `09-implementation.md` | 실제 구현과 겪은 문제 | ⬜ |

> **[02-cloud-portability.md](02-cloud-portability.md) 는 뒤의 모든 문서에 걸리는 제약이다.**
> 설계 판단을 할 때마다 그 문서로 돌아간다.

## 2단계에서 넘어온 것

### 반드시 반영할 것

```text
[04편 — probe 는 성공하는데 실제로는 503]
  readinessProbe 가 /healthz 파일만 보고 있어서
  index.html 이 없어도 "정상" 이라고 판단했다

  → 03-health-check.md 에서 이 실수를 안 하도록 설계한다
  → ready 는 "이 Pod 가 요청을 처리할 수 있는가" 를 판단해야 한다
```

```text
[2단계 전체 — 조용한 실패 네 번]
  볼륨은 Bound 인데 데이터가 없었다
  Pod 가 멈췄는데 이벤트가 없었다
  DaemonSet 이 0개인데 지표가 정상이었다
  백업이 실패했는데 아무도 몰랐다

  → 04-metrics.md 에서 "무엇을 봐야 이걸 알 수 있는가" 를 설계에 넣는다
```

```text
[13편 — 백업의 다섯 가지 문제]
  원본과 같은 노드 / 보관 정책 없음 / 복구 미검증 /
  파일 복사로는 정합성 안 맞음 / 실패를 모름

  → PostgreSQL 백업 CronJob 을 만들 때 다섯 개를 다 처리한다
```

### 그대로 가져다 쓸 것

```text
[10편] local PV + nodeAffinity 구성   → PostgreSQL StatefulSet
[13편] 백업 CronJob 구조              → pg_dump 로 바꾸면 된다
        concurrencyPolicy: Forbid
        restartPolicy: Never
        timeZone: Asia/Seoul
```

### 현재 클러스터의 제약

```text
worker 가 2대뿐이다        복제본 3개를 서로 다른 노드에 못 둔다
StorageClass 가 없다       PV 를 손으로 만들어야 한다
control-plane 이 1대다     master01 이 죽으면 클러스터 전체가 멈춘다
worker RAM 4GB            Pod 개수와 메모리 제한을 보수적으로 잡는다
```

## 이 단계에서 하지 않는 것

```text
Kubernetes 배포           4단계. 여기서는 Docker Compose 까지만
Ingress Controller 설치    4단계
Prometheus / Grafana      5단계. 여기서는 /metrics 를 내보내는 것까지만
장애 실험                  6단계. 여기서는 장애를 일으킬 "수단" 만 만든다
Helm                     7단계
CI / CD                  8~9단계
```

**"지표를 내보내는 것" 과 "그걸 모으는 것" 은 다른 단계다.** 3단계에서 설계해두지 않으면 5단계에 Prometheus 를 깔아도 볼 게 없다.

## 작업 원칙

1~2단계와 같다.

1. **명령은 직접 실행한다.** AI 도우미는 실행할 명령과 그 이유, 정상 출력의 모습을 제시한다.
2. **예상과 다른 출력이 나오면 우회하지 않는다.** 원인을 먼저 분석한다.
3. **실제 출력을 문서에 남긴다.**
4. 한 단계를 마치면 **번호 문서와 블로그 원고를 모두** 작성한다.
5. **틀린 것은 정정 표시와 함께 남긴다.** 지우지 않는다.

### 이 단계에 추가되는 원칙

```text
6. 앱 코드에 시간을 쓰지 않는다
   "동작하는 최소한" 이면 충분하다
   대신 Health Check / Metrics / 장애 주입에는 시간을 쓴다

7. 모든 설정값은 환경변수로 뺀다
   4단계에서 ConfigMap 과 Secret 으로 옮길 것이기 때문이다
   → 06편에서 본 "ConfigMap 을 바꿔도 환경변수는 안 바뀐다" 를 기억한다
```

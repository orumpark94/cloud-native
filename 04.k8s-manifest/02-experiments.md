# 02. 실험 기록

작업일: 2026-08-26

로드맵 4단계 결과물 중 실험 부분이다. 시간순으로 적는다.

```text
실험 D   PVC 기반 데이터 유지        완료
실험 A   Rolling Update              완료 — 발견이 많았다
실험 B   ConfigMap 변경              완료
실험 C   잘못된 Selector 로 인한 장애  예정
실험 E   노드 장애                   예정
```

---

## 실험 D — PVC 데이터 유지

### 목적

StatefulSet 의 핵심 주장을 검증한다.

```text
"Pod 를 지웠다 다시 만들어도 같은 이름 → 같은 PVC → 같은 데이터"
```

### 설계 — 구분되는 표시를 남긴다

```bash
kubectl exec postgres-0 -- psql -U bookstore -d bookstore \
  -c "INSERT INTO books (title, price, stock) VALUES ('삭제 전 기록', 1, 1) RETURNING id;"
# id 1001
```

```text
"1000권이 그대로 있다" 만으로는 부족하다
초기 스키마가 다시 돌아서 1000권이 새로 들어갔을 수도 있다
→ 초기 데이터에 없는 행을 하나 넣어둔다
```

### 실행

```bash
kubectl delete pod postgres-0
```

### 결과

| 항목 | 삭제 전 | 삭제 후 |
|---|---|---|
| Pod 이름 | `postgres-0` | `postgres-0` (그대로) ★ |
| Pod AGE | 140m | 24s (초기화) |
| Pod IP | 10.244.5.28 | 10.244.5.29 (바뀜) |
| 노드 | worker01 | worker01 (그대로) |
| PVC 이름 | `data-postgres-0` | 그대로 |
| PVC AGE | 142m | **142m (그대로)** ★ |
| books | 1001건 | 1001건 |
| `'삭제 전 기록'` | 있음 | **있음** ★★ |
| orders | 3건 | 3건 |

복구 시간 **11초**.

### 로그에서 확인한 것

```text
PostgreSQL Database directory appears to contain a database; Skipping initialization
```

```text
★ 두 번째 기동부터는 /docker-entrypoint-initdb.d 가 실행되지 않는다
  → ConfigMap 의 스키마를 고쳐도 반영이 안 된다
  → "스키마 변경을 어떻게 배포하는가" 가 여기서 나온다
  → 답은 Job 이나 마이그레이션 도구다
```

### 덤 — API 는 재시작하지 않았다

```text
{"msg": "의존 서비스 연결 회복", "dependency": "postgres"}
```

```text
DB 가 11초간 죽었는데
  API Pod  Running 유지, RESTARTS 0
  probe    통과 유지

readiness 에도 liveness 에도 DB 를 안 넣은 결과다 (3단계 04 문서)
```

```text
★ 만약 넣었다면
  DB 가 죽는 순간 API 2개가 동시에 재시작
  → CrashLoopBackOff → 백오프
  → 11초 장애가 몇 분짜리 장애가 된다
```

### PVC 관련해서 알아둘 것

```text
StatefulSet 을 지워도 PVC 는 안 지워진다
→ 데이터 보호가 의도다
→ 실험을 다시 하려면 PVC 를 손으로 지워야 한다
→ "지웠는데 옛날 데이터가 그대로다" 의 원인
```

```text
reclaimPolicy: Retain 인 PV 는 PVC 를 지우면 Released 가 된다
→ claimRef 에 지워진 PVC 정보가 남아 새 PVC 가 못 붙는다
→ 재사용하려면 PV 를 지우고 다시 만들어야 한다
→ 2단계 PV 3개를 그래서 지웠다
```

---

## 실험 A — Rolling Update

**이번 단계에서 가장 많이 배운 실험이다.** 측정 설계에서 세 번 실패했고, 그 과정에서 앱 버그를 하나 찾았다.

### 목적

```text
maxUnavailable: 0 + 종료 대기가 실제로 무중단을 만드는가
```

### 측정 설계 실패 3회 ★

#### 실패 1 — 대조군이 실제로는 안 바뀌었다

```bash
kubectl patch configmap bookstore-config \
  --type merge -p '{"data":{"SHUTDOWN_GRACE_SECONDS":"0"}}'
kubectl rollout restart deployment/api
```

```text
환경변수는 Pod 시작 시점에 프로세스로 복사된다

  patch 시점에 도는 Pod    grace=5 를 갖고 태어남
  rollout 으로 새로 뜬 Pod  grace=0 을 갖고 태어남
  rollout 으로 죽는 Pod     ← grace=5 다  ★

→ 죽는 쪽이 여전히 옛 값이었다. 대조가 성립하지 않았다
```

```text
[교훈] 조건을 바꿨다고 믿지 말고 확인한다
  kubectl exec deploy/api -- printenv SHUTDOWN_GRACE_SECONDS
```

값을 바꾸려면 배포를 **두 번** 해야 한다. 한 번은 새 값을 심으러, 한 번은 그 값이 죽는 과정을 보러.

#### 실패 2 — 부하를 만드는 곳이 틀렸다

```bash
# master01 에서
curl http://api.bookstore.svc.cluster.local:8000/books
```

```text
전부 실패했다. 노드는 Pod 가 아니라 클러스터 DNS 를 쓰지 않는다
```

```text
그리고 출력이 "000000" 으로 나왔다
  curl -w '%{http_code}'  실패해도 "000" 을 출력한다
  || echo 000             그리고 또 "000" 을 출력한다
→ 0이 여섯 개
```

#### 실패 3 — 분모가 달랐다

```text
A 조건   실패 5 / 500
B 조건   실패 4 / ???     ← 500회를 다 돌리기 전에 끊었다
```

```text
[교훈] 세 번 다 "성공한 것처럼 보이는" 실패였다
  ConfigMap 은 patched 라고 나왔고
  curl 은 숫자를 돌려줬고
  로그에는 200 이 잔뜩 있었다
→ 결과가 나왔다고 측정이 된 게 아니다
```

### 정착한 측정 방법

```bash
kubectl run loadgen --restart=Never --image=curlimages/curl:8.10.1 -- \
  sh -c 'i=0; while [ $i -lt 500 ]; do
    c=$(curl -s -o /dev/null -m 2 -w "%{http_code}" http://api:8000/books?limit=1)
    echo "$(date +%H:%M:%S) $c"; i=$((i+1)); sleep 0.15
  done'

kubectl wait --for=condition=Ready pod/loadgen --timeout=60s
sleep 5
kubectl rollout restart deployment/api
kubectl rollout status deployment/api

sleep 75
kubectl logs loadgen | grep -vc " 200$"    # 실패
kubectl logs loadgen | wc -l               # 분모
kubectl delete pod loadgen
```

```text
[핵심]
  Pod 안에서 돌린다        클러스터 DNS 가 된다
  Pod 안이라 master01 CPU 경합에서도 자유롭다
  NodePort 경로를 안 거친다 → 변수가 하나 준다
  분모를 고정한다
```

### 결과

| 조건 | 실패 | 총 요청 | 비율 | `delete` 소요 |
|---|---|---|---|---|
| preStop 없음 | 5 | 500 | 1.0% | 1.9초 |
| preStop 5초 | **0** | 405 | 0% | **6.96초** |

실패 패턴

```text
08:23:43  000 ×2
08:23:50  000 ×4      ← 7초 뒤
→ 두 덩어리. Pod 2개를 교체하니 교체 지점이 2번이다
```

```text
17:14:21.565  000
17:14:21.789  200
17:14:22.007  000
17:14:22.263  200
→ 번갈아 나온다. 하나는 죽고 하나는 살아 있다는 뜻
```

```text
★ 이건 "서비스가 멈췄다" 가 아니다
  서비스는 살아 있었고 일부 요청만 잘못된 곳으로 갔다
  전체 장애와 부분 실패는 원인도 대응도 다르다
```

### 원인

```text
Pod 를 지우면 두 가지가 동시에 시작된다
  kubelet 이 SIGTERM 을 보낸다
  EndpointSlice 에서 그 Pod 를 뺀다

두 번째가 각 노드의 kube-proxy → iptables 로 퍼지는 데 시간이 걸린다
→ 그 사이 요청은 아직 옛 규칙을 탄다
→ Pod 가 이미 죽었으면 연결 거부
→ Pod 가 아직 살아 있으면 정상 응답        ← preStop 이 만드는 상황
```

```text
★ readiness 를 끄는 것으로는 부족하다
  Pod 삭제 시 deletionTimestamp 가 찍히고
  EndpointSlice 컨트롤러는 그것만 보고 즉시 제거한다
  → probe 결과를 기다리지 않는다
  → 실제로 값을 하는 건 "기다린다" 쪽이다
```

```text
★ maxUnavailable: 0 과는 다른 문제다
  maxUnavailable: 0   "Ready 인 Pod 수가 줄지 않는다"  → 용량 문제
  종료 대기            "지워지는 Pod 가 잠깐 더 산다"   → 전파 지연 문제
  둘 다 필요하다
```

---

## 발견 — 앱의 종료 대기가 동작하지 않았다 ★★

`grace` 값을 0과 5로 바꿔도 결과가 같아서 종료 로그를 직접 잡았다.

```bash
POD=$(kubectl get pods -l app.kubernetes.io/name=api \
        -o jsonpath='{.items[0].metadata.name}')
kubectl logs -f "$POD" > /tmp/shutdown.log 2>&1 &
sleep 2
time kubectl delete pod "$POD"
```

```text
08:29:42.088  종료 신호 수신 (SIGTERM)
08:29:42.088  readiness 를 껐다. grace_seconds: 5.0     ← 5초 기다린다고 선언
08:29:42.211  종료 신호 재수신. 즉시 종료한다            ← 123ms 뒤  ★
08:29:42.414  종료 신호 재수신. 즉시 종료한다            ← 또  ★
08:29:42.415  종료 완료

real  0m1.898s
```

**SIGTERM 한 번에 신호 처리기가 세 번 불렸다.**

원인 코드

```python
def _on_signal(name):
    if runtime.shutting_down:
        # 두 번째 신호는 "빨리 죽어라" 다. 기다리지 않고 바로 닫는다
        logger.warning("종료 신호 재수신. 즉시 종료한다")
        stop.set()
        return
```

```text
사람이 Ctrl+C 를 두 번 누르는 상황을 생각하고 넣은 것이다
시스템이 중복 전달하자 그 조건에 걸려 대기가 통째로 사라졌다
→ grace 를 0으로 두든 5로 두든 결과가 같았던 이유
```

### 왜 세 번 불렸는지는 미확인

```text
[추정] uvicorn 이 자체 신호 처리기를 설치하는 것과 겹쳤다
       그리고 포트 2개(8000/9000)를 위해 서버를 2개 띄웠다

확인하지 못했다. 단정하지 않는다
다만 고치는 방법은 원인과 무관하다
```

### 수정

```python
DUPLICATE_WINDOW = 2.0

if now - signal_state["first_at"] < DUPLICATE_WINDOW:
    logger.debug("종료 신호 중복 전달. 무시한다")
    return
```

```text
사람이 두 번 누르는 간격보다는 짧고
시스템이 중복 전달하는 간격(수백 ms)보다는 긴 값으로 자른다
```

### 수정 후 검증

```text
08:42:54.172  종료 신호 수신
08:42:59.176  PostgreSQL 커넥션 풀 종료     ← 5.004초 뒤  ★
08:42:59.181  종료 완료

real  0m11.805s   =  preStop 5초 + 앱 대기 5초 + α
```

```text
"종료 신호 재수신" 이 사라졌고 5초가 정확히 흘렀다
```

### Compose 에서는 안 드러났다

```text
docker compose down 은 응답 시간을 재지 않는다
"종료하는 동안 요청을 계속 보내며 재본다"
→ 이걸 해야만 나온다
```

### preStop 과 앱 대기를 둘 다 두기로 했다

```text
[중복이지만 해롭지 않다]
  종료에 약 11초가 걸린다
  terminationGracePeriodSeconds 30초 안에 들어간다
  정확성 문제가 아니라 속도 문제다

[앱 쪽을 남기는 이유]
  preStop 은 Kubernetes 전용이다
  앱이 스스로 하면 어디서든 같은 동작을 한다
  매니페스트에서 preStop 을 빠뜨려도 완전 무방비가 되지는 않는다

[6단계에서 롤아웃을 자주 돌리게 되면 그때 줄인다]
```

> `kubectl delete pod` 이 11초 걸리는 것은 의도된 값이다. 장애로 오해하지 않도록 여기 적어둔다.

---

## 실험 B — ConfigMap 변경

실험 A 도중에 실제로 바꿔야 할 이유가 생겨서 자연스럽게 진행됐다.
이미지를 `20260826-0839` 로 올렸는데 `APP_VERSION` 은 `0301` 인 채였다.

### 결과

```bash
kubectl patch configmap bookstore-config \
  --type merge -p '{"data":{"APP_VERSION":"20260826-0839"}}'

kubectl get configmap bookstore-config -o jsonpath='{.data.APP_VERSION}'
# 20260826-0839      ← 오브젝트는 즉시 바뀐다

kubectl exec deploy/api -- printenv APP_VERSION
# 20260826-0301      ← Pod 는 안 바뀐다  ★
```

```bash
kubectl rollout restart deployment/api
kubectl exec deploy/api -- printenv APP_VERSION
# 20260826-0839      ← 이제 반영
```

지표도 확인

```text
app_info{... version="20260826-0839"} 1.0
```

### 왜 안 바뀌는가

```text
환경변수는 프로세스가 시작할 때 복사된다
→ 리눅스에서 남의 프로세스 환경변수를 바꿀 방법이 없다
```

### 볼륨 마운트는 다르다

```bash
kubectl exec postgres-0 -- ls -la /docker-entrypoint-initdb.d/
```

```text
..2026_08_26_08_06_22.3719455717     실제 디렉터리
..data -> ..2026_08_26_08_06_22...   심볼릭 링크
01_schema.sql -> ..data/01_schema.sql
```

```text
ConfigMap 이 바뀌면 kubelet 이 새 디렉터리를 만들고
..data 링크만 원자적으로 바꿔치기한다
→ 앱이 "반쯤 쓰인 파일" 을 보는 일이 없다
→ 갱신에 최대 1분쯤 걸린다

★ 그런데 파일이 바뀌어도 앱이 다시 읽어야 반영된다
  nginx 처럼 SIGHUP 으로 설정을 다시 읽는 앱만 이득을 본다
```

### 정리

| 주입 방식 | ConfigMap 변경 시 |
|---|---|
| 환경변수 (`envFrom`, `env`) | 반영 안 됨. Pod 재시작 필요 |
| 볼륨 마운트 | 파일은 바뀜 (최대 1분). 앱이 다시 읽어야 함 |

```text
실무에서는 ConfigMap 의 해시를 Pod annotation 에 넣어
내용이 바뀌면 Pod 스펙이 바뀌게 만든다 (Helm 이 흔히 하는 방식)
→ 자동으로 롤아웃이 걸린다
→ 지금은 안 한다. 이 불편을 겪는 게 근거가 된다
```

---

## 측정값 모음

| 항목 | 값 |
|---|---|
| PostgreSQL 첫 기동 (initdb + 스키마) | 11초 |
| PostgreSQL 재기동 (데이터 있음) | 11초 |
| API Pod 기동 → Ready | 7~28초 |
| 롤링 업데이트 전체 (replicas 2) | 약 30초 |
| `delete pod` — preStop 없음 | 1.9초 |
| `delete pod` — preStop 5초 | 6.96초 |
| `delete pod` — preStop 5 + 앱 5 | 11.8초 |
| 배포 중 실패율 — 대기 없음 | 1.0% (5/500) |
| 배포 중 실패율 — preStop 5초 | 0% (0/405) |

---

## 다음

```text
실험 C   잘못된 Selector 로 인한 장애
         api Service 의 selector 를 틀리게 바꿔 Endpoint 가 비는 걸 관찰
         → Pod 는 멀쩡한데 트래픽이 안 간다

실험 E   노드 장애 (worker01 종료)
         PostgreSQL 이 다른 노드로 못 가고 Pending 에 남는 것 확인
         → local PV 의 nodeAffinity 가 족쇄가 되는 상황
         → 되돌리는 데 시간이 걸리므로 마지막에 한다
```

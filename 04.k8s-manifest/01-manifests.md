# 01. Manifest 구성

작업일: 2026-08-26
매니페스트 실물은 `k8s/` 에 있다.

## 파일 목록과 적용 순서

```text
00-namespace.yaml                    Namespace
01-configmap.yaml                    앱 설정 (비밀 아닌 값)
02-secret.yaml                       DATABASE_URL, POSTGRES_PASSWORD
03-postgres-pv.yaml                  local PV (worker01 고정)
04-postgres-schema-configmap.yaml    초기 스키마
05-postgres.yaml                     Headless Service + StatefulSet
06-redis.yaml                        Service + Deployment
07-api.yaml                          Service + Deployment (replicas 2)
08-worker.yaml                       Deployment (replicas 1, Service 없음)
09-api-nodeport.yaml                 NodePort 30800
```

```text
[의존 관계]
  ConfigMap/Secret  →  Namespace 가 있어야 만들 수 있다
  StatefulSet       →  PV, ConfigMap, Secret 을 참조한다
  PV                →  클러스터 범위. 아무것도 필요 없다

→ 번호 순서대로 적용하면 된다
```

## 클러스터 사전 조건

```text
StorageClass 없음
  → 동적 프로비저닝이 안 된다
  → PV 를 손으로 만든다

Ingress 컨트롤러 없음
  → NodePort 로 외부 접근을 만든다
  → Ingress 는 나중에 도입하고 비교한다

master01 에 NoSchedule taint
  → 앱 Pod 가 안 뜬다
  → 이미지를 워커 2대에만 밀어넣으면 된다
```

---

## 1. Namespace

```text
default 를 안 쓰는 이유
  Namespace 는 격리가 아니다. 이름 충돌을 막는 구획일 뿐이다
  네트워크도 리소스도 안 막힌다 (NetworkPolicy / ResourceQuota 가 따로 필요)

  그래도 나누는 실익
    kubectl delete namespace 하나로 전부 지울 수 있다
    실험을 반복할 때 이게 결정적이다
```

> 2단계 실습 잔여물(`k8s-lab`)을 그 한 줄로 정리했다.
> 그때 kubectl 컨텍스트가 그 네임스페이스를 가리키고 있어서, 이후 `-n` 없는 명령이 전부 없는 네임스페이스를 향했다.
> `kubectl config set-context --current --namespace=<이름>` 으로 되돌린다.

---

## 2. ConfigMap 과 Secret 을 나눈 기준

```text
DATABASE_URL   비밀번호가 들어 있다   → Secret
REDIS_URL      비밀번호가 없다        → ConfigMap
```

```text
같은 "접속 URL" 인데 갈린다
기준은 오직 "노출돼도 되는가" 다

앱은 둘 다 그냥 환경변수로 읽는다. 어디서 왔는지 모른다
→ 그래서 나누는 판단이 순수하게 보안 기준으로만 결정된다
```

### Secret 은 암호화가 아니다

```bash
kubectl get secret bookstore-secret -o jsonpath='{.data.DATABASE_URL}' | base64 -d
```

```text
평문이 그대로 나온다. base64 는 인코딩이지 암호화가 아니다

그래도 쓰는 이유
  1. RBAC 을 따로 걸 수 있다
  2. kubectl describe 에 값이 안 찍힌다 (ConfigMap 은 그대로 보인다)
  3. 로그와 이벤트에 안 남는다
  4. etcd 저장 시 암호화를 켤 수 있다

→ "안전하다" 가 아니라 "통제할 수 있다" 가 맞다
```

### 값이 두 곳에 적히는 구조 ★

```text
DATABASE_URL 안의 비밀번호
POSTGRES_PASSWORD (컨테이너가 쓰는 값)
→ 한쪽만 고치면 앱이 못 붙는다

실무에서는 Operator 나 External Secrets 가 한 곳으로 모은다
지금은 손으로 맞춘다. 이 불편함이 나중의 근거가 된다
```

> 실제로 같은 종류의 문제가 터졌다. 이미지 태그와 `APP_VERSION` 이 어긋나 지표가 거짓말을 했다. 6절 참조.

---

## 3. PostgreSQL

### `hostPath` 가 아니라 `local`

```text
hostPath   경로만 지정한다. 어느 노드인지 모른다
           → Pod 가 다른 노드에 뜨면 빈 디렉터리를 본다
           → 데이터가 사라진 것처럼 보인다 (2단계 09편에서 겪은 상황)

local      nodeAffinity 가 필수다
           → Scheduler 가 그 노드로만 보낸다
           → 조용한 데이터 손실을 구조적으로 막는다
```

```text
★ 이 안전장치가 동시에 족쇄다
  worker01 이 죽으면 PostgreSQL Pod 는 어디로도 못 간다
  → Pending 으로 남는다
  → "DB 를 Kubernetes 에 올려도 되는가" 의 핵심 쟁점 (2단계 11편)
  → 실험 E 에서 확인 예정
```

### 겪은 문제 — 디렉터리가 없어서 멈췄다

```text
Warning  FailedMount  MountVolume.NewMounter initialization failed
                      for volume "bookstore-pgdata"
                      : path "/mnt/disks/pgdata" does not exist
```

```text
local 볼륨은 디렉터리를 자동으로 안 만든다
→ 없으면 마운트 단계에서 멈춘다
→ 동적 프로비저닝이 있으면 프로비저너가 만들어준다. 우리는 없다
```

```bash
ssh -t sjpark@worker01 "sudo mkdir -p /mnt/disks/pgdata"
```

```text
★ 진단이 어려운 이유
  PV 도 PVC 도 Bound 상태였다
  → "짝이 맞았다" 는 뜻일 뿐, 경로가 존재하는지는 확인하지 않는다
  → 마운트할 때가 되어서야 알게 된다

  Bound 만 보고 넘어가면 원인을 못 찾는다
  → describe 의 Events 를 봐야 한다
```

### `PGDATA` 를 하위 디렉터리로

```yaml
- name: PGDATA
  value: /var/lib/postgresql/data/pgdata
```

```text
마운트 지점을 그대로 데이터 디렉터리로 쓰면
initdb 가 "빈 디렉터리" 를 요구하는데 lost+found 같은 게 있을 수 있다
→ "directory not empty" 로 기동 실패
```

### `fsGroup` 은 이미지의 GID 와 안 맞아도 된다

```yaml
securityContext:
  fsGroup: 999
```

```text
999 는 데비안 계열 postgres 의 GID 다. 우리는 alpine 을 쓴다 (uid 70)
그런데도 동작했다

fsGroup 이 하는 일
  1. 마운트 지점의 그룹을 999 로 바꾸고 g+w, setgid 를 준다
  2. ★ 컨테이너 프로세스에 999 를 보조 그룹으로 추가한다

→ 이미지의 실제 GID 와 안 맞아도 쓸 수 있게 된다
```

실제 확인

```text
[컨테이너 안]  drwxrwsrwx  root  ping
[노드]         drwxrwsr-x  root  systemd-journal
```

```text
같은 디렉터리인데 그룹 이름이 다르다

  Ubuntu 노드의 /etc/group     999 = systemd-journal
  alpine 컨테이너의 /etc/group  999 = ping

→ 커널은 999 라는 숫자만 안다. 이름은 보는 쪽이 붙인다
→ 컨테이너 보안에서 UID/GID 를 숫자로 다뤄야 하는 이유
```

### probe — liveness 를 안 건다

```text
DB 가 응답을 안 하는 이유는 대개 재시작으로 안 풀린다
  긴 쿼리로 바쁨 / 커넥션 포화 / 디스크 문제
→ 재시작하면 트랜잭션이 날아가고 복구 시간만 는다

단일 인스턴스라 재시작하면 서비스가 그동안 통째로 멈춘다
→ 사람이 보고 판단하는 게 낫다

HA 구성(Patroni 등)이라면 판단이 달라진다
```

```text
startupProbe 로 초기화 시간을 벌어준다
  첫 기동에는 initdb + 스키마 적용이 돈다
  실측 11초 걸렸다
```

---

## 4. Redis — StatefulSet 이 아닌 이유

```text
영속성을 껐다 (--save "" --appendonly no)
→ 디스크에 아무것도 안 쓴다
→ 이름이 고정될 이유도, 볼륨이 따라올 이유도 없다
→ Deployment 로 충분하다

★ 대가
  Pod 가 죽으면 캐시도 큐도 통째로 사라진다
  → 접수된 주문이 증발한다
  → 00 문서에서 "미리 해결하지 않기로 한" 그 모순이다
```

### `strategy: Recreate` ★

```text
기본값 RollingUpdate 을 일부러 바꿨다

[RollingUpdate 면]
  잠깐 Redis 가 두 개 공존한다
  → Service 가 둘에 나눠 보낸다
  → A 에 캐시한 걸 B 에 물어본다 → 항상 miss
  → A 큐에 넣은 주문을 B 에서 꺼내려 한다 → 영원히 안 나온다

[Recreate 면]
  완전히 지운 뒤 새로 만든다. 잠깐 다운타임. 대신 공존이 없다
```

### liveness 를 안 거는 이유가 하나 더 있다

```text
영속성이 꺼져 있으므로 재시작 = 큐 전체 소실
→ "고치려다 데이터를 지우는" 셈이다

★ 그리고 실험이 오염된다
  6단계에서 Redis 를 죽여 큐가 사라지는 걸 관찰할 예정이다
  liveness 가 자동으로 되살리면 그 관찰이 안 된다
```

---

## 5. API 와 Worker

### 두 Deployment 가 같은 이미지를 쓴다

```yaml
# 07-api.yaml
image: bookstore:20260826-0839
env:
  - name: APP_COMPONENT
    value: "api"

# 08-worker.yaml
image: bookstore:20260826-0839      # 완전히 같다
env:
  - name: APP_COMPONENT
    value: "worker"
```

```text
이미지를 둘로 나눴다면
  API 는 v2 인데 Worker 는 v1 인 상태가 생긴다
  두 번 빌드하고 두 번 노드에 밀어넣어야 한다
```

### `envFrom` 과 `env` 를 섞는다

```text
ConfigMap  통째로 (envFrom)
  안에 있는 게 전부 이 앱의 설정이다

Secret     골라서 (env + secretKeyRef)
  POSTGRES_PASSWORD 도 들어 있는데 API 가 알 필요가 없다
  → 통째로 넣으면 API 컨테이너 환경변수에 박힌다
```

### Downward API

```text
앱이 자기 Pod 이름을 알 방법이 없다. 뜨기 전에는 정해지지도 않는다
→ Kubernetes 가 환경변수로 넣어준다

app_info 지표에만 담는다
모든 지표에 pod 라벨을 붙이면 시계열이 배로 는다
```

실제 확인

```text
app_info{component="api", namespace="bookstore",
         node="worker02", pod="api-59f5476d8c-7ssml",
         version="20260826-0839"} 1.0
```

### Service 에 관리 포트(9000)를 안 넣는다 ★★

```text
9000 에는 /metrics, /health/*, /debug/inject/* 가 있다
/debug/inject 는 서비스를 마음대로 망가뜨릴 수 있는 도구다

Service 에 안 넣으면 클러스터 안에서도 api:9000 으로 못 간다

그런데 이 셋은 여전히 동작한다
  kubelet 의 probe        Pod IP 로 직접 부른다
  Prometheus 스크레이프    Pod IP 로 직접 긁는다
  kubectl port-forward     API Server 를 거쳐 Pod 로
```

밖에서 확인

```powershell
curl http://192.168.8.142:30800/metrics
# {"detail":"Not Found"}
```

```text
30800 은 8000 으로만 간다. 그 앱에는 /metrics 경로 자체가 없다
→ 경로로만 나눴다면 여기서 지표가 그대로 노출됐다
```

### `readOnlyRootFilesystem: true` 가 통과했다

```text
"Read-only file system" 에러가 없었다
→ 앱이 정말 /tmp 외에 아무 데도 안 쓴다
→ 02·07 문서에 적어둔 제약이 실제로 지켜졌다
```

### Worker 에 Service 가 없다

```text
Worker 에게는 아무도 요청을 보내지 않는다
스스로 큐를 꺼내 갈 뿐이다 → 받을 주소가 필요 없다

그런데 관리 포트는 연다
  kubelet probe / Prometheus 스크레이프 → 둘 다 Pod IP 로 직접

→ "Service 가 없으면 접근 불가" 가 아니다
```

---

## 6. NodePort

```text
api            ClusterIP   클러스터 안에서 쓰는 주소
api-external   NodePort    밖에서 들어오는 문
```

```text
따로 만든 이유
  안과 밖의 경로가 별개의 오브젝트로 보인다
  외부 노출을 끄고 싶으면 이것만 지우면 된다
  나중에 Ingress 로 갈아탈 때 이것만 걷어내면 된다

  (NodePort 는 ClusterIP 의 상위 집합이라 type 만 바꿔도 됐다.
   실무에서는 보통 그렇게 한다)
```

```text
★ 모든 노드에서 포트가 열린다. Pod 가 없는 노드도 열린다
  master01:30800 으로 붙어도 응답이 온다
  → kube-proxy 의 iptables 규칙이 실제 Pod 가 있는 노드로 넘긴다
```

```text
nodePort 를 명시한 이유
  생략하면 30000~32767 중에서 자동으로 골라준다
  → 재배포할 때마다 바뀔 수 있다
  → 6단계 k6 스크립트도 이 포트를 쓴다
```

---

## 7. 어긋난 값 — 이미지 태그와 APP_VERSION ★

코드를 고쳐 새 이미지로 배포한 뒤 로그를 보니

```text
이미지    bookstore:20260826-0839
로그      "version": "20260826-0301"
```

```text
이미지 태그    07-api.yaml 의 image: 줄
APP_VERSION    01-configmap.yaml 의 값
→ 같은 값을 두 곳에 적어놨다. 한쪽만 고쳤다
```

```text
★ app_info{version="..."} 는
  "어느 버전에서 에러가 늘었나" 를 볼 때 쓴다
  값이 틀리면 엉뚱한 버전을 의심하게 된다

  Pod 는 Running, 응답도 정상, 지표만 거짓말한다
```

```text
[대응]
  지금은 손으로 맞춘다
  → 이미지 태그를 바꿀 때 ConfigMap 도 같이 바꾼다
  → 그리고 rollout restart 를 해야 반영된다 (02-experiments.md 실험 B)

[근본 해결]
  템플릿 도구(Helm/Kustomize)나 CI 가 한 값에서 두 곳을 채우게 한다
  → 4단계 후반 또는 8단계
```

---

## 8. 배포 흐름 (현재)

```text
[Windows]  코드 / 매니페스트 수정
    │
    ├─ scp app/ → build01
    │     docker build -t bookstore:<날짜시간> .
    │     ./scripts/push-image.sh    → worker01, worker02 (약 78초)
    │
    └─ 매니페스트의 image: 태그 수정
       scp k8s/ → master01
       kubectl apply -f ...
```

```text
★ apply 만으로 롤아웃이 걸린다
  image 가 바뀌면 Pod 스펙이 달라진다 → Kubernetes 가 알아서 교체한다
  rollout restart 를 따로 칠 필요가 없다
```

```text
★ 반드시 새 태그를 써야 한다
  같은 태그로 덮으면 노드는 "이미 있다" 며 옛 이미지를 쓴다
  imagePullPolicy: IfNotPresent 이기 때문이다
```

이미지 이력

| 태그 | 이미지 ID | 내용 |
|---|---|---|
| `20260826-0128` | `ba65ff7e4d92` | 최초 빌드. Redis 타임아웃 버그 |
| `20260826-0301` | `074008a841d6` | deps/queue 수정. 첫 K8s 배포 |
| `20260826-0839` | `7a5e1d812e7f` | 종료 신호 중복 전달 방어 |

---

## 9. 다음

```text
02-experiments.md   실험 기록 (D 데이터 유지 / A 롤링 업데이트 / B ConfigMap)
실험 C              잘못된 Selector 로 인한 장애
실험 E              노드 장애 — local PV 의 nodeAffinity 가 족쇄가 되는 상황

아직 안 한 것
  Ingress   컨트롤러가 없다
  HPA       Metrics Server 가 없다 → 5단계
  PDB       노드 정비 실험을 할 때
```

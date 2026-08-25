# 02. Deployment

2단계 세 번째 오브젝트. `Pod → ReplicaSet → Deployment` 의 마지막이다.

## 이 문서의 범위

```text
[확인한 것]
  1. 이것이 푸는 문제 — ReplicaSet 은 버전을 안 지킨다        ✅
  2. 컨트롤러 사슬 — apiserver 는 저장만 한다                 ✅
  3. 3단 소유 체인과 pod-template-hash                        ✅
  4. 롤링 업데이트 — maxSurge / maxUnavailable                ✅
  5. 롤백 — 옛 ReplicaSet 을 다시 켜는 것                     ✅
  6. 실패하는 배포 — 기존 서비스가 안 죽는다                   ✅
  7. revision 의 성질                                        ✅
  8. 곁가지 — 어노테이션과 오브젝트 3부 구조                   ✅

[다루지 않는 것]
  Recreate 전략            무중단이 목적이 아닌 경우. 별도로
  HPA 와의 상호작용         5단계 Metrics Server 이후
  PodDisruptionBudget      4단계 manifest 작성 때
```

---

# 1. 이것이 푸는 문제

01 문서에서 ReplicaSet 의 한계를 확인했다.

```text
template 을 nginx:1.28 로 바꾸고 apply 했더니
  → ReplicaSet 의 선언은 바뀌었다
  → 기존 Pod 3개는 여전히 nginx:1.27 이었다
  → 지워야만 바뀐다
```

```text
ReplicaSet 이 하는 질문     "Pod 가 3개인가?"        → 예 → 할 일 없음
ReplicaSet 이 안 하는 질문   "그 3개가 최신인가?"     → 아예 안 본다
```

**사람이 하나씩 지워가며 교체하면 세 가지 문제가 생긴다.**

```text
1. 사람이 지우고 뜰 때까지 지켜봐야 한다
2. 한 번에 다 지우면 잠깐 서비스가 0개가 된다
3. 되돌리고 싶어도 옛 설정이 어디에도 안 남는다
```

**Deployment 는 이 셋을 대신한다.**

```text
ReplicaSet   "이 버전으로 3개 유지해라"        한 버전만 안다
Deployment   "이 버전에서 저 버전으로 옮겨라"   전환 과정을 안다
```

---

# 2. 컨트롤러 사슬 — apiserver 는 저장만 한다 ★

**"Deployment 만 선언했는데 왜 ReplicaSet 이 자동으로 생기나"** 가 이 절의 주제다. 이 프로젝트 전체를 관통하는 구조이므로 여기서 정리해둔다.

## apiserver 는 아무것도 만들지 않는다

```bash
kubectl apply -f deploy-demo.yaml
```

**이 명령이 하는 일 전부.**

```text
1. kubectl 이 yaml 을 읽는다
2. apiserver 에 HTTP 요청을 보낸다
     POST /apis/apps/v1/namespaces/k8s-lab/deployments
3. apiserver 가 인증 / 인가 / admission / 스키마 검증을 한다
4. etcd 에 저장한다
     /registry/deployments/k8s-lab/deploy-demo
5. "created" 라고 응답한다
```

**ReplicaSet 도 Pod 도 안 만든다.** apiserver 는 받아 적는 창구다.

07 문서 2라운드에서 확인한 것과 같다. **apiserver 는 인증·인가·검증은 하지만 "의미를 해석해서 행동" 하지는 않는다.**

## 만드는 것은 컨트롤러다

`kube-controller-manager` 프로세스 안에 컨트롤러가 40개 넘게 들어 있다.

```text
Deployment Controller   "Deployment 오브젝트" 를 지켜본다
ReplicaSet Controller   "ReplicaSet 오브젝트" 를 지켜본다
Node Controller         "Node 오브젝트" 를 지켜본다
...
```

**각 컨트롤러가 하는 일은 똑같다.**

```text
1. 내 담당 오브젝트를 본다        "이렇게 되어야 한다" (spec)
2. 지금 실제 상태를 본다          "지금은 이렇다"      (status / 실제 오브젝트)
3. 다르면 맞춘다
4. 1번으로
```

## 사슬 전체

```text
[사람]
  kubectl apply -f deploy-demo.yaml
     ↓ HTTP
[apiserver]
  etcd 에 Deployment 저장. 끝
     ↓ watch 알림
[Deployment Controller]
  "deploy-demo 가 있는데 대응하는 ReplicaSet 이 없다"
  → ReplicaSet 생성 요청
     ↓
[apiserver]  etcd 에 ReplicaSet 저장. 끝
     ↓ watch 알림
[ReplicaSet Controller]
  "replicas 3 인데 Pod 가 0개다"
  → Pod 3개 생성 요청
     ↓
[apiserver]  etcd 에 Pod 저장. 끝
     ↓ watch 알림
[Scheduler]
  "nodeName 이 빈 Pod 가 있다" → worker01 로 정함
  → binding 생성 요청 (07 2라운드에서 본 그것)
     ↓
[apiserver]  Pod 의 spec.nodeName 을 채운다
     ↓ watch 알림
[worker01 의 kubelet]
  → containerd 에게 컨테이너 생성 요청
```

**아무도 두 단계 앞을 보지 않는다.**

```text
Deployment Controller 는 Pod 를 모른다
ReplicaSet Controller 는 이미지 버전을 모른다
Scheduler 는 컨테이너를 모른다
kubelet 은 Deployment 를 모른다
```

## 이벤트가 이 사슬의 증거다

```text
KIND         OBJ                            REASON              MSG
Deployment   deploy-demo                    ScalingReplicaSet   Scaled up replica set ... from 0 to 3
ReplicaSet   deploy-demo-8588c76444         SuccessfulCreate    Created pod: ...
Pod          deploy-demo-8588c76444-m2qxr   Scheduled           Successfully assigned ... to worker01
Pod          deploy-demo-8588c76444-m2qxr   Pulled/Created/Started
```

**이벤트 대상(KIND)이 층마다 바뀐다.** 각 층이 아래층에게 넘긴 흔적이다.

**Deployment 가 낸 이벤트는 전부 `ScalingReplicaSet` 하나뿐이다.** "Pod" 라는 단어를 한 번도 쓰지 않는다.

## 권한 구조로도 확인된다

07 문서 2라운드에서 이런 출력을 보고 **"controller-manager 는 프로세스 하나인데 바인딩이 40개다"** 라며 곁가지로 넘어갔었다.

```text
system:controller:deployment-controller       ...   deployment-controller
system:controller:replicaset-controller       ...   replicaset-controller
system:controller:daemon-set-controller       ...   daemon-set-controller
... 40개 넘게
```

**그것이 이 사슬이다.** 컨트롤러마다 신원과 권한을 따로 둔다.

```text
Deployment Controller 는 ReplicaSet 을 만들 수 있어야 한다
그런데 Secret 을 읽을 필요는 없다
→ 컨트롤러마다 딱 필요한 권한만 준다
```

07 문서의 최소 권한 원칙이 **컨트롤러 단위로도** 적용되어 있다.

```bash
kubectl get clusterrolebinding | grep -E 'deployment-controller|replicaset-controller'
kubectl describe clusterrole system:controller:deployment-controller | head -20
kubectl describe clusterrole system:controller:replicaset-controller | head -20
```

## 08 문서 장애 실험과 이어진다

```text
[실험 3 — apiserver 중단]
  사슬의 모든 연결이 apiserver 를 거친다
  → apiserver 가 죽으면 사슬 전체가 멈춘다
  → 그런데 이미 만들어진 컨테이너는 계속 돈다

[실험 4 — etcd 중단]
  controller-manager 가 Lease 를 갱신 못 해 스스로 종료했다
  → 그 안의 40개 컨트롤러가 전부 멈춘 것이다
```

**"제어 평면이 죽어도 트래픽은 안 끊긴다" 가 이 구조 때문이다.** 사슬은 "만들고 바꾸는" 일만 하고, 이미 만들어진 것을 돌리는 건 kubelet 과 커널의 몫이다.

---

# 3. 생성 — 3단 소유 체인 (2026-08-13)

```yaml
# /tmp/deploy-demo.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: deploy-demo
spec:
  replicas: 3
  selector:
    matchLabels:
      app: deploy-demo
  template:
    metadata:
      labels:
        app: deploy-demo
    spec:
      containers:
      - name: nginx
        image: nginx:1.27
```

**01 문서의 `rs-demo.yaml` 과 `kind` 와 이름만 다르다.**

```text
replicas / selector / template

→ Deployment 의 spec 은 ReplicaSet 의 spec 을 그대로 포함한다
→ 거기에 "어떻게 전환할 것인가"(strategy)가 추가될 뿐이다
```

## 발견 1 — `UP-TO-DATE` 열이 생겼다 ★

```text
root@master01:/# kubectl get deploy,rs,pods -o wide
NAME                          READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/deploy-demo   0/3     0            0           0s
                                      ^^^^^^^^^^

NAME                                     DESIRED   CURRENT   READY   AGE
replicaset.apps/deploy-demo-8588c76444   3         0         0       0s
                                         (UP-TO-DATE 가 없다)
```

```text
UP-TO-DATE = 현재 template 과 일치하는 Pod 개수
```

**01 문서에서 확인한 ReplicaSet 의 한계가 열 하나의 유무로 나타난다.**

```text
[ReplicaSet]   개수만 센다 → 이 열이 없다
[Deployment]   버전 일치를 센다 → 이 열이 있다
```

`status` 에서는 `updatedReplicas` 라는 이름이다.

## 발견 2 — `pod-template-hash` 가 자동으로 붙는다 ★★

```text
root@master01:/# kubectl get pods --show-labels
NAME                           READY   STATUS    LABELS
deploy-demo-8588c76444-jhl7r   1/1     Running   app=deploy-demo,pod-template-hash=8588c76444
deploy-demo-8588c76444-m2qxr   1/1     Running   app=deploy-demo,pod-template-hash=8588c76444
deploy-demo-8588c76444-mq68r   1/1     Running   app=deploy-demo,pod-template-hash=8588c76444
```

**우리가 쓴 라벨은 `app=deploy-demo` 하나뿐이다.**

### 왜 필요한가 — 01 문서의 라벨 실험과 직결된다

```text
01 문서에서 확인한 것
  소유권은 라벨로 판단한다
  라벨이 같으면 누구 것인지 구분할 방법이 없다
```

**Deployment 는 ReplicaSet 을 여러 개 만들 예정이므로 이 문제가 필연적으로 생긴다.**

```text
구버전 RS 도 app=deploy-demo 를 센다
신버전 RS 도 app=deploy-demo 를 센다
→ 서로 뺏는다
```

```text
root@master01:/# kubectl get rs -o jsonpath='...'
deploy-demo-8588c76444
  selector: {"app":"deploy-demo","pod-template-hash":"8588c76444"}
  owner:    Deployment/deploy-demo

root@master01:/# kubectl get deploy deploy-demo -o jsonpath='{.spec.selector.matchLabels}'
{"app":"deploy-demo"}
```

```text
Deployment   selector: app=deploy-demo                       ← 넓게 본다
                       "내 모든 세대의 Pod"

ReplicaSet   selector: app=deploy-demo + pod-template-hash   ← 좁게 본다
                       "내 세대의 Pod 만"
```

**해시 하나가 세대의 경계를 만든다.**

### 해시는 template 에서 나온다

```text
template 에 image: nginx:1.27 → 해시 8588c76444
template 에 image: nginx:1.28 → 해시 654fddd69c
```

**template 을 원래대로 되돌리면 해시가 원래 값으로 돌아오고, 그 이름의 ReplicaSet 이 이미 있다.** 이것이 rollback 의 기반이다(5절).

## 발견 3 — 3단 소유 체인

```text
Deployment  deploy-demo
     │ owns
     ▼
ReplicaSet  deploy-demo-8588c76444        owner: Deployment/deploy-demo
     │ owns
     ▼
Pod         deploy-demo-8588c76444-jhl7r × 3
```

01 문서에서는 2단이었다. 층이 하나 늘었고 그 층이 세대 전환을 담당한다.

```text
Pod         "이 컨테이너를 실행해라"
ReplicaSet  "이런 Pod 가 N개 있어야 한다"
Deployment  "그 N개를 이 버전에서 저 버전으로 옮겨라"
```

## 발견 4 — 계산 결과가 어노테이션에 미리 적혀 있다 ★

```text
root@master01:/# kubectl get deploy deploy-demo -o jsonpath='{.spec.strategy}'
{"rollingUpdate":{"maxSurge":"25%","maxUnavailable":"25%"},"type":"RollingUpdate"}

root@master01:/# kubectl get rs -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations}{"\n"}{end}'
deploy-demo-8588c76444  {
  "deployment.kubernetes.io/desired-replicas":"3",
  "deployment.kubernetes.io/max-replicas":"4",
  "deployment.kubernetes.io/revision":"1"}
```

**`strategy` 는 우리가 안 썼는데 들어 있다.** `restartPolicy: Always` 처럼 자동으로 채워진다.

```text
replicas: 3

maxSurge 25%        3 × 0.25 = 0.75 → 올림 → 1
                    → 최대 3 + 1 = 4개                ← max-replicas: 4

maxUnavailable 25%  3 × 0.25 = 0.75 → 내림 → 0
                    → 사용 가능한 것이 3개 아래로 내려가면 안 된다
```

```text
올림과 내림이 반대인 이유
  maxSurge        "몇 개까지 더 만들어도 되나" → 넉넉하게 → 올림
  maxUnavailable  "몇 개까지 없어도 되나"     → 빡빡하게 → 내림
```

**둘 다 안전한 쪽으로 반올림한다.**

## 발견 5 — revision 은 ReplicaSet 의 어노테이션이다

```text
"deployment.kubernetes.io/revision":"1"
```

```text
root@master01:/# kubectl rollout history deployment deploy-demo
REVISION  CHANGE-CAUSE
1         <none>
```

**`rollout history` 는 별도 기록이 아니라 남아있는 ReplicaSet 들의 revision 어노테이션을 모아 보여주는 것이다.**

```text
옛 ReplicaSet 을 지우면 그 revision 으로 롤백할 수 없다
히스토리가 곧 ReplicaSet 목록이다
```

---

# 4. 롤링 업데이트 (15:08:28 ~ 15:09:19)

```text
root@master01:/# date '+%H:%M:%S'; kubectl set image deployment/deploy-demo nginx=nginx:1.28
15:08:28
deployment.apps/deploy-demo image updated
```

**명령은 즉시 반환된다.** 전환 완료를 기다리지 않는다.

## 개수 변화

```text
시각       구 8588c76444    신 654fddd69c    합계   available
15:08:28        3/3/3           1/0/0          4        3
15:08:30        2/2/2           2/1/1          4        3
15:08:31        2/2/2           2/2/1          4        3
15:09:17        1/2/2           2/2/2          4        3
15:09:18        1/1/1           3/3/2          4        3
15:09:19        0/0/0           3/3/3          3        3
```

```text
maxSurge 1        → 4개를 넘지 않았다        ✓
maxUnavailable 0  → 3개 아래로 안 내려갔다   ✓
```

**`max-replicas: 4` 어노테이션이 실제로 지켜졌다.**

## 발견 6 — Deployment 는 Pod 를 만들지 않는다

```text
[Deployment 대상 이벤트]
06:08:28   ScalingReplicaSet   Scaled up   654fddd69c  from 0 to 1
06:08:30   ScalingReplicaSet   Scaled down 8588c76444  from 3 to 2
06:08:30   ScalingReplicaSet   Scaled up   654fddd69c  from 1 to 2
06:09:17   ScalingReplicaSet   Scaled down 8588c76444  from 2 to 1
06:09:18   ScalingReplicaSet   Scaled up   654fddd69c  from 2 to 3
06:09:19   ScalingReplicaSet   Scaled down 8588c76444  from 1 to 0
```

**Deployment 가 낸 이벤트는 이 6개가 전부다.** 숫자만 조절한다.

## 발견 7 — 늘리고 → 기다리고 → 줄인다

```text
06:08:28   신 0 → 1              먼저 늘린다
06:08:29   4lf6j Ready
06:08:30   구 3 → 2              그다음 줄인다
```

**새 Pod 가 `Ready` 가 된 뒤에야 옛 Pod 를 죽인다.**

```text
maxUnavailable: 0
  → 새 것을 먼저 확보하고 나서 옛 것을 뺄 수밖에 없다
```

```text
maxSurge 우선        자원을 더 쓰고 가용성을 지킨다   ← 기본값의 선택
maxUnavailable 우선  자원을 아끼고 가용성을 양보한다
```

**`Ready` 판정이 이 전환의 신호등이다.** readiness probe 를 안 쓰면 컨테이너가 시작되자마자 Ready 가 되어, 앱이 아직 초기화 중인데 옛 Pod 를 죽이게 된다.

## 발견 8 — 51초 중 45초가 이미지 받는 시간이었다 ★

```text
15:08:30   신 1개 Ready              2초    worker01. 이미지 있음
15:08:32 ~ 15:09:16                 45초   worker02. 이미지 없음 → pull
15:09:19   신 3개 Ready              2초    worker01. 이미지 있음
```

```text
06:08:33   Pulling image "nginx:1.28"                worker02
06:09:16   Successfully pulled ... in 43.128s
```

**그동안 롤아웃이 멈춰 있었다.** `maxUnavailable: 0` 이라 새 것이 Ready 되기 전에는 옛 것을 못 죽인다.

```text
배포 시간 = 전환 로직(초 단위) + 이미지 확보 시간(수십 초 ~ 수 분)
```

01 문서 실습에서 `nginx:1.28` 을 worker01 에만 받아둔 것이 우연히 대조군이 됐다.

---

# 5. 롤백 (15:21:28 ~ 15:21:33)

```text
root@master01:/# date '+%H:%M:%S'; kubectl rollout undo deployment deploy-demo; date '+%H:%M:%S'
15:21:28
deployment.apps/deploy-demo rolled back
15:21:28
```

## 발견 9 — 새 ReplicaSet 을 만들지 않는다

```text
15:21:28   deploy-demo-8588c76444   0 → 1      있던 것을 다시 늘린다
15:21:29   deploy-demo-654fddd69c   3 → 2
15:21:31   deploy-demo-8588c76444   2 → 3
15:21:33   deploy-demo-654fddd69c   1 → 0
```

**ReplicaSet 은 여전히 두 개다. 세 번째가 안 생겼다.**

```text
template 을 되돌리면 해시가 8588c76444 로 돌아온다
→ 그 이름의 RS 가 이미 있다 → 새로 만들지 않고 숫자만 늘린다
```

**Pod 이름이 증거다.**

```text
[처음]   deploy-demo-8588c76444-m2qxr / mq68r / jhl7r
[롤백]   deploy-demo-8588c76444-khtkm / sl7vr / btm8z
                     ^^^^^^^^^^ 같다      ^^^^^ 새 접미사
```

**백업 데이터가 따로 있는 것이 아니라 옛 ReplicaSet 자체가 백업이다.**

```text
ReplicaSet 오브젝트 안에 template 이 통째로 들어있다
replicas: 0 으로 껐을 뿐 안 지웠다
저장 위치는 etcd 의 /registry/replicasets/k8s-lab/deploy-demo-8588c76444
```

## 발견 10 — revision 번호가 갱신되며 옛 번호가 사라진다 ★★

```text
[롤백 전]                          [롤백 후]
654fddd69c   3   nginx:1.28   2    654fddd69c   0   nginx:1.28   2
8588c76444   0   nginx:1.27   1    8588c76444   3   nginx:1.27   3   ← 1 → 3
```

```text
REVISION  CHANGE-CAUSE          REVISION  CHANGE-CAUSE
1         <none>                2         <none>
2         <none>                3         <none>
                                ^
                                1 이 없어졌다
```

**revision 은 ReplicaSet 의 어노테이션이므로, 같은 RS 를 재사용하며 새 번호를 붙이면 옛 번호가 덮어써진다.**

## 발견 11 — 5초. 배포보다 10배 빠르다

```text
[첫 롤아웃]  51초   worker02 에 nginx:1.28 이 없었다
[롤백]        5초   nginx:1.27 이 양쪽에 다 있다
```

**순수한 전환 로직은 5초면 끝난다.** 나머지 46초는 전부 이미지 확보 시간이었다.

**"롤백이 배포보다 빠르다"** 는 것이 중요하다. 문제가 생겼을 때 되돌리는 건 이미 노드에 있는 이미지를 쓴다.

## 발견 12 — 롤백은 특별한 동작이 아니다

```text
15:21:28   8588 0→1                    늘리고
15:21:29   khtkm Ready → 654 3→2       Ready 되면 줄이고
15:21:31   8588 1→2 → 2→3
15:21:33   654 1→0
```

```text
rollout undo = "옛 template 으로 set image 한 것" 과 같다
→ maxSurge / maxUnavailable 도 그대로 적용된다
```

---

# 6. 실패하는 배포 (15:45:13 ~ 15:49:02)

**`progressDeadlineSeconds` 를 60으로 줄여 관찰 시간을 단축했다.** 기본값은 600초다.

```bash
kubectl patch deployment deploy-demo -p '{"spec":{"progressDeadlineSeconds":60}}'
```

`template` 이 아닌 필드라 ReplicaSet 은 새로 생기지 않는다.

```text
root@master01:/# date '+%H:%M:%S'; kubectl set image deployment/deploy-demo nginx=nginx:9.99-nonexistent
15:45:13
```

## 발견 13 — 구 ReplicaSet 이 한 번도 안 줄었다 ★★

```text
15:44:55   deploy-demo-8588c76444   3   3   3
15:45:13   deploy-demo-d6bc86d6c    1   0   0     ← 새 RS
   ...     (3분 49초 동안 8588c76444 는 출력에 안 나옴)
15:49:02   deploy-demo-8588c76444   3   3   3     ← 롤백 시점. 그대로
```

**`kubectl get rs -w` 에 구 RS 가 중간에 한 번도 안 찍혔다.** 변화가 없었기 때문이다.

```text
잘못된 배포가 기존 서비스를 전혀 건드리지 못했다
```

```text
root@master01:/# kubectl get pods -o wide
deploy-demo-8588c76444-btm8z   1/1   Running            10.244.5.42    worker01
deploy-demo-8588c76444-khtkm   1/1   Running            10.244.5.41    worker01
deploy-demo-8588c76444-sl7vr   1/1   Running            10.244.30.91   worker02
deploy-demo-d6bc86d6c-6twwn    0/1   ImagePullBackOff   10.244.5.43    worker01
```

**maxSurge 1 을 써서 4개가 됐고 거기서 멈췄다.** 3개를 다 만들어보고 다 실패하는 게 아니다.

**성공 경로를 위해 만든 규칙(`maxUnavailable: 0`)이 실패 경로에서 자동으로 방어선이 된다.**

```text
[maxUnavailable: 1 이었다면]
  옛 것 하나를 먼저 죽이고 새 것을 만든다
  → 새 것이 실패하면 2개로 서비스하게 된다
```

## 발견 14 — 두 조건이 서로 다른 것을 말한다 ★

```text
root@master01:/# kubectl get deploy deploy-demo -o jsonpath='{range .status.conditions[*]}...'
Available       True    MinimumReplicasAvailable
Progressing     False   ProgressDeadlineExceeded
```

```text
Available    "지금 서비스가 되고 있나?"   → 예. 3개가 정상 동작 중
Progressing  "배포가 진행되고 있나?"      → 아니오. 60초간 진전 없음
```

```text
Available True  + Progressing True     정상 배포 중
Available True  + Progressing False    배포는 막혔는데 서비스는 살아있다   ← 지금
Available False + Progressing False    서비스까지 죽었다                  ← 진짜 장애
```

```text
root@master01:/# kubectl rollout status deployment deploy-demo --timeout=30s
error: deployment "deploy-demo" exceeded its progress deadline
```

**CI/CD 파이프라인이 이 종료 코드로 배포 실패를 판정한다.** 8단계에서 쓰게 된다.

### 성공했을 때와 대비

정상 롤아웃 뒤의 조건은 이랬다.

```text
Available     True   MinimumReplicasAvailable
  lastTransitionTime  2026-08-13T05:31:32Z    ← 최초 생성 시각 그대로
Progressing   True   NewReplicaSetAvailable
  lastTransitionTime  2026-08-13T05:31:30Z    ← 최초
  lastUpdateTime      2026-08-13T06:09:19Z    ← 롤아웃 완료
```

**`Available` 의 `lastTransitionTime` 이 최초 생성 시각 그대로다.** 롤링 업데이트 내내 `True → False → True` 로 바뀐 적이 없다는 뜻이고, **무중단이었다는 증거가 타임스탬프에 남아 있다.**

```text
lastTransitionTime   status 가 True ↔ False 로 바뀐 시각
lastUpdateTime       내용(reason/message)이 갱신된 시각
```

## 발견 15 — 두 상태를 번갈아 순환한다

```text
15:45:17   ErrImagePull
15:45:28   ImagePullBackOff      11초
15:45:42   ErrImagePull          14초
15:45:57   ImagePullBackOff      15초
15:46:11   ErrImagePull          14초
15:46:26   ImagePullBackOff      15초
15:46:58   ErrImagePull          32초
15:47:12   ImagePullBackOff      14초
15:48:29   ErrImagePull          77초    ← 간격이 벌어진다
15:48:41   ImagePullBackOff      12초
```

```text
ErrImagePull        지금 시도했다가 실패했다
ImagePullBackOff    다음 시도를 기다리는 중
```

**00 문서의 initContainer 실패와 같은 패턴이다.**

```text
[initContainer 실패]   Init:0/1 → Init:Error → Init:CrashLoopBackOff → 반복
[이미지 실패]          ErrImagePull → ImagePullBackOff → 반복
```

**`kubectl get pods` 를 한 번만 치면 둘 중 아무거나 보인다.**

## 발견 16 — 오류 메시지가 원인을 정확히 말한다

```json
{"waiting":{
  "reason":"ErrImagePull",
  "message":"rpc error: code = NotFound desc = failed to pull and unpack image
             \"docker.io/library/nginx:9.99-nonexistent\":
             failed to resolve image: docker.io/library/nginx:9.99-nonexistent: not found"}}
```

```text
1. 어떤 이미지를 받으려 했나    docker.io/library/nginx:9.99-nonexistent
2. 어느 단계에서 실패했나       resolve (태그를 다이제스트로 바꾸는 단계)
3. 왜                          not found
```

```text
resolve 실패    태그 자체가 레지스트리에 없다        ← 지금
pull 실패       태그는 있는데 받다가 끊겼다
unauthorized    있는데 인증이 안 된다 (private 레지스트리)
```

**메시지만 보고 "오타인가 / 네트워크인가 / 권한인가" 를 구분할 수 있다.**

## 발견 17 — 실패한 Pod 도 IP 를 점유한다

```text
deploy-demo-d6bc86d6c-6twwn   0/1   ImagePullBackOff   10.244.5.43   worker01
```

```text
15:45:13   ContainerCreating       ← sandbox 를 먼저 만든다
15:45:14   ContainerCreating       ← IP 할당 (10.244.5.43)
15:45:17   ErrImagePull            ← 그다음 이미지를 받으려다 실패
```

**00 문서에서 확인한 순서 그대로다.**

```text
1. sandbox 생성 → 네트워크 네임스페이스 → IP 할당
2. 이미지 확보     ← 여기서 막혔다
3. 컨테이너 생성
4. 컨테이너 시작
```

**1번은 이미 끝났으므로 IP 를 점유한 채 3분 49초를 버텼다.** 이런 것이 수백 개 쌓이면 IP 가 고갈될 수 있다.

지울 때는 `ContainerStatusUnknown` 이 나왔다. **컨테이너가 한 번도 시작된 적이 없어 종료 상태를 알 수 없다는 뜻이다.**

## 롤백 결과

```text
15:49:02   kubectl rollout undo

NAME                     DESIRED   IMAGE                    REVISION
deploy-demo-654fddd69c   0         nginx:1.28               2
deploy-demo-8588c76444   3         nginx:1.27               5   ← 3 → 5
deploy-demo-d6bc86d6c    0         nginx:9.99-nonexistent   4

REVISION  CHANGE-CAUSE
2         <none>
4         <none>
5         <none>
```

**`8588c76444` 는 애초에 3이었으니 변화가 없다.** 실패한 RS 만 0으로 줄었다. **롤백이라기보다 "잘못 만든 것을 치운 것" 에 가깝다.**

---

# 7. revision 의 성질

## 번호 이력

```text
                                        발급된 번호   그 RS
1. Deployment 생성 (nginx:1.27)             1        8588c76444
2. set image nginx:1.28                     2        654fddd69c
3. rollout undo                             3        8588c76444   ← 1 이 사라짐
4. set image nginx:9.99-nonexistent         4        d6bc86d6c
5. rollout undo                             5        8588c76444   ← 3 이 사라짐

남은 것: 2, 4, 5
```

**`8588c76444` 하나가 `1 → 3 → 5` 세 번호를 거쳤다.**

```text
revision 과 ReplicaSet 은 1:1 이 아니다
  시간을 통틀어 보면   한 RS 가 여러 번호를 거칠 수 있다
  어느 한 순간에는     RS 하나에 번호 하나
```

## 번호는 순서일 뿐 식별자가 아니다

```text
"revision 1 로 돌아가주세요"
  → 이미 없다
  → 그런데 그 내용(nginx:1.27)은 revision 5 로 살아있다
```

**번호로 소통하면 어긋난다.** 내용을 확인하고 지정해야 한다.

```bash
kubectl rollout history deployment deploy-demo --revision=2
kubectl rollout undo deployment deploy-demo --to-revision=2
```

## revisionHistoryLimit

```text
revisionHistoryLimit: 10 (기본값)
  = "replicas 0 으로 내려간 옛 ReplicaSet 을 10개까지 보관한다"
```

```text
11번째 template 이 나오면
  revision 11 이 붙는다            ← 번호는 계속 증가. 1로 안 돌아간다
  옛 RS 가 11개가 된다
  → 가장 오래된 RS 하나를 지운다
  → 그 revision 으로는 롤백할 수 없게 된다
```

**번호는 무한히 증가하고 보관 개수만 제한된다.**

## 그래서 진짜 이력은 Git 에 둔다

```text
revision 은 클러스터 안의 임시 기록이다
  Deployment 를 지우면 함께 사라진다
  클러스터를 새로 만들면 처음부터 1번
  11번을 넘기면 오래된 것부터 사라진다
  번호가 재사용되면 옛 번호가 덮어써진다
```

**`CHANGE-CAUSE` 가 전부 `<none>` 인 것도 같은 맥락이다.** "왜 바꿨는지" 를 클러스터가 알 방법이 없다.

```bash
kubectl annotate deployment deploy-demo \
  kubernetes.io/change-cause="nginx 1.27 → 1.28 보안 패치"
```

**로드맵 9단계 GitOps(Argo CD)가 이 문제를 푸는 단계다.** 07 문서 4라운드에서 "etcd 를 날려도 Git 이 있으면 복구된다" 고 정리한 것과 같은 이야기다.

---

# 8. 곁가지 — 어노테이션과 오브젝트 3부 구조

이 문서에서 어노테이션이 계속 나와 개념을 정리했다.

## 라벨과 어노테이션

| | 라벨 | 어노테이션 |
|---|---|---|
| 목적 | 찾기 위한 것 | 적어두기 위한 것 |
| 셀렉터 | 있다 (`-l`) | 없다 |
| 크기 | 값이 63자 제한 | 훨씬 크게 가능 |
| 바꾸면 | 소유 관계가 바뀐다 | 관계는 안 바뀐다 |

```text
[틀린 구분]  라벨 = 동작에 쓰이는 것 / 어노테이션 = 그냥 기록
[맞는 구분]  라벨 = 검색 가능 / 어노테이션 = 검색 불가
```

**어노테이션도 동작에 쓰인다. 다만 그 이름을 아는 컴포넌트만 읽는다.**

```text
rbac.authorization.kubernetes.io/autoupdate   apiserver 가 재시작 시 복구할지 판단  (07 2라운드)
kubernetes.io/config.mirror                   kubelet 이 미러 Pod 로 취급          (07 3라운드)
deployment.kubernetes.io/revision             rollout undo 가 어디로 갈지 판단      (오늘)
kubectl.kubernetes.io/last-applied-configuration  kubectl apply 가 무엇을 지울지 판단
cni.projectcalico.org/podIP                   Calico 가 기록                       (07 3라운드)
```

## 접두사는 이름 충돌을 막는다

```text
deployment.kubernetes.io/revision
^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^
        접두사(도메인)      이름
```

```text
kubernetes.io / k8s.io   Kubernetes 자체. 예약되어 있다
projectcalico.org        Calico
argoproj.io              Argo CD
```

**같은 규칙이 라벨과 taint 키에도 적용된다.** 08 문서에서 본 `node.kubernetes.io/not-ready` 가 그 예다.

## 모든 오브젝트는 세 부분이다

```yaml
metadata:   # 이 오브젝트가 무엇인가
spec:       # 어떻게 되어야 하는가       ← 사람이 쓴다
status:     # 지금 실제로 어떤가          ← 컨트롤러가 쓴다
```

**`spec` 과 `status` 의 대비가 이 프로젝트 내내 나온 Desired State / Actual State 다.**

```text
[metadata]
  name / uid / generateName / labels / annotations
  ownerReferences / deletionTimestamp / managedFields

[spec]  사람이 쓴다
  replicas / selector / template / strategy
  nodeName(scheduler 가 채운다) / containers / tolerations

[status]  컨트롤러와 kubelet 이 쓴다
  phase / conditions / containerStatuses / lastState
  replicas / readyReplicas / updatedReplicas / observedGeneration
```

**`status` 를 손으로 고쳐도 컨트롤러가 되돌린다.** 01 문서에서 `ownerReferences` 를 직접 고쳐도 소용없다고 한 것과 같다. **파생된 값은 원본을 고쳐야 한다.**

```bash
kubectl explain deployment.spec.strategy.rollingUpdate
```

**`kubectl explain` 으로 각 필드의 설명을 볼 수 있다.** 로드맵 2단계 확인 명령 목록에 있는데 이제야 썼다.

---

# 정리

```text
 1. Deployment 는 ReplicaSet 을 대체하지 않고 여러 개 두고 조율한다
    Pod 를 직접 만들지 않는다. ScalingReplicaSet 이벤트만 낸다

 2. apiserver 는 저장만 한다. 만드는 것은 컨트롤러다
    Deployment Controller → ReplicaSet Controller → Scheduler → kubelet
    각자 자기 앞의 것만 보고 자기 다음 것만 만든다
    07 문서의 system:controller:* 40개 바인딩이 이 사슬이다

 3. UP-TO-DATE(updatedReplicas) 열이 ReplicaSet 과의 차이다
    "template 과 일치하는 Pod 가 몇 개인가"

 4. pod-template-hash 가 세대의 경계를 만든다
    Deployment 는 넓게(app), ReplicaSet 은 좁게(app + hash) 본다
    01 문서 라벨 실험의 문제를 이렇게 피한다

 5. maxSurge 25% → 1(올림) / maxUnavailable 25% → 0(내림)
    계산 결과가 max-replicas 어노테이션에 미리 적혀 있다
    실측에서 개수가 3~4 사이에서만 움직였다

 6. 늘리고 → Ready 대기 → 줄인다
    maxUnavailable: 0 이면 새 것을 확보하고서야 옛 것을 뺄 수 있다
    Ready 판정이 전환의 신호등이다

 7. 51초 중 45초가 이미지 받는 시간이었다
    배포 시간 = 전환 로직 + 이미지 확보 시간

 8. 롤백은 새 RS 를 만드는 게 아니라 옛 RS 를 다시 켜는 것이다
    백업 데이터가 따로 없다. 옛 ReplicaSet 자체가 백업이고 etcd 에 있다
    5초. 배포보다 10배 빠르다

 9. revision 은 ReplicaSet 의 어노테이션이다
    같은 RS 가 재사용되면 새 번호로 덮어써지고 옛 번호가 사라진다
    8588c76444 하나가 1 → 3 → 5 를 거쳤다
    번호는 순서일 뿐 버전 식별자가 아니다
    revisionHistoryLimit 은 번호가 아니라 보관 개수를 제한한다

10. 잘못된 이미지를 배포해도 기존 서비스가 안 죽는다
    구 RS 가 한 번도 안 줄었다
    성공 경로용 규칙(maxUnavailable: 0)이 실패 경로의 방어선이 된다

11. Available 과 Progressing 은 서로 다른 것을 말한다
    Available True + Progressing False = 배포는 막혔는데 서비스는 살아있다
    Available 의 lastTransitionTime 이 무중단의 증거로 남는다

12. 어노테이션은 metadata 의 필드 하나다
    라벨과의 차이는 "셀렉터로 검색 가능한가" 다
    kubernetes.io 접두사는 이름 충돌을 막는 도메인 표기다
```

# 실습 리소스

```text
namespace     k8s-lab       유지
deploy-demo   삭제됨        kubectl delete deployment (cascade 기본값)
              → ReplicaSet 3개와 Pod 3개가 함께 정리됐다
/tmp/deploy-demo.yaml       삭제됨

2026-08-13 기준 k8s-lab 네임스페이스는 비어 있다.
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              1절 — 버전 전환과 되돌리기
2. 생성 시 동작하는 Controller   2절 — Deployment Controller
                                컨트롤러 사슬 전체를 이 절에 정리
3. 주요 Spec 과 Status 필드     spec: replicas/selector/template/strategy/
                                      progressDeadlineSeconds/revisionHistoryLimit
                                status: updatedReplicas/conditions/observedGeneration
4. 다른 오브젝트와의 연결        ReplicaSet(소유), Pod(손자)
5. 장애 사례                    6절 — 존재하지 않는 이미지 배포
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            이미지 사전 배포 / revision 번호로 소통 금지 /
                                CHANGE-CAUSE 안 적으면 이유를 모른다 /
                                Available 과 Progressing 을 같이 봐야 한다
```

# 미확인 목록

```text
1. revisionHistoryLimit 기본값 (spec 에 명시 안 됨. 문서상 10으로 알고 있음)
2. maxUnavailable: 1 로 바꿨을 때 전환 순서가 실제로 뒤집히는지
3. Recreate 전략의 동작 (서비스가 끊기는 것을 실측)
4. progressDeadlineSeconds 를 넘긴 뒤에도 Pod 재시도가 계속되는지
   (00 문서에서 "표시만 바꾼다" 고 정리했으나 이번에 확인 안 함)
5. 옛 RS 를 11개 넘게 만들었을 때 실제로 가장 오래된 것이 지워지는지
6. kubectl.kubernetes.io/last-applied-configuration 의 실제 내용
7. ImagePullBackOff 백오프의 정확한 간격 규칙
   (관측값 11/14/15/32/77초. 규칙성을 확정하지 못함)
```

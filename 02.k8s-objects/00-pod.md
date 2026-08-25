# 00. Pod

2단계 첫 오브젝트. `cloud-native-learning-roadmap.md` 2단계 학습 순서의 첫 항목이다.

## 이 문서의 범위

1단계에서 Pod 를 이미 상당히 봤다. 중복을 피하기 위해 나눈다.

```text
[1단계에서 이미 본 것]
  Static Pod / 미러 Pod / ownerReferences / config.* 어노테이션   07 문서 3라운드
  sandbox 와 컨테이너의 분리, 컨테이너 교체 시 IP·AGE 유지        07 문서 3라운드 실험 3
  Terminating 은 phase 가 아니다                                 08 문서 실험 1
  자동 주입되는 toleration 과 restartPolicy                       08 문서

[이 문서에서 새로 보는 것]
  1. Pod 하나가 만들어지는 전 과정을 이벤트 시각으로 추적          ✅
  2. phase 와 conditions 의 차이                                 ✅
  3. 우리가 안 썼는데 들어가는 값들                               ✅
  4. scheduler 가 노드를 고르는 두 단계                           ✅
  5. Pod IP 는 어디서 오는가                                     ✅
  6. 종료 절차 — SIGTERM → grace period → SIGKILL                ✅
  7. initContainer 의 순서 보장                                  ✅
  8. 컨테이너 여러 개일 때 공유 범위                              ✅
```

**이 문서에서 다루지 않는 것.** Pod 스펙에 있지만 다른 단계에서 확인하는 것이 맞다고 판단한 항목들이다.

```text
resources (requests / limits)   6단계 시나리오 D — OOMKilled
                                지금은 부하를 줄 수단이 없어 값만 넣어봐야 의미가 없다
livenessProbe / readinessProbe  개념은 08 문서에 정리 완료
                                실패 실험은 6단계 시나리오 G
securityContext                 하드닝을 다룰 때
volumes (PV / PVC)              09-pv-pvc.md
nodeSelector / affinity         스케줄링을 깊게 볼 때
```

**5번을 확인하다가 1단계 문서의 오류를 발견해 네 문서를 수정했다.** 그 경위도 여기 남긴다.

---

# 1. 실습 환경 준비 (2026-08-11)

## 네임스페이스 분리

```text
root@master01:/# kubectl create namespace k8s-lab
kubectl config set-context --current --namespace=k8s-lab
kubectl config view --minify | grep namespace

namespace/k8s-lab created
Context "kubernetes-admin@kubernetes" modified.
    namespace: k8s-lab
```

실습 리소스를 `default`에 만들면 무엇이 실습용인지 구분이 안 된다. 네임스페이스를 분리하면 정리할 때 통째로 지울 수 있다.

`set-context --current --namespace`는 **기본 네임스페이스를 바꾼다.** 이후 `kubectl get pods`가 자동으로 `k8s-lab`을 본다.

```text
root@master01:/# kubectl config view --minify
contexts:
- context:
    cluster: kubernetes
    namespace: k8s-lab          ← 여기가 바뀐 것
    user: kubernetes-admin
  name: kubernetes-admin@kubernetes
```

**`kube-system`을 볼 때는 `-n kube-system`을 명시해야 한다.** 안 그러면 `k8s-lab`을 본다.

```text
root@master01:/# kubectl get namespace
NAME              STATUS   AGE
default           Active   7d18h
k8s-lab           Active   72s
kube-node-lease   Active   7d18h
kube-public       Active   7d18h
kube-system       Active   7d18h
```

## 발견 1 — Node는 네임스페이스가 없다

네임스페이스를 바꿔가며 조회해봤다.

```text
root@master01:/# kubectl get -n kube-public nodes
root@master01:/# kubectl get -n kube-system nodes
root@master01:/# kubectl get -n k8s-lab     nodes

(세 번 모두 동일)
NAME       STATUS   ROLES           AGE     VERSION
master01   Ready    control-plane   7d18h   v1.35.7
worker01   Ready    <none>          7d18h   v1.35.7
worker02   Ready    <none>          7d18h   v1.35.7
```

**`-n`을 붙여도 결과가 같다.** 오류도 안 난다. 리소스가 두 종류이기 때문이다.

```text
[네임스페이스 소속]              [클러스터 전역]
Pod, Service, ConfigMap          Node
Secret, Deployment               Namespace 자신
ReplicaSet, Job, PVC             PersistentVolume
ServiceAccount, Role             ClusterRole, ClusterRoleBinding
                                 StorageClass, CRD
```

`Node`는 클러스터 전역 리소스라 네임스페이스 개념이 없고, `-n`은 **무시된다.**

07 문서 4라운드의 etcd 키 구조가 이를 그대로 드러낸다.

```text
/registry/pods/k8s-lab/pod-basic
                ^^^^^^^ 네임스페이스가 경로에 들어간다

/registry/minions/worker01
                  ^^^^^^^^ 네임스페이스 자리가 없다
```

**저장 구조부터 다르다.** 상세는 `07-namespace.md`에서 다룬다.

> **미확인**: `kubectl api-resources --namespaced=false`로 전체 목록을 확인하지 않았다.

---

# 2. Pod 생성

```text
root@master01:/# kubectl get pods
No resources found in k8s-lab namespace.

root@master01:/# kubectl run pod-basic --image=nginx
pod/pod-basic created

root@master01:/# kubectl get pod pod-basic -o wide
NAME        READY   STATUS    RESTARTS   AGE   IP            NODE       NOMINATED NODE   READINESS GATES
pod-basic   1/1     Running   0          40s   10.244.5.27   worker01   <none>           <none>
```

Deployment도 ReplicaSet도 없는 **맨 Pod 하나**다. 컨트롤러가 관여하지 않으므로 `scheduler → kubelet → containerd` 경로만 보게 된다.

## 겪은 문제 — 이벤트 감시 명령이 전부 `<none>`

먼저 이렇게 시도했다가 실패했다.

```text
root@master01:/# kubectl get events -w --output-watch-events \
  -o custom-columns='TIME:.lastTimestamp,TYPE:.type,REASON:.reason,OBJ:.involvedObject.name,MSG:.message'

TIME     TYPE    REASON   OBJ      MSG
<none>   ADDED   <none>   <none>   <none>
<none>   ADDED   <none>   <none>   <none>
```

**원인**: `--output-watch-events`를 붙이면 출력이 Event 객체가 아니라 `WatchEvent`로 한 겹 감싸진다.

```text
[없을 때]                        [있을 때]
{                                {
  "lastTimestamp": "...",          "type": "ADDED",
  "type": "Normal",                "object": {
  "reason": "Scheduled"              "lastTimestamp": "...",
}                                    "type": "Normal", ...
                                   }
                                 }
```

경로가 `.object.lastTimestamp`여야 했다. **`TYPE` 열만 값이 나온 것이 증거다** — `ADDED`는 Event의 type(`Normal`/`Warning`)이 아니라 WatchEvent의 type이다. 우연히 경로가 존재해서 찍혔다.

**해결**: `--output-watch-events`를 뺀다. 추가/수정/삭제를 구분할 이유가 없었다.

```bash
kubectl get events -w \
  -o custom-columns='TIME:.lastTimestamp,TYPE:.type,REASON:.reason,OBJ:.involvedObject.name,MSG:.message'
```

## 이벤트 타임라인

```text
root@master01:/# kubectl get events --sort-by=.metadata.creationTimestamp
LAST SEEN   TYPE     REASON      OBJECT          MESSAGE
3m41s       Normal   Scheduled   pod/pod-basic   Successfully assigned k8s-lab/pod-basic to worker01
3m40s       Normal   Pulling     pod/pod-basic   Pulling image "nginx"
3m39s       Normal   Pulled      pod/pod-basic   Successfully pulled image "nginx" in 1.622s (1.622s including waiting). Image size: 63135215 bytes.
3m39s       Normal   Created     pod/pod-basic   Container created
3m38s       Normal   Started     pod/pod-basic   Container started
```

```text
root@master01:/# kubectl get events -w \
  -o custom-columns='TIME:.lastTimestamp,TYPE:.type,REASON:.reason,OBJ:.involvedObject.name,MSG:.message'
TIME                   TYPE     REASON      OBJ         MSG
<nil>                  Normal   Scheduled   pod-basic   Successfully assigned k8s-lab/pod-basic to worker01
2026-08-11T02:34:38Z   Normal   Pulling     pod-basic   Pulling image "nginx"
2026-08-11T02:34:39Z   Normal   Pulled      pod-basic   Successfully pulled image "nginx" in 1.622s
2026-08-11T02:34:39Z   Normal   Created     pod-basic   Container created
2026-08-11T02:34:40Z   Normal   Started     pod-basic   Container started
```

## 발견 2 — `Scheduled` 이벤트만 `lastTimestamp`가 없다

```text
<nil>                  Scheduled   ← 이것만
2026-08-11T02:34:38Z   Pulling
2026-08-11T02:34:39Z   Pulled
2026-08-11T02:34:39Z   Created
2026-08-11T02:34:40Z   Started
```

**이벤트를 만든 주체가 다르다.**

```text
Scheduled                              kube-scheduler 가 만든 이벤트
Pulling / Pulled / Created / Started   kubelet 이 만든 이벤트
```

Event API가 두 벌 있다.

```text
[구버전]  core/v1 Event              firstTimestamp / lastTimestamp / count / source
[신버전]  events.k8s.io/v1 Event     eventTime / series / reportingController
```

scheduler는 신버전으로 옮겨갔고 그쪽에는 `lastTimestamp`가 없다. `kubectl get events` 기본 출력이 `3m41s`로 멀쩡했던 이유는 **kubectl이 `lastTimestamp`가 없으면 `eventTime`을 대신 보기 때문**이다.

**운영 시사점**: 이벤트를 시각으로 정렬하려면 두 필드를 다 봐야 한다. 한쪽만 보면 특정 컴포넌트의 이벤트가 통째로 누락된다. 08 문서의 "타임스탬프가 없다" 문제와 같은 계열이다.

> **미확인**: `kubectl get events -o custom-columns=...,SRC:.source.component,REPORTING:.reportingComponent,EVENTTIME:.eventTime,LASTTS:.lastTimestamp`로 두 주체를 직접 대조하지 않았다.

---

# 3. phase와 conditions

```text
root@master01:/# kubectl get pod pod-basic -o jsonpath='{.status.phase}{"\n"}'
Running
```

`kubectl get pods`의 `STATUS` 열이 이 값이다. 값은 다섯 개뿐이다.

```text
Pending / Running / Succeeded / Failed / Unknown
```

**`Running`은 "잘 동작 중"이 아니라 "컨테이너가 하나라도 돌고 있다"이다.**

```text
root@master01:/# kubectl get pod pod-basic \
  -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.lastTransitionTime}{"\n"}{end}'
PodReadyToStartContainers       True    2026-08-11T02:34:40Z
Initialized                     True    2026-08-11T02:34:37Z
Ready                           True    2026-08-11T02:34:40Z
ContainersReady                 True    2026-08-11T02:34:40Z
PodScheduled                    True    2026-08-11T02:34:37Z
```

## 발견 3 — 배열 순서는 시간 순서가 아니다

```text
[출력 순서]                              [실제 시간 순서]
PodReadyToStartContainers  02:34:40      PodScheduled               02:34:37
Initialized                02:34:37      Initialized                02:34:37
Ready                      02:34:40         ↓
ContainersReady            02:34:40      PodReadyToStartContainers  02:34:40
PodScheduled               02:34:37      ContainersReady            02:34:40
                                         Ready                      02:34:40
```

**`PodScheduled`가 맨 마지막에 찍혔지만 실제로는 가장 먼저 일어났다.** `lastTransitionTime`을 봐야 순서를 안다. 08 문서에서 Node conditions를 볼 때 쓴 습관과 같다.

## 발견 4 — `Initialized`가 `PodScheduled`와 동시다

```text
PodScheduled   True   02:34:37
Initialized    True   02:34:37     ← 같은 초
```

`Initialized`는 "모든 initContainer가 성공적으로 끝났다"는 뜻이다. **initContainer가 없으면 끝낼 게 없어 즉시 True가 된다.**

**"조건이 True다"가 "무언가를 했다"를 뜻하지 않는다.** initContainer를 붙이면 이 값이 뒤로 밀린다 — 6번 항목에서 확인 예정.

## 전체 타임라인 (이벤트 + conditions)

```text
02:34:37   PodScheduled = True        scheduler 가 worker01 선택
02:34:37   Initialized  = True        initContainer 없음
02:34:38   Pulling                    이미지 수신 시작
02:34:39   Pulled                     1.622초, 63,135,215 bytes
02:34:39   Created                    컨테이너 생성
02:34:40   Started                    컨테이너 시작
02:34:40   PodReadyToStartContainers = True
02:34:40   ContainersReady = True
02:34:40   Ready = True
```

**요청부터 Ready까지 3초.**

> **미확인**: `PodReadyToStartContainers`는 sandbox 생성과 네트워크 구성 완료를 뜻하는 조건인데, 타임스탬프가 `Started`와 같은 `02:34:40`이다. sandbox는 이미지 pull보다 먼저 만들어지므로 더 이른 시각을 예상했다. status 업데이트가 묶여서 반영된 것으로 보이나 확인하지 못했다.

---

# 4. 이미지를 다시 받았다

```text
02:34:38   Pulling image "nginx"
02:34:39   Successfully pulled image "nginx" in 1.622s
```

worker01에는 08 문서 실험 1·2에서 `nginx-test`를 띄웠으므로 이미지가 이미 있었을 가능성이 높다. 그런데 `Pulling`이 찍혔다.

**원인**: 태그를 안 썼다.

```text
root@master01:/# kubectl get pod pod-basic \
  -o jsonpath='{.spec.containers[0].image}{"\t"}{.spec.containers[0].imagePullPolicy}{"\n"}'
nginx   Always
```

```text
kubectl run pod-basic --image=nginx
                              ^^^^^ nginx:latest 로 해석된다
```

```text
imagePullPolicy 기본값 규칙
  태그가 :latest 이거나 없다   →  Always
  그 외 태그 (:1.27 등)        →  IfNotPresent
```

**우리가 쓰지 않았는데 `Always`가 들어 있다.** `restartPolicy: Always`, `tolerationSeconds: 300`과 같은 자동 주입이다.

**운영 시사점**

```text
:latest 를 쓰면
  1. 레지스트리가 죽으면 Pod 가 못 뜬다 — 노드에 이미지가 있어도
  2. 같은 manifest 인데 시점에 따라 다른 버전이 뜬다
  3. 롤백 대상이 특정되지 않는다
     → "선언이 같으면 결과도 같다" 는 전제가 깨진다
```

8단계 이미지 태그 정책, 6단계 시나리오 F(존재하지 않는 이미지 배포)와 이어진다.

> **미확인 (중요)**: 아래 둘을 실행하지 않았다. 위 서술은 규칙에서 추론한 것이다.
> ```bash
> kubectl get pod pod-basic -o jsonpath='{.spec.containers[0].imagePullPolicy}'   # Always 예상
> sudo crictl images | grep nginx    # worker01 에서. 이미지가 이미 있었는지
> ```

---

# 5. scheduler가 worker01을 고른 이유

```text
root@master01:/# kubectl get nodes -o custom-columns='NODE:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory,PODS:.status.allocatable.pods'
NODE       CPU   MEM         PODS
master01   2     3858480Ki   110
worker01   2     3858488Ki   110
worker02   2     3858464Ki   110
```

스펙은 사실상 동일하다.

```text
root@master01:/# kubectl describe node worker01 | grep -A6 'Allocated resources'
Allocated resources:
  Resource           Requests    Limits
  cpu                250m (12%)  0 (0%)
  memory             0 (0%)      0 (0%)
  ephemeral-storage  0 (0%)      0 (0%)

root@master01:/# kubectl describe node worker02 | grep -A6 'Allocated resources'
Allocated resources:
  Resource           Requests    Limits
  cpu                350m (17%)  0 (0%)
  memory             70Mi (1%)   170Mi (4%)
  ephemeral-storage  0 (0%)      0 (0%)
```

## 발견 5 — filtering과 scoring은 다른 단계다

```text
[1단계 filtering — 이진 판단]
  master01   taint node-role.kubernetes.io/control-plane:NoSchedule
             pod-basic 에 toleration 없음 → 탈락
  worker01   통과
  worker02   통과

[2단계 scoring — 점수 비교]
  기본 전략 LeastAllocated — 덜 쓰는 노드에 높은 점수
  worker01  250m → 높은 점수
  worker02  350m → 낮은 점수
             ↓
  worker01 선택
```

**08 문서에서 본 taint가 여기서 filtering으로 동작한다.** "master에는 Pod가 안 뜬다"의 실체가 1단계다.

worker02가 100m CPU와 70Mi 메모리를 더 쓰는 이유를 확인했다.

```text
root@master01:/# kubectl get pods -A -o wide --field-selector spec.nodeName=worker02
NAMESPACE     NAME                                       READY  STATUS   RESTARTS      AGE    IP              NODE
kube-system   calico-kube-controllers-687fc57cb8-lkmrs   1/1    Running  12 (24h ago)  26h    10.244.30.84    worker02
kube-system   calico-node-flq4d                          1/1    Running  1 (7d1h ago)  7d4h   192.168.8.141   worker02
kube-system   coredns-7d764666f9-899zs                   1/1    Running  0             26h    10.244.30.85    worker02
kube-system   kube-proxy-nbt49                           1/1    Running  1 (7d1h ago)  7d20h  192.168.8.141   worker02
```

```text
worker01   calico-node 250m                   = 250m
worker02   calico-node 250m + coredns 100m    = 350m
           coredns memory requests 70Mi / limits 170Mi
           → describe 출력의 70Mi(1%) / 170Mi(4%) 와 정확히 일치한다
           calico-kube-controllers 는 requests 가 없다
```

**CoreDNS replica 하나가 worker02에 있어서 생긴 차이다.**

곁가지로 두 가지가 더 보인다.

```text
1. calico-node 와 kube-proxy 의 IP 가 192.168.8.141 — 노드 IP 다
   hostNetwork: true 이기 때문 (06 문서에서 확인한 내용)

2. calico-kube-controllers 의 RESTARTS 가 12
   08 문서 실험 3(apiserver 중단) 당시 7이었다
   그 뒤로도 재시작이 이어졌다는 뜻이다 — 원인 미확인
```

> **미확인**: `ImageLocality`(이미 이미지를 가진 노드에 가산점) 항목도 작용했을 수 있다. 두 요인 중 어느 쪽이 결정적이었는지 이 출력만으로는 구분되지 않는다.

---

# 6. Pod IP는 어디서 오는가 ★

이 절이 이 문서에서 가장 중요하다. **1단계 문서 네 개를 수정하게 된 발견이다.**

## 세 대역

```text
192.168.8.0/24     노드 IP        VMware 에서 고정
10.244.0.0/16      Pod IP         Calico
10.96.0.0/12       Service IP     가상 대역
```

## 첫 번째 답 — node.spec.podCIDR

```text
root@master01:/# kubectl get node worker01 -o jsonpath='{.spec.podCIDR}{"\n"}'
kubectl get node worker02 -o jsonpath='{.spec.podCIDR}{"\n"}'
10.244.1.0/24
10.244.2.0/24
```

`/16`을 `/24`씩 잘라 노드마다 배정한 것처럼 보인다. `kubeadm init --pod-network-cidr=10.244.0.0/16`이 controller-manager에 `--cluster-cidr` / `--allocate-node-cidrs=true`로 전달된 결과다.

## 발견 6 — Pod IP가 그 노드의 podCIDR 밖이다 ★★★

```text
worker01 의 podCIDR       10.244.1.0/24
worker01 에 뜬 Pod 의 IP   10.244.5.27      ← 대역 밖
```

07 문서 3라운드에 기록된 출력에도 같은 증거가 있었다.

```text
coredns-...-gv4wl   10.244.5.6     worker01   podCIDR 10.244.1.0/24   ✗
coredns-...-jhlw8   10.244.30.71   worker02   podCIDR 10.244.2.0/24   ✗
```

**`10.244.30.71`은 `/24`로는 나올 수 없는 값**인데, 당시에는 이상하게 보지 않고 지나갔다.

```text
podCIDR 이 실제로 쓰인다면 Pod IP 가 그 안에 있어야 한다
→ 없다
→ podCIDR 은 쓰이지 않는다
```

## 발견 7 — Calico는 자체 IPAM을 쓴다

```text
root@worker01:/# sudo cat /etc/cni/net.d/10-calico.conflist | grep -A4 '"ipam"'
      "ipam": {
          "type": "calico-ipam"
      },
      "policy": {
          "type": "k8s"
```

CNI 표준은 **배선**과 **주소 배분**을 별개 플러그인으로 나눈다.

```text
배선   calico
주소   calico-ipam      ← host-local 로 바꾸면 podCIDR 을 읽게 된다
정책   k8s
```

```text
host-local      node.spec.podCIDR 을 읽는다
calico-ipam     IPPool 에서 블록을 떼어 쓴다. podCIDR 무시   ← 우리 것
AWS VPC CNI     노드 ENI 의 VPC IP 를 준다                  (10단계)
```

## 발견 8 — Calico의 답은 blockaffinities에 있다

```text
root@master01:/# kubectl get blockaffinities.crd.projectcalico.org \
  -o custom-columns='NODE:.spec.node,BLOCK:.spec.cidr,STATE:.spec.state'
NODE       BLOCK              STATE
master01   10.244.241.64/26   confirmed
worker01   10.244.5.0/26      confirmed
worker02   10.244.30.64/26    confirmed
```

**실제 IP와 맞아떨어진다.**

```text
worker01   10.244.5.0/26     (범위 .0 ~ .63)
             10.244.5.6     coredns (07 문서 3라운드)         ✓
             10.244.5.27    pod-basic                        ✓

worker02   10.244.30.64/26   (범위 .64 ~ .127)
             10.244.30.71   coredns (07 문서 3라운드)         ✓
             10.244.30.84   calico-kube-controllers          ✓
             10.244.30.85   coredns (현재)                   ✓
```

**두 노드, 다섯 개 Pod 가 전부 블록 안에 들어간다.** 반대로 `node.spec.podCIDR`(`10.244.1.0/24`, `10.244.2.0/24`) 안에 들어가는 것은 하나도 없다.

```text
같은 /16 을 두 주체가 각자 나눈다

  controller-manager   /24 씩  →  node.spec.podCIDR    아무도 안 읽는다
  Calico               /26 씩  →  blockaffinities      실제로 쓰인다
```

블록 번호가 `5`, `30`, `241`로 순차적이지 않다. **Calico는 블록을 무작위 위치에서 고른다.** 노드가 동시에 블록을 요청할 때의 경합을 줄이기 위한 것으로 보인다.

## 왜 블록을 거치는가

```text
[블록이 없다면]
  Pod 를 만들 때마다 "이 주소 써도 되나" 를 클러스터 전체에 조회·기록
  → Pod 하나당 전역 조율

[블록이 있으면]
  블록 확보 시 1회만 전역 조율 (blockaffinities, STATE: confirmed)
  이후 64개까지는 노드가 혼자 배분
  → 조율 비용 1/64
```

**08 문서의 Lease와 같은 발상이다.** "전역 조율은 비싸니 횟수를 줄인다."

노드의 `allocatable pods`가 110이므로, 꽉 채우면 블록이 2개가 된다.

## podCIDR은 왜 만들어지는가

```text
kubeadm init 시점에는 어떤 CNI 를 설치할지 알 수 없다
→ CNI 는 init 이후에 설치된다
→ podCIDR 을 읽는 CNI 를 위해 일단 준비해둔다
```

```text
읽는 것     kubenet, Flannel 일부 모드,
           Cilium 의 Kubernetes Host Scope 모드,
           Calico 를 host-local + usePodCidr 로 설정한 경우
읽지 않는 것 Calico 기본값(calico-ipam), AWS VPC CNI 등
```

없는데 필요하면 그 CNI가 아예 동작하지 않고, 있는데 안 쓰면 대체로 무해하다. 그래서 `--allocate-node-cidrs=true`가 기본값이다.

다만 완전히 무해하지는 않다. Calico 공식 문서는 이 값을 **"unused node CIDRs"** 라고 부르며 `--allocate-node-cidrs=false`를 권한다.

```text
/16 을 /24 로 자르면 256 개
→ 노드 257 대째부터 자를 조각이 없다
→ CIDRNotAvailable 로 노드 등록이 막힌다
→ Calico 는 그 값을 쓰지도 않는데 실패한다
```

노드 3대인 이 클러스터에서는 기본값을 유지한다.

**출처**
```text
https://docs.tigera.io/calico/latest/networking/ipam/get-started-ip-addresses
https://docs.cilium.io/en/stable/network/concepts/ipam/kubernetes/
```

## 이 발견으로 수정한 1단계 문서

| 문서 | 원래 서술 | 수정 |
|---|---|---|
| `00-environment.md` | "반드시 일치해야 한다 / 불일치하면 Pod가 IP를 못 받는다" | Calico는 podCIDR을 안 읽는다는 사실 + 맞추는 진짜 이유 2개 + "podCIDR은 왜 만드나" 절 추가 |
| `06-cni-calico.md:142` | 같은 오해 | 수정 노트 추가 |
| `07-control-plane-analysis.md:1359` | `10.244.5.6 (Pod CIDR)` | `(Calico IPAM 블록)` + 당시 놓친 `10.244.30.71` 증거 기록 |
| `README.md` / `AGENTS.md` | 존재하지 않는 `blog/` 참조 | 실재 원고 목록으로 교체 |

**틀린 이유**: 두 개의 다른 위험을 하나로 뭉쳤다.

```text
[진짜 위험]  Calico 풀이 노드 IP 대역과 겹친다
            192.168.0.0/16 이 위험한 이유는 이것이다
[가짜 위험]  Calico 풀이 node.spec.podCIDR 과 다르다
            이건 문제가 아니다
```

**맞춰야 하는 것은 바깥쪽 `/16`이고, 안쪽 분할 방식은 무관하다.** kube-proxy가 `clusterCIDR`로 "Pod 대역인지 외부인지"를 판단하므로, Calico 풀이 그 안에 들어가기만 하면 된다.

## 이름은 넷인데 값은 하나다 — 실측

```text
root@master01:/# sudo grep -E 'cluster-cidr|allocate-node-cidrs|node-cidr-mask-size' \
  /etc/kubernetes/manifests/kube-controller-manager.yaml
    - --allocate-node-cidrs=true
    - --cluster-cidr=10.244.0.0/16

root@master01:/# kubectl -n kube-system get cm kube-proxy -o yaml | grep -i clusterCIDR
    clusterCIDR: 10.244.0.0/16

root@master01:/# kubectl get ippools.crd.projectcalico.org \
  -o custom-columns='CIDR:.spec.cidr,BLOCKSIZE:.spec.blockSize'
CIDR            BLOCKSIZE
10.244.0.0/16   26
```

| 어디서 | 뭐라고 부르나 | 값 |
|---|---|---|
| `kubeadm init` | `--pod-network-cidr` | `10.244.0.0/16` |
| controller-manager | `--cluster-cidr` | `10.244.0.0/16` |
| kube-proxy | `clusterCIDR` | `10.244.0.0/16` |
| Calico | IPPool `cidr` | `10.244.0.0/16` |

**이름이 넷인데 값은 하나다.** `kubeadm`에 준 값이 controller-manager와 kube-proxy로 이름을 바꿔 전달되고, Calico에는 우리가 같은 값을 따로 써줬다.

`--node-cidr-mask-size`는 출력에 없다. **기본값 `24`를 쓴다는 뜻이고, 그것이 `podCIDR`이 `/24`인 이유다.** 그리고 IPPool의 `blockSize`가 `26`이므로 Calico는 같은 `/16`을 `/26`으로 자른다.

```text
같은 10.244.0.0/16 을
  controller-manager 는 /24 로 자르고   (256 조각)  → 안 쓰임
  Calico 는            /26 으로 자른다  (1024 조각) → 실제
```

> **미확인**
> ```bash
> ip route | grep 10.244        # 노드 라우팅에 /26 단위 경로가 보이는지
> kubectl get ipamblocks.crd.projectcalico.org \
>   -o custom-columns='BLOCK:.spec.cidr,UNALLOC:.spec.unallocated'   # 블록 내 빈자리
> ```

---

---

# 7. 종료 절차 — SIGTERM → grace period → SIGKILL

08 문서 실험 1에서 `deletionTimestamp`가 찍히고 Pod가 사라지는 것은 봤지만, **그 사이에 무슨 일이 일어나는지는 확인하지 않았다.** 여기서 확인한다.

## 먼저 값 확인

```text
root@master01:/# kubectl get pod pod-basic -o jsonpath='{.spec.terminationGracePeriodSeconds}{"\n"}'
30

root@master01:/# sudo grep -i 'event-ttl' /etc/kubernetes/manifests/kube-apiserver.yaml
(출력 없음 — 기본값 사용)
```

`30`도 우리가 쓴 적이 없다. `restartPolicy: Always`, `imagePullPolicy: Always`, `tolerationSeconds: 300`에 이은 **네 번째 자동 주입 값**이다.

`--event-ttl`이 없는 것으로 07 문서 4라운드의 미확인 항목이 닫힌다. 실제로 이 실험 도중 `kubectl get events`에서 pod-basic 생성 이벤트가 사라진 것을 확인했다. Pod는 그대로인데 이벤트만 없어졌다.

## 실험 설계 — 두 번 대조한다

한 번만 해서는 `30`의 의미를 볼 수 없다. **SIGTERM에 즉시 반응하는 것**과 **무시하는 것**을 대조한다.

```text
[실험 A] pod-basic      nginx. SIGTERM 을 받으면 바로 종료
[실험 B] pod-stubborn   sh -c 'trap "" TERM; sleep 3600'. SIGTERM 을 무시
```

관측은 세 곳에서 한다.

```text
터미널 A [master01]  kubectl get pod -w   PHASE 와 deletionTimestamp
터미널 B [master01]  kubectl get events -w
터미널 C [worker01]  프로세스와 컨테이너 수를 1초 간격으로
```

**터미널 C가 핵심이다.** A·B는 apiserver가 아는 것만 보여준다. 신호가 실제로 언제 전달되고 프로세스가 언제 죽는지는 노드 안에서만 보인다.

## 실험 A — nginx (2026-08-11 14:16)

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete pod pod-basic
14:16:34
pod "pod-basic" deleted from k8s-lab namespace
```

```text
[터미널 B — 이벤트]
2026-08-11T05:16:34Z   Normal   Killing   pod-basic   Stopping container pod-basic

[터미널 A — Pod 상태]
14:16:34 pod-basic   1/1   Terminating   ...
14:16:34 pod-basic   0/1   Completed     ...
14:16:35 pod-basic   0/1   Completed     ...

[터미널 C — worker01]
14:16:33 nginx프로세스=1
14:16:34 nginx프로세스=0        ← 같은 초에 죽었다
```

```text
T0        14:16:34   delete 요청
T0 + 0초             Killing 이벤트 / Terminating
T0 + 0초             nginx 프로세스 소멸
T0 + 1초  14:16:35   Pod 소멸
```

**30초 중 1초도 안 썼다.** `kubectl delete`도 매달리지 않았다.

### 겪은 문제 1 — 컨테이너 이름이 `nginx`가 아니었다

터미널 C에서 `sudo crictl ps --name nginx` 로 세었더니 **삭제 전부터 0**이었다.

```text
14:16:19 nginx프로세스=1 컨테이너=0     ← Pod 가 살아있는데 0
```

답은 이벤트에 있었다.

```text
Killing   pod-basic   Stopping container pod-basic
                                         ^^^^^^^^^
```

```text
kubectl run pod-basic --image=nginx
            ^^^^^^^^^         ^^^^^
            Pod 이름          이미지
                ↓
Pod 이름        pod-basic
컨테이너 이름    pod-basic      ← Pod 이름과 같다. 이미지 이름과 무관
이미지          nginx
프로세스        nginx: master   ← 이미지가 실행하는 것
```

**세 층의 이름이 각각 다르다.** `crictl`로 찾을 때 헷갈리면 "컨테이너가 없다"고 오진한다.

### 겪은 문제 2 — `0`이 두 줄로 찍혔다

```text
14:16:34 nginx프로세스=0
0 컨테이너=0
```

```bash
pgrep -cf 'nginx: master' || echo 0
```

`pgrep -c`는 못 찾으면 **`0`을 출력하면서 종료 코드는 1**을 낸다. `|| echo 0`이 또 실행돼 두 번 찍혔다. `|| echo 0`을 빼면 된다.

## 실험 B — SIGTERM을 무시하는 컨테이너 (14:24)

```bash
kubectl run pod-stubborn --image=nginx --command -- sh -c 'trap "" TERM; sleep 3600'
```

```text
trap "" TERM     SIGTERM 을 받아도 무시하라
sleep 3600       1시간 동안 아무것도 안 한다
```

### 발견 9 — 컨테이너 안에 프로세스가 둘이다

```text
[worker01]
14:22:05 sleep프로세스=2 컨테이너=1
```

`pgrep -f`가 2를 센 이유는 명령줄 전체에서 찾기 때문이다.

```text
[PID 1]  sh -c trap "" TERM; sleep 3600     ← 이 문자열에 'sleep 3600' 이 있다
           └── [자식] sleep 3600
```

**이 구조가 실험의 핵심이다.**

```text
kubelet → containerd → 컨테이너의 PID 1 에게만 SIGTERM 을 보낸다
자식 프로세스에는 안 보낸다
```

```text
SIGTERM  →  PID 1 (sh)     trap "" TERM 이라 무시
            자식 (sleep)    애초에 신호를 못 받음
                ↓
        아무도 안 죽는다 → 30초 대기 → SIGKILL
```

**이것이 실무의 "PID 1 문제"다.**

```text
[흔한 Dockerfile 실수]
  ENTRYPOINT ["sh", "-c", "python app.py"]
  → PID 1 = sh, 앱은 자식
  → SIGTERM 이 앱에 안 간다
  → 정리 작업(연결 종료, 진행 중 요청 마무리)을 못 하고 30초 뒤 강제 종료

[올바른 방법]
  ENTRYPOINT ["python", "app.py"]      앱이 직접 PID 1 이 된다
  또는  sh -c 'exec python app.py'     exec 로 sh 를 대체한다
```

3단계 애플리케이션 개발에서 다시 다룬다.

### 결과

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete pod pod-stubborn; date '+%H:%M:%S'
14:24:21
pod "pod-stubborn" deleted from k8s-lab namespace
14:24:53
```

```text
[터미널 A — PHASE 와 deletionTimestamp]
14:21:28 pod-stubborn   Running   <none>
14:24:21 pod-stubborn   Running   2026-08-11T05:24:51Z
14:24:52 pod-stubborn   Running   2026-08-11T05:24:51Z
14:24:52 pod-stubborn   Failed    2026-08-11T05:24:51Z
14:24:53 pod-stubborn   Failed    2026-08-11T05:24:53Z

[터미널 B — 이벤트]
2026-08-11T05:24:21Z   Normal   Killing   pod-stubborn   Stopping container pod-stubborn

[터미널 C — worker01]
14:24:35 sh=1 sleep=1 컨테이너=1
   ...   (30초 내내 동일)
14:24:50 sh=1 sleep=1 컨테이너=1
14:24:52 sh=0 sleep=0 컨테이너=0
```

```text
T0        14:24:21   delete 요청 / Killing 이벤트 / SIGTERM 전송
T0 ~ +30초           sh, sleep 둘 다 살아있음. PHASE = Running
T0 + 31초 14:24:52   SIGKILL → 둘 다 소멸. PHASE = Failed
T0 + 32초 14:24:53   Pod 소멸. kubectl delete 반환
```

**30초를 꽉 채웠다.**

## 발견 10 — `deletionTimestamp`는 삭제 시각이 아니라 마감 시각이다 ★★★

```text
delete 요청        14:24:21
deletionTimestamp  14:24:51      ← 30초 뒤. 미래 시각이다
```

```text
deletionTimestamp = 요청 시각 + terminationGracePeriodSeconds
                  = "이 시각까지 정리하고 사라져라" 는 데드라인
```

**이름이 오해를 부른다.** "삭제된 시각"처럼 읽히지만 "삭제되어야 하는 시각"이다.

실제로 죽고 나자 값이 갱신됐다.

```text
14:24:21   deletionTimestamp = 05:24:51Z    데드라인
14:24:53   deletionTimestamp = 05:24:53Z    실제 종료 확인 후
```

### 이것이 08 문서의 미해결 문제를 푼다

08 문서 실험 1에 이렇게 기록하고 원인을 못 밝혔다.

```text
09:28:19   Node NotReady 판정 + taint
09:33:19   TaintManagerEviction
09:33:50   deletionTimestamp 값 (2026-08-10T00:33:50Z)

미확인: tolerationSeconds: 300 을 넘는 31초의 원인
```

```text
09:28:19  + 300초 (tolerationSeconds)  =  09:33:19   축출 판단. 정확히 300초
09:33:19  +  30초 (gracePeriod)        =  09:33:49   deletionTimestamp
                                          09:33:50   실제 기록 (1초 오차)
```

**서로 다른 두 타이머가 겹쳐 있었다.**

```text
tolerationSeconds: 300   "노드가 안 보여도 5분은 기다린다"   ← 축출 판단
gracePeriod: 30          "죽으라고 한 뒤 30초는 봐준다"      ← 종료 절차
```

08 문서의 해당 항목을 수정했다.

## 발견 11 — `Failed`와 `Completed`가 갈린다

```text
[실험 A] SIGTERM 으로 스스로 종료  exit 0     → Completed / Succeeded
[실험 B] SIGKILL 로 강제 종료      exit 137   → Failed
                              (128 + 9. 9번 신호로 죽었다는 뜻)
```

**`kubectl get pods`만 봐도 정상 종료였는지 강제 종료였는지 구분된다.**

```text
Completed      시간 안에 스스로 정리하고 나갔다
Failed / Error 못 나가서 죽임당했다 — 정리 작업이 중간에 끊겼다
```

**운영에서 경고 신호다.** 배포할 때마다 `Failed`로 끝난다면 앱이 SIGTERM을 처리하지 못하고 있다는 뜻이다.

## 발견 12 — PID 1이 죽으면 나머지도 죽는다

```text
14:24:50   sh=1 sleep=1
14:24:52   sh=0 sleep=0     ← 둘 다 동시에
```

SIGKILL은 PID 1에만 갔을 텐데 `sleep`도 같이 죽었다.

```text
PID 네임스페이스의 1번 프로세스가 종료되면
→ 커널이 그 네임스페이스의 나머지 프로세스를 전부 SIGKILL 한다
```

**컨테이너가 격리된 PID 네임스페이스라는 것이 여기서 드러난다.** 1번이 죽으면 그 안의 세계가 통째로 정리된다.

## 발견 13 — `Killing`은 "죽였다"가 아니라 "죽이기 시작한다"

```text
14:24:21   Killing 이벤트
14:24:52   실제로 죽음        ← 31초 뒤
```

이벤트만 보면 14:24:21에 끝난 것처럼 보인다. **이벤트는 시작을 알리지 완료를 알리지 않는다.** 08 문서 실험 3에서 apiserver의 `Killing` 이벤트를 보고 판단했던 것도 같은 함정이었다.

## 두 실험 종합

| | 실험 A (nginx) | 실험 B (`trap "" TERM`) |
|---|---|---|
| SIGTERM 반응 | 즉시 종료 | 무시 |
| 걸린 시간 | **< 1초** | **32초** |
| 실제 종료 신호 | SIGTERM | SIGKILL |
| 최종 상태 | `Completed` | `Failed` |
| `kubectl delete` | 즉시 반환 | 32초 매달림 |
| grace period 30초 | 안 씀 | 꽉 채움 |
| `PHASE` (대기 중) | — | `Running` |

```text
terminationGracePeriodSeconds 는 "대기 시간" 이 아니라 "상한" 이다
빨리 죽으면 그만큼 빨리 끝나고, 안 죽으면 그 시각에 강제 종료된다
```

```text
삭제 절차 전체

1. kubectl delete           apiserver 에 요청
2. apiserver                deletionTimestamp = now + gracePeriod 기록
                            (오브젝트는 아직 안 지운다)
3. kubelet                  컨테이너 PID 1 에 SIGTERM
4-a. 프로세스가 죽으면        즉시 다음 단계
4-b. 안 죽으면               deletionTimestamp 시각에 SIGKILL
5. kubelet                  종료 확인 후 apiserver 에 최종 삭제 요청
6. apiserver                오브젝트 제거
```

**08 문서 실험 1에서 Pod가 `Terminating`에 영원히 머문 이유가 5번이다.** kubelet이 죽어 있어 "지웠습니다"를 보고할 주체가 없었다.

---

# 8. initContainer

## 이것이 푸는 문제

앱을 띄우기 전에 **반드시 끝나야 하는 준비 작업**이 있다. DB 스키마 마이그레이션, 설정 파일 수신, 의존 서비스 대기, 디렉터리 준비 같은 것들이다.

앱 컨테이너 안에서 하면 세 가지 문제가 생긴다.

```text
[1] 앱 이미지가 더러워진다
    마이그레이션 도구 / psql / curl / git 을 앱 이미지에 넣어야 한다
    운영 중엔 안 쓰는 도구가 계속 들어있고, 그 취약점이 앱으로 이어진다

[2] 실패해도 컨테이너는 이미 "시작된" 상태다
    Running 인데 실제로는 아무것도 못 하는 Pod 가 만들어진다
    readiness 설정이 허술하면 Service 에 등록되어 트래픽을 받는다

[3] PID 1 문제 (7절 참조)
    sh 로 감싸면 SIGTERM 이 앱에 안 간다
```

**initContainer 는 [1]과 [2]를 푼다.** [3]은 별개 문제이며 `ENTRYPOINT` 로 풀어야 한다.

```text
initContainer 가 주는 것
  1. 준비 안 된 앱이 서비스에 투입되는 것을 구조적으로 막는다   ← 주된 이유
  2. 이미지를 분리할 수 있다
  3. 준비 작업에만 높은 권한을 줄 수 있다
```

> **용어 주의.** Kubernetes 의 `initContainer` 와 리눅스의 `init 프로세스(PID 1)` 는 이름만 비슷하고 아무 관계가 없다. `initContainer` 를 써도 PID 1 문제는 그대로 남는다.

## 실험 A — 정상 initContainer 2개 (2026-08-11 14:38)

```yaml
# /tmp/pod-init.yaml
apiVersion: v1
kind: Pod
metadata:
  name: pod-init
spec:
  initContainers:
  - name: init-1
    image: busybox
    command: ['sh', '-c', 'echo "[init-1] 시작"; sleep 10; echo "[init-1] 완료"']
  - name: init-2
    image: busybox
    command: ['sh', '-c', 'echo "[init-2] 시작"; sleep 5; echo "[init-2] 완료"']
  containers:
  - name: main
    image: nginx
```

**컨테이너 이름을 명시했다.** 실험 A(종료 절차)에서 `kubectl run` 이 Pod 이름을 컨테이너 이름으로 쓰는 바람에 헤맸으므로, 이번에는 이름이 분명하다.

### STATUS 전이

```text
14:38:14 pod-init   0/1   Init:0/2          0s
14:38:28 pod-init   0/1   Init:1/2          14s
14:38:35 pod-init   0/1   PodInitializing   21s
14:38:37 pod-init   1/1   Running           23s
```

**initContainer 가 있으면 `STATUS` 열이 진행 상황을 직접 보여준다.**

```text
Init:0/2           init 2개 중 0개 완료
Init:1/2           1개 완료
PodInitializing    init 은 끝났고 main 준비 중
Running            main 시작
```

### 컨테이너별 실행 시각

```text
init-1   startedAt 05:38:17  finishedAt 05:38:27   exitCode 0    10초
init-2   startedAt 05:38:29  finishedAt 05:38:34   exitCode 0     5초
main     startedAt 05:38:36  (running)
```

### 발견 14 — 순차 실행이다

```text
init-1   05:38:17 ~ 05:38:27
init-2   05:38:29 ~ 05:38:34     ← init-1 이 끝난 뒤에 시작한다
```

**동시에 돌았다면 총 10초에 끝났을 텐데 15초가 걸렸다.** 겹치지 않는다.

이 덕분에 "먼저 DB 를 만들고, 그다음 스키마를 넣는다" 같은 의존 순서를 표현할 수 있다.

### 발견 15 — `Initialized` 의 의미가 확정됐다 ★

```text
[pod-basic]   PodScheduled 02:34:37  →  Initialized 02:34:37    차이 0초
[pod-init]    PodScheduled 05:38:14  →  Initialized 05:38:35    차이 21초
```

3절 발견 4에서 "initContainer 가 없어서 즉시 통과한 것 같다"고 **추측만 했다.** 이번 대비로 확정됐다.

```text
Initialized = 모든 initContainer 가 성공적으로 끝났다
              없으면 끝낼 게 없어 즉시 True
```

시각도 정확하다.

```text
init-2 종료   05:38:34
Initialized   05:38:35     ← 1초 뒤
```

### 발견 16 — `PodReadyToStartContainers` 의 정체도 드러났다

3절 미확인 항목이었다. `pod-basic` 에서는 전 과정이 3초라 조건들이 전부 같은 초에 찍혀 구분되지 않았다.

```text
[pod-basic]   PodReadyToStartContainers 02:34:40 = ContainersReady = Ready
              → 순서를 알 수 없었다

[pod-init]    PodScheduled               05:38:14
              PodReadyToStartContainers  05:38:17   ← init-1 시작 시각
              Initialized                05:38:35
              Ready                      05:38:37
              → 18초 앞선다. 확실히 다른 단계다
```

**`PodReadyToStartContainers` 는 sandbox 와 네트워크가 준비된 시점이고, init 완료를 기다리지 않는다.** 실험 B에서 한 번 더 확인된다.

**느린 Pod 를 만드니 빠른 Pod 에서 안 보이던 순서가 보였다.**

### 발견 17 — `READY` 의 분모는 initContainer 를 안 센다

```text
14:38:14  0/1  Init:0/2       ← init 이 2개인데 분모는 1
14:38:37  1/1  Running
```

```text
READY 의 분모 = containers 배열 개수
init 진행은 STATUS 열에서 Init:n/m 으로 따로 보여준다
```

### 발견 18 — busybox 를 두 번 받았다

```text
05:38:15   Pulling busybox     init-1 용
05:38:28   Pulling busybox     init-2 용     ← 같은 이미지인데 또
05:38:35   Pulling nginx       main 용
```

4절과 같은 이유다. `image: busybox` 는 태그가 없어 `busybox:latest` 로 해석되고 `imagePullPolicy: Always` 가 붙는다.

**`imagePullPolicy` 는 Pod 단위가 아니라 컨테이너마다 따로 적용된다.**

```text
컨테이너 3개 × Always = pull 3번
총 23초 중 약 4.6초가 이미지 확인에 쓰였다
init 이 5개면 5번 받는다. 레지스트리가 죽으면 Pod 가 못 뜬다
```

### 발견 19 — 끝난 컨테이너의 로그가 남는다

```text
root@master01:/# kubectl logs pod-init -c init-1
[init-1] 시작
[init-1] 완료
```

이미 종료된 컨테이너인데 로그가 조회된다. **컨테이너 객체가 노드에 남아 있기 때문이다**(10절에서 확인).

```text
initContainerStatuses   init-1, init-2 → terminated, exitCode 0
containerStatuses       main           → running
```

**배열이 분리되어 있고, 같은 Pod 안에서 상태가 다르다.** 장애 분석에서 `-c` 로 컨테이너를 골라야 하는 이유다.

## 실험 B — 실패하는 initContainer (15:25)

```yaml
initContainers:
- name: init-fail
  image: busybox
  command: ['sh', '-c', 'echo "[init-fail] 준비 작업 시작"; sleep 3; echo "[init-fail] 실패"; exit 1']
containers:
- name: main
  image: nginx
```

### STATUS 순환

```text
15:25:37  Init:0/1                0
15:25:40  Init:Error              0
15:25:42  Init:0/1                1 (3s ago)
15:25:45  Init:Error              1
15:25:59  Init:CrashLoopBackOff   1
15:26:01  Init:0/1                2
15:26:04  Init:Error              2
15:26:29  Init:CrashLoopBackOff   2
15:26:31  Init:0/1                3
15:26:34  Init:Error              3
15:27:33  Init:CrashLoopBackOff   3
15:27:35  Init:0/1                4
15:27:38  Init:Error              4
15:28:42  Init:CrashLoopBackOff   4
15:29:08  Init:0/1                5
15:29:11  Init:Error              5
```

### 발견 20 — 상태 세 개를 순환한다

```text
Init:0/1                실행 중
   ↓ 3초 뒤 exit 1
Init:Error              실패로 끝났다
   ↓ 백오프 대기
Init:CrashLoopBackOff   다음 시도를 기다리는 중
   ↓
Init:0/1                다시 실행
```

**운영 시사점**: `kubectl get pods` 를 한 번만 치면 셋 중 아무거나 보인다. 같은 Pod 인데 볼 때마다 다르게 보인다. **`RESTARTS` 숫자를 함께 봐야 정확하다.**

### 발견 21 — 백오프 값은 상태 필드에서 읽는다 ★

watch 출력으로 잰 간격은 부정확했다.

```text
[watch 로 관측한 Error → 0/1 간격]
  2초 / 16초 / 27초 / 61초 / 90초
```

kubelet 이 직접 말해주는 값이 따로 있다.

```text
state: {"waiting":{
  "message":"back-off 2m40s restarting failed container=init-fail pod=pod-init-fail_k8s-lab(...)",
  "reason":"CrashLoopBackOff"}}
```

```text
2m40s = 160초
restartCount 5 → 5번째 백오프
10 → 20 → 40 → 80 → 160 → (상한 300)
```

**교과서 값 그대로다.** watch 로 잰 값은 kubelet 동기화 주기(약 10초)와 화면 갱신 지연이 섞인 결과였다.

```text
화면을 재는 것보다 상태 필드의 message 를 읽는 것이 정확하다
```

08 문서에서 정리한 `CrashLoopBackOff` 백오프가 **initContainer 에도 그대로 적용된다.**

### 발견 22 — 무한 재시도한다

```text
restartCount   1 → 2 → 3 → 4 → 5 ...
```

7절에서 정리한 것과 일치한다.

```text
[Pod / Deployment / StatefulSet]   제한 없음. 영원히 재시도
[Job / CronJob]                    backoffLimit: 6 → 포기
```

**배포해놓고 방치하면 며칠이고 5분 간격으로 계속 시도한다.** 그동안 이미지도 계속 받는다(발견 18).

### 발견 23 — main 은 시작조차 안 한다 ★★

네 가지 증거로 확인했다.

**1. 이미지를 받으려는 시도조차 없다**

```text
root@master01:/# kubectl get events --sort-by=... | grep pod-init-fail
Pulling image "busybox"   ← 여러 번
Pulled  busybox           ← 6번 (최초 1 + 재시작 5)
BackOff  Back-off restarting failed container init-fail in pod pod-init-fail_k8s-lab(...)
```

**`Pulling image "nginx"` 가 한 번도 없다.**

```text
[실험 A 성공]  busybox → busybox → nginx   (3번 pull)
[실험 B 실패]  busybox × 6                (nginx 0번)
```

**2. 노드에 컨테이너가 없다**

```text
root@worker01:/# sudo crictl ps -a | grep -E 'init-fail|main'
e8a7a4dfef9a  ...  Exited   init-fail   5   825b06908a9ab   pod-init-fail   k8s-lab
c65348e3ba29f ...  Running  main        0   077a87453be47   pod-init        k8s-lab
                                                            ^^^^^^^^
                                                            이건 다른 Pod
```

**마지막 열이 Pod 이름이다.** `main` 은 `pod-init` 것이고, `pod-init-fail` 의 `main` 은 목록에 없다.

> 컨테이너 이름이 둘 다 `main` 이라 혼동하기 쉽다. `crictl` 로 볼 때는 POD 열을 확인해야 한다.

**3. 상태에 명시되어 있다**

```text
main    {"waiting":{"reason":"PodInitializing"}}
```

```text
running     실행 중
terminated  종료됨
waiting     아직 시작 안 함   ← reason 에 이유가 적힌다
```

**4. 로그 조회가 그 이유를 그대로 말한다**

```text
root@master01:/# kubectl logs pod-init-fail -c main
Error from server (BadRequest): container "main" in pod "pod-init-fail"
is waiting to start: PodInitializing
```

**"로그가 없다"가 아니라 "아직 시작 안 했다"이다.** 실무에서 자주 오해하는 메시지다.

### 조건 대비

```text
[pod-init — 성공]                    [pod-init-fail — 실패]
PodScheduled               True      PodScheduled               True
PodReadyToStartContainers  True      PodReadyToStartContainers  True    ← 둘 다 True
Initialized                True      Initialized                False   ★ 여기서 갈린다
ContainersReady            True      ContainersReady            False
Ready                      True      Ready                      False
```

**`Initialized` 하나에서 갈리고 그 아래가 전부 못 넘어간다.**

그리고 init 이 계속 실패하는데도 `PodReadyToStartContainers` 는 `True` 다. **발견 16의 해석(sandbox 단계 조건)이 재확인됐다.**

### 실패 정보

```text
lastState: {"terminated":{
  "containerID":"containerd://e8a7a4df...",
  "exitCode":1,
  "reason":"Error",
  "startedAt":"2026-08-11T06:29:08Z",
  "finishedAt":"2026-08-11T06:29:11Z"}}
```

**정확히 3초.** `sleep 3` 그대로다.

## 실험 A · B 종합

| | 실험 A (성공) | 실험 B (실패) |
|---|---|---|
| init 결과 | `exit 0` | `exit 1` |
| `Initialized` | 21초 뒤 `True` | 계속 `False` |
| nginx 이미지 | 받음 | **안 받음** |
| `main` 컨테이너 | 생성·실행 | **생성조차 안 됨** |
| `main` 상태 | `running` | `waiting: PodInitializing` |
| 재시도 | 없음 | 무한. 10→20→40→80→160초 |
| 최종 | `Running` | `Init:CrashLoopBackOff` 순환 |

**"준비가 안 된 앱이 서비스에 투입되는 것을 구조적으로 막는다"가 증명됐다.** 이미지조차 안 받고 컨테이너 객체 자체가 안 생긴다.

## 장애 진단에서의 활용

```text
"Pod 가 안 떠요" 라는 신고를 받았을 때

  Pending                  스케줄할 노드가 없다
  Init:0/2                 initContainer 에서 막혔다  → kubectl logs -c <init이름>
  Init:CrashLoopBackOff    반복 실패 중              → lastState 의 exitCode
  PodInitializing          init 은 끝났고 앱 이미지 받는 중
  CrashLoopBackOff         앱이 뜨다가 죽는다        → kubectl logs -c <앱이름>
```

**어디를 볼지가 STATUS 한 열로 정해진다.**

---

# 9. 컨테이너 여러 개일 때 — Pod 는 왜 존재하는가

지금까지 컨테이너를 하나만 넣었다. 그러면 **Pod 가 불필요한 껍데기로 보인다.** 둘 이상 넣어야 이유가 드러난다.

## 이것이 푸는 문제

컨테이너 둘이 긴밀히 협력해야 하는 상황을 Docker 만으로 표현하려면 네 가지가 걸린다.

```text
[1] 같은 호스트에 뜬다는 보장이 없다      서버가 여러 대면 흩어진다
[2] 파일 공유가 호스트 경로에 의존한다
[3] 네트워크가 따로다                    서로의 IP 를 알아야 한다
[4] 생명주기가 따로 논다                 하나가 죽어도 다른 건 산다
```

**"항상 붙어 다녀야 하는 컨테이너"를 표현할 방법이 없다.**

```text
Pod = 세 가지의 단위

  스케줄링 단위   Pod 단위로 노드가 정해진다. 안의 컨테이너는 반드시 같은 노드
  공유 단위       네트워크, hostname, 선언한 볼륨
  생명주기 단위   함께 뜨고 함께 죽는다
```

## 실험 (2026-08-12)

```yaml
# /tmp/pod-multi.yaml
apiVersion: v1
kind: Pod
metadata:
  name: pod-multi
spec:
  volumes:
  - name: shared
    emptyDir: {}
  containers:
  - name: web
    image: nginx
    volumeMounts:
    - name: shared
      mountPath: /shared
  - name: helper
    image: busybox
    command: ['sh', '-c', 'sleep 3600']
    volumeMounts:
    - name: shared
      mountPath: /shared
```

### 발견 24 — 컨테이너가 둘인데 IP 는 하나

```text
root@master01:/# kubectl get pod pod-multi -o wide
NAME        READY   STATUS    RESTARTS   AGE    IP            NODE
pod-multi   2/2     Running   0          116s   10.244.5.31   worker01
```

```text
IP 는 Pod 에 붙는다. 컨테이너에 붙는 게 아니다
```

`10.244.5.31` 은 여전히 worker01 의 Calico 블록 `10.244.5.0/26` 안이다(6절 확인).

**`READY` 의 분모가 `2` 로 바뀌었다.**

```text
2/2 = 컨테이너 둘 다 준비됐다
하나만 준비되면 1/2 → Pod 전체가 Ready 가 아니다
→ 사이드카가 죽어도 앱이 서비스에서 빠진다
→ 묶는다는 것은 운명을 같이한다는 뜻이다
```

### 발견 25 — hostname 이 같다

```text
root@master01:/# kubectl exec pod-multi -c web -- hostname
pod-multi
root@master01:/# kubectl exec pod-multi -c helper -- hostname
pod-multi
```

UTS 네임스페이스도 공유한다.

### 발견 26 — `localhost` 로 통한다 ★

```text
root@master01:/# kubectl exec pod-multi -c helper -- wget -qO- http://localhost:80 | head -5
<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
```

**`helper` 에는 웹서버가 없는데 `localhost:80` 이 응답한다.** 같은 네트워크 네임스페이스를 쓰기 때문이다.

```text
[Pod 밖의 앱에 붙으려면]  Service 이름 → DNS 조회 → 네트워크 통과
[같은 Pod 안이면]        localhost. 끝
```

**이것이 사이드카 패턴의 기반이다.**

**함정**: 포트 공간도 공유하므로 두 컨테이너가 같은 포트를 쓰면 나중에 뜬 쪽이 실패한다. 한 Pod 안에서는 포트를 겹치지 않게 설계해야 한다.

### 발견 27 — 볼륨은 명시적으로 선언한 것만 공유된다

```text
root@master01:/# kubectl exec pod-multi -c helper -- sh -c 'echo "helper 가 쓴 내용" > /shared/test.txt'
root@master01:/# kubectl exec pod-multi -c web -- cat /shared/test.txt
helper 가 쓴 내용
```

```text
[Pod 수준]      volumes        "이런 볼륨이 있다"
[컨테이너 수준]  volumeMounts   "나는 그걸 여기에 붙이겠다"
```

**한쪽에만 `volumeMounts` 를 쓰면 그 컨테이너만 볼 수 있고, 마운트 경로도 컨테이너마다 다르게 줄 수 있다.** 공유는 기본값이 아니라 선언이다.

### 발견 28 — 파일시스템은 격리된다

```text
root@master01:/# kubectl exec pod-multi -c web -- ls /usr/share/nginx/html
50x.html
index.html

root@master01:/# kubectl exec pod-multi -c helper -- ls /usr/share/nginx/html
ls: /usr/share/nginx/html: No such file or directory
command terminated with exit code 1
```

`web` 은 nginx 이미지, `helper` 는 busybox 이미지다. **서로 다른 파일시스템을 갖는다.**

```text
앱은 최소 이미지(distroless)로 만들어 공격 표면을 줄이고
디버깅 도구는 사이드카에 넣는다
→ 앱 이미지를 더럽히지 않고 도구를 쓸 수 있다
```

**8절 initContainer 의 "이미지 분리" 이점과 같은 성질이다.**

### 발견 29 — PID 는 격리되고, `exec` 최적화가 실증됐다 ★★

```text
root@master01:/# kubectl exec pod-multi -c helper -- ps -ef
PID   USER     TIME  COMMAND
    1 root      0:00 sleep 3600
   35 root      0:00 ps -ef
```

**`nginx` 가 안 보인다.** 프로세스는 격리된다.

```text
[네트워크를 공유하는 이유]  협력하려면 통신이 쉬워야 한다
[PID 를 격리하는 이유]      서로의 프로세스를 죽이거나 들여다보지 못하게
```

**그런데 `PID 1` 이 `sh` 가 아니라 `sleep 3600` 이다.**

7절에서 셸의 `exec` 최적화를 설명했는데, 그것이 우연히 대조군으로 증명됐다.

```text
[pod-stubborn]  sh -c 'trap "" TERM; sleep 3600'    명령 둘
                → sh=1  sleep=1     프로세스 두 개
                → sh 가 PID 1. SIGTERM 이 sleep 에 안 감
                → 32초 걸려 SIGKILL 로 종료

[helper]        sh -c 'sleep 3600'                  명령 하나
                → sleep 만 PID 1     프로세스 한 개
                → 셸이 자기 자리를 sleep 에게 넘기고 사라졌다 (자동 exec)
```

**같은 `sh -c` 인데 명령 개수 하나 차이로 결과가 갈렸다.** 이 버그가 왜 간헐적이고 찾기 어려운지가 여기서 드러난다.

### 발견 30 — sandbox 가 이것을 가능하게 한다

```text
root@worker01:/# sudo crictl ps -a
CONTAINER      ...  NAME      ATTEMPT  POD ID          POD
c65348e3ba29f  ...  main      0        077a87453be47   pod-init
a57cd0516958a  ...  init-2    0        077a87453be47   pod-init
3d0dac24cb675  ...  init-1    0        077a87453be47   pod-init
                                       ^^^^^^^^^^^^^
                                       셋 다 같은 POD ID
```

```text
root@worker01:/# sudo crictl pods
POD ID          CREATED             STATE      NAME                NAMESPACE
e6e77a834f075   About an hour ago   Ready      pod-multi           k8s-lab
077a87453be47   23 hours ago        Ready      pod-init            k8s-lab
```

**`POD ID` 가 sandbox 의 ID 다.** 07 문서 3라운드에서 본 구조가 확인된다.

```text
kubelet 이 먼저 sandbox 를 띄운다 → 네트워크 네임스페이스를 들고 있다
그다음 컨테이너들이 그 네임스페이스에 합류한다
파일시스템은 각자 자기 이미지 것을 쓴다
```

**07 3라운드에서 "컨테이너를 죽여도 IP 가 유지됐다"던 이유가 이것이다.** sandbox 가 안 죽었으니 네트워크가 유지됐다.

## 공유 경계

```text
[공유한다]
  네트워크 네임스페이스   IP, 포트 공간, localhost
  UTS 네임스페이스        hostname
  IPC 네임스페이스        (확인 안 함)
  선언한 볼륨             volumeMounts 를 쓴 것만

[격리한다]
  마운트 네임스페이스     파일시스템. 볼륨 지점만 예외
  PID 네임스페이스        프로세스 (기본값)
                         spec.shareProcessNamespace: true 로 켤 수 있다 (확인 안 함)
```

## 판단 기준

```text
[한 Pod 에 넣는다]
  생명주기가 같다        앱이 죽으면 사이드카도 의미 없다
  항상 1:1 이다          앱 1개당 수집기 1개
  localhost 나 파일 공유가 필요하다

[나눈다]
  따로 확장해야 한다      앱 10개, DB 1개
  생명주기가 다르다       앱을 재배포해도 DB 는 유지
  포트가 겹친다
```

**같은 Pod 에 넣으면 개수가 항상 같아진다.** `replicas: 5` 면 앱도 5개, DB 도 5개다. 3단계에서 앱을 설계할 때 이 판단을 하게 된다.

---

# 10. 곁가지 — sandbox 기록에서 읽은 것

`crictl pods` 출력에 예상하지 못한 정보가 있었다.

## 발견 31 — sandbox 생성 시각이 실험의 기록이다 ★

```text
root@master01:/# sudo crictl pods
POD ID          CREATED      NAME
ec2502043e321   2 days ago   etcd-master01
168706eb60a4d   2 days ago   kube-apiserver-master01
862b201cef496   2 days ago   coredns-7d764666f9-v64mb
63b4ae7c234f4   4 days ago   kube-scheduler-master01
972b8e3d0a43d   8 days ago   calico-node-5khhz
e5ebf50049bcb   8 days ago   kube-proxy-zbzcj
5e9507b7aa01e   8 days ago   kube-controller-manager-master01
```

```text
8일 전    클러스터 구축
4일 전    07 문서 3라운드 실험 2 — scheduler manifest 를 mv 했다
2일 전    08 문서 실험 3·4 — apiserver / etcd manifest 를 mv 했다
```

**`kube-controller-manager` 만 8일 전 그대로다.** 우리가 건드리지 않은 유일한 Control Plane 컴포넌트다.

```text
sandbox 는 컨테이너보다 오래 산다
컨테이너가 재시작해도 sandbox 는 유지된다
sandbox 가 새로 만들어졌다는 것은 Pod 자체가 다시 만들어졌다는 뜻이다
```

## 발견 32 — 08 문서의 거짓말이 아직 남아 있다 ★★

```text
root@master01:/# kubectl get pod -n kube-system kube-apiserver-master01 -o wide
NAME                      READY  STATUS   RESTARTS      AGE  IP              NODE
kube-apiserver-master01   1/1    Running  5 (47h ago)   8d   192.168.8.143   master01
```

```text
kubectl 이 말하는 것   AGE 8d       "이 Pod 는 8일째다"
crictl 이 말하는 것    2 days ago   "이 sandbox 는 2일 전에 만들어졌다"
                                    ↑ 이쪽이 사실이다
```

08 문서 실험 3의 발견이 그대로 남아 있다. **미러 Pod 를 삭제할 수 없어(자기참조) 오브젝트가 안 지워졌고, 그래서 계속 늙고 있다.**

```text
RESTARTS 5 (47h ago)   ← 실험 4(etcd 중단)에서 5번 재시작한 기록. 이건 사실
AGE 8d                 ← 거짓
```

**같은 출력의 두 숫자가 서로 다른 이야기를 한다.**

```text
07 3라운드의 세 층이 여기서 재확인된다

  선언   /etc/kubernetes/manifests/kube-apiserver.yaml
  실제   sandbox + 컨테이너        2일 전에 새로 만들어졌다
  사본   미러 Pod                  8일 전 것이 그대로 남아있다
```

## 발견 33 — CoreDNS 가 master01 에 있다

```text
root@master01:/# kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide
NAME                       READY  STATUS   RESTARTS  AGE    IP               NODE
coredns-7d764666f9-899zs   1/1    Running  0         2d1h   10.244.30.85     worker02
coredns-7d764666f9-v64mb   1/1    Running  0         2d3h   10.244.241.65    master01
```

master01 에는 `NoSchedule` taint 가 있는데도 떠 있다.

```text
tolerations:
  {"key":"CriticalAddonsOnly","operator":"Exists"}
  {"effect":"NoSchedule","key":"node-role.kubernetes.io/control-plane"}    ← 이것 때문
  {"effect":"NoExecute","key":"node.kubernetes.io/not-ready","tolerationSeconds":300}
  {"effect":"NoExecute","key":"node.kubernetes.io/unreachable","tolerationSeconds":300}
```

**toleration 네 개의 출처가 둘이다.**

```text
[명시적으로 쓴 것]  CriticalAddonsOnly / control-plane
[자동 주입된 것]    not-ready / unreachable (각 300초)
```

08 문서에서 따로 배운 두 가지가 한 Pod 안에 섞여 있다.

**IP `10.244.241.65` 는 master01 의 Calico 블록 `10.244.241.64/26`(범위 64~127) 안이다.** 6절에서 "master01 블록은 거의 비어 있을 것"이라 했는데, Control Plane Pod 들은 `hostNetwork` 라 Pod IP 를 안 받고 CoreDNS 만 받는다.

**두 CoreDNS 의 AGE 가 2일 전으로, 08 문서 장애 실험 날이다.** 실험 1·2에서 worker01 이 NotReady 가 되자 축출됐고, toleration 덕에 master01 로 갈 수 있었다.

```text
노드가 복구돼도 Pod 는 저절로 원래 자리로 돌아가지 않는다
Kubernetes 는 "지금 잘 돌고 있으면 건드리지 않는다"
```

## 발견 34 — 죽은 sandbox 가 남는 이유

```text
root@worker01:/# sudo crictl pods
69e9a54727cb5   8 days ago   Ready      calico-node-bsg58   ATTEMPT 1
3ec297b040587   8 days ago   NotReady   calico-node-bsg58   ATTEMPT 0
1ed33c1a9bdb1   8 days ago   Ready      kube-proxy-c8rqh    ATTEMPT 1
0a74ff62dd0b8   8 days ago   NotReady   kube-proxy-c8rqh    ATTEMPT 0
```

**`crictl pods` 의 `NotReady` 는 "고장난 Pod" 가 아니다.**

```text
Pod 오브젝트    calico-node-bsg58   하나. kubectl 로 보면 Running

그 Pod 의 sandbox 이력
  ATTEMPT 0   3ec297b040587   NotReady   8일 전에 죽은 것. 기록만 남음
  ATTEMPT 1   69e9a54727cb5   Ready      지금 쓰는 것
```

**Pod 하나가 살아온 동안 sandbox 는 여러 번 바뀔 수 있고, `crictl pods` 는 그 이력을 전부 보여준다.**

죽은 sandbox 에 컨테이너 기록이 붙어 있다.

```text
root@worker01:/# sudo crictl ps -a --pod 3ec297b040587
CONTAINER      IMAGE          CREATED     STATE    NAME          ATTEMPT  POD
814c130e4ed71  c281bc67214e2  8 days ago  Exited   calico-node   0        calico-node-bsg58
```

**동작 중인 것이 아니라 종료된 컨테이너의 기록이다.**

```text
kubelet 은 컨테이너 이름당 죽은 것 하나를 일부러 남긴다
→ kubectl logs --previous 로 이전 실행의 로그를 볼 수 있게 하려고
→ lastState 의 exitCode / reason / 시각도 이 기록에서 나온다
```

**8절 실험 B에서 읽은 `lastState`(exitCode 1, 3초)가 바로 이 기록이었다.**

```text
컨테이너 기록이 안 지워지니 sandbox 도 못 지운다
다음 재시작이 일어나면 밀려나며 정리된다. 무한히 쌓이지 않는다
```

### 보강 (2026-08-13) — 정리되는 것과 남는 것의 차이

01 문서 실습에서 Pod 를 삭제하며 sandbox 의 소멸 과정을 관찰했다.

```text
root@worker01:/# crictl pods        # kubectl delete pods 직후
28dd2910e9b2b   8 minutes ago       NotReady   rs-demo-qqfbs   k8s-lab
c669dd9890b36   About an hour ago   NotReady   rs-demo-ss4gq   k8s-lab

root@worker01:/# crictl pods        # 잠시 뒤
(두 줄이 사라짐)
```

**삭제가 세 단계로 나뉜다.**

```text
[1] Pod 오브젝트가 사라진다     kubectl get pods → 즉시
[2] sandbox 가 멈춘다           crictl pods → NotReady
       네트워크 네임스페이스 해체 / IP 를 Calico 블록에 반납
[3] sandbox 기록이 정리된다     crictl pods → 목록에서 사라짐
```

**`kubectl` 로는 [1]만 보인다.** `NotReady` 는 "멈췄지만 기록은 아직 있는" 중간 상태다.

이것이 위 발견 34의 의문을 정리해준다. **왜 `calico-node` 의 ATTEMPT 0 sandbox 는 9일째 남아 있는가.**

```text
[rs-demo-qqfbs]           Pod 오브젝트가 사라졌다
                          → 보존할 이유가 없다 → GC 가 지운다

[calico-node ATTEMPT 0]   Pod 오브젝트는 아직 살아있다 (ATTEMPT 1 로 실행 중)
                          → 그 안의 죽은 컨테이너를 하나 남겨둔다 (--previous 용)
                          → 컨테이너가 남으니 sandbox 도 못 지운다
```

```text
Pod 가 사라진 sandbox                → 치울 대상. 곧 정리된다
Pod 는 살아있고 옛 sandbox 만 죽은 것  → 증거. 로그 보존을 위해 남는다
```

**IP 재사용 관찰**: 삭제된 Pod 의 주소가 바로 재사용되지는 않았다.

```text
r5mw8   10.244.5.33   (삭제)
9fs8c   10.244.5.34   ← 33 이 아니라 34
m8rmv   10.244.5.35
qqfbs   10.244.5.x
```

옛 연결이 새 Pod 로 가는 것을 막으려는 것으로 보이나 **재사용 정책은 확인하지 않았다.** 블록 내 빈자리는 `ipamblocks` 의 `unallocated` 필드에 있다.

**한계**: `--previous` 는 직전 하나까지만 본다. CrashLoopBackOff 로 20번 재시작한 Pod 에서 "처음에 왜 죽기 시작했나"는 볼 수 없다. **5단계에서 로그를 노드 밖으로 수집해야 하는 이유다.**

> **미확인**
> ```text
> 1. calico-node / kube-proxy 의 sandbox 가 8일 전 재생성된 원인
>    두 Pod 가 같은 시각(7d1h 전)에 재시작한 것으로 보아 노드 재부팅으로 추정
> 2. kubectl logs --previous 로 8일 전 로그가 실제로 나오는지 (미실행)
> 3. controller-manager 의 --terminated-pod-gc-threshold 기본값 (미확인)
> 4. kubelet 의 컨테이너 GC 정확한 조건과 기본값
> ```

## Pod 오브젝트는 자동으로 안 치워질 수 있다

노드 층과 오브젝트 층을 나눠야 한다.

```text
[노드 층]  컨테이너 / sandbox / 이미지
           kubelet 이 자동 정리한다. 사람이 개입할 필요 없다
           08 문서 실험 2의 ImageGCFailed / ContainerGCFailed 가 그 증거

[오브젝트 층]
  컨트롤러가 만든 Pod    컨트롤러가 정리한다
  맨 Pod 가 Failed       아무도 안 지운다. 사람이 지워야 한다
  축출(Evicted)된 Pod    남는다. 수백 개씩 쌓이는 것이 실무의 고전적 문제
```

```text
kubectl get pods --field-selector status.phase=Failed
kubectl delete pods --field-selector status.phase=Failed
```

**Kubernetes 는 "실행 중인 것"을 관리하지 "끝난 것의 기록"을 적극적으로 치우지 않는다.** 그 기록이 장애 분석에 필요하기 때문이기도 하다.

6단계 시나리오 D(OOMKilled)와 B(Worker VM 강제 종료)에서 실제 문제로 만나게 된다.

---

# 여기까지의 정리

```text
1. Node 는 클러스터 전역 리소스라 -n 이 무시된다
2. Pod 생성은 3초. Scheduled → Pulling → Pulled → Created → Started
3. 이벤트를 만든 주체가 둘이라 시각 필드가 다르다
   scheduler = eventTime, kubelet = lastTimestamp
4. phase 는 한 단어 요약, conditions 는 단계별 세부 + 전이 시각
   conditions 의 배열 순서는 시간 순서가 아니다
5. initContainer 가 없으면 Initialized 는 즉시 True 다
   조건이 True 라고 무언가를 한 것은 아니다
6. :latest 는 imagePullPolicy 를 Always 로 만든다 (실측 확인)
7. scheduler 는 filtering(될까) → scoring(어디가 나을까) 두 단계
   taint 는 filtering 에서 동작한다
   worker02 가 진 이유는 CoreDNS 가 거기 있어서다 (250m vs 350m)
8. node.spec.podCIDR 은 controller-manager 가 적지만 Calico 는 안 읽는다
9. 실제 대역은 Calico 의 blockaffinities 에 있다. 블록은 /26
   두 노드 다섯 Pod 가 전부 블록 안, podCIDR 안에는 하나도 없다
10. 블록을 거치는 이유는 조율 비용이다 (Lease 와 같은 발상)
11. podCIDR 을 만드는 이유는 kubeadm 이 CNI 를 모르기 때문이다
12. 같은 /16 을 이름 넷으로 부른다
    --pod-network-cidr / --cluster-cidr / clusterCIDR / IPPool cidr
    controller-manager 는 /24 로, Calico 는 /26 으로 자른다
13. Event 는 TTL 이 있어 사라진다 (--event-ttl 미지정 = 기본값)
    Pod 는 그대로인데 이벤트만 없어지는 것을 실측했다
14. terminationGracePeriodSeconds 는 대기 시간이 아니라 상한이다
    nginx < 1초 / SIGTERM 무시 32초 — 두 실험의 대비로 확정
15. deletionTimestamp 는 삭제 시각이 아니라 마감 시각이다
    = 요청 시각 + gracePeriod
    → 08 문서의 "331초 중 설명 안 되는 31초" 가 이것이었다
16. SIGTERM 은 컨테이너의 PID 1 에게만 간다
    sh 로 감싸면 앱이 신호를 못 받는다 (PID 1 문제)
17. PID 1 이 죽으면 커널이 그 네임스페이스의 나머지를 정리한다
18. Completed 와 Failed 로 정상 종료/강제 종료가 구분된다 (exit 0 vs 137)
19. Killing 이벤트는 시작 신호이지 완료 신호가 아니다

--- initContainer (8절) ---
20. initContainer 는 순차 실행이다. 앞의 것이 끝나야 다음이 시작한다
21. 단순한 순서가 아니라 관문이다. 성공(exit 0)해야 다음으로 넘어간다
22. Initialized 조건의 의미가 확정됐다
    없으면 즉시 True, 있으면 전부 끝날 때까지 False (0초 vs 21초)
23. PodReadyToStartContainers 는 sandbox 단계 조건이다
    init 이 계속 실패해도 True 로 남는다
24. READY 의 분모는 containers 개수. initContainer 는 안 센다
25. imagePullPolicy 는 컨테이너마다 적용된다. busybox 를 두 번 받았다
26. 실패하면 앱 컨테이너는 이미지조차 안 받고 생성되지도 않는다
    네 가지로 확인 — 이벤트 / crictl / state.waiting / logs 오류
27. 백오프 값은 화면을 재지 말고 state.waiting.message 를 읽는다
    "back-off 2m40s" = 160초. 10→20→40→80→160 그대로

--- 멀티 컨테이너 (9절) ---
28. Pod 는 스케줄링 / 공유 / 생명주기의 단위다
29. IP 는 Pod 에 붙는다. 컨테이너 둘이어도 IP 는 하나
30. localhost 로 서로 통한다. 대신 포트 공간도 공유한다(충돌 주의)
31. 볼륨 공유는 기본값이 아니라 volumeMounts 선언이다
32. 파일시스템과 PID 는 격리된다. 서로 다른 이미지를 쓸 수 있는 이유
33. sh -c 의 명령 개수에 따라 exec 최적화가 갈린다 (7절 이론의 실증)
34. sandbox(POD ID)가 네임스페이스를 들고 있고 컨테이너들이 합류한다

--- sandbox 기록 (10절) ---
35. sandbox 생성 시각이 우리 실험의 기록으로 남아 있다
36. apiserver 의 AGE 는 여전히 거짓이다 (kubectl 8d vs crictl 2 days ago)
37. crictl pods 의 NotReady 는 고장난 Pod 가 아니라 옛 sandbox 기록이다
38. kubelet 이 죽은 컨테이너를 이름당 하나 남긴다
    --previous 와 lastState 가 그 기록에서 나온다
```

# 실습 리소스

```text
namespace      k8s-lab        유지 — 2단계 내내 사용
pod-basic      삭제됨         7절 실험 A (정상 종료)
pod-stubborn   삭제됨         7절 실험 B (SIGTERM 무시)
pod-init-fail  삭제됨         8절 실험 B (초기화 실패)
pod-init       삭제됨         8절 실험 A
pod-multi      삭제됨         9절
/tmp/pod-init.yaml, /tmp/pod-multi.yaml   재현용으로 master01 에 남김

2026-08-13 기준 k8s-lab 네임스페이스는 비어 있다.
```

**로드맵 2단계 결과물 7항목 대응**

```text
1. 오브젝트의 역할              9절 — Pod 는 세 가지의 단위
2. 생성 시 동작하는 Controller   맨 Pod 는 컨트롤러가 없다. scheduler → kubelet 만
                                (컨트롤러 이야기는 01-replicaset.md 에서)
3. 주요 Spec 과 Status 필드     3절 phase/conditions, 8·9절 initContainerStatuses 등
4. 다른 오브젝트와의 연결        Node(스케줄), Namespace(경계), 볼륨
5. 장애 사례                    7절 SIGTERM 무시, 8절 초기화 실패
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            :latest / PID 1 / 포트 충돌 / Failed Pod 누적
```

# 미확인 목록

```text
[확정됨 — 2026-08-11]
  imagePullPolicy = Always                    실측 확인
  worker02 의 추가 자원 = CoreDNS             실측 확인
  세 곳의 CIDR 값이 전부 10.244.0.0/16        실측 확인
  IPPool blockSize = 26                       실측 확인

[해소됨 — 2026-08-11/12]
  PodReadyToStartContainers 의 정체     8절 발견 16 — sandbox 단계 조건
  Event TTL (--event-ttl)              7절 — 플래그 없음 = 기본값 사용

[남은 것]
1. worker01 에 nginx 이미지가 이미 있었는지 (crictl images 미확인)
   → Pulling 이 찍힌 것이 Always 때문인지, 정말 없어서인지 구분 안 됨
2. ImageLocality 가 노드 선택에 작용했는지
3. kubectl api-resources --namespaced=false 전체 목록
4. 이벤트의 source.component / reportingComponent 직접 대조
5. ip route 에 /26 단위 경로가 보이는지
6. ipamblocks 의 unallocated 필드 (블록 내 빈자리 관리 방식)
7. calico-kube-controllers 의 RESTARTS 가 7 → 12 로 늘어난 원인
8. kubectl get events -w 가 끊기거나 목록을 다시 출력하는 이유
   watch 재연결로 보이나 확인하지 않음. 이벤트가 중복 출력되므로
   화면 순서를 시각으로 믿으면 안 된다
9. kubectl logs --previous 로 8일 전 로그가 실제로 나오는지
10. controller-manager 의 --terminated-pod-gc-threshold 기본값
11. kubelet 컨테이너 GC 의 정확한 조건과 기본값
12. CrashLoopBackOff 백오프의 현재 버전 기본값
    "back-off 2m40s" 로 160초는 확인했으나 상한과 리셋 조건은 미확인
13. spec.shareProcessNamespace: true 를 켰을 때의 동작
14. Pod 의 IPC 네임스페이스 공유 여부 (확인 안 함)
15. calico-node / kube-proxy 의 sandbox 가 8일 전 재생성된 원인
    노드 재부팅으로 추정
```

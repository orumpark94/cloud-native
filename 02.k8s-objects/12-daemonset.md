# 12. DaemonSet — 이미 두 달째 돌고 있던 것

`cloud-native-learning-roadmap.md` 2단계.

앞의 오브젝트들은 **우리가 새로 만들어서** 봤다. 이 문서는 반대다.

```text
1단계에서 Calico 를 설치했을 때 calico-node 가 노드마다 하나씩 떴다
kubeadm init 을 했을 때 kube-proxy 도 노드마다 하나씩 떴다

둘 다 DaemonSet 이다
그런데 "설치하니까 떴다" 로만 알고 넘어갔다
```

**이미 20일째 눈앞에서 돌고 있던 것을 이제야 오브젝트로 여는 문서다.**

실험은 넷이다.

```text
실험 A   이미 도는 것을 연다        calico-node / kube-proxy
실험 B   직접 만든다               로그 수집기 흉내
실험 C   일부 노드에만 둔다         nodeSelector
실험 D   Static Pod 와 비교        etcd / apiserver 는 왜 DaemonSet 이 아닌가
```

---

## 0. 큰 틀 — Static Pod 와 DaemonSet 은 어느 층인가

세부로 들어가기 전에 방향을 잡는다. **이걸 모르면 뒤가 파편으로 보인다.**

### Kubernetes 의 관리 기능은 전부 API Server 를 거친다

```text
Deployment 로 띄운다   → 컨트롤러가 API Server 에 요청 → etcd 에 저장
DaemonSet 으로 띄운다  → 컨트롤러가 API Server 에 요청 → etcd 에 저장
스케줄러가 배치한다     → API Server 에서 읽고 API Server 에 쓴다

그럼 API Server 와 etcd 자체는 누가 띄우나?
→ Kubernetes 는 못 띄운다. 자기가 아직 없으니까
```

### 그래서 선이 하나 그어진다

```text
                    [ API Server 가 살아나는 시점 ]
  ────────────────────────────────────────────────────────────

  선 아래 — Kubernetes 기능을 못 쓴다
    etcd / kube-apiserver / kube-controller-manager / kube-scheduler
    → 파일로 띄운다. Static Pod

  선 위 — Kubernetes 기능을 쓸 수 있다
    kube-proxy / calico-node / CSI node / 로그·모니터링 / 우리 앱
    → 오브젝트로 띄운다. 노드마다 필요하면 DaemonSet
```

**Static Pod 는 "Kubernetes 를 쓸 수 없는 구간" 을 위한 비상구다.** 그 구간이 끝나면 안 쓴다.

### kube-proxy 와 calico-node 는 왜 선 위인가

```text
[etcd / apiserver]     이것들이 없으면 클러스터가 존재하지 않는다

[kube-proxy]           하는 일이 "Service 정보를 받아 iptables 규칙을 만드는 것"
                       Service 정보를 어디서 받나? → API Server
                       → API Server 가 없으면 할 일 자체가 없다

[calico-node]          어느 Pod 가 어느 노드에 있는지를 API Server 에서 받는다
                       → 마찬가지다
```

### 선 위에서는 왜 DaemonSet 이 나은가

기술적으로는 calico-node 도 Static Pod 로 만들 수 있다. 노드 3대에 파일 3개를 놓으면 된다.

```text
[Static Pod 로 했다면]
  노드 추가   → 그 노드에 접속해 파일 복사
  버전 업     → 노드마다 들어가 파일 수정
  상태 확인   → kubectl 로 보이지만 고칠 수는 없다
  롤백        → 기록이 없다. 손으로 옛 파일을 찾는다

[DaemonSet]
  노드 추가   → 자동
  버전 업     → yaml 한 장 + 롤링 갱신
  상태 확인   → kubectl get ds
  롤백        → ControllerRevision
```

**"가능하면 DaemonSet, 어쩔 수 없을 때만 Static Pod."**

### 부팅 순서

```text
  [1] systemd 가 kubelet 시작
        kubelet 은 API Server 없이도 동작한다. 파일만 읽으면 되니까
  [2] /etc/kubernetes/manifests 를 읽어 Static Pod 4개를 띄운다
  [3] API Server 가 응답한다
  ══════════════ 여기가 선이다 ══════════════
  [4] kubelet 이 노드를 등록한다
  [5] 컨트롤러들이 시작. DaemonSet 컨트롤러가 Pod 오브젝트를 만든다
  [6] 각 노드 kubelet 이 kube-proxy / calico-node 를 실행한다
  [7] 일반 Pod 가 뜰 수 있다 (CoreDNS, 우리 앱)
```

**1단계에서 겪은 순서다.**

```text
kubeadm init 직후   NotReady / CoreDNS Pending   → [6]이 안 끝났다
Calico 설치 후      Ready / CoreDNS Running       → [6] 완료, [7]로
```

### 한 장으로

```text
                     Static Pod              DaemonSet
  ──────────────────────────────────────────────────────────────
  언제 필요한가        API Server 이전         API Server 이후
  정의가 어디 있나     각 노드의 파일          etcd (클러스터 공유)
  누가 만드나          그 노드의 kubelet       DaemonSet 컨트롤러
  노드를 추가하면      손으로 파일 복사        자동
  버전을 올리려면      노드마다 파일 수정       yaml 한 장 + 롤링 갱신
  kubectl 로 고칠 수  없다 (사본만 보인다)     있다
  ──────────────────────────────────────────────────────────────
  대상               etcd, apiserver,        kube-proxy, CNI, CSI node,
                     controller-manager,     로그·모니터링 에이전트
                     scheduler
```

7절에서 이 그림을 실측으로 확인한다.

---

## 0-B. DaemonSet 이 푸는 문제

```text
"모든 노드에 하나씩" 이 필요한 것들이 있다

  네트워크 플러그인    노드마다 라우팅과 방화벽 규칙을 깔아야 한다
  kube-proxy          노드마다 Service 규칙을 깔아야 한다
  로그 수집기          노드마다 /var/log 를 읽어야 한다
  모니터링 에이전트     노드마다 CPU·메모리를 재야 한다   ← 5단계 node_exporter
  스토리지 드라이버     노드마다 볼륨을 붙여야 한다      ← 09편의 CSI node 플러그인
```

```text
[Deployment 로 안 되는 이유]
  replicas: 3 이라고 적으면 3개가 뜬다
  그런데 한 노드에 2개, 다른 노드에 1개가 뜰 수 있다
  → "노드마다 정확히 하나" 를 보장할 수 없다

  노드를 한 대 추가하면 replicas 를 손으로 4로 고쳐야 한다
```

### 개수를 세는 방식이 다르다

```text
Deployment    spec.replicas 로 개수를 정한다      사람이 정한다
StatefulSet   spec.replicas 로 개수를 정한다      사람이 정한다
DaemonSet     replicas 필드가 없다               ★
              노드를 세서 status 에 적는다
```

---

## 1. 실험 A — 이미 도는 것을 연다

```bash
kubectl get daemonset -A
kubectl -n kube-system get ds kube-proxy -o yaml | grep -A5 'replicas'
kubectl -n kube-system get ds kube-proxy -o jsonpath='{.status}' | tr ',' '\n'
```

```text
NAMESPACE     NAME          DESIRED   CURRENT   READY   NODE SELECTOR            AGE
kube-system   calico-node   3         3         3       kubernetes.io/os=linux   20d
kube-system   kube-proxy    3         3         3       kubernetes.io/os=linux   20d
```

`replicas` 를 grep 한 결과는 **비어 있었다.**

```text
{"currentNumberScheduled":3
 "desiredNumberScheduled":3      ← 우리가 3이라고 적은 적이 없다
 "numberAvailable":3
 "numberMisscheduled":0
 "numberReady":3
 "observedGeneration":3
 "updatedNumberScheduled":3}
```

**발견 1.** `spec` 에 개수가 없다. `status.desiredNumberScheduled` 는 **계산 결과**다.

```text
이름 자체가 그 뜻이다
  desiredNumberScheduled = "배치되어야 하는 것으로 계산된 수"
  replicas               = "내가 원하는 수"
```

**발견 2.** `numberMisscheduled` 라는 필드가 있다 — "있으면 안 되는 곳에 떠 있는 Pod 수".

```text
Deployment 에는 이런 개념이 없다
"어디에 있느냐" 가 의미를 갖는 워크로드라서 생긴 필드다
```

### 발견 2-B. ★ "노드마다 하나씩" 은 yaml 에 없다 — kind 한 줄이다

kube-proxy 의 정의를 열어보면 `spec` 안에 그런 설정이 아무 데도 없다.

```yaml
spec:
  revisionHistoryLimit: 10
  selector:
    matchLabels:
      k8s-app: kube-proxy
  template:
    ...
```

답은 맨 위 두 줄에 있다.

```yaml
apiVersion: apps/v1
kind: DaemonSet          ← 이 한 줄이 전부다
```

```yaml
# Deployment                          # DaemonSet
apiVersion: apps/v1                   apiVersion: apps/v1
kind: Deployment                      kind: DaemonSet          ← 여기만 다르다
spec:                                 spec:
  replicas: 4                           (없다)                 ← 그리고 이것뿐
  selector: ...                         selector: ...
  template: ...                         template: ...
```

```text
[우리가 적는 것]     무엇을 원하는가
[컨트롤러가 아는 것]  어떻게 달성하는가

API Server 가 kind 를 보고 담당 컨트롤러를 정한다
그 방식은 yaml 에 없다. 컨트롤러 코드에 들어 있다
```

`replicas` 를 적으면 규격에 없는 필드라 거부된다.

```bash
kubectl apply -f /tmp/bad-ds.yaml --dry-run=server
error: ... unknown field "spec.replicas"
```

```text
[이 프로젝트에서 반복되는 원리]
  Deployment    "4개"           → 어디에 둘지는 스케줄러가
  StatefulSet   "3개, 순서대로"   → 이름과 볼륨 연결은 컨트롤러가
  DaemonSet     (개수 없음)      → 노드를 세는 건 컨트롤러가
  PVC           "10Gi RWO"      → 어느 디스크인지는 컨트롤러가
```

### 발견 3. ★ hostNetwork — Pod IP 가 노드 IP다

```text
calico-node-5khhz    192.168.8.143    master01
calico-node-bsg58    192.168.8.142    worker01
kube-proxy-6284z     192.168.8.143    master01

db-0                 10.244.5.8       worker01     ← 대조군
coredns-7d764666f9   10.244.5.13      worker01
```

```bash
kubectl -n kube-system get ds kube-proxy -o jsonpath='{.spec.template.spec.hostNetwork}'
true
```

```text
[calico-node 가 hostNetwork 여야 하는 이유]
  이 Pod 가 하는 일이 "노드에 CNI 를 설치하는 것" 이다
  그런데 Pod 가 IP 를 받으려면 CNI 가 이미 있어야 한다
  → 닭과 달걀 → 자기는 CNI 없이 노드 네트워크를 쓴다

[kube-proxy 가 hostNetwork 여야 하는 이유]
  이 Pod 가 하는 일이 "노드의 iptables 규칙을 만드는 것" 이다
  자기만의 네트워크 네임스페이스 안에서 규칙을 만들면 소용이 없다
  → 노드의 네트워크 네임스페이스 안에 있어야 한다
```

**07(NetworkPolicy)편에서 흘린 이야기가 여기서 회수된다.**

```text
[07 편의 발견]
  "hostNetwork Pod 는 NetworkPolicy 가 적용되지 않는다"
  그때는 그런 게 있다고만 적고 넘어갔다

[지금]
  그 실물이 눈앞에 있다 — calico-node 와 kube-proxy 가 그것이다
```

### 발견 4. 이름 규칙이 셋 다 다르다

```text
web-769d9cfbdb-clrpr    Deployment    해시 두 겹 (ReplicaSet 해시 + 랜덤)
db-0                    StatefulSet   순번
calico-node-5khhz       DaemonSet     랜덤 하나
```

```text
DaemonSet 은 순번이 필요 없다
"어느 노드냐" 가 곧 신원이기 때문이다 → 이름은 아무래도 상관없다
```

### 발견 5. 10편 장애 실험의 흔적이 남아 있다

```text
calico-node-flq4d   RESTARTS 2 (2d18h ago)   worker02
kube-proxy-79w2l    RESTARTS 1 (2d18h ago)   worker02
```

worker02 전원을 내렸던 시각이다. **다른 노드의 것들은 재시작 기록이 없다.** 장애가 그 노드에만 국한됐다는 증거다.

---

## 2. master01 의 벽을 어떻게 뚫었나

```bash
kubectl describe node master01 | grep -A3 Taints
kubectl -n kube-system get ds kube-proxy -o jsonpath='{.spec.template.spec.tolerations}'
```

```text
Taints:  node-role.kubernetes.io/control-plane:NoSchedule

tolerations:  [{"operator":"Exists"}]
```

### 발견 6. ★ 이 toleration 은 "전부 무시" 다

```text
[보통의 toleration]
  key: node-role.kubernetes.io/control-plane
  effect: NoSchedule
  → "이 표시 하나만 무시한다"

[kube-proxy 의 것]
  operator: Exists 만 있다
  key 가 없다     → 모든 key 에 해당
  effect 가 없다  → 모든 effect 에 해당
  → "어떤 표시든 전부 무시한다"
```

### 발견 6-B. ★★ 그런데 tolerations 도 자동 주입된다 — 앞의 해석이 틀렸다

> **정정.** 이 문서 초판에는 "kube-proxy 는 `operator: Exists` 라서 노드가 죽어도
> 안 쫓겨난다" 고 적었다. **틀렸다.** 그건 kube-proxy 만의 특권이 아니다.

`log-agent` 로 확인했다. **우리가 적은 것과 실제 Pod 에 들어 있는 것이 다르다.**

```bash
kubectl -n k8s-lab get ds  log-agent -o jsonpath='{.spec.template.spec.tolerations}'
kubectl -n k8s-lab get pod -l app=log-agent -o jsonpath='{.items[0].spec.tolerations}'
```

```text
[우리가 yaml 에 적은 것]   1개
  node-role.kubernetes.io/control-plane : NoSchedule

[실제 Pod 에 들어 있는 것]  7개
  node-role.kubernetes.io/control-plane : NoSchedule    ← 우리 것
  node.kubernetes.io/not-ready          : NoExecute     ← 자동
  node.kubernetes.io/unreachable        : NoExecute     ← 자동  ★
  node.kubernetes.io/disk-pressure      : NoSchedule    ← 자동
  node.kubernetes.io/memory-pressure    : NoSchedule    ← 자동
  node.kubernetes.io/pid-pressure       : NoSchedule    ← 자동
  node.kubernetes.io/unschedulable      : NoSchedule    ← 자동
```

**발견 7의 nodeAffinity 와 같은 방식이다.** DaemonSet 컨트롤러가 Pod 를 만들면서 넣는다.

```text
[정정된 결론]
  모든 DaemonSet Pod 가 노드 장애에도 축출되지 않는다
  우리가 tolerations 에 뭘 적든 상관없다

  10편에서 calico-node 와 kube-proxy 가 살아남은 것은
  kube-proxy 가 특별해서가 아니라 DaemonSet 이라서였다
```

### 왜 축출하지 않는가 — 축출이 무의미하기 때문이다

```text
[일반 Pod]
  노드가 죽었다 → 다른 노드로 옮기면 서비스가 이어진다 → 축출할 이유가 있다

[DaemonSet Pod]
  그 노드 전용이다. 다른 노드로 옮길 수가 없다
  → 축출해봐야 갈 곳이 없다 → 축출 자체가 무의미하다
```

```text
그리고 노드가 살아나면
  Pod 를 두었으면  → 바로 이어서 동작한다
  축출했으면       → 다시 만들어야 한다
```

10편 실측과 정확히 맞는다.

```text
worker02 를 다시 켰을 때
  web Pod (Deployment)  유령이 정리되고 새것은 이미 다른 노드에 있었다
  calico-node           RESTARTS 만 올라갔다. Pod 는 그대로였다
```

### 사용자가 쓰는 toleration 의 역할은 다르다

```text
[우리가 쓰는 것]     주로 NoSchedule — "어느 노드에 배치될 수 있는가"
[자동으로 붙는 것]   주로 NoExecute  — "노드에 문제가 생겨도 남는가"
                                       DaemonSet 이면 무조건 남는다
```

### 발견 6-C. ★ hostNetwork 인 것만 하나 더 붙는다

```text
[log-agent]   7개
[kube-proxy]  8개

차이:  node.kubernetes.io/network-unavailable : NoSchedule
```

```text
network-unavailable 은 "이 노드의 CNI 가 아직 준비 안 됐다" 는 표시다

  일반 Pod     CNI 가 없으면 IP 를 못 받는다 → 뜨면 안 된다 → 걸려야 맞다
  hostNetwork  CNI 가 필요 없다 → 걸릴 이유가 없다 → 자동 면제
```

**발견 3의 닭과 달걀이 여기서 한 번 더 나온다.**

```text
calico-node 가 바로 그 CNI 를 설치하는 Pod 다
이 면제가 없으면 영원히 못 뜬다 — 자기가 자기를 막는다
→ hostNetwork 하나로 두 가지를 동시에 해결한다
```

### calico-node 는 표현이 다를 뿐 같은 효과다

```text
[kube-proxy]   {"operator":"Exists"}                        한 줄로 전부

[calico-node]  {"effect":"NoSchedule","operator":"Exists"}    모든 NoSchedule
               {"key":"CriticalAddonsOnly","operator":"Exists"}
               {"effect":"NoExecute","operator":"Exists"}     모든 NoExecute
```

```text
CriticalAddonsOnly 는 "핵심 애드온만 허용" 이라고 표시된 노드용이다
관리자가 특정 노드를 그렇게 막아둘 때 쓴다
```

### 용어 정리 — taint 와 toleration 은 방향이 반대다

```text
taint       노드에 붙는다      "여기 오지 마"          밀어내는 힘
toleration  Pod 에 붙는다      "나는 그거 견딜 수 있어"  그 힘을 견디는 능력

  방에 "여기 담배 연기 있음" 표시가 붙어 있다        ← taint
  "나는 괜찮습니다" 하는 사람만 들어간다             ← toleration
```

**toleration 은 허가증이지 지시가 아니다.**

```text
[노드가 주도 — taint / toleration]     노드가 문지기. Pod 는 통과 자격만 갖는다
[Pod 가 주도 — nodeSelector / affinity] Pod 가 스스로 고른다
```

---

## 3. 발견 7. ★★ 스케줄러를 거친다

```bash
kubectl -n kube-system get pod kube-proxy-6284z -o jsonpath='{.spec.affinity}'
```

```json
{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":
  {"nodeSelectorTerms":[{"matchFields":[
    {"key":"metadata.name","operator":"In","values":["master01"]}]}]}}}
```

**우리가 적은 적이 없다. DaemonSet 컨트롤러가 박아 넣은 것이다.**

```text
[가설 A — 틀렸다]  DaemonSet 이 nodeName 을 직접 적어 스케줄러를 건너뛴다
[가설 B — 맞았다]  "이 노드에만" 조건을 박고 스케줄러가 그걸 보고 배치한다
```

```text
[왜 중요한가]
  스케줄러를 거치므로 스케줄러의 다른 판단도 정상 적용된다
  노드에 메모리가 부족하면 → 그 노드의 DaemonSet Pod 는 Pending 이 된다
  → 조용히 실패하지 않고 이유가 이벤트로 남는다
```

10편에서 `nodeName` 을 직접 적어 스케줄러를 건너뛴 적이 있는데, **DaemonSet 은 그 방식을 안 쓴다.**

> 예전 버전에서는 DaemonSet 컨트롤러가 `nodeName` 을 직접 박았다가 스케줄러를 쓰도록 바뀐 것으로 알고 있다. **학습 데이터 기준이며 확인하지 않았다.**

### matchFields 라는 것이 나왔다

```text
[matchExpressions]  노드의 라벨을 본다
                    kubernetes.io/hostname = worker01
                    → 10편 local PV 의 nodeAffinity 가 이 방식이었다

[matchFields]       오브젝트의 필드를 본다
                    metadata.name = master01
```

```text
라벨은 사람이 바꿀 수 있다. 이름은 못 바꾼다
→ "정확히 그 노드 하나" 를 지목하는 데는 이쪽이 확실하다
```

### 실측 — Pod 마다 다른 노드가 박혀 있다

```text
log-agent-t5xpv    metadata.name In [master01]
log-agent-mr5nw    metadata.name In [worker01]
log-agent-n66n7    metadata.name In [worker02]
```

**"노드마다 하나씩" 이라는 개념이 실제로는 "각 노드 전용 Pod 를 하나씩 만든다" 로 구현돼 있다.**

### 발견 8. 컨트롤러가 한 단이다

```json
[{"kind":"DaemonSet","name":"kube-proxy","controller":true,...}]
```

```text
Deployment    → ReplicaSet 오브젝트 → Pod 오브젝트     2단
StatefulSet   → Pod 오브젝트                          1단
DaemonSet     → Pod 오브젝트                          1단
```

> **주의 — 이 "단" 은 오브젝트를 몇 번 거치느냐일 뿐이다.**
>
> ```text
> [층 1] 오브젝트를 만드는 층   ← "1단/2단" 은 여기 안에서의 이야기다
>        컨트롤러가 etcd 에 "이런 Pod 가 있어야 한다" 를 기록한다
>        이 시점에 컨테이너는 하나도 없다
>
> [층 2] 배치를 정하는 층       ← 셋 다 동일
>        Scheduler 가 비어 있는 nodeName 을 채운다
>
> [층 3] 실제로 만드는 층       ← 셋 다 동일
>        kubelet → CRI → containerd → runc → 컨테이너
> ```
>
> 컨트롤러들은 `kube-controller-manager` **하나의 프로세스 안**에서 돈다.
> `statefulset-controller` 나 `daemonset-controller` 라는 Pod 는 없다.
> master01 의 컨트롤러가 worker02 의 containerd 에 명령할 방법도 없고, 그럴 필요도 없다.
> 컨트롤러도 kubelet 도 오직 API Server 하고만 대화한다.

---

## 3-B. 같은 template 하나로 노드마다 다르게

kube-proxy 의 정의에 이런 게 있다.

```yaml
- --hostname-override=$(NODE_NAME)
env:
- name: NODE_NAME
  valueFrom:
    fieldRef:
      apiVersion: v1
      fieldPath: spec.nodeName
```

**발견 7-B.** template 은 하나인데 각 kube-proxy 는 자기가 어느 노드인지 알아야 한다.

```text
template 에 노드 이름을 적을 수가 없다. 하나뿐이니까

[해결]  "네 Pod 오브젝트의 spec.nodeName 값을 환경변수에 넣어라"
        master01 의 Pod → NODE_NAME=master01
        worker01 의 Pod → NODE_NAME=worker01

Downward API 라고 한다
```

**발견 7의 nodeAffinity 자동 주입과 짝이다.**

```text
nodeAffinity    Kubernetes 가 "이 Pod 는 어느 노드로" 를 정한다   → 배치용
NODE_NAME       그 값을 컨테이너 안 프로그램에게 알려준다          → 실행용
```

### 그 밖의 필드 — 지금까지 배운 것이 다 들어 있다

```yaml
hostNetwork: true                    # 발견 3. 노드의 네트워크 공간에서 iptables 를 만든다
nodeSelector:
  kubernetes.io/os: linux            # DESIRED 계산에 쓰인다. Windows 노드는 제외
serviceAccount: kube-proxy           # 08편. Service / EndpointSlice 를 읽어야 한다
securityContext:
  privileged: true                   # 커널 네트워크 설정을 만져야 한다
volumeMounts:
- mountPath: /run/xtables.lock       # iptables 동시 수정 방지 잠금. 노드 것을 공유한다
- mountPath: /lib/modules            # 커널 모듈이 로드돼 있는지 확인용 (readOnly)
- mountPath: /var/lib/kube-proxy     # 06편. ConfigMap 볼륨. 모드와 Pod 대역 설정
priorityClassName: system-node-critical   # 메모리 부족 시 거의 마지막까지 살아남는다
```

```text
시스템 컴포넌트라고 특별한 문법을 쓰는 게 아니다
우리가 쓰는 것과 똑같은 필드를 조합해 만들어져 있다
```

---

## 4. 실험 B — 직접 만든다

### manifest

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: log-agent
  namespace: k8s-lab
spec:
  selector:
    matchLabels:
      app: log-agent
  template:
    metadata:
      labels:
        app: log-agent
    spec:
      terminationGracePeriodSeconds: 5
      containers:
      - name: agent
        image: nginx:alpine
        command: ["sh", "-c", "sleep infinity"]
        volumeMounts:
        - name: varlog
          mountPath: /host/var/log
          readOnly: true
      volumes:
      - name: varlog
        hostPath:
          path: /var/log
```

```text
[없는 것]
  replicas       노드 수가 곧 개수다
  tolerations    일부러 뺐다. 1차 실험 대상이다
  hostNetwork    이 Pod 는 네트워크를 안 만지므로 필요 없다

[hostPath 로 /var/log 를 읽기 전용으로]
  09편에서는 "데이터를 저장하는 곳" 으로 썼다
  여기서는 "노드의 것을 읽는 창구" 로 쓴다
  → DaemonSet 이 hostPath 를 쓰는 건 이 용도가 대부분이다
```

### 발견 9. ★★ DESIRED 가 3이 아니라 2다

```text
NAME        DESIRED   CURRENT   READY   AGE
log-agent   0         0         0       0s      ← 만든 직후
log-agent   2         2         1       14s

log-agent-5nx6g   Running   worker01
log-agent-mvfrz   Running   worker02
                            master01 에는 없다
```

**하나가 Pending 인 게 아니라 아예 안 만들어졌다.**

```text
[10편 — StatefulSet]
  db-1 을 만들었다 → 갈 노드가 없다 → Pending 으로 남았다
  READY 2/3 → "하나 모자라다" 고 기록된다
  FailedScheduling 이벤트가 남았다

[DaemonSet]
  master01 용 Pod 를 아예 안 만들었다
  DESIRED 2 → "2개면 충분하다" 고 기록된다
  이벤트도 없다
```

```text
DaemonSet 컨트롤러는 Pod 를 만들기 전에 먼저 계산한다
  "내 tolerations 로 갈 수 있는 노드가 어디인가"
  master01 은 못 간다 → 대상에서 제외 → 개수가 2
```

**"실패했다" 가 아니라 "애초에 대상이 아니다" 다.**

```text
[운영에서 위험한 지점]
  DESIRED 와 READY 가 둘 다 2라서 아무 문제가 없어 보인다
  그런데 master01 에는 로그 수집기가 없다
  → 그 노드의 로그는 아무도 안 걷고 있다
  → 지표만 보면 정상이다
```

09편 hostPath 사고, 10편 발견 27 과 같은 성격이다. **조용히 빠진다.**

### 발견 10. DESIRED 가 처음엔 0이었다

```text
log-agent   0   0   0   0s      ← 만든 직후
log-agent   2   2   1   14s
```

```text
[Deployment 였다면]  spec.replicas: 4 를 우리가 적었으므로 처음부터 4다
[DaemonSet]          세어봐야 안다. 아직 안 셌으므로 0이다
```

### 발견 11. DaemonSet 도 ControllerRevision 을 쓴다

```text
NAME                     CONTROLLER                   REVISION   AGE
calico-node-74fd74974c   daemonset.apps/calico-node   1          20d
kube-proxy-6b8958ccc     daemonset.apps/kube-proxy    3          20d
kube-proxy-7d7486bcb6    daemonset.apps/kube-proxy    2          10d
```

```text
StatefulSet 과 같은 구조다
  컨트롤러는 Pod 를 직접 소유하고, 세대 기록은 ControllerRevision 이 맡는다
Deployment 만 ReplicaSet 이 두 역할을 겸한다
```

### 발견 12. ★ revision 번호는 순서이지 나이가 아니다

```text
REVISION 3 의 나이가 20일
REVISION 2 의 나이가 10일       ← 번호가 큰 쪽이 더 오래됐다
```

현재 도는 Pod 를 확인해 답이 나왔다.

```text
kube-proxy Pod 3개 전부   controller-revision-hash: 6b8958ccc
                          = kube-proxy-6b8958ccc = REVISION 3 = 20일 된 오브젝트
```

```text
[해석]
  20일 전  클러스터 구축. 원본 template     → 오브젝트 A (revision 1)
  10일 전  뭔가 바꿨다                      → 오브젝트 B (revision 2) 새로 생성
  그 뒤    원본으로 되돌렸다
           → 새 오브젝트를 만들지 않는다
           → 기존 A 의 번호를 3으로 올린다

ControllerRevision 은 template 내용으로 이름(해시)을 만든다
같은 내용이면 같은 이름 → 이미 있으면 재사용하고 번호만 갱신한다
```

두 revision 의 이미지는 같았다(`v1.35.7`). **10일 전에 이미지 외의 무엇이 바뀌었다가 되돌아온 것인데, 무엇인지는 확인하지 않았다.**

---

## 5. toleration 을 붙이면

```yaml
      tolerations:
      - key: node-role.kubernetes.io/control-plane
        operator: Exists
        effect: NoSchedule
```

**kube-proxy 의 것과 일부러 다르게 썼다.**

```text
[kube-proxy]  [{"operator":"Exists"}]     모든 taint 를 견딘다
[우리 것]     key 와 effect 를 명시        이 표시 하나만 견딘다
```

```text
[의도]
  로그 수집기는 kube-proxy 만큼 특권적일 이유가 없다
  → 배치 범위를 제어 노드 하나로만 넓힌다
```

> **다만 발견 6-B 를 보라.** 이렇게 좁게 써도 **노드 장애 시 거동은 달라지지 않는다.**
> `unreachable:NoExecute` 는 DaemonSet 컨트롤러가 자동으로 넣어주기 때문이다.
> 우리가 쓰는 toleration 은 **"어느 노드에 배치될 수 있는가"** 만 정한다.

### 결과 — DESIRED 3

```text
              master01 의 taint    Pod 의 toleration    결과
  일반 Pod         있다                없다              배치 안 됨
  kube-proxy       있다                모두 견딤          배치됨
  1차 log-agent    있다                없다              배치 안 됨   DESIRED 2
  2차 log-agent    있다                그것만 견딤        배치됨      DESIRED 3
```

### 발견 13. ★ 롤링업데이트는 먼저 지우고 나중에 만든다

```text
[1] master01 — 없던 것이라 그냥 생성
    log-agent-t5xpv   Pending → Running (47초, 이미지를 받아야 했다)

[2] worker01 — [1]이 끝난 뒤
    log-agent-5nx6g   Terminating       ← 옛것을 먼저 지운다
    log-agent-mr5nw   Pending → Running  2초

[3] worker02 — [2]가 끝난 뒤
    log-agent-mvfrz   Terminating
    log-agent-n66n7   Pending → Running  1초
```

```text
[Deployment 의 롤링업데이트]
  새 Pod 를 먼저 띄우고 → 준비되면 옛 것을 지운다 (maxSurge)
  → 잠깐 개수가 늘어난다

[DaemonSet]
  노드마다 하나인데 잠깐 둘을 띄울 수 없다
  → 옛 것을 지우고 → 새로 만든다 → 그 노드는 몇 초 비어 있다
```

**그리고 노드를 하나씩 처리한다.** 기본값이 `maxUnavailable: 1` 이다.

```text
로그 수집기가 한 노드에서 잠깐 빠지는 건 감수한다
그런데 세 노드에서 동시에 빠지면 그동안 로그를 아무도 안 걷는다
```

### 발견 14. ★ Error 와 Completed 를 가르는 것은 종료 코드다

```text
[10편]  db-1   0/1   Completed      nginx 그대로
[여기]  5nx6g  0/1   Error          nginx:alpine + command 덮어씀
```

```text
[10편]  nginx 가 종료 신호(SIGTERM)를 받고 정상 종료 절차를 밟는다
        → 종료 코드 0 → Completed

[여기]  command: ["sh","-c","sleep infinity"] 로 바꿨다
        sleep 은 종료 신호를 처리하는 코드가 없다
        → 신호에 의해 강제로 끝난다 → 코드 0 이 아니다 → Error
```

```text
Kubernetes 는 종료 코드만 본다
  0 이면 Completed / 0 이 아니면 Error
```

```text
[실무에서 왜 문제인가]
  종료 신호를 제대로 처리하지 않으면 Pod 를 지울 때마다 Error 로 기록된다
  → 정상 삭제인지 진짜 오류인지 구분이 안 된다
  → 5단계에서 알람을 걸 때 노이즈가 된다

  그리고 더 중요한 것
    종료 신호를 안 받는다는 건 "정리할 시간을 안 갖는다" 는 뜻이다
    DB 라면 쓰던 것을 마무리하지 못하고 죽는다
    → 09편에서 말한 저널·WAL 이 필요한 이유가 이것이다
```

---

## 6. 실험 C — 일부 노드에만 두기

```bash
kubectl label node worker01 log-collect=yes
```

```yaml
      nodeSelector:
        log-collect: "yes"
```

### 발견 15. ★ spec 이 먼저 바뀌고 status 가 뒤따른다

```text
DESIRED CURRENT READY UP-TO-DATE AVAILABLE  NODE SELECTOR
  3       3      3       3          3       <none>              이전
  3       3      3       3          3       log-collect=yes     ← spec 만 바뀌었다
  1       1      1       0          1       log-collect=yes     ← 계산 후
  1       1      0       1          0       log-collect=yes     ← 교체 중
  1       1      1       1          1       log-collect=yes     완료
```

```text
NODE SELECTOR   spec 이다. apply 한 즉시 반영된다
DESIRED         status 다. 컨트롤러가 다시 세어야 바뀐다
```

09편 PVC 바인딩에서 본 것과 같다.

```text
[09]   PVC   spec.volumeName 을 먼저 쓰고 → status 를 Bound 로
[여기] DS    spec.nodeSelector 가 먼저 바뀌고 → status 를 다시 계산
```

### 발견 16. UP-TO-DATE 가 3 → 0 으로 떨어졌다

```text
worker01 의 Pod 는 안 지워졌는데 UP-TO-DATE 가 0이 됐다

template 이 바뀌었기 때문이다
그 Pod 는 옛 template 으로 만들어졌다 → "낡은 것" 으로 분류된다
→ 지웠다가 다시 만든다
```

### 처리 순서

```text
[1] 대상에서 빠진 노드 정리      worker02, master01 의 Pod 삭제
[2] 남은 노드의 낡은 Pod 교체    worker01 의 Pod 를 지우고 새로 생성
```

### 발견 17. numberMisscheduled 는 못 잡았다

```text
1  1  0
1  1  0
1  1  0
1  1  0
```

```text
[추측]
  컨트롤러가 한 번의 처리 안에서
    "대상 아닌 Pod 를 지운다" 와 "status 를 갱신한다" 를 함께 한다
  → status 를 쓸 시점에는 이미 삭제 대상으로 표시돼 있어 안 세는 것으로 보인다
```

**확인하지 못했다.** 못 잡은 것도 결과다 — 정상적인 방법으로는 이 필드가 올라가는 걸 보기 어렵다는 뜻이다.

### 발견 18. ★★ 대상 노드가 없으면 모든 숫자가 0이고, 그게 "정상" 으로 보인다

```bash
kubectl label node worker01 log-collect-
```

```text
NAME        DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR
log-agent   0         0         0       0            0           log-collect=yes

kubectl -n k8s-lab get pod -l app=log-agent
No resources found in k8s-lab namespace.
```

```text
DESIRED 0 / READY 0 이면 숫자상 완전히 정상이다
그런데 로그 수집기가 한 대도 안 돌고 있다

→ 라벨 오타 하나로 전체가 사라져도 아무도 안 알려준다
```

**이 문서에서 가장 중요한 발견이다.**

```text
[같은 성격의 것들]
  09편   PVC 는 Bound 인데 데이터가 없다
  10편   db-1 이 6분째 멈춰 있는데 이벤트가 없다
  여기   DaemonSet 이 0개인데 모든 지표가 정상이다
```

```text
[5단계에서 감시해야 할 것]
  DaemonSet 은 "READY < DESIRED" 만 보면 안 된다
  DESIRED 자체가 0이거나 노드 수보다 적은 경우를 봐야 한다
  → kube_daemonset_status_desired_number_scheduled 와 노드 수를 비교
```

### 발견 19. Pod 를 지우면 즉시 같은 노드에 다시 생긴다

["노드당 하나" 의 보장]
19-B. 거름망이 아니라 컨트롤러가 세고 조정한다 ★★
    노드별로 Pod 수를 세어 0이면 만들고 2개 이상이면 지운다
    스케줄러는 개수를 안 센다. "이 하나가 여기 들어가나" 만 본다
19-C. selector 로 입양한다 ★
    같은 라벨의 Pod 를 손으로 만들었더니 6초 만에 ownerReference 가 붙고 지워졌다
    → 01편 ReplicaSet 과 같은 방식. 소유권은 라벨로 정해진다
    → 라벨이 남의 selector 와 겹치면 내 Pod 가 입양당하고 지워진다
    → 오래된 쪽이 살아남았다 (규칙이 나이순인지는 미확인)
19-D. 삭제 지시가 와도 만들던 컨테이너는 끝까지 만든 뒤 종료된다
    이미지·볼륨·네트워크는 되돌릴 수 없는 작업이라 중간에 못 멈춘다
19-E. numberMisscheduled 는 "대상 아닌 노드에 있는 것" 만 센다
    "대상 노드의 초과분" 은 세지 않는다 → 발견 17의 조건이 좁혀졌다

```text
kubectl delete pod -l app=log-agent
→ log-agent-lcqmb   ContainerCreating   worker01   1s
→ log-agent-lcqmb   Running             worker01   8s
```

---

## 6-B. "노드당 하나" 를 무엇이 보장하는가 ★★

거름망(스케줄러)이 하는 일이 아니다. **컨트롤러가 세고 조정한다.**

### 컨트롤러가 매번 하는 일

```text
  1. 노드 목록을 가져온다
  2. selector 로 내 Pod 를 모은다              ← 여기서 입양이 일어난다
  3. Pod 를 노드 이름(spec.nodeName)으로 묶는다
  4. 노드마다 판정한다

  이 노드가 대상인가?    그 노드의 Pod 수    할 일
  ──────────────────────────────────────────────────────
      대상이다                 0개          1개 만든다
      대상이다                 1개          그대로 둔다
      대상이다               2개 이상       초과분을 지운다
    대상이 아니다              0개          아무것도 안 한다
    대상이 아니다             1개 이상      전부 지운다  (misscheduled)
```

```text
"이 노드가 대상인가" 의 기준
  tolerations 로 그 노드의 taint 를 견디는가
  nodeSelector 조건에 맞는가
  template 의 nodeAffinity 에 맞는가

→ 대상인 노드 수 = desiredNumberScheduled
```

### 두 겹으로 보장된다

```text
[겹 1] 컨트롤러가 노드당 1개만 만든다. 2개면 지운다
[겹 2] 만든 Pod 에 nodeAffinity 를 박아 그 노드로만 가게 한다 (발견 7)
```

```text
[겹 2 가 없으면 어떻게 되나]
  컨트롤러  "worker01 에 없네" → Pod 하나 만든다
  스케줄러  조건이 없으니 worker02 로 보낸다
  컨트롤러  "worker01 에 아직 없네" → 또 만든다
            "worker02 에는 2개네" → 하나 지운다
  → 영원히 안 끝난다
```

### 실측 — 같은 라벨의 Pod 를 손으로 하나 더 만들어봤다

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: fake-agent
  namespace: k8s-lab
  labels:
    app: log-agent          # DaemonSet 의 selector 와 같게
spec:
  nodeName: worker01        # 이미 log-agent 가 있는 노드
  containers:
  - name: agent
    image: nginx:alpine
    command: ["sh", "-c", "sleep infinity"]
```

```text
fake-agent   Pending              1s
fake-agent   ContainerCreating    4s
fake-agent   Terminating          6s      ← 6초 만에 지우기로 결정
```

**발견 19-B. ★★ selector 로 입양한 뒤 초과분을 지운다.**

```json
kubectl -n k8s-lab get pod fake-agent -o jsonpath='{.metadata.ownerReferences}'
[{"kind":"DaemonSet","name":"log-agent","controller":true,
  "uid":"bcfee406-2cb1-4bf8-be11-0fc3958c517a","blockOwnerDeletion":true}]
```

```text
Events:
  Normal  SuccessfulDelete  30s  daemonset-controller  Deleted pod: fake-agent
```

```text
우리가 만든 Pod 인데 DaemonSet 이 자기 것으로 가져갔다
→ 01편(ReplicaSet)에서 확인한 것과 같은 방식이다
   소유권은 "누가 만들었나" 가 아니라 "라벨이 맞나" 로 정해진다
```

```text
[실무 함의]
  라벨을 함부로 붙이면 안 된다
  기존 컨트롤러의 selector 와 겹치면
  → 내 Pod 가 남의 컨트롤러에 입양당하고
  → 개수가 안 맞으면 지워진다
```

**어느 쪽이 살아남았나.**

```text
log-agent-lcqmb   3h47m   살아남았다
fake-agent        6s      지워졌다
```

오래된 쪽이 남았다. **다만 규칙이 정확히 "나이순" 인지는 확인하지 않았다.**

### 발견 19-C. 삭제 지시가 와도 만들던 컨테이너는 끝까지 만든다

```text
fake-agent   0/1   ContainerCreating    5s
fake-agent   0/1   Terminating          6s     ← 여기서 삭제 지시
fake-agent   1/1   Terminating         45s     ← 그런데 1/1 이 됐고 IP 도 받았다
fake-agent   0/1   Error               52s
```

```text
kubelet 이 컨테이너를 만들던 중이었다
삭제 지시가 와도 중간에 못 멈춘다 → 끝까지 만들고 → 종료 절차를 밟는다

이미지 받기 / 볼륨 붙이기 / 네트워크 설정은 되돌릴 수 없는 작업이다
중간에 끊으면 노드에 찌꺼기가 남는다
```

### 발견 19-D. numberMisscheduled 의 조건이 좁혀졌다

이번에도 `numberMisscheduled` 는 0이었다. **두 경우가 다르기 때문이다.**

```text
[misscheduled]   대상이 아닌 노드에 내 Pod 가 있다
[이번 경우]      대상인 노드에 Pod 가 2개다 → 잘못된 위치가 아니라 초과다
```

발견 17의 미확인 항목이 여기서 한 단계 좁혀졌다.

### 정리 — 층이 둘이다

```text
컨트롤러   몇 개 있어야 하는가        → 개수를 맞춘다 (만들고 지운다)
스케줄러   이 하나가 여기 들어가는가   → 자격을 본다 (taint / 리소스 / 볼륨)

거름망은 개수를 세지 않는다
```

---

## 7. 실험 D — Static Pod 와 비교

```bash
sudo ls -l /etc/kubernetes/manifests/
kubectl -n kube-system get pod etcd-master01 -o jsonpath='{.metadata.ownerReferences}'
kubectl -n kube-system get pod etcd-master01 -o jsonpath='{.metadata.annotations.kubernetes\.io/config\.mirror}'
kubectl get ds -A
```

```text
-rw------- 1 root root 2602 Aug  3 17:11 etcd.yaml
-rw------- 1 root root 3959 Aug  3 17:11 kube-apiserver.yaml
-rw------- 1 root root 3458 Aug  3 17:11 kube-controller-manager.yaml
-rw------- 1 root root 1726 Aug  3 17:11 kube-scheduler.yaml
```

### 발견 20. ★★ Static Pod 의 주인은 Node 다

```json
[{"apiVersion":"v1","controller":true,"kind":"Node","name":"master01",...}]
```

```text
web-clrpr        주인이 ReplicaSet
db-0             주인이 StatefulSet
log-agent-w8hmt  주인이 DaemonSet
etcd-master01    주인이 Node            ★ 컨트롤러가 아니다
```

**어떤 컨트롤러도 이 Pod 를 관리하지 않는다. 노드 자신이 소유한다.**

### 방향이 반대다

```text
[일반 Pod]
  컨트롤러가 Pod 오브젝트를 만든다 → 스케줄러가 노드를 정한다
  → kubelet 이 그걸 보고 컨테이너를 만든다
  오브젝트가 먼저, 컨테이너가 나중

[Static Pod]
  kubelet 이 /etc/kubernetes/manifests 파일을 읽는다 → 바로 컨테이너를 만든다
  → 그러고 나서 API Server 에 "이런 게 돌고 있습니다" 하고 등록한다
  컨테이너가 먼저, 오브젝트가 나중
```

```text
그 오브젝트를 mirror Pod 라고 한다. 실물의 그림자일 뿐이다
config.mirror: 4014eb7abb6fb0c28f2dbaded53072fd   ← 06편에서 본 그것

kubectl delete pod etcd-master01 을 해도
  → 그림자만 지워진다 → 실제 컨테이너는 안 죽는다 → kubelet 이 다시 만든다
```

### 발견 21. ★★ 왜 DaemonSet 이 아닌가 — 부팅 순서다

```text
DaemonSet 컨트롤러는 kube-controller-manager 안에서 돈다
그 컨트롤러는 API Server 에 요청을 보낸다

그럼 API Server 를 DaemonSet 으로 만들면?
→ API Server 를 띄우려면 API Server 가 필요하다
```

```text
[부팅 순서]

  1. kubelet 시작
       API Server 없이도 동작한다. 파일만 읽으면 되니까
  2. /etc/kubernetes/manifests 를 읽어 Static Pod 를 띄운다
       etcd / kube-apiserver / kube-controller-manager / kube-scheduler
  3. API Server 가 산다
     ─────────── 이 선을 넘어야 아래가 가능하다 ───────────
  4. 컨트롤러들이 동작을 시작한다
  5. DaemonSet 컨트롤러가 Pod 오브젝트를 만든다
  6. kube-proxy / calico-node 가 뜬다
```

```text
Static Pod    "API Server 이전" 에 필요한 것
DaemonSet     "API Server 이후" 에 필요한 것
```

`get ds -A` 에 apiserver 와 etcd 가 없는 이유가 이것이다.

### 발견 21-B. ★ 정의가 사는 곳도 다르다 — GitOps 로 이어진다

```text
[Static Pod]
  /etc/kubernetes/manifests/etcd.yaml
  → 파일이 원본이다. 지우면 컨테이너가 죽는다. 항상 그 자리에 있어야 한다

[DaemonSet]
  apply 하는 순간 etcd 에 들어간다
  → 그 뒤로는 etcd 가 원본이다. 파일은 지워도 아무 일도 안 일어난다
```

**`apply` 는 "이 내용을 등록해줘" 이지 "이 파일을 계속 봐줘" 가 아니다.**

```text
그래서 kube-proxy 의 yaml 파일은 아예 없다
  kubeadm 이 내부 template 으로 만들어 apply 하고 끝냈다
  정의를 보려면 etcd 에서 꺼낸다

  kubectl -n kube-system get ds kube-proxy -o yaml
```

꺼낸 것을 그대로 다시 쓰면 안 된다.

```text
[우리가 쓴 것]       spec.template ...
[클러스터가 붙인 것]   metadata.uid / resourceVersion / creationTimestamp / generation
                     status: {...}

"우리가 원한 것" 과 "지금 상태" 가 섞여 있다
```

**여기서 문제가 하나 남는다.**

```text
클러스터에 등록은 됐는데 그 정의의 원본이 아무 데도 없다
  누가 언제 왜 바꿨는지 모른다
  클러스터를 새로 만들면 다시 만들 수가 없다
```

```text
지금 우리 클러스터의 상태
  kube-proxy    kubeadm 이 만들었고 파일이 없다
  calico-node   1단계에서 받은 파일이 남아 있는지 불확실
  나머지        ~/manifests 에 있으나 master01 한 대뿐이다
```

**4단계 GitOps 의 출발점이다.** 로드맵의 이 칸을 거기서 채운다.

```text
코드 변경 → 테스트 → 이미지 생성 → Registry 저장 → GitOps 선언 변경
                                                    ^^^^^^^^^^^^^^
```

### 같은 문제를 다른 층에서 두 번 푼다

```text
Static Pod    apiserver 를 띄우려면 apiserver 가 필요 → 파일로 우회
hostNetwork   CNI 를 설치하려면 CNI 가 필요 → 노드 네트워크로 우회
```

---

## 정리

```text
[큰 틀]
 0. Kubernetes 의 관리 기능은 전부 API Server 를 거친다
    → API Server 자체는 Kubernetes 로 못 띄운다 → 선이 그어진다
    선 아래  Static Pod. 파일이 원본. 넷뿐이다
    선 위    DaemonSet 등. etcd 가 원본
    kube-proxy / calico-node 는 API Server 에서 정보를 받아야 하므로 선 위다

[개수]
 1. replicas 필드가 없다. status.desiredNumberScheduled 는 계산 결과다
 1-B. "노드마다 하나씩" 은 yaml 에 없다. kind 한 줄이 정한다 ★
    선언에는 "무엇을", 컨트롤러가 "어떻게" 를 안다
    replicas 를 적으면 unknown field 로 거부된다
 2. numberMisscheduled 라는 필드가 있다 — "있으면 안 되는 곳의 Pod 수"
 3. 만든 직후에는 DESIRED 가 0이다. 세어봐야 알기 때문이다
 3-B. Downward API 로 자기 노드 이름을 환경변수로 받는다
    nodeAffinity 는 배치용, NODE_NAME 은 실행용. 짝이다

[네트워크]
 4. calico-node / kube-proxy 는 hostNetwork 다. Pod IP 가 노드 IP 다 ★
    CNI 를 설치하려면 CNI 가 필요한 문제를 이렇게 푼다
    07편의 "hostNetwork Pod 는 NetworkPolicy 가 안 걸린다" 의 실물이다

[배치]
 5. taint 는 노드에, toleration 은 Pod 에 붙는다. 견뎌야 들어간다
 6. tolerations 도 자동 주입된다 ★★  (초판 정정)
    우리가 1개 적었는데 Pod 에는 7개가 들어 있었다
    unreachable:NoExecute 가 포함되므로 모든 DaemonSet Pod 가 축출되지 않는다
    → kube-proxy 가 특별해서가 아니라 DaemonSet 이라서였다
    → 축출해봐야 갈 곳이 없으니 축출 자체가 무의미하다
    → 우리가 쓰는 toleration 은 "배치 범위" 만 정한다
 6-B. hostNetwork 인 Pod 에만 network-unavailable 면제가 하나 더 붙는다 ★
    CNI 를 설치하는 Pod 가 CNI 없다고 막히면 영원히 못 뜬다
 7. DaemonSet 이 Pod 마다 nodeAffinity 를 박아 넣는다 ★★
    matchFields metadata.name — 라벨이 아니라 오브젝트 이름을 본다
    → 스케줄러를 건너뛰지 않는다. 리소스 부족 같은 판단도 정상 적용된다
 8. toleration 이 없으면 Pending 이 아니라 아예 안 만든다 ★★
    10편은 Pending 으로 남아 READY 2/3 이었다. 여기는 DESIRED 2다
    "실패" 가 아니라 "대상이 아님" 이다

[갱신]
 9. 컨트롤러는 1단이다. 세대 기록은 ControllerRevision 이 맡는다
10. revision 번호는 순서이지 나이가 아니다 ★
    같은 template 이면 오브젝트를 재사용하고 번호만 올린다
11. 롤링업데이트는 먼저 지우고 나중에 만든다 (Deployment 와 반대)
    노드를 하나씩 처리한다 (maxUnavailable: 1)
12. spec 이 먼저 바뀌고 status 가 뒤따른다
    NODE SELECTOR 는 바뀌었는데 DESIRED 는 아직 3이었던 순간이 있다
13. template 이 바뀌면 살아 있는 Pod 도 UP-TO-DATE 에서 빠진다

[조용한 실패]
14. 대상 노드가 없으면 모든 숫자가 0이고 그게 정상으로 보인다 ★★★
    라벨 오타 하나로 전체가 사라져도 아무도 안 알려준다
    → 5단계에서 DESIRED 자체를 노드 수와 비교해야 한다
15. numberMisscheduled 가 올라가는 순간은 잡지 못했다

[종료]
16. Error 와 Completed 를 가르는 것은 종료 코드다 ★
    command 를 sleep 으로 덮으면 종료 신호를 처리하지 못해 Error 가 된다
    → 정상 삭제와 진짜 오류가 구분되지 않는다

[Static Pod]
17. Static Pod 의 주인은 Node 다. 어떤 컨트롤러도 관리하지 않는다 ★★
18. 방향이 반대다. 컨테이너가 먼저 생기고 오브젝트(mirror)가 나중에 등록된다
19. etcd / apiserver 가 DaemonSet 이 아닌 이유는 부팅 순서다 ★★
    API Server 이전에 필요한 것은 파일로, 이후에 필요한 것은 오브젝트로
19-B. 정의가 사는 곳도 다르다 ★
    Static Pod 는 파일이 원본, DaemonSet 은 etcd 가 원본
    → kube-proxy 는 yaml 파일이 아예 없다
    → "정의의 원본을 어디 둘 것인가" 문제가 남는다 → 4단계 GitOps
20. Static Pod 와 hostNetwork 는 같은 문제를 다른 층에서 푼 것이다
```

## 확인 명령

```bash
# 무엇이 도는가
kubectl get daemonset -A
kubectl -n kube-system get ds kube-proxy -o jsonpath='{.status}' | tr ',' '\n'

# 왜 그 노드에 있는가 / 없는가
kubectl describe node <노드> | grep -A3 Taints
kubectl -n <ns> get ds <이름> -o jsonpath='{.spec.template.spec.tolerations}'; echo
kubectl -n <ns> get ds <이름> -o jsonpath='{.spec.template.spec.nodeSelector}'; echo
kubectl get node -L <라벨키>

# DaemonSet 이 박아 넣은 조건
kubectl -n <ns> get pod <이름> -o jsonpath='{.spec.affinity}'; echo

# 네트워크
kubectl -n <ns> get ds <이름> -o jsonpath='{.spec.template.spec.hostNetwork}'; echo
kubectl get pod -A -o wide          # Pod IP 가 노드 IP 인지 본다

# 세대
kubectl -n <ns> get controllerrevision
kubectl -n <ns> get pod -l <셀렉터> -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.controller-revision-hash}{"\n"}{end}'

# Static Pod
sudo ls -l /etc/kubernetes/manifests/
kubectl -n kube-system get pod etcd-master01 -o jsonpath='{.metadata.ownerReferences}'; echo
kubectl -n kube-system get pod etcd-master01 -o jsonpath='{.metadata.annotations.kubernetes\.io/config\.mirror}'; echo
```

## 미확인

```text
 1. numberMisscheduled 가 실제로 올라가는 조건
 2. kube-proxy 의 revision 2 에서 무엇이 바뀌었는지 (이미지는 같았다)
 3. DaemonSet 컨트롤러가 예전에 nodeName 을 직접 박았다가 바뀐 것이 맞는지
 4. 노드 리소스가 부족할 때 DaemonSet Pod 가 정말 Pending 이 되는지 (미실행)
 5. updateStrategy: OnDelete 로 바꿨을 때의 동작 (미실행)
 6. maxUnavailable 을 2 이상으로 올렸을 때 정말 동시에 처리하는지 (미실행)
 7. 노드를 새로 join 시키면 몇 초 만에 DaemonSet Pod 가 뜨는지 (노드 추가 안 해봄)
 8. Static Pod 를 kubectl delete 했을 때의 정확한 동작 (1단계에서 일부 확인)
 9. calico-node 가 revision 1 그대로인 이유 — 20일간 한 번도 안 바뀌었는지
10. priorityClassName (system-node-critical) 이 배치에 미치는 영향
```

## 정리 명령

```bash
kubectl -n k8s-lab delete ds log-agent
kubectl label node worker01 log-collect-
rm -f ~/manifests/log-agent.yaml
```

## 다음

```text
13-job-cronjob.md   끝나는 것이 정상인 유일한 워크로드
                    backoffLimit — 재시도를 포기하는 설정
                    11-storage.md 에서 필요하다고 한 백업 CronJob 을 여기서 만든다
```

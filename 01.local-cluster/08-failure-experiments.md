# 08. 장애 실험 — 컴포넌트를 멈춰보고 확인한다

07 문서에서 Control Plane의 구조를 네 라운드로 해부했다. 그 과정에서 만든 가설들을 실제로 멈춰보며 확인한다.

로드맵 1단계의 마지막 두 질문을 채운다.

| # | 질문 | 실험 |
|---|---|---|
| 8 | apiserver가 중단되면 기존 Pod와 신규 스케줄링은 어떻게 달라지는가 | 실험 3 |
| 9 | kubelet이나 containerd가 중단되면 Node와 Pod 상태는 어떻게 변하는가 | 실험 1·2 |

## 관측 환경의 한계 — 먼저 적어둔다

`AGENTS.md`의 로드맵 원칙 2는 이렇다.

> **장애 실험 전에 관측 환경을 먼저 구성한다.**
> 관측 없는 장애 실험은 "Pod가 다시 떴다" 이상을 알려주지 못한다.

**지금 관측 스택이 없다.** Prometheus와 Grafana는 5단계에서 구성한다.

```text
쓸 수 있는 것   kubectl get/describe, kubectl get events, journalctl, crictl, 손목시계
쓸 수 없는 것   시계열 지표, 자동 수집, 사후 소급 분석
```

그래서 이렇게 한다.

```text
1. 손으로 시각을 기록한다
2. 이번에 관측하지 못한 것을 실험마다 명시한다
   → 5단계에서 Observability를 구성하는 이유의 근거가 된다
3. 5단계 이후 같은 실험을 반복해 대조한다
```

**"무엇을 못 봤는가"를 남기는 것 자체가 이 문서의 결과물 중 하나다.**

## 실험 목록

```text
실험 1   worker01 kubelet 중단        ✅ 2026-08-10
실험 2   worker01 containerd 중단     ✅ 2026-08-10
실험 3   master01 apiserver 중단      ✅ 2026-08-10
실험 4   master01 etcd 중단           ✅ 2026-08-10
```

## 네 실험 종합

| | 실험 1 kubelet | 실험 2 containerd | 실험 3 apiserver | 실험 4 etcd |
|---|---|---|---|---|
| 죽인 대상 | worker 에이전트 | worker 런타임 | 제어 평면 입구 | 제어 평면 저장소 |
| 컨테이너 | 계속 실행 | 계속 실행 | 계속 실행 | 계속 실행 |
| **트래픽** | **200** | **200** | **200** | **200** |
| `kubectl` | 정상 | 정상 | `refused` | **응답 없음** |
| Node 상태 | `Unknown` | `False` | 갱신 안 됨 | 갱신 안 됨 |
| taint | `unreachable` | `not-ready` | 없음 | 없음 |
| 감지 시간 | 52초 | 28초 | — | — |
| Pod 축출 판단 | 300초 뒤 | 300초 뒤 | 없음 | 없음 |
| `deletionTimestamp` | +30초 | +30초 | — | — |
| 관측 수단 | kubectl | kubectl + ps | crictl/etcdctl | crictl 만 |

**네 번 다 트래픽이 끊기지 않았다.**

```text
제어 평면이 어떻게 망가지든
이미 깔린 iptables 규칙은 커널에 남아 있다
        ↓
"설정하는 자와 전달하는 자는 다르다"
```

**다만 apiserver 를 쓰는 앱은 예외다**(실험 3 발견 2, 실험 4 발견 8). `calico-kube-controllers` 는 두 실험 모두에서 CrashLoopBackOff 에 빠졌다.

### 실패 방식이 두 종류로 갈린다

```text
[없다]   실험 1 · 3
         connection refused / Ready = Unknown
         즉시 실패. 원인이 명확하다

[답을 안 한다]  실험 2 · 4
         timeout / deadline exceeded
         매달린다. 원인이 불명확하다
```

**후자가 실무에서 훨씬 어렵다.**

**실험 1·2로 로드맵 질문 9의 답이 나왔다.**

```text
질문 9  kubelet 이나 containerd 가 중단되면 Node 와 Pod 상태는 어떻게 변하는가

  kubelet 중단      Ready = Unknown  → unreachable taint  → 감지 52초
  containerd 중단   Ready = False    → not-ready taint    → 감지 28초

  둘 다 컨테이너는 계속 돌고, 조건 변경 300초 뒤 축출 판단이 내려진다
  deletionTimestamp 는 거기서 다시 +30초 (terminationGracePeriodSeconds)
  둘 다 원본 컨테이너를 죽일 수단이 없어 Terminating 이 해소되지 않는다
```

**실험 3으로 로드맵 질문 8의 답이 나왔다.**

```text
질문 8  apiserver 가 중단되면 기존 Pod 와 신규 스케줄링은 어떻게 달라지는가

  기존 Pod        영향 없음. 계속 돌고 트래픽도 정상 (전 구간 200)
                  단 apiserver 를 쓰는 앱은 헬스 체크 실패로 CrashLoopBackOff
  신규 스케줄링   불가능. kubectl 자체가 안 된다
  노드 장애 감지   불가능. 판정 주체가 apiserver 를 통해 일하므로
  관측            etcdctl / crictl / journalctl / curl 로만 가능
```

**로드맵 1단계 질문 9개가 전부 채워졌다.**

## 공통 준비

관측 대상이 될 일반 워크로드를 하나 띄운다. 시스템 Pod만 보면 해석이 어렵다.

```text
$ kubectl create deployment nginx-test --image=nginx --replicas=4
deployment.apps/nginx-test created

$ kubectl get pods -o wide -l app=nginx-test
NAME                          READY  STATUS   NODE
nginx-test-6ff8854996-hq9pm   1/1    Running  worker02
nginx-test-6ff8854996-pskx9   1/1    Running  worker02
nginx-test-6ff8854996-vc6kb   1/1    Running  worker01
nginx-test-6ff8854996-vxvqk   1/1    Running  worker01
```

worker01과 worker02에 2개씩 나뉘어 떴다. **worker01을 죽였을 때 그 2개가 어떻게 되는지**가 관찰 대상이다.

컨트롤러의 타이머 설정도 미리 확인했다.

```text
$ grep -E 'node-monitor|pod-eviction|default-not-ready|default-unreachable' \
    /etc/kubernetes/manifests/kube-controller-manager.yaml
(출력 없음)

$ grep -E 'default-not-ready-toleration-seconds|default-unreachable-toleration-seconds' \
    /etc/kubernetes/manifests/kube-apiserver.yaml
(출력 없음)
```

**아무것도 설정하지 않았다 = 전부 기본값이다.** 그 기본값이 몇 초인지는 실측으로 알아낸다.

## 터미널 배치

```text
T1  master01   kubectl get nodes -w
T2  master01   kubectl get pods -w
T3  worker01   crictl 관찰 + kubelet 제어
```

`kubectl get -w`에는 타임스탬프가 없으므로 직접 붙였다.

```bash
kubectl get nodes -w | while read line; do echo "$(date '+%H:%M:%S') $line"; done
```

**T3가 중요하다.** kubelet을 죽여도 SSH 접속은 살아 있다. kubelet은 컨테이너를 관리할 뿐 SSH와 무관하다는 것 자체가 관측 대상이다.

---

# 실험을 읽기 위한 개념 세 가지

실험 결과에 `Lease`, `taint`, `toleration`이 나온다. 이것들이 무엇인지 먼저 정리한다.

## 1. Lease — "살아있다"를 어떻게 알리는가

실험에서 이런 일이 있었다.

```text
09:27:27   worker01 에서 kubelet 을 멈췄다
09:28:19   master01 이 "worker01 NotReady" 라고 판정했다
```

**master01은 worker01이 죽은 것을 어떻게 알았는가.** worker01이 "저 죽습니다"라고 말한 것이 아니다. 그냥 멈췄을 뿐이다.

### 답 — 살아있을 때 계속 말하게 시킨다

```text
죽은 것을 감지하는 방법은 하나뿐이다
  → "살아있다" 는 신호가 끊기는 것을 본다
```

이 주기적인 신호를 **하트비트(heartbeat)** 라고 한다.

Kubernetes에는 노드끼리 직접 대화하는 통로가 없다. 전부 apiserver를 거친다(07 문서 1~2라운드).

```text
worker01 kubelet ──renew──> apiserver ──write──> etcd
                                │
                                └──watch──> master01 의 node-controller
```

**"보고"라는 것이 결국 etcd에 무언가를 쓰는 일이다.**

**쓰는 쪽도 읽는 쪽도 apiserver를 거친다.** etcd에 직접 붙을 수 있는 것은 apiserver 하나뿐이다(07 문서 4라운드 — `etcd-ca`가 따로 분리된 이유). node-controller는 etcd를 열어보는 것이 아니라 apiserver에 watch를 걸어놓고 변경을 받는다.

> **2026-08-11 수정.** 처음에는 `node-controller ─── etcd` 로 화살표를 그렸으나 이는 잘못이다. controller-manager에는 etcd 클라이언트 인증서가 없다(`/etc/kubernetes/controller-manager.conf`에는 apiserver 접속 정보만 있다). 확인 명령은 아래 "Lease를 etcd에서 직접 확인하기" 참조.

### 그럼 무엇을 쓰는가

```text
[옛날 방식] Node 오브젝트를 통째로 갱신
  CPU 여유, 메모리, 디스크, 이미지 목록, 조건 5가지, 주소 ...
  → 노드 3대면 괜찮지만 1000대면 etcd 가 못 버틴다

[지금 방식] Lease 오브젝트의 시각만 갱신
  이름 / 주인 / 갱신 시각
  → 아주 가볍다
```

### Lease라는 이름의 뜻

**임대차 계약**이다. 부동산 lease와 같은 단어다.

```text
계약에는 기한이 있다
계속 쓰려면 갱신(renew)해야 한다
갱신을 안 하면 만료된다
```

```text
RenewTime = 마지막으로 갱신한 시각 = 그 시각에 살아있었다
```

### 누가 무엇을 하는가

```text
kubelet (worker01)     자기 Lease 의 RenewTime 을 주기적으로 갱신
        ↓
apiserver              받아서 etcd 에 저장. 판단은 하지 않는다
        ↓
node-controller        Lease 를 확인하고 "너무 오래됐다" 판정
(controller-manager)   → Node 를 NotReady 로 바꾼다
```

**판정 주체는 apiserver가 아니라 controller-manager다.** 07 문서 2라운드에서 이름만 봤던 `node-controller`가 이 일을 한다.

apiserver가 "판단하지 않는다"는 것은 **아무것도 안 한다는 뜻이 아니다.**

```text
apiserver 가 하는 일     인증 / 인가 / admission / 스키마 검증 / etcd 에 쓰기
apiserver 가 안 하는 일  "이 RenewTime 이 오래됐으니 죽은 것이다" 라는 해석
```

apiserver에게 `RenewTime`은 저장할 값일 뿐이고, 그것이 40초 전인지 4시간 전인지는 관심 밖이다. **숫자에 의미를 부여하는 쪽이 따로 있다.**

### Lease를 etcd에서 직접 확인하기

07 문서 4라운드에서 쓴 방법 그대로다.

```text
root@master01:/# ETCD=$(sudo crictl ps --name etcd -q)
root@master01:/# sudo crictl exec $ETCD etcdctl \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/leases --prefix --keys-only
```

etcd 안에서의 이름은 이렇다.

```text
/registry/leases/kube-node-lease/master01               노드 하트비트
/registry/leases/kube-node-lease/worker01
/registry/leases/kube-node-lease/worker02

/registry/leases/kube-system/kube-controller-manager    리더 선출
/registry/leases/kube-system/kube-scheduler
/registry/leases/kube-system/apiserver-<해시>            apiserver 신원
```

**namespace가 용도를 가른다.** 같은 Lease 오브젝트인데 읽는 쪽이 다르다.

```text
kube-node-lease   "나 살아있다"   → node-controller 가 본다   ← 실험 1·2
kube-system       "내가 리더다"   → 각 컴포넌트가 서로 본다   ← 실험 3·4
```

Control Plane이 1대라 리더 선출은 실질적으로 경쟁이 없지만, **갱신 자체는 계속 일어난다.** 실험 3·4에서 controller-manager와 scheduler가 스스로 종료한 것이 이 Lease를 갱신하지 못했기 때문이다.

### 이것은 "파일"이 아니다

```text
/registry/leases/kube-node-lease/worker01
```

슬래시가 있어 경로처럼 보이지만 **이 문자열 전체가 하나의 key 이름**이다. `/registry/leases`라는 디렉터리는 존재하지 않는다. etcd는 key-value 저장소이고, 슬래시는 `--prefix`로 묶어 조회하기 위한 관례일 뿐이다.

디스크에 실제로 존재하는 파일은 하나다.

```text
root@master01:/# sudo ls -la /var/lib/etcd/member/snap/
db          ← Lease, Pod, Secret ... 모든 key-value 가 이 파일 하나 안에 있다
```

`etcdctl snapshot save` 한 번으로 클러스터 전체가 백업됐던 이유가 이것이다.

### 하트비트를 눈으로 확인하는 법

값을 그대로 `get`하면 protobuf라 `RenewTime`이 읽히지 않는다(07 문서 4라운드에서 확인). 대신 **메타데이터를 보면 된다.**

```text
root@master01:/# sudo crictl exec $ETCD etcdctl \
  --cacert=... --cert=... --key=... \
  get /registry/leases/kube-node-lease/worker01 -w json | head -c 400
```

```text
"mod_revision"   이 key 가 마지막으로 수정된 시점의 전역 리비전
"version"        이 key 가 지금까지 몇 번 쓰였는가
```

**10초쯤 뒤 같은 명령을 다시 치면 `version`이 올라가 있다.** 그 사이에 kubelet이 하트비트를 보냈다는 증거다. 실험 1에서 kubelet을 멈췄을 때 멈춘 것이 바로 이 증가다.

읽을 수 있는 형태로 보려면 apiserver를 거쳐야 한다.

```text
root@master01:/# kubectl -n kube-node-lease get lease worker01 -o yaml
```

```text
같은 데이터를 두 경로로 본 것

etcdctl   원본 그대로. protobuf. 아무도 해석해주지 않는다
kubectl   apiserver 가 protobuf → 구조체 → YAML 로 변환해준 것
```

## 2. taint / toleration

### 이것이 푸는 문제

Pod를 만들면 scheduler가 어느 노드에 놓을지 정한다.

```text
$ kubectl create deployment nginx-test --image=nginx --replicas=4
→ worker01 에 2개, worker02 에 2개. master01 에는 0개
```

**왜 master01에는 안 떴는가.**

master01도 노드다. CPU도 메모리도 있고, 07 문서 1라운드에서 확인했듯 **kubelet의 신원도 worker와 똑같다**(`O=system:nodes, CN=system:node:master01`).

**"master니까 알아서 피한다"는 규칙은 없다.** 무언가가 막은 것이다.

### 막는 방법이 둘인데 방향이 반대다

```text
[방법 A] Pod 가 피한다 — nodeSelector
  spec:
    nodeSelector:
      kubernetes.io/hostname: worker01

  문제: 모든 Pod 에 써야 한다
        하나라도 빼먹으면 master01 에 뜬다
        막는 쪽이 아니라 피하는 쪽이 매번 노력해야 한다

[방법 B] 노드가 막는다 — taint
  master01 에 "여기 오지 마" 표시를 붙인다

  아무것도 안 써도 안 온다        ← 기본이 차단
  예외로 올 수 있는 것만 따로 허락  ← toleration
```

| | 누가 정하나 | 기본값 |
|---|---|---|
| `nodeSelector` | **Pod**가 "여기 갈래" | 아무 데나 감 |
| `taint` | **Node**가 "오지 마" | 안 감 |

**taint는 "실수로 오는 것"을 막는 장치다.**

### 실물 확인

```text
$ kubectl describe node master01 | grep -A3 'Taints:'
Taints:  node-role.kubernetes.io/control-plane:NoSchedule
```

**이 한 줄 때문에 nginx가 master01에 안 왔다.**

그런데 master01에도 Pod는 있다. apiserver, etcd, scheduler, kube-proxy, calico-node 등이다. "오지 마"라고 써놨는데 어떻게 있는가.

```text
$ kubectl get pod kube-apiserver-master01 -n kube-system \
    -o jsonpath='{.spec.tolerations}'
[{"operator":"Exists"}]
```

**"모든 표시를 무시한다"** 는 뜻이다. 어떤 taint가 붙어 있든 간다.

```text
taint        노드가 붙이는 "오지 마" 표시
toleration   Pod 가 가진 "그 표시를 무시하겠다" 선언
```

### taint의 구조 — 세 부분

```text
key = value : effect
```

```text
node-role.kubernetes.io/control-plane  :  NoSchedule
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^     ^^^^^^^^^^
key (value 생략)                           effect

gpu = true : NoSchedule
^^^   ^^^^   ^^^^^^^^^^
key   value  effect
```

```text
key      이 표시의 이름. 필수
value    세부 값. 생략 가능
effect   어떻게 할 것인가. 필수
```

### effect 세 가지 — 적용 시점이 다르다

| effect | 새 Pod 배치 | 이미 있는 Pod |
|---|---|---|
| `NoSchedule` | 막음 | 그대로 둠 |
| `PreferNoSchedule` | 되도록 막음 (자리 없으면 허용) | 그대로 둠 |
| `NoExecute` | 막음 | **쫓아냄** |

**`NoExecute`만 이미 도는 Pod를 건드린다.**

실험 1에서 taint가 두 개 붙은 이유가 이것이다.

```text
node.kubernetes.io/unreachable:NoSchedule    새 Pod 가 이 노드로 안 가게
node.kubernetes.io/unreachable:NoExecute     이미 있는 Pod 를 쫓아내게
```

### toleration의 문법

```yaml
spec:
  tolerations:
  - key: "node-role.kubernetes.io/control-plane"
    operator: "Exists"
    effect: "NoSchedule"
```

읽는 법: **"control-plane이라는 key의 NoSchedule taint는 무시할 수 있다"**

```text
operator: Exists   key 만 맞으면 된다. value 는 안 본다
operator: Equal    key 와 value 가 둘 다 맞아야 한다
```

`key`를 생략하면 "모든 key", `effect`를 생략하면 "모든 effect"다. 그래서 `{"operator":"Exists"}` 하나가 **모든 taint를 무시**하게 된다.

**매칭 규칙** — 노드의 taint가 3개면 Pod는 **3개 전부**에 대한 toleration을 가져야 간다. 하나라도 못 견디면 못 간다.

### `tolerationSeconds` — `NoExecute`에만 있다

```text
tolerationSeconds 없음   영원히 견딘다. 안 나간다
tolerationSeconds: 300   그 taint 가 붙은 뒤 300초만 견딘다
```

`NoSchedule`에는 이 값이 없다. 애초에 이미 있는 Pod를 건드리지 않기 때문이다.

```text
[apiserver]  {"operator":"Exists"}
             tolerationSeconds 없음 → master01 에서 절대 안 쫓겨난다

[nginx]      {"key":"...unreachable","effect":"NoExecute","tolerationSeconds":300}
             300초만 견딘다
```

### 실험 1에 대입하면

```text
09:28:19   worker01 에 unreachable:NoExecute 부착
           nginx Pod 는 tolerationSeconds: 300 을 갖고 있다 → 버틴다
09:33:19   300초 경과 → 축출
```

**정확히 300초 뒤에 쫓겨난 이유다.** 그리고 그 toleration은 우리가 쓴 적이 없다. `kubectl create deployment` 한 줄만 쳤는데 붙어 있었다.

```text
apiserver 의 admission 이 모든 Pod 에 자동으로 넣는다
"노드가 잠깐 안 보인다고 바로 옮기지 마라.
 일시적인 네트워크 문제일 수 있으니 5분은 기다려라."
```

값은 바꿀 수 있다.

```text
짧게 하면   장애 시 빨리 옮긴다. 대신 오판이 늘어난다
길게 하면   오판은 줄지만 복구가 느리다
```

**정답이 없는 절충**이며 그 이유는 발견 7에 있다.

### toleration은 자격증명이 아니다 — 중요

인증서·토큰(07 문서 1~2라운드)과 혼동하면 안 된다.

```text
[인증서 / 토큰]
  CA 가 서명한다
  받는 쪽이 검증한다
  위조하면 걸린다
  → 진짜 자격증명

[toleration]
  아무나 yaml 에 쓰면 된다
  발급도 검증도 없다
  → 자격증명이 아니라 그냥 "선언"
```

아무 Pod에나 이 한 줄만 쓰면 master01에 뜰 수 있다. 허가를 받는 절차가 없다.

```yaml
spec:
  tolerations:
  - operator: "Exists"
```

**그래서 taint는 보안 장치가 아니다.**

```text
taint 로 "오지 마" 를 붙여도
아무나 toleration 을 써서 갈 수 있다
        ↓
접근을 "막는" 게 아니라 "실수로 오는 것" 을 막는 장치다
```

진짜로 못 오게 하려면 다른 층이 필요하다.

```text
taint / toleration   배치를 어디로 할지 — 스케줄링 영역. 검증 없음
RBAC                 애초에 만들 수 있는지 — 인가 영역. 검증 있음
Admission 정책        "이런 toleration 은 금지" 를 강제
```

### toleration은 임시가 아니다

Pod spec에 박혀 있는 **영구 속성**이다. Pod가 만들어질 때 정해지고 죽을 때까지 안 바뀐다. 시간 제한이 생기는 것은 `tolerationSeconds`가 있을 때뿐이다.

### 1단계에서 이미 두 번 만났다

**첫 번째 — master01**

```text
node-role.kubernetes.io/control-plane:NoSchedule
```

`nginx-test` 4개가 worker01/worker02에만 뜬 이유다. **우연이 아니었다.**

**두 번째 — CNI 설치 전 CoreDNS가 Pending이던 것**

```text
node.kubernetes.io/not-ready:NoSchedule
  → CNI 가 없어서 노드가 NotReady
  → NotReady 노드에는 배치하지 마라
  → CoreDNS 가 갈 곳이 없어 Pending
```

06 문서의 "CNI를 설치하니 갑자기 다 떴다"는 taint가 사라져서였다.

### `not-ready`와 `unreachable`의 차이

```text
not-ready     kubelet 이 보고는 하는데 "상태가 안 좋다" 고 함
              → Ready = False
              → 예: CNI 가 없다, 디스크가 꽉 찼다

unreachable   kubelet 이 보고 자체를 안 함
              → Ready = Unknown
              → 예: kubelet 이 죽었다, 노드가 꺼졌다, 네트워크가 끊겼다
```

**`False`(아니다)와 `Unknown`(모른다)의 차이다.** 이 차이가 발견 7의 핵심이 된다.

### 직접 확인하는 방법

5분이면 되고 되돌릴 수 있다.

```bash
# 1. worker02 에 표시를 붙인다
kubectl taint nodes worker02 demo=test:NoSchedule
kubectl describe node worker02 | grep -A3 'Taints:'

# 2. 기존 Pod 는? → 아무 일도 안 일어난다 (NoSchedule 이므로)
kubectl get pods -o wide -l app=nginx-test

# 3. 새 Pod 를 만들면? → 전부 worker01 에만 뜬다
kubectl create deployment taint-test --image=nginx --replicas=4
kubectl get pods -o wide -l app=taint-test

# 4. NoExecute 로 바꾸면? → worker02 의 Pod 가 즉시 쫓겨난다
kubectl taint nodes worker02 demo=test:NoExecute
kubectl get pods -o wide -l app=nginx-test

# 5. 정리 (반드시)
kubectl taint nodes worker02 demo=test:NoExecute-
kubectl taint nodes worker02 demo=test:NoSchedule-
kubectl delete deployment taint-test
kubectl describe node worker02 | grep -A3 'Taints:'    # <none> 이어야 한다
```

> 명령 끝의 **`-`(하이픈)이 제거**를 뜻한다. `:NoSchedule-` 처럼 쓴다.

**4번에서 즉시 쫓겨나는 것**이 실험 1과 대비된다. 우리가 붙인 `demo=test`에 대해서는 Pod에 toleration이 아예 없으므로 `tolerationSeconds`를 따질 것도 없이 바로 나간다. 실험 1에서 300초를 기다린 것은 `unreachable`에 대한 toleration이 있었기 때문이다.

## 3. probe — liveness / readiness / startup

실험 3과 4의 결과가 전부 이것으로 갈린다.

### 이것이 푸는 문제

앱이 시작하면서 캐시를 로딩한다. 30초 걸린다. 그동안 프로세스는 떠 있지만 요청은 처리할 수 없다.

```text
"살아있나?"       → 살아있다. 프로세스가 멀쩡히 돌고 있다
"일할 수 있나?"    → 아니다. 아직 준비 중이다
```

**두 질문의 답이 다르다.** 그래서 probe가 두 개다. 하나였다면 캐시 로딩 중인 멀쩡한 앱을 계속 죽여 영원히 시작하지 못한다.

### 진짜 차이는 조치다

```text
readiness 실패   →  Service 에서 뺀다. 컨테이너는 그대로 둔다
liveness 실패    →  컨테이너를 죽인다. 다시 띄운다
```

```text
readiness   가벼운 조치. 되돌리기 쉽다. 성공하면 즉시 트래픽 복귀
liveness    무거운 조치. 되돌릴 수 없다. 연결이 전부 끊긴다
```

**같은 엔드포인트를 호출해도 된다.** `/health` 하나를 두 probe가 똑같이 불러도 결과가 다르다. 조치가 다르기 때문이다.

### `kubectl get pods`에서 구분된다

```text
NAME     READY   STATUS    RESTARTS   AGE
app-1    0/1     Running   0          5m     ← readiness 실패 중
app-2    1/1     Running   5          5m     ← liveness 가 5번 죽였다
```

```text
READY 열      readiness 의 결과. 0/1 = 트래픽 안 감
RESTARTS 열   liveness 의 결과 (다른 이유도 있음)
STATUS 열     둘 다 Running. 여기만 보면 구분이 안 된다
```

**`0/1 Running`은 "살아는 있는데 일은 안 하는 중"이다.**

### 상황별 판단

```text
[A] 시작이 느리다 (캐시 로딩, 마이그레이션)
      readiness ✓  준비될 때까지 트래픽을 막는다
      liveness  ✗  죽이면 영원히 시작 못 한다

[B] DB / 외부 API 연결이 끊겼다
      readiness ✓  트래픽을 빼서 다른 replica 가 처리하게 한다
      liveness  ✗  재시작해도 상대는 여전히 죽어 있다

[C] 데드락에 빠졌다
      readiness △  트래픽은 안 가지만 앱은 영원히 멈춰 있다
      liveness  ✓  재시작해야만 풀린다
```

**liveness가 존재하는 이유는 C 하나다.** 데드락, 메모리 누수로 GC만 도는 상태, 연결 풀 고갈, 무한 루프 — 밖에서 보면 안 죽었으므로 아무도 안 고쳐준다. 재시작 말고 방법이 없다.

**실험 3의 `calico-kube-controllers`가 정확히 B였다.**

### 원칙

```text
liveness    "내가 회생 불가능한가"    ← 나 자신만 봐야 한다
readiness   "지금 일할 수 있나"       ← 남에게 의존해도 된다

→ liveness 에 외부 의존성을 넣지 마라
```

재시작이 그 문제를 못 고칠 뿐 아니라 더 나빠진다.

```text
DB 가 잠깐 느려짐 → liveness 에 DB 체크가 있다면
→ 모든 replica 가 동시에 재시작
→ DB 에 연결을 한꺼번에 다시 맺는다 → DB 가 더 느려진다 → 또 재시작
```

**재시작 폭풍이 이렇게 만들어진다.** 잠깐 느려졌을 뿐인데 서비스가 완전히 죽는다.

### readiness 실패가 실제로 하는 일

```text
1. readiness 실패
2. Pod 의 Ready condition 이 False
3. EndpointSlice 에서 그 Pod 의 IP 가 빠진다
4. kube-proxy 가 iptables 규칙을 고친다
5. 그 Pod 로 가는 경로가 없어진다
```

Pod는 살아 있고 로그도 쓰고 접속해서 디버깅도 할 수 있다. 요청만 안 온다.

**`liveness`로 죽이면 증거가 사라진다.** readiness를 선호할 이유 중 하나다.

> 3~4단계(EndpointSlice → iptables)는 2단계에서 직접 열어볼 부분이다.

### startupProbe

```text
[문제] 앱 시작에 3분 걸린다 → liveness 를 3분 이상 기다리게 설정하면
       나중에 진짜 데드락일 때도 3분을 기다린다

[해결] startupProbe 가 성공할 때까지 liveness / readiness 를 아예 안 돌린다
       성공하면 다시 안 돌고, 그때부터 나머지가 시작된다
```

**"시작할 때의 느림"과 "돌다가 멈춤"을 구분하는 장치다.**

### 정리

```text
                readiness              liveness
─────────────────────────────────────────────────────────────
묻는 것          "지금 일할 수 있나"      "회생 불가능한가"
실패 시 조치      Service 에서 뺀다        컨테이너를 죽인다
조치의 무게       가볍다. 되돌릴 수 있다    무겁다. 되돌릴 수 없다
외부 의존성       넣어도 된다              넣으면 안 된다
kubectl 표시      READY 0/1               RESTARTS 증가
유예             짧게 줘도 된다            길게 줘야 한다
─────────────────────────────────────────────────────────────

차이는 "무엇을 묻는가" 가 아니라 "실패했을 때 무엇을 하는가" 다
```

**마지막 줄(유예)이 실험 4에서 결정적으로 드러난다** — apiserver의 `/livez`는 `failureThreshold: 8`, `/readyz`는 `3`이다.

### 확인 명령

```bash
# apiserver 의 세 probe 를 나란히 본다
sudo grep -A8 'livenessProbe:\|readinessProbe:\|startupProbe:' \
  /etc/kubernetes/manifests/kube-apiserver.yaml

# READY 열이 무엇인지
kubectl get pods -A -o wide | head -20

# Ready condition 직접 보기 — 이 값이 False 가 되면 EndpointSlice 에서 빠진다
kubectl get pod -n kube-system -l k8s-app=kube-dns \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
```

---

# 실험 1 — worker01 kubelet 중단 (2026-08-10)

## 개요

```text
대상       worker01 의 kubelet
방법       systemctl stop kubelet
지속       19분 19초 (09:27:27 ~ 09:46:46)
영향 범위  worker01 에 배치된 Pod 4개 (nginx 2, coredns 1, 그 외 DaemonSet)
결과       Pod 는 worker02 로 재배치. 원본 컨테이너는 kubelet 복구 시까지 계속 실행
```

## 가설

실험 전에 적어두고 맞는지 확인했다.

```text
1. Node 가 즉시 NotReady 가 되지는 않는다
2. 그 노드의 컨테이너는 계속 돈다 (containerd 가 돌리므로)
3. kubectl 에는 여전히 Running 으로 보인다 (마지막 보고 상태가 남음)
4. 일정 시간 뒤 Pod 가 다른 노드로 옮겨간다
```

**네 개 전부 맞았다.** 다만 예측이 틀린 것이 하나 있었다(발견 3).

## 타임라인

```text
09:27:24   worker01 kubelet 의 마지막 Lease 갱신
09:27:27   kubelet 중단                                  ← T0
09:27:53   worker01 컨테이너 2개 Running 확인            T0 + 26초
09:28:19   Node Ready → Unknown                          T0 + 52초
           taint unreachable:NoSchedule / :NoExecute 부착
09:28:20   Pod 에 NodeNotReady 경고 이벤트
09:30:08   kubectl 은 여전히 1/1 Running 이라고 표시      T0 + 2분 41초
09:33:19   TaintManagerEviction "Marking for deletion"    NotReady + 300초
09:33:20   worker02 에 대체 Pod 2개 생성
09:33:50   Pod 에 deletionTimestamp 기록
09:38:24   worker01 컨테이너 여전히 Running               T0 + 11분
09:46:46   kubelet 재시작                                 ← T1
09:46:46   Node Ready 복귀                                T1 + 1초 미만
09:46:48   Pod → Completed                                T1 + 2초
09:47:51   Pod 오브젝트 소멸. nginx 4개로 수렴            T1 + 65초 이내
```

**감지 52초 / 축출 300초 / 복구 1초 미만.**

> **2026-08-11 해결.** `TaintManagerEviction` 이벤트(09:33:19)와 `deletionTimestamp` 값(09:33:50)
> 사이의 31초는 **`terminationGracePeriodSeconds: 30`** 이다.
>
> ```text
> 09:28:19  + 300초 (tolerationSeconds)  =  09:33:19   축출 판단. 정확히 300초
> 09:33:19  +  30초 (gracePeriod)        =  09:33:49   deletionTimestamp 값
>                                            09:33:50  실제 기록 (1초 오차)
> ```
>
> **`deletionTimestamp`는 "삭제된 시각"이 아니라 "이 시각까지 정리하고 사라져라"는 마감 시각이다.**
> apiserver가 `요청 시각 + gracePeriodSeconds`로 계산해 기록한다.
>
> 서로 다른 두 타이머가 겹쳐 있던 것이다.
>
> ```text
> tolerationSeconds: 300   "노드가 안 보여도 5분은 기다린다"   ← 축출 판단
> gracePeriod: 30          "죽으라고 한 뒤 30초는 봐준다"      ← 종료 절차
> ```
>
> 2단계 Pod 종료 절차 실험에서 확인했다. [02.k8s-objects/00-pod.md](../02.k8s-objects/00-pod.md) 참조.

## 발견 1 — 컨테이너는 kubelet과 무관하게 돈다

```text
$ date '+%H:%M:%S'; sudo crictl ps | grep nginx     # worker01, T0 + 26초
09:27:53
4ee191f2932fb  5253dc86cc93a  3 minutes ago  Running  nginx  0  ...vxvqk
9f13583b5ca5a  5253dc86cc93a  3 minutes ago  Running  nginx  0  ...vc6kb
```

T0 + 11분 시점에도 그대로였다.

```text
09:38:24
4ee191f2932fb  ...  13 minutes ago  Running  nginx  0  ...vxvqk
9f13583b5ca5a  ...  13 minutes ago  Running  nginx  0  ...vc6kb
```

3라운드에서 확인한 구조 그대로다.

```text
kubelet      선언을 읽고 조정하는 관리자
containerd   실제로 컨테이너를 돌리는 자
```

**관리자가 없어져도 이미 시작된 프로세스는 계속 돈다.**

## 발견 2 — Lease가 하트비트다

```text
$ kubectl describe node worker01 | grep -A4 'Lease:'
Lease:
  HolderIdentity:  worker01
  AcquireTime:     <unset>
  RenewTime:       Mon, 10 Aug 2026 09:27:24 +0900
```

kubelet은 "살아있다"를 **Lease의 `RenewTime`을 갱신하는 방식**으로 알린다. node-controller가 그 시각이 오래됐으면 죽었다고 판정한다.

```text
$ kubectl get lease -A
NAMESPACE         NAME                      HOLDER
kube-node-lease   master01                  master01
kube-node-lease   worker01                  worker01
kube-node-lease   worker02                  worker02
kube-system       apiserver-h2pptbp4...     apiserver-h2pptbp4..._6c87c42d-...
kube-system       kube-controller-manager   master01_5b9fd15d-...
kube-system       kube-scheduler            master01_fbb1145e-...
```

**4라운드에서 본 `/registry/leases` 6개의 정체가 여기서 풀린다.**

```text
하트비트 3개    노드마다 하나. "나 살아있다"
리더 선출 3개   3라운드의 --leader-elect 가 쓰는 것
```

`kube-scheduler`의 HOLDER가 `master01_fbb1145e-...`인 것이 3라운드에서 본 `--leader-elect=true`의 실물이다.

**왜 Node 상태 대신 Lease를 쓰는가** — Node 오브젝트는 용량·이미지 목록·조건들을 담고 있어 크다. 노드 1000대가 10초마다 그것을 전부 갱신하면 etcd 부담이 크다. Lease는 시각 하나뿐이라 훨씬 가볍다.

## 발견 3 — taint는 `not-ready`가 아니라 `unreachable`이다

예측이 틀렸다.

```text
$ kubectl describe node worker01 | grep -A3 'Taints:'
Taints:  node.kubernetes.io/unreachable:NoExecute
         node.kubernetes.io/unreachable:NoSchedule
```

```text
not-ready     kubelet 이 보고는 하는데 Ready = False
              "나 살아있는데 상태가 안 좋아"

unreachable   kubelet 이 보고 자체를 안 함. Ready = Unknown
              "연락이 안 된다"
```

Node 조건이 `False`가 아니라 `Unknown`이었다.

```text
Ready   Unknown   2026-08-10T00:28:19Z   NodeStatusUnknown
```

**클러스터는 노드가 죽었는지 네트워크만 끊겼는지 구분할 수 없다.** 그래서 "모른다"고 한다.

taint가 두 개인 것도 역할이 다르다.

```text
NoSchedule   즉시 효력. 새 Pod 를 여기 배치하지 마라
NoExecute    유예 후 효력. 이미 있는 Pod 도 쫓아내라
```

## 발견 4 — kubectl은 마지막 보고 상태를 보여준다

T0 + 2분 41초 시점의 출력이다.

```text
$ date '+%H:%M:%S'; kubectl get pods -o wide -l app=nginx-test
09:30:08
nginx-test-...-vc6kb   1/1   Running   0   5m39s   10.244.5.7   worker01
nginx-test-...-vxvqk   1/1   Running   0   5m39s   10.244.5.8   worker01
```

kubelet이 죽은 지 3분 가까이 됐는데 `1/1 Running`이다.

```text
kubectl get pods 가 보여주는 것
  = 실시간 상태  ✗
  = 마지막으로 보고된 상태  ✓
```

**Node 상태는 갱신되는데 Pod 상태는 안 된다.**

```text
Node   node-controller 가 Lease 를 보고 능동적으로 판정 → 갱신됨
Pod    kubelet 이 보고해야만 갱신 → 멈춤
```

이번에는 우연히 실제와 일치했다(컨테이너가 정말 돌고 있었다). **만약 kubelet 중단 후 컨테이너까지 죽었다면 kubectl은 여전히 Running이라고 했을 것이고 아무도 몰랐을 것이다.**

**장애 시 `kubectl get pods`만 보면 안 되는 이유다.** Node 상태를 함께 봐야 한다.

## 발견 5 — toleration 300초는 자동 주입된다

```text
$ kubectl get pod nginx-test-6ff8854996-vc6kb -o jsonpath='{.spec.tolerations}'
[{"effect":"NoExecute","key":"node.kubernetes.io/not-ready",
  "operator":"Exists","tolerationSeconds":300},
 {"effect":"NoExecute","key":"node.kubernetes.io/unreachable",
  "operator":"Exists","tolerationSeconds":300}]
```

**Deployment에 이런 것을 쓴 적이 없다.** apiserver의 admission이 넣어준 것이다.

```text
"노드가 안 보여도 300초는 참아라. 일시적 문제일 수 있으니까."
```

두 종류가 다 들어 있어 `not-ready`든 `unreachable`이든 300초를 기다린다. 타임라인의 `09:28:19 → 09:33:19`이 정확히 그 300초다.

## 발견 6 — `Terminating`은 phase가 아니다

```text
$ kubectl get pod -l app=nginx-test -o custom-columns=\
  'NAME:.metadata.name,NODE:.spec.nodeName,PHASE:.status.phase,DELETED:.metadata.deletionTimestamp'
NAME                          NODE       PHASE     DELETED
nginx-test-...-vc6kb          worker01   Running   2026-08-10T00:33:50Z
nginx-test-...-vxvqk          worker01   Running   2026-08-10T00:33:50Z
```

`kubectl get pods`의 STATUS 열에는 `Terminating`이라고 나오는데 실제 `.status.phase`는 `Running`이다.

```text
실제로 존재하는 phase   Pending / Running / Succeeded / Failed / Unknown
Terminating             phase 가 아니라, deletionTimestamp 가 있을 때
                        kubectl 이 화면에 그려주는 표시
```

**왜 안 사라지는가** — 삭제는 두 단계다.

```text
"지워라"      apiserver 가 deletionTimestamp 기록      (09:33:50)
"지웠습니다"  kubelet 이 컨테이너를 죽이고 확인         ← 할 사람이 없다
```

뒷단계를 수행할 kubelet이 없으니 Pod 오브젝트가 영원히 Terminating에 머문다.

## 발견 7 — 선언은 4개인데 실제로는 6개가 돌았다 ★

이 실험에서 가장 중요한 발견이다.

```text
[우리가 선언한 것]
  replicas: 4        nginx 4개만 돌아야 한다

[09:33 ~ 09:46 사이 실제로 돌던 것]
  worker02   4개  ← 클러스터가 새로 띄운 것
  worker01   2개  ← 원래 있던 것. 안 죽었다
  ─────────────
  합계       6개
```

**13분 동안 선언보다 50% 많이 돌았다.**

### 왜 이런 일이 생기는가

근본 원인은 하나다.

```text
클러스터가 아는 것     worker01 과 연락이 안 된다
클러스터가 모르는 것   worker01 이 죽었는지, 연락만 끊긴 건지
```

두 경우의 진실이 정반대다.

```text
[경우 A — 노드가 정말 죽었다]
  전원이 나갔다 / 커널이 죽었다
  → 그 위의 Pod 도 다 죽었다
  → 빨리 다른 노드에 띄워야 한다. 안 그러면 서비스 중단

[경우 B — 연락만 끊겼다]
  네트워크가 끊겼다 / kubelet 만 죽었다      ← 이번 실험
  → Pod 는 멀쩡히 돌고 있다
  → 새로 띄우면 중복이 된다
```

**밖에서는 A와 B가 완전히 똑같이 보인다.** 조용한 것은 매한가지다.

```text
"응답이 없다" 로부터 "죽었다" 를 결론 낼 수 없다
```

이것이 발견 3의 `Ready = Unknown`(False가 아니라 Unknown)의 의미다.

### Kubernetes의 선택

구분할 수 없으니 하나를 골라야 한다.

```text
[A라고 가정]   새로 띄운다
               맞으면 → 빠른 복구
               틀리면 → 중복 실행

[B라고 가정]   가만히 있는다
               맞으면 → 중복 없음
               틀리면 → 서비스가 계속 죽어 있음
```

**Deployment의 기본값은 A다. 가용성을 우선한다.**

그래서 `replicas: 4`의 의미가 사실은 이렇다.

```text
"항상 정확히 4개"       ✗
"최소 4개는 되게 노력"   ✓
```

### 언제 문제가 되는가

**앱의 성격에 따라 완전히 갈린다.**

```text
[괜찮은 경우 — 상태 없는 앱]
  nginx, API 서버, 이미지 리사이저
  → 6개가 떠도 요청을 나눠 처리할 뿐 서로 간섭하지 않는다
  → 자원을 좀 더 쓰는 것 외에는 문제 없다
  → 이번 실험이 이 경우라 아무 일도 일어나지 않았다

[치명적인 경우 — 상태를 가진 앱]
  데이터베이스        같은 디스크에 두 프로세스가 쓴다 → 파일 손상
  큐 소비자 / 배치     같은 주문을 두 번 처리 → 결제 중복, 재고 이중 차감
  단일 실행 보장 작업   크론, 마이그레이션 → 두 개가 동시에 돌면 안 된다
```

### 어떻게 막는가 — 네 가지

**1. StatefulSet을 쓴다**

```text
Deployment   노드가 unreachable → 300초 뒤 새로 띄운다     (가용성 우선)
StatefulSet  노드가 unreachable → 띄우지 않고 기다린다     (안전 우선)
             사람이 노드를 강제 삭제해야 옮겨진다
```

"확신할 수 없으면 아무것도 하지 않는다"는 선택이다. DB를 StatefulSet으로 배포하는 이유다.

**대가는 자동 복구가 안 된다는 것이다.** 새벽에 노드가 죽으면 사람이 개입해야 한다.

**2. 스토리지가 막아준다 (fencing)**

```text
ReadWriteOnce 볼륨
  → 한 번에 한 노드만 마운트할 수 있다
  → worker01 이 붙잡고 있으면 worker02 가 못 붙는다
  → 새 Pod 가 뜨려다 실패한다

AWS EBS
  → 다른 인스턴스에 붙이려면 먼저 떼야 한다
  → 자연스럽게 중복이 차단된다
```

**"두 개가 동시에 쓰는 것"을 스토리지 층에서 물리적으로 막는 방식**이다.

**3. 앱이 스스로 리더를 정한다 — 여기서 Lease가 다시 나온다**

```text
Pod 가 6개 떠 있어도
  → 일을 시작하기 전에 Lease 를 잡으려 시도한다
  → 잡은 하나만 일한다. 나머지는 대기한다

worker01 의 Pod   Lease 갱신 실패 (연락 끊김)
                  → 스스로 "나는 리더가 아니다" 판단 → 멈춘다
worker02 의 Pod   Lease 획득 → 일한다
```

**중복이 떠도 실제로 일하는 것은 하나가 된다.**

`kube-controller-manager`와 `kube-scheduler`가 `--leader-elect=true`를 쓰는 이유가 정확히 이것이다(07 문서 3라운드). **Kubernetes 자신도 이 문제를 알고 있어서 자기 컴포넌트에 리더 선출을 걸어뒀다.**

**4. 노드를 확실히 죽인다 (fencing / STONITH)**

```text
클라우드라면
  → "연락 안 되는 노드" 를 API 로 강제 종료한다
  → 이제 확실히 죽었다
  → 그다음 Pod 를 옮긴다
```

**추측을 사실로 바꾸는 방법**이다. 온프레미스에서는 VM을 강제 종료하는 것이 그에 해당한다.

### 정리

```text
문제      "응답 없음" 으로부터 "죽었음" 을 결론 낼 수 없다
          그런데 무언가는 결정해야 한다

기본 선택  가용성 우선. 300초 기다리고 새로 띄운다
          중복 실행 가능성은 감수한다

그래서    stateless 앱에는 기본값이 적절하다
          stateful 앱은 StatefulSet 으로 반대 선택을 한다
          정말 중요한 것은 스토리지나 앱 레벨에서 한 번 더 막는다
```

**"Kubernetes가 알아서 해주겠지"가 통하지 않는 지점이다.** 앱의 성격을 알고 설계해야 한다.

## 발견 8 — 시스템 Pod도 같은 규칙을 따른다

```text
$ kubectl get events -A --sort-by='.lastTimestamp' | tail -20
default       5m1s   Normal  TaintManagerEviction  pod/nginx-test-...-vxvqk
kube-system   5m1s   Normal  TaintManagerEviction  pod/coredns-7d764666f9-gv4wl
default       5m1s   Normal  TaintManagerEviction  pod/nginx-test-...-vc6kb
default       5m     Normal  SuccessfulCreate      replicaset/nginx-test-6ff8854996
kube-system   5m     Normal  SuccessfulCreate      replicaset/coredns-7d764666f9
```

**CoreDNS도 함께 축출됐다.** taint는 대상을 가리지 않는다.

새로 뜬 CoreDNS의 IP가 눈에 띈다.

```text
기존 worker02 Pod   10.244.30.72 ~ .75
새 CoreDNS          10.244.241.65      ← 다른 대역
```

Calico가 노드마다 `/26` 블록을 할당하는데 기존 블록이 차서 새 블록을 추가로 가져간 것으로 보인다(06 문서의 IPAM 구조). **확인하지 않았다.**

## 복구

```text
$ date '+%H:%M:%S'; sudo systemctl start kubelet; sudo systemctl is-active kubelet
09:46:46
active
```

```text
T1 + 0초    worker01 Ready
T1 + 1초    Pod 오브젝트 갱신 시작
T1 + 2초    Pod → Completed
T1 + 65초 이내   Pod 소멸, nginx 4개로 수렴
```

### 발견 9 — 죽는 건 느리고 사는 건 즉시다

```text
죽을 때     kubelet 중단 → Node NotReady      52초
살아날 때   kubelet 시작 → Node Ready         1초 미만
```

**50배 차이이며 구조적인 이유가 있다.**

```text
["있다" 를 확인하는 것]
  Lease 의 RenewTime 이 방금 갱신됐다 → 신호 하나면 즉시 확정

["없다" 를 확인하는 것]
  Lease 가 갱신되지 않았다
  → 얼마나 안 와야 "없는" 것인가?
  → 잠깐 느린 것일 수도, 네트워크가 끊긴 것일 수도
  → 기다려봐야 안다
```

**"신호가 없다"는 신호가 아니다.** 있음은 증명되지만 없음은 추정할 수밖에 없다.

```text
너무 짧게 잡으면   잠깐 느린 노드를 죽었다고 오판 → 멀쩡한 Pod 를 옮김
너무 길게 잡으면   진짜 죽었는데 복구가 늦음
        ↓
그 절충이 52초 + 300초였다
```

### 발견 10 — `Completed`라는 중간 상태

```text
09:46:47   Terminating
09:46:48   Completed      ← 잠깐 나타났다
09:47:51   소멸
```

`Completed`는 컨테이너가 **정상 종료(exit 0)** 했다는 뜻이다.

```text
1. kubelet 재시작
2. apiserver 에서 "이 Pod 들은 deletionTimestamp 가 있다" 확인
3. kubelet → 컨테이너에 SIGTERM
4. nginx 가 정상 종료 (exit 0)     → Completed
5. kubelet → apiserver "다 지웠습니다"
6. Pod 오브젝트 제거
```

**강제로 죽인 것이 아니라 정중하게 요청한 것이다.** `terminationGracePeriodSeconds`(기본 30초) 안에 끝나지 않으면 그때 SIGKILL이 간다.

### 발견 11 — 재분배는 자동으로 되지 않는다

worker01이 Ready로 돌아온 뒤에도 Pod 4개가 전부 worker02에 남았다.

```text
Kubernetes 는 이미 잘 도는 Pod 를 굳이 옮기지 않는다
  → 옮기려면 죽였다 살려야 하고, 그것이 더 위험하다
  → 재분배는 별도 도구(descheduler)의 영역이다
```

한쪽 쏠림을 풀려면 `kubectl rollout restart deployment/nginx-test`로 다시 뿌려야 한다.

## 이번에 관측하지 못한 것

```text
1. kubectl get -w 에 타임스탬프가 없다
   → while read 로 직접 붙여야 했다

2. worker01 컨테이너가 정확히 언제 죽었는지 초 단위로 못 잡았다
   → watch 는 화면만 갱신할 뿐 기록이 남지 않는다

3. 그 19분 동안 노드의 CPU / 메모리 / 네트워크 상태를 모른다
   → 지표가 아예 없다

4. Event 는 TTL 로 사라진다
   → 이번에 본 TaintManagerEviction 은 곧 없어진다
   → 나중에 다시 보면 증거가 남지 않는다

5. "언제부터 이상했나" 를 소급해서 볼 수 없다
   → 이번에는 T0 를 우리가 정했으므로 알았다
   → 실제 장애에서는 T0 를 찾는 것부터 시작해야 하는데 불가능하다
```

**5번이 결정적이다.** 5단계에서 Prometheus를 구성한 뒤 같은 실험을 반복해 무엇이 달라지는지 대조한다.

## 운영 시사점

```text
1. kubectl get pods 만 보고 판단하지 않는다
   Pod 상태는 kubelet 이 보고해야만 갱신된다
   Node 상태를 함께 봐야 한다

2. 노드 장애 시 중복 실행 구간이 존재한다
   상태를 가진 앱(DB, 큐 소비자)은 이 구간에서 데이터가 깨질 수 있다
   StatefulSet 과 Pod 삭제 정책을 별도로 검토해야 한다

3. 감지 52초 + 축출 300초 = 약 6분
   그동안 그 노드의 Pod 는 트래픽을 못 받거나 중복으로 받는다
   더 빠른 전환이 필요하면 tolerationSeconds 를 낮출 수 있으나,
   오판 위험이 커진다는 대가를 진다

4. 복구 후 Pod 는 자동으로 재분배되지 않는다
   쏠림이 문제라면 rollout restart 나 descheduler 가 필요하다
```

## 설계 시사점 — 상태를 가진 것을 어디에 둘 것인가

발견 7에서 자연스럽게 나오는 질문이다. **DB를 Kubernetes에 올려야 하는가.**

### Pod의 전제와 DB의 전제가 반대다

```text
[Kubernetes 가 Pod 에 대해 가정하는 것]
  언제든 죽어도 된다
  언제든 다른 노드로 옮겨져도 된다
  똑같은 것이 여러 개 있어도 된다
  이름과 IP 가 매번 바뀌어도 된다

[DB 가 필요로 하는 것]
  죽으면 안 된다
  디스크와 함께 있어야 한다
  하나만 있어야 한다 (쓰기 기준)
  주소가 안정적이어야 한다
```

**설계 철학이 정반대다.** StatefulSet은 그 간극을 메우려는 장치이며, 메우려 한다는 것 자체가 억지로 맞추고 있다는 신호이기도 하다.

### 밖으로 빼는 이유 다섯 가지

```text
1. 중복 실행 위험         발견 7. StatefulSet 은 막아주지만 자동 복구를 포기한다
2. 전제가 반대다          위 표
3. 운영 부담이 넘어온다    백업/복구, 페일오버, 버전 업그레이드, 튜닝
                          관리형 DB 는 이것을 대신 해준다
4. 노드 유지보수마다 흔들린다  커널 패치, K8s 업그레이드 → drain → DB 이동
                          앱은 옮겨져도 무방하지만 DB 는 매번 페일오버다
5. 스토리지 계층이 하나 더 낀다
                          앱 → PVC → PV → CSI → 실제 디스크
                          로컬 디스크를 쓰면 그 노드에 묶여 K8s 의 장점이 사라진다
```

### DB만의 이야기가 아니다

```text
"DB 만 빼면 앱은 stateless 다"  ← 자주 틀린다
```

앱에도 상태가 숨어 있다.

```text
세션         메모리에 로그인 정보  → Pod 가 죽으면 로그아웃  → Redis 로
업로드 파일   Pod 안 디스크         → Pod 가 죽으면 소실     → S3 로
캐시         Pod 마다 다른 내용     → 요청마다 결과가 다름   → Redis 로
로그         Pod 안 파일           → Pod 가 죽으면 소실     → 표준 출력 + 수집기
```

```text
Pod 안에 남는 것    코드와 설정뿐
Pod 밖으로 뺀 것    데이터, 세션, 파일, 로그
        ↓
그래야 Pod 를 아무 때나 죽이고 옮길 수 있다
= Kubernetes 가 잘하는 일을 할 수 있게 된다
```

**"상태를 Pod 밖으로"가 원칙이고 DB는 그중 하나일 뿐이다.**

### 그럼에도 K8s에 올리는 경우

```text
1. 온프레미스        관리형 DB 가 없다. 어차피 직접 운영해야 한다
                     ← 우리 학습 환경(VMware 3대)이 여기 해당한다
2. 개발·테스트       브랜치마다 띄웠다 지운다. 데이터가 날아가도 된다
3. 성숙한 Operator   CloudNativePG(PostgreSQL), Strimzi(Kafka), Vitess(MySQL)
```

### Operator — 컨트롤러 패턴의 연장

07 문서 3라운드에서 배운 구조가 여기서 다시 쓰인다.

```text
Desired State  /  Controller  /  Actual State
```

**Operator는 "우리가 만든 컨트롤러"다.**

```text
[기본 컨트롤러]
  deployment-controller   "Deployment 선언대로 Pod 개수를 맞춘다"

[Operator]
  postgres-operator       "PostgreSQL 클러스터 선언대로 맞춘다"
                          리더가 죽으면 복제본을 승격시킨다
                          백업을 주기적으로 뜬다
                          버전 업그레이드를 순서대로 진행한다
```

**DBA가 하던 판단을 코드로 만든 것이다.**

```text
StatefulSet 만 쓰면   "노드가 죽었네... 사람이 와서 판단해라"
Operator 를 쓰면      "리더가 죽었네. 가장 최신 복제본을 승격하자"
```

그리고 Operator 자신도 리더 선출(Lease)을 쓴다. 자기가 중복 실행되면 안 되기 때문이다.

### 판단 기준

| 상황 | 권장 |
|---|---|
| 클라우드 + 운영 DB | 관리형 (RDS / Aurora / Cloud SQL) |
| 클라우드 + 팀에 DBA 없음 | 관리형 필수 |
| 온프레미스 + 운영 DB | K8s + 성숙한 Operator, 또는 K8s 밖 전용 서버 |
| 개발·테스트 | K8s 안에 띄워도 무방 |
| 날아가도 되는 캐시 | K8s 안에 띄워도 무방 |

```text
"이 데이터가 사라지면 무슨 일이 생기는가?"

회사가 망한다        → 관리형 DB 또는 전용 인프라
불편하지만 복구된다   → Operator 를 쓴다면 K8s 도 가능
아무 일 없다         → K8s 에 그냥 띄운다
```

### 로드맵과의 관계

```text
1~9단계    온프레미스 (VMware 3대)
           관리형 DB 가 없다. 띄운다면 K8s 안이다

10~12단계  AWS EKS
           "앱은 EKS, DB 는 RDS" 구성이 자연스럽다
```

**10단계의 설계 결정을 이 실험이 뒷받침한다.**

```text
"왜 DB 를 RDS 로 뺐나요?"
  → "노드가 unreachable 이 됐을 때 중복 실행 구간이 생기는 것을 직접 확인했고,
     StatefulSet 은 그것을 막는 대신 자동 복구를 포기합니다.
     운영 DB 에 그 트레이드오프를 지는 것은 부담이 큽니다."
```

남이 그렇다고 해서가 아니라 **직접 본 것으로 설명할 수 있게 된다.**

## 검증 결과

```text
가설 1   Node 가 즉시 NotReady 가 되지 않는다        ✅ 52초
가설 2   그 노드의 컨테이너는 계속 돈다               ✅ 19분간 유지
가설 3   kubectl 에는 Running 으로 보인다             ✅ 확인
가설 4   일정 시간 뒤 다른 노드로 옮겨간다            ✅ 300초 후

예측 오류  taint 가 not-ready 일 것이라 예상했으나
           실제로는 unreachable 이었다 (발견 3)
```

---

# 실험 2 — worker01 containerd 중단 (2026-08-10)

## 개요

```text
대상       worker01 의 containerd
방법       systemctl stop containerd
지속       12분 2초 (11:28:32 ~ 11:40:34)
영향 범위  worker01 에 배치된 Pod 전부
결과       Pod 는 worker02 로 재배치.
           원본 컨테이너는 containerd 복구 시까지 계속 실행
```

**실험 1과 같은 노드를 망가뜨렸지만 결과가 갈렸다.** 그 차이가 이 실험의 목적이다.

## 실험 1과 무엇이 다른가

```text
kubelet
   │  CRI 로 통신 (/run/containerd/containerd.sock)
   ▼
containerd  (systemd 서비스)
   │  컨테이너마다 shim 프로세스를 띄움
   ▼
containerd-shim-runc-v2  ← 컨테이너의 실제 부모 프로세스
   │
   ▼
nginx 프로세스
```

실험 1은 맨 위를 죽였고, 실험 2는 가운데를 죽인다.

## 가설

```text
1. 컨테이너는 안 죽는다. shim 이 부모라서 containerd 데몬과 별개로 산다
2. kubelet 은 살아 있지만 아무것도 못 한다. 물어볼 상대가 없다
3. Node 가 NotReady 가 되는데 실험 1보다 빠르다
   실험 1은 "연락이 끊긴 것을 확인" 하느라 52초 걸렸다
   이번엔 kubelet 이 살아서 직접 신고한다
4. taint 가 unreachable 이 아니라 not-ready 로 붙는다
   Ready = Unknown 이 아니라 Ready = False 가 된다
```

**네 개 전부 맞았다.** 다만 복구에 대한 예측은 틀렸다(발견 8).

## 관측 방법이 달라진다

실험 1에서는 `crictl ps`로 컨테이너를 봤다. **이번에는 쓸 수 없다.**

```text
crictl 은 containerd 에 물어보는 도구다
        ↓
containerd 가 죽으면 crictl 도 못 쓴다
        ↓
컨테이너는 살아 있는데 볼 수단이 없어진다
```

대신 프로세스를 직접 본다.

```bash
ps -ef | grep -E 'containerd-shim|nginx: master' | grep -v grep
```

**도구가 대상에 의존하면 대상이 죽을 때 도구도 죽는다.** 5단계 관측 스택을 설계할 때 기억할 지점이다.

## 타임라인

```text
11:28:32   containerd 중단                                    ← T0
11:29:00   Ready → False                                      T0 + 28초
           reason   KubeletNotReady
           message  container runtime is down
           taint    not-ready:NoSchedule / :NoExecute
11:29:29   crictl 실패 확인 / ps 로 컨테이너 생존 확인
11:29:47   Lease RenewTime 갱신됨                             ← 계속 살아있다
11:30:48   Lease RenewTime 또 갱신됨
11:34:31   Pod 에 deletionTimestamp                           조건 변경 + 331초
11:34:3x   worker02 에 대체 Pod 2개 생성
11:36:09   Terminating 유지. PHASE 는 Running
11:40:34   containerd 재시작                                  ← T1
11:40:41   Pod 갱신 시작                                      T1 + 7초
11:40:42   Pod → Completed                                    T1 + 8초
11:40:45   Node Ready                                         T1 + 11초
11:43:39   worker01 의 nginx 프로세스 0개 확인
```

**감지 28초 / 축출 331초 / 복구 11초.**

## 발견 1 — 컨테이너는 shim 덕분에 산다

```text
$ ps -ef | grep -E 'containerd-shim|nginx: master' | grep -v grep
root  1399  1  Aug04  /usr/bin/containerd-shim-runc-v2 -namespace k8s.io -id 1ed33c...
root  1400  1  Aug04  /usr/bin/containerd-shim-runc-v2 -namespace k8s.io -id 69e9a5...
root  3117467     1  11:19  /usr/bin/containerd-shim-runc-v2 -namespace k8s.io -id 3482fa...
root  3117594  3117467  11:20  nginx: master process nginx -g daemon off;
```

**`containerd-shim`의 부모 PID가 `1`(systemd)이다.** containerd가 아니다.

```text
systemd
  ├── containerd          ← 죽었다
  └── containerd-shim     ← 부모가 systemd 라 안 죽는다
        └── nginx
```

**이것이 shim이 존재하는 이유다.**

```text
containerd 가 shim 의 부모였다면
  → containerd 재시작할 때마다 모든 컨테이너가 죽는다
  → 런타임 업그레이드 = 전체 서비스 중단

shim 을 systemd 밑으로 떼어놓으면
  → containerd 를 껐다 켜도 컨테이너는 계속 돈다
  → 무중단 업그레이드가 가능하다
```

02 문서에서 컨테이너 런타임 계층을 정리할 때 `shim`이 나왔는데, **왜 그렇게 설계됐는지가 여기서 증명된다.**

## 발견 2 — kubelet은 살아 있지만 아무것도 못 한다

```text
$ sudo systemctl is-active kubelet
active

$ sudo crictl ps
FATA validate CRI v1 runtime API for endpoint "unix:///run/containerd/containerd.sock":
     rpc error: code = Unavailable ... connect: no such file or directory
```

kubelet 로그가 무엇을 하려다 실패하는지 그대로 보여준다.

```text
ListPodSandbox ... failed              "Pod 목록 좀 줘"
GenericPLEG: Unable to retrieve pods   "상태 변화 좀 알려줘"
Version from runtime service failed    "너 버전이 뭐야"
Container runtime sanity check failed  "너 살아있니"
ExecSync cmd from runtime service      "프로브 좀 실행해줘"
  cmd=["/bin/calico-node","-felix-ready","-bird-ready"]
  cmd=["/usr/bin/check-status","-l"]
ImageFsInfo from image service failed  "디스크 얼마나 썼어"
Failed to rotate container logs        "로그 파일 좀 돌려줘"
Skipping pod synchronization           "포기"
  err="container runtime is down"
```

**kubelet이 하는 일이 대부분 containerd를 통한다.**

```text
[kubelet 이 직접 할 수 있는 것]
  apiserver 와 통신 / Lease 갱신 / 파일 읽기

[containerd 없이는 못 하는 것]
  컨테이너 목록·상태·생성·삭제
  헬스 프로브 실행 (ExecSync)
  이미지 관리 / 로그 로테이션
```

`ExecSync`가 눈에 띈다. **프로브도 containerd를 거쳐 컨테이너 안에서 실행되므로** 함께 막힌다.

## 발견 3 — `not-ready`와 `unreachable`이 실물로 갈린다 ★

```text
$ kubectl get node worker01 -o jsonpath='{range .status.conditions[*]}...'
Ready   False   2026-08-10T02:29:00Z   KubeletNotReady

$ kubectl describe node worker01 | grep -A3 'Taints:'
Taints:  node.kubernetes.io/not-ready:NoExecute
         node.kubernetes.io/not-ready:NoSchedule

$ kubectl describe node worker01 | grep -A3 'Ready '
Ready   False   ...   KubeletNotReady   container runtime is down
```

**앞 절에서 개념으로만 설명한 구분이 여기서 실측됐다.**

```text
[실험 1 — kubelet 중단]
Ready    Unknown                 "연락이 안 된다"
reason   NodeStatusUnknown
taint    unreachable

[실험 2 — containerd 중단]
Ready    False                   "연락은 되는데 일을 못 한다"
reason   KubeletNotReady
message  container runtime is down
taint    not-ready
```

## 발견 4 — Lease는 계속 갱신된다

```text
$ kubectl describe node worker01 | grep -A4 'Lease:'
RenewTime:  Mon, 10 Aug 2026 11:29:47 +0900
...
RenewTime:  Mon, 10 Aug 2026 11:30:48 +0900     ← 1분 뒤에도 갱신됨
```

**kubelet이 살아 있으니 하트비트는 계속 뛴다.**

```text
Lease 가 갱신됨      "나 살아있다"          → unreachable 이 아니다
Ready = False        "그런데 일을 못 한다"   → not-ready 다
```

이것이 감지가 빨랐던 이유이기도 하다.

```text
[실험 1]  kubelet 이 죽음 → Lease 중단
          → node-controller 가 "50초 넘게 조용하네" 추정
          → Ready = Unknown → 52초

[실험 2]  kubelet 이 살아있음 → Lease 계속
          → kubelet 이 스스로 "runtime is down" 신고
          → Ready = False → 28초
```

**"남이 추정하는 것"과 "본인이 신고하는 것"의 차이다.** 본인이 말하니 더 빠르고, `message`에 원인까지 담긴다.

```text
Unknown   추정. 죽었는지 네트워크 문제인지 모른다
False     사실. 이유까지 알려준다
```

## 발견 5 — 축출 시각이 실험 1과 정확히 같다

```text
[실험 1]  09:28:19 (Ready→Unknown)  →  09:33:50 (deletionTimestamp)   331초
[실험 2]  11:29:00 (Ready→False)    →  11:34:31 (deletionTimestamp)   331초
```

**두 번 다 331초다.** `tolerationSeconds`는 300초이므로 31초가 남는다.

```text
확인된 것   두 실험에서 331초로 재현된다
미확인      나머지 31초의 출처
```

이벤트를 뒤졌으나 **taint를 붙였다는 기록이 없다.** taint 부착은 이벤트를 남기지 않는 것으로 보인다.

```text
$ kubectl get events -A --field-selector involvedObject.name=worker01 --sort-by='.lastTimestamp'
default   9m23s   Normal   NodeNotReady        node/worker01   Node worker01 status is now: NodeNotReady
default   97s     Warning  ImageGCFailed       node/worker01   rpc error ... containerd.sock
default   32s     Warning  ContainerGCFailed   node/worker01   rpc error ... containerd.sock
```

**추정** — taint 부착이 조건 변경보다 약 31초 늦고, 카운트다운은 taint 부착 시점부터 시작한다. **근거는 없다.**

다음 실험에서 이렇게 잡으면 확인된다.

```bash
while true; do
  echo "$(date '+%H:%M:%S') $(kubectl get node worker01 -o jsonpath='{.spec.taints[*].key}')"
  sleep 1
done
```

## 발견 6 — GC도 함께 실패한다

```text
ImageGCFailed       "이미지 정리 실패"
ContainerGCFailed   "컨테이너 찌꺼기 정리 실패"
```

**장애가 길어지면 2차 피해로 이어질 수 있다.**

```text
containerd 가 오래 죽어 있으면
  → 이미지가 안 지워지고 죽은 컨테이너가 안 치워진다
  → 디스크가 찬다
  → DiskPressure 조건이 붙는다
  → 또 다른 taint 가 붙는다
```

이번에는 12분이라 조건이 멀쩡했다.

```text
DiskPressure   False   KubeletHasNoDiskPressure
```

**하나의 고장이 다른 고장을 부르는 연쇄를 짧게 끊어서 안 본 것뿐이다.**

## 발견 7 — 다시 6개가 돌았다

```text
[클러스터가 아는 것]          [실제]
worker02 에 4개 Running       worker02 에 4개 Running
worker01 의 2개는 Terminating worker01 에 nginx 프로세스 2개 생존
→ "정상 4개"                  → 총 6개
```

**실험 1의 발견 7이 다른 원인으로 재현됐다.**

```text
실험 1   노드와 연락 두절 → 죽었는지 확신 못 함 → 죽일 수 없음
실험 2   노드는 살아있고 연락도 되는데
         컨테이너를 죽일 수단(containerd)이 없음
```

**두 번째가 더 답답하다.** 클러스터가 상황을 정확히 알고 있는데도 손을 못 쓴다.

`PHASE`와 `STATUS`가 갈리는 것도 그대로였다.

```text
NAME                          NODE       PHASE     DELETED
nginx-test-59b9b9cf79-gxhlv   worker01   Running   2026-08-10T02:34:31Z
nginx-test-59b9b9cf79-v8h85   worker01   Running   2026-08-10T02:34:31Z
```

## 복구

```text
11:40:34   containerd 재시작                  ← T1
11:40:41   Pod 갱신 시작                      T1 + 7초
11:40:42   Pod → Completed                    T1 + 8초
11:40:45   Node Ready                         T1 + 11초
11:43:39   worker01 nginx 프로세스 0개
```

### 발견 8 — 복구가 실험 1보다 느리다 (예측 오류)

```text
[실험 1]  kubelet 재시작 → Node Ready       1초 미만
[실험 2]  containerd 재시작 → Node Ready    11초
```

**"kubelet이 살아 있으니 더 빠를 것"이라 예측했으나 반대였다.**

이유가 관찰 출력에 보인다.

```text
11:40:04 worker01 NotReady
11:40:15 worker01 NotReady
11:40:25 worker01 NotReady
11:40:35 worker01 NotReady
11:40:45 worker01 Ready       ← 여기서 바뀜
```

**약 10초 간격으로 계속 찍혔다.** 내용이 안 바뀌어도 kubelet은 주기적으로 보고한다. 이것이 하트비트의 실물이다.

```text
11:40:34   containerd 가 살아남
11:40:35   보고 → 아직 NotReady (직전 상태 기준)
11:40:45   보고 → 이제 Ready
```

**복구가 느린 것이 아니라 다음 보고 주기를 기다린 것이다.**

```text
[실험 1]  kubelet 이 죽어 있었다
          → 재시작하면 처음부터 시작한다
          → 뜨자마자 보고. 주기를 기다릴 이유가 없다

[실험 2]  kubelet 이 계속 돌고 있었다
          → 이미 주기에 맞춰 돌고 있다
          → 다음 차례까지 기다린다
```

**"새로 시작"이 "이미 돌던 것"보다 빨랐다.** 직관과 반대다.

### 발견 9 — 일을 먼저 하고 보고는 나중이다

```text
11:40:41   Pod 갱신 시작      ← 먼저
11:40:45   Node Ready         ← 나중
```

```text
kubelet 이 containerd 복구를 감지
  1. 밀린 일부터 처리 — "이 Pod 들은 삭제 대상이었지" → 컨테이너 종료
  2. 다음 보고 주기에 상태 갱신 — "이제 Ready 입니다"
```

`Completed` 중간 상태도 실험 1과 같았다(SIGTERM → exit 0).

### 최종 상태

```text
$ kubectl get nodes
master01 / worker01 / worker02   전부 Ready

$ kubectl get pods -o wide -l app=nginx-test
nginx 4개. 전부 worker02

$ kubectl describe node worker01 | grep -A3 'Taints:'
Taints:  <none>

$ ps -ef | grep 'nginx: master' | grep -v grep    # worker01
(없음)
```

**자동 재분배는 이번에도 없었다**(실험 1 발견 11).

## 실험 1 vs 실험 2

| | 실험 1 (kubelet) | 실험 2 (containerd) |
|---|---|---|
| kubelet 상태 | dead | **active** |
| Lease 갱신 | 중단 | **계속됨** |
| `Ready` | **Unknown** | **False** |
| reason | `NodeStatusUnknown` | `KubeletNotReady` |
| message | — | **`container runtime is down`** |
| taint | `unreachable` | **`not-ready`** |
| **감지** | **52초** | **28초** |
| **복구** | **1초 미만** | **11초** |
| 축출까지 | 조건 + 331초 | 조건 + 331초 |
| 컨테이너 | 계속 실행 | 계속 실행 |
| `crictl` | 됨 | **안 됨** |
| GC | 정상 | **실패** |

**감지는 실험 2가 빠르고 복구는 실험 1이 빠르다. 이유가 같다.**

```text
kubelet 이 죽으면      감지  남이 추정해야 해서 느리다
                      복구  새로 시작하니 즉시

kubelet 이 살아있으면  감지  본인이 신고하니 빠르다
                      복구  주기를 기다리니 느리다
```

## 이번에 관측하지 못한 것

```text
1. taint 가 붙은 정확한 시각
   이벤트를 남기지 않는다. 331초 중 31초의 출처를 못 밝혔다

2. 장애가 길어졌을 때 DiskPressure 로 이어지는지
   12분이라 조건이 안 바뀌었다

3. worker01 의 nginx 가 정확히 언제 죽었는지
   watch 는 화면만 갱신하고 기록이 안 남는다
   11:40:42 로 추정되나 초 단위 확인은 못 했다

4. containerd 가 죽은 동안 worker01 의 Pod 가
   실제로 트래픽을 처리할 수 있었는지
   컨테이너는 살아 있었지만 Service 를 통해 접근되는지는 확인 안 함
```

**4번이 중요한 미확인 항목이다.** "프로세스가 살아있다"와 "요청을 받는다"는 다르다. 다음에 확인해야 한다.

## 운영 시사점

```text
1. containerd 재시작은 컨테이너를 죽이지 않는다
   런타임 업그레이드를 무중단으로 할 수 있는 근거다
   다만 그동안 노드는 NotReady 이고 5분 뒤 Pod 가 축출된다
   → 5분 안에 끝내면 축출 없이 넘어간다

2. 관측 도구가 대상에 의존하면 함께 죽는다
   crictl 은 containerd 가 없으면 못 쓴다
   ps, journalctl 처럼 의존하지 않는 수단을 알아둬야 한다

3. Ready 조건의 reason 과 message 를 봐야 한다
   NotReady 라는 사실만으로는 원인을 알 수 없다
   KubeletNotReady + "container runtime is down" 이 원인을 바로 알려준다

4. 장애가 길어지면 GC 실패가 2차 피해로 이어질 수 있다
```

## 검증 결과

```text
가설 1   컨테이너는 안 죽는다              ✅ shim 이 systemd 자식이라 생존
가설 2   kubelet 은 살아있지만 못 한다      ✅ "Skipping pod synchronization"
가설 3   실험 1보다 빠르게 감지된다         ✅ 28초 (실험 1은 52초)
가설 4   taint 가 not-ready 로 붙는다       ✅ Ready=False / KubeletNotReady

예측 오류  복구가 실험 1보다 빠를 것이라 예측했으나 반대였다 (발견 8)
           kubelet 이 살아있으면 다음 보고 주기를 기다려야 한다
```

---

# 실험 3 — master01 apiserver 중단 (2026-08-10)

## 개요

```text
대상       master01 의 kube-apiserver
방법       manifest 파일을 /tmp 로 이동
지속       약 8분 (12:07 ~ 12:15:09)
영향 범위  클러스터 전체의 제어 평면
결과       기존 Pod 와 트래픽은 무중단.
           단 apiserver 에 의존하는 앱(calico-kube-controllers)은 CrashLoopBackOff
```

## 앞의 두 실험과 무엇이 다른가

```text
[실험 1·2]  worker 노드 하나가 망가졌다
            → 클러스터가 감지하고 대응했다
            → kubectl 로 전부 관찰할 수 있었다

[실험 3]    감지하고 대응할 주체 자신이 죽는다
            → kubectl 이 안 된다
            → 관찰 수단이 사라진다
```

**"고장을 처리하는 쪽이 고장난다"** 는 상황이다. 그래서 **관측 준비가 실험의 절반**이었다.

## 멈추는 방법 — 자기참조 문제

**apiserver 자신이 Static Pod다.** `crictl stop`으로는 못 멈춘다. kubelet이 다시 살린다.

```text
사본(미러 Pod)   kubectl delete → 소용없고, 게다가 kubectl 이 곧 안 됨
선언(파일)        mv → 유일한 방법
실제(컨테이너)    crictl stop → kubelet 이 다시 살림
```

07 문서 3라운드의 세 층 실험이 여기서 실전으로 쓰인다.

```text
"apiserver 를 멈추려면 apiserver 를 안 거치는 방법을 써야 한다"
```

## 관측 준비 — kubectl 없이 보는 법

| 볼 것 | 도구 | 어디서 |
|---|---|---|
| 컨테이너 | `crictl ps` | master01 |
| kubelet 상태 | `journalctl -u kubelet -f` | 각 노드 |
| Service 트래픽 | `curl` 루프 | worker01 / worker02 |
| 클러스터 상태 | `crictl exec <etcd> etcdctl get` | master01 |
| 포트 | `ss -tlnp` | master01 |

관측 대상으로 Service를 하나 만들었다.

```text
$ kubectl expose deployment nginx-test --port=80 --type=NodePort --name=nginx-test-svc
NAME             TYPE       CLUSTER-IP     PORT(S)
nginx-test-svc   NodePort   10.99.58.152   80:32285/TCP
```

**트래픽 감시가 이번 실험의 핵심 관측**이다. worker01과 worker02에서 2초마다 요청을 보내고 시각과 함께 기록했다.

```bash
while true; do
  echo "$(date '+%H:%M:%S') $(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://192.168.8.142:32285)"
  sleep 2
done | tee /tmp/traffic-test.log
```

> `etcdctl`은 `kubectl exec`을 쓸 수 없으므로 `crictl exec`으로 들어가야 한다.
> 컨테이너 ID를 미리 확보해뒀다: `d3b9107b190e...`

## 가설

```text
1. Control Plane Pod 는 계속 돈다 (Static Pod)
2. worker 의 Pod 도 계속 돈다
3. Service 트래픽이 정상적으로 흐른다            ← 실험 2의 미확인 4번
4. kubectl 은 전부 실패한다
5. kubelet 은 살아서 재시도하지만 실패 로그만 쌓인다
6. Node 상태는 아무도 안 바꾼다 (감지할 주체가 없음)
7. etcd 는 정상. etcdctl 로 상태를 볼 수 있다
8. 복구하면 밀린 보고가 한꺼번에 몰린다
```

**전부 맞았다.** 다만 가설 3에 중요한 예외가 있었다(발견 2).

## 타임라인

```text
12:07경    apiserver 중단                              ← T0
           이벤트: Killing "Stopping container kube-apiserver"
12:07경    controller-manager / scheduler 재시작
           RESTARTS 3→4 / 2→3 (리더 상실로 자살, 발견 8)
12:08경    scheduler Readiness probe failed (500)
12:08경    calico-kube-controllers probe 실패 시작
12:11:01   트래픽 감시 시작. 200
12:12경    calico-kube-controllers Killing (liveness 실패)
12:12:26   apiserver 부재 확인. kubectl 실패
12:13~14   kubelet 재시도 반복
12:14경    calico-kube-controllers 재시작
12:15:09   manifest 복원                               ← T1
12:15:09   kubelet 이 apiserver Pod 볼륨 준비 시작
12:15:12   apiserver 응답 시작 (refused → forbidden)   T1 + 3초
12:15경    controller-manager 리더 획득 (새 UUID)
12:15경    RegisteredNode × 3
12:16경    scheduler 리더 획득 (새 UUID)
12:18:07   kubectl 완전 정상
12:22:29   전 Pod Running. calico-kube-controllers RESTARTS 7
트래픽      12:11:01 ~ 12:18:22 전 구간 200
```

**T0 는 명령 출력을 남기지 못했으나 이벤트가 기록했다.** 명령 기록보다 시스템 로그가 더 믿을 만하다는 실증이다.

## 발견 1 — 트래픽이 한 번도 안 끊겼다 ★

```text
[worker01]  12:11:01 ~ 12:14:23   전부 200   (apiserver 중단 중)
            12:15:18 ~ 12:18:22   전부 200   (복구 중 / 후)
[worker02]  12:11:41 ~ 12:14:23   전부 200
```

```text
kubectl   The connection to the server 192.168.8.143:6443 was refused
curl      200 200 200 200 200 ...
```

**06 문서에서 개념으로만 설명한 것이 증명됐다.**

```text
제어 평면    apiserver 중심. 마비됨
데이터 평면  커널의 iptables 규칙. 멀쩡함
```

kube-proxy가 이미 규칙을 깔아놨고 **그 규칙은 커널에 있다.** apiserver가 없어도 커널은 계속 패킷을 넘긴다.

> **설정하는 자와 전달하는 자는 다르다.**

## 발견 2 — 그런데 예외가 있다 ★

```text
14m 전   calico-kube-controllers  Readiness probe failed
         Error verifying datastore: Get "https://10.96.0.1:443/apis/..."
         dial tcp 10.96.0.1:443: connect: connection refused
13m 전   Liveness probe failed
9m33s 전 Killing — Container failed liveness probe, will be restarted
8m10s 전 Started
4m52s 전 BackOff — Back-off restarting failed container
```

**`calico-kube-controllers`가 죽었다 살았다를 반복했다.** 최종적으로 `RESTARTS 7`이었다.

### 왜 nginx는 멀쩡한데 이것은 죽었나

```text
nginx                     apiserver 를 안 쓴다   → 영향 0
calico-kube-controllers   apiserver 를 쓴다      → 죽는다
```

#### 전제 — apiserver 로 가는 경로는 두 개다

이 대목은 오해하기 쉬워 먼저 정리한다.

```text
[경로 A] 관리 경로 — 모든 노드에 항상 있다
  kubelet ──> apiserver     PodSpec 수신 / 상태 보고 / Lease 갱신

[경로 B] 애플리케이션 경로 — 앱마다 있을 수도 없을 수도
  컨테이너 ──> apiserver    앱이 자기 필요에 의해 직접 붙는다
```

**경로 B는 kubelet을 거치지 않는다.** 컨테이너는 격리된 리눅스 프로세스에 자기 IP가 붙은 것뿐이고, 네트워크가 있으면 어디든 붙을 수 있다. 컨테이너 입장에서 `10.96.0.1:443`은 **그냥 HTTPS 서버 하나**다.

Kubernetes는 이것을 전제로 설계되어 있다. 07 문서 2라운드의 3종 세트가 그 증거다.

```text
/var/run/secrets/kubernetes.io/serviceaccount/
├── ca.crt      상대(apiserver)를 검증한다
├── token       나를 증명한다
└── namespace

→ 아무도 안 시켰는데 모든 Pod 에 들어간다
→ "컨테이너가 apiserver 를 호출할 것" 을 예상한 설계
```

1라운드에서 `apiserver.crt`의 SAN에 `10.96.0.1`이 들어있던 것도 이 때문이다.

**nginx도 token을 갖고 있다. 쓸 수 있는데 쓸 일이 없어서 안 쓸 뿐이다.**

#### probe가 도는 순서

```text
1. kubelet 이 calico 컨테이너에게 묻는다        ← 같은 노드 안. 로컬
     "살아있나"

2. 컨테이너 안의 점검 코드가 자기 일을 해본다
     apiserver 호출 → connection refused        ← 경로 B 가 끊긴 것

3. 컨테이너가 kubelet 에게 "아니요" 를 답한다    ← 본인이 신고한 것

4. kubelet 이 컨테이너를 죽인다
5. restartPolicy: Always → 다시 띄운다 → 2번 반복 → CrashLoopBackOff
```

**kubelet은 이 판단에 apiserver를 쓰지 않는다.** kubelet이 아는 것은 "이 컨테이너가 계속 아니라고 답한다"뿐이고, apiserver가 죽었다는 사실은 모른다.

이벤트의 `Get "https://10.96.0.1:443/apis/..."`도 kubelet이 그 주소로 간 것이 아니라, **컨테이너가 실행한 점검 명령의 오류가 이벤트에 실린 것**이다.

> probe 종류(exec / httpGet)는 이벤트 메시지 형태로 추론한 것이며 확인하지 않았다.
> `kubectl -n kube-system describe deploy calico-kube-controllers | grep -A3 -i liveness`

#### 왜 죽이고 왜 다시 띄우는가 — 서로 다른 두 규칙이다

```text
[죽이는 판단]  liveness 가 실패했다 → 이 컨테이너는 정상이 아니다 → 종료
[띄우는 판단]  컨테이너가 종료됐다 → restartPolicy 가 Always 다 → 다시 띄운다
```

**두 번째는 probe와 무관하다.** "어떤 이유로든 컨테이너가 죽으면 다시 띄운다"는 별개 규칙이고, probe 실패는 그 이유 중 하나일 뿐이다.

```text
컨테이너가 종료되는 경우
  스스로 exit / OOM Kill / liveness 실패 / 노드 재부팅
        ↓
  전부 같은 규칙을 탄다 → restartPolicy 를 본다
```

```text
[죽이는 이유]
  liveness probe 에는 재시작 말고 할 수 있는 조치가 없다
  "정상이 아니다" 만 알고 "왜" 는 모르기 때문이다

  liveness 가 상정하는 고장 — 데드락, 메모리 누수, 연결 풀 고갈, 무한 루프
  → 전부 프로세스 내부 문제이고 전부 재시작으로 고쳐진다

[다시 띄우는 이유]
  restartPolicy: Always 가 기본값이다
  taint 의 tolerationSeconds: 300 처럼 안 써도 모든 Pod 에 붙는다
  Kubernetes 가 상정하는 워크로드가 "계속 떠 있어야 하는 서비스" 이기 때문
  한 번 하고 끝나는 일은 Job 이라는 별도 오브젝트로 뺐다
```

```text
[재시작이 통하는 경우]  원인이 컨테이너 안에 있다 → 새로 시작하면 사라진다
[이번 경우]             원인이 밖에 있다 → 새로 시작해도 그대로다
```

#### 재시작에 횟수 제한은 없다

```text
[Pod / Deployment / StatefulSet / DaemonSet]
  제한 없음. kubelet 은 포기하지 않는다

[Job / CronJob]
  backoffLimit: 6 (기본값). 6번 실패하면 Job 을 Failed 로 마킹하고 그만둔다
```

```text
서비스   "지금 안 되더라도 나중엔 될 수 있다" → 계속 시도
배치     "6번 해도 안 되면 잘못된 것이다"    → 사람을 부른다
```

> `Deployment`의 `progressDeadlineSeconds`(기본 600초)는 롤아웃 진전이 없을 때
> Deployment의 **상태 표시만** 실패로 바꾼다. Pod 재시작을 멈추지 않는다.
> "Deployment가 실패했다"와 "재시도를 그만뒀다"는 다르다.

대신 간격을 벌린다.

```text
10초 → 20 → 40 → 80 → 160 → 300 → 300 ... (상한에서 멈춤)

BackOff = "물러난다" 이지 "그만둔다" 가 아니다
```

**`CrashLoopBackOff`는 포기가 아니라 "다음 재시도를 기다리는 중"이다.** 일정 시간 정상 실행되면 카운터가 초기화된다. **apiserver를 살리자 calico가 스스로 돌아온 것**이 이 동작이다.

```text
관측: apiserver 중단 약 8분 → RESTARTS 7
      간격이 계속 10초였다면 40번 넘었을 것이다
      7번에 그친 것이 간격이 벌어졌다는 증거다
```

> **미확인**: 10초 시작 / 5분 상한 / 카운터 리셋 시간은 오래된 기본값이다.
> 최근 버전에는 이를 조정하는 kubelet 플래그와 feature gate 가 추가됐고,
> 이 클러스터는 v1.35.7이다. 해당 버전 공식 문서 확인이 필요하다.

#### 설계 원칙

```text
liveness probe 에 외부 의존성을 넣지 마라

readiness 에 넣는 것   ✓  "못 읽으면 트래픽 주지 마라"
liveness 에 넣는 것    ✗  "못 읽으면 나를 죽여라" → 재시작 폭풍

liveness   "내가 고장났나"      나 자신만 봐야 한다
readiness  "지금 일할 수 있나"   남에게 의존해도 된다
```

**실험 4에서 apiserver 자신이 같은 문제를 겪는다.** `/livez`가 etcd를 보는 것은 피할 수 없어 `failureThreshold: 8`로 완화한다.

### 그래서 결론을 다듬어야 한다

```text
[틀린 표현]
  apiserver 가 죽어도 데이터 평면은 멀쩡하다

[정확한 표현]
  네트워크 경로(iptables 규칙)는 멀쩡하다
  그 위에서 도는 앱이 apiserver 를 쓰면 그 앱은 죽는다
```

```text
apiserver 를 안 쓰는 앱   nginx, 대부분의 웹 서비스        영향 없음
apiserver 를 쓰는 앱      Operator, 컨트롤러, 서비스 메시,
                         Ingress 컨트롤러, 오토스케일러    죽는다
```

**"Kubernetes 위에 올린 앱"이 얼마나 Kubernetes에 의존하는지에 따라 장애 영향이 갈린다.**

### `10.96.0.1`의 정체

```text
Get "https://10.96.0.1:443/apis/crd.projectcalico.org/..."
     ^^^^^^^^^^
```

07 문서 1라운드에서 `apiserver.crt`의 SAN에 있던 그 IP다.

```text
IP Address:10.96.0.1   Service 대역 첫 IP. kubernetes 기본 Service
```

> Pod 안에서 apiserver에 접근할 때 쓰는 주소라 미리 넣어둔다.

**Pod가 실제로 그 주소를 쓰는 것이 여기서 확인된다.**

## 발견 3 — apiserver만 죽고 나머지는 산다

```text
[컨테이너]
kube-controller-manager   Running
kube-scheduler            Running
etcd                      Running
kube-apiserver            없음

[포트]
127.0.0.1:10257     controller-manager   LISTEN
127.0.0.1:10259     scheduler            LISTEN
127.0.0.1:2379      etcd                 LISTEN
192.168.8.143:2379  etcd                 LISTEN
6443                                     없음
```

**scheduler와 controller-manager는 프로세스로는 살아 있다.** 다만 apiserver에 못 붙으니 아무 일도 못 한다.

```text
살아있음 ≠ 동작함
```

## 발견 4 — etcdctl이 유일한 관측 수단이 된다

```text
$ sudo crictl exec d3b9107b190e etcdctl \
    --cacert=... --cert=... --key=... \
    get /registry/minions --prefix --keys-only
/registry/minions/master01
/registry/minions/worker01
/registry/minions/worker02
```

07 문서 4라운드에서 배운 것이 **실전에서 유일한 창구**가 됐다.

```text
apiserver 가 없어도 etcd 는 살아있다
        ↓
클러스터의 마지막 상태를 볼 수 있는 유일한 방법
```

`kubectl exec`을 못 쓰므로 `crictl exec`으로 들어가야 한다는 것도 실전 지식이다.

## 발견 5 — 자기참조: 미러 Pod를 지울 수 없다 ★

로그에서 2초마다 반복됐다.

```text
E0810 12:13:47 mirror_client.go:139] "Failed deleting a mirror pod"
  err="Delete https://192.168.8.143:6443/api/v1/namespaces/kube-system/pods/kube-apiserver-master01:
       dial tcp 192.168.8.143:6443: connect: connection refused"
  pod="kube-system/kube-apiserver-master01"
```

```text
kubelet:  "kube-apiserver.yaml 파일이 사라졌네"
          "3라운드에서 본 대로 미러 Pod 를 지워야 한다"
          "apiserver 에 삭제 요청을 보내자"
                ↓
          그 apiserver 가 방금 내가 죽인 그 apiserver 다
                ↓
          connection refused → 2초 뒤 재시도 → 영원히
```

**"apiserver를 지웠다는 사실을 apiserver에게 알려야 하는데 apiserver가 없다."**

부트스트랩 역설의 거울상이다.

```text
[부트스트랩]  apiserver 를 띄우려면 apiserver 가 필요하다
              → Static Pod 로 해결

[지금]        apiserver 를 지우려면 apiserver 가 필요하다
              → 해결 못 한다
```

## 발견 6 — 그 결과가 AGE에 남았다 ★

```text
kube-apiserver-master01           1/1  Running  0            6d19h
kube-scheduler-master01           1/1  Running  3 (15m ago)  2d20h
```

**apiserver는 방금 죽었다 살아났는데 `RESTARTS 0`에 `AGE 6d19h`다.**

3라운드와 비교하면 이상하다.

```text
[3라운드] scheduler 를 mv → 되돌림 → AGE 4s. 완전히 새 Pod
[실험 3]  apiserver 를 mv → 되돌림 → AGE 6d19h. 원래 그대로
```

### 이유는 발견 5다

```text
[3라운드 — scheduler]
  파일 삭제 → kubelet 이 미러 Pod 삭제 요청
            → apiserver 가 살아있음 → 삭제 성공
            → 복구 시 새 Pod 오브젝트 생성 → AGE 0

[실험 3 — apiserver]
  파일 삭제 → kubelet 이 미러 Pod 삭제 요청
            → apiserver 가 없음 → 실패 (무한 재시도)
            → etcd 의 Pod 오브젝트가 안 지워짐
            → 복구 시 기존 오브젝트를 갱신 → AGE 유지
```

**자기참조의 결과가 `AGE` 값에 흔적으로 남았다.**

`kube-scheduler`의 `AGE 2d20h`는 08-07 3라운드 실험의 흔적이다. **두 실험의 결과가 나란히 보인다.**

## 발견 7 — 재시도 정책이 항목마다 다르다

```text
Lease 갱신        interval="7s"     7초마다. 포기 안 함
노드 상태 보고     5회 재시도 → "update node status exceeds retry count"
                  포기하지만 다음 주기에 또 시도
미러 Pod 삭제     2초마다. 포기 안 함
이벤트 기록       10초마다 → "Unable to write event (retry limit exceeded!)"
                  ★ 완전히 포기한다
```

**이벤트만 포기한다.**

```text
Lease / 상태 / Pod   클러스터 동작에 필수 → 될 때까지 시도
이벤트               있으면 좋지만 없어도 동작 → 포기
```

### 다만 일부는 살아남았다

```text
로그에서는  "Unable to write event (retry limit exceeded!)"
그런데      12:07 의 Killing 이벤트가 복구 후 조회된다
```

kubelet이 버퍼에 들고 있다가 **복구 후 재전송**한 것이다.

```text
일부는 재전송 성공
일부는 재시도 한도를 넘겨 영영 소실
        ↓
"장애 중 이벤트는 일부만 남는다"
```

4라운드에서 "Event는 TTL로 사라진다"고 했는데, **애초에 기록조차 안 될 수 있다**는 것이 추가된다. **장애 분석에 제일 필요한 것이 그때의 이벤트인데 그것이 가장 잘 사라진다.**

## 복구

```text
12:15:09   manifest 복원                     ← T1
12:15:09   kubelet 이 apiserver Pod 볼륨 준비 (VerifyControllerAttachedVolume)
12:15:12   apiserver 응답 시작               T1 + 3초
12:18:07   kubectl 완전 정상
```

### 발견 8 — 오류 메시지가 바뀌는 순간이 부활의 증거다

```text
12:15:09   dial tcp 192.168.8.143:6443: connect: connection refused
                                              ^^^^^^^^^^^^^^^^^^
                                              서버가 없다

12:15:12   pods "kube-controller-manager-master01" is forbidden:
           User "system:node:master01" cannot get resource "pods"
           no relationship found between node 'master01' and this object
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
           서버가 답했다. 다만 거절했다
```

```text
connection refused   연결 자체가 안 됨    → 서버 없음
forbidden            연결은 됐는데 거절   → 서버 살아남
```

**3초 만에 응답이 시작됐다.**

### 발견 9 — Node Authorizer의 그래프 재구축 순간을 포착했다 ★

```text
no relationship found between node 'master01' and this object
```

**07 문서 2라운드에서 본 그 메시지다.**

```text
$ kubectl auth can-i get configmap/kube-root-ca.crt \
    --as=system:node:worker01 --as-group=system:nodes -n default
no - no relationship found between node 'worker01' and this object
```

4라운드의 이 문장이 이유다.

> apiserver가 재시작되면 그래프는 사라지고 etcd에서 다시 만든다.

```text
12:15:12   apiserver 가 방금 떴다
           → Node Authorizer 의 그래프가 아직 비어 있다
           → etcd 에서 읽어와 재구축하는 중

kubelet:  "kube-controller-manager-master01 Pod 상태 좀 줘"
Node Authorizer: "master01 에서 그 Pod 로 가는 경로가 있나?"
                 "...그래프가 아직 안 만들어졌는데?"
                 → no relationship found
```

**세 라운드가 한 줄의 오류 메시지에서 만난다.**

```text
2라운드   Node Authorizer 가 그래프를 탐색한다
4라운드   그래프는 apiserver 메모리에 있고 재시작하면 다시 만든다
실험 3    재시작 직후 그래프가 비어 있는 순간이 실제로 존재한다
```

일시적이며 곧 해소된다. 12:18:07에는 `kubectl`이 완전히 정상이었다.

## 발견 10 — 리더를 잃으면 스스로 죽는다 ★

```text
[실험 전 — 08-10 11:31]
controller-manager   master01_5b9fd15d-844d-420d-b39e-c86c328441e3
scheduler            master01_fbb1145e-48e6-4ccf-bba3-a26944085676

[복구 후 이벤트]
7m18s   LeaderElection  lease/kube-controller-manager
                        master01_77b75618-3b73-4051-9f7e-8e57fc19c413 became leader
6m21s   LeaderElection  lease/kube-scheduler
                        master01_1bb0c37b-497b-4ff0-a10d-48413f96357d became leader
```

**UUID가 바뀌었다.** 프로세스가 재시작되면서 새 신원으로 리더를 다시 잡았다.

이것이 12:07의 재시작(`RESTARTS 3→4`, `2→3`)을 설명한다.

```text
1. apiserver 죽음
2. controller-manager / scheduler 가 Lease 를 갱신 못 함
3. "나는 더 이상 리더가 아니다" 판단 → 스스로 종료
4. kubelet 이 재시작
5. 새 UUID 로 리더 시도 → apiserver 가 없어 대기
6. apiserver 복구 → 리더 획득
```

**확신 없이 계속 일하는 것보다 죽는 것이 안전하다.**

앞 절의 "중복 실행을 막는 방법 3 — 앱이 스스로 리더를 정한다"를 **Kubernetes 자기 컴포넌트가 지키고 있다.**

리더가 된 직후 이것을 했다.

```text
7m17s   RegisteredNode  node/master01 / worker01 / worker02
```

**node-controller가 새로 시작하며 노드 목록을 다시 읽은 것**이다. 리더 획득 1초 만이다.

## 발견 11 — 축출이 일어나지 않는다

```text
nginx-test 4개   worker01 2 / worker02 2   그대로 유지
```

실험 1·2에서는 5분 뒤 Pod가 다른 노드로 옮겨갔는데 이번에는 아무 일도 없었다.

```text
apiserver 가 죽으면
  → 노드 상태를 판정할 주체(node-controller)가 apiserver 를 통해 일한다
  → 판정 자체가 안 일어난다
  → taint 도 안 붙고 축출도 없다
```

**가설 6이 확인된다.** 감지 시스템 자신이 죽으면 아무것도 감지되지 않는다.

## 이번에 관측하지 못한 것

```text
1. T0(mv 실행) 시각을 명령 출력으로 못 남겼다
   이벤트로 복원했지만 "약 12:07" 수준의 정밀도다

2. kubectl 이 정확히 언제부터 되기 시작했는지
   12:18:07 에 확인했을 때 이미 정상. 그 사이 어딘가

3. apiserver 중단 중 etcd 의 데이터가 실제로 변하지 않았는지
   etcdctl 로 키 목록만 봤고 값 비교는 안 했다

4. calico-kube-controllers 가 죽어 있는 동안
   Calico 의 네트워크 정책 동기화가 실제로 멈췄는지
   트래픽은 정상이었지만 "새 정책을 적용했다면?" 은 확인 못 함
```

**4번이 중요한 미확인 항목이다.** 이번에는 네트워크 설정을 안 바꿨으므로 문제가 안 드러났다.

## 운영 시사점

```text
1. apiserver 장애는 "서비스 장애" 가 아니다
   기존 Pod 와 트래픽은 계속 동작한다
   장애 대응 시 "서비스가 죽었나" 와 "관리가 안 되나" 를 구분해야 한다

2. 다만 apiserver 에 의존하는 앱은 함께 죽는다
   Operator, Ingress 컨트롤러, 오토스케일러, 서비스 메시
   앱이 얼마나 Kubernetes 에 의존하는지 파악해둬야 한다

3. kubectl 없이 진단할 수단을 미리 알아둬야 한다
   crictl / journalctl / etcdctl / curl
   특히 etcdctl 은 crictl exec 으로 들어가야 한다는 것까지

4. Control Plane 이 하나면 이런 상황이 실제로 온다
   HA 구성(마스터 3대 + LB)이 필요한 이유가 이것이다

5. 장애 중 이벤트는 일부만 남는다
   "언제부터 이상했나" 를 이벤트로 재구성하는 데 한계가 있다
```

## 검증 결과

```text
가설 1   Control Plane Pod 는 계속 돈다        ✅ scheduler / cm / etcd 생존
가설 2   worker 의 Pod 도 계속 돈다            ✅ nginx 4개 그대로
가설 3   Service 트래픽이 흐른다               ✅ 전 구간 200
         (단 apiserver 에 의존하는 앱은 예외)   ← 발견 2
가설 4   kubectl 은 전부 실패한다              ✅ connection refused
가설 5   kubelet 은 살아서 재시도한다           ✅ 항목별 재시도 정책 확인
가설 6   Node 상태는 아무도 안 바꾼다           ✅ 축출 없음
가설 7   etcdctl 로 상태를 볼 수 있다           ✅ crictl exec 경유
가설 8   복구 시 밀린 보고가 몰린다             ✅ 리더 재선출 + RegisteredNode

예측 보완  "데이터 평면 무중단" 은 절반만 맞았다
           네트워크 경로는 무중단이지만
           apiserver 를 쓰는 앱은 CrashLoopBackOff 에 빠진다
```

---

# 실험 4 — master01 etcd 중단 (2026-08-10)

## 개요

```text
대상       master01 의 etcd
방법       manifest 파일을 /tmp 로 이동
지속       6분 17초 (13:33:29 ~ 13:39:46)
영향 범위  클러스터 전체의 제어 평면
결과       기존 Pod 와 트래픽은 무중단
           apiserver 는 살아있다가 5회 재시작
           controller-manager / scheduler 는 11~15초 만에 재시작
```

**로드맵 질문은 실험 3까지로 다 채워졌다.** 이 실험은 추가 학습이다.

```text
07 문서 4라운드에서 이렇게 썼다
  "etcd 를 잃으면 선언을 잃는다"
  "etcd 가 죽으면 apiserver 는 아무것도 못 한다"

그런데 잠깐 멈추는 것과 잃는 것은 다르다. 그 차이를 본다
```

## 실험 3과 무엇이 다른가 — 실패의 양상

```text
[실험 3 — apiserver 중단]
  서버가 없다 → connection refused
  즉시 실패. 오류 메시지가 원인을 알려준다

[실험 4 — etcd 중단]
  apiserver 는 살아있고 포트도 열려 있다
  요청은 받는데 etcd 에서 못 읽으니 응답을 못 한다
  → 매달린다
```

**"연결은 되는데 응답이 안 오는" 상태**다. 실무에서 훨씬 흔하고 진단하기 어렵다.

## 가설

```text
1. etcd 컨테이너가 사라진다
2. apiserver 는 살아있다. 포트 6443 은 열려 있다
   → 요청이 timeout 되거나 500 이 난다
3. apiserver 도 결국 재시작될 수 있다
   → livez probe 가 etcd 를 확인한다면
4. 트래픽은 여전히 무중단
5. calico-kube-controllers 는 또 죽는다
6. controller-manager / scheduler 도 리더 상실로 자살
```

**전부 맞았다.** 다만 관찰 중 잘못 판단한 것이 하나 있었다(발견 3).

## 타임라인

```text
13:33:29   etcd 중단                                    ← T0
13:33:40   controller-manager 재시작   (T0 + 11초)      RESTARTS 4→5
13:33:44   scheduler 재시작           (T0 + 15초)      RESTARTS 3→4
13:33:43   etcd 컨테이너 사라짐. 포트 2379 없음
           apiserver Running / 포트 6443 LISTEN
           kubectl 응답 없음 (timeout 15 가 죽임)
13:34경    calico-kube-controllers Liveness 실패
13:35경    apiserver Liveness probe failed (500)        ← 첫 실패
           apiserver Readiness probe failed (500)
13:35:03   관찰 — 아직 Running. 재시작 전
13:36:51~57 apiserver 로그: etcd 재시도 + Handler timeout 반복
13:37경    Killing — "failed liveness probe, will be restarted"
13:38경    calico-kube-controllers Killing
13:38:55   apiserver 마지막 재시작 (총 5회)
13:39:46   manifest 복원                                ← T1
13:39:57   etcd / apiserver 새 컨테이너 Started
13:40경    controller-manager 리더 획득 (새 UUID)
13:40경    RegisteredNode × 3
13:41경    scheduler 리더 획득 (새 UUID)
13:42:57   전부 정상
트래픽      13:32:56 ~ 13:35:36 전 구간 200
```

## 발견 1 — kubectl이 매달린다 ★

```text
$ timeout 15 kubectl get nodes 2>&1 | head -3
Terminated
```

**`Terminated`는 kubectl의 오류가 아니다.** `timeout 15`가 15초를 기다리다 죽인 것이다.

```text
[실험 3]  The connection to the server 192.168.8.143:6443 was refused
          → 즉시 실패. 오류가 원인을 알려준다

[실험 4]  (응답 없음)
          → kubectl 은 오류조차 못 낸다. 서버가 답을 안 하니까
```

apiserver의 핸들러 타임아웃(기본 60초)까지 기다려야 답이 온다.

```text
refused   "서버가 없다"          원인 명확. 어디를 볼지 안다
timeout   "서버가 답을 안 한다"   원인 불명확. 어디가 막혔는지 모른다
```

## 발견 2 — 밖에서 보면 정상이다

```text
$ sudo crictl ps | grep -E 'apiserver|etcd'
935885cc54f7a ... Running  kube-apiserver     ← apiserver 만. etcd 없음

$ sudo ss -tlnp | grep -E '6443|2379'
LISTEN *:6443  kube-apiserver                 ← 6443 만. 2379 없음
```

```text
포트 스캔       통과 (6443 LISTEN)
TCP 헬스체크    통과
프로세스 확인    정상
        ↓
겉보기에는 멀쩡한데 아무것도 안 된다
```

**"죽은 것"보다 "느린 것"이 진단하기 어렵다.** 로드밸런서의 TCP 헬스체크만으로는 이 상태를 못 걸러낸다.

## 발견 3 — apiserver는 바로 죽지 않고 3분 30초를 버틴다 ★

관찰 중에는 `RESTARTS 0`으로 보여 "안 죽는다"고 판단했으나 **틀렸다.**

```text
[관찰 중 — 13:35:03]
935885cc54f7a ... Running  kube-apiserver  0
                                           ^ crictl 의 ATTEMPT 열이다
                                             kubectl 의 RESTARTS 가 아니다

[복구 후 — 13:42:57]
kube-apiserver-master01   1/1  Running  5 (4m2s ago)
                                        ^ RESTARTS 5
```

**6분 동안 다섯 번 재시작했다.** 관찰을 1분 34초까지만 해서 놓쳤다.

### 원인 — 이벤트가 직접 알려준다

```text
31m 전   Warning  Unhealthy  pod/kube-apiserver-master01
         Liveness probe failed: HTTP probe failed with statuscode: 500

31m 전   Warning  Unhealthy  pod/kube-apiserver-master01
         Readiness probe failed: HTTP probe failed with statuscode: 500

29m 전   Normal   Killing    pod/kube-apiserver-master01
         Container kube-apiserver failed liveness probe, will be restarted
```

**`/livez`도 `/readyz`도 500을 반환했다. 둘 다 etcd를 확인한다.**

```text
[실험 중 세운 추정]  /livez 는 etcd 를 안 본다. 그래서 안 죽는다   ✗
[실제]              둘 다 etcd 를 본다. 다만 유예 횟수가 다르다
```

### 3과 8의 차이가 설계의 핵심이다

```text
$ sudo grep -A6 'livenessProbe:\|readinessProbe:\|startupProbe:' \
    /etc/kubernetes/manifests/kube-apiserver.yaml
livenessProbe    failureThreshold: 8    path: /livez
readinessProbe   failureThreshold: 3    path: /readyz
startupProbe     failureThreshold: 24   path: /livez
```

```text
readiness 3번 실패   →  "준비 안 됨" 표시. 트래픽을 안 보낸다
                        가벼운 조치. 되돌리기 쉽다

liveness 8번 실패    →  "죽었다" 판정. 컨테이너를 재시작한다
                        무거운 조치. 연결이 전부 끊긴다
```

**가벼운 조치는 빨리, 무거운 조치는 늦게.**

```text
etcd 가 1~2초 끊김
  → readiness 3번 실패 전에 회복 → 아무 일 없음

etcd 가 30초 끊김
  → readiness 실패 → "준비 안 됨"
  → liveness 는 아직 8번 안 됨 → 안 죽인다
  → 회복되면 그대로 이어간다

etcd 가 몇 분 끊김
  → liveness 8번 실패 → 재시작
  → "내 문제일 수도 있으니 한번 새로 시작해보자"
```

**"의존 대상이 아프면 일단 트래픽만 빼고, 오래가면 그때 나를 의심한다."**

만약 liveness도 3번이었다면 etcd가 잠깐 느려질 때마다 apiserver가 재시작되고, 재시작하는 동안 모든 연결이 끊겨 etcd가 더 느려지는 **재시작 폭풍**이 났을 것이다.

## 발견 4 — 컴포넌트마다 버티는 시간이 다르다

```text
controller-manager        11초         Lease 조회 실패 → 리더 상실 → 자살
scheduler                 15초         같음
calico-kube-controllers   약 1분       Liveness 실패 → kubelet 이 죽임
apiserver                 약 3분 30초   Liveness 8회 실패 → kubelet 이 죽임
```

**"죽었을 때의 피해"에 비례해 유예가 정해져 있다.**

```text
apiserver 가 죽으면 클러스터 전체가 마비된다
  → 최대한 버티게 한다 (failureThreshold 8)

controller-manager 는 죽어도 트래픽에 영향이 없다
  → 확신 없이 일하느니 즉시 자살한다 (리더 선출 규칙)
```

## 발견 5 — 실험 3의 반대편 로그를 봤다 ★

```text
$ sudo crictl logs --tail 40 935885cc54f7
E ... wrap.go:53] "Timeout or abort while handling"
    method="PATCH" URI="/api/v1/nodes/worker02/status?timeout=2s"
E ... timeout.go:140] "Post-timeout activity"
    method="PATCH" path="/api/v1/nodes/master01/status"
E ... timeout.go:140] "Post-timeout activity"
    path="/apis/coordination.k8s.io/v1/namespaces/kube-system/leases/kube-controller-manager"
```

**실험 3에서 kubelet 쪽에서 본 그 요청들이다.**

```text
[실험 3 — kubelet 로그 = 보내는 쪽]
  "Error updating node status" ... connection refused
  "Failed to ensure lease exists" ... connection refused

[실험 4 — apiserver 로그 = 받는 쪽]
  "Timeout or abort while handling" PATCH /api/v1/nodes/worker02/status
  "Post-timeout activity" .../leases/kube-controller-manager
```

**같은 요청을 양쪽에서 본 셈이다.**

### `?timeout=2s` — 클라이언트가 먼저 포기한다

```text
URI="/api/v1/nodes/worker02/status?timeout=2s"
                                   ^^^^^^^^^ kubelet 이 건 제한 시간
```

```text
kubelet:   "2초 안에 답 없으면 끊는다"
apiserver: (etcd 응답을 기다리는 중)
2초 경과 → kubelet 이 연결을 끊음
apiserver: "답을 쓰려는데 상대가 없네" → "Post-timeout activity"
```

### etcd 클라이언트의 오류가 두 단계로 변한다

```text
retrying of unary invoker failed
  target="etcd-endpoints://.../127.0.0.1:2379"
  method="/etcdserverpb.KV/Txn"      쓰기
  method="/etcdserverpb.KV/Range"    읽기

error: connection refused    처음. 즉시 실패
       DeadlineExceeded      나중. 연결 시도하다 시간 초과
```

apiserver의 etcd 클라이언트가 **연결 재시도 백오프**를 하기 때문이다.

## 발견 6 — etcd의 AGE가 유지됐다 (자기참조 재현) ★

```text
etcd-master01   1/1  Running  0  6d20h
                              ^  ^^^^^
                        RESTARTS  AGE
```

**방금 죽었다 살아났는데 `RESTARTS 0`에 `AGE 6d20h`다.**

실험 3에서 apiserver가 그랬다. **같은 이유이며 한 다리 건넜다.**

```text
etcd.yaml 을 mv
  → kubelet 이 etcd 미러 Pod 삭제 요청
  → apiserver 에 보내야 한다
  → 그런데 그 apiserver 는 etcd 가 없어서 응답을 못 한다
  → 삭제 실패
  → etcd 의 Pod 오브젝트가 그대로 남는다
  → 복구 시 기존 오브젝트를 갱신 → AGE 유지
```

```text
[실험 3]  apiserver 를 지우려면 apiserver 가 필요하다
[실험 4]  etcd 를 지우려면 apiserver 가 필요한데
          그 apiserver 는 etcd 가 있어야 동작한다
```

**자기참조가 `AGE` 값에 두 번 흔적을 남겼다.**

## 발견 7 — 트래픽 무중단 (세 실험 연속)

```text
[worker01]  13:32:56 ~ 13:35:36   전부 200
[worker02]  13:32:56 ~ 13:35:36   전부 200
```

**etcd가 죽어도 트래픽은 흐른다.**

```text
사용자 요청 → iptables 규칙 → Pod
              ↑ 커널에 있다. etcd 도 apiserver 도 안 거친다
```

## 발견 8 — calico의 오류도 양상이 바뀌었다

```text
[실험 3]  dial tcp 10.96.0.1:443: connect: connection refused
[실험 4]  Get "https://10.96.0.1:443/..." : context deadline exceeded
```

**같은 앱, 같은 주소, 다른 실패 방식이다.** 실험 3·4의 차이가 의존하는 앱에도 그대로 전달된다.

```text
실험 3   서버가 없다        → refused
실험 4   서버가 답을 안 한다 → deadline exceeded
```

이번에도 `Liveness probe failed` → `Killing` → 재시작을 반복했다.

## 복구

```text
13:39:46   manifest 복원                     ← T1
13:39:57   etcd / apiserver 새 컨테이너 Started  T1 + 11초
13:40경    controller-manager 리더 획득 (master01_11934754-...)
13:40경    RegisteredNode × 3
13:41경    scheduler 리더 획득 (master01_50186adf-...)
13:42:57   전부 정상
```

### 리더 UUID가 또 바뀌었다

```text
[실험 3 복구 후]
controller-manager   master01_77b75618-3b73-4051-9f7e-8e57fc19c413
scheduler            master01_1bb0c37b-497b-4ff0-a10d-48413f96357d

[실험 4 복구 후]
controller-manager   master01_11934754-8c42-4a5e-b85e-53e575272a9c
scheduler            master01_50186adf-d74a-4d7b-a869-d2490dc4682e
```

**두 실험 다 같은 패턴이다.** 리더 상실 → 자살 → 재시작 → 새 UUID로 재획득.

복구 직후 `kube-scheduler`도 잠깐 `Readiness probe failed: 500`을 겪었다. apiserver가 막 떠서 응답이 느렸기 때문이다.

## 실험 3 vs 실험 4

| | 실험 3 (apiserver) | 실험 4 (etcd) |
|---|---|---|
| apiserver 프로세스 | **없음** | **살아있다가 5회 재시작** |
| 포트 6443 | 닫힘 | **열림** |
| `kubectl` | `connection refused` (즉시) | **응답 없음 (60초 대기)** |
| 원인 파악 | 쉬움 | **어려움** |
| cm / scheduler | 재시작 | **11초 / 15초 만에 재시작** |
| calico 오류 | `refused` | **`deadline exceeded`** |
| 미러 Pod 삭제 | 실패 (자기참조) | **실패 (한 다리 건너)** |
| AGE 유지된 것 | apiserver | **etcd** |
| 트래픽 | 200 | **200** |

## 이번에 관측하지 못한 것

```text
1. /livez 와 /readyz 의 응답 본문
   kubectl get --raw 도 timeout 되어 어느 체크가 실패했는지 못 봤다
   500 이라는 것과 "etcd 때문" 이라는 것은 이벤트로만 간접 확인

2. apiserver 의 첫 재시작 정확한 시각
   이벤트가 분 단위로 반올림되어 "13:37경" 수준

3. 다섯 번의 재시작 각각의 시각
   이벤트가 집계·중복 제거되어 한 번만 보인다

4. etcd 가 죽은 동안 데이터가 실제로 변하지 않았는지
   재시작 전후 키 개수 비교를 안 했다
```

## 운영 시사점

```text
1. TCP 헬스체크만으로는 이 상태를 못 걸러낸다
   포트는 열려 있고 프로세스도 살아있다
   로드밸런서 헬스체크는 HTTP 응답까지 확인해야 한다

2. "느린 장애" 가 "죽은 장애" 보다 진단하기 어렵다
   refused 는 원인을 알려주지만 timeout 은 아무것도 안 알려준다

3. liveness 와 readiness 의 failureThreshold 를 다르게 잡아야 한다
   의존 대상의 장애로 자기가 재시작되면 안 된다
   readiness 는 짧게, liveness 는 넉넉하게

4. 컴포넌트마다 실패 시 행동이 다르다
   apiserver 는 버틴다 / controller-manager 는 즉시 자살한다
   "죽었을 때의 피해" 에 비례한 설계다

5. etcd 를 잠깐 멈추는 것은 되돌릴 수 있다
   데이터는 그대로다. "멈춤" 과 "손실" 은 다르다
   다만 그동안 클러스터 관리는 완전히 불가능하다
```

## 검증 결과

```text
가설 1   etcd 컨테이너가 사라진다                 ✅
가설 2   apiserver 는 살아있고 포트는 열려 있다     ✅ kubectl 은 매달린다
가설 3   apiserver 도 결국 재시작될 수 있다        ✅ 3분 30초 뒤 시작, 총 5회
가설 4   트래픽은 무중단                          ✅ 전 구간 200
가설 5   calico-kube-controllers 는 죽는다        ✅ 오류 양상만 다름
가설 6   cm / scheduler 도 리더 상실로 자살        ✅ 11초 / 15초

관찰 오류  실험 중 "apiserver 가 안 죽는다" 고 판단했으나 틀렸다
           crictl 의 ATTEMPT 열을 kubectl 의 RESTARTS 로 착각했고,
           관찰을 1분 34초에서 멈춰 그 뒤의 재시작을 놓쳤다
```

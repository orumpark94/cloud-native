# 06. Calico CNI 설치 — 3대가 Ready로 전환

현재 3대 모두 `NotReady`다. 이유는 명확하게 확인되었다.

```text
Ready   False   KubeletNotReady
        container runtime network not ready: NetworkReady=false
        reason:NetworkPluginNotReady
        message:Network plugin returns error: cni plugin not initialized
```

이 단계에서 이것이 해소된다.

---

## 왜 Operator가 아니라 Manifest 방식인가

Calico 공식 문서는 **Tigera Operator 방식을 권장**한다. 그럼에도 이 프로젝트는 **단일 Manifest 방식**을 쓴다.

| 방식 | 동작 | 학습 관점 |
|---|---|---|
| Operator | Operator 설치 → `Installation` CR 적용 → **Operator가 알아서** 리소스 생성 | 실제 K8s 리소스가 추상화됨 |
| **Manifest** | YAML 하나에 DaemonSet·Deployment·RBAC·CRD가 **전부 명시** | 무엇이 생성되는지 파일로 읽을 수 있음 |

로드맵 학습 원칙 1의 일관된 적용이다.

```text
Minikube 대신 kubeadm       Control Plane 구성을 직접 보려고
Helm 대신 순수 Manifest      리소스 구조를 직접 보려고
Operator 대신 Manifest       CNI가 어떤 K8s 리소스로 구현되는지 보려고
```

**핵심 확인 사항**: CNI는 특별한 무언가가 아니라 **DaemonSet + Deployment + RBAC**로 구현된 평범한 Kubernetes 워크로드다. Operator를 쓰면 이 사실이 가려진다.

Operator 패턴은 로드맵 9~10단계(Argo CD)에서 다룬다.

---

## 설치 전 상태 기록

로드맵 결과물 **"CNI 설치 전후 Pod Network 비교"** 의 "전" 데이터다.

```bash
# master01에서
kubectl get nodes -o wide
kubectl get pods -A -o wide
kubectl describe node master01 | grep -A15 Conditions

# 3대 각각에서
ls -la /etc/cni/net.d/
ls /opt/cni/bin/
ip -br addr show
ip route
```

### 예상 상태 (설치 전)

```text
/etc/cni/net.d/     비어 있음 (.kubernetes-cni-keep 뿐)
/opt/cni/bin/       표준 플러그인 20개 (calico, calico-ipam은 없음)
ip -br addr         lo, ens33 뿐 — cali*, tunl*, vxlan.calico 없음
ip route            노드 대역 경로만 — Pod 대역(10.244.x) 경로 없음
Node                3대 모두 NotReady
CoreDNS             2개 Pending
```

---

## 설치 절차

### 1단계. Manifest 다운로드 (적용은 아직)

```bash
curl -O https://raw.githubusercontent.com/projectcalico/calico/v3.32.1/manifests/calico.yaml
```

**바로 `kubectl apply -f <URL>`을 하지 않는 이유**

- 무엇이 설치되는지 읽어보기 위해
- Pod CIDR을 우리 값으로 수정해야 하므로
- 이 파일이 **우리 클러스터의 선언 기록**이 된다. 나중에 재구축하거나 GitOps로 옮길 때 그대로 쓰인다

### 2단계. 무엇이 만들어지는지 확인

```bash
grep '^kind:' calico.yaml | sort | uniq -c
wc -l calico.yaml
```

**예상되는 리소스 종류**

| 리소스 | 역할 |
|---|---|
| `CustomResourceDefinition` | `IPPool`, `BGPPeer` 등 Calico 전용 오브젝트 타입 등록 |
| `DaemonSet` (calico-node) | **모든 노드에 하나씩** 배치. CNI 바이너리와 설정을 노드에 설치하고 라우팅 관리 |
| `Deployment` (calico-kube-controllers) | 클러스터 전체 상태 관리 (노드 삭제 시 IP 회수 등) |
| `ServiceAccount` / `ClusterRole` / `ClusterRoleBinding` | Calico가 apiserver에 접근할 권한 |
| `ConfigMap` | CNI 설정 템플릿 |

**여기서 확인할 것**: CNI가 커널 모듈이나 특수한 시스템 데몬이 아니라, **평범한 DaemonSet**이라는 사실이다. 노드마다 하나씩 도는 Pod가 `/etc/cni/net.d/`에 설정을 쓰고 라우팅을 관리한다.

DaemonSet의 볼륨 마운트를 보면 어떻게 노드 파일시스템을 건드리는지 보인다.

```bash
grep -A5 -B2 'cni-bin-dir\|cni-net-dir' calico.yaml
```

### 3단계. Pod CIDR 설정 ★ 가장 중요

```bash
grep -n -A3 'CALICO_IPV4POOL_CIDR' calico.yaml
```

기본 상태는 **주석 처리**되어 있다.

```yaml
# - name: CALICO_IPV4POOL_CIDR
#   value: "192.168.0.0/16"
```

공식 문서는 "kubeadm 환경에서는 Calico가 자동으로 감지한다"고 설명한다. **그럼에도 명시적으로 지정한다.**

**왜 자동 감지에 의존하지 않는가**

- 자동 감지가 실패해도 **오류 없이 기본값(`192.168.0.0/16`)으로 넘어간다.** 그 값은 우리 노드 IP 대역(`192.168.8.x`)과 겹쳐 진단이 어려운 라우팅 장애를 일으킨다
- 이 파일이 우리 클러스터의 선언 기록인데, 중요한 값이 파일에 안 적혀 있으면 나중에 읽는 사람이 알 수 없다
- 명시하면 적용 후 `kubectl get ippool`로 **의도한 값과 실제 값을 대조**할 수 있다

주석을 풀고 값을 바꾼다.

```yaml
- name: CALICO_IPV4POOL_CIDR
  value: "10.244.0.0/16"
```

들여쓰기를 정확히 맞춘다. 주변 환경변수와 같은 깊이여야 한다.

```bash
grep -n -A3 'CALICO_IPV4POOL_CIDR' calico.yaml     # 수정 후 재확인
```

**Phase 5에서 `kubeadm init --pod-network-cidr=10.244.0.0/16`에 준 값과 맞춰서 쓴다.**

> **2026-08-11 수정.** 원래는 "불일치하면 Kubernetes는 노드에 `10.244.x.0/24`를 할당했다고 알고 있는데 Calico는 다른 대역에서 IP를 발급해 통신이 성립하지 않는다"고 적었으나 **부정확하다.**
>
> **Calico는 `node.spec.podCIDR`을 읽지 않는다.** 기본 IPAM인 `calico-ipam`은 IPPool에서 `/26` 블록을 떼어 쓰므로, 두 값이 어긋나도 Pod는 정상적으로 IP를 받고 통신도 된다. 실제로 이 클러스터가 그 상태다(`podCIDR`은 `/24`, 실제 IP는 `/26` 블록에서 나온다).
>
> 맞추는 진짜 이유는 두 가지다.
> 1. 대역 자체가 노드 IP(`192.168.8.x`)와 겹치면 안 된다 — Calico 기본값 `192.168.0.0/16`이 위험한 이유는 이것이지 `podCIDR`과 달라서가 아니다.
> 2. `kube-proxy`가 `clusterCIDR`로 "Pod 대역인지 외부인지"를 판단한다. 바깥쪽 `/16`이 다르면 NAT 판단이 어긋난다.
>
> 즉 **맞춰야 하는 것은 바깥쪽 `/16`이고, 안쪽을 `/24`로 자르든 `/26`으로 자르든 무관하다.** 상세는 [00-environment.md](00-environment.md)의 "이 값이 나오는 두 곳" 절 참조.

### 4단계. 적용

```bash
kubectl apply -f calico.yaml
```

### 5단계. 실시간 관찰 ★

**이 단계가 이번 Phase의 핵심이다.** 적용 직후 상태 전이를 관찰한다.

터미널 두 개를 띄우는 것을 권한다.

```bash
# 터미널 1
kubectl get pods -n kube-system -w

# 터미널 2
kubectl get nodes -w
```

### 예상되는 순서

```text
calico-node DaemonSet이 3개 노드에 스케줄됨
  ↑ 일반 Pod인데 어떻게 배치되나? hostNetwork를 쓰기 때문에 CNI 없이도 뜬다

각 노드의 calico-node Pod가 초기화
  → /opt/cni/bin/ 에 calico, calico-ipam 바이너리 복사
  → /etc/cni/net.d/10-calico.conflist 설정 파일 작성   ★ 여기가 전환점

kubelet이 CNI 설정을 인식
  → NetworkReady=true 로 전환
  → Node가 Ready

Node가 Ready가 되면 not-ready taint 제거
  → Pending이던 CoreDNS가 스케줄됨
  → CNI가 CoreDNS Pod에 10.244.x.x IP 할당
  → CoreDNS Running
```

**3대가 거의 동시에 `Ready`로 뒤집히는 것**을 보게 된다. 각 노드의 calico-node가 독립적으로 자기 노드에 설정을 쓰기 때문이다.

---

## 검증

### A. 클러스터 상태

```bash
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

**기대 결과**

- 3대 모두 `Ready`
- `calico-node` Pod 3개 Running (노드마다 하나) — IP는 **노드 IP**(hostNetwork)
- `calico-kube-controllers` 1개 Running — IP는 **10.244.x.x**(일반 Pod)
- `coredns` 2개 Running — IP는 **10.244.x.x**

`calico-node`와 `calico-kube-controllers`의 IP 대역이 다른 것을 확인한다. 전자는 노드 네트워크를 직접 다뤄야 해서 hostNetwork를 쓰고, 후자는 apiserver와만 통신하면 되므로 일반 Pod다.

### B. 노드 파일시스템 변화 (3대에서)

```bash
ls -la /etc/cni/net.d/
cat /etc/cni/net.d/10-calico.conflist
ls -la /opt/cni/bin/ | grep -i calico
```

`10-calico.conflist`가 **새로 생겼다.** 이 파일 하나가 없어서 15시간 동안 `NotReady`였다.

`/opt/cni/bin/`에 `calico`, `calico-ipam`이 추가되었다. **표준 플러그인 20개는 원래 있었고**, Calico가 자기 것 2개를 얹은 것이다.

### C. 네트워크 인터페이스와 라우팅 (3대에서)

```bash
ip -br addr show
ip route
```

**새로 생긴 것**

- `tunl0` 또는 `vxlan.calico` — 노드 간 Pod 트래픽 터널
- `cali*` — Pod마다 하나씩 생기는 veth 인터페이스 (Pod가 있는 노드에만)
- 라우팅 테이블에 `10.244.x.0/24` 경로 — 다른 노드의 Pod 대역으로 가는 길

**라우팅 테이블이 핵심이다.** 다른 노드의 Pod 대역이 그 노드를 향하도록 경로가 잡혀 있어야 노드 간 Pod 통신이 성립한다. 이것이 Phase 2에서 `net.ipv4.ip_forward=1`을 설정한 이유와 연결된다.

### D. Calico가 인식한 IP Pool

```bash
kubectl get ippool -o yaml | grep -A5 cidr
```

**`10.244.0.0/16`이어야 한다.** `192.168.0.0/16`이 나오면 3단계 수정이 반영되지 않은 것이므로 즉시 조치한다.

### E. 각 노드에 할당된 Pod 대역

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.podCIDR}{"\n"}{end}'
```

Controller Manager가 `10.244.0.0/16`을 노드별로 `/24`씩 쪼개 나눠준 결과다.

```text
master01   10.244.0.0/24
worker01   10.244.1.0/24
worker02   10.244.2.0/24
```

**이것이 Kubernetes 쪽 선언이고, Calico는 이 범위 안에서 실제 IP를 발급한다.** 두 값이 일치해야 하는 이유가 여기 있다.

### F. Pod 간 통신 테스트 ★ 최종 검증

```bash
kubectl run test1 --image=busybox --restart=Never -- sleep 3600
kubectl run test2 --image=busybox --restart=Never -- sleep 3600
kubectl get pods -o wide
```

**서로 다른 노드에 배치되었는지 확인한다.** 같은 노드면 하나를 지우고 다시 만든다.

```bash
kubectl exec test1 -- ping -c 3 <test2의 Pod IP>
```

**같은 노드 안 통신은 검증 가치가 낮다.** CNI에 문제가 있어도 성공할 수 있기 때문이다. **다른 노드의 Pod와 통신되어야** 라우팅과 터널이 정상이라는 뜻이다.

### G. DNS 동작 확인

```bash
kubectl exec test1 -- nslookup kubernetes.default
```

이것은 CoreDNS와 kube-proxy를 함께 검증한다.

```text
test1이 DNS 질의
→ /etc/resolv.conf 의 nameserver = CoreDNS의 ClusterIP (10.96.0.10)
→ kube-proxy가 만든 iptables 규칙이 실제 CoreDNS Pod IP로 DNAT
→ CoreDNS가 응답
```

실패하면 CoreDNS 또는 kube-proxy 문제다. **Phase 2에서 설정한 `net.bridge.bridge-nf-call-iptables=1`이 여기서 검증된다** — 이 값이 없으면 iptables 규칙이 적용되지 않아 이 단계에서 실패한다.

### 정리

```bash
kubectl delete pod test1 test2
```

---

## 실행 결과 기록

### 설치 전 상태 (2026-08-04 09:49)

**노드 3대 공통** — master01 / worker01 / worker02 모두 동일했다.

```text
$ ls -la /etc/cni/net.d/
-rw-r--r-- 1 root root 0 Dec 18  2025 .kubernetes-cni-keep
                                       # CNI 설정 파일 없음

$ ls /opt/cni/bin/ | grep -i calico
(출력 없음)                             # calico 바이너리 없음 (표준 플러그인 20개는 존재)

$ ip -br addr show
lo       UNKNOWN   127.0.0.1/8 ::1/128
ens33    UP        192.168.8.143/24    # worker01=.142, worker02=.141
                                       # cali*, tunl*, vxlan.calico 없음

$ ip route | grep 10.244
(출력 없음)                             ★ 데이터 평면이 존재하지 않는다는 결정적 지표

$ ip route
default via 192.168.8.2 dev ens33 proto static
192.168.8.0/24 dev ens33 proto kernel scope link src 192.168.8.143
                                       # 노드 대역 경로뿐
```

**클러스터 상태**

```text
$ kubectl get nodes -o wide
NAME       STATUS     ROLES           AGE   VERSION
master01   NotReady   control-plane   16h   v1.35.7
worker01   NotReady   <none>          16h   v1.35.7
worker02   NotReady   <none>          16h   v1.35.7

$ kubectl get pods -A -o wide
coredns-...-2dwfb                  0/1   Pending   <none>          <none>
coredns-...-jhlw8                  0/1   Pending   <none>          <none>
etcd-master01                      1/1   Running   192.168.8.143   master01
kube-apiserver-master01            1/1   Running   192.168.8.143   master01
kube-controller-manager-master01   1/1   Running   192.168.8.143   master01
kube-proxy-c8rqh                   1/1   Running   192.168.8.142   worker01
kube-proxy-nbt49                   1/1   Running   192.168.8.141   worker02
kube-proxy-zbzcj                   1/1   Running   192.168.8.143   master01
kube-scheduler-master01            1/1   Running   192.168.8.143   master01

$ kubectl describe node master01 | grep -A15 Conditions
Ready   False   Tue, 04 Aug 2026 09:49:17 +0900   Mon, 03 Aug 2026 17:11:58 +0900
        KubeletNotReady
        container runtime network not ready: NetworkReady=false
        reason:NetworkPluginNotReady
        message:Network plugin returns error: cni plugin not initialized
```

**읽어낼 점**

| 관찰 | 의미 |
|---|---|
| `ip route`에 `10.244` 경로 없음 | 데이터 평면 부재. 노드 간 Pod 통신 경로가 아예 없다 |
| Ready의 `LastTransitionTime`이 init 시각(8/3 17:11) 그대로 | **16시간 동안 한 번도 Ready가 된 적이 없다.** 조건이 계속 False |
| Control Plane 5개는 Running, IP는 노드 IP | hostNetwork 사용 → CNI 없이 동작 |
| CoreDNS만 Pending, IP `<none>` | 일반 Pod라 CNI가 없으면 IP를 받지 못함 |
| `MemoryPressure`/`DiskPressure`/`PIDPressure`는 모두 False | 자원 문제가 아니다. **오직 네트워크 때문에 NotReady** |

마지막 항목이 중요하다. Node가 `NotReady`인 원인이 자원 부족이나 kubelet 장애가 아니라 **네트워크 플러그인 하나**라는 것이 Conditions 표에서 명확히 분리되어 보인다.

### YAML 편집 과정에서 겪은 문제 (기록)

`CALICO_IPV4POOL_CIDR` 주석을 푸는 과정에서 **들여쓰기 오류로 두 번 실패**했다.

```text
error: error parsing calico.yaml: error converting YAML to JSON:
       yaml: line 210: mapping values are not allowed in this context
```

**원인**: 주석 `# `은 두 글자인데 `#`만 제거해 공백이 한 칸 남았다.

```yaml
            - name: CALICO_IPV4POOL_CIDR      # 12칸 + '- '  → name은 14칸에서 시작
               value: "10.244.0.0/16"         # 15칸  ✗ 14칸이어야 함
```

두 번째 실패는 **편집 중 무관한 줄(`CALICO_DISABLE_FILE_LOGGING`의 value)까지 밀린 것**이었다.

**배운 것 3가지**

1. **에러의 줄 번호는 파일 전체 기준이 아니다.** `calico.yaml`은 `---`로 구분된 다중 문서이며, 줄 번호는 **문제가 발생한 문서 내의 상대 위치**다. 보고된 `line 210`의 실제 위치는 파일의 7634줄이었다. `grep -n`으로 실제 위치를 찾아야 한다.

2. **에러를 따라가며 하나씩 고치면 두더지 잡기가 된다.** 파서는 가장 먼저 깨진 곳 하나만 알려준다. 원본과 `diff`를 떠서 **변경 사항 전체를 한 번에 확인**하는 편이 빠르다.

   ```bash
   curl -s <원본 URL> -o /tmp/calico-orig.yaml
   diff /tmp/calico-orig.yaml calico.yaml     # 의도한 2줄만 나와야 정상
   ```

3. **적용 전에 반드시 검증한다.**

   ```bash
   kubectl apply -f calico.yaml --dry-run=client
   ```

   첫 시도에서 이것을 건너뛴 탓에 **40여 개 리소스가 생성된 뒤 파싱이 중단**되어 부분 적용 상태가 되었다. (`kubectl apply`는 멱등이므로 재적용으로 복구되지만, 검증을 먼저 했다면 클러스터를 건드리지 않고 오류만 확인할 수 있었다.)

**로드맵과의 연결**: 이 경험이 7단계에서 Helm으로 전환하는 이유다.

> 순수 Manifest 작성 → 각 리소스의 동작 이해 → 환경별 중복과 설정 차이 경험 → **Template과 Packaging 필요성 체감** → Helm Chart로 전환

Helm의 `values.yaml`은 들여쓰기를 건드리지 않고 값만 바꾸게 해준다. 지금 겪은 문제가 정확히 그것이 해결하는 문제다.

### 설치 후 상태 (2026-08-04 09:53)

#### Pod 생성 순서 관찰 (`kubectl get pods -n kube-system -w`)

```text
calico-node-5khhz      0/1  Pending            0s
calico-node-5khhz      0/1  Init:0/3          12s     ← init 컨테이너 3개
calico-node-5khhz      0/1  Init:1/3          13s
calico-node-5khhz      0/1  Init:2/3          17s
coredns-...-jhlw8      0/1  ContainerCreating 16h     ★ 16시간 Pending이던 것이 움직임
calico-node-5khhz      0/1  PodInitializing   34s
calico-node-5khhz      0/1  Running           35s
calico-node-5khhz      1/1  Running           46s     ← Readiness 통과까지 11초
coredns-...-2dwfb      1/1  Running           16h
calico-kube-controllers 1/1 Running           58s
```

**주목할 점**: `calico-node`가 `Init:2/3`을 지나는 시점에 **16시간 동안 `Pending`이던 CoreDNS가 `ContainerCreating`으로 전환**되었다. init 컨테이너 중 하나가 `/etc/cni/net.d/`에 설정을 쓰는 순간 Node가 `Ready`가 되고, `not-ready` taint가 제거되어 CoreDNS가 스케줄된 것이다.

#### 클러스터 상태

```text
$ kubectl get nodes -o wide
NAME       STATUS   ROLES           AGE   VERSION
master01   Ready    control-plane   16h   v1.35.7
worker01   Ready    <none>          16h   v1.35.7
worker02   Ready    <none>          16h   v1.35.7

$ kubectl get pods -A -o wide
calico-kube-controllers-...   1/1  Running  10.244.30.66    worker02   ← 일반 Pod
calico-node-5khhz             1/1  Running  192.168.8.143   master01   ← hostNetwork
calico-node-bsg58             1/1  Running  192.168.8.142   worker01
calico-node-flq4d             1/1  Running  192.168.8.141   worker02
coredns-...-2dwfb             1/1  Running  10.244.30.65    worker02
coredns-...-jhlw8             1/1  Running  10.244.30.67    worker02
(Control Plane 5개는 노드 IP 유지)
```

`calico-node`는 노드 IP(hostNetwork), `calico-kube-controllers`와 CoreDNS는 Pod IP를 받았다. **역할에 따라 네트워크 방식이 다르다** — 전자는 노드의 라우팅과 인터페이스를 직접 조작해야 하고, 후자는 apiserver와만 통신하면 된다.

#### IP Pool 및 노드별 podCIDR

```text
$ kubectl get ippool -o yaml | grep -A3 cidr
    cidr: 10.244.0.0/16          ★ 수동 편집이 반영됨
    ipipMode: Always
    natOutgoing: true
    nodeSelector: all()

$ kubectl get nodes -o jsonpath='...'
master01        10.244.0.0/24
worker01        10.244.1.0/24
worker02        10.244.2.0/24
```

#### 노드 파일시스템 및 네트워크 — 설치 전후 비교

| 항목 | 설치 전 | 설치 후 |
|---|---|---|
| `/etc/cni/net.d/` | `.kubernetes-cni-keep` 뿐 | **`10-calico.conflist`**, `calico-kubeconfig` 생성 |
| `/opt/cni/bin/ \| grep calico` | 출력 없음 | `calico`, `calico-ipam` |
| 인터페이스 | `lo`, `ens33` | + **`tunl0`**, (Pod가 있는 노드는) `cali*` |
| `ip route \| grep 10.244` | **출력 없음** | **노드별 블록 경로 3개** |

```text
# master01
tunl0@NONE       UNKNOWN   10.244.241.64/32
10.244.5.0/26   via 192.168.8.142 dev tunl0 proto bird metric 1024 onlink
10.244.30.64/26 via 192.168.8.141 dev tunl0 proto bird metric 1024 onlink
blackhole 10.244.241.64/26 proto bird

# worker01
tunl0@NONE       UNKNOWN   10.244.5.0/32
blackhole 10.244.5.0/26 proto bird
10.244.30.64/26  via 192.168.8.141 dev tunl0 proto bird metric 1024 onlink
10.244.241.64/26 via 192.168.8.143 dev tunl0 proto bird metric 1024 onlink

# worker02  (Pod 3개가 배치되어 cali* 존재)
tunl0@NONE           UNKNOWN   10.244.30.64/32
calie7fbff90b0b@if2  UP
cali61489fcab34@if3  UP
calia6cb3443a91@if3  UP
10.244.5.0/26    via 192.168.8.142 dev tunl0 proto bird metric 1024 onlink
10.244.241.64/26 via 192.168.8.143 dev tunl0 proto bird metric 1024 onlink
blackhole 10.244.30.64/26 proto bird
10.244.30.65 dev calie7fbff90b0b scope link metric 1024
10.244.30.66 dev cali61489fcab34 scope link metric 1024
10.244.30.67 dev calia6cb3443a91 scope link metric 1024
```

**데이터 평면이 master를 경유하지 않는다는 증거**가 여기 있다. worker01의 라우팅에서 worker02의 블록(`10.244.30.64/26`)으로 가는 next-hop이 `192.168.8.141`(worker02)이며, master(`192.168.8.143`)가 아니다.

> 이 출력에 대한 상세 분석 — Calico IPAM이 Kubernetes podCIDR을 사용하지 않는 이유, `/26` 블록의 의미, `blackhole` 경로, `proto bird`, IPIP 터널 구조 — 은 이 문서 아래 **"심화 — Calico 네트워킹 구조 분석"** 절에 정리했다.

### Pod 간 통신 / DNS 테스트 (2026-08-04)

#### Pod 배치

```text
$ kubectl get pods -o wide
NAME    READY   STATUS    IP             NODE
test1   1/1     Running   10.244.5.3     worker01
test2   1/1     Running   10.244.5.4     worker01
test3   1/1     Running   10.244.5.5     worker01
test4   1/1     Running   10.244.30.68   worker02
```

worker02에는 이미 Pod 3개(CoreDNS ×2, calico-kube-controllers)가 있었고 worker01은 비어 있었다. **Scheduler가 덜 붐비는 노드를 선호**한 결과다. master01에는 배치되지 않았다 — `node-role.kubernetes.io/control-plane:NoSchedule` taint 때문이다.

#### 노드 간 Pod 통신 — 성공

```text
$ kubectl exec test1 -- ping -c 3 10.244.30.68
64 bytes from 10.244.30.68: seq=0 ttl=62 time=2.085 ms
3 packets transmitted, 3 packets received, 0% packet loss
```

**TTL로 홉 수를 확인할 수 있다.**

```text
worker02의 tunl0(10.244.30.64) 응답   ttl=63    홉 1개
worker02의 Pod(10.244.30.68) 응답     ttl=62    홉 2개  ← 터널 도착 후 cali* veth로 한 번 더
```

#### traceroute — 경로 확인

```text
$ kubectl exec test1 -- traceroute 10.244.30.64
 1  192.168.8.142 (192.168.8.142)  0.016 ms      ← worker01 자기 노드 IP
 2  10.244.30.64  (10.244.30.64)   0.564 ms
```

**두 가지가 증명된다.**

| 관찰 | 의미 |
|---|---|
| 경로에 `192.168.8.143`(master01)이 없음 | **데이터 평면이 master를 경유하지 않는다** |
| hop 1이 worker01 자기 IP(`192.168.8.142`) | **Phase 2의 `net.ipv4.ip_forward=1`이 동작 중.** 노드가 Pod 트래픽의 라우터 역할 |

`ip_forward=0`이었다면 hop 1에서 패킷이 폐기되어 통신 자체가 불가능했다.

#### Pod 내부에서 본 네트워크

```text
$ kubectl exec test1 -- ip addr
2: tunl0@NONE: <NOARP> mtu 1480 qdisc noop        # 사용되지 않음(noop)
3: eth0@if8: ... mtu 1480
    inet 10.244.5.3/32 scope global eth0

$ kubectl exec test1 -- ip route
default via 169.254.1.1 dev eth0
169.254.1.1 dev eth0 scope link
```

상세 분석은 아래 심화 절의 "6. Pod 내부에서 본 네트워크" 참조.

#### DNS 테스트 — NXDOMAIN은 실패가 아니었다 ★

```text
$ kubectl exec test1 -- nslookup kubernetes.default
Server:  10.96.0.10
Address: 10.96.0.10:53
** server can't find kubernetes.default: NXDOMAIN        ← 여기서 오판하기 쉬움

$ kubectl exec test1 -- nslookup kubernetes.default.svc.cluster.local
Server:  10.96.0.10
Name:    kubernetes.default.svc.cluster.local
Address: 10.96.0.1                                        ✅ 정상
```

**NXDOMAIN과 timeout은 완전히 다른 신호다.**

```text
timeout    "DNS 서버에 도달조차 못 했다"         → 네트워크 경로 문제
NXDOMAIN   "서버가 '그런 이름 없다'고 응답했다"   → 경로는 정상, 이름 문제
```

첫 시도에서 **응답이 돌아왔다는 사실 자체**가 아래 경로 전체의 정상 동작을 증명한다.

```text
test1 Pod (worker01)
  → 10.96.0.10:53 질의                         ClusterIP — 실체 없는 가상 IP
  → kube-proxy의 iptables DNAT 규칙 적용         ★ 여기가 동작
  → 실제 CoreDNS Pod IP(worker02)로 변환
  → 노드 간 IPIP 터널 통과
  → CoreDNS 응답
```

**Phase 2에서 설정한 `net.bridge.bridge-nf-call-iptables=1`이 이 지점에서 검증된다.** 그 값이 없었다면 DNAT가 적용되지 않아 timeout이 발생했을 것이다.

**원인**: busybox의 `nslookup`이 `/etc/resolv.conf`의 검색 도메인(search domain)을 제대로 처리하지 않는 알려진 이슈다.

```text
kubernetes.default + default.svc.cluster.local  → 없음
kubernetes.default + svc.cluster.local          → 이 단계에서 찾아야 정상
kubernetes.default + cluster.local              → 없음
```

**교훈**: 장애를 "DNS가 안 된다"로 뭉뚱그리면 어디를 봐야 할지 알 수 없다. **응답 유무로 먼저 갈라야** 조사 범위가 절반으로 줄어든다.

#### 최종 검증 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 노드 간 Pod 통신 | 통과 | ping 0% loss, ttl=62 |
| IPIP 터널 + BGP 라우팅 | 통과 | traceroute 2홉 |
| `ip_forward` 동작 | 통과 | hop 1이 노드 자기 IP |
| 데이터 평면 master 미경유 | 통과 | 경로에 `.143` 없음 |
| kube-proxy iptables DNAT | 통과 | ClusterIP 질의에 응답 도달 |
| CoreDNS 이름 해석 | 통과 | FQDN 질의 시 `10.96.0.1` 반환 |

**Phase 7 완료.** 로드맵 1단계의 클러스터 구축 목표가 달성되었다.

---


---

## 심화 — Calico 네트워킹 구조 분석

> 설치 로그와 라우팅 테이블을 근거로 Calico가 실제로 무엇을 하는지 확인한다.

### 1. calico.yaml은 Pod를 만들지 않는다

#### 두 가지를 분리해야 한다

```text
calico.yaml이 한 일    Calico 자신을 클러스터에 배포하는 일회성 작업
Pod를 만드는 일        완전히 별개. 사용자가 Deployment/Pod를 선언할 때 발생
```

앞으로 애플리케이션 Pod를 만들 때 `calico.yaml`은 아무 관련이 없다. **Calico는 그 과정에서 IP를 할당하고 인터페이스를 연결하는 역할만 한다.**

#### "master가 worker에게 적용했다"는 정확하지 않다

Kubernetes의 핵심 설계이므로 정밀하게 볼 필요가 있다.

```text
[흔한 오해]  master가 worker에게 "이거 띄워"라고 밀어넣는다 (push)

[실제]
kubectl apply
  → apiserver가 DaemonSet 선언을 etcd에 저장. 여기서 끝.
       ↓
각 노드의 kubelet이 apiserver를 지켜보다가
"내 노드에 떠야 할 Pod가 생겼다"를 감지하고 스스로 가져간다 (pull)
```

**master는 worker에게 명령을 전송하지 않는다.** 선언을 저장할 뿐이고 각 노드가 자기 할 일을 찾아간다.

이 구조 덕분에 worker가 일시적으로 꺼져 있어도 문제가 없고, 다시 켜지면 밀린 일을 알아서 처리한다. **선언적(declarative) 모델**이 명령형(imperative) 시스템과 결정적으로 다른 지점이다.

---

### 2. Pod 생명주기 — 상태 표시의 의미

#### 실제 관찰된 순서

```text
Pending           etcd에 저장됨. 노드 미배정이거나 시작 전
   ↓
Init:0/3          샌드박스(pause) 생성 완료 → init 컨테이너 1번째 실행 중
Init:1/3          1번 완료, 2번 실행 중
Init:2/3          2번 완료, 3번 실행 중
   ↓
PodInitializing   init 전부 완료 → 메인 컨테이너 시작 중
   ↓
Running 0/1       컨테이너는 떴으나 아직 준비되지 않음
Running 1/1       Readiness Probe 통과
```

#### `Init:x/N`과 `ContainerCreating`의 차이

```text
calico-node               → Init:0/3, Init:1/3, ...
coredns                   → ContainerCreating
calico-kube-controllers   → ContainerCreating
```

**init 컨테이너가 있으면 `Init:x/N`, 없으면 `ContainerCreating`으로 표시된다.** 실제로는 같은 구간(샌드박스 생성 + 이미지 받기 + CNI 호출)이며 표시 방식만 다르다.

#### STATUS와 READY는 다른 것을 본다

```text
calico-node-5khhz   0/1  Running   35s
calico-node-5khhz   1/1  Running   46s      ← 11초 뒤
```

| 열 | 의미 |
|---|---|
| **STATUS** | 컨테이너가 실행 중인가 |
| **READY** | **Readiness Probe를 통과했는가** |

**"프로세스가 떠 있다"와 "요청을 받을 준비가 됐다"는 다르다.** 로드맵 6단계 시나리오 G가 이 차이를 다룬다 — 프로세스는 살아 있는데 Ready가 아니어서 Service Endpoint에서 제외되는 상황이다.

#### 전환점을 로그에서 확인할 수 있었다

```text
calico-node-5khhz   0/1  Init:2/3           17s
coredns-...-jhlw8   0/1  ContainerCreating  16h     ← 16시간 Pending이던 것이 움직임
```

`calico-node`의 init 컨테이너가 `/etc/cni/net.d/`에 설정을 쓰는 순간 Node가 `Ready`가 되고, 자동으로 붙어 있던 `node.kubernetes.io/not-ready:NoSchedule` taint가 제거되어 CoreDNS가 스케줄되었다.

init 컨테이너 목록은 다음으로 확인한다.

```bash
kubectl -n kube-system get pod <calico-node-xxxxx> \
  -o jsonpath='{range .spec.initContainers[*]}{.name}{"\n"}{end}'
```

---

### 3. 터널과 IP 할당 구조

#### 터널은 상대 노드마다 뚫는 것이 아니다

```text
master01   tunl0@NONE   10.244.241.64/32
worker01   tunl0@NONE   10.244.5.0/32
worker02   tunl0@NONE   10.244.30.64/32
```

**각 노드에 `tunl0` 인터페이스가 하나씩만 존재한다.** 상대 노드별로 별도 터널을 생성하지 않는다.

라우팅을 보면 명확하다. 목적지는 다른데 **나가는 장치는 모두 `tunl0`** 이다.

```text
# master01
10.244.5.0/26   via 192.168.8.142 dev tunl0      ← worker01행
10.244.30.64/26 via 192.168.8.141 dev tunl0      ← worker02행
                                  ^^^^^^^^^^ 동일한 장치
```

IPIP 터널은 **일대다(one-to-many)** 로 동작한다. 인터페이스는 하나지만 패킷마다 라우팅 테이블의 next-hop을 참조해 **바깥쪽 목적지 IP를 결정**한다.

```text
[원본]  10.244.30.65 (worker02의 Pod)로 가는 패킷
   ↓ tunl0이 IPIP로 캡슐화
[캡슐]  바깥: 192.168.8.143 → 192.168.8.141   (노드 IP)
        안쪽: 10.244.241.x  → 10.244.30.65     (Pod IP)
   ↓ 일반 네트워크로 전송
worker02가 껍질을 제거하고 Pod에 전달
```

`ippool`의 `ipipMode: Always`가 이 캡슐화 설정이다.

#### Calico는 Kubernetes의 podCIDR을 사용하지 않는다 ★

**이 문서에서 가장 중요한 발견이다.** 두 값을 나란히 비교하면 드러난다.

```text
[Kubernetes가 할당한 podCIDR]      [Calico가 실제로 사용하는 블록]
master01   10.244.0.0/24           master01   10.244.241.64/26
worker01   10.244.1.0/24           worker01   10.244.5.0/26
worker02   10.244.2.0/24           worker02   10.244.30.64/26
```

**전혀 다르다.** `10.244.241.64`는 `10.244.0.0/24`에 포함되지도 않는다.

```text
kube-controller-manager  →  각 노드에 /24씩 할당 (node.spec.podCIDR)
                            → Calico는 이 값을 참조하지 않음

Calico의 자체 IPAM       →  IPPool(10.244.0.0/16)에서 /26 블록을 필요할 때 할당
                            → 실제 Pod IP는 여기서 나옴
```

확인 방법:

```bash
cat /etc/cni/net.d/10-calico.conflist
```

`"ipam": {"type": "calico-ipam"}`이 지정되어 있다. **`host-local`이 아니라 `calico-ipam`을 사용하므로 podCIDR을 참조하지 않는다.**

> **그렇다면 `kubeadm init --pod-network-cidr`은 왜 필요했는가**
>
> 두 가지 이유가 있다. ① Controller Manager의 노드별 CIDR 할당 기능을 켜고 `node.spec.podCIDR`을 채운다. ② Calico가 kubeadm 환경에서 IPPool의 기본 대역을 결정할 때 참조한다. 즉 **대역의 전체 범위는 일치해야 하지만, 노드별 세부 분할은 각자 다르게 관리한다.**

#### /24가 아니라 /26인 이유

```text
/24 고정 할당   노드당 254개 IP 예약. Pod 5개만 실행해도 249개 낭비
/26 블록 할당   노드당 62개씩. 부족하면 블록을 추가로 할당받음
```

`/16`을 `/26`으로 분할하면 **1024개 블록**이 나온다. `/24`로 분할하면 256개뿐이다. **훨씬 유연하고 낭비가 적다.**

블록 크기는 IPPool의 `blockSize` 필드로 조정할 수 있다.

#### `blackhole` 경로의 역할

```text
blackhole 10.244.241.64/26 proto bird      ← master01 자기 블록
```

**"이 블록은 내 소유다. 여기 속하지만 실제로 할당되지 않은 IP로 가는 패킷은 폐기하라."**

이것이 없으면 미할당 IP로 향하는 패킷이 기본 경로(`default via 192.168.8.2`)를 타고 외부로 유출된다. **소유 대역을 선언하는 동시에 누수를 차단하는 장치**다.

#### `cali*` 인터페이스는 Pod가 있는 노드에만 생긴다

```text
worker02   calie7fbff90b0b@if2, cali61489fcab34@if3, calia6cb3443a91@if3
           10.244.30.65 dev calie7fbff90b0b scope link
           10.244.30.66 dev cali61489fcab34 scope link
           10.244.30.67 dev calia6cb3443a91 scope link
```

**Pod가 worker02에만 배치되었기 때문이다.** `cali*`는 Pod 하나당 하나씩 생성되는 veth의 호스트 쪽 끝이며, 반대쪽 끝은 Pod의 네트워크 네임스페이스 안에 있다.

```text
coredns 2개 + calico-kube-controllers 1개 = 3개  →  cali* 3개
```

`@if2`, `@if3`은 **반대쪽 인터페이스의 인덱스 번호**다. Pod 내부에서 `ip addr`를 실행하면 해당 번호의 인터페이스를 확인할 수 있다.

#### `proto bird`의 의미

```text
10.244.5.0/26 via 192.168.8.142 dev tunl0 proto bird
                                          ^^^^^^^^^^
```

**BIRD**는 `calico-node` 컨테이너 내부에서 실행되는 **BGP 데몬**이다. 노드끼리 BGP로 "내 블록은 이것"이라는 정보를 교환하고, 그 결과를 커널 라우팅 테이블에 기록한다.

`proto bird`는 **"이 경로를 BIRD가 추가했다"** 는 표시다. 다른 값과 비교하면 출처가 구분된다.

| proto | 출처 |
|---|---|
| `static` | 사람이 설정 (netplan으로 지정한 기본 게이트웨이) |
| `kernel` | 커널이 자동 생성 (인터페이스에 IP를 붙이면 생기는 로컬 경로) |
| `bird` | **BIRD(BGP)가 동적으로 배포** |

**노드가 추가되면 BGP로 자동 전파된다.** 수동으로 라우팅을 설정할 필요가 없다.

---

### 4. Calico는 CRI가 아니라 CNI다

#### 가장 흔한 혼동

```text
CRI  =  컨테이너를 만드는 인터페이스    →  containerd
CNI  =  네트워크를 연결하는 인터페이스   →  Calico
```

**Calico는 컨테이너를 하나도 만들지 않는다.** 컨테이너 생성은 전적으로 containerd + runc의 일이다.

```text
kubelet  →[CRI]→  containerd  →[OCI]→  runc      컨테이너 생성
                       ↓
                    [CNI]→  Calico                네트워크 연결
```

**containerd가 컨테이너(정확히는 Pod 샌드박스)를 만든 뒤, 그 네트워크 네임스페이스에 IP를 연결해 달라고 Calico를 호출하는 구조다.**

#### Calico가 실제로 수행하는 5가지

| 역할 | 담당 | 이번 출력에서 확인된 증거 |
|---|---|---|
| **IPAM** — IP 할당 | `calico-ipam` 바이너리 | `/26` 블록, Pod IP `10.244.30.65` |
| **인터페이스 생성** | `calico` 바이너리 | `cali*` veth 3개 |
| **라우팅 배포** | BIRD (BGP) | `proto bird` 경로들 |
| **패킷 캡슐화** | IPIP | `tunl0`, `ipipMode: Always` |
| **NetworkPolicy** — Pod 간 방화벽 | Felix (iptables 규칙 생성) | 아직 미사용 |

**마지막 항목이 Flannel 대신 Calico를 선택한 이유다.** 로드맵 10단계에서 Argo CD가 NetworkPolicy를 관리하도록 계획되어 있는데, Flannel은 이를 지원하지 않아 나중에 CNI를 교체해야 했을 것이다.

#### 정리

> Calico는 컨테이너를 만드는 도구가 아니라,
> **이미 만들어진 컨테이너에 네트워크를 연결하고, 노드 간 경로를 관리하고, 통신 정책을 강제하는** 도구다.

---

### 5. Calico 설치로 정확히 무엇이 바뀌었는가

#### 흔한 정리 방식과 그 오차

> "노드끼리 네트워크적으로 연결되지 않은 상태였는데, Calico를 설치해서
> 각 노드가 Calico 프로세스를 통해 서로 통신하게 되었다"

방향은 맞지만 **두 지점이 부정확하다.**

#### 오차 ① 노드끼리는 원래도 연결되어 있었다

```text
계층 1   노드 네트워크   192.168.8.0/24    VMware가 제공. 처음부터 존재
계층 2   Pod 네트워크    10.244.0.0/16     ← Calico가 만든 것
```

**없었던 것은 Pod 네트워크이지 노드 네트워크가 아니다.** 증거는 여럿 있다.

| 시점 | 증거 | 의미 |
|---|---|---|
| Phase 2 | `ping worker01` 성공 | 노드 간 IP 통신 정상 |
| Phase 6 | `kubeadm join` 성공 | worker → master:6443 통신 정상 |
| 16시간 | kubelet이 계속 상태 보고 | 제어 평면 지속 동작 |
| 16시간 | kube-proxy가 3개 노드에서 Running | hostNetwork로 정상 동작 |

**노드 네트워크가 없었다면 join 자체가 불가능했다.**

정확한 표현은 다음과 같다.

> 노드 간 물리 연결은 처음부터 있었고, 그 위에 **Pod들이 서로 통신할 수 있는 별도의 가상 네트워크**를 Calico가 얹었다.

`tunl0`이라는 이름이 이 구조를 그대로 나타낸다. **터널**은 "이미 존재하는 네트워크 위로 다른 네트워크를 통과시키는" 기법이다. 기존 노드 네트워크(`192.168.8.x`)를 **운반 수단**으로 삼아 Pod 네트워크(`10.244.x.x`)를 실어 나른다.

#### 오차 ② 실제 패킷은 Calico 프로세스를 거치지 않는다 ★

```text
Calico가 한 일     라우팅 테이블에 경로를 기록하고, tunl0 인터페이스를 생성하고,
                   cali* veth를 연결하고, iptables 규칙을 삽입     → 설정 작업

실제 패킷 전달     리눅스 커널이 라우팅 테이블을 참조해 tunl0으로 보내고,
                   커널의 IPIP 모듈이 캡슐화 수행                  → 커널의 일
```

Pod가 패킷을 보낼 때 **calico-node 프로세스에 질의하지 않는다.** 커널이 이미 설정된 라우팅 테이블만 보고 처리한다.

증거는 라우팅 출력에 있다.

```text
10.244.5.0/26 via 192.168.8.142 dev tunl0 proto bird
                                          ^^^^^^^^^^ "BIRD가 이 경로를 넣었다"는 기록일 뿐
```

`proto bird`는 **경로의 출처를 표시**할 뿐이다. 일단 기록되면 그 경로는 **커널 소유**이며 커널이 독립적으로 사용한다.

#### 검증 실험

```bash
# 1. 통신 확인
kubectl exec test1 -- ping -c 2 <test2 IP>

# 2. calico-node를 전부 삭제
kubectl -n kube-system delete pod -l k8s-app=calico-node

# 3. 재생성 전에 즉시 재확인
kubectl exec test1 -- ping -c 2 <test2 IP>
```

**계속 성공한다.** calico-node가 없어도 커널의 라우팅과 터널 설정은 그대로 남아 있기 때문이다.

**다만 그 사이에 새 Pod를 생성하면 IP를 받지 못한다.** `calico-ipam`을 호출할 수 없기 때문이다.

```text
기존 Pod 통신    커널이 처리        → calico-node가 없어도 계속 동작
새 Pod 생성      Calico 호출 필요   → calico-node가 없으면 실패
```

DaemonSet이므로 수 초 내에 재생성된다. 안전한 실험이다.

#### 정확한 요약

> 노드 간 네트워크는 처음부터 있었지만, **Pod들이 사용할 가상 네트워크가 없었다.**
>
> Calico를 설치하자 각 노드의 calico-node가 **커널의 라우팅 테이블·터널 인터페이스·veth·iptables를 설정**했고,
> BGP로 서로의 IP 블록 정보를 교환해 경로를 자동 배포했다.
>
> 이후 **실제 Pod 트래픽은 커널이 그 설정을 따라 처리**하며 calico-node 프로세스를 경유하지 않는다.
> calico-node는 **설정을 생성하고 변화에 맞춰 갱신하는 관리자** 역할이다.

#### 이 구분이 만들어내는 공통 패턴

같은 논리가 다른 제어 컴포넌트에도 적용된다.

| 컴포넌트 | 중단되면 | 유지되는 것 |
|---|---|---|
| `calico-node` | 새 Pod IP 할당 실패 | 기존 Pod 간 통신 |
| `kube-proxy` | 새 Service 규칙 반영 안 됨 | 기존 iptables 규칙에 의한 통신 |
| `kube-apiserver` | 모든 변경 작업 불가 | **이미 실행 중인 Pod 전부 정상 동작** |

**Kubernetes의 제어 컴포넌트는 대부분 "설정을 만들어두고 빠지는" 구조**라, 중단되어도 이미 만들어진 것은 계속 동작한다. 이것이 시스템 복원력(resilience) 설계의 핵심이다.

**로드맵 1단계의 마지막 질문 두 개가 정확히 이것을 검증한다.**

```text
- API Server가 중단되었을 때 기존 Pod와 신규 스케줄링은 어떻게 달라지는가
- kubelet이나 containerd가 중단되면 Node와 Pod 상태는 어떻게 변하는가
```

→ [08-failure-experiments.md](08-failure-experiments.md)에서 직접 실험한다.

---

### 6. Pod 내부에서 본 네트워크

Pod 안에서 `ip addr`와 `ip route`를 실행하면 Calico 설계의 핵심이 드러난다.

```text
$ kubectl exec test1 -- ip addr
2: tunl0@NONE: <NOARP> mtu 1480 qdisc noop qlen 1000     # 사용되지 않음
3: eth0@if8: <BROADCAST,MULTICAST,UP,LOWER_UP,M-DOWN> mtu 1480
    inet 10.244.5.3/32 scope global eth0

$ kubectl exec test1 -- ip route
default via 169.254.1.1 dev eth0
169.254.1.1 dev eth0 scope link
```

#### ① `eth0@if8` — veth 쌍의 반대쪽 인덱스

```text
Pod 안:   3: eth0@if8       "내 짝은 호스트의 8번 인터페이스"
호스트:   8: caliXXXX@if3   "내 짝은 저쪽 네임스페이스의 3번"
```

**하나의 veth 쌍이 두 네트워크 네임스페이스에 양 끝을 걸치고 있다.** 호스트에서 확인할 수 있다.

```bash
# 해당 Pod가 있는 노드에서
ip link show | grep -A1 '^8:'
```

#### ② `/32` 넷마스크 — Calico의 핵심 설계 ★

```text
inet 10.244.5.3/32
                ^^^ /24가 아니라 /32
```

**Pod는 자기 네트워크에 자기 혼자만 있다고 인식한다.** 같은 노드의 다른 Pod조차 "다른 네트워크"로 취급한다.

결과적으로 **모든 트래픽이 예외 없이 기본 경로를 통해 호스트로 나간다.** 호스트가 전부 라우팅한다.

| 방식 | Pod IP | 같은 노드 내 Pod 통신 |
|---|---|---|
| 브리지 기반 CNI (Flannel 등) | `/24` 공유 | 브리지에서 직접 처리 — 호스트 라우팅 미경유 |
| **Calico** | **`/32`** | **호스트 라우팅을 반드시 경유** |

**Calico가 브리지를 사용하지 않는 이유가 이것이다.** 모든 패킷이 호스트 커널을 지나므로 **NetworkPolicy(iptables 규칙)를 예외 없이 적용**할 수 있다. 브리지 방식에서는 같은 노드 내 통신이 정책을 우회할 여지가 생긴다.

#### ③ `169.254.1.1` — 존재하지 않는 게이트웨이

`169.254.0.0/16`은 **link-local 대역**으로 어느 장비에도 할당되지 않는다. 그런데 Pod는 이를 기본 게이트웨이로 사용한다.

**동작 원리**

```text
1. Pod가 외부로 패킷 전송 시도
2. 기본 게이트웨이 169.254.1.1의 MAC 주소를 알아야 함 → ARP 요청
3. 호스트의 cali* 인터페이스가 "그것은 나다"라고 응답      ← proxy ARP
4. Pod가 그 MAC으로 패킷 전송 → 호스트에 도착
5. 호스트 커널이 라우팅 테이블을 참조해 처리
```

확인 방법:

```bash
# Pod가 있는 노드에서
cat /proc/sys/net/ipv4/conf/cali*/proxy_arp
```

`1`이 출력된다. Calico가 각 `cali*` 인터페이스에 proxy ARP를 활성화한 것이다.

**왜 이렇게 하는가**

`/32`와 한 세트로 동작하는 설계다.

```text
Pod IP가 /32       → 자기 외에는 전부 "다른 네트워크"
게이트웨이가 가짜   → 모든 패킷이 예외 없이 호스트로 나감
```

**모든 Pod가 동일한 게이트웨이 주소를 사용할 수 있다.** 노드마다 게이트웨이 IP를 별도로 할당할 필요가 없어 IP를 절약하며, Pod가 다른 노드로 이동해도 설정이 동일하다.

#### ④ MTU 1480 — IPIP 오버헤드

```text
mtu 1480          # 일반적인 1500이 아님
```

```text
1500  일반 이더넷 MTU
 -20  IPIP 바깥쪽 IP 헤더
─────
1480  Pod가 사용 가능한 크기
```

`calico.yaml`의 `veth_mtu` ConfigMap 값이 이것이다.

**MTU 불일치는 진단이 매우 어려운 장애를 만든다.** "ping은 되는데 대용량 전송이 멈춘다", "HTTP GET은 되는데 POST가 실패한다" 같은 형태로 나타난다. 작은 패킷은 통과하고 큰 패킷만 조각화 문제로 막히기 때문이다.

#### ⑤ Pod 안의 `tunl0`은 사용되지 않는다

```text
2: tunl0@NONE: <NOARP> mtu 1480 qdisc noop
                                      ^^^^ 비활성 상태
```

`ipip` 커널 모듈이 로드되면 **모든 네트워크 네임스페이스에 `tunl0`이 생성**된다. Pod 안의 것은 사용되지 않으며, 실제 캡슐화는 **호스트 네임스페이스의 `tunl0`** 이 수행한다.

---


---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| CNI 설치 전에는 왜 Pod 통신이 안 되는가 | `/etc/cni/net.d/` 설정이 없어 kubelet이 네트워크 미준비로 판단. 바이너리는 있었음 |
| CNI는 무엇으로 구현되는가 | 특별한 시스템 데몬이 아니라 **DaemonSet + Deployment + RBAC** |
| calico-node는 CNI가 없는데 어떻게 뜨는가 | hostNetwork를 사용하므로 Pod IP가 필요 없음 |
| Pod CIDR이 두 곳에 나오는 이유 | kubeadm은 노드별 podCIDR을 할당하고, Calico는 그 안에서 실제 IP를 발급 |
| 노드 간 Pod 통신은 어떻게 이루어지는가 | 터널 인터페이스(`tunl0`/`vxlan.calico`) + 라우팅 테이블 + `ip_forward` |
| calico.yaml이 애플리케이션 Pod를 만드는가 | 아니다. Calico 자신을 배포한 일회성 작업이며, 이후에는 IP 할당 역할만 한다 |
| master가 worker에게 Pod를 밀어넣는가 | 아니다. apiserver에 선언을 저장하고 각 노드의 kubelet이 가져간다 (pull) |
| `Init:x/N`과 `ContainerCreating`의 차이는 | init 컨테이너 유무에 따른 표시 차이. 실제 구간은 같다 |
| `Running 0/1`과 `1/1`의 차이는 | STATUS는 컨테이너 실행 여부, READY는 Readiness Probe 통과 여부 |
| 노드마다 별도 터널을 만드는가 | 아니다. `tunl0` 하나가 일대다로 동작하며 next-hop에 따라 캡슐 목적지를 결정 |
| Pod IP는 `node.spec.podCIDR`에서 나오는가 | 아니다. Calico 자체 IPAM이 IPPool에서 `/26` 블록을 할당한다 |
| `blackhole` 경로는 왜 있는가 | 자기 블록 소유 선언 + 미할당 IP로 가는 패킷의 외부 유출 차단 |
| `proto bird`는 무엇인가 | calico-node 안의 BGP 데몬(BIRD)이 동적으로 배포한 경로라는 표시 |
| Calico는 CRI인가 | 아니다. CNI다. 컨테이너를 만들지 않고 네트워크만 담당한다 |
| **Calico 설치 전에 노드끼리 통신이 안 됐는가** | **아니다. 노드 네트워크는 처음부터 있었고, 없었던 것은 Pod 네트워크다** |
| **패킷이 calico-node를 거쳐 가는가** | **아니다. 커널이 처리한다. Calico는 커널 설정을 만들고 갱신하는 관리자다** |
| calico-node가 죽으면 통신이 끊기는가 | 기존 통신은 유지된다. 새 Pod의 IP 할당만 실패한다 |
| Pod IP가 왜 `/32`인가 | 같은 노드 Pod도 "다른 네트워크"로 만들어 모든 트래픽이 호스트 라우팅을 거치게 함 → NetworkPolicy 우회 차단 |
| `169.254.1.1`은 어느 장비인가 | 존재하지 않는다. 호스트의 `cali*`가 proxy ARP로 응답하는 가짜 게이트웨이 |
| Pod의 MTU가 1480인 이유는 | IPIP 바깥쪽 IP 헤더 20바이트를 뺀 값 |
| DNS 질의가 NXDOMAIN이면 네트워크 문제인가 | 아니다. **응답이 왔다는 것 자체가 경로 정상의 증거**다. timeout이어야 경로 문제다 |

# 04. kubeadm init — Control Plane 생성 (master01)

**이 단계에서 처음으로 클러스터가 만들어진다.** Phase 3~4는 도구를 설치했을 뿐이다.

명령 자체는 한 줄이지만 내부적으로 10단계가 넘는 작업을 순서대로 수행한다. **출력을 단계별로 읽는 것이 이 Phase의 핵심**이며, 그 출력이 로드맵 1단계 질문 여러 개의 답을 담고 있다.

---

## init 전 상태 (2026-08-03, master01)

"후"만 보면 무엇이 새로 생긴 것인지 특정할 수 없다. 비교 기준으로 "전"을 기록한다.

### A. Kubernetes 설정 디렉터리 — 거의 비어 있다

```text
$ ls -la /etc/kubernetes/
drwxrwxr-x   3 root root 4096 Aug  3 10:50 .
drwxrwxr-x   2 root root 4096 Aug  3 10:50 manifests        # 이것뿐

$ ls -la /etc/kubernetes/manifests/
-rw-r--r-- 1 root root    0 Jul 23 03:14 .kubelet-keep      # 0바이트 표시 파일

$ ls -la /etc/kubernetes/pki/
ls: cannot access '/etc/kubernetes/pki/': No such file or directory
```

`manifests/`가 **비어 있다는 것이 중요하다.** kubelet은 이 디렉터리를 감시하고 있지만 띄울 것이 없다. init이 여기에 Static Pod manifest 4개를 넣는 순간 kubelet이 Control Plane을 띄운다.

`pki/`는 아직 존재하지 않는다. init이 CA를 생성하면서 만든다.

> `.kubelet-keep`, `.kubernetes-cni-keep`은 패키지가 빈 디렉터리를 유지하려고 넣은 0바이트 표시 파일이다. 우리가 만든 것이 아니며 무시해도 된다.

### B. kubelet이 기동하지 못하는 이유 — 로그에 그대로 있다

```text
$ ls /var/lib/kubelet/config.yaml
ls: cannot access '/var/lib/kubelet/config.yaml': No such file or directory

$ ls /etc/kubernetes/kubelet.conf
ls: cannot access '/etc/kubernetes/kubelet.conf': No such file or directory

$ systemctl is-active kubelet
activating

$ journalctl -u kubelet -n 5 --no-pager
Started kubelet.service - kubelet: The Kubernetes Node Agent.
(kubelet)[16898]: kubelet.service: Referenced but unset environment variable
    evaluates to an empty string: KUBELET_KUBEADM_ARGS
kubelet[16898]: E0803 12:14:54 run.go:72] "command failed"
    err="failed to load kubelet config file, path: /var/lib/kubelet/config.yaml,
    error: ... open /var/lib/kubelet/config.yaml: no such file or directory"
kubelet.service: Main process exited, code=exited, status=1/FAILURE
kubelet.service: Failed with result 'exit-code'.
```

**빠진 것은 3개다.**

| 파일 | 역할 | 누가 만드는가 |
|---|---|---|
| `/var/lib/kubelet/config.yaml` | kubelet 자신의 설정 | `kubeadm init` / `join` |
| `/etc/kubernetes/kubelet.conf` | apiserver 접속용 kubeconfig | `kubeadm init` / `join` |
| `/var/lib/kubelet/kubeadm-flags.env` | `KUBELET_KUBEADM_ARGS`의 출처 | `kubeadm init` / `join` |

### kubelet과 kubeadm 패키지는 어떻게 협력하는가

세 번째 파일의 존재는 systemd drop-in 구조에서 나온다.

```bash
cat /etc/systemd/system/kubelet.service.d/10-kubeadm.conf
```

```text
/lib/systemd/system/kubelet.service                       ← kubelet 패키지가 설치
/etc/systemd/system/kubelet.service.d/10-kubeadm.conf     ← kubeadm 패키지가 설치 (drop-in)
```

**kubelet 자체는 kubeadm의 존재를 모른다.** kubeadm 패키지가 drop-in 파일을 얹어서 "설정은 이 경로에서 읽고, 실행 인자는 이 환경변수에서 가져와라"를 주입한다. 이 덕분에 kubelet은 kubeadm 없이도 쓸 수 있고, kubeadm은 kubelet을 수정하지 않고도 자기 방식을 강제할 수 있다.

### C. 컨테이너 런타임에 아무것도 없다

```text
$ sudo crictl ps -a
CONTAINER   IMAGE   CREATED   STATE   NAME   ATTEMPT   POD ID   POD   NAMESPACE
(헤더만 출력)

$ sudo crictl pods
POD ID   CREATED   STATE   NAME   NAMESPACE   ATTEMPT   RUNTIME
(헤더만 출력)

$ sudo crictl images
IMAGE   TAG   IMAGE ID   SIZE
(헤더만 출력)
```

이미지조차 하나도 없다. init이 Control Plane 이미지를 내려받는 것부터 시작한다.

### D. CNI — 바이너리는 있는데 설정이 없다

```text
$ ls -la /opt/cni/bin/
bandwidth  bridge  dhcp  dummy  firewall  host-device  host-local  ipvlan
loopback  macvlan  portmap  ptp  sbr  static  tap  tuning  vlan  vrf
                                                       ← 표준 CNI 플러그인 20개, 이미 설치됨

$ ls -la /etc/cni/net.d/
-rw-r--r-- 1 root root 0 Dec 18  2025 .kubernetes-cni-keep
                                                       ← 설정 파일 없음

$ ip -br addr show
lo       UNKNOWN   127.0.0.1/8 ::1/128
ens33    UP        192.168.8.143/24 fe80::20c:29ff:fe8f:7cd4/64
                                                       ← cali*, tunl*, cni0 없음

$ ip route
default via 192.168.8.2 dev ens33 proto static
192.168.8.0/24 dev ens33 proto kernel scope link src 192.168.8.143
                                                       ← Pod 대역 경로 없음
```

**이것이 이 문서에서 가장 중요한 발견이다.**

표준 CNI 플러그인 바이너리는 `kubernetes-cni` 패키지(kubelet의 의존성)로 **이미 설치되어 있다.** 그런데도 CNI는 동작하지 않는다.

```text
/opt/cni/bin/       플러그인 실행 파일                 ← 있음
/etc/cni/net.d/     어떤 플러그인을 어떻게 쓸지 설정    ← 없음  ★
```

kubelet은 `/etc/cni/net.d/`에서 설정 파일을 찾는다. 비어 있으면 **"네트워크가 준비되지 않음"으로 판단하고 Node를 `NotReady`로 유지**한다. 도구는 있는데 사용 설명서가 없는 상태다.

**Calico가 하는 일**은 바이너리를 설치하는 것이 아니라, `/etc/cni/net.d/`에 설정 파일을 쓰고 자기 전용 바이너리(`calico`, `calico-ipam`)를 추가하는 것이다. Phase 6에서 이 디렉터리에 파일이 생기는 순간 Node가 `Ready`로 전환된다.

> 부수 확인: `ip route`의 기본 경로가 `proto static`으로 나온다. Phase 0의 고정 IP 전환이 라우팅 테이블 수준에서도 반영된 증거다. 이전에는 `proto dhcp`였다.

---

## 실행 절차

### 1단계. 필요한 이미지 목록 확인

```bash
sudo kubeadm config images list
```

**왜 먼저 보는가**: init이 어떤 컨테이너 이미지를 필요로 하는지 미리 파악한다. 여기 나오는 것들이 곧 Control Plane의 실체다 — `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `etcd`, `coredns`, `pause`, `kube-proxy`.

### 2단계. 이미지 미리 받기

```bash
sudo kubeadm config images pull
```

**왜 init과 분리하는가**: 이미지 다운로드는 네트워크에 의존하는 단계이고, init 전체 중 가장 오래 걸리며 가장 자주 실패한다. 미리 분리해서 받으면 **"이미지 문제"와 "init 로직 문제"를 구분**할 수 있다.

만약 init 도중에 다운로드가 실패하면 어느 단계에서 왜 멈췄는지 판단하기 어렵다. 실패 지점을 좁히는 것이 목적이다.

```bash
sudo crictl images        # 받아진 이미지 확인
```

### 3단계. init 실행

```bash
sudo kubeadm init \
  --apiserver-advertise-address=192.168.8.143 \
  --pod-network-cidr=10.244.0.0/16
```

| 플래그 | 이유 |
|---|---|
| `--apiserver-advertise-address` | apiserver가 "나는 이 주소에 있다"고 광고할 IP. 인증서 SAN과 kubeconfig에 이 값이 들어간다. 생략하면 기본 경로 인터페이스에서 자동 선택하는데, VMware 어댑터가 여러 개면 잘못 고를 수 있어 명시한다 |
| `--pod-network-cidr` | Controller Manager가 각 노드에 나눠줄 Pod IP 대역. **Phase 6의 Calico 설정과 반드시 일치해야 한다** |

**출력 전체를 저장해 둔다.** 특히 마지막의 `kubeadm join` 명령은 Phase 7에서 필요하다.

---

## init이 내부에서 하는 일

출력에 `[단계이름]` 형태로 나타난다. 순서와 의미는 다음과 같다.

```text
[preflight]           사전 검사 — CPU 수, swap, 포트 사용 여부, 커널 모듈, 런타임 연결
[certs]               인증서 생성 — CA를 만들고 그 CA로 각 구성요소 인증서 서명
[kubeconfig]          kubeconfig 생성 — admin, super-admin, kubelet, controller-manager, scheduler
[etcd]                etcd Static Pod manifest 배치
[control-plane]       apiserver / controller-manager / scheduler Static Pod manifest 배치
[kubelet-start]       kubelet 설정 파일 3개 작성 후 kubelet 재시작   ← 여기서 kubelet이 살아남
[wait-control-plane]  kubelet이 Static Pod를 띄우고 apiserver가 응답할 때까지 대기
[upload-config]       사용한 설정을 ConfigMap으로 클러스터에 저장
[mark-control-plane]  이 노드에 label과 taint 부여
[bootstrap-token]     worker가 join할 때 쓸 토큰 발급
[addon]               CoreDNS와 kube-proxy 배포
```

### 주목할 지점 1 — `[kubelet-start]`와 `[wait-control-plane]`의 관계

```text
[kubelet-start]
  → /var/lib/kubelet/config.yaml 작성
  → /var/lib/kubelet/kubeadm-flags.env 작성
  → kubelet 재시작 → 이제 정상 기동

[control-plane]
  → /etc/kubernetes/manifests/ 에 manifest 4개 배치

[wait-control-plane]
  → kubeadm은 여기서 "기다린다"
        ↑
     실제로 컨테이너를 띄우는 것은 kubelet이다.
     kubelet이 manifests/ 디렉터리를 읽고 Static Pod를 실행한다.
```

**kubeadm은 Control Plane을 직접 실행하지 않는다.** manifest 파일을 놓고 kubelet이 일해주기를 기다린다. Phase 4에서 `systemctl enable --now kubelet`으로 kubelet을 미리 켜둔 이유가 이것이다.

이 구조 때문에 **"kubeadm init이 여기서 멈췄다"는 증상의 원인은 대부분 kubelet 쪽에 있다.** 그때는 `journalctl -u kubelet -f`와 `sudo crictl ps -a`로 확인한다.

### 주목할 지점 2 — 닭과 달걀 문제를 Static Pod가 푼다

```text
apiserver를 띄우려면 → 누가 띄우나?
일반 Pod로 띄우려면  → apiserver가 필요 → 하지만 그게 지금 띄우려는 것
```

**Static Pod가 이 모순을 해결한다.** kubelet이 `/etc/kubernetes/manifests/` 디렉터리를 **직접 읽어서** 컨테이너를 띄운다. apiserver도 etcd도 필요 없다. 자세한 내용은 [07-control-plane-analysis.md](07-control-plane-analysis.md)에 정리한다.

### 주목할 지점 3 — Control Plane에 taint가 붙는다

`[mark-control-plane]` 단계에서 이 노드에 taint가 붙는다.

```text
node-role.kubernetes.io/control-plane:NoSchedule
```

**의미**: 일반 워크로드가 이 노드에 스케줄되지 않는다. Control Plane 노드의 자원을 apiserver·etcd가 확보하도록 보호하는 것이다.

이 때문에 Phase 6에서 CoreDNS가 `Pending`으로 남는다 — worker가 아직 없고 master는 taint 때문에 배치 불가이기 때문이다. **Phase 7에서 worker를 join하면 해결된다.** 이것도 장애가 아니다.

---

## init 직후 해야 할 것

### kubeconfig 설정

```bash
mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config
```

**왜 복사하는가**: `kubectl`은 기본적으로 `$HOME/.kube/config`를 읽는다. `/etc/kubernetes/admin.conf`는 root 소유라 일반 사용자가 매번 sudo를 써야 한다.

**운영 환경에서는 이렇게 하지 않는다.** `admin.conf`에는 클러스터 전체 권한을 가진 클라이언트 인증서가 들어 있다. 사용자마다 최소 권한 인증서를 발급하는 것이 정석이며, 10단계 EKS Access Entry와 비교할 지점이다.

### join 명령 저장

출력 마지막의 `kubeadm join ...` 전문을 기록해 둔다. 토큰은 **기본 24시간 후 만료**되며, 만료되면 재발급한다.

```bash
kubeadm token create --print-join-command
```

---

## init 직후의 예상 상태 — NotReady가 정상이다

```bash
kubectl get nodes
kubectl get pods -A
```

```text
NAME       STATUS     ROLES           AGE   VERSION
master01   NotReady   control-plane   1m    v1.35.7
           ^^^^^^^^ 정상

CoreDNS Pod 2개가 Pending
           ^^^^^^^ 정상
```

**CNI가 없기 때문이다.** `/etc/cni/net.d/`가 여전히 비어 있으므로 kubelet이 "네트워크 준비 안 됨"으로 보고한다.

이 상태를 반드시 캡처한다. 로드맵 결과물 **"CNI 설치 전후 Pod Network 비교"** 의 중간 데이터다.

```bash
kubectl get nodes -o wide
kubectl describe node master01 | grep -A10 Conditions
kubectl get pods -A -o wide
ls -la /etc/cni/net.d/
ip -br addr show
```

`describe node`의 Conditions에서 `NetworkUnavailable` 또는 `KubeletNotReady` 메시지를 확인한다. **kubelet이 왜 Ready가 아니라고 판단하는지가 문장으로 나온다.**

---

## 실패했을 때

```bash
sudo kubeadm reset -f
sudo rm -rf /etc/cni/net.d/*
sudo systemctl restart containerd kubelet
```

`kubeadm reset`이 완벽하지 않아 잔여물이 남는 경우가 있다. 스냅샷 `02-runtime-done`으로 롤백하는 편이 확실하다.

**실패 원인을 먼저 분석한다.** 다시 시도하기 전에 어느 단계에서 멈췄는지, 로그가 무엇을 말하는지 확인한다.

```bash
journalctl -u kubelet -n 50 --no-pager
sudo crictl ps -a
sudo crictl logs <container-id>
```

---

## 실행 결과 기록

<!-- init 출력 전문과 이후 상태를 기록한다 -->

### kubeadm config images list

```text
(미실행)
```

### kubeadm init 출력

```text
(미실행)
```

### init 후 /etc/kubernetes 구조

```text
(미실행)
```

### init 후 kubelet 상태

```text
(미실행)
```

### init 후 노드 및 Pod 상태 (CNI 설치 전)

```text
(미실행)
```

---


---

## 심화 — 클러스터 신뢰 구조: 암호화·인증·인가는 다른 층이다

> init이 생성한 인증서와 CA가 실제로 무엇을 보장하고 무엇을 보장하지 않는지 정리한다.

### 흔한 오해

> "Kubernetes 클러스터는 master가 만든 인증서를 이용한 공개키 암호화 기반 연결이다"

방향은 맞지만 세 가지가 뭉쳐 있다. **분리해야 정확해진다.**

### 세 개의 층

| 층 | 답하는 질문 | 담당 | 인증서의 역할 |
|---|---|---|---|
| **암호화** | 도청·변조를 막는가 | **TLS** | 공개키는 **키 교환에만** 사용 |
| **인증**(authentication) | 너는 **누구**인가 | **X.509 인증서** | 여기가 인증서의 본체 |
| **인가**(authorization) | 너는 **무엇을 할 수 있나** | **RBAC / Node Authorizer** | 인증서는 관여하지 않음 |

#### ① 암호화 — 실제 데이터는 공개키로 암호화하지 않는다

```text
1. TLS 핸드셰이크
   → 공개키/개인키로 서로를 확인하고 "대칭키"를 안전하게 교환
2. 실제 데이터 전송
   → 교환한 대칭키(AES 등)로 암호화       ← 공개키 암호가 아님
```

공개키 암호는 대칭키보다 수백~수천 배 느리다. **키를 안전하게 전달하는 용도로만** 쓰고 이후 통신은 대칭키로 한다.

따라서 "공개키 암호화 기반 통신"보다 **"공개키로 신원을 확인하고 대칭키로 통신"** 이 정확하다.

#### ② 인증 — 인증서가 증명하는 것은 신원뿐이다

```text
subject=O = system:nodes, CN = system:node:worker01
        ↑ 그룹              ↑ 사용자 이름
```

Kubernetes는 인증서 Subject 필드로 신원을 판단한다.

```text
CN (Common Name)   →  사용자 이름
O  (Organization)  →  그룹
```

**인증서가 증명하는 것은 "나는 worker01이다"까지다.** 그 이상은 없다.

#### ③ 인가 — 인증서에는 권한 정보가 없다

인증서 안을 아무리 확인해도 "Pod를 생성할 수 있다" 같은 권한은 없다. **신원만 있다.** 그 신원이 무엇을 할 수 있는지는 **클러스터에 저장된 RBAC 규칙**이 별도로 정한다.

```text
인증서            "나는 system:nodes 그룹의 system:node:worker01이다"
        ↓
클러스터의 RBAC   "system:nodes 그룹은 이런 것들을 할 수 있다"
        ↓
Node Authorizer   "게다가 worker01은 자기 노드의 Pod에만 접근 가능"
```

**같은 CA가 서명한 인증서인데 권한이 완전히 다른 이유가 이것이다.** 인증서가 다른 게 아니라 그 신원에 매핑된 RBAC 규칙이 다르다.

##### 직접 확인하는 방법

`--as`는 다른 신원인 것처럼 요청을 보내는 기능(impersonation)이다. 실제 인증서 없이도 인가 판정을 시험해볼 수 있다.

```bash
# worker01의 신원으로 무엇을 할 수 있는가
kubectl auth can-i --list --as=system:node:worker01 --as-group=system:nodes

# 구체적 판정
kubectl auth can-i delete nodes --as=system:node:worker01 --as-group=system:nodes
kubectl auth can-i create pods  --as=system:node:worker01 --as-group=system:nodes

# 비교: 관리자 권한
kubectl auth can-i --list
```

이 실험이 **"인증 ≠ 인가"** 를 가장 명확하게 보여준다.

### 인증서가 유일한 인증 수단은 아니다

`/etc/kubernetes/pki/`에 `sa.key`가 있다는 것이 힌트다. **그것은 인증서가 아니다.**

| 인증 수단 | 누가 사용 | 형식 |
|---|---|---|
| **X.509 인증서** | 노드(kubelet), 관리자, Control Plane 컴포넌트 | 인증서 |
| **ServiceAccount 토큰** | **Pod 안의 애플리케이션** | JWT — `sa.key`로 서명 |
| **Bootstrap 토큰** | join 초기 단계 | 문자열 |
| **OIDC / Webhook** | 외부 ID 연동 | 프로바이더에 따라 다름 |

**Pod가 apiserver에 접근할 때는 인증서를 쓰지 않는다.** ServiceAccount 토큰(JWT)을 쓴다. Pod마다 인증서를 발급하는 것은 비현실적이기 때문이다.

**로드맵 10~11단계(EKS)에서 이 구조가 확인된다.** EKS는 사용자 인증을 **AWS IAM**으로 하며, IAM 자격증명이 Kubernetes 신원으로 매핑된다. **인증 방식은 교체 가능하고 인가(RBAC)는 그대로**라는 설계가 그때 체감된다.

### 신뢰 상대는 "master 머신"이 아니라 "CA"다

worker가 신뢰하는 것은 `192.168.8.143`이라는 머신이 아니라 **그 CA가 서명했다는 사실**이다.

```text
worker01의 /etc/kubernetes/pki/ca.crt
  → 이 CA가 서명한 것이면 신뢰
  → master01이라는 특정 머신을 신뢰하는 것이 아님
```

**결과**

- Control Plane을 3대로 늘리는 HA 구성에서도 worker 설정은 그대로다. 같은 CA를 공유하므로
- 실무에서는 CA를 Vault나 HSM 등 외부에 두기도 한다. **master 머신에 종속된 개념이 아니다**
- 반대로 **CA 개인키가 유출되면 클러스터 전체가 무너진다.** 임의의 신원을 만들어낼 수 있게 된다

### 정확한 요약

> Kubernetes 클러스터는 노드끼리 직접 신뢰하는 구조가 아니라,
> **하나의 CA를 공통 신뢰 기준으로 삼는 PKI 기반 신원 체계**다.
>
> 각 구성원은 CA가 서명한 인증서로 **자신이 누구인지 증명**하고(인증),
> 통신은 그 인증서로 수립한 **TLS 채널**로 보호되며(암호화),
> 무엇을 할 수 있는지는 **클러스터의 RBAC 규칙**이 별도로 결정한다(인가).
>
> 인증서는 노드·관리자용 주 수단일 뿐이며,
> Pod는 ServiceAccount 토큰을, EKS는 IAM을 쓰는 등 **인증 방식은 교체 가능**하다.

---

---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| kubeadm은 어떤 인증서와 kubeconfig를 생성하는가 | `[certs]`, `[kubeconfig]` 단계 출력 — 상세는 [07-control-plane-analysis.md](07-control-plane-analysis.md) |
| Control Plane 구성요소는 어디에서 실행되는가 | Static Pod — kubelet이 `/etc/kubernetes/manifests/`를 직접 읽어 실행 |
| kubeadm은 Control Plane을 직접 띄우는가 | 아니다. manifest를 배치하고 kubelet이 띄우기를 기다린다 |
| CNI 설치 전에 왜 Pod 통신이 안 되는가 | 바이너리는 있으나 `/etc/cni/net.d/` 설정이 없어 kubelet이 네트워크 미준비로 판단 |
| 인증서는 통신을 암호화하는가 | 직접 하지 않는다. TLS 키 교환에 쓰이고 실제 데이터는 대칭키로 암호화 |
| 인증서에 권한 정보가 있는가 | 없다. 신원(CN·O)만 있고 권한은 RBAC가 별도로 결정 |
| 인증 수단은 인증서뿐인가 | 아니다. ServiceAccount 토큰(JWT), Bootstrap 토큰, OIDC 등이 있으며 EKS는 IAM을 사용 |
| 노드는 master 머신을 신뢰하는가 | 아니다. CA를 신뢰한다. 그래서 HA 구성에서도 worker 설정이 바뀌지 않는다 |

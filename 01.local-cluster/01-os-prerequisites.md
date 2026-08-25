# 01. OS 사전 준비 (3대 전부)

Kubernetes를 설치하기 전에 OS 수준에서 4가지를 준비한다. 대상은 master01, worker01, worker02 **전부**다.

```text
1. /etc/hosts 에 3노드 등록          이름으로 서로 참조할 수 있게
2. swap 비활성화                     kubelet이 기동을 거부하는 조건 제거
3. 커널 모듈 로드                    overlay, br_netfilter
4. sysctl 파라미터 설정               ip_forward, bridge-nf-call-iptables
```

**이 문서의 목적은 명령을 복사하는 것이 아니다.** 각 설정을 빼면 무엇이 어떻게 깨지는지 이해하는 것이 목적이다. Phase 8의 장애 분석과 6단계 장애 실험에서 이 지식이 직접 쓰인다.

---

## 순서가 중요하다

3번(커널 모듈)과 4번(sysctl)은 **반드시 이 순서**여야 한다.

```text
커널 모듈 로드
→ /proc/sys/net/bridge/ 경로가 생성됨
→ 그 경로의 sysctl 값을 설정할 수 있음
```

`br_netfilter` 모듈이 로드되지 않은 상태에서 `net.bridge.bridge-nf-call-iptables`를 설정하려 하면 이렇게 실패한다.

```text
sysctl: cannot stat /proc/sys/net/bridge/bridge-nf-call-iptables: No such file or directory
```

**왜 그런가**: sysctl 파라미터는 커널이 `/proc/sys/` 아래에 노출하는 **가상 파일**이다. `net.bridge.*` 계열 파일은 `br_netfilter` 모듈이 만들어낸다. 모듈이 없으면 설정할 파일 자체가 존재하지 않는다.

이것이 실무에서 흔한 실수다. `/etc/sysctl.d/`에 설정을 써놨는데 모듈 로드 설정을 빠뜨리면, **재부팅 후 sysctl 적용이 조용히 실패**하고 Pod 네트워킹이 안 되는 원인을 찾느라 시간을 쓴다.

---

## 1. `/etc/hosts` 에 3노드 등록

### 현재 상태 확인

```bash
cat /etc/hosts
hostname -I          # 이 노드의 실제 IP
```

Ubuntu 기본값은 보통 이런 모양이다.

```text
127.0.0.1 localhost
127.0.1.1 master01
```

### 설정

3대 모두 아래 3줄을 추가한다.

```bash
sudo tee -a /etc/hosts <<'EOF'

# Kubernetes cluster nodes
192.168.8.143  master01
192.168.8.142  worker01
192.168.8.141  worker02
EOF
```

### 왜 필요한가

**DNS 서버 없이 이름으로 서로를 참조하기 위해서다.** 이 환경에는 내부 DNS가 없다. `ssh worker01`, `ping worker02` 같은 명령이 동작하게 되고, 문서와 로그에서 IP 대신 이름을 쓸 수 있어 가독성이 올라간다.

**kubelet과의 관계**: kubelet은 Node 이름을 **hostname에서 가져온다**(`--hostname-override`를 주지 않는 한). 즉 `kubectl get nodes`에 나오는 이름은 `hostname` 값이다. `/etc/hosts`는 그 이름을 IP로 해석할 수 있게 해준다.

### `127.0.1.1` 줄 처리 — 주석 처리하기로 결정

Ubuntu는 hostname을 `127.0.1.1`에 매핑한다. 이 줄이 우리가 추가한 항목보다 **위에** 있으므로, 이름 해석은 첫 매칭을 반환해 **자기 hostname이 loopback으로 해석된다.**

실제로 Phase 2 진행 중 확인된 현상:

```text
master01 에서 ping worker01  →  192.168.8.142   (남의 이름 = 실제 IP)
worker02 에서 ping worker01  →  192.168.8.142   (남의 이름 = 실제 IP)
worker01 에서 ping worker01  →  127.0.1.1       (자기 이름 = loopback)
```

**kubelet의 Node IP에는 영향이 없다.** kubelet은 `/etc/hosts`가 아니라 기본 경로(default route)가 나가는 인터페이스의 IP를 Node IP로 쓴다. 즉 `kubectl get nodes -o wide`에는 `192.168.8.14x`가 정상적으로 표시된다.

그럼에도 주석 처리하는 이유는 두 가지다.

1. **진단 시 혼란** — 노드마다 같은 명령이 다른 답을 준다. Phase 8 장애 분석에서 이름 해석 결과를 근거로 판단할 때 함정이 된다.
2. **hostname을 해석하는 다른 구성요소** — kubeadm preflight, etcd, 각종 operator가 hostname을 해석했을 때 loopback을 받으면 예상과 다르게 동작할 수 있다.

필수는 아니지만 한 줄로 제거할 수 있는 불확실성이다.

```bash
grep -n '127.0.1.1' /etc/hosts
getent hosts $(hostname)                              # 수정 전: 127.0.1.1

sudo sed -i 's/^127\.0\.1\.1/#127.0.1.1/' /etc/hosts

getent hosts $(hostname)                              # 수정 후: 192.168.8.14x
```

**주석 처리해도 안전한 이유**: 우리가 추가한 `192.168.8.14x  <hostname>` 항목이 있으므로 이름 해석이 계속 성공한다. `/etc/hosts`는 파일 조회이므로 인터페이스가 down이어도 해석 자체는 동작한다. 따라서 `sudo`가 hostname 역방향 해석에서 타임아웃되는 문제도 발생하지 않는다.

> **주의**: `127.0.1.1` 줄을 주석 처리하려면 반드시 실제 IP 항목이 먼저 추가되어 있어야 한다. 둘 다 없으면 hostname 해석이 실패해 `sudo` 실행마다 수 초씩 지연된다.

### 확인

```bash
ping -c 1 master01
ping -c 1 worker01
ping -c 1 worker02
getent hosts worker01     # 이름 해석 결과 확인
```

3대 모두에서 나머지 2대가 이름으로 해석되고 응답해야 한다.

---

## 2. swap 비활성화

### 현재 상태 확인

```bash
swapon --show
free -h | grep -i swap
grep -n swap /etc/fstab
```

이 환경은 `/swap.img` **파일** 방식 3.8GB다. (파티션 방식이 아님)

### 설정

```bash
# 1) 현재 세션에서 즉시 비활성화
sudo swapoff -a

# 2) 재부팅 후에도 유지되도록 /etc/fstab 에서 주석 처리
sudo cp /etc/fstab /etc/fstab.bak          # 원본 백업
sudo sed -i '/\sswap\s/s/^/#/' /etc/fstab
```

`sed` 명령의 의미: `\sswap\s`(앞뒤가 공백인 `swap` 문자열)를 포함한 줄의 맨 앞(`^`)에 `#`를 붙인다. `/etc/fstab`에서 swap 항목만 골라 주석 처리하는 것이다.

**두 단계가 모두 필요한 이유**: `swapoff -a`는 현재 커널 상태만 바꾼다. `/etc/fstab`은 부팅 시 마운트 목록이므로, 이걸 고치지 않으면 재부팅하면 swap이 되살아난다. **Phase 0의 고정 IP 작업과 같은 구조의 문제다** — "지금 동작함"과 "재시작을 견딤"은 다르다.

### 디스크 3.8GB 회수 (선택)

swap을 안 쓸 것이므로 파일을 삭제해 공간을 되찾을 수 있다.

```bash
sudo rm /swap.img         # swapoff 이후에만 실행할 것
df -h /
```

루트 여유가 16GB인 상황에서 3.8GB는 의미 있는 크기다. 5단계에서 Prometheus와 Loki가 디스크를 쓰므로 미리 확보해두면 좋다.

> **주의**: `swapoff -a`를 하지 않은 상태에서 `/swap.img`를 삭제하면 커널이 사용 중인 파일이 사라져 시스템이 불안정해진다. 반드시 `swapon --show`가 빈 출력인 것을 확인한 뒤 삭제한다.

### 왜 kubelet은 swap을 거부하는가

kubelet은 기본 설정(`failSwapOn: true`)에서 swap이 활성화되어 있으면 **아예 기동하지 않는다.** 단순한 취향이 아니라 Kubernetes의 리소스 모델과 충돌하기 때문이다.

**이유 1 — 메모리 Limit이 강제되지 않는다**

```text
swap 없음:  컨테이너가 Memory Limit 초과 → 커널이 OOMKill → Pod 재시작
swap 있음:  컨테이너가 Memory Limit 초과 → 디스크로 스왑 → 계속 실행 (매우 느리게)
```

Limit을 걸어둔 의미가 사라진다. 장애가 명확한 실패(OOMKilled) 대신 **원인 불명의 성능 저하**로 나타난다. 운영에서 이게 훨씬 나쁘다 — 알람도 안 울리고 원인 파악도 어렵다.

**이유 2 — Scheduler의 판단 근거가 무너진다**

Scheduler는 노드의 **물리 메모리** 여유를 보고 Pod를 배치한다. swap이 있으면 "메모리가 남아 있다"는 판단이 실제 물리 메모리를 의미하지 않게 되어, 과다 배치가 일어난다.

**이유 3 — 성능 예측이 불가능해진다**

RAM 접근은 나노초, 디스크는 밀리초 단위다. 약 1000배 차이다. 어떤 Pod의 어떤 메모리 페이지가 스왑됐는지 알 수 없으므로 응답시간이 무작위로 튄다. 6단계에서 p95/p99 응답시간을 측정하는데, swap이 켜져 있으면 측정값 자체가 무의미해진다.

**6단계 시나리오 D와 직결된다**

시나리오 D는 Memory Limit을 낮추고 OOMKilled를 재현하는 실험이다. **swap이 켜져 있으면 이 실험이 성립하지 않는다.** OOMKill 대신 스왑이 일어나 Container 종료 사유, Exit Code, Restart Count를 관찰할 수 없다.

> **참고**: Kubernetes 1.22부터 `NodeSwap` 기능 게이트로 swap을 제한적으로 허용하는 기능이 개발되어 왔다. 다만 이 프로젝트에서는 기본 동작(swap 비활성화)을 따른다. 표준 동작을 먼저 이해하는 것이 목적이고, 6단계 실험이 OOMKilled 관찰에 의존하기 때문이다.

### 확인

```bash
swapon --show        # 아무 출력이 없어야 정상
free -h              # Swap 행이 0B
grep swap /etc/fstab # 주석(#) 처리되어 있어야 함
```

`swapon --show`가 **빈 출력**인 것이 정상이다. 명령이 실패한 게 아니라 활성 swap이 없다는 뜻이다.

---

## 3. 커널 모듈 로드 (`overlay`, `br_netfilter`)

### 설정

```bash
# 부팅 시 자동 로드되도록 설정 파일 작성
sudo tee /etc/modules-load.d/k8s.conf <<'EOF'
overlay
br_netfilter
EOF

# 현재 세션에서 즉시 로드
sudo modprobe overlay
sudo modprobe br_netfilter
```

여기서도 **두 단계**다. `/etc/modules-load.d/k8s.conf`는 부팅 시 적용되고, `modprobe`는 지금 당장 적용한다. 재부팅하지 않고 진행하려면 둘 다 필요하다.

`/etc/modules-load.d/` 아래에 별도 파일(`k8s.conf`)로 두는 이유: `/etc/modules`를 직접 수정하면 어떤 항목을 우리가 추가했는지 추적하기 어렵고, 패키지 업그레이드 시 충돌 가능성이 있다. 목적별로 파일을 분리하면 나중에 되돌리기도 쉽다.

### `overlay` — 컨테이너 이미지가 동작하는 원리

containerd는 기본적으로 **overlayfs 스냅샷터**를 사용한다.

컨테이너 이미지는 여러 개의 **읽기 전용 레이어**로 구성된다.

```text
컨테이너에서 보이는 파일시스템 (하나로 합쳐진 뷰)
  ↑ overlayfs가 병합
  ├─ 쓰기 가능 레이어      ← 컨테이너 실행 중 변경사항 (컨테이너별로 하나)
  ├─ 애플리케이션 레이어    ← 읽기 전용 (이미지)
  ├─ 라이브러리 레이어      ← 읽기 전용 (이미지, 여러 컨테이너가 공유)
  └─ 베이스 OS 레이어      ← 읽기 전용 (이미지, 여러 컨테이너가 공유)
```

**핵심 이점**: 같은 베이스 이미지를 쓰는 컨테이너 10개를 띄워도 읽기 전용 레이어는 **디스크에 한 번만** 존재한다. 컨테이너마다 새로 생기는 건 쓰기 가능 레이어뿐이다. 이것이 컨테이너가 VM보다 압도적으로 가벼운 이유 중 하나다.

**없으면**: containerd가 overlayfs 스냅샷터를 쓸 수 없다. 더 느린 방식으로 대체되거나 컨테이너 생성이 실패한다.

> 최신 커널은 모듈을 처음 사용할 때 자동 로드하는 경우가 많다. 그래도 명시적으로 로드하는 이유는 **containerd가 시작되는 시점에 이미 준비되어 있음을 보장**하기 위함이다. 부팅 순서에 의존하는 불확실성을 없앤다.

### `br_netfilter` — 이게 없으면 Service가 동작하지 않는다

이 모듈이 4번의 sysctl 설정과 묶여 Kubernetes 네트워킹의 핵심을 이룬다.

**문제 상황**: 리눅스 브리지(bridge)는 **L2 스위치**처럼 동작한다. 브리지를 통과하는 패킷은 L3 라우팅을 거치지 않으므로, 기본적으로 **iptables 규칙을 타지 않는다.**

```text
br_netfilter 없음
  Pod A ──┐
          ├── 브리지 (L2 스위칭) ──> Pod B
  Pod C ──┘         ↑
                    iptables를 우회함 (통과하지 않음)

br_netfilter 있음
  Pod A ──┐
          ├── 브리지 ──> iptables 체인 통과 ──> Pod B
  Pod C ──┘
```

**왜 이게 치명적인가**: kube-proxy는 Service를 **iptables 규칙으로 구현**한다. ClusterIP(`10.96.x.x`)로 온 패킷을 실제 Pod IP로 DNAT하는 규칙이다.

```text
Pod가 ClusterIP 10.96.0.10:53 (CoreDNS) 로 요청
→ iptables DNAT 규칙이 실제 Pod IP 10.244.1.5:53 으로 변환
→ CoreDNS Pod에 도달
```

브리지를 통과하는 패킷이 iptables를 안 타면 **이 변환이 일어나지 않는다.** 결과적으로 Service로 향하는 통신이 전부 실패한다. 증상은 "Pod IP로는 직접 통신되는데 Service 이름으로는 안 된다", "DNS 조회가 타임아웃된다"로 나타난다.

이것이 [00-environment.md](00-environment.md)에 기록한 Service Network(`10.96.0.0/12`)가 실제 인터페이스에 붙지 않는 가상 대역이라는 사실과 연결된다. 가상 대역이 동작하는 것은 순전히 iptables 규칙 덕분이고, `br_netfilter`가 그 규칙이 적용될 수 있게 해준다.

### 확인

```bash
lsmod | grep -E 'overlay|br_netfilter'
```

두 모듈이 모두 나와야 한다. 출력 예시:

```text
br_netfilter           32768  0
bridge                311296  1 br_netfilter
overlay               212992  0
```

`bridge` 모듈이 함께 나오는 것은 정상이다. `br_netfilter`가 `bridge`에 의존하므로 자동으로 함께 로드된다. 세 번째 열(`1 br_netfilter`)은 "bridge 모듈을 br_netfilter가 사용 중"이라는 의존 관계 표시다.

모듈이 로드되면 sysctl 경로가 생겼는지도 확인한다.

```bash
ls /proc/sys/net/bridge/
```

`bridge-nf-call-iptables` 등의 파일이 보여야 4번을 진행할 수 있다.

---

## 4. sysctl 파라미터 설정

### 설정

```bash
sudo tee /etc/sysctl.d/k8s.conf <<'EOF'
net.ipv4.ip_forward                 = 1
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF

# 재부팅 없이 즉시 적용
sudo sysctl --system
```

`/etc/sysctl.conf`를 직접 수정하지 않고 `/etc/sysctl.d/k8s.conf`로 분리하는 이유는 커널 모듈과 같다 — 우리가 추가한 설정을 추적 가능하게 하고 패키지 업그레이드와의 충돌을 피한다.

`sysctl --system`은 `/etc/sysctl.d/`, `/usr/lib/sysctl.d/` 등 모든 위치의 설정을 다시 읽어 적용한다. 출력에 각 파일 경로와 적용된 값이 나열되므로, **우리 파일의 3개 값이 보이는지 확인**한다.

### `net.ipv4.ip_forward = 1` — 노드가 라우터 역할을 한다

리눅스는 기본적으로 **자기 자신이 목적지가 아닌 패킷을 버린다.** 서버는 라우터가 아니므로 이게 안전한 기본값이다.

그런데 Kubernetes에서 노드는 **Pod 트래픽의 라우터**다.

```text
Node 네트워크    192.168.8.0/24     노드 자신의 IP
Pod 네트워크     10.244.0.0/16      Pod의 IP  ← 서로 다른 대역
```

Pod IP는 노드 IP와 **다른 대역**이다. 따라서 다음 트래픽은 모두 노드가 "전달(forward)"해야 한다.

```text
worker01의 Pod (10.244.1.5)
→ worker01 노드가 패킷을 받아서 전달
→ worker02 노드
→ worker02의 Pod (10.244.2.7)

Pod (10.244.1.5)
→ 노드가 전달 + NAT
→ 외부 인터넷
```

**없으면**: 같은 노드 안의 Pod끼리는 통신될 수 있지만 **다른 노드의 Pod와는 통신이 안 되고**, Pod에서 외부 인터넷으로도 나갈 수 없다. Phase 6 검증에서 "서로 다른 노드의 Pod 간 통신"을 확인하는 이유가 바로 이것이다.

### `net.bridge.bridge-nf-call-iptables = 1` — 3번 모듈의 스위치

`br_netfilter` 모듈은 기능을 **제공**하고, 이 sysctl 값이 그 기능을 **켠다.** 모듈만 로드하고 이 값을 켜지 않으면 브리지 패킷은 여전히 iptables를 우회한다.

```text
br_netfilter 모듈 로드     → /proc/sys/net/bridge/ 경로 생성 (기능 제공)
bridge-nf-call-iptables=1  → 브리지 패킷이 iptables를 통과 (기능 활성화)
```

`ip6tables` 버전도 함께 설정하는 이유: IPv6를 쓰지 않더라도 Kubernetes 공식 문서가 요구하며, 나중에 IPv6를 다룰 때 누락으로 인한 문제를 피한다.

### 확인

```bash
sysctl net.ipv4.ip_forward
sysctl net.bridge.bridge-nf-call-iptables
sysctl net.bridge.bridge-nf-call-ip6tables
```

셋 다 `= 1`이어야 한다.

---

## 재부팅 검증 (필수)

Phase 0에서 배운 교훈을 적용한다. **"지금 동작함"이 아니라 "재시작을 견딤"이 완료 조건이다.**

```bash
sudo reboot
```

재부팅 후 3대에서 전부 확인한다.

```bash
swapon --show                              # 빈 출력 (swap 없음)
lsmod | grep -E 'overlay|br_netfilter'     # 두 모듈 로드됨
sysctl net.ipv4.ip_forward                 # = 1
sysctl net.bridge.bridge-nf-call-iptables  # = 1
ip -4 addr show ens33                      # 고정 IP 유지 (Phase 0 회귀 확인)
ping -c 1 worker01                         # /etc/hosts 동작
```

**여기서 실패하기 쉬운 지점**: `sysctl` 값이 `0`으로 나오는 경우다. 원인은 대부분 `/etc/modules-load.d/k8s.conf`를 만들지 않아 부팅 시 `br_netfilter`가 로드되지 않았고, 그래서 `/etc/sysctl.d/k8s.conf` 적용이 조용히 실패한 것이다. 이 문서 앞부분의 "순서가 중요하다"에서 설명한 문제가 실제로 발생한 상태다.

---

## 실행 결과 기록 (2026-07-30)

3대 모두 동일한 결과가 나왔다.

```text
$ swapon --show
(빈 출력)                          # 활성 swap 없음 — 정상

$ lsmod | grep -E 'overlay|br_netfilter'
br_netfilter           32768  0
bridge                425984  1 br_netfilter    # bridge를 br_netfilter가 사용 중
overlay               212992  0

$ sysctl net.ipv4.ip_forward
net.ipv4.ip_forward = 1

$ sysctl net.bridge.bridge-nf-call-iptables
net.bridge.bridge-nf-call-iptables = 1

$ ping -c 1 worker01
64 bytes from worker01 (192.168.8.142): icmp_seq=1 ttl=64 time=1.04 ms
```

`lsmod` 출력의 세 번째 열 `1 br_netfilter`는 **의존 관계 표시**다. `bridge` 모듈을 `br_netfilter`가 사용 중이라는 뜻이며, `br_netfilter`를 로드하면 `bridge`가 자동으로 함께 로드된다.

### 재부팅 검증 완료 (2026-08-03)

값이 맞는 것만으로는 부족하다. 위 값들은 **수동 적용 결과일 수도 있기 때문**이다.

```text
swapoff -a   →  현재 커널 상태만 변경
/etc/fstab   →  재부팅 후 유지 여부를 결정        ← 진짜 검증 대상

modprobe                      →  현재 커널 상태만 변경
/etc/modules-load.d/k8s.conf  →  부팅 시 로드 여부를 결정   ← 진짜 검증 대상
```

Phase 0의 고정 IP 작업과 동일한 구조다. 한 줄로 확인한다.

```bash
uptime -p ; swapon --show ; lsmod | grep -E 'overlay|br_netfilter' ; \
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables ; \
getent hosts $(hostname) ; timedatectl | grep synchronized
```

**결과 (3대 동일)**

```text
$ uptime -p
up 3 days, 17 hours          # 설정 작성(7/30 오전) 이후 부팅됨

$ swapon --show
(빈 출력)

$ lsmod | grep -E 'overlay|br_netfilter'
br_netfilter           32768  0
bridge                425984  1 br_netfilter
overlay               212992  0

$ sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables
net.ipv4.ip_forward = 1
net.bridge.bridge-nf-call-iptables = 1

$ getent hosts $(hostname)
192.168.8.143   master01     # 127.0.1.1이 아님 → 주석 처리 적용됨
```

### 검증 상태 — 전부 통과

| 항목 | 3대 결과 |
|---|---|
| swap 비활성화 | 통과 |
| `overlay` / `br_netfilter` 로드 | 통과 |
| `net.ipv4.ip_forward = 1` | 통과 |
| `net.bridge.bridge-nf-call-iptables = 1` | 통과 |
| 노드 간 이름 해석 및 통신 | 통과 |
| **재부팅 후 유지** | **통과** — 재부팅 + 3.7일 연속 가동을 견딤 |
| `127.0.1.1` 주석 처리 | 통과 — hostname이 실제 IP로 해석됨 |

**Phase 2 종료.** `uptime`이 3일 17시간이라는 것은 단순히 재부팅을 한 번 견딘 것을 넘어, 설정이 장기 가동 중에도 유지되고 있음을 뜻한다.

---

## 반복 작업 기록

> 로드맵 학습 원칙 1에 따라 이 단계는 3대에 수동으로 반복한다. 이후 Ansible 도입의 근거 자료로 쓰기 위해, 반복 과정에서 느낀 불편함과 실수를 아래에 기록한다.

| 항목 | 내용 |
|---|---|
| 3대에 동일 작업을 반복하며 느낀 점 | (작성) |
| 실수했거나 빠뜨린 단계 | (작성) |
| 자동화하면 좋을 부분 | (작성) |
| 자동화하면 오히려 이해가 어려워질 부분 | (작성) |

---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| kubelet은 왜 swap이 켜져 있으면 기동을 거부하는가 | Memory Limit 강제, Scheduler 판단, 성능 예측이 모두 무너지기 때문 |
| `overlay` 모듈은 무엇을 가능하게 하는가 | 이미지 레이어 공유 — 컨테이너가 가벼운 이유 |
| `br_netfilter`가 없으면 무엇이 깨지는가 | 브리지 패킷이 iptables를 우회 → kube-proxy의 Service DNAT 규칙이 적용되지 않음 |
| `ip_forward`가 없으면 무엇이 깨지는가 | 다른 노드의 Pod 간 통신, Pod → 외부 통신 |
| 커널 모듈과 sysctl의 순서가 왜 중요한가 | 모듈이 sysctl 경로를 생성하므로, 모듈 없이는 설정할 파일 자체가 없음 |

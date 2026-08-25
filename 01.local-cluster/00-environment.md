# 00. 환경 정보 및 네트워크 설계

## 호스트 (Hypervisor)

| 항목 | 값 |
|---|---|
| OS | Windows 11 Enterprise |
| CPU | AMD Ryzen 5 3500X — 6코어 / 6스레드 (SMT 없음) |
| RAM | 32GB |
| 하이퍼바이저 | VMware Workstation |

## 노드 구성

| hostname | IP | 역할 | vCPU | RAM |
|---|---|---|---|---|
| master01 | 192.168.8.143 | control-plane | 2 | 4GB |
| worker01 | 192.168.8.142 | worker | 2 | 4GB |
| worker02 | 192.168.8.141 | worker | 2 | 4GB |

> **확인 필요**: 위 vCPU/RAM 값은 계획값이다. Phase 0에서 `nproc`, `free -h`로 실측한 뒤 이 표를 갱신한다.

### master01에 vCPU 2개 이상이 필요한 이유

kubeadm은 Control Plane에 **최소 2 CPU**를 요구하고, 이를 사전 검사(preflight check)에서 강제로 차단한다.

```text
[ERROR NumCPU]: the number of available CPUs 1 is less than the required 2
```

`--ignore-preflight-errors=NumCPU`로 우회할 수 있지만 하지 않는다. Control Plane 노드 하나에서 `kube-apiserver`, `etcd`, `kube-controller-manager`, `kube-scheduler`, `kubelet`, `containerd`가 동시에 동작한다. 특히 **etcd는 디스크 쓰기 지연에 민감**해서, CPU 경합으로 합의(consensus) 처리가 늦어지면 리더 선출을 반복하며 클러스터 전체가 불안정해진다.

증상이 "kubectl이 느리다" 정도로만 나타나 원인 파악이 어렵고, 5단계에서 Prometheus를 올리면 원인 불명의 타임아웃으로 이어진다.

### 노드 OS (2026-07-30 master01 실측)

| 항목 | 값 |
|---|---|
| 배포판 | Ubuntu 24.04.3 LTS (noble) |
| 커널 | 6.8.0-71-generic |
| 아키텍처 | x86-64 |
| 루트 파일시스템 | LVM — `/dev/mapper/ubuntu--vg-ubuntu--lv` (24G, 여유 16G) |
| 네트워크 인터페이스 | `ens33` (altname `enp2s1`) |
| swap | `/swap.img` **파일** 방식, 3.8G |
| ufw | inactive |
| 시간 동기화 | NTP active, `System clock synchronized: yes` |

커널 6.8은 `overlay`, `br_netfilter` 모듈을 모두 지원한다. Ubuntu 24.04 LTS는 kubeadm 설치가 검증된 조합이다.

> **worker01 / worker02는 미확인.** 3대가 동일한 이미지에서 만들어졌더라도 실측해 기록한다.

### 디스크 — 5단계에서 다시 볼 지점

루트 파티션 여유가 16GB다. 1단계(컨테이너 이미지 몇 GB)는 충분하지만, 5단계에서 Prometheus(메트릭 시계열)와 Loki(로그)를 올리면 빠르게 찬다.

LVM 구성이므로 조치는 가능하다: VMware에서 가상 디스크 크기를 늘린 뒤 `pvresize` → `lvextend` → `resize2fs`로 **온라인 확장**할 수 있다. 지금은 조치하지 않고, 5단계 진입 시 `df -h`를 먼저 확인한다.

## CPU 오버커밋 — 미리 알아둘 전제

vCPU 합계가 **6개**로, 호스트 논리 코어(6개)를 전부 할당하는 구성이다. 여기에 Windows 호스트와 VMware 자체 오버헤드가 추가된다.

평소 유휴 상태의 Kubernetes 노드는 CPU를 거의 쓰지 않으므로 문제되지 않는다. 다만 다음을 미리 기록해 둔다.

> **6단계 k6 부하 테스트에서 응답시간이 늘어나거나 p99가 튀는 현상은 호스트 CPU 경합이 원인일 수 있다.**
>
> 이것을 Kubernetes나 애플리케이션 문제로 오진하지 않기 위한 전제다. 부하 테스트 중에는 Windows 작업 관리자에서 호스트 CPU 사용률을 함께 관찰하고, 호스트가 100%에 붙어 있으면 측정값의 절대치는 신뢰하지 않는다. **변화의 방향(장애 전후 비교)만 해석한다.**

## 노드 IP는 반드시 고정(static)이어야 한다

**2026-07-30 확인 결과, master01의 IP는 DHCP 임대 상태였다.**

```text
inet 192.168.8.143/24 metric 100 brd 192.168.8.255 scope global dynamic ens33
     valid_lft 1078sec preferred_lft 1078sec
```

`dynamic`과 `valid_lft`(남은 임대 시간)가 그 근거다. **Phase 5(`kubeadm init`) 전에 고정 IP로 변경해야 한다.**

### 왜 치명적인가

kubeadm은 `init` 시점의 IP를 인증서와 설정 파일에 **구워 넣는다**.

```text
/etc/kubernetes/pki/apiserver.crt   SAN(Subject Alternative Name)에 노드 IP 포함
/etc/kubernetes/admin.conf          server: https://192.168.8.143:6443
/etc/kubernetes/kubelet.conf        server: https://192.168.8.143:6443
etcd peer / client URL              https://192.168.8.143:2380 / :2379
worker의 kubelet.conf (join 이후)   server: https://192.168.8.143:6443
```

IP가 바뀌면 이 전부가 무효가 된다.

```text
IP 변경
→ apiserver는 새 IP에서 리스닝하지만 인증서 SAN에는 옛 IP만 존재
→ kubectl: x509: certificate is valid for 192.168.8.143, not <새 IP>
→ worker: apiserver 연결 실패 → 전체 노드 NotReady
→ 복구: 인증서 전체 재발급 + 3대 kubeconfig 수정, 또는 클러스터 재구축
```

### 진단이 어려운 이유

DHCP 서버는 보통 같은 클라이언트에 같은 IP를 재할당하므로 **며칠 또는 몇 주간 정상 동작한다.** VM을 장기간 껐다 켰거나 임대가 만료된 뒤에야 터지고, 그 시점에는 원인을 IP 변경이라고 떠올리기 어렵다. 운영에서 실제로 시간을 많이 잡아먹는 유형의 장애다.

### 2026-07-30 확인 결과 — VMware NAT 모드 확정

```text
default via 192.168.8.2 dev ens33 proto dhcp src 192.168.8.143 metric 100
DNS Servers: 192.168.8.2
DNS Domain: localdomain

/etc/netplan/50-cloud-init.yaml  (권한 600)
network:
  version: 2
  ethernets:
    ens33:
      dhcp4: true
```

게이트웨이가 `.2`이므로 **VMware NAT(VMnet8)** 모드다. (Bridged는 보통 물리 공유기인 `.1`) DNS도 `192.168.8.2`로, VMware NAT가 제공하는 DNS 포워더다.

### DHCP 풀 범위 조정 — 하지 않기로 결정 (2026-07-30)

VMware NAT의 기본 DHCP 풀은 `192.168.8.128 ~ 192.168.8.254`이므로, 고정 IP `.141~.143`은 풀 안쪽이다. 일반적으로는 풀을 축소해 고정 IP를 풀 밖으로 빼는 것이 안전하다.

**다만 이 환경에서는 조정하지 않는다.** vmnet8에 이 3대만 존재하고 셋 모두 고정 IP로 전환하면 DHCP를 요청하는 주체가 없어져 충돌이 성립하지 않는다.

> **남은 조건**: 이후 vmnet8에 VM을 추가할 때(k6 부하 생성용, bastion 등)는 DHCP가 `.141~.143`을 배정할 수 있다. 그때는 추가 VM에 풀 밖 고정 IP를 주거나 풀 범위를 `.128~.140`으로 축소한다. VM을 추가하는 시점에 이 항목을 다시 확인한다.

### 고정 IP 전환 절차

> **VMware 콘솔에서 작업한다.** SSH로 접속한 상태에서 네트워크 설정을 잘못 적용하면 접속이 끊겨 복구할 수 없다.

**1. netplan 설정 교체** (노드별로 `addresses` 값만 다름)

```yaml
# /etc/netplan/50-cloud-init.yaml
network:
  version: 2
  ethernets:
    ens33:
      dhcp4: false
      addresses:
        - 192.168.8.143/24        # master01. worker01=.142, worker02=.141
      routes:
        - to: default
          via: 192.168.8.2
      nameservers:
        addresses: [192.168.8.2, 8.8.8.8]
        search: [localdomain]
```

`nameservers`에 `8.8.8.8`을 함께 두는 이유: `192.168.8.2`는 VMware NAT의 DNS 포워더로, VMware 서비스가 멈추면 이름 해석이 전부 실패한다. 공인 DNS를 보조로 두어 패키지 설치 같은 외부 통신이 끊기지 않게 한다.

`routes`로 기본 게이트웨이를 지정한다. 예전 문법인 `gateway4`는 deprecated이며 netplan이 경고를 출력한다.

**2. cloud-init의 네트워크 관리 비활성화 (필수)**

```bash
echo 'network: {config: disabled}' | sudo tee /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
```

**이 단계를 빠뜨리면 재부팅 후 DHCP로 돌아간다.** Ubuntu Server는 cloud-init이 부팅마다 `50-cloud-init.yaml`을 재생성하기 때문이다. 파일명이 `50-cloud-init.yaml`인 것이 cloud-init 관리 하에 있다는 증거다.

**3. 적용**

```bash
sudo chmod 600 /etc/netplan/50-cloud-init.yaml   # 권한 경고 방지
sudo netplan generate
sudo netplan try                                  # 120초 후 자동 롤백 — 안전한 검증
sudo netplan apply                                # try 성공 후 확정
```

`netplan try`를 먼저 쓰는 이유: 설정이 잘못되어 네트워크가 끊기면 120초 후 이전 설정으로 자동 복구된다. `apply`는 즉시 확정되므로 복구 장치가 없다.

**4. 검증**

```bash
ip -4 addr show ens33      # 'dynamic'과 valid_lft가 사라져야 정상
ip route | grep default    # via 192.168.8.2 유지
ping -c 2 8.8.8.8          # 외부 통신
resolvectl status          # DNS 확인
sudo reboot                # 재부팅 후 IP가 유지되는지 확인 (cloud-init 무효화 검증)
```

`ip -4 addr show`에서 **`dynamic` 키워드와 `valid_lft` 표시가 없어야** 고정 IP로 전환된 것이다. 재부팅 후에도 유지되는지 확인하는 것이 2단계(cloud-init 비활성화)의 검증이다.

### 고정 IP 지정 시 확인할 것

| 확인 항목 | 명령 | 이유 |
|---|---|---|
| 게이트웨이 | `ip route \| grep default` | netplan `routes.via`에 필요 |
| DNS | `resolvectl status` | netplan `nameservers`에 필요 |
| netplan 파일 | `ls /etc/netplan/` | 수정 대상 파일명 확인 |
| DHCP 풀 범위 | VMware / 공유기 설정 | **고정 IP가 풀 안에 있으면 IP 충돌 위험** |

게이트웨이 주소로 VMware 네트워크 모드를 판별할 수 있다.

| 게이트웨이 | 모드 | 주의 |
|---|---|---|
| `192.168.8.2` | VMware NAT | VMware NAT DHCP 기본 풀은 `.128~.254`. 현재 `.141~.143`이 풀 안쪽일 가능성이 높다 |
| `192.168.8.1` | Bridged | 물리 공유기의 DHCP 풀 범위 확인 필요 |

고정 IP를 DHCP 풀 안쪽에 지정하면, 나중에 다른 기기가 같은 IP를 임대받아 충돌한다. 풀 밖 주소로 옮기거나 풀 범위를 줄인다.

### 추가 주의: cloud-init이 netplan 설정을 되돌린다

Ubuntu Server는 cloud-init이 부팅 시 `/etc/netplan/50-cloud-init.yaml`을 재생성한다. netplan만 수정하면 **재부팅 후 DHCP로 돌아간다.** cloud-init의 네트워크 관리를 비활성화해야 설정이 유지된다.

## VM 복제 시 확인 필수 — product_uuid 고유성

VM 3대를 템플릿에서 복제했다면 `machine-id`와 `product_uuid`가 동일할 수 있다.

```bash
cat /etc/machine-id
sudo cat /sys/class/dmi/id/product_uuid
```

**Kubernetes 공식 요구사항으로, 노드마다 `product_uuid`가 고유해야 한다.** kubelet이 노드를 식별하는 데 사용하며 중복되면 노드 등록이 꼬인다. `machine-id` 중복은 DHCP 서버가 여러 VM에 같은 IP를 할당하려 하거나 journald 로그가 섞이는 문제를 일으킨다.

| 노드 | machine-id | product_uuid | 판정 |
|---|---|---|---|
| master01 | `2d1e6339f3e741bc852ef6dea43b9894` | `e7354d56-2d06-3a7d-5d7a-1a41078f7cd4` | 고유 |
| worker01 | 미확인 | 미확인 | 확인 필요 |
| worker02 | `3c1083b407504c4c8c1a575622e1be32` | `8a1a4d56-b34e-74f1-9154-6971d530a687` | 고유 |

**2026-07-30 확인**: master01과 worker02의 machine-id, product_uuid가 모두 다르다. 복제로 인한 중복 문제는 없다. worker01만 확인하면 이 항목은 종료된다.

## hostname 오타 — 해결됨 (2026-07-30)

**최초 확인 시 worker02의 hostname이 `woker02`로 `r`이 빠져 있었다. `hostnamectl set-hostname worker02`로 수정 완료.** worker01은 오타가 없었다.

> **참고**: hostname을 바꾼 뒤에도 셸 프롬프트는 `root@woker02`로 남는다. 프롬프트는 로그인 시점에 한 번 읽은 값을 유지하므로, 재로그인하면 갱신된다. 판단 기준은 `hostnamectl` 출력이다.

아래는 이 문제가 왜 중요했는지에 대한 기록이다.

**2026-07-30 최초 확인 결과 worker02의 hostname이 `woker02`로, `r`이 빠져 있었다.**

**왜 반드시 고쳐야 하는가**: kubelet은 hostname을 그대로 Kubernetes Node 이름으로 사용한다. 이 상태로 `kubeadm join`하면 노드가 영구적으로 `woker02`로 등록된다.

```text
$ kubectl get nodes
master01   Ready   control-plane
worker01   Ready   <none>
woker02    Ready   <none>          ← 오타가 클러스터에 고정됨
```

join 이후에 바꾸려면 `kubectl delete node` + `kubeadm reset` 후 재join이 필요하다. 지금은 명령 두 줄이면 된다.

```bash
sudo hostnamectl set-hostname worker02
# /etc/hosts 의 옛 hostname 항목도 함께 수정
```

worker01도 동일한 오타(`woker01`)가 있는지 확인한다.

## 커널 버전 불일치

```text
master01   Linux 6.8.0-71-generic
worker02   Linux 6.8.0-136-generic
```

같은 Ubuntu 24.04.3인데 커널 패치 레벨이 다르다. **기능상 블로커는 아니다** — 양쪽 모두 `overlay`, `br_netfilter`와 Kubernetes 요구 커널 기능을 지원한다.

다만 6단계에서 "worker01과 worker02의 복구 시간이 왜 다른가"를 분석할 때 노드 간 다른 변수가 있으면 해석이 어려워진다. 실험 설계상 3대 조건을 동일하게 맞추는 것이 유리하다.

**Phase 2 전에 3대 모두 `sudo apt update && sudo apt upgrade`로 정렬한다.** Kubernetes 패키지 설치 전이므로 지금이 가장 안전한 시점이다. (Phase 4에서 `apt-mark hold`를 적용한 뒤에는 kubelet이 함께 올라가지 않도록 주의해야 한다)

## 시간대 (Time Zone)

**2026-07-30 확인 결과 `Etc/UTC`.** 한국 시간과 9시간 차이가 난다.

```text
Time zone: Etc/UTC (UTC, +0000)
Local time: Thu 2026-07-30 01:32:31 UTC     ← 한국 시간 10:32
```

6단계 Incident Report에 `발생 시각 / 탐지 시각 / 복구 시각`을 기록한다. k6 부하 테스트를 한국 시간으로 보면서 `kubectl get events`와 Loki 로그를 UTC로 읽으면 시각 대조에서 지속적으로 혼란이 생긴다.

| 선택 | 장점 | 단점 |
|---|---|---|
| `Asia/Seoul` | 벽시계와 로그 시각 일치 → 실험 기록이 쉬움 | 실무 관행과 다름 |
| UTC 유지 | 실무 표준. AWS/CloudWatch도 UTC이므로 10단계 이후 일관성 | 매번 +9 계산 필요 |

**어느 쪽을 택하든 3대가 동일해야 한다.** 노드마다 다르면 로그 상관관계 분석이 불가능해진다.

> **결정 (2026-07-30): `Asia/Seoul`로 통일한다.**

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl                                  # Time zone: Asia/Seoul (KST, +0900)
```

3대 모두 적용한다. 변경 후에도 `System clock synchronized: yes`가 유지되어야 한다 — 시간대는 표시 방식만 바꾸는 것이고 NTP 동기화는 UTC 기준으로 계속 동작한다.

**알아둘 점**: Kubernetes와 Prometheus는 내부적으로 항상 UTC로 시각을 저장한다. 시간대 변경은 노드의 OS 로그(`journalctl`)와 셸 출력에 영향을 준다. 즉 `journalctl`은 KST로 보이지만 `kubectl get events`의 타임스탬프나 Prometheus 시계열은 UTC 기반이며, Grafana가 브라우저 시간대로 변환해 보여준다. 이 차이를 알고 있어야 나중에 시각 대조에서 혼란이 없다.

## 네트워크 대역 설계

세 개의 대역이 등장하고, 이들이 겹치지 않아야 한다.

| 대역 | CIDR | 용도 | 누가 관리 |
|---|---|---|---|
| Node Network | `192.168.8.0/24` | VM(노드)의 실제 IP | VMware / 물리 네트워크 |
| **Pod Network** | `10.244.0.0/16` | Pod에 할당되는 IP | Calico (CNI) |
| Service Network | `10.96.0.0/12` | ClusterIP Service의 가상 IP | kube-apiserver (kubeadm 기본값) |

### Pod CIDR을 10.244.0.0/16으로 지정하는 이유 (중요)

**Calico의 기본 Pod CIDR은 `192.168.0.0/16`이며, 이 값을 그대로 쓰면 안 된다.**

```text
192.168.0.0/16  →  192.168.0.0 ~ 192.168.255.255    Calico 기본 Pod 대역
192.168.8.143   →  이 범위 안에 포함                 master01 노드 IP
192.168.8.142   →  이 범위 안에 포함                 worker01 노드 IP
192.168.8.141   →  이 범위 안에 포함                 worker02 노드 IP
```

**왜 문제인가**: 리눅스는 목적지 IP를 라우팅 테이블에서 조회해 어느 인터페이스로 보낼지 결정한다. Pod 대역과 노드 대역이 겹치면 동일한 목적지에 대해 "물리 NIC로 보낼 경로"와 "Calico 터널로 보낼 경로"가 함께 존재하게 되고, 더 구체적인(prefix가 긴) 경로가 이기는 규칙 때문에 **노드로 가야 할 패킷이 Pod 터널로 들어간다.**

증상이 명확하지 않은 것이 더 문제다. "Pod 간 통신은 되는데 SSH가 끊긴다", "특정 노드만 응답이 없다" 같은 형태로 나타나 원인을 찾기 어렵다.

그래서 겹치지 않는 `10.244.0.0/16`을 **명시적으로** 지정한다.

### 이 값이 나오는 두 곳 — 맞춰서 쓴다

```text
1. kubeadm init --pod-network-cidr=10.244.0.0/16
   → Controller Manager 에 --cluster-cidr / --allocate-node-cidrs=true 로 전달된다
   → 각 노드에 /24 씩 잘라 node.spec.podCIDR 에 기록한다
   → kube-proxy 의 clusterCIDR 에도 같은 값이 들어간다

2. Calico manifest 의 CALICO_IPV4POOL_CIDR 환경변수
   → Calico 가 실제로 Pod 에 IP 를 발급할 때 사용하는 대역
```

> **2026-08-11 수정.** 이 절에는 원래 "반드시 일치해야 한다 / 불일치하면 Pod가 IP를 못 받는다"고 적혀 있었으나 **부정확하다.** 2단계에서 실측한 결과를 반영해 아래와 같이 고친다. 실측 근거는 [02.k8s-objects/00-pod.md](../02.k8s-objects/00-pod.md) 참조.

**Calico는 `node.spec.podCIDR`을 읽지 않는다.** 기본 IPAM인 `calico-ipam`은 IPPool에서 블록(`/26`)을 떼어 노드에 배정하고, 그 안에서 Pod에 주소를 준다. 두 값이 어긋나 있어도 Pod는 정상적으로 IP를 받는다. 실제로 이 클러스터가 그 상태다.

```text
worker01  node.spec.podCIDR      10.244.1.0/24     ← controller-manager 가 적음. 안 쓰임
worker01  Calico blockaffinity   10.244.5.0/26     ← 실제로 쓰이는 것
worker01  Pod IP                 10.244.5.27       ← /26 블록 안. /24 밖
```

Calico 공식 문서도 `--allocate-node-cidrs=false`를 권하며 이 값을 **"unused node CIDRs"** 라고 부른다.

**그렇다면 왜 맞추는가.** 이유는 두 가지이며, 원래 적혀 있던 이유와 다르다.

```text
[이유 1] 대역 자체가 노드 IP 와 겹치면 안 된다      ← 위 절의 진짜 문제
         Calico 기본값 192.168.0.0/16 이 위험한 것은
         podCIDR 과 달라서가 아니라 노드 IP 를 삼키기 때문이다

[이유 2] kube-proxy 가 clusterCIDR 로 "Pod 대역인지 외부인지" 를 판단한다
         바깥쪽 /16 이 서로 다르면 NAT 판단이 어긋난다
         → 맞춰야 하는 것은 바깥쪽 /16 이고,
           안쪽을 /24 로 자르든 /26 으로 자르든 무관하다
```

```text
[맞춰야 하는 것]    바깥쪽 /16
[안 맞아도 되는 것]  안쪽 분할 방식 (podCIDR /24 vs Calico 블록 /26)
```

### 그럼 podCIDR은 왜 만들어지는가

`kubeadm init` 시점에는 어떤 CNI를 설치할지 알 수 없다. CNI는 init 이후에 설치되기 때문이다. 그리고 `podCIDR`을 **실제로 읽는 CNI가 존재한다.**

```text
podCIDR 을 읽는 것    kubenet, Flannel 일부 모드,
                     Cilium 의 Kubernetes Host Scope 모드,
                     Calico 를 host-local + usePodCidr 로 설정한 경우

읽지 않는 것          Calico 기본값(calico-ipam), AWS VPC CNI 등
```

**없는데 필요하면 그 CNI가 아예 동작하지 않고, 있는데 안 쓰면 대체로 무해하다.** 그래서 `--allocate-node-cidrs=true`가 기본값이다.

다만 완전히 무해하지는 않다.

```text
/16 을 /24 로 자르면 256 개뿐이다
→ 노드가 257 대째 들어오면 자를 조각이 없다
→ CIDRNotAvailable 이벤트로 노드 등록이 막힌다
→ Calico 는 그 값을 쓰지도 않는데 노드 추가가 실패한다
```

노드 3대인 이 클러스터에서는 문제가 되지 않으므로 기본값을 유지한다. **"쓰지 않는 값이 고갈되어 장애를 만들 수 있다"** 는 사례로만 기록해 둔다.

### 확인 명령

```bash
# 세 곳의 이름은 다르지만 값은 같아야 한다
sudo grep -E 'cluster-cidr|allocate-node-cidrs' /etc/kubernetes/manifests/kube-controller-manager.yaml
kubectl -n kube-system get cm kube-proxy -o yaml | grep -i clusterCIDR
kubectl get ippools.crd.projectcalico.org -o custom-columns='CIDR:.spec.cidr,BLOCKSIZE:.spec.blockSize'

# 어느 IPAM 을 쓰는가 — calico-ipam 이면 podCIDR 을 안 읽는다
sudo cat /etc/cni/net.d/10-calico.conflist | grep -A4 '"ipam"'

# 두 분할을 나란히 놓고 본다
kubectl get nodes -o custom-columns='NODE:.metadata.name,PODCIDR:.spec.podCIDR'
kubectl get blockaffinities.crd.projectcalico.org -o custom-columns='NODE:.spec.node,BLOCK:.spec.cidr,STATE:.spec.state'
```

### Service Network는 왜 라우팅 테이블에 없는가

`10.96.0.0/12`는 **실제로 어떤 인터페이스에도 붙지 않는 가상 대역**이다. ClusterIP로 향하는 패킷은 kube-proxy가 만든 iptables 규칙(또는 IPVS 규칙)에 의해 실제 Pod IP로 **목적지 주소가 변환(DNAT)**된다.

그래서 `ping <ClusterIP>`는 실패하는 것이 정상이다. ICMP에 대한 iptables 규칙이 없기 때문이다. Service 검증은 반드시 **실제 포트로 TCP 연결**해서 확인해야 한다. 이 사실을 모르면 "Service가 죽었다"고 오진하게 된다.

## 필요 포트

Ubuntu Server는 ufw가 기본 inactive이지만, active인 경우 아래 포트가 필요하다.

### master01 (control-plane)

| 포트 | 용도 | 접근 주체 |
|---|---|---|
| 6443/tcp | kube-apiserver | 전체 노드, kubectl 클라이언트 |
| 2379-2380/tcp | etcd (클라이언트 / peer) | kube-apiserver (자기 노드) |
| 10250/tcp | kubelet API | Control Plane |
| 10257/tcp | kube-controller-manager | 자기 노드 |
| 10259/tcp | kube-scheduler | 자기 노드 |

### worker01 / worker02

| 포트 | 용도 |
|---|---|
| 10250/tcp | kubelet API |
| 30000-32767/tcp | NodePort Service 범위 |

### 공통 (Calico)

| 포트 | 용도 |
|---|---|
| 179/tcp | BGP (BGP 모드 사용 시) |
| 4789/udp | VXLAN (VXLAN 모드 사용 시) |

> Calico는 BGP와 VXLAN 중 하나로 동작한다. Phase 6에서 어느 모드로 설정되었는지 확인해 이 표를 정리한다.

## Phase 0 확인 명령

3대 모두에서 실행하고 결과를 아래에 기록한다.

```bash
hostnamectl                # hostname — kubelet이 Node 이름으로 사용
lsb_release -a             # Ubuntu 버전
uname -r                   # 커널 버전
nproc                      # vCPU 수 (master01은 2 이상 필수)
free -h                    # RAM
df -h /                    # 루트 파티션 여유 공간
ip -4 addr show            # IP 확인
timedatectl                # 시간 동기화 상태 (etcd가 clock skew에 민감)
sudo ufw status            # 방화벽 상태
swapon --show              # 현재 swap 상태 (Phase 2 이전이므로 켜져 있는 것이 정상)
```

노드 간 통신 확인:

```bash
ping -c 2 192.168.8.143    # master01
ping -c 2 192.168.8.142    # worker01
ping -c 2 192.168.8.141    # worker02
```

### 실행 결과 기록

<!-- Phase 0 실행 후 각 노드의 출력을 아래에 붙여 넣는다 -->

#### master01 (2026-07-30)

```text
$ hostnamectl
 Static hostname: master01
       Icon name: computer-vm
         Chassis: vm
      Machine ID: 2d1e6339f3e741bc852ef6dea43b9894
  Virtualization: vmware
Operating System: Ubuntu 24.04.3 LTS
          Kernel: Linux 6.8.0-71-generic
    Architecture: x86-64
 Hardware Vendor: VMware, Inc.
  Hardware Model: VMware Virtual Platform

$ lsb_release -a
Distributor ID: Ubuntu
Description:    Ubuntu 24.04.3 LTS
Release:        24.04
Codename:       noble

$ uname -r
6.8.0-71-generic

$ nproc
2                          # kubeadm 최소 요구(2) 충족

$ free -h
               total        used        free      shared  buff/cache   available
Mem:           3.8Gi       470Mi       3.3Gi       1.5Mi       231Mi       3.3Gi
Swap:          3.8Gi          0B       3.8Gi       # Phase 2에서 비활성화 대상

$ df -h /
Filesystem                         Size  Used Avail Use% Mounted on
/dev/mapper/ubuntu--vg-ubuntu--lv   24G  6.3G   16G  29% /

$ ip -4 addr show
2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    altname enp2s1
    inet 192.168.8.143/24 metric 100 brd 192.168.8.255 scope global dynamic ens33
       valid_lft 1078sec preferred_lft 1078sec
       # ^^^ DHCP 임대. Phase 5 전에 고정 IP 전환 필요

$ timedatectl
                Time zone: Etc/UTC (UTC, +0000)
System clock synchronized: yes
              NTP service: active

$ sudo ufw status
Status: inactive             # 포트 개방 작업 불필요

$ swapon --show
NAME      TYPE SIZE USED PRIO
/swap.img file 3.8G   0B   -2   # 파일 방식 → /etc/fstab의 /swap.img 줄 처리
```

**판정**

| 항목 | 결과 |
|---|---|
| OS / 커널 | 정상 (Ubuntu 24.04.3, 커널 6.8) |
| vCPU 2개 | 정상 — preflight 통과 가능 |
| RAM | 정상 (3.8Gi) |
| 시간 동기화 | 정상 |
| 방화벽 | inactive — 조치 불필요 |
| **IP 방식** | **DHCP — 고정 IP 전환 필요 (블로커)** |
| 시간대 | UTC — 결정 필요 |
| product_uuid | 미확인 — 3대 비교 필요 |

#### worker01 (2026-07-30)

```text
$ hostnamectl
 Static hostname: worker01                    # 오타 없음
      Machine ID: 45ea217cb4fc42f08216ad0bd79a9d14
Operating System: Ubuntu 24.04.3 LTS
          Kernel: Linux 6.8.0-71-generic

$ nproc
2

$ free -h / df -h /
Mem: 3.8Gi   /  루트 24G (여유 16G)

$ ip -4 addr show
2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.142/24 brd 192.168.8.255 scope global ens33
       valid_lft forever preferred_lft forever
       # ^^^ 고정 IP 적용 완료 (dynamic 없음, metric 없음, forever)

$ timedatectl
Time zone: Etc/UTC (UTC, +0000)               # 아직 UTC — 변경 필요

$ swapon --show
/swap.img file 3.8G   0B   -2

$ sudo cat /sys/class/dmi/id/product_uuid
06e24d56-487e-4f27-88d6-c26590244cd2
```

#### worker02 (2026-07-30)

```text
$ hostnamectl
 Static hostname: worker02                    # 오타 수정 완료
      Machine ID: 3c1083b407504c4c8c1a575622e1be32
Operating System: Ubuntu 24.04.3 LTS
          Kernel: Linux 6.8.0-136-generic     # master01/worker01(71)과 다름

$ nproc
2

$ free -h / df -h /
Mem: 3.8Gi   /  루트 24G (여유 16G)

$ ip -4 addr show
2: ens33: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500
    inet 192.168.8.141/24 metric 100 brd 192.168.8.255 scope global dynamic ens33
       valid_lft 1679sec preferred_lft 1679sec
       # ^^^ netplan 파일은 수정됐으나 아직 apply 안 됨

$ timedatectl
Time zone: Asia/Seoul (KST, +0900)            # 변경 완료

$ swapon --show
/swap.img file 3.8G   0B   -2

$ sudo cat /sys/class/dmi/id/product_uuid
8a1a4d56-b34e-74f1-9154-6971d530a687
```

### 고정 IP 적용 여부 판별법

세 노드의 `ip -4 addr show` 출력 차이가 그대로 판별 기준이다.

```text
DHCP 상태
  inet 192.168.8.143/24 metric 100 brd ... scope global dynamic ens33
                        ^^^^^^^^^^                        ^^^^^^^
     valid_lft 1669sec preferred_lft 1669sec
     ^^^^^^^^^^^^^^^^

고정 IP 적용됨
  inet 192.168.8.142/24 brd ... scope global ens33
     valid_lft forever preferred_lft forever
               ^^^^^^^
```

판별 기준 3개: **`dynamic` 없음 / `metric 100` 없음 / `valid_lft forever`.** `metric 100`은 DHCP 클라이언트가 붙이는 값이므로 고정 IP에서는 사라진다.

### Phase 0 완료 (2026-07-30) — 재부팅 후 최종 검증

| 항목 | master01 | worker01 | worker02 |
|---|---|---|---|
| hostname | master01 | worker01 | worker02 (오타 수정) |
| nproc | 2 | 2 | 2 |
| product_uuid 고유 | 정상 | 정상 | 정상 |
| **고정 IP (재부팅 후)** | `.143` forever | `.142` forever | `.141` forever |
| **커널** | 6.8.0-136 | 6.8.0-136 | 6.8.0-136 |
| **OS** | 24.04.4 | 24.04.4 | 24.04.4 |
| 시간대 | Asia/Seoul | Asia/Seoul | Asia/Seoul |
| 시계 동기화 | yes | yes | **no (확인 필요)** |

`apt upgrade` 결과 커널이 셋 다 `6.8.0-136`으로, OS도 `24.04.3` → `24.04.4`로 정렬되었다. 노드 간 조건 차이가 제거되어 6단계 장애 실험의 결과 해석이 명확해진다.

### 재부팅 검증이 왜 결정적이었는가

master01의 `Boot ID`가 `2eb949ef…` → `8b1ca6e3…`로 변경되었다. 즉 실제로 재부팅이 일어났고, **그 이후에도 `valid_lft forever`가 유지되었다.**

```text
재부팅 전   inet 192.168.8.143/24 brd ... scope global ens33
               valid_lft forever preferred_lft forever
재부팅 후   inet 192.168.8.143/24 brd ... scope global ens33
               valid_lft forever preferred_lft forever    ← 유지됨
```

이것이 cloud-init 네트워크 관리 비활성화가 정상 동작한다는 증거다. 만약 `99-disable-network-config.cfg`가 없었다면 부팅 시 cloud-init이 `50-cloud-init.yaml`을 `dhcp4: true`로 재생성해 `dynamic`이 다시 나타났을 것이다.

**교훈**: 설정 변경 작업의 완료 조건은 "지금 동작함"이 아니라 "재시작을 견딤"이다. 특히 클라우드 이미지 기반 OS는 부팅 시 설정을 재생성하는 계층(cloud-init)이 별도로 존재한다.

### worker02 시계 동기화 — 해결됨

최초 확인 시 worker02만 `System clock synchronized: no`였다. 재부팅 직후 `systemd-timesyncd`가 첫 동기화를 완료하지 못한 일시적 상태로 추정했고, **2026-08-03 재확인 결과 3대 모두 `yes`로 확인되어 종료되었다.**

**왜 확인해야 했는가**: Phase 7의 `kubeadm join`에서 worker가 apiserver의 TLS 인증서 유효기간을 검증한다. 시계가 크게 틀어지면 `certificate has expired or is not yet valid`로 join이 실패한다. 또한 etcd는 clock skew에 민감하다.

**교훈**: 재부팅 직후의 상태 값은 "아직 수렴하지 않은 값"일 수 있다. `systemd-timesyncd`, `systemd-networkd` 같은 서비스는 부팅 후 수십 초~수 분에 걸쳐 상태가 확정된다. 부팅 직후 한 번 본 값으로 장애를 판단하지 않는다.

### NetworkManager — Calico 요구사항 확인

```text
$ systemctl is-active NetworkManager
inactive          # 3대 모두
```

Calico 공식 요구사항에 **NetworkManager가 있으면 비활성화하라**고 명시되어 있다. NetworkManager가 Calico가 생성하는 `cali*` 인터페이스를 자기 관리 대상으로 인식해 조작하면 Pod 네트워킹이 깨지기 때문이다.

Ubuntu Server는 `systemd-networkd`를 사용하므로 `inactive`가 정상이다. 조치 불필요.

### Phase 0 최종 확인 (2026-08-03)

```text
$ uptime -p
up 3 days, 17 hours

$ getent hosts $(hostname)
192.168.8.143   master01        # worker01=.142, worker02=.141

$ timedatectl | grep synchronized
System clock synchronized: yes  # 3대 모두

$ systemctl is-active NetworkManager
inactive                        # 3대 모두
```

**Phase 0 종료.** 모든 항목이 재부팅과 3.7일 연속 가동을 견뎠다.

## VMware 스냅샷 기록

되돌릴 수 없는 실수는 OS 재설치가 아니라 스냅샷 복원으로 복구한다. Phase 완료 시마다 3대 모두 스냅샷을 남긴다.

| 스냅샷 이름 | 시점 | 생성 여부 |
|---|---|---|
| `00-clean-os` | OS 설치 직후, 아무 설정 전 | 대기 |
| `01-os-prereq-done` | Phase 2 완료 (swap/sysctl/모듈) | 대기 |
| `02-runtime-done` | Phase 3~4 완료 (containerd + kube 패키지) | 대기 |
| `03-cluster-ready` | Phase 7 완료 (3노드 Ready) | 대기 |

`03-cluster-ready`가 가장 중요하다. Phase 8의 Control Plane 장애 실험에서 복구가 실패하면 이 지점으로 롤백한다.

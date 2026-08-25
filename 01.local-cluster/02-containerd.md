# 02. containerd 설치 및 구성 (3대 전부)

컨테이너 런타임(container runtime)을 설치한다. 대상은 master01, worker01, worker02 **전부**다.

## 왜 Kubernetes보다 컨테이너 런타임이 먼저인가

**kubelet은 컨테이너를 직접 실행하지 못한다.** kubelet이 하는 일은 "PodSpec을 읽고, 컨테이너 런타임에게 이렇게 만들어달라고 요청"하는 것이다. 실제로 프로세스를 격리하고, 이미지를 내려받고, 네임스페이스와 cgroup을 만드는 것은 컨테이너 런타임의 몫이다.

```text
kube-apiserver
  ↓ (PodSpec 전달)
kubelet
  ↓ [CRI 규약 — gRPC over Unix socket]
containerd
  ↓ [OCI 규약]
runc
  ↓
실제 컨테이너 프로세스 (namespace + cgroup으로 격리)
```

각 계층의 책임은 다음과 같다.

| 구성요소 | 역할 |
|---|---|
| kubelet | 무엇을 실행할지 결정하고 요청. 상태를 apiserver에 보고 |
| **CRI** | kubelet ↔ 런타임 사이의 **규약**(인터페이스). 특정 런타임에 종속되지 않게 함 |
| containerd | 이미지 관리, 스냅샷, 컨테이너 수명주기 관리 |
| **OCI** | containerd ↔ 실행기 사이의 **규약** |
| runc | 실제로 namespace/cgroup을 만들어 프로세스를 격리 실행 |

CRI(Container Runtime Interface)가 존재하는 이유는 **kubelet을 특정 런타임에 묶지 않기 위해서**다. CRI만 구현하면 containerd든 CRI-O든 kubelet 입장에서는 동일하다. 과거 Docker를 쓰려면 `dockershim`이라는 어댑터가 kubelet 안에 있었는데, Kubernetes 1.24에서 제거되었다. 지금 containerd를 직접 쓰는 이유가 이것이다.

---

## 함정 1: Ubuntu 패키지는 CRI 플러그인을 꺼둔다

Ubuntu 저장소의 containerd는 기본 설정 파일에 이런 줄이 들어 있다.

```toml
disabled_plugins = ["cri"]
```

**이 상태로 두면 `kubeadm init`이 실패한다.** kubelet이 containerd에 연결은 되지만 CRI 서비스가 응답하지 않아, 컨테이너를 만들어달라는 요청을 보낼 통로가 없다.

**왜 Ubuntu는 꺼두는가**: containerd를 Docker의 백엔드로만 쓰는 경우가 많고, 그때는 CRI가 필요 없다. CRI는 Kubernetes 전용 인터페이스이기 때문에 기본값으로는 비활성화해둔 것이다.

해결은 기본 설정을 **재생성**하는 것이다. 부분 수정이 아니라 통째로 다시 만든다.

```bash
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null
```

`containerd config default`는 **현재 설치된 containerd 버전에 맞는 완전한 기본 설정**을 출력한다. 여기에는 `disabled_plugins`가 비어 있다.

---

## 함정 2: cgroup 드라이버 불일치

cgroup(control group)은 리눅스 커널이 프로세스 그룹의 CPU·메모리 사용량을 제한하고 측정하는 기능이다. Kubernetes의 Resource Request/Limit이 실제로 강제되는 지점이 바로 여기다.

문제는 **cgroup을 조작하는 방식이 두 가지**라는 것이다.

| 드라이버 | 동작 |
|---|---|
| `systemd` | systemd에게 cgroup 생성을 요청. systemd가 단독 관리자 |
| `cgroupfs` | `/sys/fs/cgroup`을 직접 조작 |

**Ubuntu를 포함한 대부분의 현대 리눅스는 systemd가 부팅 시부터 cgroup 트리를 관리한다.** 여기서 containerd가 `cgroupfs`로 직접 조작하면 **관리 주체가 둘이 된다.**

```text
systemd    "이 cgroup은 내가 관리한다"
containerd "이 cgroup은 내가 직접 만들었다"
   ↓
같은 자원에 대해 서로 다른 두 관리자가 존재
   ↓
- 메모리 Limit이 의도대로 적용되지 않음
- 자원 사용량 측정값이 부정확 (Metrics가 틀림)
- systemd가 재시작하며 cgroup을 정리할 때 컨테이너가 예고 없이 죽음
```

증상이 **간헐적이고 재현이 어렵다**는 것이 최악이다. "Pod가 가끔 죽는데 로그에 아무것도 없다" 같은 형태로 나타난다.

**kubelet은 systemd 드라이버를 기본으로 쓴다.** 따라서 containerd도 systemd로 맞춘다.

```bash
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
```

> **버전에 따라 설정 경로가 다르다.** containerd 1.x는 `[plugins."io.containerd.grpc.v1.cri"...]`, 2.x는 config version 3을 쓰며 플러그인 경로가 변경되었다. 다만 **키 이름 `SystemdCgroup`은 양쪽 동일**하므로, 위 `sed`는 경로를 몰라도 동작한다. 적용 후 반드시 `grep`으로 검증한다.

---

## 설치 절차

### 1. 설치 가능한 버전 확인

```bash
apt-cache policy containerd
```

Ubuntu 24.04 저장소의 containerd 버전을 확인한다. Docker 공식 저장소(`containerd.io` 패키지)를 쓰면 더 최신 버전을 받을 수 있지만, **이 프로젝트는 Ubuntu 저장소를 사용한다.** 저장소를 추가로 등록하는 단계가 줄어 실패 지점이 적고, 학습 목표(CRI와 cgroup 이해)에는 버전 차이가 영향을 주지 않기 때문이다.

### 2. 설치

```bash
sudo apt update
sudo apt install -y containerd
containerd --version
```

`runc`는 containerd 패키지의 의존성으로 함께 설치된다.

### 3. 설정 재생성 (CRI 활성화)

```bash
sudo mkdir -p /etc/containerd
containerd config default | sudo tee /etc/containerd/config.toml > /dev/null
```

### 4. cgroup 드라이버를 systemd로 변경

```bash
sudo sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
```

### 5. 적용

```bash
sudo systemctl restart containerd
sudo systemctl enable containerd
```

`enable`은 부팅 시 자동 시작을 설정한다. **Phase 0·2에서 반복해서 겪은 "재시작을 견디는가" 문제와 같은 맥락이다.**

---

## 검증

### 5-1. 설정이 의도대로 들어갔는지

```bash
grep -n 'SystemdCgroup' /etc/containerd/config.toml     # = true 여야 함
grep -n 'disabled_plugins' /etc/containerd/config.toml  # 빈 배열이거나 cri가 없어야 함
```

### 5-2. containerd 데몬 상태

```bash
systemctl status containerd --no-pager
```

`Active: active (running)`이어야 한다.

### 5-3. CRI 플러그인이 실제로 살아 있는지 ★ 핵심

```bash
sudo ctr plugins ls | grep -i cri
```

`ctr`은 containerd에 함께 설치되는 저수준 CLI다. 이 명령은 로드된 플러그인과 각각의 상태를 보여준다.

**`STATUS`가 `ok`여야 한다.** `error`나 `skip`이면 CRI가 동작하지 않는 것이고, 이 상태로 Phase 5에 가면 `kubeadm init`이 실패한다.

> **왜 `crictl`을 쓰지 않는가**: `crictl`은 CRI를 검사하는 표준 도구지만 `cri-tools` 패키지에 들어 있고, 이는 Phase 4에서 `kubeadm`과 함께 설치된다. 지금은 아직 없으므로 `ctr`로 확인한다. Phase 4 이후에는 `crictl version`, `crictl info`로 더 자세히 볼 수 있다.

### 5-4. CRI 소켓 파일 존재

```bash
ls -la /run/containerd/containerd.sock
```

kubelet이 이 Unix 소켓을 통해 containerd와 통신한다. Phase 5에서 kubeadm이 자동 감지하는 대상이며, 런타임이 여러 개면 `--cri-socket`으로 명시해야 한다.

### 5-5. 실제로 컨테이너가 뜨는지 (선택)

```bash
sudo ctr image pull docker.io/library/hello-world:latest
sudo ctr run --rm docker.io/library/hello-world:latest test
```

Kubernetes 없이 containerd만으로 컨테이너를 실행해 보는 것이다. **이 시점에 이미 컨테이너는 동작한다**는 사실을 확인하면, Kubernetes가 추가하는 것이 무엇인지(스케줄링, 자가 치유, 서비스 디스커버리, 선언적 상태 관리) 명확해진다.

정리:

```bash
sudo ctr image rm docker.io/library/hello-world:latest
```

---

## 재부팅 검증

```bash
sudo reboot
```

재부팅 후 3대에서:

```bash
systemctl is-enabled containerd     # enabled
systemctl is-active containerd      # active
sudo ctr plugins ls | grep -i cri   # STATUS ok
grep SystemdCgroup /etc/containerd/config.toml
```

---

## 실행 결과 기록 (2026-08-03)

3대 모두 동일한 결과가 나왔다.

```text
$ grep -n 'SystemdCgroup' /etc/containerd/config.toml
109:            SystemdCgroup = true

$ grep -n 'disabled_plugins' /etc/containerd/config.toml
5:disabled_plugins = []                      # 기본값 ["cri"]에서 변경됨

$ sudo ctr plugins ls | grep -i cri
io.containerd.cri.v1     images    -             ok
io.containerd.cri.v1     runtime   linux/amd64   ok
io.containerd.grpc.v1    cri       -             ok

$ ls -la /run/containerd/containerd.sock
srw-rw---- 1 root root 0 Aug  3 09:40 /run/containerd/containerd.sock
```

### 설치된 버전

```text
$ containerd --version
containerd github.com/containerd/containerd/v2 2.2.1

$ systemctl is-enabled containerd
enabled
```

**containerd 2.2.1** — Ubuntu 24.04 저장소에서 설치되었다. CRI v1을 구현하므로 Kubernetes 1.35와 호환된다. (Kubernetes는 1.26에서 CRI v1alpha2 지원을 제거했고 현재는 CRI v1만 사용한다)

플러그인 ID만 보고도 2.x임을 먼저 판별할 수 있었다.

```text
io.containerd.cri.v1   images     ← 2.0에서 추가됨
io.containerd.cri.v1   runtime    ← 2.0에서 추가됨
io.containerd.grpc.v1  cri        ← 1.x부터 존재
```

containerd **2.0에서 CRI 플러그인이 image 서비스와 runtime 서비스로 분리**되었다. 1.x에서는 `io.containerd.grpc.v1.cri` 하나만 존재한다.

**이 사실이 실제로 중요했던 이유**: 설정 파일에서 `SystemdCgroup`이 위치하는 섹션 경로가 1.x와 2.x에서 다르다.

```text
1.x:  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
2.x:  config version 3 — 플러그인 경로가 변경됨
```

만약 1.x를 가정하고 경로를 지정해 `sed`를 실행했다면, 해당 섹션이 없어 **아무것도 치환되지 않은 채 명령은 성공(exit 0)** 했을 것이다. 결과적으로 cgroup 드라이버가 `cgroupfs`로 남아 Phase 5 이후 간헐적 장애로 이어진다.

**키 이름 `SystemdCgroup`만으로 치환**하고 적용 후 `grep`으로 검증하는 방식이 버전에 무관하게 안전하다. 명령의 성공/실패가 아니라 **결과 상태를 확인**하는 것이 원칙이다.

### 재부팅 검증을 생략한 이유

Phase 2에서는 실제 재부팅으로 검증했지만, 여기서는 `systemctl is-enabled containerd`로 대체한다.

| 대상 | 실패 방식 | 필요한 검증 |
|---|---|---|
| `/etc/fstab`, `/etc/modules-load.d` | 문법 오류나 로드 순서 문제로 **조용히 실패** | 실제 재부팅 |
| systemd 서비스 enable | 심볼릭 링크 유무로 결정. 중간 상태 없음 | `is-enabled` 확인으로 충분 |

검증 방법은 **그 설정이 어떻게 실패할 수 있는지**에 맞춰 정한다.

---

## 반복 작업 기록

| 항목 | 내용 |
|---|---|
| 3대 반복 시 불편했던 점 | (작성) |
| 실수하거나 빠뜨린 단계 | (작성) |
| 자동화 후보 | (작성) |

---


---

## 심화 — 컨테이너 런타임 계층과 CRI / OCI / CNI

> containerd를 설치하며 정리한 내용. "kubelet은 어떻게 컨테이너를 띄우는가"를 계층별로 본다.

### 1. 왜 CRI가 생겼는가

초기 Kubernetes(2014~2016)는 **kubelet 코드 안에 Docker 호출이 직접 박혀 있었다.**

```text
kubelet 소스코드
  if (컨테이너 만들어야 함) {
      docker.CreateContainer(...)     ← Docker 함수를 직접 호출
  }
```

그러다 CoreOS가 만든 `rkt`라는 다른 런타임을 쓰고 싶다는 요구가 나왔고, kubelet에 rkt 코드도 추가했다.

```text
kubelet 소스코드
  if (런타임 == docker)  { docker.CreateContainer(...) }
  if (런타임 == rkt)     { rkt.CreateContainer(...) }
  if (런타임 == ???)     { ... }        ← 런타임이 늘 때마다 kubelet을 고쳐야 함
```

문제가 명확하다. 새 런타임이 나올 때마다 **Kubernetes 본체 코드**를 수정하고 테스트하고 릴리스해야 한다. 런타임 개발사는 Kubernetes 팀에 코드 머지를 요청해야 한다.

### 2. CRI는 "규약"이다 — 프로그램이 아니다

2016년 Kubernetes 1.5에서 CRI를 도입해 이 구조를 뒤집었다.

```text
[이전]  kubelet이 각 런타임을 어떻게 부를지 알아야 함
[이후]  kubelet은 CRI라는 약속된 형식으로만 요청을 보냄
        런타임 쪽에서 그 형식을 알아듣도록 구현
```

```text
        kubelet
           │
           │  "CreateContainer 요청"  ← CRI가 정한 형식
           ▼
    ┌──────┴───────┐
    │   CRI 규약    │   ← 문서/명세일 뿐, 실행되는 프로그램이 아님
    └──────┬───────┘
           │
    ┌──────┴──────┬──────────┐
    ▼             ▼          ▼
containerd     CRI-O      다른 런타임
```

**kubelet은 이제 상대가 containerd인지 CRI-O인지 모른다.** CRI 형식으로 요청을 던지고, 그걸 알아듣는 누군가가 응답하면 된다. 런타임 개발사는 Kubernetes 코드를 건드릴 필요 없이 자기 쪽에서 CRI를 구현하면 된다.

콘센트에 비유하면, kubelet은 "220V 규격 플러그"를 꽂을 뿐이고 발전소가 화력인지 원자력인지 알 필요가 없다.

---

### 3. "런타임"이라는 단어부터

#### 뜻 1 — 시간대로서의 런타임 (run + time)

프로그램의 인생을 두 시기로 나눈 것이다.

- **컴파일 타임**: 코드를 기계어로 번역하는 시점. 오타나 문법 오류는 여기서 걸린다.
- **런타임**: 프로그램이 실제로 켜져서 돌아가는 시점. "런타임 에러"는 실행 중에 터진 에러다. 0으로 나누기 같은 것은 번역할 때는 모르다가 실제로 돌려보니 터지는 것이다.

#### 뜻 2 — 물건으로서의 런타임 (실행시켜 주는 프로그램)

프로그램을 실제로 돌려주는 소프트웨어 자체를 가리킨다. 자바 코드는 JVM이 있어야 돌고, JS 코드는 Node.js가 있어야 돈다. 이때 JVM이나 Node.js를 "자바 런타임", "자바스크립트 런타임"이라고 부른다.

**컨테이너 런타임은 뜻 2다.** → 컨테이너를 실제로 만들고 돌려주는 프로그램.

---

### 4. 고수준 런타임과 저수준 런타임

컨테이너를 띄우는 일은 두 종류의 작업으로 나뉜다.

```text
(1) 준비 작업   이미지를 다운받고, 디스크에 저장하고, 압축 풀고, 목록 관리
(2) 진짜 실행   리눅스에게 "이 프로세스를 격리된 상태로 띄워라"라고 명령
```

이 둘을 한 프로그램이 다 하면 덩치가 커지고 갈아끼우기 어렵다. 그래서 층을 나눴다.

| 계층 | 담당 | 예시 | 비유 |
|---|---|---|---|
| **고수준 런타임** | (1) 준비 작업 + 오케스트레이션 | containerd, CRI-O | 식당 매니저 — 주문 받고 재료 관리 |
| **저수준 런타임** | (2) 실제 격리 실행 | runc, gVisor, Kata | 주방장 — 실제로 요리 |

> **고수준 런타임은 단순 전달자가 아니다.** containerd는 명령을 받아 넘기기만 하는 것이 아니라 이미지 다운로드, 저장, 스냅샷 관리, **CNI 호출을 통한 Pod IP 할당**까지 수행한다. 중개인이 아니라 실무자다.

---

### 5. 두 개의 규격이 층 사이를 잇는다

| 연결 | 규격 이름 |
|---|---|
| kubelet ↔ containerd | **CRI** |
| containerd ↔ runc | **OCI** |

CRI와 OCI 모두 **프로그램이 아니라 약속(규격서)** 이다. "이런 명령어를 이런 형식으로 받아들여라"라고 문서로 정해놓은 것이다.

혼동하기 쉬운 지점이라 짚어둔다. **CRI는 위쪽 화살표만 담당한다.** 아래쪽은 OCI 관할이다.

```text
kubelet
  │  CRI
containerd
  │  OCI
runc
  │  (syscall)
리눅스 커널
```

---

### 6. 저수준 런타임 3종 — 격리를 만드는 방식이 다르다

벽의 두께가 다르다고 보면 된다.

| 런타임 | 격리 방식 | 특징 |
|---|---|---|
| **runc** | 리눅스 커널 기능(namespace·cgroup)으로 칸막이 | 커널을 호스트와 공유. 빠르지만 커널 취약점이 뚫리면 위험 |
| **gVisor** | 유저 공간에 커널 역할을 하는 계층(Sentry)을 세움 | 컨테이너가 진짜 커널을 직접 만지지 못함. 느림 |
| **Kata** | 아예 경량 가상머신을 띄움 | 게스트 커널이 별도로 존재. 가장 안전하고 가장 무거움 |

**호스트의 진짜 커널을 직접 쓰는 것은 runc뿐이다.** 나머지 둘은 오히려 "진짜 커널을 못 만지게 하려고" 만들어졌다.

```text
runc     호스트 커널에게 직접 "이 프로세스 격리해줘" 요청
         → 컨테이너와 호스트가 같은 커널 사용

gVisor   컨테이너의 시스템 콜을 중간에서 가로채 유저 공간에서 처리
         → 진짜 커널까지 요청이 거의 도달하지 않음

Kata     컨테이너용 게스트 커널을 따로 부팅해서 그 안에 넣음
         → 커널이 논리적으로 2개
```

OCI 규격만 지키면 누구나 만들 수 있어서 실제로는 더 많다. `crun`(C로 만든 가벼운 runc 대체재), `youki`(Rust 버전), Firecracker 기반 구현 등이 있다.

**현실적으로는 99% runc를 쓴다.** gVisor·Kata는 남의 코드를 돌려야 하는 특수 상황(클라우드 업체가 고객 코드 실행, 멀티테넌트 환경)에서 등장한다. 지금 단계에서는 "runc가 기본이고, 보안이 더 필요하면 갈아끼울 선택지가 있다" 정도로 충분하다.

> runc·gVisor·Kata는 방법론이 아니라 **실제로 설치되는 프로그램 파일**이다. `which runc`를 실행하면 경로가 나온다. 격리 방식이라는 철학이 다른 게 아니라, 그 철학을 코드로 구현해 실행 파일로 만들어놓은 제품 3개다.

---

### 7. CRI는 구체적으로 어떻게 생겼는가

CRI는 **gRPC API**이고 **Unix 소켓**으로 통신한다. 그 소켓이 Phase 3에서 확인했던 파일이다.

```text
/run/containerd/containerd.sock
```

CRI는 **두 개의 서비스**로 나뉜다.

| 서비스 | 담당 | 주요 호출 |
|---|---|---|
| **RuntimeService** | 컨테이너·Pod의 수명주기 | `RunPodSandbox`, `CreateContainer`, `StartContainer`, `StopContainer`, `ListContainers`, `ExecSync` |
| **ImageService** | 이미지 관리 | `PullImage`, `ListImages`, `RemoveImage`, `ImageStatus` |

**Phase 3에서 본 출력이 정확히 이 구조와 대응한다.**

```text
io.containerd.cri.v1     images    -             ok      ← ImageService
io.containerd.cri.v1     runtime   linux/amd64   ok      ← RuntimeService
io.containerd.grpc.v1    cri       -             ok      ← gRPC 서버 자체
```

containerd 2.0에서 CRI 플러그인을 둘로 쪼갠 것이 CRI의 두 서비스 구조를 그대로 반영한 것이다.

---

### 8. Pod Sandbox — CRI에만 있는 개념

Kubernetes의 Pod는 **여러 컨테이너가 네트워크와 IPC 네임스페이스를 공유**하는 단위다. 같은 Pod 안의 컨테이너들이 `localhost`로 통신할 수 있는 이유다. 이걸 구현하려면 누군가 그 네임스페이스를 **붙들고 있어야** 한다.

```text
1. RunPodSandbox     울타리를 먼저 세운다
                       - runc가 pause 컨테이너 프로세스를 실행 → 네트워크 네임스페이스 생성
                       - containerd가 CNI 플러그인을 호출 → 그 네임스페이스에 veth와 IP를 설정
2. CreateContainer   그 울타리 안에 앱 컨테이너를 생성
3. StartContainer    실행
```

`crictl ps -a`를 실행하면 **`pause`라는 이름의 컨테이너**가 Pod마다 하나씩 보인다. 아무 일도 하지 않고 잠들어 있는 컨테이너인데, **네임스페이스를 유지하는 것이 그 역할**이다. 앱 컨테이너가 죽었다 살아나도 pause가 살아 있으면 **Pod IP가 유지**된다.

#### 왜 "sandbox"라는 추상적인 이름을 쓰는가

같은 `RunPodSandbox` 명령이라도 저수준 런타임에 따라 **물리적으로 전혀 다른 물건**이 만들어진다.

```text
runc   →  pause 컨테이너 생성
Kata   →  경량 VM 부팅
```

명령은 같은데 결과물의 정체가 다르다. 이 차이를 상위 계층이 몰라도 되게 감추려고 `sandbox`라는 중립적인 이름을 쓴 것이다.

---

### 9. 전체 흐름 정리

```text
kubelet이 CRI 규격으로 containerd에게 Pod 생성을 요청한다.
containerd는 이미지를 준비하고, OCI 규격으로 runc에게 실행을 맡긴다.
runc가 샌드박스(울타리)를 먼저 만들고,
containerd가 CNI를 호출해 그 안에 네트워크와 IP를 붙인다.
이어서 앱 컨테이너들이 같은 샌드박스 안에 생성된다.
kubelet에게는 이 결과물이 Pod 하나로 보인다.
```

#### 흔히 헷갈리는 세 지점

**① kubelet은 커널에게 말을 걸지 않는다**

kubelet은 커널이라는 존재를 신경 쓰지 않는다. containerd에게 "이런 Pod 만들어줘"라고 요청하고 끝이다. 커널 언어로 번역하는 것은 체인 맨 끝 runc의 일이고, gVisor·Kata는 그마저도 하지 않는다(자기가 커널 역할을 대신하므로).

**② 노드 입장에서 "Pod"라는 물건은 존재하지 않는다**

Pod는 Kubernetes 세계의 개념이다. 노드에 실제로 만들어지는 것은 **샌드박스 + 컨테이너들**이다. 샌드박스가 곧 Pod의 육체인 셈이다.

**③ 컨테이너를 순서대로 넣는 오케스트레이션은 containerd의 일이다**

runc는 "이 프로세스를 이렇게 격리해 띄워라"라는 단발 명령만 수행한다. 샌드박스를 먼저 만들고 그 다음 앱 컨테이너를 그 네임스페이스에 붙이는 **순서 관리**는 상위 계층인 containerd가 담당한다.

---

### 10. crictl

**CRI 소켓에 직접 요청을 보내는 CLI 도구**다. `cri-tools` 프로젝트에 들어 있고 Kubernetes SIG-Node가 관리한다.

쉽게 말해 **kubelet 흉내를 내는 도구**다. kubelet이 하는 것과 똑같이 CRI 호출을 보내고 응답을 보여준다.

```bash
sudo crictl ps          # 실행 중인 컨테이너
sudo crictl ps -a       # 종료된 것 포함
sudo crictl pods        # Pod Sandbox 목록
sudo crictl images      # 이미지 목록
sudo crictl logs <id>   # 컨테이너 로그
sudo crictl inspect <id>
```

#### 도구 4개 비교

| 도구 | 말을 거는 대상 | 보이는 범위 | 쓰는 때 |
|---|---|---|---|
| **kubectl** | kube-apiserver (HTTPS) | **클러스터 전체** | 평상시 거의 모든 작업 |
| **crictl** | containerd의 **CRI** (소켓) | **이 노드 1대** | apiserver가 죽었을 때, kubelet 디버깅 |
| **ctr** | containerd **네이티브 API** | 이 노드 1대, 더 저수준 | containerd 자체 문제 진단 |
| **nerdctl** | containerd (Docker CLI 호환) | 이 노드 1대 | 수동으로 컨테이너 다룰 때 |

핵심 차이는 **조회 경로**다.

```text
kubectl  →  apiserver  →  etcd     "선언된 상태" (이렇게 되어야 한다)
crictl   →  containerd            "실제 상태"   (지금 이 노드에서 실제로 도는 것)
```

**이 둘이 다를 수 있다**는 것이 중요하다. 그 차이를 보는 것이 장애 분석의 출발점이다.

#### 실제 활용 — apiserver가 죽었을 때

```text
$ kubectl get pods
The connection to the server 192.168.8.143:6443 was refused

$ sudo crictl ps
CONTAINER    IMAGE      STATE     NAME
a1b2c3...    etcd       Running   etcd
d4e5f6...    ...        Running   kube-scheduler
```

**apiserver가 죽어도 컨테이너는 계속 돈다.** `kubectl`로는 이 사실을 확인할 방법이 없다. 로드맵 질문 "API Server가 중단되면 기존 Pod와 신규 스케줄링은 어떻게 달라지는가"에 근거를 갖고 답하게 해주는 도구다.

#### 함정: `ctr`로는 Kubernetes 컨테이너가 보이지 않는다

containerd에는 **네임스페이스**라는 격리 개념이 있다.

```bash
sudo ctr containers ls              # 아무것도 나오지 않음
sudo ctr -n k8s.io containers ls    # 이제 보임
```

kubelet이 만든 컨테이너는 **`k8s.io` 네임스페이스**에 들어가는데, `ctr`의 기본값은 `default` 네임스페이스다. `crictl`은 CRI를 통해 조회하므로 이 문제가 없다. 노드에서 Kubernetes 컨테이너를 볼 때 `ctr`보다 `crictl`을 쓰는 이유다.

#### 주의: crictl로 컨테이너를 만들지 않는다

`crictl`은 **진단·검증 도구**다. 컨테이너를 직접 만들 수도 있지만 하면 안 된다.

```text
crictl로 컨테이너 생성
→ containerd에는 존재
→ 하지만 etcd에는 그런 Pod 기록이 없음
→ kubelet이 "선언되지 않은 컨테이너"로 보고 정리해버림
```

Kubernetes는 **선언된 상태로 수렴시키는** 시스템이므로, 선언 없이 만든 것은 제거 대상이 된다. 조회(`ps`, `logs`, `inspect`)에만 쓴다.

---

### 11. "Kubernetes가 Docker를 버렸다"의 실제 의미

2020년에 화제가 된 뉴스인데 오해가 많다.

```text
Docker는 CRI를 구현하지 않았음
→ kubelet 안에 dockershim이라는 어댑터를 유지해야 했음
→ Kubernetes 1.20에서 deprecated 공지
→ Kubernetes 1.24(2022)에서 제거
```

**Docker로 빌드한 이미지는 그대로 쓸 수 있다.** 이미지 형식은 OCI라는 별개 표준이고 containerd도 그것을 읽는다. 바뀐 것은 "kubelet이 Docker 데몬을 직접 호출하지 않는다"는 것뿐이다.

이 프로젝트에서 containerd를 직접 쓰는 이유가 이 흐름의 결과다.

---

### 12. 전체 아키텍처 속 위치

#### Control Plane — "무엇을 어디에 띄울지" 결정하는 두뇌

| 구성요소 | 역할 |
|---|---|
| **kube-apiserver** | 모든 요청의 관문. `kubectl apply`가 도착하는 곳 |
| **etcd** | 클러스터의 모든 상태가 저장되는 DB. 이것이 날아가면 클러스터가 통째로 사라짐 |
| **kube-scheduler** | 새 Pod를 어느 노드에 배치할지 결정 |
| **kube-controller-manager** | "원하는 상태 = 현재 상태"가 되도록 계속 감시하고 조정 |

#### Worker Node — 결정된 것을 실제로 실행하는 손발

| 구성요소 | 역할 |
|---|---|
| **kubelet** | PodSpec을 받아 CRI로 컨테이너 런타임에 요청 |
| **kube-proxy** | Service를 iptables 규칙으로 구현 |
| **컨테이너 런타임** | containerd + runc |

#### 그 사이를 잇는 규격 3개

| 규격 | 연결 | 없으면 |
|---|---|---|
| **CRI** | kubelet ↔ 고수준 런타임 | kubelet이 컨테이너를 만들 수 없음 |
| **OCI** | 고수준 런타임 ↔ 저수준 런타임 | 런타임 교체 불가, 이미지 호환성 상실 |
| **CNI** | 런타임 ↔ 네트워크 플러그인 | **Pod가 만들어져도 IP를 받지 못함** |

**CNI는 컨테이너 런타임만큼이나 필수다.** Calico, Cilium, Flannel 같은 것들이며, 앞서 "샌드박스를 만들 때 IP가 할당된다"고 한 그 작업의 실행자다. Phase 5에서 `kubeadm init` 직후 Node가 `NotReady`로 남는 이유가 바로 CNI가 아직 없기 때문이다.

**CoreDNS도 사실상 필수다.** Pod끼리 이름으로 통신하려면 필요하다.

---

### 최종 요약

```text
Control Plane  =  결정하는 곳  (apiserver, etcd, scheduler, controller-manager)
Worker Node    =  실행하는 곳  (kubelet, kube-proxy, 컨테이너 런타임)
CRI·OCI·CNI    =  그 사이를 잇는 규격들
```

---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| kubelet은 왜 컨테이너 런타임이 따로 필요한가 | kubelet은 요청만 하고, 실제 격리·실행은 런타임의 책임 |
| CRI는 왜 존재하는가 | kubelet을 특정 런타임에 종속시키지 않기 위한 인터페이스 |
| Ubuntu 기본 설정으로는 왜 안 되는가 | `disabled_plugins = ["cri"]` — Kubernetes 전용 인터페이스가 꺼져 있음 |
| cgroup 드라이버가 불일치하면 무엇이 깨지는가 | 관리 주체가 둘이 되어 Limit 미적용, Metrics 부정확, 간헐적 컨테이너 종료 |
| containerd만으로도 컨테이너가 되는데 Kubernetes는 왜 필요한가 | 스케줄링, 자가 치유, 서비스 디스커버리, 선언적 상태 관리 |

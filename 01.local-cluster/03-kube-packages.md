# 03. kubelet / kubeadm / kubectl 설치 (3대 전부)

Kubernetes 패키지 3개를 설치한다. 대상은 master01, worker01, worker02 **전부**다.

> **이 단계가 끝나도 클러스터는 생기지 않는다.** 도구만 설치된 상태다. 클러스터는 Phase 5의 `kubeadm init`에서 처음 만들어진다.

## 설치할 버전: v1.35

2026-08 기준 공식 지원 버전은 다음과 같다.

| 버전 | 최신 패치 | EOL |
|---|---|---|
| 1.36 | 1.36.2 | 2027-06-28 |
| **1.35** | 1.35.6 | 2027-02-28 |
| 1.34 | 1.34.9 | 2026-10-27 |

**1.35를 선택한 이유**

- 최신(1.36)보다 한 마이너 낮아 Argo CD, Helm chart, kube-prometheus-stack 등 생태계 도구의 호환성 검증이 끝나 있다. 8~9단계에서 이 도구들을 쓴다.
- 1.34는 EOL이 2026-10로 남은 기간이 짧다.
- Calico v3.32가 1.34 / 1.35 / 1.36을 모두 지원하므로 CNI 제약은 없다.

---

## 세 도구의 역할은 각각 다르다

| 도구 | 성격 | 언제 동작하는가 |
|---|---|---|
| **kubeadm** | 부트스트랩 도구 | `init` / `join` **실행 시점에만**. 상주하지 않음 |
| **kubelet** | 데몬 (systemd 서비스) | **항상 상주**. 각 노드에서 컨테이너를 관리 |
| **kubectl** | CLI 클라이언트 | 사용자가 명령할 때만 |

혼동하기 쉬운 지점이다. **kubeadm은 클러스터를 "만들어주고 빠지는" 도구**이고, 클러스터를 실제로 굴리는 것은 kubelet과 Control Plane 구성요소다. 클러스터가 동작 중일 때 kubeadm 프로세스는 존재하지 않는다.

```text
kubeadm init 실행
  → 인증서 생성
  → kubeconfig 생성
  → Static Pod manifest 배치
  → kubelet에게 넘김
  → kubeadm 종료 (여기서 역할 끝)

이후로는 kubelet과 Control Plane이 클러스터를 유지
```

### `cri-tools`(crictl)는 자동으로 설치되지 않는다

과거 kubeadm 패키지는 `cri-tools`를 의존성으로 끌어왔으나, **v1.35 기준으로는 함께 설치되지 않는다.** 별도로 설치해야 한다.

```bash
apt-cache policy cri-tools
sudo apt-get install -y cri-tools
```

**`crictl`이 왜 필요한가** — `kubectl`과 조회 경로가 완전히 다르다.

```text
kubectl  →  kube-apiserver  →  etcd에 저장된 "선언된 상태"를 조회
crictl   →  containerd (CRI 소켓)  →  이 노드에서 실제로 도는 컨테이너를 직접 조회
```

`kubectl`은 **apiserver가 살아 있어야만** 동작한다. Phase 8에서 apiserver를 의도적으로 중단시키는 실험을 하는데, 그 순간 `kubectl`은 아무 정보도 주지 못한다.

```text
apiserver 중단
→ kubectl get pods    : 연결 실패
→ sudo crictl ps      : 정상 동작 — 컨테이너가 여전히 실행 중임을 확인
```

이것이 로드맵 질문 **"API Server가 중단되었을 때 기존 Pod와 신규 스케줄링은 어떻게 달라지는가"** 에 실제로 답하는 도구다. `kubectl`이 실패한다고 Pod가 죽은 것이 아니라는 사실을 `crictl`로 증명하게 된다.

Control Plane 자체가 Static Pod로 동작하므로, apiserver가 죽었을 때 나머지 Control Plane 컨테이너 상태를 볼 수 있는 유일한 수단이기도 하다.

---

## 저장소 URL에 버전이 들어간다

```text
https://pkgs.k8s.io/core:/stable:/v1.35/deb/
                                  ^^^^^
```

**마이너 버전마다 저장소가 분리되어 있다.** 이는 실수로 마이너 버전을 건너뛰는 업그레이드를 막기 위한 구조다. 나중에 1.36으로 올리려면 이 URL을 바꾸고 `apt update`를 다시 해야 한다.

> 과거에는 `apt.kubernetes.io`(Google 호스팅) 단일 저장소를 썼으나 2023년에 폐지되었다. 오래된 블로그 글에 나오는 `apt.kubernetes.io` 주소는 더 이상 동작하지 않는다.

---

## 설치 절차

### 1. 사전 패키지

```bash
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gpg
```

### 2. GPG 키 등록

```bash
sudo mkdir -p -m 755 /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.35/deb/Release.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
sudo chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg
```

**왜 GPG 키가 필요한가**: apt는 다운로드한 패키지가 실제로 Kubernetes 프로젝트가 서명한 것인지 검증한다. 키가 없으면 중간자 공격으로 변조된 패키지를 받을 수 있다. `--dearmor`는 텍스트 형식(ASCII armor) 키를 apt가 읽는 바이너리 형식으로 변환한다.

`chmod 644`가 필요한 이유: 파일이 root만 읽을 수 있으면 apt가 비특권 사용자로 저장소를 갱신할 때 키를 읽지 못해 경고가 발생한다.

### 3. 저장소 등록

```bash
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.35/deb/ /' \
  | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo chmod 644 /etc/apt/sources.list.d/kubernetes.list
```

**파일 확장자는 반드시 `.list`여야 한다.** apt는 `/etc/apt/sources.list.d/` 아래에서 `.list`(한 줄 형식) 또는 `.sources`(deb822 형식)만 읽는다. 다른 확장자로 만들면 **오류 없이 조용히 무시**되어 `apt update`에 저장소가 잡히지 않는다.

`[signed-by=...]`는 "이 저장소의 패키지는 이 키로만 검증하라"는 뜻이다. 키를 전역 신뢰 목록에 넣지 않고 저장소별로 한정하는 것이 현재 권장 방식이다.

### 4. 설치

```bash
sudo apt-get update
apt-cache policy kubelet          # 설치될 버전 확인
sudo apt-get install -y kubelet kubeadm kubectl
```

`apt-cache policy`로 먼저 확인하는 이유: 저장소가 제대로 잡혔는지, 어떤 패치 버전이 설치될지 미리 본다. 여기서 `Candidate: (none)`이 나오면 3번의 저장소 등록이 실패한 것이다.

### 5. 버전 고정 (필수)

```bash
sudo apt-mark hold kubelet kubeadm kubectl
apt-mark showhold                 # 3개가 나와야 함
```

**왜 반드시 고정하는가**

`apt upgrade`가 kubelet을 임의로 올려버리면 Control Plane과 버전이 어긋난다. Kubernetes에는 **버전 차이 정책(version skew policy)** 이 있다.

```text
kubelet 버전  ≤  kube-apiserver 버전       ← 반드시 지켜야 함
kubelet은 apiserver보다 최대 1 마이너까지 낮을 수 있음
kubelet이 apiserver보다 높으면  → 지원되지 않음. 노드가 비정상 동작
```

즉 **업그레이드는 반드시 Control Plane 먼저, Worker 나중**이라는 순서가 있다. `apt upgrade`는 이 순서를 모르므로 자동 업그레이드를 막아야 한다.

이 프로젝트에서는 Phase 0에서 `apt upgrade`로 커널을 정렬했는데, 그때가 마지막 자유로운 업그레이드 시점이었다. 이제부터 `apt upgrade`는 Kubernetes 패키지를 건드리지 않는다.

### 6. kubelet 서비스 등록

```bash
sudo systemctl enable --now kubelet
```

---

## 이 시점의 kubelet은 crashloop 상태가 정상이다

설치 직후 kubelet 상태를 보면 이렇게 나온다.

```bash
systemctl status kubelet --no-pager
```

```text
Active: activating (auto-restart) (Result: exit-code)
```

또는 `failed`와 `activating`을 반복한다. **이것은 장애가 아니다.**

**왜 그런가**: kubelet이 동작하려면 두 가지가 필요하다.

```text
1. /var/lib/kubelet/config.yaml    kubelet 자신의 설정
2. /etc/kubernetes/kubelet.conf    apiserver 접속용 kubeconfig
```

둘 다 아직 없다. 이 파일들은 **`kubeadm init`(master) 또는 `kubeadm join`(worker)이 생성**한다. 그전까지 kubelet은 부팅 → 설정 없음 → 종료 → systemd가 재시작 을 반복한다.

로그로 직접 확인해 볼 수 있다.

```bash
journalctl -u kubelet -n 20 --no-pager
```

`/var/lib/kubelet/config.yaml` 관련 오류가 보이면 예상대로 동작하는 것이다.

**이 상태를 미리 알아두는 이유**: Phase 5에서 `kubeadm init`이 실패했을 때, kubelet이 crashloop인 것을 보고 "kubelet이 원인"이라고 오진하기 쉽다. 실제로는 init이 설정을 만들어주지 못한 것이 원인이고 kubelet의 crashloop은 결과다. **원인과 결과를 구분하는 연습**이다.

---

## 검증

```bash
kubeadm version
kubectl version --client
kubelet --version
crictl --version                          # cri-tools도 함께 설치됨
apt-mark showhold                         # kubelet, kubeadm, kubectl
systemctl is-enabled kubelet              # enabled
systemctl is-active kubelet               # activating 또는 failed — 정상
```

### CRI 연결 확인 (Phase 3의 후속)

`crictl`이 설치되었으므로 CRI 통신을 더 정확히 볼 수 있다. 다만 `crictl`은 기본적으로 여러 소켓 경로를 탐색하므로 명시하는 편이 확실하다.

```bash
sudo crictl --runtime-endpoint unix:///run/containerd/containerd.sock version
```

정상이면 `RuntimeName: containerd`와 버전이 출력된다. 매번 옵션을 주지 않으려면 설정 파일을 만든다.

```bash
sudo tee /etc/crictl.yaml <<'EOF'
runtime-endpoint: unix:///run/containerd/containerd.sock
image-endpoint: unix:///run/containerd/containerd.sock
timeout: 10
EOF

sudo crictl version
sudo crictl info | head -20
```

---

## 실행 결과 기록 (2026-08-03)

3대 모두 동일한 결과가 나왔다.

```text
$ kubeadm version
kubeadm version: &version.Info{Major:"1", Minor:"35", GitVersion:"v1.35.7",
  GitCommit:"96cb9ab4201d88ce5e549fde047a686171838fdb", GitTreeState:"clean",
  BuildDate:"2026-07-22T17:53:59Z", GoVersion:"go1.25.12", Platform:"linux/amd64"}

$ kubectl version --client
Client Version: v1.35.7
Kustomize Version: v5.7.1

$ apt-mark showhold
kubeadm
kubectl
kubelet

$ systemctl is-enabled kubelet
enabled

$ systemctl is-active kubelet
activating                          # 정상 — 설정 파일이 아직 없음

$ crictl --version
Command 'crictl' not found          # cri-tools 별도 설치 필요
```

### 설치된 패치 버전은 v1.35.7

공식 릴리스 페이지에서 확인한 최신 패치는 `1.35.6`이었으나 실제로는 `1.35.7`이 설치되었다. 문서 페이지가 갱신되기 전이었던 것으로, 더 최신 패치를 받은 것이므로 문제되지 않는다.

**3대의 버전이 동일한 것이 중요하다.** 버전 차이 정책(kubelet ≤ apiserver) 때문에 노드마다 버전이 갈리면 문제가 되는데, 같은 저장소에서 같은 시점에 설치해 `v1.35.7`로 통일되었다.

### `cri-tools` 누락

`crictl`이 kubeadm 의존성으로 함께 설치될 것으로 예상했으나 설치되지 않았다. 별도 설치가 필요하다.

```bash
sudo apt-get install -y cri-tools
sudo crictl version
```

**교훈**: "이 패키지에 딸려 온다"는 전제는 버전에 따라 바뀐다. 필요한 명령이 실제로 존재하는지는 `--version` 실행으로 직접 확인한다.

### kubelet crashloop 원인 직접 확인

```bash
journalctl -u kubelet -n 15 --no-pager
ls /var/lib/kubelet/config.yaml       # No such file
ls /etc/kubernetes/kubelet.conf       # No such file
```

이 두 파일이 없어 kubelet이 기동에 실패하고 systemd가 재시작을 반복한다. Phase 5의 `kubeadm init`이 파일을 생성하는 순간 `active (running)`으로 전환된다.

**Phase 5 전에 파일이 없다는 것을 직접 확인해두면**, init 이후 생성되는 것을 보고 인과관계가 명확해진다.

---

## 반복 작업 기록

| 항목 | 내용 |
|---|---|
| 3대 반복 시 불편했던 점 | (작성) |
| 실수하거나 빠뜨린 단계 | (작성) |
| 자동화 후보 | (작성) |

---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| kubeadm / kubelet / kubectl의 차이는 | 부트스트랩 도구 / 상주 데몬 / CLI 클라이언트 |
| 클러스터 동작 중에 kubeadm은 무엇을 하는가 | 아무것도 하지 않는다. init·join 시점에만 실행되고 종료 |
| 저장소 URL에 왜 버전이 들어가는가 | 마이너 버전을 건너뛰는 업그레이드를 구조적으로 차단 |
| `apt-mark hold`가 왜 필수인가 | kubelet ≤ apiserver 버전 정책이 있고, 업그레이드는 Control Plane 먼저라는 순서가 있음 |
| 설치 직후 kubelet이 죽는 이유는 | 설정 파일이 아직 없음. `kubeadm init`/`join`이 생성해 줌 |

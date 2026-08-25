# 1단계. VMware 기반 Kubernetes 클러스터 구축

`cloud-native-learning-roadmap.md` **1단계**의 작업 기록이다.

## 목표

클러스터를 한 번 띄우는 것이 목적이 아니다. kubeadm이 만드는 인증서, kubeconfig, Static Pod 구조와 Node 등록 절차를 직접 확인해 아래 질문에 답할 수 있는 상태가 되는 것이 목표다.

### 이 단계를 끝내면 답할 수 있어야 하는 질문

| # | 질문 | 답변 위치 |
|---|---|---|
| 1 | kubeadm은 어떤 인증서와 kubeconfig를 생성하는가 | [04-cluster-init.md](04-cluster-init.md) · [05-worker-join.md](05-worker-join.md) |
| 2 | kubelet은 API Server를 어떻게 찾고 인증하는가 | [05-worker-join.md](05-worker-join.md) |
| 3 | Worker Node는 어떤 절차를 거쳐 클러스터에 등록되는가 | [05-worker-join.md](05-worker-join.md) |
| 4 | CNI 설치 전에는 왜 Pod 간 통신이 동작하지 않는가 | [06-cni-calico.md](06-cni-calico.md) |
| 5 | Control Plane 구성요소는 실제로 어디에서 실행되는가 | [07-control-plane-analysis.md](07-control-plane-analysis.md) |
| 6 | Static Pod는 일반 Pod와 무엇이 다른가 | [07-control-plane-analysis.md](07-control-plane-analysis.md) |
| 7 | etcd에는 어떤 정보가 저장되는가 | [07-control-plane-analysis.md](07-control-plane-analysis.md) |
| 8 | API Server가 중단되면 기존 Pod와 신규 스케줄링은 어떻게 달라지는가 | [08-failure-experiments.md](08-failure-experiments.md) |
| 9 | kubelet이나 containerd가 중단되면 Node와 Pod 상태는 어떻게 변하는가 | [08-failure-experiments.md](08-failure-experiments.md) |

**9개 질문 전부 답변 완료. 1단계를 마쳤다.**

### 한 줄 요약

```text
1. 인증서 3종(ca / etcd-ca / front-proxy-ca)이 CA부터 분리되어 있고,
   자기서명 CA가 신뢰받는 근거는 서명이 아니라 배포 경로다
2. kubelet은 kubelet.conf의 클라이언트 인증서로 인증한다
   그 인증서는 join 시점에 TLS Bootstrap으로 발급받은 것이다
3. 토큰으로 임시 인증 → CSR 제출 → 자동 승인 → 인증서 수령 → Node 등록
4. CNI가 없으면 Pod에 IP를 줄 주체가 없다. NetworkUnavailable이 안 풀린다
5. Static Pod로 실행된다. kubelet이 파일을 읽어 apiserver 없이 띄운다
6. 선언이 파일이고, 소유자가 Node이며, config.* 어노테이션이 붙는다
   apiserver로 삭제할 수 없다 (자기참조)
7. /registry 아래 351개 키. protobuf. Secret은 평문이다
8. 기존 Pod와 트래픽은 그대로. 신규 스케줄링·축출·복구가 전부 멈춘다
   단, apiserver를 쓰는 앱은 헬스 체크 실패로 함께 죽는다
9. kubelet 중단 → Ready=Unknown(52초) / containerd 중단 → Ready=False(28초)
   둘 다 컨테이너는 계속 돌고, 축출은 331초 뒤에 일어난다
```

### 관통선

```text
인증·인가·선언은 전부 apiserver 가 제공하는 것이다.
etcd 에 직접 붙으면 그것이 전부 사라진다.
그래서 etcd 만 CA 부터 따로 격리한다.

설정하는 자와 전달하는 자는 다르다.
그래서 제어 평면이 넷 다 죽어도 트래픽은 끊기지 않았다.
```

## 문서 구성

**두 종류의 문서를 나눠서 쓴다.** 독자와 목적이 다르기 때문이다.

```text
01.local-cluster/                작업 기록 — 무엇을 했고 어떤 출력이 나왔나
├─ 00-environment.md
├─ 01-os-prerequisites.md
└─ ...

작업다이어리/01.local-k8s-cluster/  블로그 원고 — 왜 그렇게 동작하는가
├─ 2026-08-06 작업노트
├─ 2026-08-07 작업노트
└─ ...
```

| | 번호 문서 | 작업다이어리 |
|---|---|---|
| 독자 | 나중의 나 — 재현·진단용 | 모르는 사람 |
| 축 | **시간순** (설치 순서) | **주제 하나를 끝까지** |
| 내용 | 실행 명령, 실제 출력, 검증 결과, 겪은 문제 | 커널·프로세스 수준의 동작 원리, 반박 Q&A |

**왜 나누는가**: 초기에는 한 문서에 실행 기록과 개념을 함께 담았는데, 블로그 글을 쓸 때 문제가 있었다. 작업 기록은 시간순이라 같은 주제가 여러 문서에 흩어지고, "우리가 이렇게 했다"는 맥락이 독자에게는 불필요하다. 반대로 개념 설명을 작업 기록에 섞으면 나중에 재현할 때 필요한 정보를 찾기 어렵다.

**두 문서는 같은 작업을 다른 축으로 자른 것**이다. 예를 들어 Control Plane 분석 작업 하나가 이렇게 나뉜다.

```text
07-control-plane-analysis.md   실행 명령 → 실제 출력 → 검증 → 겪은 문제
2026-08-08 작업노트             etcd 는 무엇인가 → 왜 거기 다 있나 → Q&A
```

### 블로그 원고 목록

원고는 전부 [`작업다이어리/01.local-k8s-cluster/`](../작업다이어리/01.local-k8s-cluster/)에 날짜 이름으로 있다.

| 원고 | 대응하는 작업 | 주제 |
|---|---|---|
| `2026-07-30 작업노트` | [00](00-environment.md) ~ [03](03-kube-packages.md) | 사전 준비 — 왜 2 vCPU이고 왜 swap을 끄는가 |
| `2026-08-03 작업노트.txt` | [03](03-kube-packages.md) ~ [06](06-cni-calico.md) | 버전 선택과 설치 진행 기록 |
| `2026-08-06 작업노트` | [07](07-control-plane-analysis.md) 1~2라운드 | 인증과 인가 — 너 누구냐 / 너 뭘 할 수 있냐 |
| `2026-08-07 작업노트` | [07](07-control-plane-analysis.md) 3라운드 | Static Pod — 선언 / 실제 / 사본의 세 층 |
| `2026-08-08 작업노트` | [07](07-control-plane-analysis.md) 4라운드 | etcd — 그 모든 것이 저장된 단 한 곳 |
| `2026-08-10 작업노트` | [08](08-failure-experiments.md) | 장애를 넣어보고 배운 것 — 무엇이 죽고 무엇이 사는가 |

> **2026-08-11 수정.** 이 목록에는 원래 `blog/kubeadm-init.md`, `blog/kubeadm-join.md`, `blog/calico-cni.md` 세 편이 적혀 있었으나 **그 파일들은 작성되지 않았고 `blog/` 디렉터리도 정리했다.** 문서 체계를 정할 때 계획만 적어두고 실제 원고는 작업다이어리에 쓴 것이다. 실재하는 파일로 목록을 교체했다.

뒤의 네 편이 순서대로 이어진다.

```text
08-06   인증서로 무엇을 증명하고 무엇을 허락받는가        → 인증과 인가
08-07   선언이 파일이고 프로세스는 결과다                 → 세 층
08-08   그 선언이 저장된 단 한 곳                        → etcd
08-10   그것들을 하나씩 부수면 무엇이 죽고 무엇이 사는가   → 제어/데이터 평면
```

**아직 원고가 없는 주제**가 셋 있다. `04`~`06` 작업 기록은 있으나 개념 원고로 재구성되지 않았다.

```text
kubeadm init   apiserver 를 띄우려면 apiserver 가 필요하다        → Static Pod
kubeadm join   인증하려면 인증서가 필요한데 받으려면 인증해야 한다  → 일회용 토큰
CNI            조직도는 만들어졌는데 도로가 없다                   → 데이터 평면
```

## 왜 Minikube / kind를 쓰지 않는가

Minikube와 kind는 Kubernetes를 빠르게 체험하기에는 좋지만, **위 9개 질문의 답이 되는 과정을 전부 추상화**한다. Control Plane 구성요소 배치, 인증서 생성, kubeconfig 발급, kubelet 등록, CNI 설치가 자동으로 끝나기 때문에 1단계의 학습 목표를 달성할 수 없다.

kubeadm은 최소 기능을 갖춘 표준 클러스터를 직접 구성하게 하므로, 클러스터 초기화와 Node Join 과정을 눈으로 확인할 수 있다.

## 클러스터 구성

```text
                    ┌─────────────────────────────────────┐
                    │  master01        192.168.8.143      │
                    │  ─────────────────────────────────  │
                    │  Static Pod (kubelet이 직접 실행)   │
                    │    ├─ kube-apiserver                │
                    │    ├─ kube-scheduler                │
                    │    ├─ kube-controller-manager       │
                    │    └─ etcd                          │
                    │  DaemonSet                          │
                    │    ├─ kube-proxy                    │
                    │    └─ calico-node                   │
                    │  systemd                            │
                    │    ├─ kubelet                       │
                    │    └─ containerd                    │
                    └──────────────┬──────────────────────┘
                                   │ :6443 (kube-apiserver)
                    ┌──────────────┴──────────────┐
                    │                             │
       ┌────────────┴─────────────┐  ┌────────────┴─────────────┐
       │ worker01   192.168.8.142 │  │ worker02   192.168.8.141 │
       │ ──────────────────────── │  │ ──────────────────────── │
       │ DaemonSet                │  │ DaemonSet                │
       │   ├─ kube-proxy          │  │   ├─ kube-proxy          │
       │   └─ calico-node         │  │   └─ calico-node         │
       │ systemd                  │  │ systemd                  │
       │   ├─ kubelet             │  │   ├─ kubelet             │
       │   └─ containerd          │  │   └─ containerd          │
       └──────────────────────────┘  └──────────────────────────┘
```

상세 환경 정보는 [00-environment.md](00-environment.md) 참조.

## 진행 체크리스트

| 문서 | 내용 | 대상 | 상태 |
|---|---|---|---|
| [00-environment.md](00-environment.md) | 환경 정보, 고정 IP, 네트워크 대역 설계 | 3대 | ✅ 완료 |
| [01-os-prerequisites.md](01-os-prerequisites.md) | swap, 커널 모듈, sysctl, hosts | 3대 | ✅ 완료 |
| [02-containerd.md](02-containerd.md) | containerd 2.2.1, CRI 활성화, cgroup 드라이버 | 3대 | ✅ 완료 |
| [03-kube-packages.md](03-kube-packages.md) | kubeadm/kubelet/kubectl v1.35.7 | 3대 | ✅ 완료 |
| [04-cluster-init.md](04-cluster-init.md) | `kubeadm init` — Control Plane 생성 | master01 | ✅ 완료 |
| [05-worker-join.md](05-worker-join.md) | `kubeadm join` — TLS Bootstrap, 신뢰 구조 | worker 2대 | ✅ 완료 |
| [06-cni-calico.md](06-cni-calico.md) | Calico v3.32.1 — 데이터 평면 구성 | master01 | ✅ 완료 |
| [07-control-plane-analysis.md](07-control-plane-analysis.md) | 인증서·kubeconfig·Static Pod·etcd 분석 | master01 | ✅ 완료 (1~4라운드) |
| [08-failure-experiments.md](08-failure-experiments.md) | apiserver/etcd/kubelet/containerd 중단 실험 | 3대 | ✅ 완료 (실험 1~4) |

> **worker join과 CNI 설치 순서를 바꿨다.** 원래 계획은 CNI 설치 후 join이었으나, worker를 먼저 join한 뒤 CNI를 설치하면 **3대가 동시에 `NotReady` → `Ready`로 전환되는 것**을 한 번에 관찰할 수 있다. CNI가 무엇을 해결하는지가 훨씬 명확하게 드러난다. 문서 번호도 실제 실행 순서에 맞췄다.

> 진행 상태는 각 문서의 "실행 결과 기록" 섹션을 기준으로 판단한다.

## 구축 순서와 의존 관계

```text
OS 기본 설정 (hostname, hosts)
→ Swap 비활성화                    ← kubelet이 기동을 거부하는 조건
→ Kernel Module + sysctl 설정      ← 없으면 Pod 네트워킹이 동작하지 않음
→ containerd 설치 및 구성           ← CRI 활성화 + cgroup 드라이버 일치
→ kubelet, kubeadm, kubectl 설치
→ kubeadm init                     ← 인증서/kubeconfig/Static Pod 생성
→ kubectl용 kubeconfig 설정
→ CNI Plugin 설치                  ← 이 시점에 Node가 Ready로 전환
→ Worker Node kubeadm join
```

각 단계가 **왜 그 순서인지**가 중요하다. 예를 들어 CNI를 kubeadm init보다 먼저 설치할 수 없는 이유는, CNI 자체가 Kubernetes 리소스(DaemonSet)로 배포되기 때문에 API Server가 먼저 떠 있어야 한다는 것이다.

## 이 단계에서 만들지 않는 것

**Ansible playbook과 설치 스크립트는 만들지 않는다.**

로드맵 학습 원칙 1(자동화하기 전에 직접 구성한다)에 따라 Phase 2~4를 3대에 수동으로 반복한다. 반복 작업의 번거로움과 실수 지점을 직접 겪은 뒤에 자동화해야, Ansible이 무엇을 해결해주는 도구인지 알 수 있다.

반복 중 겪은 불편함은 각 문서의 "반복 작업 기록" 항목에 남긴다. 이후 Ansible 도입 시 근거 자료가 된다.

## 작업 원칙

1. **명령은 직접 실행한다.** AI 도우미는 실행할 명령과 그 이유, 정상 출력의 모습을 제시하고, 실행 결과 해석을 돕는다.
2. **예상과 다른 출력이 나오면 우회하지 않는다.** 다른 명령을 시도하기 전에 원인을 먼저 분석한다. 실패 출력 자체가 학습 대상이다.
3. **Phase 완료마다 VMware 스냅샷을 남긴다.** 되돌릴 수 없는 실수는 OS 재설치가 아니라 스냅샷 복원으로 복구한다.
4. **실제 출력을 문서에 남긴다.** 명령만 적어둔 문서는 재현할 때 쓸모가 없다. 정상 출력이 어떤 모습이었는지가 나중에 장애 판단의 기준이 된다.

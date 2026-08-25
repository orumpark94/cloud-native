# 05. Worker Node Join — TLS Bootstrap과 신뢰 구조

worker01, worker02를 클러스터에 등록한다.

```bash
sudo kubeadm join 192.168.8.143:6443 --token <token> \
     --discovery-token-ca-cert-hash sha256:<hash>
```

**이 문서의 목적은 명령 기록이 아니다.** join이 worker에 무엇을 남겼는지 확인해 아래 두 질문에 답하는 것이다.

```text
- kubelet은 API Server를 어떻게 찾고 인증하는가
- Worker Node는 어떤 절차를 거쳐 클러스터에 등록되는가
```

---

## join이 하는 일의 정확한 성격

흔히 "join이 마스터와 워커의 통신 방식을 정의한다"고 이해하기 쉬운데, 세 가지를 정밀하게 구분해야 한다.

### ① 규칙을 정의한 것은 init이고, join은 그 규칙대로 발급받는 절차다

`kubeadm init` 출력에 이미 준비가 끝나 있었다.

```text
[bootstrap-token] Configured RBAC rules to allow Node Bootstrap tokens to post CSRs
[bootstrap-token] Configured RBAC rules to allow the csrapprover controller automatically approve CSRs
[bootstrap-token] Creating the "cluster-info" ConfigMap in the "kube-public" namespace
```

```text
init  →  "노드가 이렇게 오면 이렇게 받아주겠다"는 규칙과 CA를 준비
join  →  그 규칙대로 줄 서서 신분증을 받아오는 절차
```

worker를 100대 붙여도 클러스터 설정은 바뀌지 않는다. 규칙은 이미 정해져 있고 각 노드가 신원만 발급받는다.

### ② 신뢰 상대는 "master 노드"가 아니라 "CA"다

worker가 신뢰하는 것은 `192.168.8.143`이라는 머신이 아니라 **그 CA가 서명했다는 사실**이다.

```text
worker01의 /etc/kubernetes/pki/ca.crt
  → 이 CA가 서명한 것이면 신뢰한다
  → master01이라는 특정 머신을 신뢰하는 것이 아니다
```

그래서 Control Plane을 3대로 늘리는 HA 구성에서도 worker 설정은 그대로다. 같은 CA를 공유하기 때문이다. 반대로 **CA 개인키가 유출되면 클러스터 전체가 무너진다** — 임의의 신원을 만들어낼 수 있게 된다.

### ③ join이 세우는 것은 제어 채널이지 데이터 평면이 아니다

```text
join이 세운 것       kubelet ↔ apiserver     인증된 제어 채널
join이 다루지 않는 것  Pod ↔ Pod               데이터 평면 → CNI의 영역
```

**증거**: 3대가 join을 마치고 인증서도 발급받았는데 여전히 `NotReady`다. 통신이 전부 정의됐다면 `Ready`여야 한다. Pod 네트워크는 [06-cni-calico.md](06-cni-calico.md)에서 해결된다.

---

## Join 절차 상세

```text
1. worker가 cluster-info ConfigMap을 읽는다 (kube-public 네임스페이스)
   → 인증 없이 접근 가능. init이 RBAC로 익명 읽기를 허용해둠
   → CA 인증서와 apiserver 주소를 획득

2. --discovery-token-ca-cert-hash 로 받은 CA를 검증
   → worker는 아직 CA가 없으므로 받아야 하는데,
     받은 것이 위조가 아님을 확인할 방법이 필요하다
   → 해시를 미리 알고 있으면 검증 가능. 없으면 중간자 공격(MITM)에 무방비

3. Bootstrap Token으로 apiserver에 임시 인증

4. kubelet이 키 쌍을 생성하고 CSR 제출
   → "이 공개키에 대해 system:node:worker01 이름으로 서명해달라"

5. csrapprover 컨트롤러가 자동 승인 → CA가 서명
   → /var/lib/kubelet/pki/ 에 저장

6. 이후로는 토큰이 아니라 이 인증서로 통신. Node 오브젝트가 etcd에 등록
```

### 토큰과 인증서의 역할 구분

```text
--token                          worker → apiserver 방향 인증 (임시 신분)
--discovery-token-ca-cert-hash   apiserver → worker 방향 검증 (상호 인증)
```

**토큰은 일회용 임시 신분이고, 진짜 신분증은 그것으로 발급받는 장기 인증서다.** 그래서 토큰이 24시간 후 만료되어도 이미 join한 노드는 영향받지 않는다.

### CSR은 1시간 후 자동 삭제된다

```text
$ kubectl get csr
No resources found          # join 15시간 후 확인 결과
```

승인된 CSR은 controller-manager의 `csrcleaner`가 약 1시간 후 정리한다. CSR은 인증서 발급을 위한 **일회성 요청**이므로, 노드 증가와 인증서 갱신이 반복되면 etcd에 불필요한 오브젝트가 누적되기 때문이다.

**join 직후가 아니면 CSR로 확인할 수 없다.** 대신 worker에 남은 인증서 파일이 증거가 된다.

---

## 실행 결과 (2026-08-03)

### A. 발급받은 인증서

```text
# worker01
$ ls -la /var/lib/kubelet/pki/
-rw------- 1 root root 1114 Aug  3 17:27 kubelet-client-2026-08-03-17-27-50.pem
lrwxrwxrwx 1 root root   59 Aug  3 17:27 kubelet-client-current.pem
                              -> /var/lib/kubelet/pki/kubelet-client-2026-08-03-17-27-50.pem
-rw-r--r-- 1 root root 2270 Aug  3 17:27 kubelet.crt
-rw------- 1 root root 1675 Aug  3 17:27 kubelet.key
```

| 파일 | 용도 |
|---|---|
| `kubelet-client-current.pem` | **심볼릭 링크**. kubelet이 apiserver에 접속할 때 쓰는 클라이언트 인증서 |
| `kubelet-client-<타임스탬프>.pem` | 실제 인증서. 갱신될 때마다 새 파일이 생성됨 |
| `kubelet.crt` / `kubelet.key` | kubelet이 **서버 역할**을 할 때 쓰는 인증서 (10250 포트) |

**심볼릭 링크 구조인 이유**: kubelet은 만료 전에 인증서를 스스로 갱신한다. 새 파일을 만들고 링크만 바꾸면 되므로 설정 파일을 수정할 필요가 없다.

### B. 신뢰 경계 — worker에는 CA 개인키가 없다

```text
# master01의 /etc/kubernetes/pki/  (15개 파일 + etcd/ 디렉터리)
ca.crt              ca.key                       ← CA 개인키 있음
apiserver.crt       apiserver.key
apiserver-etcd-client.crt/.key
apiserver-kubelet-client.crt/.key
front-proxy-ca.crt  front-proxy-ca.key
front-proxy-client.crt/.key
sa.key              sa.pub
etcd/

# worker01의 /etc/kubernetes/pki/
ca.crt                                           ← 공개 인증서 하나뿐
```

```text
ca.crt   공개 인증서  →  서명을 "검증"할 수 있음
ca.key   개인 키      →  서명을 "만들" 수 있음      ← worker에는 없음
```

**worker가 침해되어도 다른 노드의 인증서를 위조할 수 없다.** 서명 권한이 Control Plane에만 존재하는 것이 Kubernetes 보안 모델의 기초다.

### C. Control Plane 인증서 15개의 분류

| 파일 | 용도 |
|---|---|
| `ca.crt` / `ca.key` | **클러스터 CA** — 모든 신원의 뿌리 |
| `apiserver.crt` / `.key` | apiserver의 **서버** 인증서 (6443에서 제시) |
| `apiserver-kubelet-client.*` | apiserver가 **kubelet에 접속**할 때. `kubectl logs`·`exec`의 역방향 경로 |
| `apiserver-etcd-client.*` | apiserver가 **etcd에 접속**할 때 |
| `front-proxy-ca.*` / `front-proxy-client.*` | **별도 CA** — 확장 API 서버(Aggregation Layer)용 |
| `sa.key` / `sa.pub` | **인증서가 아님.** ServiceAccount 토큰(JWT) 서명용 키 쌍 |
| `etcd/` | **또 다른 별도 CA** — etcd 클러스터 내부 인증 전용 |

**CA가 하나가 아니라 최소 3개다.**

```text
클러스터 CA      노드·관리자·apiserver 인증
front-proxy CA   확장 API 서버 인증
etcd CA          etcd 내부 인증
```

**침해 범위를 격리하기 위한 설계다.** etcd CA가 유출되어도 노드 인증서를 위조할 수 없다. `sa.key`가 별도인 것도 같은 맥락이며, ServiceAccount 토큰 서명은 X.509와 다른 메커니즘(JWT)이다.

### D. kubelet이 apiserver를 찾는 방법

```text
$ sudo grep -E 'server:|client-certificate' /etc/kubernetes/kubelet.conf
    server: https://192.168.8.143:6443
    client-certificate: /var/lib/kubelet/pki/kubelet-client-current.pem
```

**master의 `admin.conf`와 형식이 다르다.**

```text
admin.conf     client-certificate-data: LS0tLS1CRUdJTi...    ← 인증서를 파일 안에 내장
kubelet.conf   client-certificate: /var/lib/kubelet/pki/...  ← 파일 경로만 참조
```

**경로 참조 방식인 이유는 자동 갱신 때문이다.** kubelet이 새 인증서 파일을 만들고 심볼릭 링크만 바꾸면 되며, kubeconfig 자체를 다시 쓸 필요가 없다.

init 출력에 이 동작이 명시되어 있었다.

```text
[kubelet-finalize] Updating "/etc/kubernetes/kubelet.conf" to point to a rotatable
                   kubelet client certificate and key
```

### E. 인증서 안의 신원 — Kubernetes 인증의 핵심

```text
master01   subject=O = system:nodes, CN = system:node:master01
worker01   subject=O = system:nodes, CN = system:node:worker01
worker02   subject=O = system:nodes, CN = system:node:worker02
issuer=CN = kubernetes
```

**Kubernetes는 인증서의 Subject 필드로 신원을 판단한다.**

```text
CN (Common Name)   →  사용자 이름  →  system:node:worker01
O  (Organization)  →  그룹        →  system:nodes
```

`system:nodes` 그룹에는 **Node Authorizer**라는 전용 인가 모듈이 적용된다. **자기 노드에 배치된 Pod의 정보만 읽을 수 있고 다른 노드 것은 접근할 수 없다.** worker가 침해되어도 클러스터 전체 정보를 수집할 수 없다.

**master01도 kubelet으로서는 worker와 동일한 신원 체계를 따른다.** 같은 `system:nodes` 그룹이며 노드 이름만 다르다. master01이 특별한 이유는 kubelet 때문이 아니라 Control Plane 컴포넌트가 거기서 실행되기 때문이고, 그것들은 별도의 인증서를 사용한다. **역할이 신원에 섞여 있지 않다.**

대조 확인:

```text
$ sudo grep client-certificate-data ~/.kube/config | awk '{print $2}' \
    | base64 -d | openssl x509 -noout -subject
subject=O = kubeadm:cluster-admins, CN = kubernetes-admin
```

**같은 CA가 서명한 인증서인데 권한이 완전히 다르다.** 차이는 Subject 필드뿐이다. 인증서 기반 인증이 어떻게 동작하는지 보여주는 가장 명확한 예다.

### F. 인증서 유효기간과 5분 백데이팅

```text
master01   notBefore=Aug  3 08:06:53 2026 GMT      ← 발급 시각(08:11)보다 5분 이전
           notAfter =Aug  3 08:11:53 2027 GMT

worker01   notBefore=Aug  3 08:22:50 2026 GMT      ← 백데이팅 없음
           notAfter =Aug  3 08:22:50 2027 GMT
```

**kubeadm이 생성하는 인증서는 유효 시작 시각을 5분 앞당긴다. 노드 간 시계 오차를 허용하기 위해서다.**

```text
발급 시각이 08:11인데 어떤 노드의 시계가 3분 느리면
→ 그 노드는 현재를 08:08로 인식
→ notBefore가 08:11이면 "아직 유효하지 않은 인증서"로 거부
→ 5분 여유로 이를 방지
```

**Phase 0에서 worker02의 `System clock synchronized: no`를 그냥 넘기지 않았던 이유가 이것이다.** 시계가 5분 이상 어긋나면 join이 `certificate is not yet valid`로 실패한다.

worker의 인증서는 CSR을 통해 발급되어 백데이팅이 적용되지 않는다. 발급 경로가 다르기 때문이다.

**유효기간은 1년이다.** 운영에서 실제로 장애가 발생하는 지점이며, "1년 전 구축한 클러스터가 갑자기 죽었다"의 대표적 원인이다.

| 인증서 | 자동 갱신 |
|---|---|
| kubelet 클라이언트 | **자동** — `-current.pem` 심볼릭 링크 구조가 이를 위한 것 |
| Control Plane (apiserver 등) | **수동** — `kubeadm certs renew` 필요 |

```bash
sudo kubeadm certs check-expiration
```

상세는 [07-control-plane-analysis.md](07-control-plane-analysis.md)에서 다룬다.

### G. worker에는 없는 것

```text
master01의 /etc/kubernetes/   admin.conf, super-admin.conf, kubelet.conf,
                              controller-manager.conf, scheduler.conf, manifests/, pki/
worker01의 /etc/kubernetes/   kubelet.conf, pki/(ca.crt만)
```

worker에 `controller-manager.conf`와 `scheduler.conf`가 없는 이유는 **그 컴포넌트가 실행되지 않기 때문**이다. `manifests/`도 비어 있다 — Static Pod가 없다.

### H. worker에서 kubectl이 실패하는 것은 정상이다

```text
$ kubectl get nodes
The connection to the server localhost:8080 was refused
```

**worker에는 kubeconfig가 없다.** kubectl은 설정을 찾지 못하면 기본값인 `http://localhost:8080`으로 접속을 시도한다. 이는 Kubernetes 초기의 인증 없는 평문 포트이며 현재는 어떤 클러스터도 열어두지 않는다.

Phase 4에서 3대에 동일하게 kubectl을 설치했기 때문에 명령 자체는 존재한다.

**실무에서는 worker에서 클러스터를 조작하지 않는다.** worker에 관리자 kubeconfig를 두면 해당 노드가 침해되었을 때 클러스터 전체가 넘어간다. 관리 명령은 Control Plane이나 별도 관리 단말에서 실행한다.

---

## 클러스터 상태 (join 완료 시점)

```text
$ kubectl get nodes -o wide
NAME       STATUS     ROLES           VERSION   INTERNAL-IP
master01   NotReady   control-plane   v1.35.7   192.168.8.143
worker01   NotReady   <none>          v1.35.7   192.168.8.142
worker02   NotReady   <none>          v1.35.7   192.168.8.141

$ kubectl get pods -A -o wide
kube-system   coredns-...-2dwfb                  0/1   Pending   <none>          <none>
kube-system   coredns-...-jhlw8                  0/1   Pending   <none>          <none>
kube-system   etcd-master01                      1/1   Running   192.168.8.143   master01
kube-system   kube-apiserver-master01            1/1   Running   192.168.8.143   master01
kube-system   kube-controller-manager-master01   1/1   Running   192.168.8.143   master01
kube-system   kube-proxy-c8rqh                   1/1   Running   192.168.8.142   worker01
kube-system   kube-proxy-nbt49                   1/1   Running   192.168.8.141   worker02
kube-system   kube-proxy-zbzcj                   1/1   Running   192.168.8.143   master01
kube-system   kube-scheduler-master01            1/1   Running   192.168.8.143   master01
```

**확인 사항**

- 3대 모두 `NotReady` — CNI 부재. 정상
- `kube-proxy`가 **3개**로 증가 — DaemonSet이 노드마다 하나씩 배치
- kube-proxy의 IP가 **노드 IP와 동일** — hostNetwork 사용, 그래서 CNI 없이 동작
- worker의 `ROLES`가 `<none>` — kubeadm은 worker에 role label을 붙이지 않는다. Kubernetes에서 role은 강제되는 개념이 아니라 관례다

---


---

## 심화 — 제어 평면과 데이터 평면은 별개다

> join이 세운 것은 제어 채널뿐이며, Pod 간 통신은 CNI의 영역이라는 사실을 구조적으로 정리한다.

### Control Plane은 수동적인 저장소가 아니다

"master는 클러스터 정보를 갖고 있는 뇌"라는 비유는 적절하다. 다만 **저장만 하는 것이 아니라 계속 판단하고 명령한다.**

| 구성요소 | 뇌에 비유하면 | 하는 일 |
|---|---|---|
| **etcd** | 기억 | 상태 저장 |
| **apiserver** | 모든 감각과 명령이 지나는 관문 | 인증·인가·검증 후 etcd 읽기/쓰기 |
| **scheduler** | 판단 | 새 Pod를 어느 노드에 배치할지 결정 |
| **controller-manager** | 항상성 유지 | **선언 상태 ≠ 실제 상태**이면 계속 조정 |

특히 controller-manager는 **가만히 있지 않는다.** 지속적으로 "현재 상태가 선언과 다른가"를 확인하고 다르면 수정한다. Pod를 삭제해도 다시 생성되는 것이 이 루프 때문이다.

### 성격이 완전히 다른 두 평면

#### 제어 평면 (Control Plane) — 별 구조, 전부 apiserver 경유

```text
              apiserver
             /    |    \
            /     |     \
      worker01  master  worker02

  worker01과 worker02는 서로 직접 대화하지 않는다
```

**노드끼리는 제어 목적으로 통신하지 않는다.** worker01의 kubelet은 worker02의 존재를 알 필요조차 없으며, 오직 apiserver에게만 보고하고 지시받는다.

여기서는 "별개의 연결"이 아니라 **"전부 master를 통한 연결"** 이다.

#### 데이터 평면 (Data Plane) — 노드 간 직접, master 미경유

```text
      worker01의 Pod  ─────────────>  worker02의 Pod
                    (master를 경유하지 않음)
```

**Pod 간 실제 트래픽은 노드 사이를 직접 흐른다.** master는 관여하지 않는다. 만약 모든 Pod 트래픽이 master를 거친다면 master가 병목이 되어 클러스터가 확장될 수 없다.

#### 두 평면의 담당자가 다르다

| 평면 | 무엇이 만드나 | 없으면 |
|---|---|---|
| 제어 평면 | `kubeadm init` / `join` — 인증서와 kubeconfig | 노드가 등록되지 않음 |
| **데이터 평면** | **CNI (Calico)** | **Pod가 IP를 받지 못함** |

### 지금 상태가 이 분리를 증명한다

```text
제어 평면    join 완료, 3노드 등록, 인증서 발급     ✅ 완성
데이터 평면  Pod가 IP도 받지 못함                    ❌ 없음
                        ↓
                    NotReady
```

**제어 평면이 정상인데도 `NotReady`다.** 두 평면이 독립적이라는 명확한 증거이며, **Calico는 데이터 평면만 만든다.**

역방향도 성립한다. Phase 8에서 apiserver를 중단시키면 **제어 평면은 마비되지만 이미 실행 중인 Pod들은 계속 통신한다.** `kubectl`은 실패하고 `crictl`로만 확인 가능한 상태가 된다.

### 라우팅 테이블로 확인하는 방법

데이터 평면이 master를 경유하지 않는다는 사실은 라우팅 테이블에서 직접 확인된다.

```bash
# worker01 에서
ip route | grep 10.244
```

**CNI 설치 전**

```text
(출력 없음)          ← 데이터 평면이 존재하지 않음
```

**CNI 설치 후**

```text
10.244.2.0/24 via 192.168.8.141 dev tunl0
                  ^^^^^^^^^^^^^ worker02의 IP — master(.143)가 아님
```

**worker02의 Pod 대역으로 가는 경로가 worker02를 직접 가리킨다.** 이 한 줄이 두 평면의 분리를 가장 간결하게 보여준다.

---

---

## 이 단계가 답하는 질문

| 질문 | 답 |
|---|---|
| kubelet은 API Server를 어떻게 찾는가 | `/etc/kubernetes/kubelet.conf`의 `server:` — init 시점의 IP가 고정 기록됨 |
| kubelet은 어떻게 인증하는가 | `/var/lib/kubelet/pki/`의 클라이언트 인증서. Subject의 CN·O가 신원 |
| Worker는 어떤 절차로 등록되는가 | cluster-info 조회 → CA 검증 → 토큰 인증 → CSR 제출 → 자동 승인 → 인증서 발급 |
| 토큰과 인증서의 관계는 | 토큰은 일회용 임시 신분, 인증서가 장기 신분증 |
| master와 worker의 신뢰 경계는 | CA 개인키(`ca.key`)의 유무. worker는 검증만 가능하고 발급은 불가 |
| join이 Pod 네트워크도 설정하는가 | 아니다. 제어 채널만 담당하며 데이터 평면은 CNI의 영역 |
| 노드끼리 직접 통신하는가 | 제어 평면은 아니오(전부 apiserver 경유), 데이터 평면은 예(직접) |
| Pod 트래픽이 master를 거치는가 | 거치지 않는다. 거친다면 master가 병목이 되어 확장 불가 |

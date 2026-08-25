# 07. Control Plane 해부 — 인증서 · kubeconfig · Static Pod · etcd

클러스터가 정상 동작하는 상태에서 `kubeadm init`이 만든 것을 하나씩 열어본다.

로드맵 1단계 질문 9개 중 **4개를 이 단계에서 채운다.**

| # | 질문 | 다루는 라운드 | 상태 |
|---|---|---|---|
| 1 | kubeadm은 어떤 인증서와 kubeconfig를 생성하는가 | 1·2라운드 | ✅ 완료 |
| 5 | Control Plane 구성요소는 실제로 어디에서 실행되는가 | 3라운드 | ✅ 완료 |
| 6 | Static Pod는 일반 Pod와 무엇이 다른가 | 3라운드 | ✅ 완료 |
| 7 | etcd에는 어떤 정보가 저장되는가 | 4라운드 | ✅ 완료 |

진행은 4라운드로 나눈다.

```text
1라운드   /etc/kubernetes 전체 구조 + 인증서 해부      ✅ 2026-08-06
2라운드   kubeconfig 5개 권한 비교                     ✅ 2026-08-06
3라운드   Static Pod 해부 + 3층 실험                   ✅ 2026-08-07
4라운드   etcd 내부 직접 열기                          ✅ 2026-08-08
```

**07 문서 완료.** 남은 질문 8·9는 [08-failure-experiments.md](08-failure-experiments.md)에서 다룬다.

네 라운드가 하나의 흐름으로 이어진다.

```text
1라운드   인증 — 너 누구냐
2라운드   인가 — 너 뭐 할 수 있냐
3라운드   선언형 — 파일이 원본이고 프로세스는 결과다
4라운드   etcd — 그 모든 것이 저장된 단 한 곳

관통선: 인증·인가·선언은 전부 apiserver가 제공하는 것이다.
        etcd에 직접 붙으면 그것이 전부 사라진다.
        그래서 etcd만 CA부터 따로 격리한다.
```

---

# 1라운드 — 디렉터리 구조와 인증서 (2026-08-06)

## 1. 전체 구조

```text
$ sudo tree /etc/kubernetes

/etc/kubernetes
├── admin.conf
├── controller-manager.conf
├── kubelet.conf
├── manifests
│   ├── etcd.yaml
│   ├── kube-apiserver.yaml
│   ├── kube-controller-manager.yaml
│   └── kube-scheduler.yaml
├── pki
│   ├── apiserver.crt
│   ├── apiserver-etcd-client.crt
│   ├── apiserver-etcd-client.key
│   ├── apiserver.key
│   ├── apiserver-kubelet-client.crt
│   ├── apiserver-kubelet-client.key
│   ├── ca.crt
│   ├── ca.key
│   ├── etcd
│   │   ├── ca.crt
│   │   ├── ca.key
│   │   ├── healthcheck-client.crt
│   │   ├── healthcheck-client.key
│   │   ├── peer.crt
│   │   ├── peer.key
│   │   ├── server.crt
│   │   └── server.key
│   ├── front-proxy-ca.crt
│   ├── front-proxy-ca.key
│   ├── front-proxy-client.crt
│   ├── front-proxy-client.key
│   ├── sa.key
│   └── sa.pub
├── scheduler.conf
└── super-admin.conf

4 directories, 31 files
```

**31개 파일이 세 묶음으로 나뉜다.**

| 묶음 | 개수 | 내용 |
|---|---|---|
| kubeconfig | 5 | `admin` / `super-admin` / `kubelet` / `controller-manager` / `scheduler` |
| Static Pod manifest | 4 | `etcd` / `kube-apiserver` / `kube-controller-manager` / `kube-scheduler` |
| PKI | 22 | 인증서·키 20개 + `sa.key` / `sa.pub` |

`pki/etcd/`가 별도 디렉터리인 것이 눈에 띈다. **etcd만 CA가 분리되어 있기 때문**이다.

## 2. 인증서 만료 현황

```text
$ sudo kubeadm certs check-expiration

CERTIFICATE                EXPIRES                  RESIDUAL TIME   CERTIFICATE AUTHORITY
admin.conf                 Aug 03, 2027 08:11 UTC   362d            ca
apiserver                  Aug 03, 2027 08:11 UTC   362d            ca
apiserver-etcd-client      Aug 03, 2027 08:11 UTC   362d            etcd-ca
apiserver-kubelet-client   Aug 03, 2027 08:11 UTC   362d            ca
controller-manager.conf    Aug 03, 2027 08:11 UTC   362d            ca
etcd-healthcheck-client    Aug 03, 2027 08:11 UTC   362d            etcd-ca
etcd-peer                  Aug 03, 2027 08:11 UTC   362d            etcd-ca
etcd-server                Aug 03, 2027 08:11 UTC   362d            etcd-ca
front-proxy-client         Aug 03, 2027 08:11 UTC   362d            front-proxy-ca
scheduler.conf             Aug 03, 2027 08:11 UTC   362d            ca
super-admin.conf           Aug 03, 2027 08:11 UTC   362d            ca

CERTIFICATE AUTHORITY   EXPIRES                  RESIDUAL TIME
ca                      Jul 31, 2036 08:11 UTC   9y
etcd-ca                 Jul 31, 2036 08:11 UTC   9y
front-proxy-ca          Jul 31, 2036 08:11 UTC   9y
```

### 발견 1 — apiserver는 상대에 따라 다른 CA의 인증서를 쓴다

`CERTIFICATE AUTHORITY` 열을 보면 갈린다.

```text
apiserver                  ca          클러스터 CA
apiserver-kubelet-client   ca          클러스터 CA
apiserver-etcd-client      etcd-ca     ← 다른 CA
```

**같은 apiserver 프로세스인데 신분증이 여러 개다.**

```text
클라이언트·노드를 상대할 때   클러스터 CA 소속
etcd를 상대할 때             etcd CA 소속
```

etcd 입장에서는 "클러스터 CA"라는 것을 아예 모른다. `etcd-ca`가 서명한 인증서만 받아들인다.

**CA 분리가 실제로 작동하는 방식이다.** etcd CA가 유출되어도 노드 인증서를 위조할 수 없고, 클러스터 CA가 유출되어도 etcd에 직접 붙을 수 없다. 침해 범위가 격리된다.

### 발견 2 — CA는 10년, 나머지는 1년

```text
일반 인증서   Aug 03, 2027    362d
CA           Jul 31, 2036    9y
```

10배 차이이며 이유가 명확하다.

| | 유효기간 | 갱신 비용 |
|---|---|---|
| **CA** | 10년 | **클러스터 전면 재구축** — 모든 노드에 새 `ca.crt` 배포 + 전체 인증서 재발급 |
| **일반 인증서** | 1년 | CA만 있으면 재발급. `kubeadm certs renew` |

PKI의 기본 설계 원칙이다.

```text
상위(CA)로 갈수록   길게 — 교체가 어려우므로
하위로 갈수록       짧게 — 유출 시 피해 기간을 줄이려고
```

다만 10년도 언젠가 온다. 그 시점에는 CA 갱신이라는 큰 작업이 필요하며, 실무에서 오래된 클러스터를 쉽게 손대지 못하는 이유 중 하나다.

### 발견 3 — kubelet.conf가 목록에 없다

```text
admin.conf              목록에 있음
controller-manager.conf 목록에 있음
scheduler.conf          목록에 있음
super-admin.conf        목록에 있음
kubelet.conf            없음        ★
```

**kubeadm의 관리 대상이 아니기 때문이다.**

kubelet 인증서는 자동 갱신되며, `kubelet.conf`는 인증서를 내장하지 않고 파일 경로만 참조한다.

```text
admin.conf     client-certificate-data: LS0tLS1CRUdJTi...   내용을 내장
kubelet.conf   client-certificate: /var/lib/kubelet/pki/... 경로만 참조
```

갱신할 때 새 파일을 만들고 심볼릭 링크만 바꾸면 되므로 kubeadm이 개입할 필요가 없다.

## 3. CA 3개는 전부 self-signed다

```text
$ for ca in ca.crt front-proxy-ca.crt etcd/ca.crt; do
    openssl x509 -in /etc/kubernetes/pki/$ca -noout -subject -issuer
  done

subject=CN = kubernetes        issuer=CN = kubernetes
subject=CN = front-proxy-ca    issuer=CN = front-proxy-ca
subject=CN = etcd-ca           issuer=CN = etcd-ca
```

**셋 다 `subject == issuer`다.** 자기가 자기를 서명했다는 뜻이며, 위에 아무도 없는 **신뢰의 뿌리(root of trust)** 다.

그리고 CN이 전부 다르다. **서로 아무 관계가 없는 독립된 세 개의 루트**다.

## 4. apiserver 인증서 상세

```text
$ sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text

Issuer: CN = kubernetes                    ← CA가 서명
Subject: CN = kube-apiserver

Not Before: Aug  3 08:06:53 2026 GMT       ← 발급(08:11)보다 5분 이전
Not After : Aug  3 08:11:53 2027 GMT

X509v3 Key Usage: critical
    Digital Signature, Key Encipherment
X509v3 Extended Key Usage:
    TLS Web Server Authentication          ← 서버용
X509v3 Basic Constraints: critical
    CA:FALSE                               ← 서명 권한 없음

X509v3 Subject Alternative Name:
    DNS:kubernetes, DNS:kubernetes.default, DNS:kubernetes.default.svc,
    DNS:kubernetes.default.svc.cluster.local, DNS:master01,
    IP Address:10.96.0.1, IP Address:192.168.8.143
```

**5분 백데이팅이 확인된다.** `Not Before`가 발급 시각보다 5분 이르다. 노드 간 시계 오차를 허용하기 위한 것이며, 시계가 5분 이상 어긋나면 `certificate is not yet valid`로 실패한다.

SAN에 7개(DNS 5 + IP 2)가 들어 있다. **SAN은 목록이므로 IP 하나만 넣어야 하는 제약이 없다.** `--apiserver-cert-extra-sans`로 추가할 수 있다.

`IP Address:10.96.0.1`은 Service 대역의 첫 IP이자 `kubernetes` 기본 Service의 ClusterIP다. Pod 안에서 apiserver에 접근할 때 쓰는 주소라 미리 넣어둔다.

## 5. 서버용과 클라이언트용은 EKU가 구분한다

```text
$ sudo openssl x509 -in /etc/kubernetes/pki/apiserver-kubelet-client.crt -noout -text

Issuer: CN = kubernetes
Subject: CN = kube-apiserver-kubelet-client

X509v3 Extended Key Usage:
    TLS Web Client Authentication          ← 클라이언트용
X509v3 Basic Constraints: critical
    CA:FALSE
```

같은 CA가 서명했는데 용도가 다르다.

```text
apiserver.crt                  TLS Web Server Authentication    남이 나에게 올 때
apiserver-kubelet-client.crt   TLS Web Client Authentication    내가 남에게 갈 때
```

**`Extended Key Usage` 한 필드가 역할을 결정한다.**

### apiserver 이름이 붙은 인증서가 3개인 이유

```text
apiserver.crt                  나는 서버다               (누가 6443으로 올 때 제시)
apiserver-kubelet-client.crt   나는 kubelet의 클라이언트다  (kubectl logs/exec의 역방향)
apiserver-etcd-client.crt      나는 etcd의 클라이언트다     (모든 읽기/쓰기)
```

**하나의 프로세스가 상대에 따라 다른 신분증을 꺼내 쓴다.** 사원증·운전면허증·여권을 상황에 따라 쓰는 것과 같다.

## 6. CA 여부는 인증서 내용이 결정한다

```text
$ sudo openssl x509 -in /etc/kubernetes/pki/ca.crt -noout -text \
    | grep -A3 'Basic Constraints\|X509v3 Key Usage'
X509v3 Key Usage: critical
    Digital Signature, Key Encipherment, Certificate Sign
                                        ^^^^^^^^^^^^^^^^ 서명 권한
X509v3 Basic Constraints: critical
    CA:TRUE
X509v3 Subject Key Identifier:
    63:AD:A4:42:EA:D6:34:66:60:94:28:DE:F6:1C:BC:FF:08:EE:53:2B

$ sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text \
    | grep -A3 'Basic Constraints\|X509v3 Key Usage'
X509v3 Key Usage: critical
    Digital Signature, Key Encipherment          ← Certificate Sign 없음
X509v3 Extended Key Usage:
    TLS Web Server Authentication
X509v3 Basic Constraints: critical
    CA:FALSE
X509v3 Authority Key Identifier:
    63:AD:A4:42:EA:D6:34:66:60:94:28:DE:F6:1C:BC:FF:08:EE:53:2B
```

`Subject Key Identifier`(ca.crt)와 `Authority Key Identifier`(apiserver.crt)가 **완전히 같다.** 전자는 "나의 지문", 후자는 "나를 서명한 자의 지문"이다. `Issuer: CN`은 이름이라 겹칠 수 있으므로, 검증하는 쪽이 CA를 정확히 지목하는 근거가 된다. CA 교체 과도기에 옛/새 CA를 구분하는 것도 이 값이다.

**차이는 두 필드뿐이다.**

```text
CA:TRUE            "나는 CA다"
Certificate Sign   "다른 인증서를 서명할 수 있다"
```

**파일 이름이 `ca.crt`라서 CA인 것이 아니라, 인증서 안에 그렇게 적혀 있어서 CA다.**

`critical` 표시가 붙어 있으므로 검증하는 쪽이 이 필드를 **반드시** 확인해야 한다. `apiserver.crt`로 다른 인증서를 서명하려 하면 검증 단계에서 거부된다.

## 7. sa는 인증서가 아니다

```text
$ sudo ls -la /etc/kubernetes/pki/sa.*
-rw------- 1 root root 1679  sa.key
-rw------- 1 root root  451  sa.pub

$ sudo openssl x509 -in /etc/kubernetes/pki/sa.pub -noout -subject
Could not read certificate from /etc/kubernetes/pki/sa.pub
error:1608010C:STORE routines:ossl_store_handle_load_result:unsupported

$ sudo openssl rsa -pubin -in /etc/kubernetes/pki/sa.pub -noout -text
Public-Key: (2048 bit)
Modulus:
    00:e3:75:0c:ca:3a:2c:cb:83:8a:55:67:58:1f:ca:...
```

**x509로 읽기가 실패하는 것 자체가 증거다.** `sa.pub`은 X.509 인증서가 아니라 순수 RSA 공개키다.

크기도 차이를 보여준다.

```text
kubelet.crt   2270 bytes   발급자, 유효기간, SAN, 확장필드, 서명이 전부 들어감
sa.pub         451 bytes   공개키 숫자만
```

> `ca.crt` 자체의 크기는 측정하지 않았다. 위는 같은 시점에 측정한
> `/var/lib/kubelet/pki/kubelet.crt`와의 비교다.

이 키 쌍은 **ServiceAccount 토큰(JWT) 서명**에 쓰인다. Pod 안의 애플리케이션이 apiserver에 접근할 때는 인증서가 아니라 이 JWT를 쓴다. Pod마다 인증서를 발급하는 것은 비현실적이기 때문이다.

**즉 클러스터에는 두 개의 인증 체계가 공존한다.**

```text
X.509 인증서   노드, 관리자, Control Plane 컴포넌트
JWT (sa.key)   Pod 안의 애플리케이션
```

## 8. 서버와 클라이언트는 고정 역할이 아니다

master01의 kubelet 신원을 보면 worker와 동일하다.

```text
$ sudo openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -noout -subject
subject=O = system:nodes, CN = system:node:master01
```

**master01도 클러스터 안에서는 하나의 노드일 뿐이다.** 그룹도 `system:nodes`로 같고 이름만 다르다. master01이 특별한 이유는 Control Plane 컨테이너가 거기서 돌기 때문이며, 그것들은 별도 인증서를 쓴다. **역할이 신원에 섞여 있지 않다.**

그런데 kubelet 설정을 보면 kubelet도 서버 노릇을 한다.

```yaml
$ grep -A6 'authentication:' /var/lib/kubelet/config.yaml
authentication:
  anonymous:
    enabled: false        ← 익명 접근 차단
  webhook:
    enabled: true
  x509:                   ← 클라이언트 인증서로 검증

$ grep -A3 'authorization:' /var/lib/kubelet/config.yaml
authorization:
  mode: Webhook
```

**인증·인가 설정을 갖는 것은 서버만 하는 일이다.**

```text
[방향 1]  kubelet ──> apiserver:6443
          클라이언트 kubelet      (O=system:nodes, CN=system:node:master01)
          서버      apiserver    (CN=kube-apiserver)

[방향 2]  apiserver ──> kubelet:10250
          클라이언트 apiserver    (CN=kube-apiserver-kubelet-client)
          서버      kubelet      (kubelet.crt)
          ^^^^^^^^^^^^^^^^^^^^^ 역할이 뒤바뀐다
```

**누가 먼저 말을 거느냐에 따라 서버/클라이언트가 바뀐다.** 그래서 양쪽 모두 두 벌씩 갖고 있다.

```text
$ ls -la /var/lib/kubelet/pki/
-rw-------  1 root root 2822  kubelet-client-2026-08-03-17-11-55.pem   클라이언트용
lrwxrwxrwx  1 root root   59  kubelet-client-current.pem -> (위 파일)
-rw-r--r--  1 root root 2270  kubelet.crt                              서버용
-rw-------  1 root root 1675  kubelet.key                              서버용 개인키
```

### 발견 — 두 방향이 대칭은 아니다 (네 번째 CA)

서버용 인증서를 누가 서명했는지 확인했다.

```text
$ sudo openssl x509 -in /var/lib/kubelet/pki/kubelet.crt -noout -subject -issuer
subject=CN = master01@1785744715
issuer=CN = master01-ca@1785744715
```

`master01-ca`는 `/etc/kubernetes/pki`에도, `kubeadm certs check-expiration` 목록에도 없다. **kubelet이 기동할 때 자기 CA를 즉석에서 만들어 자기 서버 인증서를 서명한 것**이다(이름의 타임스탬프가 둘 다 같다).

배포된 적이 없는 CA이므로 아무도 검증할 수 없다. apiserver 설정을 보면 실제로 검증하지 않는다.

```text
$ grep kubelet /etc/kubernetes/manifests/kube-apiserver.yaml
- --kubelet-client-certificate=/etc/kubernetes/pki/apiserver-kubelet-client.crt
- --kubelet-client-key=/etc/kubernetes/pki/apiserver-kubelet-client.key
- --kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname

$ grep -i 'serverTLSBootstrap\|rotateCertificates' /var/lib/kubelet/config.yaml
rotateCertificates: true
```

세 줄 모두 **apiserver가 자기를 증명하는 도구**다. 상대를 검증하는 `--kubelet-certificate-authority`는 **없다**. kubelet 쪽 `serverTLSBootstrap`도 출력에 없으므로 기본값 `false`다(`rotateCertificates`는 클라이언트 인증서 갱신용으로 별개).

```text
[방향 1] kubelet → apiserver:6443
  양쪽 다 검증. 완전한 mTLS

[방향 2] apiserver → kubelet:10250
  kubelet이 apiserver를 검증      O  ca.crt로
  apiserver가 kubelet을 검증      X  검증할 CA가 없음
```

**열린 칸은 하나뿐이다.** kubelet에 아무나 접속하는 것은 여전히 막혀 있다(`anonymous.enabled: false` + x509 + Webhook). 노출되는 것은 "중간자가 가짜 kubelet을 세웠을 때 apiserver가 구분하지 못한다" 하나이며, 중간자 위치를 먼저 잡아야 성립한다.

**이것은 kubeadm 기본값이며 구성 실수가 아니다.** 서버 CSR은 노드가 자기 주소(SAN)를 주장하는 것이라 자동 승인할 수 없고, 켜려면 운영자가 CSR을 승인해야 한다. kubeadm은 그 부담을 기본으로 지우지 않는다.

```text
해결책   kubelet:   serverTLSBootstrap: true
         apiserver: --kubelet-certificate-authority=/etc/kubernetes/pki/ca.crt
대가     노드 추가 시마다 서버 CSR 승인 필요
판단     지금 단계에서는 조치하지 않는다. 하드닝 단계에서 다룬다.
```

> `serverTLSBootstrap`과 서버 CSR 자동 승인 정책은 공식 문서 확인이 필요하다.

파일 권한이 `.crt`와 `.key`의 성격을 보여준다.

```text
kubelet.crt   0644   접속할 때마다 상대에게 건네주는 것이므로 숨길 이유가 없다
kubelet.key   0600   본인만
```

`kubelet-client-current.pem`이 `0600`이고 크기가 큰 이유는 **인증서와 개인키가 한 파일에** 들어 있기 때문이다. 자동 갱신 시 파일 하나만 새로 쓰고 심볼릭 링크를 옮기면 되도록 묶어둔 것이다(2절의 발견 3).

### `anonymous.enabled: false`가 중요한 이유

kubelet의 10250 포트는 위험한 API를 노출한다.

```text
/exec   컨테이너 안에서 임의 명령 실행
/run    컨테이너 실행
/logs   로그 조회
```

**인증 없이 열려 있으면 그 노드의 모든 컨테이너를 장악할 수 있다.** 과거 실제 침해 사례가 있었고 그래서 지금은 기본값이 `false`다.

### `authorization.mode: Webhook` — 순환 구조

kubelet이 인가 판단을 스스로 하지 않고 apiserver에 위임한다.

```text
1. apiserver ──> kubelet     "이 Pod 로그 줘"          apiserver가 클라이언트
2. kubelet ──> apiserver     "이 사용자 허용해도 되나?"  kubelet이 클라이언트  ★
3. apiserver ──> kubelet     "RBAC 확인. 허용"
4. kubelet ──> apiserver     로그 전송
```

**`kubectl logs` 한 번에 역할이 두 번 바뀐다.**

이렇게 하는 이유는 **인가 규칙의 단일 출처를 유지하기 위해서**다. kubelet이 RBAC 규칙을 따로 들고 있으면 노드마다 동기화 문제가 생긴다. 권한 정보는 etcd 한 곳에만 두고, 판단이 필요할 때마다 물어본다.

## 9. 세 번째 CA는 준비만 되어 있다

`front-proxy-ca`는 확장 API 서버(Aggregated API Server)에 신원을 넘길 때 쓴다. apiserver가 사용자를 인증한 뒤 그 결과를 HTTP 헤더로 전달하고, 헤더를 붙일 자격을 이 CA로 증명하는 구조다.

```text
$ grep 'front-proxy\|requestheader' /etc/kubernetes/manifests/kube-apiserver.yaml
- --proxy-client-cert-file=/etc/kubernetes/pki/front-proxy-client.crt
- --proxy-client-key-file=/etc/kubernetes/pki/front-proxy-client.key
- --requestheader-allowed-names=front-proxy-client
- --requestheader-client-ca-file=/etc/kubernetes/pki/front-proxy-ca.crt
- --requestheader-extra-headers-prefix=X-Remote-Extra-
- --requestheader-group-headers=X-Remote-Group
- --requestheader-username-headers=X-Remote-User
```

### 발견 1 — 검증이 2중이다

```text
--requestheader-client-ca-file   1차: front-proxy-ca가 서명했는가
--requestheader-allowed-names    2차: CN이 정확히 front-proxy-client인가
```

CA만 확인하면 그 CA가 발급한 **다른** 인증서로도 헤더를 위조할 수 있다. `X-Remote-User`를 붙일 수 있다는 것은 아무 신원이나 주장할 수 있다는 뜻이므로 이름까지 못 박는다.

### 발견 2 — 배포 준비는 네 겹인데 쓰는 쪽이 없다

```text
$ kubectl get clusterrole system:auth-delegator -o yaml
rules:
- apiGroups: [authentication.k8s.io]
  resources: [tokenreviews]            verbs: [create]   인증 위임
- apiGroups: [authorization.k8s.io]
  resources: [subjectaccessreviews]    verbs: [create]   인가 위임
metadata:
  annotations:
    rbac.authorization.kubernetes.io/autoupdate: "true"
  resourceVersion: "86"
  creationTimestamp: "2026-08-03T08:12:01Z"      ← 클러스터 기동 8초 뒤

$ kubectl get cm extension-apiserver-authentication -n kube-system \
    -o jsonpath='{.data}' | tr ',' '\n' | cut -c1-80
{"client-ca-file":"-----BEGIN CERTIFICATE-----\nMIIDBTCCAe2gAwIBAgIIKRxCjiwH7E4w
"requestheader-allowed-names":"[\"front-proxy-client\"]"
"requestheader-client-ca-file":"-----BEGIN CERTIFICATE-----\nMIIDETCCAfmgAwIBAgI
"requestheader-extra-headers-prefix":"[\"X-Remote-Extra-\"]"
"requestheader-group-headers":"[\"X-Remote-Group\"]"
"requestheader-username-headers":"[\"X-Remote-User\"]"}

$ kubectl get clusterrolebinding -o wide | grep auth-delegator
(출력 없음)

$ kubectl get apiservice | grep -v Local
NAME   SERVICE   AVAILABLE   AGE
```

```text
CA 파일 4개                                    있음
apiserver 설정 7줄                             있음
system:auth-delegator ClusterRole              있음 (부트스트랩 때 생성)
extension-apiserver-authentication ConfigMap   있음
        ↓
그 권한을 받은 바인딩                          없음
확장 API 서버                                  없음
```

**설치 시점에 미리 만들어두는 이유**는 나중에 추가하려면 apiserver 설정 변경 + 재시작이 필요하기 때문이다. 비용은 파일 4개이고, 안 쓰면 아무 일도 일어나지 않는다.

### 발견 3 — ConfigMap에 CA가 두 개다

```text
client-ca-file                 MIIDBTCC...   클러스터 CA
requestheader-client-ca-file   MIIDETCC...   front-proxy CA
```

`client-ca-file` 값은 `/etc/kubernetes/pki/ca.crt`와 앞부분이 완전히 일치한다(`MIIDBTCCAe2gAwIBAgIIKRxCjiwH7E4w...`). 확장 서버가 요청을 두 경로로 받을 수 있기 때문이다.

```text
apiserver 프록시 경유   front-proxy CA로 검증 → X-Remote-* 헤더를 신뢰
직접 접속               클러스터 CA로 검증    → 그 인증서의 Subject를 사용
어느 쪽도 아님          익명 취급 (헤더는 무시됨)
```

> 5단계에서 `metrics-server`를 설치하면 이 준비물들이 처음으로 쓰인다.
> 위 출력이 그때의 대조군이다.

## 1라운드 정리

```text
 1. CA는 3개이며 전부 self-signed. 서로 아무 관계가 없다
 2. self-signed가 신뢰되는 이유는 서명이 아니라 배포 때문이다
    각 노드 디스크의 ca.crt가 신뢰 기준이며, join 시 해시로 확인하고 받아온 것
 3. apiserver는 상대에 따라 다른 CA의 인증서를 쓴다 (etcd 상대는 etcd-ca)
    CA 분리의 목적은 침해 격리다
 4. CA는 10년, 나머지는 1년. 교체 비용이 다르기 때문
 5. kubelet.conf는 kubeadm 관리 대상이 아니다 (자동 갱신, 경로 참조 방식)
 6. 서버용/클라이언트용은 Extended Key Usage가 구분한다
 7. CA 여부는 Basic Constraints(CA:TRUE) + Key Usage(Certificate Sign)가 결정한다
    SKI/AKI가 "누가 서명했는지"를 이름이 아닌 지문으로 지목한다
 8. SAN은 목록이다. IP를 여러 개 넣을 수 있고 LB 주소도 여기 들어간다
 9. sa.key/sa.pub은 인증서가 아니다. JWT 서명용이며 별개의 인증 체계다
10. 서버·클라이언트는 고정 역할이 아니라 통신 방향에 따라 바뀐다
    다만 두 방향이 대칭은 아니다. apiserver → kubelet 방향은 서버 검증이 생략된다
    kubelet 서버 인증서는 kubelet이 만든 자기 CA(master01-ca)가 서명했고,
    apiserver에 --kubelet-certificate-authority가 설정되어 있지 않다
    kubeadm 기본값이며, 서버 CSR을 자동 승인할 수 없기 때문이다
11. kubelet의 10250은 /exec, /run을 노출하므로 anonymous.enabled: false가 중요하다
12. kubelet은 인가를 스스로 판단하지 않고 apiserver에 위임한다 (Webhook)
    인가 규칙의 단일 출처를 유지하기 위해서다
13. front-proxy-ca는 "남의 신원을 대신 주장할" 자격을 관리하는 별도 CA다
    현재는 준비만 되어 있고 쓰는 쪽이 없다
```

### 미확인

```text
1. apiserver의 CRL/OCSP 미지원 — 개별 무효화 명령이 없다는 것은 확실
2. 확장 서버가 헤더를 읽고 검증하는 실제 동작 — 5단계에서 관찰 가능
3. EKU 위반 시 실제로 거부되는지 — 시도한 적 없음
4. serverTLSBootstrap을 켰을 때 서버 CSR이 자동 승인되지 않는다는 것 — 공식 문서
5. master01-ca 인증서가 디스크에 파일로 남는지 — 확인하지 않음
```

### 알려진 갭 (조치하지 않음)

```text
apiserver → kubelet 방향의 서버 신원 확인이 생략되어 있다.
kubeadm 기본값이며 구성 실수가 아니다.
VM 3대 폐쇄 네트워크이므로 실질 위험이 없다고 판단해 지금은 두고,
하드닝을 다룰 때 serverTLSBootstrap과 함께 다시 본다.
```

> 개념 설명과 Q&A는 `작업다이어리/01.local-k8s-cluster/2026-08-06 작업노트` 참조.
> 이 문서는 명령·출력·검증 결과만 남긴다.

---

# 2라운드 — kubeconfig 5개 권한 비교 (2026-08-06)

1라운드에서 인증서는 신원만 담는다는 것을 확인했다. 그렇다면 권한은 어디에 있는가.

```text
질문: 같은 CA가 서명했는데 왜 권한이 다른가
답:   인증서는 "누구인가"만 말한다.
      "무엇을 할 수 있는가"는 클러스터 안의 별도 오브젝트가 정한다.
```

## 1. kubeconfig 5개의 신원

```text
$ for f in admin super-admin controller-manager scheduler; do
    grep 'client-certificate-data' /etc/kubernetes/$f.conf \
      | awk '{print $2}' | base64 -d | openssl x509 -noout -subject
  done
$ openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -noout -subject

admin.conf                O = kubeadm:cluster-admins, CN = kubernetes-admin
super-admin.conf          O = system:masters,         CN = kubernetes-super-admin
controller-manager.conf                               CN = system:kube-controller-manager
scheduler.conf                                        CN = system:kube-scheduler
kubelet.conf              O = system:nodes,           CN = system:node:master01
```

kubelet만 명령이 다르다. `kubelet.conf`는 인증서를 내장하지 않고 경로만 참조하기 때문이다(1라운드 발견 3).

### 발견 — 그룹이 있는 것과 없는 것이 갈린다

```text
그룹(O)이 있는 것   관리자 2개, 노드
CN만 있는 것        controller-manager, scheduler
```

| 대상 | 개수 | 그룹 |
|---|---|---|
| 노드 | 수십~수백 대로 늘어남 | `system:nodes` — 규칙을 한 번만 쓴다 |
| 관리자 | 여러 명 | `kubeadm:cluster-admins` |
| scheduler / controller-manager | 클러스터에 하나 | 없음 — CN을 직접 지목 |

**그룹은 여러 주체에 같은 규칙을 적용할 때 쓰는 장치다.** 대상이 하나뿐이면 필요 없다.

## 2. admin과 super-admin의 차이

```text
$ diff <(grep -v 'certificate-data\|key-data' /etc/kubernetes/admin.conf) \
       <(grep -v 'certificate-data\|key-data' /etc/kubernetes/super-admin.conf)

<     user: kubernetes-admin
>     user: kubernetes-super-admin
```

> `sudo`를 붙이면 `/dev/fd/63: No such file or directory`로 실패한다. sudo가 표준 입출력이 아닌 파일 디스크립터를 닫기 때문이다. 이미 root면 sudo 없이 실행한다.

### 발견 1 — 파일 차이는 사용자 이름 한 줄뿐이다

```text
같은 것   CA, 서버 주소, 클러스터 이름
다른 것   사용자 이름
```

권한 목록도 같다(3 참조). 둘 다 `*.* [*]`다.

### 발견 2 — 진짜 차이는 자동 복구 여부다

```text
$ kubectl get clusterrolebinding -o wide | grep -i 'super\|system:masters'
cluster-admin   ClusterRole/cluster-admin   2d16h   system:masters
```

```text
[cluster-admin]                        [kubeadm:cluster-admins]
annotations:                           (어노테이션 없음)
  rbac.authorization.kubernetes.io/
    autoupdate: "true"

→ apiserver 재시작 시 자동 복구        → 지우면 그걸로 끝
```

`system:masters`는 apiserver 부트스트랩 정책으로 보호되며 RBAC를 우회한다.

**데드락 시나리오가 이 설계의 이유다.**

```text
kubeadm:cluster-admins 바인딩을 실수로 삭제
  → admin.conf로 아무것도 못 함
  → 권한이 없으니 바인딩을 복구할 수도 없음
  → 클러스터를 고칠 방법이 없음

super-admin.conf 사용
  → system:masters는 여전히 동작 → 복구 가능
```

| | 용도 | 권한 출처 | 회수 |
|---|---|---|---|
| `admin.conf` | 평소 작업 | RBAC(ClusterRoleBinding) | 가능 |
| `super-admin.conf` | 비상 복구 | 부트스트랩 정책(RBAC 우회) | 불가 |

kubeadm이 `admin.conf`를 `system:masters`에서 빼고 별도 그룹으로 옮긴 이유다. 일상 작업에 최고 권한 계정을 쓰지 않게 하려는 것이다.

## 3. 실제 권한 비교

`--kubeconfig`로 실제 그 신원이 되어 물어본다. `--as`(impersonation)와 달리 진짜 인증서를 쓴다.

```text
$ kubectl auth can-i --list --kubeconfig=/etc/kubernetes/<name>.conf

[admin / super-admin]  — 동일
*.*                      [*]

[controller-manager]
secrets                  [create delete get update]
serviceaccounts          [create get update]
serviceaccounts/token    [create]          ← 토큰 발급
events                   [create patch update]

[scheduler]
bindings                 [create]          ← Pod를 노드에 묶음
pods/binding             [create]
pods                     [delete get list watch]
persistentvolumeclaims   [get list patch update watch]
persistentvolumes        [get list patch update watch]
nodes                    [get list watch]
```

### 발견 1 — 하는 일이 권한에 그대로 드러난다

```text
scheduler는 Secret을 못 읽는다
controller-manager는 Pod를 노드에 못 묶는다
```

최소 권한 원칙이 실제로 적용되어 있다. 컴포넌트가 뚫려도 그 컴포넌트가 할 수 있는 일 이상은 못 한다.

### 발견 2 — "Pod를 노드에 배치한다"의 실체

scheduler 권한에 `pods [update]`나 `pods [patch]`가 **없다.** 대신 이것이 있다.

```text
bindings       [create]
pods/binding   [create]
```

**scheduler는 Pod를 수정하지 않는다. binding이라는 별도 오브젝트를 만든다.**

```text
scheduler:  "Pod nginx와 Node worker01을 묶는 binding을 만들어라"
apiserver:  binding을 받아 Pod의 spec.nodeName을 채운다
```

추상적인 "배치"가 오브젝트 생성이라는 구체적 API 호출로 표현되어 있다. 이 `spec.nodeName`이 6절의 그래프에서 다시 나온다.

## 4. 인증서와 권한을 잇는 것 — ClusterRoleBinding

```text
$ kubectl get clusterrolebinding -o custom-columns=\
  'NAME:.metadata.name,ROLE:.roleRef.name,SUBJECTS:.subjects[*].name'

cluster-admin                     cluster-admin              system:masters
kubeadm:cluster-admins            cluster-admin              kubeadm:cluster-admins
kubeadm:apiserver-kubelet-client  system:kubelet-api-admin   kube-apiserver-kubelet-client
kubeadm:node-autoapprove-...      ...:selfnodeclient         system:nodes
kubeadm:node-proxier              system:node-proxier        kube-proxy
system:controller:*               system:controller:*        (40여 개)
```

### 발견 1 — 연결은 3단이다

```text
인증서의 Subject
  O  = kubeadm:cluster-admins
  CN = kubernetes-admin
        │  apiserver가 인증 후 이 문자열로 신원 확정
        ▼
ClusterRoleBinding "kubeadm:cluster-admins"
  subjects: Group kubeadm:cluster-admins   ← 인증서의 O와 매칭
  roleRef:  ClusterRole cluster-admin
        ▼
ClusterRole "cluster-admin"
  rules: [모든 리소스, 모든 동작]
        ▼
      모든 권한
```

**왜 분리했는가** — 인증서는 발급 후 만료까지 못 바꾼다. 권한은 언제든 바꿔야 한다. 그래서 둘을 떼어놓고 중간에 바인딩을 뒀다. 인증서는 그대로 두고 바인딩만 바꾸면 권한이 달라진다.

### 발견 2 — 신원 하나에 바인딩이 여러 개 붙는다

```text
$ kubectl get clusterrolebinding -o wide \
    | grep 'kube-controller-manager\|kube-scheduler'

calico-tier-getter               ClusterRole/calico-tier-getter               system:kube-controller-manager
system:kube-controller-manager   ClusterRole/system:kube-controller-manager   system:kube-controller-manager
system:kube-scheduler            ClusterRole/system:kube-scheduler            system:kube-scheduler
system:volume-scheduler          ClusterRole/system:volume-scheduler          system:kube-scheduler
```

**3절에서 scheduler에 PV/PVC 권한이 있던 이유가 여기 있다.**

```text
system:kube-scheduler     Pod 배치 (bindings, pods, nodes)
system:volume-scheduler   볼륨 관련 (PV, PVC)
```

Pod를 어느 노드에 놓을지 정하려면 그 노드에서 볼륨을 붙일 수 있는지도 봐야 한다. 특정 AZ에만 있는 EBS 볼륨이라면 그 AZ의 노드에만 배치할 수 있다. 역할이 다르므로 ClusterRole도 나눠져 있다.

### 발견 3 — 서드파티가 기존 신원에 권한을 얹는다

```text
calico-tier-getter   ClusterRole/calico-tier-getter   system:kube-controller-manager
```

kubeadm이 만든 게 아니다. **Calico가 설치되면서 controller-manager의 신원에 권한을 덧붙인 것이다.**

발견 1의 "왜 분리했는가"에 대한 실물 증거다.

```text
[인증서에 권한이 들어있었다면]
  Calico 설치 → controller-manager 인증서 재발급 → CA 서명 → 재시작 → 중단

[실제 — 분리되어 있으므로]
  Calico 설치 → ClusterRoleBinding 하나 추가 → 끝
```

## 5. Pod의 신원 — 네 번째 인증 방식

여기까지는 전부 X.509였다. Pod 안의 애플리케이션에는 인증서가 없다.

```text
$ kubectl -n kube-system get pod calico-node-5khhz \
    -o jsonpath='{.spec.volumes[?(@.projected)].projected.sources[*]}'

{"serviceAccountToken":{"expirationSeconds":3607,"path":"token"}}
{"configMap":{"items":[{"key":"ca.crt","path":"ca.crt"}],"name":"kube-root-ca.crt"}}
{"downwardAPI":{"items":[{"fieldRef":{"fieldPath":"metadata.namespace"},"path":"namespace"}]}}
```

### 발견 1 — 인증 재료가 3종 세트로 주입된다

```text
token       나는 누구인가        ← 인증
ca.crt      상대가 진짜인가      ← apiserver 검증
namespace   나는 어디 소속인가   ← 요청 경로 구성
```

Pod 안에서 `/var/run/secrets/kubernetes.io/serviceaccount/`에 세 파일로 확인된다.

**`ca.crt`가 함께 들어가는 이유가 중요하다.** Pod도 apiserver를 검증해야 한다. worker가 join할 때 CA를 먼저 받아야 했던 것과 같은 구조다. 누구든 apiserver와 통신하려면 CA가 있어야 한다.

### 발견 2 — X.509와 JWT는 구조가 대응한다

```text
"iss": "https://kubernetes.default.svc.cluster.local"
"aud": ["https://kubernetes.default.svc.cluster.local"]
"sub": "system:serviceaccount:kube-system:calico-node"
"iat": 1785974211
"nbf": 1785974211
"exp": 1817510211
"kubernetes.io": {
  "namespace": "kube-system",
  "node": {"name": "master01", "uid": "..."},
  "pod":  {"name": "calico-node-5khhz", "uid": "..."},
  "serviceaccount": {"name": "calico-node", "uid": "..."},
  "warnafter": 1785977818
}
```

| 개념 | X.509 | JWT |
|---|---|---|
| 발급자 | `Issuer: CN` | `iss` |
| 사용자 | `Subject: CN` | `sub` |
| 그룹 | `Subject: O` | 자동 부여 (`system:serviceaccounts` 등) |
| 유효 시작 | `Not Before` | `nbf` |
| 유효 종료 | `Not After` | `exp` |
| 대상 제한 | `SAN` | `aud` |
| 서명 | `ca.key` | `sa.key` |
| 검증 | `ca.crt` | `sa.pub` |

1라운드 발견 7에서 "sa는 인증서가 아니라 JWT 서명용"이라고 확인한 그 키가 여기 쓰인다.

**핵심 — 인증 방식이 달라도 인가는 같은 경로를 탄다.** apiserver 입장에서는 둘 다 "사용자 이름 + 그룹"으로 환원되고, 그다음 RBAC 검사는 동일하다. 로드맵 11단계에서 EKS가 IAM으로 인증하면서도 RBAC를 그대로 쓰는 이유다.

`nbf`에 백데이팅이 없는 것도 차이다. X.509는 5분을 앞당기는데(1라운드 3) JWT는 `iat == nbf`다. apiserver 하나가 발급하고 검증하므로 노드 간 시계 오차가 없기 때문이다.

### 발견 3 — 토큰은 Pod에 묶여 있다 (bound)

```text
"pod":  {"name": "calico-node-5khhz", "uid": "3b4c00f9-..."}
"node": {"name": "master01",          "uid": "3ede84ea-..."}
```

X.509에는 이런 필드가 없다.

```text
인증서   파일만 있으면 어디서든 사용 가능
JWT      발급받은 Pod가 살아 있어야만 유효

토큰 유출 → 그 Pod 삭제 → uid 사라짐 → apiserver가 거부
```

**인증서는 취소 수단이 없다.** 만료일까지 유효하며, 개별 무효화 명령이 없다(CA 교체가 유일한 대응). Pod는 수천 개가 자주 뜨고 죽으므로, 취소 가능한 자격증명이 필요했다.

### 발견 4 — 만료 값이 세 개다

Pod spec에는 `expirationSeconds: 3607`(약 1시간)인데 토큰의 `exp`는 다르다.

```text
exp - iat        = 31,536,000초 = 365일    ← 1년
warnafter - iat  = 3,607초                 ← expirationSeconds와 일치
```

| 값 | 의미 |
|---|---|
| `expirationSeconds` 3607 | Pod가 요청한 **갱신 주기** |
| `warnafter` | "원래는 여기서 만료였어야 함" — 이후 사용 시 apiserver가 기록 |
| `exp` 1년 후 | 실제 **강제 만료** |

**레거시 호환 때문이다.** 옛날 클라이언트 라이브러리들이 토큰 파일을 시작 시 한 번만 읽고 다시 읽지 않았다. 진짜로 1시간에 만료되면 그런 앱들이 전부 죽는다. `exp`를 넉넉히 주되 `warnafter`로 "아직 갱신 안 하는 앱이 있다"를 기록에 남기는 절충안이다.

**"만료 1시간"이 아니라 "갱신 주기 1시간, 강제 만료 1년"이 정확한 표현이다.** kubelet은 여전히 약 1시간마다 파일을 갱신한다.

```text
$ grep -i 'service-account' /etc/kubernetes/manifests/kube-apiserver.yaml
--service-account-issuer=https://kubernetes.default.svc.cluster.local
--service-account-key-file=/etc/kubernetes/pki/sa.pub          ← 검증용 공개키
--service-account-signing-key-file=/etc/kubernetes/pki/sa.key  ← 서명용 개인키
```

이름이 헷갈리는 지점: `--service-account-key-file`이 **공개키**다. 서명용은 `signing-key-file` 쪽이다.

## 6. 인가 모듈은 두 개다

4절에서 "권한은 ClusterRoleBinding이 정한다"고 했는데 절반만 맞다.

```text
$ grep authorization-mode /etc/kubernetes/manifests/kube-apiserver.yaml
--authorization-mode=Node,RBAC
```

### 발견 1 — RBAC로는 "자기 것만"을 표현할 수 없다

kubelet은 자기 노드 Pod가 쓰는 Secret/ConfigMap을 읽어야 한다(5절의 3종 세트를 kubelet이 넣는다). 그런데 RBAC 규칙은 이런 형태다.

```text
resources: ["secrets"]
verbs: ["get"]
→ "모든 Secret을 읽을 수 있다"
```

`resourceNames`로 이름을 지정할 수 있지만 쓸 수 없다. 필요한 목록이 Pod 생성·삭제마다 바뀌기 때문이다.

| 표현 | RBAC |
|---|---|
| "Secret을 읽을 수 있다" | 가능 |
| "tls-cert라는 Secret을 읽을 수 있다" | 가능 |
| "자기 노드 Pod가 쓰는 Secret만" | **불가능** |

**RBAC 문법에 "자기 것"이라는 개념이 없다.** 그래서 규칙 조회가 아니라 관계 계산으로 답하는 모듈을 따로 붙였다.

### 발견 2 — Node Authorizer는 그래프를 탐색한다

apiserver 메모리에 오브젝트 간 관계도가 있다. Pod spec에서 파생된다.

```text
nodeName             Node ── Pod
volumes[].secret     Pod ── Secret
volumes[].configMap  Pod ── ConfigMap
serviceAccountName   Pod ── ServiceAccount
```

```text
Node(worker01)
  ├── Pod(calico-node-bsg58)
  ├── Pod(coredns-...)     └── ConfigMap(coredns)
  └── Pod(kube-proxy-c8rqh) └── ConfigMap(kube-proxy)
```

상태(Running 등)가 아니라 **관계만** 담는다. 원본은 etcd에 있고 이것은 메모리의 색인이다. apiserver 재시작 시 다시 만들어진다.

경로는 **2홉**(Node → Pod → 리소스)까지다. 무제한이면 공유 ServiceAccount를 타고 모든 Secret에 닿는다.

### 발견 3 — 실측으로 확인했다

```text
$ kubectl get pods -A --field-selector spec.nodeName=worker01 \
    -o custom-columns='NS:.metadata.namespace,POD:.metadata.name,VOL:.spec.volumes[*].name'
kube-system   calico-node-bsg58          ...,kube-api-access-n6rm5
kube-system   coredns-7d764666f9-gv4wl   config-volume,kube-api-access-gz59h
kube-system   kube-proxy-c8rqh           kube-proxy,xtables-lock,...,kube-api-access-spq9j

# 1. 그룹 없이
$ kubectl auth can-i get configmap/kube-proxy \
    --as=system:node:worker01 -n kube-system
no

# 2. 그룹 포함
$ kubectl auth can-i get configmap/kube-proxy \
    --as=system:node:worker01 --as-group=system:nodes -n kube-system
yes

# 3. 그룹 포함 + worker01 Pod가 안 쓰는 대상
$ kubectl auth can-i get configmap/kube-root-ca.crt \
    --as=system:node:worker01 --as-group=system:nodes -n default
no - no relationship found between node 'worker01' and this object
```

> 이름은 `resource/name` 형태여야 한다. 세 번째 인자로 쓰면
> `you must specify two arguments: verb resource or verb resource/resourceName`로 실패한다.

세 줄이 각각 다른 것을 증명한다.

| 비교 | 증명하는 것 |
|---|---|
| 1 vs 2 | 대상이 같은데 그룹만 추가하니 `no` → `yes`. **그룹이 담당 여부를 정한다** |
| 2 vs 3 | 사용자·그룹·동작이 같은데 대상만 다르니 결과가 갈림. **대상별로 판단한다** |
| 1 vs 3 | 둘 다 `no`인데 3만 이유를 말함. **개입 안 함 vs 개입해서 못 찾음** |

```text
2.  worker01 → kube-proxy-c8rqh → ConfigMap(kube-proxy)   ✓ 2홉으로 도달
3.  worker01 → (default 네임스페이스엔 Pod 없음)           ✗ 1홉에서 끊김
```

3절의 메시지가 그래프의 직접 증거다.

```text
no relationship found between node 'worker01' and this object
   ^^^^^^^^^^^^                  ^^^^^^^^^^^^     ^^^^^^^^^^^
   관계                          출발점            목적지
```

또한 세 Pod 전부에 `kube-api-access-*`가 붙어 있다. 5절의 3종 세트이며, 명시하지 않아도 kubelet이 모든 Pod에 넣는다.

### 발견 4 — 인가 모듈의 대답은 3가지다

```text
Allow        허용해라
Deny         거부해라
NoOpinion    내 담당이 아니다. 다음 모듈에게
```

2가지면 모듈을 여러 개 못 쓴다. 첫 모듈이 담당 아닌 요청에 `Deny`를 내면 그다음 모듈은 물어보지도 못한다.

```text
--authorization-mode=Node,RBAC

순서대로 물어본다
  먼저 Allow하는 쪽이 이긴다
  Deny가 나오면 즉시 거부
  전부 NoOpinion이면 거부      ← 기본값이 거부
```

**둘 다 통과해야 하는 관문이 아니라, 순서대로 물어보는 답변자 목록이다.**

### 발견 5 — `--list`에 경고가 붙는 이유

```text
$ kubectl auth can-i --list --as=system:node:worker01
Warning: the list may be incomplete:
         node authorizer does not support user rule resolution
```

Node Authorizer에는 나열할 규칙이 애초에 없다. 요청이 와야 그래프를 탐색해 답한다. 그래서 그 출력에는 RBAC 부분만 나오고, 실제 권한보다 짧다.

**같은 이유로 `--list`로는 Node Authorizer를 검증할 수 없다.** 발견 3처럼 대상을 특정해서 물어봐야 한다.

## 7. Pod 안 ca.crt의 출처 확인

5절에서 `kube-root-ca.crt` ConfigMap이 나왔다. 만든 적이 없는데 어디서 왔는가.

```text
$ grep -E 'root-ca-file|service-account' \
    /etc/kubernetes/manifests/kube-controller-manager.yaml
--root-ca-file=/etc/kubernetes/pki/ca.crt
--service-account-private-key-file=/etc/kubernetes/pki/sa.key
--use-service-account-credentials=true

$ kubectl get cm -A | grep kube-root-ca
default           kube-root-ca.crt   1   2d23h
kube-node-lease   kube-root-ca.crt   1   2d23h
kube-public       kube-root-ca.crt   1   2d23h
kube-system       kube-root-ca.crt   1   2d23h

$ kubectl get cm kube-root-ca.crt -n default -o jsonpath='{.data.ca\.crt}' | head -3
$ head -3 /etc/kubernetes/pki/ca.crt
-----BEGIN CERTIFICATE-----
MIIDBTCCAe2gAwIBAgIIKRxCjiwH7E4wDQYJKoZIhvcNAQELBQAwFTETMBEGA1UE   ← 동일
AxMKa3ViZXJuZXRlczAeFw0yNjA4MDMwODA2NTNaFw0zNjA3MzEwODExNTNaMBUx
```

네임스페이스 4개 전부에 있고 내용이 디스크의 `ca.crt`와 같다. 경로가 확정됐다.

```text
/etc/kubernetes/pki/ca.crt          원본 (디스크)
      ↓ controller-manager의 --root-ca-file이 읽음
      ↓ root-ca-cert-publisher 컨트롤러가 배포
kube-root-ca.crt ConfigMap × 4      모든 네임스페이스
      ↓ Pod spec의 projected volume
/var/run/secrets/.../ca.crt         Pod 안
```

`--use-service-account-credentials=true`는 controller-manager 안의 40여 개 컨트롤러가 **각자의 ServiceAccount로** 동작하게 한다. 4절에서 본 `system:controller:*` 바인딩 40여 개의 정체다.

```text
kubeconfig 5개  →  신원 5개   →  권한 5종류
컨트롤러 40개   →  신원 40개  →  권한 40종류    같은 원리, 더 세밀
```

컨트롤러 구조 자체는 08단계에서 controller-manager를 중단시켜볼 때 다시 본다.

## 2라운드 정리

```text
 1. 인증서는 신원만 담고, 권한은 ClusterRoleBinding → ClusterRole이 정한다
 2. 인증서는 못 바꾸지만 바인딩은 바꿀 수 있다. 그래서 분리했다
 3. 신원 하나에 바인딩을 여러 개 붙일 수 있다 (scheduler = 2개)
 4. 서드파티도 기존 신원에 권한을 얹는다 (Calico의 calico-tier-getter)
 5. 그룹(O)은 여러 주체를 묶을 때 쓴다. 하나뿐인 컴포넌트는 CN만 있다
 6. 하는 일이 권한에 드러난다. "배치"의 실체는 binding 오브젝트 생성이다
 7. admin과 super-admin은 권한이 같다. 차이는 복구 가능성(autoupdate)이다
 8. Pod는 인증서가 아니라 JWT를 쓴다. X.509와 구조가 대응한다
 9. 토큰은 Pod에 묶여 있다(uid). 인증서와 달리 취소가 가능하다
10. 토큰 만료는 갱신 주기 1시간 / 강제 만료 1년. 레거시 호환 때문이다
11. 인가 모듈도 하나가 아니다. --authorization-mode=Node,RBAC
12. RBAC는 "자기 것만"을 표현할 수 없어 관계 계산 모듈을 따로 붙였다
13. Pod 안 ca.crt는 root-ca-cert-publisher가 배포한 ConfigMap에서 온다
```

**이 라운드의 결론 — 인증과 인가의 분리**

```text
[인증]  X.509 / JWT / IAM  →  전부 "사용자 이름 + 그룹" 문자열로 환원
[인가]  그 문자열로 판단     →  Node Authorizer → RBAC 순서로 평가
```

인증 단계의 산출물이 문자열 두 개로 통일되므로 인가는 인증 방식을 몰라도 된다. EKS가 IAM으로 인증을 갈아끼워도 RBAC 규칙은 로컬 클러스터와 같은 이유다.

**로드맵 질문 1 답변 완료** — 1라운드에서 인증서 20여 개와 CA 3개, 2라운드에서 kubeconfig 5개의 신원과 권한을 확인했다.

### 미확인

```text
1. --service-account-extend-token-expiration의 정확한 이름과 기본값
   apiserver 플래그에 없다 = 기본값 적용 중. 연장 사실은 exp 값으로 증명됨
2. apiserver의 CRL/OCSP 미지원 — 개별 무효화 명령이 없다는 것은 확실
3. Node Authorizer 그래프의 선 종류 전부 — 4개는 확인, PVC→PV 등이 더 있을 수 있음
```

---

# 3라운드 — Static Pod 해부 (2026-08-07)

`kubeadm init`에서 다룬 부트스트랩 역설을 실물로 확인한다.

```text
Pod를 만들려면 apiserver가 필요하다
그런데 apiserver 자신도 Pod다
      → apiserver를 띄우려면 apiserver가 필요하다
```

## 1. manifest 디렉터리와 kubelet 설정

```text
$ ls /etc/kubernetes/manifests/
etcd.yaml  kube-apiserver.yaml  kube-controller-manager.yaml  kube-scheduler.yaml

$ grep -i staticPodPath /var/lib/kubelet/config.yaml
staticPodPath: /etc/kubernetes/manifests
```

경로가 코드에 박힌 것이 아니라 **kubelet 설정값**이다. 바꿀 수 있고, kubeadm이 init 시점에 넣어준 값이다.

## 2. manifest 하나 열어보기

```text
$ sudo cat /etc/kubernetes/manifests/kube-scheduler.yaml
apiVersion: v1
kind: Pod                              ← Deployment가 아니다
metadata:
  labels:
    component: kube-scheduler
    tier: control-plane
  name: kube-scheduler                 ← 노드명이 없다
  namespace: kube-system
spec:
  containers:
  - command:
    - kube-scheduler
    - --authentication-kubeconfig=/etc/kubernetes/scheduler.conf
    - --authorization-kubeconfig=/etc/kubernetes/scheduler.conf
    - --bind-address=127.0.0.1         ← 루프백에만 바인딩
    - --kubeconfig=/etc/kubernetes/scheduler.conf
    - --leader-elect=true              ← scheduler는 하나뿐인데?
    image: registry.k8s.io/kube-scheduler:v1.35.7
    ...
    volumeMounts:
    - mountPath: /etc/kubernetes/scheduler.conf
      name: kubeconfig
      readOnly: true
  hostNetwork: true                    ← Pod 네트워크를 쓰지 않는다
  priority: 2000001000
  priorityClassName: system-node-critical
  volumes:
  - hostPath:
      path: /etc/kubernetes/scheduler.conf
      type: FileOrCreate
    name: kubeconfig
status: {}                             ← 비어 있다
```

### 발견 1 — 일반 Pod 정의와 다른 점

```text
kind: Pod           ReplicaSet도 Deployment도 없다. Pod 하나의 정의다
name에 노드명 없음   그런데 클러스터에서는 붙어 있다 (발견 2)
nodeName 없음        scheduler가 채워주는 필드인데 없다 (발견 6)
hostNetwork: true    CNI 없이도 뜰 수 있다 (발견 7)
status: {}           상태를 apiserver가 관리하지 않는다는 표시
volumes는 hostPath만 ConfigMap/Secret을 못 쓴다 — apiserver가 필요하므로
```

**`volumes`가 hostPath뿐인 것이 결정적이다.** ConfigMap이나 Secret을 마운트하려면 apiserver에서 받아와야 하는데, apiserver를 띄우는 중이므로 불가능하다. 그래서 kubeconfig도 파일 경로로 직접 붙인다.

## 3. 클러스터에서 보이는 모습

```text
$ kubectl get pods -n kube-system -o wide | grep -E 'apiserver|scheduler|controller-manager|etcd'
etcd-master01                      1/1  Running  1 (3d3h ago)  3d22h  192.168.8.143  master01
kube-apiserver-master01            1/1  Running  2 (31h ago)   3d22h  192.168.8.143  master01
kube-controller-manager-master01   1/1  Running  2             3d22h  192.168.8.143  master01
kube-scheduler-master01            1/1  Running  2             3d22h  192.168.8.143  master01
```

### 발견 2 — 이름에 노드명이 붙는다

```text
[파일]      name: kube-scheduler
[클러스터]  name: kube-scheduler-master01
                              ^^^^^^^^^ kubelet이 붙였다
```

Control Plane을 여러 대로 늘리면 이유가 드러난다.

```text
master01 / master02 / master03 에 같은 kube-scheduler.yaml
→ Pod 이름이 전부 kube-scheduler → apiserver에서 충돌
→ kubelet이 자기 노드명을 붙여 구분
```

**같은 파일을 여러 노드에 그대로 뿌릴 수 있는 구조다.**

## 4. 미러 Pod의 증거

```text
$ kubectl get pod kube-scheduler-master01 -n kube-system -o yaml \
    | grep -B2 -A8 'annotations:\|ownerReferences:'
metadata:
  annotations:
    kubernetes.io/config.hash: dcefdf84232060db703cb0098efa5bf6
    kubernetes.io/config.mirror: dcefdf84232060db703cb0098efa5bf6
    kubernetes.io/config.seen: "2026-08-03T17:12:03.240652323+09:00"
    kubernetes.io/config.source: file
  creationTimestamp: "2026-08-03T08:12:03Z"
  name: kube-scheduler-master01
  ownerReferences:
  - apiVersion: v1
    controller: true
    kind: Node
    name: master01
    uid: 3ede84ea-97ec-47fd-97a7-9f0241db181c
```

### 발견 3 — 소유자가 Node다

일반 Pod는 ReplicaSet이 소유자인데 이것은 **Node**다. "이 Pod는 이 노드가 만든 것"이라는 뜻이다.

`blockOwnerDeletion`이 없는 것도 차이다. 일반 Pod에는 있다(발견 5 표 참조).

### 발견 4 — config.* 어노테이션 네 개

```text
config.hash     manifest 내용의 해시 = 이 Static Pod의 식별자
config.mirror   "이건 미러다" 표시. hash와 같은 값
config.seen     kubelet이 그 파일을 처음 본 시각
config.source   어디서 왔나 — file / http / api
```

**`config.seen`과 `creationTimestamp`가 같은 시각이다.**

```text
config.seen        2026-08-03T17:12:03.240 +09:00
creationTimestamp  2026-08-03T08:12:03Z      = 17:12:03 KST
```

kubelet이 파일을 읽은 그 순간에 apiserver의 Pod 오브젝트가 생겼다. **파일이 먼저고 apiserver 기록이 나중**이라는 순서가 시각으로 확인된다.

`config.hash`는 manifest 내용에서 계산된다. **파일을 수정하면 해시가 바뀌고 kubelet이 그것으로 변경을 감지**한다.

### 발견 5 — Node uid가 2라운드 JWT와 같다

```text
[3라운드 — ownerReferences]
kind: Node, name: master01, uid: 3ede84ea-97ec-47fd-97a7-9f0241db181c

[2라운드 — calico-node의 JWT payload]
"node": { "name": "master01", "uid": "3ede84ea-..." }
```

같은 Node 오브젝트를 두 곳에서 참조한다.

```text
JWT의 node.uid           "이 토큰은 이 노드 위의 Pod 것이다"
Static Pod의 owner uid   "이 Pod는 이 노드가 만든 것이다"
```

**Node 오브젝트가 노드 위 자원들의 소유권 기준점 노릇을 한다.**

## 5. 대조군 — 일반 Pod

```text
$ kubectl get pods -n kube-system -o wide | grep coredns
coredns-7d764666f9-gv4wl  1/1  Running  0             2d1h   10.244.5.6    worker01
coredns-7d764666f9-jhlw8  1/1  Running  1 (3d3h ago)  3d22h  10.244.30.71  worker02

$ kubectl get pod coredns-7d764666f9-gv4wl -n kube-system -o yaml \
    | grep -B2 -A8 'annotations:\|ownerReferences:'
metadata:
  annotations:
    cni.projectcalico.org/containerID: 7b4f952ca9b2...
    cni.projectcalico.org/podIP: 10.244.5.6/32
    cni.projectcalico.org/podIPs: 10.244.5.6/32
  generateName: coredns-7d764666f9-
  name: coredns-7d764666f9-gv4wl
  ownerReferences:
  - apiVersion: apps/v1
    blockOwnerDeletion: true
    controller: true
    kind: ReplicaSet
    name: coredns-7d764666f9
```

| | Static Pod | 일반 Pod |
|---|---|---|
| `ownerReferences` | **Node** master01 | **ReplicaSet** coredns-7d764666f9 |
| `blockOwnerDeletion` | 없음 | `true` |
| `config.source` | `file` | 없음 |
| 이름 | `kube-scheduler-master01` | `coredns-7d764666f9-gv4wl` |
| 이름을 붙인 자 | kubelet | apiserver (`generateName`) |
| IP | `192.168.8.143` (노드 IP) | `10.244.5.6` (Calico IPAM 블록) |
| CNI 어노테이션 | 없음 | Calico가 붙임 |

> **2026-08-11 수정.** IP 열의 라벨이 원래 `(Pod CIDR)`이었으나 부정확하다. `10.244.5.6`은 그 노드의 `node.spec.podCIDR`(`10.244.1.0/24`)이 **아니라** Calico IPAM이 배정한 블록(`10.244.5.0/26`)에서 나온 주소다.
>
> 위 출력의 `10.244.30.71`(worker02)도 마찬가지다. worker02의 `podCIDR`은 `10.244.2.0/24`이므로 `/24`로는 나올 수 없는 값인데, 당시에는 이상하게 보지 않고 지나갔다. **Calico는 `node.spec.podCIDR`을 읽지 않는다**는 것을 2단계에서 실측으로 확인했다. [00-environment.md](00-environment.md)의 "이 값이 나오는 두 곳" 절과 [02.k8s-objects/00-pod.md](../02.k8s-objects/00-pod.md) 참조.

## 6. nodeName은 어디서 오는가

### 발견 6 — Static Pod는 scheduler를 거치지 않는다

2라운드에서 "배치의 실체는 binding 오브젝트 생성"임을 확인했다.

```text
[일반 Pod]
  Pod 생성 → scheduler가 노드 선택 → binding 생성 → spec.nodeName 채워짐

[Static Pod]
  kubelet이 파일을 읽는다 → 자기가 실행한다
  → nodeName은 자기 자신. 물어볼 필요가 없다
```

manifest에는 `nodeName` 필드가 없는데(2절 출력 참조), `kubectl get pods -o wide`의 `NODE` 열에는 `master01`이 찍힌다. 그 열은 `spec.nodeName`을 읽은 것이다. **kubelet이 미러 Pod를 만들 때 채워 넣었다는 뜻이다.**

**이것이 부트스트랩 역설의 해답이다.** scheduler를 거치지 않으므로 scheduler가 없어도 뜬다. 마찬가지로 apiserver 없이도 뜬다.

## 7. hostNetwork와 부트스트랩 순서

### 발견 7 — CNI 없이도 뜬다

```text
kube-scheduler-master01   192.168.8.143    노드 IP 그대로
coredns-...-gv4wl         10.244.5.6       Pod CIDR
```

`hostNetwork: true`면 Pod 전용 네트워크를 쓰지 않고 노드 네트워크를 그대로 쓴다. 6단계에서 관찰한 순서가 여기서 설명된다.

```text
kubeadm init 직후
  Control Plane Pod 4개    Running    ← CNI가 없는데도 떴다
  CoreDNS                  Pending    ← CNI가 없어서 못 뜸
```

**CNI를 설치하려면 apiserver가 필요한데 apiserver가 CNI를 필요로 하면 또 순환**이다. `hostNetwork`가 그것을 끊는다.

`--bind-address=127.0.0.1`이 함께 있는 이유도 여기 있다. hostNetwork라 노드 IP를 쓰지만, 바인딩은 루프백으로 제한해 외부 접근을 막는다. **hostNetwork의 위험을 바인딩으로 상쇄한 구성이다.**

## 8. 곁가지 — leader-elect와 Lease

### 발견 8 — 2라운드의 Lease 권한이 여기서 쓰인다

```text
- --leader-elect=true      scheduler가 하나뿐인데도 켜져 있다
```

Control Plane을 늘릴 때 설정을 바꾸지 않아도 되게 한 것이다. 그리고 이 리더 선출이 2라운드에서 본 권한을 쓴다.

```text
[2라운드 — scheduler 권한 목록]
leases.coordination.k8s.io   []   [kube-scheduler]   [get list update watch]
                                  ^^^^^^^^^^^^^^^^ 이름이 지정된 리스
```

**"왜 scheduler에게 Lease 권한이 있는가"의 답이다.** 리더 자리를 나타내는 오브젝트가 Lease다.

```text
확인 필요:
  kubectl get lease -n kube-system
  kubectl get lease kube-scheduler -n kube-system -o yaml
```

## 9. 실험 — 세 층을 각각 건드려본다

Static Pod에는 세 층이 있다. 각각을 없애보면 무엇이 원본인지 드러난다.

```text
선언   /etc/kubernetes/manifests/kube-scheduler.yaml   디스크 파일
실제   containerd가 돌리는 컨테이너                     노드 위
사본   apiserver의 Pod 오브젝트 (미러 Pod)              etcd
```

### 실험 1 — `kubectl delete` (사본을 지운다)

```text
[삭제 전]
uid                e1d2fee0-76c8-41b3-b577-2f7cd46254ae
creationTimestamp  2026-08-03T08:12:03Z
restartCount       2

$ kubectl delete pod kube-scheduler-master01 -n kube-system
pod "kube-scheduler-master01" deleted from kube-system namespace

[삭제 후]
$ kubectl get pod kube-scheduler-master01 -n kube-system \
    -o custom-columns='UID:.metadata.uid,CREATED:.metadata.creationTimestamp,START:.status.startTime,RESTART:.status.containerStatuses[0].restartCount'
UID                                    CREATED                START                  RESTART
435e7b1e-c9ea-45e6-b941-976a9379a2c2   2026-08-07T06:57:29Z   2026-08-04T03:15:46Z   2

$ sudo crictl ps -a | grep scheduler
3c363c7c5b5a2  af4ba4e4da63f  31 hours ago  Running  kube-scheduler  2  ...
```

| | 삭제 전 | 삭제 후 | |
|---|---|---|---|
| `uid` | `e1d2fee0-...` | `435e7b1e-...` | 바뀜 |
| `creationTimestamp` | `08-03T08:12:03Z` | `08-07T06:57:29Z` | 바뀜 |
| `startTime` | — | `08-04T03:15:46Z` | **유지** |
| `restartCount` | 2 | 2 | **유지** |
| 컨테이너 | — | `31 hours ago` Running | **안 죽음** |

**컨테이너는 손도 안 탔다.** 31시간 전 것이 그대로 돌고 있다.

#### 발견 9 — metadata와 status가 다르게 반응한다

```text
[metadata]  apiserver가 만든다 → 새 오브젝트이므로 새 값
  uid, creationTimestamp 전부 바뀜

[status]    kubelet이 보고한다 → 실제 상태이므로 그대로
  startTime, restartCount 유지
```

`kubectl delete`가 하는 일은 **apiserver에게 "그 기록 지워"** 이며, apiserver는 노드의 컨테이너를 직접 죽일 수단이 없다. kubelet은 watch로 미러 Pod 삭제를 감지하고 즉시 다시 만들되, status는 자기가 들고 있던 실제 상태를 그대로 보고한다.

**`startTime`이 살아남은 것이 "컨테이너는 안 죽었다"의 결정적 증거다.**

### 실험 2 — manifest 파일을 옮긴다 (선언을 없앤다)

```text
$ sudo mv /etc/kubernetes/manifests/kube-scheduler.yaml /tmp/

$ kubectl get -A pod | grep -i sche
(출력 없음)                                  ← 이번엔 안 돌아온다

$ sudo mv /tmp/kube-scheduler.yaml /etc/kubernetes/manifests/

$ kubectl get -A pod | grep -i sche
kube-system   kube-scheduler-master01   0/1   Running   0   4s
kube-system   kube-scheduler-master01   0/1   Running   0   10s
kube-system   kube-scheduler-master01   1/1   Running   0   15s
```

**파일이 사라지자 컨테이너까지 죽었고 미러 Pod도 사라졌다.** 되돌리자 완전히 새것으로 떴다(`RESTARTS 0`, `AGE 4s`).

#### 발견 10 — 파일이 프로세스를 죽인 것이 아니라 kubelet이 죽였다

실행 중인 프로세스는 원본 파일과 무관하다. 리눅스에서는 바이너리를 지워도 이미 도는 프로세스는 계속 돌고, 애초에 `kube-scheduler.yaml`은 바이너리가 아니라 설정 텍스트다. 실행 파일은 컨테이너 이미지 안에 있고 containerd가 따로 보관한다.

```text
파일이 사라짐
  → kubelet이 감지
  → "이제 이걸 돌릴 이유가 없다"
  → containerd에 종료 지시 → SIGTERM
```

**파일은 "소스"가 아니라 "선언"이다.**

```text
Desired State   /etc/kubernetes/manifests/*.yaml
Controller      kubelet
Actual State    containerd가 돌리는 컨테이너

kubelet은 명령을 실행하는 것이 아니라 둘의 차이를 없앤다
```

#### 발견 11 — `0/1`에서 `1/1`까지 15초

```text
Running   컨테이너는 이미 떠 있다
0/1       그런데 아직 "준비됨"이 아니다
```

manifest의 프로브 설정 때문이다(2절).

```text
startupProbe    initialDelaySeconds: 10    10초는 기다려준다
readinessProbe  periodSeconds: 1           그 뒤 1초마다 확인
```

**"프로세스가 떴다"와 "일할 준비가 됐다"는 다르다.**

### 실험 3 — `crictl stop` (실제 컨테이너만 죽인다)

파일은 그대로 두고 컨테이너만 죽인다.

```text
[중지 전]
$ sudo crictl ps | grep scheduler
4871c2bbc1dcb  af4ba4e4da63f  8 minutes ago  Running  kube-scheduler  0  63b4ae7c234f4  ...
^^^^^^^^^^^^^                                                         ^  ^^^^^^^^^^^^^
CONTAINER                                                       ATTEMPT  POD ID(샌드박스)

$ sudo crictl stop 4871c2bbc1dcb

[중지 후]
$ sudo crictl ps | grep scheduler
376f4e1842dd4  af4ba4e4da63f  1 second ago  Running  kube-scheduler  1  63b4ae7c234f4  ...
^^^^^^^^^^^^^                 ^^^^^^^^^^^^                           ^  ^^^^^^^^^^^^^
새 컨테이너                    방금                                 0→1  그대로

$ kubectl get pod kube-scheduler-master01 -n kube-system
kube-scheduler-master01   0/1   Running   1 (3s ago)   9m5s
                                          RESTARTS 1   AGE 유지
```

#### 발견 12 — 샌드박스는 살아남는다

```text
컨테이너 ID   4871c2bbc1dcb → 376f4e1842dd4    바뀜
샌드박스 ID   63b4ae7c234f4 → 63b4ae7c234f4    그대로
```

`crictl ps`에 ID가 두 개 나오는 이유가 여기 있다.

```text
Pod 샌드박스     네트워크·IPC 네임스페이스를 들고 있는 껍데기 (pause 컨테이너)
  └── 컨테이너   실제 애플리케이션
```

**울타리는 두고 안에 든 것만 갈아끼운다.** 그래서 `AGE`는 유지되고 `RESTARTS`만 오른다.

```text
AGE 9m5s      Pod(샌드박스)가 9분 5초째 살아있다
RESTARTS 1    그 안의 컨테이너가 한 번 교체됐다
```

**Pod가 컨테이너 재시작 후에도 같은 IP를 유지하는 이유가 이것이다.** 샌드박스가 네트워크 네임스페이스를 들고 있다.

`crictl`의 `ATTEMPT`와 `kubectl`의 `RESTARTS`는 같은 카운터다.

> 문법 주의: `crictl stop`은 **컨테이너 ID**(첫 열)를 받는다. POD ID나 IMAGE ID를 주면 아무 일도 일어나지 않는다.
> 샌드박스째 중지하려면 `crictl stopp <POD ID>`를 쓴다.

### 세 실험 종합

| | 건드린 층 | 샌드박스 | 컨테이너 | Pod uid | RESTARTS | AGE |
|---|---|---|---|---|---|---|
| `kubectl delete` | 사본(미러) | 유지 | 유지 | **바뀜** | 2 유지 | — |
| `mv` 파일 | 선언 | 새것 | 새것 | 바뀜 | **0 초기화** | **0s** |
| `crictl stop` | 실제 컨테이너 | **유지** | 새것 | 유지 | **0→1** | 유지 |

```text
선언(파일)      없애면 → 전부 사라짐. 안 돌아옴
실제(컨테이너)  죽이면 → 샌드박스는 남고 컨테이너만 교체. 되살아남
사본(미러)      지우면 → 아무 일 없음. 기록만 새로 생김
```

## 3라운드 정리

```text
 1. Control Plane 4개는 master01의 /etc/kubernetes/manifests/ 파일로 실행된다
    kubelet이 그 디렉터리를 직접 읽는다 (staticPodPath 설정값)
 2. Static Pod는 apiserver도 scheduler도 거치지 않는다
    manifest에 nodeName이 없는데 클러스터에는 master01로 찍히는 것이 증거
 3. 그래서 부트스트랩 역설이 풀린다 — apiserver를 띄우는 데 apiserver가 필요 없다
 4. hostNetwork: true라 CNI 없이도 뜬다. CoreDNS가 Pending이던 것과 대비된다
 5. volumes가 hostPath뿐이다. ConfigMap/Secret은 apiserver가 필요하므로 못 쓴다
 6. kubelet이 apiserver에 올리는 것은 "미러 Pod" — 읽기 전용 사본이다
    ownerReferences가 Node이고 config.source가 file이다
 7. 이름에 노드명이 붙는다. 여러 Control Plane에서 이름이 충돌하지 않게 하려는 것
 8. kubectl delete로는 사본만 지워진다. 컨테이너도 파일도 그대로다
 9. metadata는 apiserver가, status는 kubelet이 만든다. 그래서 다르게 반응한다
10. 파일이 프로세스를 죽이는 것이 아니라 kubelet이 죽인다
    Desired State(파일) / Controller(kubelet) / Actual State(컨테이너)
11. 샌드박스와 컨테이너는 별개다. 컨테이너만 교체되면 AGE는 유지되고 RESTARTS가 오른다
12. Node uid가 2라운드 JWT의 node.uid와 같다. Node가 소유권 기준점이다
13. --leader-elect가 2라운드에서 본 scheduler의 Lease 권한을 쓴다
```

**로드맵 질문 5·6 답변 완료.**

## 미확인

```text
1. kubectl get lease -n kube-system                          미실행
2. Control Plane Pod의 재시작 이력 (etcd 1 / 나머지 2)
   3d4h 전 재시작은 고정 IP 설정 후 재부팅으로 추정되나,
   apiserver만 31h 전에 한 번 더 재시작한 이유는 확인하지 않음
     kubectl get pod kube-apiserver-master01 -n kube-system \
       -o jsonpath='{.status.containerStatuses[0].lastState}'
3. Pod startTime이 08-04T03:15:46Z인 이유
   최초 등록(08-03T08:12:03Z)과 하루 차이가 난다
4. calico-kube-controllers의 재시작 6회 — 다른 Pod보다 유독 많다
```

---

# 4라운드 — etcd 내부 (2026-08-08)

3라운드가 "미러 Pod는 etcd에 저장된 기록"으로 끝났다. 그 etcd를 직접 연다.

```text
확인하려는 것
  1. 클러스터의 진짜 상태가 어디에 어떤 모습으로 있는가
  2. apiserver를 건너뛰면 인증·인가가 어떻게 되는가
  3. Secret은 실제로 어떻게 저장되는가
  4. 1~3라운드에서 본 것들이 정말 거기 있는가
```

> **위험**: `etcdctl del` / `put` / `compact`는 절대 쓰지 않는다.
> 특히 `etcdctl del /registry --prefix`는 한 줄로 클러스터를 소멸시킨다.
> 이 라운드는 `get`만 쓴다.

## 1. 백업부터

```text
$ kubectl exec -n kube-system etcd-master01 -- etcdctl \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key \
    snapshot save /var/lib/etcd/backup-20260808.db
...
{"msg":"fetched snapshot","size":"3.5 MB","took":"111.833999ms","etcd-version":"3.6.0"}
Snapshot saved at /var/lib/etcd/backup-20260808.db

$ sudo ls -lh /var/lib/etcd/backup-20260808.db
-rw------- 1 root root 3.4M Aug  8 13:09 /var/lib/etcd/backup-20260808.db
```

**이 3.4MB 파일 하나가 클러스터 전체다.** `/var/lib/etcd`는 hostPath라 컨테이너 안에서 저장해도 호스트 디스크에 남는다.

> 5절에서 확인하듯 이 파일에는 Secret이 평문으로 들어 있다. 실험 후 삭제했다.

## 2. 접속 경로와 인증서

```text
$ sudo grep -E 'data-dir|listen-client-urls|advertise-client-urls|cert-file|key-file|trusted-ca-file|client-cert-auth' \
    /etc/kubernetes/manifests/etcd.yaml
- --advertise-client-urls=https://192.168.8.143:2379
- --cert-file=/etc/kubernetes/pki/etcd/server.crt
- --client-cert-auth=true
- --data-dir=/var/lib/etcd
- --key-file=/etc/kubernetes/pki/etcd/server.key
- --listen-client-urls=https://127.0.0.1:2379,https://192.168.8.143:2379
- --peer-cert-file=/etc/kubernetes/pki/etcd/peer.crt
- --peer-client-cert-auth=true
- --peer-key-file=/etc/kubernetes/pki/etcd/peer.key
- --peer-trusted-ca-file=/etc/kubernetes/pki/etcd/ca.crt
- --trusted-ca-file=/etc/kubernetes/pki/etcd/ca.crt

$ sudo grep -A4 'volumeMounts:' /etc/kubernetes/manifests/etcd.yaml
    volumeMounts:
    - mountPath: /var/lib/etcd
      name: etcd-data
    - mountPath: /etc/kubernetes/pki/etcd
      name: etcd-certs

$ kubectl exec -n kube-system etcd-master01 -- etcdctl version
etcdctl version: 3.6.6
API version: 3.6
```

이후 명령은 셸 함수로 줄였다.

```bash
etcdget() {
  kubectl exec -n kube-system etcd-master01 -- etcdctl \
    --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/server.crt \
    --key=/etc/kubernetes/pki/etcd/server.key "$@"
}
```

### 발견 1 — etcd 컨테이너는 `pki/etcd`만 마운트한다

처음에 `apiserver.crt`로 접속을 시도했다가 이 오류를 봤다.

```text
Error: open /etc/kubernetes/pki/apiserver.crt: no such file or directory
```

etcd 컨테이너에는 그 파일이 없다. `pki` 전체가 아니라 `pki/etcd`만 붙어 있기 때문이다.

```text
etcd 컨테이너가 볼 수 있는 것   etcd 인증서 4쌍
볼 수 없는 것                  ca.key, apiserver.key, sa.key ...
```

**파일시스템 수준의 최소 권한이다.** etcd가 뚫려도 클러스터 CA 개인키는 가져갈 수 없다.

### 발견 2 — 네트워크에도 리스닝한다

```text
--listen-client-urls=https://127.0.0.1:2379,https://192.168.8.143:2379
```

3라운드의 scheduler와 대조된다.

```text
kube-scheduler   --bind-address=127.0.0.1        루프백만
etcd             127.0.0.1 + 192.168.8.143       네트워크에도 열림
```

HA 구성에서 다른 노드의 apiserver가 붙어야 하기 때문이다. 그래서 `--client-cert-auth=true`가 유일한 방어선이 되며, 6절에서 그것이 실제로 작동함을 확인한다.

`--peer-*` 플래그가 따로 있는 것도 눈에 띈다.

```text
--cert-file / --key-file            클라이언트(apiserver)를 상대할 때
--peer-cert-file / --peer-key-file  다른 etcd 멤버를 상대할 때
```

1라운드에서 apiserver가 상대별로 다른 인증서를 쓰던 것과 같은 패턴이다.

## 3. 키 구조

```text
$ etcdget get / --prefix --keys-only | grep -c .
351

$ etcdget get / --prefix --keys-only | grep . | cut -d/ -f2 | sort -u
registry

$ etcdget get / --prefix --keys-only | grep . | cut -d/ -f1-3 | sort | uniq -c | sort -rn
     74 /registry/clusterroles
     61 /registry/clusterrolebindings
     45 /registry/serviceaccounts
     23 /registry/apiregistration.k8s.io
     23 /registry/apiextensions.k8s.io
     21 /registry/crd.projectcalico.org
     13 /registry/pods
     12 /registry/configmaps
     11 /registry/roles
     11 /registry/rolebindings
     11 /registry/flowschemas
      8 /registry/prioritylevelconfigurations
      6 /registry/leases
      4 /registry/services
      4 /registry/namespaces
      3 /registry/minions
      3 /registry/csinodes
      2 /registry/replicasets
      2 /registry/priorityclasses
      2 /registry/ipaddresses
      2 /registry/endpointslices
      2 /registry/deployments
      2 /registry/daemonsets
      2 /registry/controllerrevisions
      1 /registry/servicecidrs
      1 /registry/ranges
      1 /registry/poddisruptionbudgets
      1 /registry/masterleases
```

### 발견 3 — 최상위는 `/registry` 하나뿐이다

```text
/registry/<리소스종류>/<네임스페이스>/<이름>
```

Kubernetes는 etcd를 통째로 쓰지 않고 `/registry` 아래만 쓴다.

### 발견 4 — 저장된 것의 절반 이상이 권한 데이터다

```text
74  clusterroles
61  clusterrolebindings
45  serviceaccounts
11  roles
11  rolebindings
───────────────────
202 / 351 = 58%
```

2라운드의 결론이 수치로 확인된다.

> 인증서는 "누구인가"만 말한다.
> "무엇을 할 수 있는가"는 클러스터 안의 별도 오브젝트가 정한다.

**그 "별도 오브젝트"가 etcd 내용물의 대부분이다.** 실제로 도는 Pod는 13개인데 권한 관련은 202개다.

### 발견 5 — Node는 `minions`로 저장된다

```text
$ etcdget get /registry/minions --prefix --keys-only
/registry/minions/master01
/registry/minions/worker01
/registry/minions/worker02
```

```text
API 이름     kubectl get nodes
저장 경로    /registry/minions
```

Kubernetes 초기에 워커 노드를 minion이라 부르던 흔적이다. 바꾸면 기존 클러스터의 데이터를 읽을 수 없으므로 못 바꾼다.

### 발견 6 — Events가 없다

목록에 `/registry/events`가 아예 없다. Event는 기본 TTL이 있어 시간이 지나면 etcd에서 자동 삭제된다. **로그가 아니라 휘발성 데이터다.** `kubectl describe`에서 Event가 자주 사라지는 이유가 이것이다.

### 그 밖에 눈에 띄는 것

```text
23  apiregistration.k8s.io   1라운드의 apiservice. 전부 Local이었다
23  apiextensions.k8s.io     CRD 정의
21  crd.projectcalico.org    Calico가 추가한 오브젝트
 6  leases                   3라운드의 leader-elect가 쓰는 것
 1  masterleases             apiserver 자신의 엔드포인트 등록
11  flowschemas              API 우선순위/공정성(APF)
```

## 4. 값 열어보기 — Static Pod

```text
$ etcdget get /registry/pods/kube-system/kube-scheduler-master01
k8s
v1Pod
kube-scheduler-master01
kube-system"*$22bd7954-d0df-4fc3-9e00-2b6f41ec0bfe...
kubernetes.io/config.hash    dcefdf84232060db703cb0098efa5bf6
kubernetes.io/config.mirror  dcefdf84232060db703cb0098efa5bf6
kubernetes.io/config.seen    2026-08-07T16:19:15.314288950+09:00
kubernetes.io/config.source  file
Nod master01 "$3ede84ea-97ec-47fd-97a7-9f0241db181c
...
kubeletUpdate FieldsV1:
{"f:metadata":{"f:annotations":{".":{},"f:kubernetes.io/config.hash":{}, ... (매우 김)
...
containerd://4871c2bbc1dcbe3c23370355a2681f64aaab79abfb318aaace1693e28991c92d
containerd://376f4e1842dd46f2c45f7d19e0de8f5eb6ffe40990265d6c1d171e1a7e2ca36a
```

> 바이너리를 터미널에 그대로 출력하면 제어 문자로 해석되어 터미널이 오작동한다.
> 실제로 PuTTY가 자기 이름을 응답해 다음 명령줄에 섞여 들어갔다.
> `| strings` 또는 `| cat -v`로 걸러야 하며, 깨졌으면 `reset`으로 복구한다.

### 발견 7 — 저장 형식은 protobuf다

맨 앞의 `k8s` 매직 바이트 뒤는 protobuf 바이너리다.

```text
kubectl get -o yaml   apiserver가 YAML로 번역해서 보여주는 것
etcd 안               protobuf 바이너리
```

**우리가 보던 YAML은 저장 형식이 아니라 표현 형식이었다.**

다만 문자열은 그대로 읽히므로 3라운드에서 본 어노테이션이 실제로 들어 있음을 확인할 수 있다. `config.source: file`, `ownerReferences`의 Node uid `3ede84ea-...`가 그대로다.

### 발견 8 — 값의 상당 부분이 `managedFields`다

```text
{"f:metadata":{"f:annotations":{".":{},"f:kubernetes.io/config.hash":{}, ...
```

"어느 필드를 누가 설정했는지" 기록이다. 여러 주체가 같은 오브젝트를 수정할 때 충돌을 막는 장치이며, **실제 설정보다 이 메타데이터가 더 크다.** etcd 용량을 차지하는 주요 원인이다.

### 발견 9 — 3라운드 실험의 흔적이 남아 있다

```text
config.seen: 2026-08-07T16:19:15
```

원래 값은 `2026-08-03T17:12:03`이었다(3라운드 4절). 3라운드에서 manifest를 `mv`로 옮겼다 되돌렸을 때 **kubelet이 파일을 새로 읽은 시각**이 여기 박혔다.

컨테이너 ID도 두 개 보인다.

```text
containerd://4871c2bbc1dcb...   3라운드에서 crictl stop으로 죽인 것
containerd://376f4e1842dd4...   그 뒤에 새로 뜬 것
```

**실험이 데이터로 남았다.** 죽은 것은 `lastState`로, 새 것은 현재 상태로 기록되어 있다.

## 5. Secret은 평문이다

먼저 Secret이 하나도 없음을 확인했다.

```text
$ etcdget get /registry/secrets --prefix --keys-only | grep -c .
0
```

새 kubeadm 클러스터에는 Secret이 없다. 부트스트랩 토큰 Secret은 자동 정리되고, 지금 방식의 ServiceAccount 토큰은 Secret을 만들지 않는다(2라운드의 bound token). **2라운드에서 본 방식 전환이 개수 0으로 확인된다.**

그래서 테스트용으로 하나 만들어 확인했다.

```text
$ kubectl create secret generic etcd-test --from-literal=password=SuperSecret123 -n default
secret/etcd-test created

$ etcdget get /registry/secrets/default/etcd-test
/registry/secrets/default/etcd-test
k8s
v1Secret
  etcd-test default "*$29463fa2-649b-496d-a3c8-b323f56bc093
  kubectl-createUpdate FieldsV1:
  {"f:data":{".":{},"f:password":{}},"f:type":{}}
  passwordSuperSecret123 Opaque
          ^^^^^^^^^^^^^^

$ kubectl delete secret etcd-test -n default
secret "etcd-test" deleted from default namespace
```

### 발견 10 — base64는 인코딩이지 암호화가 아니다

```text
kubectl get secret -o yaml
  data:
    password: U3VwZXJTZWNyZXQxMjM=      암호화된 것처럼 보인다

etcd 안
  passwordSuperSecret123                 평문
```

base64는 apiserver가 YAML로 렌더링할 때 씌우는 옷이다. YAML에 바이너리를 담을 수 없어 인코딩하는 것뿐이며, 되돌리는 데 키가 필요 없다.

저장 시 암호화는 설정되어 있지 않다.

```text
$ grep -i encryption /etc/kubernetes/manifests/kube-apiserver.yaml
(출력 없음)
```

`--encryption-provider-config`가 없다. **kubeadm 기본값이며, 이것이 평문 저장의 직접적 원인이다.**

```text
etcd 데이터 파일이나 백업 파일을 손에 넣으면
  → 클러스터의 모든 Secret을 평문으로 읽는다
  → RBAC도 인증서도 무의미하다
```

1절에서 만든 백업 파일이 그 자체로 위험물이었으므로 실험 후 삭제했다.

```text
방어 수단
  1. etcd 접근 통제       mTLS + etcd 전용 CA        ← 현재 유일하게 켜져 있음
  2. 디스크 암호화         /var/lib/etcd 볼륨
  3. 저장 시 암호화        --encryption-provider-config
  4. 외부 비밀 관리        Vault, AWS Secrets Manager
```

## 6. mTLS 검증 — 1라운드 CA 분리의 실측

1라운드에서 "etcd는 etcd-ca가 서명한 인증서만 받아들인다"고 주장했다. 검증한다.

먼저 `openssl s_client`로 시도했으나 판단 근거가 되지 못했다.

```text
$ sudo openssl s_client -connect 127.0.0.1:2379 \
    -CAfile /etc/kubernetes/pki/etcd/ca.crt \
    -cert /etc/kubernetes/pki/apiserver.crt \
    -key /etc/kubernetes/pki/apiserver.key </dev/null
SSL handshake has read 1478 bytes and written 1585 bytes
Verify return code: 0 (ok)
```

`Verify return code: 0`은 **클라이언트가 서버를 검증한 결과**다. 우리가 알고 싶은 것은 반대 방향이다. 실제로 요청을 보내야 확인된다.

```text
$ curl -sS --cacert /etc/kubernetes/pki/etcd/ca.crt \
    --cert /etc/kubernetes/pki/apiserver-etcd-client.crt \
    --key  /etc/kubernetes/pki/apiserver-etcd-client.key \
    https://127.0.0.1:2379/version
{"etcdserver":"3.6.6","etcdcluster":"3.6.0","storage":"3.6.0"}

$ curl -sS --cacert /etc/kubernetes/pki/etcd/ca.crt \
    --cert /etc/kubernetes/pki/apiserver.crt \
    --key  /etc/kubernetes/pki/apiserver.key \
    https://127.0.0.1:2379/version
curl: (56) OpenSSL SSL_read: error:0A000418:SSL routines::tlsv1 alert unknown ca

$ curl -sS --cacert /etc/kubernetes/pki/etcd/ca.crt \
    https://127.0.0.1:2379/version
curl: (56) OpenSSL SSL_read: error:0A00045C:SSL routines::tlsv13 alert certificate required
```

### 발견 11 — 두 알림이 서로 다르다

```text
unknown ca             인증서는 냈는데 그 CA를 모른다
certificate required   인증서를 아예 안 냈다
```

etcd가 두 상황을 구분해 답했다. 각각 다른 방어선이 작동한다는 뜻이다.

```text
방어선 1  --client-cert-auth=true    인증서를 반드시 내야 한다
방어선 2  --trusted-ca-file          그 인증서가 etcd-ca 소속이어야 한다
```

**`unknown ca`가 1라운드의 CA 분리를 증명한다.** `apiserver.crt`는 만료되지 않은 정상 인증서이고 클러스터 안에서 매일 쓰이지만, etcd 앞에서는 아무 의미가 없다.

```text
클러스터 CA(ca.key)가 유출되면
  → 노드·관리자·컴포넌트 인증서를 위조할 수 있다
  → 그러나 etcd에는 붙지 못한다
  → 5절에서 확인한 Secret 평문 전체는 지킬 수 있다
```

### 발견 12 — 핸드셰이크 성공과 인증 통과는 다르다

```text
curl: (56) OpenSSL SSL_read: ... alert unknown ca
                   ^^^^^^^^ 핸드셰이크가 아니라 읽기 시점
```

TLS 1.3은 클라이언트 인증서 검증을 핸드셰이크 뒤에 한다. 그래서 `openssl s_client`에 `</dev/null`을 주면 거부 신호를 받을 기회 없이 종료되어 "성공"처럼 보인다. **실제로 데이터를 주고받아야 확인된다.**

알림에 `tlsv13`이 찍힌 것은 이 클러스터가 TLS 1.3을 쓴다는 증거이기도 하다.

> 위 명령의 `echo "--- exit: $? ---"`는 앞선 `echo`의 종료 코드를 읽어 항상 0이 나왔다.
> `curl ... ; rc=$?` 형태로 받아야 한다. 결론에는 영향이 없다.

## 7. 디스크의 저장 구조 — WAL / snap / db (2026-08-11 추가)

지금까지는 **etcd를 API로 열어봤다.** 이제 디스크에 실제로 어떤 파일이 있는지 본다.

```text
root@master01:/var/lib/etcd# ls -la
drwx------  3 root root 4096 Aug 10 14:23 .
drwx------  4 root root 4096 Aug 10 13:39 member

root@master01:/var/lib/etcd/member# ls
snap  wal

root@master01:/var/lib/etcd/member/snap# ls
0000000000000004-0000000000133a5e.snap  0000000000000004-000000000013af91.snap
0000000000000004-000000000013616f.snap  0000000000000004-000000000013d6a2.snap
0000000000000004-0000000000138880.snap  db

root@master01:/var/lib/etcd/member/wal# ls
000000000000000a-00000000000e2e1c.wal  000000000000000c-00000000001103d7.wal
000000000000000b-00000000000f98f4.wal  000000000000000d-0000000000125db1.wal
000000000000000e-000000000013c7f8.wal  0.tmp
```

### etcd는 DBMS인가 — 그렇다

```text
etcd = Raft(합의) + bbolt(저장) + gRPC API
                    ^^^^^
                    실제 저장 엔진
```

`bbolt`는 BoltDB를 etcd 팀이 fork한 **임베디드 key-value 저장소**다. 별도 프로세스가 아니라 etcd 바이너리에 링크된 라이브러리다. **SQLite와 같은 부류**이며, `ps`에 `bbolt`라는 프로세스는 없다.

RDBMS와 구조가 대응한다.

| | RDBMS | etcd |
|---|---|---|
| 변경 기록 | redo log / WAL | `member/wal/*.wal` |
| 실제 데이터 | 테이블스페이스 | `member/snap/db` |
| 복구 기준점 | checkpoint LSN | `db` 안 `meta` 버킷의 `consistent_index` |
| 인덱스 구조 | B+tree | B+tree (bbolt) |
| 다중 버전 | MVCC | revision 기반 MVCC |
| 공간 회수 | VACUUM FULL | compaction + defrag |

### 발견 13 — WAL은 복구용이자 복제용이다

```text
[RDBMS 의 WAL]   목적: 장애 복구 하나
                 독자: 재시작할 때의 나 자신

[etcd 의 WAL]    목적: 장애 복구 + 복제
                 독자: 나 자신 + 다른 etcd 멤버
```

**etcd의 WAL은 Raft 로그 그 자체다.**

```text
리더가 "이 변경을 로그 5번 자리에" 를 팔로워에게 보낸다
팔로워가 자기 WAL 에 쓰고 fsync 하고 "썼다" 고 답한다
과반이 답하면 "5번까지 확정"
각자 자기 bbolt 에 반영한다

→ 같은 로그를 같은 순서로 재생하면 같은 상태가 된다
```

MySQL은 redo log(복구용)와 binlog(복제용)가 따로인데, **etcd는 그 둘이 한 파일이다.**

이 클러스터는 etcd 1대라 합의할 상대가 없지만 **파일 구조는 3대일 때와 같다.** 혼자서도 fsync 하고 "과반(=자기 자신)"을 확인하고 진행한다.

### 발견 14 — bbolt 자신에게는 WAL이 없다

```text
etcd  ── WAL 있음 (Raft 로그)
  └─ bbolt ── WAL 없음
```

bbolt는 다른 방법으로 크래시 안전성을 얻는다.

```text
1. 쓰기는 항상 새 페이지에 한다 (copy-on-write). 원본은 안 건드린다
2. 다 쓰고 fsync
3. 마지막에 meta 페이지 하나를 갈아끼운다   ← 이 순간이 커밋
4. meta 페이지는 2개를 번갈아 쓴다. 하나 깨져도 다른 하나가 유효
```

**커밋이 "포인터 하나 바꾸기"라 원자적이다.** 그래서 별도 로그가 필요 없다.

durability가 두 겹이 된다.

```text
[1층] Raft WAL       매 쓰기마다 fsync. 여기까지가 "응답해도 되는" 기준
[2층] bbolt commit   모아서 반영. 뒤처져 있어도 된다
                     뒤처진 만큼은 재시작 때 WAL 로 메운다
```

### 발견 15 — `.snap`은 v2 store다. checkpoint가 아니다 ★

**처음에는 `.snap`을 DBMS의 checkpoint로 이해했으나 이는 틀렸다.** 공식 문서 확인 결과:

```text
member/snap/*.snap
  = JSON 으로 직렬화된 store v2 내용
  + 멤버십 정보 (member attributes, peer URL)
  + storage version

  "As of etcd v3, the content is redundant to the content of /snap/db files"
  "with store v2 decommissioning we expect the files to stop being written at all"
```

**v3 데이터는 `.snap`에 없다.** v2 시절의 잔재이며 지금은 `db`와 중복이고, 앞으로 아예 안 쓰이게 될 예정이다.

v3의 복구 기준점은 `db` 파일 안에 있다.

```text
db 의 meta 버킷 → consistent_index
  = "WAL 의 몇 번 엔트리까지 bolt DB 에 반영했는가"
```

```text
재시작 복구 순서

1. 리더에게 받아둔 .snap.db 가 consistent_index 보다 최신인지 확인
2. 있으면 그 스냅샷에서 로드
3. consistent_index 부터 WAL 을 재생
4. 멤버십·인증 정보를 db 에서 복원
```

**"이미 반영한 것을 다시 반영하지 않게 막는 기준점"** 이 consistent_index다.

다만 `.snap` 파일이 생성되는 **사건**(snapshot)은 여전히 의미가 있다. 그 지점 이전의 WAL을 버릴 수 있게 해준다.

### 발견 16 — 파일 이름으로 설정값을 역산할 수 있다 ★

```text
{term}-{index}.snap      둘 다 16진수
{seq}-{첫 엔트리 index}.wal
```

`.snap` 5개의 index를 10진수로 바꾸면 이렇다.

```text
0x133a5e = 1,260,126
0x13616f = 1,270,127      차이  10,001
0x138880 = 1,280,128      차이  10,001
0x13af91 = 1,290,129      차이  10,001
0x13d6a2 = 1,300,130      차이  10,001
```

**정확히 10,001씩이다.** 공식 문서의 `--snapshot-count` 기본값은 **100,000**이므로, kubeadm이 `10000`으로 낮춰 넣었다는 뜻이다.

`.wal` 쪽은 간격이 일정하지 않다.

```text
0x0e2e1c =   929,308
0x0f98f4 = 1,022,196      차이  92,888
0x1103d7 = 1,115,095      차이  92,899
0x125db1 = 1,203,633      차이  88,538
0x13c7f8 = 1,296,376      차이  92,743
```

```text
스냅샷    "엔트리 10000개마다"        → 개수 기준이라 일정
WAL 파일  "약 64MB 넘으면 다음 것"     → 크기 기준이라 들쭉날쭉
```

엔트리 크기가 제각각(Lease 갱신은 작고 Pod 생성은 큼)이라 같은 용량에 들어가는 개수가 달라진다.

`term = 4`는 **etcd가 지금까지 겪은 리더 선출 횟수**다. 08 문서 실험 4에서 etcd를 껐다 켠 것이 반영됐을 수 있다.

### 발견 17 — 파일 개수가 정확히 상한에 있다

```text
--max-wals=5        WAL 을 5개까지 보관
--max-snapshots=5   .snap 을 5개까지 보관 (30초마다 오래된 것 정리)
```

```text
snap/  .snap 5개 + db        ← 정확히 5개
wal/   .wal 5개 + 0.tmp      ← 정확히 5개 + 미리 할당분
```

6일 돌린 클러스터라 **이미 정상 순환 궤도에 들어간 상태**다.

`0.tmp`는 **다음에 쓸 WAL 파일을 미리 할당해둔 것**이다. 파일을 늘려가며 쓰면 파일시스템이 블록을 새로 잡느라 순간 느려지는데, etcd는 매 쓰기마다 fsync를 하므로 그 지연이 그대로 클러스터 지연이 된다. 그래서 한가할 때 미리 만들어 둔다.

### `db` 안의 버킷 구조

```text
key               v3 데이터 본체. revision ID 를 key 로 쓴다
lease             Lease 의 TTL 과 남은 시간
meta              consistent_index, compaction 상태     ← 핵심
members           현재 멤버십
members_removed   제거된 멤버 ID
auth              인증 revision, role / user
cluster           버전, downgrade 상태
alarm             클러스터 이상 진단
```

**3절에서 본 `/registry/...` 문자열은 `key` 버킷 안에 있고, 버킷의 key는 그 문자열이 아니라 revision ID다.** 문자열 → revision 매핑은 etcd가 메모리에 별도 B-tree 인덱스로 들고 있다.

### 발견 18 — MVCC가 `--watch`를 가능하게 한다

```text
etcd 는 값을 덮어쓰지 않는다. revision 마다 별도의 값을 남긴다

worker01 Lease  revision 1260126  RenewTime 09:27:14
                revision 1260140  RenewTime 09:27:24
                revision 1260155  RenewTime 09:27:34
```

```text
kubectl get ... --watch     "이 revision 이후 변경분만 줘"
                            → 놓친 이벤트 없이 이어받을 수 있다
```

**08 문서에서 "node-controller가 apiserver에 watch를 걸어 받는다"고 한 것이 이 위에서 동작한다.** revision이 있어서 "어디까지 봤는지"를 말할 수 있다.

대가는 무한 누적이다.

```text
compaction   오래된 revision 을 논리적으로 버린다
             Kubernetes 는 apiserver 가 5분마다 자동 실행한다

defrag       compaction 후에도 db 파일 크기는 안 줄어든다
             (bbolt 안에 빈 페이지로 남는다)
             → defrag 를 해야 실제로 반환된다
```

**`VACUUM FULL`과 같은 상황**이다. 삭제해도 파일이 안 줄고, 별도 명령을 쳐야 줄어든다.

### 운영 시사점 — 08 문서 실험 4와 연결된다

```text
etcd 의 모든 쓰기는 fsync 를 기다린다
        ↓
디스크가 느리면 etcd 가 느리다
etcd 가 느리면 apiserver 가 느리다
apiserver 가 느리면 클러스터 전체가 느리다
```

실험 4에서 본 `Timeout or abort while handling`이 이 연쇄의 극단이다. **디스크가 느릴 때는 같은 일이 약하게, 그러나 만성적으로 일어난다.**

```text
etcd 는 SSD 에 둔다
다른 워크로드와 디스크를 공유하지 않는다
네트워크 스토리지(NFS 등)에 두지 않는다
```

5단계에서 볼 지표를 미리 적어둔다.

```text
etcd_disk_wal_fsync_duration_seconds        WAL fsync 지연
etcd_disk_backend_commit_duration_seconds   bbolt 커밋 지연
etcd_server_leader_changes_seen_total       리더 교체 횟수 (= term 증가)
```

지금은 이 값들을 볼 수 없다. **`.snap` 파일 이름의 `term`을 보는 것이 현재 할 수 있는 최선이다.**

### 출처

```text
https://etcd.io/docs/v3.6/learning/persistent-storage-files/
https://pkg.go.dev/go.etcd.io/etcd/server/v3/etcdserver/api/snap
```

## 4라운드 정리

```text
 1. 클러스터의 모든 상태는 etcd의 /registry 아래 351개 키다. 3.4MB
 2. 최상위는 /registry 하나뿐. 키는 /registry/<리소스>/<네임스페이스>/<이름>
 3. 저장된 것의 58%(202/351)가 권한 데이터다 — 2라운드 결론의 수치적 확인
 4. Node는 minions로 저장된다 — 초기 명칭의 화석
 5. Events는 없다 — TTL로 자동 삭제되는 휘발성 데이터
 6. 저장 형식은 protobuf. YAML은 apiserver의 표현일 뿐이다
 7. 값의 상당 부분이 managedFields — 누가 어느 필드를 고쳤는지 기록
 8. Secret은 평문이다. base64는 인코딩이지 암호화가 아니다
    --encryption-provider-config가 없다 (kubeadm 기본값)
 9. etcd 백업 파일은 Secret 평문 덩어리다. 취급에 주의해야 한다
10. etcd는 노드 IP에도 리스닝하지만 mTLS로 막혀 있다
    unknown ca → CA 분리가 실재한다 (1라운드 검증 완료)
    certificate required → client-cert-auth가 강제된다
11. etcd 컨테이너는 pki/etcd만 마운트한다 — 파일시스템 수준 최소 권한
12. 3라운드 실험의 흔적이 데이터로 남아 있다 (config.seen, 죽은 컨테이너 ID)
13. apiserver를 건너뛰면 인증·인가가 전부 무의미하다
    그래서 etcd만 CA부터 분리해 격리한다

--- 2026-08-11 추가 (7절) ---
14. etcd는 DBMS다. 안에 bbolt(BoltDB fork)를 임베디드로 쓴다
    별도 프로세스가 아니라 링크된 라이브러리다 (SQLite와 같은 부류)
15. WAL은 복구용이자 복제용이다 — Raft 로그 그 자체다
    MySQL의 redo log와 binlog가 하나로 합쳐진 셈
16. bbolt에는 WAL이 없다. copy-on-write + meta 페이지 교체로 원자성을 얻는다
17. .snap은 checkpoint가 아니다. v2 store의 잔재이며 db와 중복이다
    v3의 복구 기준점은 db 안 meta 버킷의 consistent_index다
18. 파일 이름으로 설정값을 역산할 수 있다
    .snap 간격 10,001 → --snapshot-count=10000 (기본값 100,000에서 낮춘 것)
    .wal 간격이 불규칙한 이유는 크기(64MB) 기준이기 때문
19. 파일 개수가 상한(--max-wals=5 / --max-snapshots=5)에 정확히 도달해 순환 중이다
20. MVCC가 --watch를 가능하게 한다. 대가는 compaction + defrag 필요
21. 모든 쓰기가 fsync를 기다린다 → 디스크 지연이 클러스터 지연이 된다
```

**로드맵 질문 7 답변 완료.**

## 미확인

```text
1. --encryption-provider-config를 켰을 때 etcd에 어떻게 저장되는지
   켜면 값이 k8s:enc:... 로 시작하는 암호문이 된다고 알고 있으나 확인하지 않음
2. Event의 정확한 TTL 기본값 — apiserver의 --event-ttl 확인 필요
3. /registry 351개 중 집계에 잡히지 않은 2개 키의 정체
4. managedFields를 끄거나 줄이는 방법이 있는지

--- 2026-08-11 추가 (7절) ---
5. etcd.yaml에 --snapshot-count=10000이 실제로 적혀 있는지 미확인
   파일명 간격(10,001)으로 역산한 값이다. grep으로 확인 필요
6. .wal 파일이 실제로 전부 같은 크기인지 미확인 (ls -la로 확인 필요)
   공식 문서는 "약 64MB 초과 시 cut"이라고만 기술한다
7. .snap 안에 v3 데이터(/registry/...)가 정말 없는지 미확인
   strings로 확인 가능. db와 비교하면 성격 차이가 드러난다
8. term=4가 실험 4의 etcd 재시작 때문인지 확정하지 못했다
   재시작 시점의 term 값을 기록해두지 않았다
9. bbolt 버전 — 임베디드라 etcdctl version에 드러나지 않는다
```

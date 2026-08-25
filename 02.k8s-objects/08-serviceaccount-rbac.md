# 08. ServiceAccount / RBAC

2단계 아홉 번째. **07 에서 절반만 막았다. 나머지 절반을 막는다.**

```text
[07 에서 막은 것]   Pod → Pod 로 가는 패킷        NetworkPolicy + Calico + 커널
[07 에서 안 막은 것] 누구 → apiserver 로 가는 요청  ← 이 문서
```

```text
NetworkPolicy 를 걸어 team-b 로 가는 패킷은 막았다
그런데 kubectl 로는 여전히 뭐든 할 수 있었다
  kubectl delete namespace team-b     ← 실제로 이걸로 지웠다
```

```text
[데이터 층]  Pod → Pod        NetworkPolicy 가 막는다
[제어 층]    누구 → apiserver  RBAC 이 막는다        ← 이 문서
```

## 1단계와 무엇이 다른가

```text
[1단계 07 문서 2라운드 — 인가편]
  admin.conf 와 super-admin.conf 의 권한 차이를 읽었다
  Pod 안의 3종 세트로 apiserver 를 호출해봤다
  system:controller:* 40여 개 바인딩 목록을 봤다
  → 전부 "이미 만들어진 것" 을 읽기만 했다

[08 에서 한 것]
  제한된 ServiceAccount 를 직접 만들었다
  Pod 에 붙여 403 을 봤고, Role 을 붙여 200 을 봤다
  네임스페이스를 넘어가면 다시 막히는 것을 봤다
```

## 이 문서의 범위

```text
[확인한 것]
  1. 권한은 서버가 아니라 신원에 붙는다                    ✅ ★★
  2. 같은 노드에서 kubeconfig 를 바꾸면 신원이 바뀐다        ✅ ★
  3. 컴포넌트마다 신원과 권한이 나뉘어 있다                  ✅
  4. "권한 없음" 의 실제 뜻                                ✅
  5. 세 조각 — 신원 / 규칙 / 연결                          ✅ ★
  6. Pod 안에서 403 → 200 (재시작 없이)                    ✅ ★★
  7. Role 과 ClusterRole 의 조합 네 가지                    ✅ ★
  8. default SA 에 권한을 주면 안 되는 이유                 ✅ ★

[다루지 않는 것]
  사용자(User) 만들기          Kubernetes 에 User 오브젝트가 없다
  OIDC / 외부 인증 연동        10단계 이후
  automountServiceAccountToken 개념만
  Admission Webhook            RBAC 다음 단계의 검증. 미실습
  audit log                    5단계 이후
```

---

# 0. 전체 흐름 — 셋이 각각 언제 쓰이는가 ★

**각론 전에 지도를 먼저 그린다. 이게 없으면 나머지가 조각으로 읽힌다.**

## 만들 때의 순서

```text
[T0] 네임스페이스를 만든다
     → default SA 자동 생성. 토큰은 아직 없다

[T1] Pod 를 그냥 배포한다 (serviceAccountName 미지정)
     → Admission 이 "default" 를 채워 넣는다
     → Admission 이 토큰 볼륨을 spec 에 추가한다
     → kubelet 이 "이 Pod 용 토큰" 을 요청해 받아 마운트한다
     → 앱이 호출 → 403 (신원은 있는데 권한이 없다)

[T2] 전용 SA 를 만든다        → 계정만 생겼다. 권한 없음
[T3] Role 을 만든다           → 규칙만 생겼다. 아무에게도 안 붙었다 → 여전히 no
[T4] RoleBinding 을 만든다    → 연결됐다 → can-i 는 yes
     그런데 Pod 는 아직 default SA 를 쓴다 → 앱은 여전히 403   ★ 흔한 실수
[T5] Pod 를 serviceAccountName 으로 다시 만든다 → 200
```

```text
serviceAccountName 은 Pod 생성 시 정해진다
돌고 있는 Pod 의 계정을 바꿀 수 없다 → 06 의 환경 변수와 같은 이유
```

## 요청 하나가 처리될 때

```text
앱: GET /api/v1/namespaces/k8s-lab/pods  +  Authorization: Bearer <토큰>
       ▼
apiserver
  1. 토큰 서명 검증                                    (인증)
  2. sub 를 읽는다
     "system:serviceaccount:k8s-lab:monitor-sa"        ← SA 가 여기서 쓰인다
  3. 그 신원에 걸린 바인딩을 찾는다
     → monitor-binding                                 ← RoleBinding 이 여기서
  4. 그 바인딩이 가리키는 Role 을 읽는다
     → pods: get, list                                 ← Role 이 여기서
  5. 지금 요청과 대조한다
     요청: list pods in k8s-lab / 규칙: list pods → 일치
  6. 200
```

```text
[ServiceAccount]  Pod 생성 시 "이 계정으로 토큰을 발급해달라"
                  요청 시 토큰의 sub 에서 "누구인지" 를 읽는 근거
[Role]            요청이 올 때만 읽는다. 평소에는 그냥 텍스트다
[RoleBinding]     신원과 Role 을 잇는 다리. 없으면 apiserver 가 Role 을 못 찾는다
```

```text
요청마다 이 셋을 매번 조회한다. 미리 계산해두지 않는다
→ 그래서 RoleBinding 을 걸자마자 Pod 재시작 없이 200 이 된다 (6절 실측)
```

## 무엇 하나만 빠져도 안 된다

```text
SA 없음           → Pod 가 안 만들어진다 (Admission 이 거부. 11절)
Role 없음         → RoleBinding 이 가리킬 게 없다
RoleBinding 없음  → 신원과 규칙이 안 이어진다 → 403     (T3)
Pod 에 SA 미지정   → 권한 준 계정과 Pod 가 쓰는 계정이 다르다 → 403  (T4)
```

## 그런데 대부분의 Pod 는 이게 필요 없다 ★

```text
[apiserver 를 부르는 앱]     ← 소수
  Istio / ArgoCD / Prometheus / cert-manager
  Calico / kube-proxy        ← 07, 03 에서 이미 본 것들

[apiserver 를 안 부르는 앱]  ← 대부분
  nginx / Spring / Node / MySQL / Redis / 배치 작업
```

```text
"이 Pod 가 apiserver 를 부를 일이 있나?"
  없다 → 아무것도 만들지 않는다. 토큰도 끈다
  있다 → SA / Role / RoleBinding / serviceAccountName 넷을 만든다
```

> **ArgoCD 는 사실상 cluster-admin 급이 필요하다.** Git 대로 모든 오브젝트를 만들고
> 고치기 때문이다. 그래서 ArgoCD 가 탈취되면 클러스터가 통째로 넘어간다.
> "Git 만 고치면 배포된다" 는 "Git 을 고칠 수 있으면 클러스터를 고칠 수 있다" 와 같은 말이다.
> 6단계에서 다룰 지점이다.

---

# 1. 지금 나는 누구인가

```text
root@master01:/# kubectl auth whoami
ATTRIBUTE                                           VALUE
Username                                            kubernetes-admin
Groups                                              [kubeadm:cluster-admins system:authenticated]
Extra: authentication.kubernetes.io/credential-id   [X509SHA256=4b08d4e3ad74b21f...]
```

```text
X509 = 클라이언트 인증서로 인증했다는 뜻
admin.conf 안의 그 인증서다 (1단계)

인증서의 CN  → Username    kubernetes-admin
인증서의 O   → Group       kubeadm:cluster-admins
```

**`system:authenticated` 는 인증서에 없다.** apiserver 가 "인증에 성공한 모든 신원" 에게 자동으로 붙인다.

```text
root@master01:/# kubectl auth can-i --list | head -3
Resources    Non-Resource URLs   Resource Names   Verbs
*.*          []                  []               [*]
             [*]                 []               [*]
```

**`kubectl auth can-i` 는 실제로 해보지 않고 권한을 물어보는 명령이다.**

## 발견 1 — --as 로 다른 신원인 척 물어볼 수 있다

```text
root@master01:/# kubectl auth can-i delete namespace
yes
root@master01:/# kubectl auth can-i delete namespace --as=system:serviceaccount:k8s-lab:default
no
root@master01:/# kubectl auth can-i get pods --as=system:serviceaccount:k8s-lab:default
no
```

```text
impersonation 이라고 한다
실제로 그 계정이 될 필요 없이 권한만 확인할 수 있다
다만 이것도 권한이 있어야 쓸 수 있다
```

**같은 서버, 같은 kubectl, 같은 명령인데 답이 다르다.** 이것이 3절의 근거다.

## 곁가지 — 07 의 내용이 경고로 나온다

```text
Warning: resource 'namespaces' is not namespace scoped
```

`namespaces` 가 cluster-scoped 라 나온 경고다. `can-i` 는 기본으로 현재 네임스페이스를 붙여 묻는데 그게 의미 없는 리소스라 알려준 것이다.

---

# 2. "권한 없음" 의 실제 뜻

```text
root@master01:/# kubectl auth can-i --list --as=system:serviceaccount:k8s-lab:default
Resources                                       Non-Resource URLs                      Verbs
selfsubjectreviews.authentication.k8s.io        []                                     [create]
selfsubjectaccessreviews.authorization.k8s.io   []                                     [create]
selfsubjectrulesreviews.authorization.k8s.io    []                                     [create]
                                                [/.well-known/openid-configuration]    [get]
                                                [/api] [/api/*] [/apis] [/apis/*]      [get]
                                                [/healthz] [/livez] [/readyz]          [get]
                                                [/openapi] [/openapi/*]                [get]
                                                [/openid/v1/jwks]                      [get]
                                                [/version]                             [get]
```

**목록이 비어 있지 않다.** 오브젝트를 읽고 쓰는 권한만 없을 뿐이다.

## 발견 2 — 그 최소한이 어디서 오는가

```text
root@master01:/# kubectl get clusterrolebindings -o custom-columns='NAME:...,SUBJECTS:...' \
                   | grep -E 'basic-user|discovery|public-info'
system:basic-user                        system:authenticated
system:discovery                         system:authenticated
system:public-info-viewer                system:authenticated,system:unauthenticated
system:service-account-issuer-discovery  system:serviceaccounts     ← 넷째
```

```text
[system:basic-user]   "내가 누구인지, 내가 뭘 할 수 있는지" 를 물어보는 것
                      자기 자신에 대해서만. 남의 권한은 못 묻는다
[system:discovery]    "이 클러스터에 어떤 API 가 있는지" 를 읽는 것
                      kubectl 이 시작할 때 이걸 읽어 명령을 만든다
```

```text
이 둘이 없으면 kubectl 이 아예 동작을 못 한다
→ 인증만 되면 무조건 준다
```

## 발견 3 — ServiceAccount 가 admin 보다 더 가진 것이 있다 ★

```text
[admin 목록에 없고 SA 목록에만 있는 것]
  /.well-known/openid-configuration
  /openid/v1/jwks
```

```text
system:service-account-issuer-discovery 의 대상이
system:serviceaccounts 그룹이다
→ 모든 ServiceAccount 가 속한다
→ 사람(kubernetes-admin)은 여기 없다
```

```text
"이 클러스터가 발급한 토큰을 어떻게 검증하는가" 를 알려주는 정보다
외부 시스템이 ServiceAccount 토큰을 검증할 때 쓴다
```

**10단계 EKS 에서 다시 만난다.** AWS 가 이 정보를 읽어 Pod 의 신원을 검증하고 IAM 역할을 준다(IRSA).

## 발견 4 — autoupdate 어노테이션

```text
Annotations:  rbac.authorization.kubernetes.io/autoupdate: true
```

**1단계 인가편에서 본 그것이다.** 사람이 고쳐도 apiserver 가 재시작하면 되돌린다. 시스템 동작에 필수라 함부로 못 고치게 한 것이다.

---

# 3. 권한은 서버가 아니라 신원에 붙는다 ★★

## 같은 노드에서 kubeconfig 를 바꾸면 신원이 바뀐다

```text
root@master01:/# kubectl auth whoami
Username  kubernetes-admin
Groups    [kubeadm:cluster-admins system:authenticated]

root@master01:/# sudo KUBECONFIG=/etc/kubernetes/kubelet.conf kubectl auth whoami
Username  system:node:master01
Groups    [system:nodes system:authenticated]

root@master01:/# sudo KUBECONFIG=/etc/kubernetes/scheduler.conf kubectl auth whoami
Username  system:kube-scheduler
Groups    [system:authenticated]

root@master01:/# sudo KUBECONFIG=/etc/kubernetes/controller-manager.conf kubectl auth whoami
Username  system:kube-controller-manager
Groups    [system:authenticated]
```

```text
같은 노드, 같은 kubectl 바이너리
파일만 바꿨을 뿐인데 신원이 넷이 된다
→ 노드에 매핑돼 있다면 이럴 수 없다
```

```text
root@master01:/# sudo KUBECONFIG=/etc/kubernetes/kubelet.conf kubectl auth can-i delete namespace
no
```

**kubelet 은 노드에 있으면서도 네임스페이스를 못 지운다.** "노드의 권한" 같은 건 없다.

## 발견 5 — 세 곳이 나뉘어 있다 ★

```text
[신원]  누구인가          인증서 / 토큰 (파일에 있다)
[규칙]  무엇을 할 수 있나  Role / ClusterRole
[연결]  누구에게 주나      RoleBinding / ClusterRoleBinding
```

```text
파일에도 권한이 없다. 파일에는 신분증만 있다
권한은 apiserver 안의 바인딩에 있다

파일        신분증
바인딩      출입 권한 명부
신분증이 있어도 명부에 이름이 없으면 못 들어간다
```

**셋이 다 있어야 권한이 성립한다.** 7절에서 하나씩 붙여가며 확인한다.

## 그래서 admin.conf 가 위험하다

```text
apiserver 는 "어디서 왔나" 를 안 본다. "무엇을 제시했나" 만 본다
→ 파일을 복사해 어디서 써도 똑같이 admin 이다
→ 클러스터 밖의 노트북이어도 된다 (apiserver 에 닿기만 하면)
```

```text
그리고 인증서는 취소가 어렵다
  비밀번호는 바꾸면 끝
  인증서는 만료될 때까지 유효하다 (기본 1년)
  → 유출되면 CA 를 갈아야 한다 = 클러스터를 다시 세우는 수준
```

## 발견 6 — Kubernetes 에는 User 오브젝트가 없다

```bash
kubectl get users
```

```text
[예상]  error: the server doesn't have a resource type "users"
```

```text
"kubernetes-admin 이라는 사용자를 만든다" 같은 게 불가능하다
→ 인증서를 발급하는 순간 그 사람이 존재하게 된다
→ 사용자 관리를 Kubernetes 밖에 맡긴 것이다 (인증서 / OIDC / 클라우드 IAM)

ServiceAccount 만 진짜 오브젝트다
그래서 이 문서의 실습이 ServiceAccount 로 이루어진다
```

> **미확인**: `kubectl get users` 를 실제로 실행하지 않았다.

---

# 4. 컴포넌트마다 신원과 권한이 나뉘어 있다

## 프로그램마다 conf 가 고정돼 있다

```bash
sudo grep -i kubeconfig /etc/kubernetes/manifests/kube-scheduler.yaml
sudo grep -i kubeconfig /etc/kubernetes/manifests/kube-controller-manager.yaml
sudo systemctl cat kubelet | grep -i kubeconfig
```

```text
실행 명령에 --kubeconfig 로 못 박혀 있다
프로그램이 실행 중에 다른 걸 고르는 게 아니다
```

## 발견 7 — 신원이 붙는 방식도 다르다

```text
kubelet     Username  system:node:master01
            Group     system:nodes             ← 그룹으로 묶인다

scheduler   Username  system:kube-scheduler
            Group     (없음)                    ← 이름으로 직접
```

```text
노드는 여러 대다. 이름이 다 다르다
  system:node:master01 / system:node:worker01 / ...
→ 하나하나 바인딩을 걸 수 없다 → system:nodes 그룹으로 묶는다

scheduler 는 클러스터에 하나뿐이다 → 이름으로 직접 걸면 된다
```

**kubelet 은 Node Authorizer 라는 별도 인가 방식으로도 처리된다.** 1단계 인가편에서 다룬 그것이다.

## 발견 8 — controller-manager 가 1단계의 의문을 푼다 ★

```bash
sudo grep -i 'use-service-account-credentials' /etc/kubernetes/manifests/kube-controller-manager.yaml
```

```text
[예상]  --use-service-account-credentials=true
```

```text
[1단계에서 본 것]  system:controller:* 바인딩이 40여 개. 왜 이렇게 많지?

[답]
  controller-manager 는 컨트롤러를 수십 개 돌린다
    deployment-controller / replicaset-controller / endpointslice-controller
    root-ca-cert-publisher / namespace-controller ...

  이 플래그가 켜져 있으면
  → 컨트롤러마다 자기 ServiceAccount 로 apiserver 를 호출한다
  → 그래서 SA 도 40여 개, 바인딩도 40여 개다
```

```text
controller-manager 자신의 신원은 그 SA 들의 토큰을 발급받는 데만 쓴다
실제 작업은 각 컨트롤러의 SA 로 한다
```

**07 에서 본 `root-ca-cert-publisher` 도 그중 하나였다.**

> **미확인**: 이 플래그와 `kubectl get sa -n kube-system` 목록을 실제로 조회하지 않았다.

## 같은 설계 원칙이 두 가지로 나타난다

```text
[역할의 분리]
  apiserver 는 저장만 / 컨트롤러는 만들기만 / kubelet 은 자기 노드만
  → 하나가 죽어도 나머지가 돈다   (1단계 장애 실험 4개)

[권한의 분리]
  각자 자기 일에 필요한 만큼만 갖는다
  → 하나가 뚫려도 그것만 뚫린다
```

```text
scheduler 가 탈취돼도 Secret 은 못 읽는다
kubelet 이 탈취돼도 네임스페이스는 못 지운다
```

---

# 5. 실험 준비 — Pod 안에서 apiserver 를 부른다 (2026-08-21)

```bash
kubectl -n k8s-lab run apitest --image=curlimages/curl:latest --restart=Never -- sleep 3600
kubectl -n k8s-lab wait --for=condition=Ready pod/apitest --timeout=120s
kubectl -n k8s-lab get pod apitest -o jsonpath='{.spec.serviceAccountName}{"\n"}'
```

```text
apitest   10.244.5.62   worker01
serviceAccountName: default      ← 지정 안 했으니 그 네임스페이스의 default SA
```

```text
curl 이 든 이미지를 쓴다
nginx 나 busybox 에는 TLS 검증까지 되는 curl 이 없다
```

## 발견 9 — 3종 세트가 06 의 구조 그대로다

```text
root@master01:/# kubectl -n k8s-lab exec apitest -- ls -la /var/run/secrets/kubernetes.io/serviceaccount/
drwxrwxrwt 3 root root  140  .                              ← t = sticky bit. tmpfs
drwxr-xr-x 2 root root  100  ..2026_08_21_00_31_26.543032714
lrwxrwxrwx 1 root root   31  ..data -> ..2026_08_21_00_31_26.543032714
lrwxrwxrwx 1 root root   13  ca.crt -> ..data/ca.crt
lrwxrwxrwx 1 root root   16  namespace -> ..data/namespace
lrwxrwxrwx 1 root root   12  token -> ..data/token
```

**ConfigMap 볼륨과 완전히 같은 링크 3단 구조다.** 토큰이 갱신될 때 `..data` 만 갈아끼운다.

## 발견 10 — 403 이지 401 이 아니다

```text
root@master01:/# kubectl -n k8s-lab exec apitest -- sh -c '
TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
curl -s -o /dev/null -w "%{http_code}\n" \
  --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  -H "Authorization: Bearer $TOKEN" \
  https://kubernetes.default.svc/api/v1/namespaces/k8s-lab/pods'
403
```

```text
401 Unauthorized   "너 누구야?"        인증 실패
403 Forbidden      "너인 건 알겠는데"   인가 실패
```

**토큰은 유효하니 인증은 통과했고, 권한이 없어 인가에서 막혔다.**

## 발견 11 — 실패 메시지가 Role 의 문법을 알려준다 ★

```json
{
  "kind": "Status",
  "status": "Failure",
  "message": "pods is forbidden: User \"system:serviceaccount:k8s-lab:default\"
              cannot list resource \"pods\" in API group \"\"
              in the namespace \"k8s-lab\"",
  "reason": "Forbidden",
  "code": 403
}
```

```text
User        system:serviceaccount:k8s-lab:default    누가
verb        list                                     무엇을
resource    pods                                     어떤 것에
apiGroup    "" (core)                                어느 그룹의
namespace   k8s-lab                                  어디서
```

**실패 메시지를 읽으면 무엇을 허용해야 하는지 그대로 나온다.** 이것이 RBAC 을 짜는 실무 방법이다.

---

# 6. 403 에서 200 으로 ★★

## Role 만 만들면 아직 아무 일도 없다

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: k8s-lab
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
```

```text
root@master01:/# kubectl auth can-i list pods -n k8s-lab --as=system:serviceaccount:k8s-lab:default
no
```

```text
[신원]  default SA        있다
[규칙]  pod-reader Role   방금 만들었다
[연결]  RoleBinding       없다      ← 이것 때문에 아직 no
```

## 각 필드의 뜻

```text
apiGroups: [""]
  빈 문자열이 "core" 그룹이다
  pods / services / configmaps / secrets / nodes / namespaces ...
  다른 그룹의 예
    "apps"                       deployments / replicasets / statefulsets
    "networking.k8s.io"          ingresses / networkpolicies
    "rbac.authorization.k8s.io"  roles / rolebindings
```

**07 의 `kubectl api-resources` 출력 두 번째 열이 그것이다.**

```text
resources: ["pods"]
  복수형. URL 경로에 쓰이는 이름 그대로다
  /api/v1/namespaces/k8s-lab/pods
```

```text
verbs
  get     개별 조회      GET .../pods/apitest
  list    목록 조회      GET .../pods
  watch   변경 감시      06 에서 본 그것
  create / update / patch / delete / deletecollection
```

**`get` 과 `list` 는 다른 권한이다.** list 를 안 주면 "무엇이 있는지" 자체를 모른다.

## RoleBinding 을 걸면 즉시 바뀐다

```yaml
kind: RoleBinding
metadata:
  name: pod-reader-binding
  namespace: k8s-lab
subjects:
- kind: ServiceAccount
  name: default
  namespace: k8s-lab
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

```text
subjects   누구에게 줄 것인가 (여러 명 가능)
roleRef    어떤 규칙을 줄 것인가 (하나만. 나중에 바꿀 수도 없다)
```

```text
root@master01:/# kubectl auth can-i list pods    -n k8s-lab --as=...:default    yes
root@master01:/# kubectl auth can-i delete pods  -n k8s-lab --as=...:default    no
root@master01:/# kubectl auth can-i list secrets -n k8s-lab --as=...:default    no
```

```text
delete   verbs 에 안 넣었다
secrets  resources 에 안 넣었다
```

## 발견 12 — Pod 를 재시작하지 않았는데 권한이 바뀐다 ★★

```text
[Role 만 있을 때]   403
[RoleBinding 후]    200
```

```text
토큰도 그대로, 신원도 그대로다
바뀐 것은 apiserver 안의 바인딩뿐이다
→ "권한은 파일이 아니라 apiserver 에 있다" 가 실측으로 확인됐다
```

## 발견 13 — Role 은 namespaced 다

```text
root@master01:/# ... https://kubernetes.default.svc/api/v1/namespaces/kube-system/pods
403
```

```text
k8s-lab 에 건 Role 은 k8s-lab 안에서만 통한다 (07 에서 본 그것)
```

---

# 7. Role 과 ClusterRole — 조합 네 가지 ★

```text
                          Role            ClusterRole
─────────────────────────────────────────────────────────────
RoleBinding               그 네임스페이스   그 네임스페이스만    ★ 헷갈리는 조합
ClusterRoleBinding        불가능           클러스터 전체
```

## 발견 14 — ClusterRole 을 RoleBinding 으로 붙이면 그 네임스페이스에만 적용된다 ★★

```yaml
kind: ClusterRole
metadata:
  name: pod-reader-cluster       # namespace 필드가 없다. cluster-scoped 니까
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list"]
```

```yaml
kind: RoleBinding
metadata:
  namespace: k8s-lab
roleRef:
  kind: ClusterRole              # Role 이 아니라 ClusterRole
  name: pod-reader-cluster
```

```text
root@master01:/# kubectl auth can-i list pods -n k8s-lab     --as=...:default   yes
root@master01:/# kubectl auth can-i list pods -n kube-system --as=...:default   no
```

```text
ClusterRole 인데도 kube-system 은 안 된다
→ 규칙이 어디 정의됐느냐가 아니라, 어디에 연결됐느냐가 범위를 정한다
```

## 왜 이 조합이 필요한가

```text
"Pod 를 읽을 수 있다" 는 규칙을 10개 네임스페이스에서 쓰고 싶다

[Role 로 하면]  네임스페이스마다 똑같은 Role 을 10개 만든다
                규칙을 고치면 10군데를 고쳐야 한다

[ClusterRole + RoleBinding]  ClusterRole 하나만 정의한다
                             각 네임스페이스에서 RoleBinding 으로 연결한다
                             → 규칙은 하나, 적용 범위는 네임스페이스별
```

**Kubernetes 기본 ClusterRole 이 이렇게 쓰라고 만들어져 있다.**

```text
root@master01:/# kubectl get clusterrole view edit admin cluster-admin
view / edit / admin / cluster-admin   전부 2026-08-03 (kubeadm init 시각)
```

```text
view          읽기만
edit          읽기 + 쓰기 (RBAC 은 못 건드린다)
admin         edit + RBAC 관리 (그 네임스페이스 안에서)
cluster-admin 전부

앞의 셋은 RoleBinding 으로 네임스페이스에 붙이라고 만든 것이다
"team-a 에게 team-a 네임스페이스의 admin 을 준다" 같은 식
```

## 발견 15 — ClusterRoleBinding 으로 바꾸면 전체가 된다

```text
root@master01:/# kubectl auth can-i list pods -n kube-system --as=...:default   yes
root@master01:/# kubectl auth can-i list pods -A            --as=...:default   yes
```

```text
Pod 안에서도 kube-system 조회가 403 → 200
```

## 발견 16 — 네 번째 조합은 CLI 에 옵션조차 없다

```text
root@master01:/# kubectl create clusterrolebinding bad-binding --role=pod-reader ...
error: unknown flag: --role
```

```text
--clusterrole 만 있다. --role 은 없다
옵션이 없다는 것 자체가 답이다
```

```text
왜 안 되나
  Role 은 특정 네임스페이스의 것이다
  ClusterRoleBinding 은 전체에 적용된다
  → "k8s-lab 의 규칙을 전체에 적용" 은 말이 안 된다
```

> **미확인**: yaml 로 억지로 만들어 apiserver 의 검증 오류를 직접 보지 않았다.

## 발견 17 — ClusterRole 집계 (aggregation)

```text
root@master01:/# kubectl describe clusterrole view | head -5
Name:    view
Labels:  kubernetes.io/bootstrapping=rbac-defaults
         rbac.authorization.k8s.io/aggregate-to-edit=true      ← 이 라벨
```

```text
edit 은 규칙을 직접 안 갖는다
"이 라벨이 붙은 ClusterRole 들의 규칙을 전부 합친 것" 이다
```

**라벨 셀렉터가 여기서도 쓰인다.**

```text
Service → Pod                라벨
Service → EndpointSlice      라벨
NetworkPolicy → 네임스페이스   라벨
ClusterRole → ClusterRole    라벨   ← 새로 나온 것
```

```text
[왜 유용한가]
  오퍼레이터를 설치해 새 CRD 가 생겼다
  그 리소스를 edit 권한자가 다룰 수 있게 하고 싶다

  집계가 없다면  기본 edit 을 직접 고쳐야 한다 → autoupdate 로 되돌려진다
  집계가 있으면  라벨 붙인 ClusterRole 을 하나 추가하면 자동으로 합쳐진다
```

> **미확인**: `kubectl get clusterrole edit -o jsonpath='{.aggregationRule}'` 미조회.

---

# 8. default SA 에 권한을 주면 안 되는 이유 ★

## 발견 18 — 그 네임스페이스의 모든 Pod 가 갖게 된다

```bash
kubectl -n k8s-lab run apitest2 --image=curlimages/curl:latest --restart=Never -- sleep 3600
```

```text
root@master01:/# kubectl -n k8s-lab exec apitest2 -- ... /namespaces/kube-system/pods
200
```

```text
새로 만든 Pod 인데 아무것도 안 했는데 권한이 있다
default SA 를 쓰기 때문이다
```

```text
[실무 사고]
  "모니터링 앱이 Pod 목록을 읽어야 한다" 며 default SA 에 권한을 줬다
  → 그 네임스페이스의 nginx 도, 배치 작업도, 전부 그 권한을 갖는다
  → 하나가 탈취되면 그 권한이 통째로 넘어간다
```

## 발견 19 — 전용 계정으로 나누면 같은 네임스페이스에서도 갈린다

```bash
kubectl -n k8s-lab create serviceaccount pod-reader-sa
# RoleBinding 의 subject 를 pod-reader-sa 로 바꾼다
```

```text
root@master01:/# kubectl auth can-i list pods -n k8s-lab --as=...:default         no
root@master01:/# kubectl auth can-i list pods -n k8s-lab --as=...:pod-reader-sa   yes
```

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: apitest3
spec:
  serviceAccountName: pod-reader-sa     # 명시적으로 지정
  containers:
  - name: curl
    image: curlimages/curl:latest
    command: ["sleep", "3600"]
```

```text
root@master01:/# kubectl -n k8s-lab exec apitest3 -- ... /namespaces/k8s-lab/pods
200
root@master01:/# kubectl -n k8s-lab exec apitest -- ... /namespaces/k8s-lab/pods
403
```

**같은 네임스페이스의 두 Pod 인데 권한이 갈린다.** 이것이 계정을 나누는 이유다.

## 안 쓰면 토큰을 아예 안 붙이는 게 낫다

```yaml
spec:
  automountServiceAccountToken: false
```

```text
nginx 는 apiserver 를 부를 일이 없다
그런데 토큰이 들어 있으면
→ 컨테이너가 탈취됐을 때 그 토큰으로 apiserver 를 호출할 수 있다
→ 안 쓰는 열쇠를 주머니에 넣고 다니는 셈이다
```

> **미확인**: `automountServiceAccountToken: false` 를 실제로 적용해보지 않았다.

---

# 9. ServiceAccount 는 네임스페이스의 오브젝트다

## 발견 20 — 이름에 네임스페이스가 들어간다

```text
User "system:serviceaccount:k8s-lab:default"
      ^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^ ^^^^^^^
      고정 접두사            네임스페이스  이름
```

```text
"default" 만으로는 누구인지 알 수 없다
k8s-lab 의 default 와 team-b 의 default 는 다른 계정이다
→ 네임스페이스를 붙여야 유일해진다
```

**User 와 대비하면 명확하다.**

```text
[User]  kubernetes-admin / system:kube-scheduler
        네임스페이스가 없다
        → 오브젝트가 아니다. 인증서에서 오는 이름일 뿐이다

[SA]    system:serviceaccount:k8s-lab:default
        네임스페이스가 이름의 일부
        → namespaced 오브젝트다 (07 에서 본 목록에 있다)
```

## 발견 21 — Pod 는 자기 네임스페이스의 SA 만 쓸 수 있다

```yaml
spec:
  serviceAccountName: pod-reader-sa      # 이름만 쓴다
  # serviceAccountNamespace: team-b      # 이런 필드가 없다
```

**07 의 "오브젝트 참조는 네임스페이스를 못 넘는다" 가 여기서도 적용된다.**

```text
그래서 네임스페이스마다 default SA 가 자동으로 생긴다
없으면 Pod 를 아예 못 만든다
→ 06 의 kube-root-ca.crt 와 같은 이유다
```

## 발견 22 — 그룹도 자동으로 둘 붙는다

```text
system:serviceaccounts              모든 ServiceAccount
system:serviceaccounts:k8s-lab      k8s-lab 의 모든 ServiceAccount
```

```text
두 번째는 "이 네임스페이스의 모든 Pod 에게" 라는 바인딩에 쓸 수 있다
다만 default SA 에 주는 것과 같은 위험이 있다
```

**2절의 `system:service-account-issuer-discovery` 가 첫 번째 그룹에 걸려 있었다.**

> **미확인**: `--as-group` 으로 그룹 권한을 확인하지 않았다.

---

# 10. 토큰은 언제 만들어지나 ★

**여기서 오래된 자료와 지금이 다르다. 혼동의 원인이다.**

```text
[Kubernetes 1.24 이전 — 옛 방식]
  kubectl create sa foo
  → Secret "foo-token-xxxxx" 가 자동으로 생긴다
  → 그 Secret 안에 JWT 가 들어 있다. 만료가 없다
  → Pod 에 그 Secret 을 마운트한다

[1.24 이후 — 지금 방식]
  kubectl create sa foo
  → Secret 이 안 생긴다        ★
  → Pod 가 뜰 때 kubelet 이 apiserver 에 토큰을 요청한다 (TokenRequest API)
  → 짧은 수명 (기본 1시간). 자동 갱신
  → 그 Pod 에 묶여 있다. Pod 가 죽으면 무효
```

## 발견 23 — SA 를 만들어도 Secret 이 안 생긴다

```text
root@master01:/# kubectl -n k8s-lab create sa test-sa
serviceaccount/test-sa created
root@master01:/# kubectl -n k8s-lab get secret
(SA 토큰 Secret 이 하나도 없다)
```

**07 에서 `kubectl get sa,cm -n k8s-lab` 을 쳤을 때 Secret 이 없었던 것이 이 이야기였다.**

## 왜 바꿨나

```text
[옛 방식의 문제]
  토큰이 만료되지 않는다 → 유출되면 영원히 유효하다
  Secret 에 저장된다     → etcd 에 그대로 남는다 (06 에서 확인)
  어느 Pod 가 썼는지 모른다 → 여러 Pod 가 같은 토큰을 쓴다

[지금 방식]
  1시간마다 갱신
  Pod 에 묶여 있다
  Secret 오브젝트를 안 만든다
  audience 가 있다 → 지정된 대상에게만 유효
```

**06 에서 본 토큰 디렉터리 날짜 갱신이 그 갱신이었다.**

```text
drwxrwxrwt 3 root root 140 Aug 20 13:41 kube-api-access-7cjcl
Pod 는 Aug 10 생성인데 디렉터리는 Aug 20 → kubelet 이 갱신하고 링크를 갈아끼운 것
```

## 토큰 안의 내용

```bash
kubectl -n k8s-lab exec apitest -- cat /var/run/secrets/kubernetes.io/serviceaccount/token \
  | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null; echo
```

```json
{
  "aud": ["https://kubernetes.default.svc.cluster.local"],
  "exp": 1755...,
  "iss": "https://kubernetes.default.svc.cluster.local",
  "kubernetes.io": {
    "namespace": "k8s-lab",
    "pod": { "name": "apitest", "uid": "..." },
    "serviceaccount": { "name": "default", "uid": "..." }
  },
  "sub": "system:serviceaccount:k8s-lab:default"
}
```

```text
sub    apiserver 가 읽는 신원. --as 에 쓴 문자열과 같다
exp    만료 시각
pod    이 토큰이 그 Pod 전용이라는 표시
aud    이 토큰이 유효한 대상
```

> **미확인**: 토큰을 실제로 디코딩하지 않았다. 위 내용은 형식 설명이다.

---

# 11. default 는 누가 붙여주나 — Admission ★★

**우리 yaml 에 `serviceAccountName` 을 쓴 적이 없는데 붙어 있었다.**

```text
root@master01:/# kubectl -n k8s-lab get pod apitest -o jsonpath='{.spec.serviceAccountName}'
default
```

## 발견 24 — ServiceAccount admission plugin 이 한다

```text
1. serviceAccountName 이 비어 있으면  → "default" 로 채운다
2. 지정된 SA 가 없으면                 → 거부한다
3. 토큰 볼륨을 자동으로 추가한다
   (automountServiceAccountToken 이 true 일 때)
```

```bash
kubectl -n k8s-lab get pod apitest -o yaml | grep -A3 'volumes:'
```

```text
volumes:
- name: kube-api-access-xxxxx
  projected: ...
```

**우리 yaml 에 이 볼륨을 쓴 적이 없다.** Admission 이 넣어준 것이다.

## 발견 25 — 없는 SA 를 지정하면 Pod 가 아예 안 만들어진다

```yaml
spec:
  serviceAccountName: does-not-exist
```

```text
root@master01:/# kubectl apply -f /tmp/nosa.yaml
Error from server (Forbidden): pods "nosa-test" is forbidden:
error looking up service account k8s-lab/does-not-exist:
serviceaccount "does-not-exist" not found

root@master01:/# kubectl -n k8s-lab get pod nosa-test
Error from server (NotFound): pods "nosa-test" not found
```

```text
[SA 지정은 선택이다]
  안 쓰면  Admission 이 default 를 채운다
  쓰면     그 SA 가 미리 있어야 한다
```

## 발견 26 — 403 이 무조건 RBAC 은 아니다 ★★

**1단계 07 문서 1라운드에서 본 요청 처리 흐름을 떠올리면 된다.**

```text
요청이 apiserver 에 온다
   ▼
1. 인증 (Authentication)      너 누구야?           실패 → 401
   ▼
2. 인가 (Authorization/RBAC)  그거 할 수 있어?      실패 → 403
   ▼
3. Admission                  내용이 말이 되나?     실패 → 403 또는 400
   ▼
4. 검증 (Validation)          형식이 맞나?
   ▼
5. etcd 에 저장
```

```text
우리는 3번에서 막혔다
RBAC 은 통과했다 (admin 이니 Pod 를 만들 권한이 있다)
"없는 SA 를 가리킨다" 는 내용 때문에 Admission 이 거부했다
```

**메시지 형식으로 구분한다.**

```text
[RBAC 이 막았다면]
  "User ... cannot create resource \"pods\" in namespace ..."
  → 누가 무엇을 못 한다는 형식

[Admission 이 막았다]
  "error looking up service account k8s-lab/does-not-exist"
  → 내용이 잘못됐다는 형식
```

```text
403 이 나왔다고 무조건 권한 문제는 아니다
메시지를 읽어야 어디서 막혔는지 안다
```

---

# 12. RBAC 에는 "거부" 가 없다 ★

```text
Role 에 쓸 수 있는 것은 허용뿐이다
"이건 빼고 다 줘" 를 표현할 방법이 없다
```

```text
바인딩이 여러 개 걸리면 권한은 합집합이 된다
  RoleBinding A → pods 읽기
  RoleBinding B → secrets 읽기
  → 둘 다 갖는다

빼는 방법은 바인딩을 지우는 것뿐이다
```

```text
"cluster-admin 을 주되 Secret 만 못 읽게" → 불가능하다
→ 최소한으로 시작해서 필요한 것만 더해가야 한다
```

## 07 의 NetworkPolicy 와 정반대다

```text
[NetworkPolicy]
  정책이 없으면        전부 허용
  하나라도 걸면        명시한 것만 허용 (화이트리스트로 전환)

[RBAC]
  바인딩이 없으면      전부 거부
  바인딩을 걸면        그만큼만 허용 (더하기만 된다)
```

```text
NetworkPolicy 는 "열려 있다가 걸면 닫힌다"
RBAC 은 "닫혀 있고 여는 것만 가능하다"
```

**6절에서 Role 만 만들었을 때 여전히 `no` 였던 것이 이 때문이다.**

## 유일한 예외 — system:masters

```bash
sudo KUBECONFIG=/etc/kubernetes/super-admin.conf kubectl auth whoami
```

```text
[예상]
  Username  kubernetes-super-admin
  Groups    [system:masters system:authenticated]
```

```text
system:masters 그룹은 RBAC 을 아예 건너뛴다
바인딩을 확인하지 않고 무조건 통과시킨다
```

```text
admin.conf        RBAC 을 통과해서 권한을 얻는다
                  → RBAC 이 망가지면 이것도 못 쓴다
super-admin.conf  RBAC 을 건너뛴다
                  → RBAC 을 망가뜨렸을 때의 비상용
```

**1단계 인가편에서 다룬 그것이다.** "세 조각이 다 있어야 한다" 의 유일한 예외이므로 평소에 쓰면 안 된다.

> **미확인**: super-admin.conf 로 `auth whoami` 를 실행하지 않았다.
> `kubectl get clusterrolebinding cluster-admin -o jsonpath='{.subjects}'` 도 미조회.

---

# 정리

```text
[신원]
 1. 권한은 서버가 아니라 신원에 붙는다
    같은 노드에서 kubeconfig 를 바꾸면 신원이 넷이 된다 (실측)
 2. 파일에도 권한이 없다. 파일에는 신분증(인증서/토큰)만 있다
    권한은 apiserver 안의 바인딩에 있다
 3. apiserver 는 "어디서 왔나" 를 안 본다. "무엇을 제시했나" 만 본다
    → admin.conf 를 복사하면 어디서든 admin 이다
 4. Kubernetes 에 User 오브젝트는 없다. ServiceAccount 만 오브젝트다
    사용자 관리를 밖에 맡긴 설계다

[세 조각]
 5. 신원 / 규칙(Role) / 연결(RoleBinding) 셋이 다 있어야 권한이 성립한다
    Role 만 만들면 아무 일도 안 일어난다 (실측: 여전히 no)

[실험]
 6. Pod 안에서 403 → RoleBinding 을 걸자 200
    Pod 를 재시작하지 않았다. 토큰도 신원도 그대로다
 7. 401 과 403 은 다르다. 인증 실패와 인가 실패
 8. 실패 메시지가 Role 의 문법을 그대로 알려준다
    User / verb / resource / apiGroup / namespace

[Role vs ClusterRole]
 9. 조합 넷 중 셋만 가능하다
10. ClusterRole 을 RoleBinding 으로 붙이면 그 네임스페이스에만 적용된다
    → 규칙이 어디 정의됐느냐가 아니라 어디에 연결됐느냐가 범위를 정한다
11. 기본 ClusterRole(view/edit/admin)은 그렇게 쓰라고 만든 것이다
12. ClusterRoleBinding 은 ClusterRole 만 가리킬 수 있다. CLI 에 옵션조차 없다
13. edit 은 규칙을 직접 안 갖고 라벨로 집계한다 (aggregate-to-edit)

[계정 분리]
14. default SA 에 권한을 주면 그 네임스페이스의 모든 Pod 가 갖는다 (실측)
15. 전용 SA 로 나누면 같은 네임스페이스에서도 권한이 갈린다 (실측)
16. 안 쓰면 automountServiceAccountToken: false 로 아예 안 붙이는 게 낫다

[ServiceAccount 의 위치]
16-1. SA 는 "Pod 의 계정" 이면서 "네임스페이스의 오브젝트" 다. 둘 다 맞다
      그래서 이름에 네임스페이스가 들어간다
      system:serviceaccount:<네임스페이스>:<이름>
16-2. User 는 오브젝트가 아니라 네임스페이스가 없다
16-3. Pod 는 자기 네임스페이스의 SA 만 쓸 수 있다 (참조가 못 넘는다)
      그래서 네임스페이스마다 default SA 가 자동으로 생긴다
16-4. 그룹도 자동으로 둘 붙는다
      system:serviceaccounts / system:serviceaccounts:<네임스페이스>

[토큰]
16-5. 토큰은 SA 를 만들 때가 아니라 Pod 가 뜰 때 발급된다 (1.24 이후)
      SA 를 만들어도 Secret 이 안 생긴다 (실측)
      짧은 수명, 자동 갱신, 그 Pod 에 묶여 있다
16-6. 옛 방식은 Secret 에 만료 없는 토큰을 저장했다
      오래된 자료를 보고 배우면 여기서 혼동한다

[Admission]
16-7. serviceAccountName 을 안 쓰면 Admission 이 default 를 채워 넣는다
      토큰 볼륨도 Admission 이 자동으로 추가한다
16-8. 없는 SA 를 지정하면 Pod 가 아예 안 만들어진다 (실측)
16-9. 403 이 무조건 RBAC 은 아니다. Admission 도 403 을 낸다
      메시지 형식으로 구분한다

[RBAC 의 성질]
16-10. RBAC 에는 거부 규칙이 없다. 허용만 있고 합집합이 된다
       "이건 빼고 다 줘" 를 표현할 수 없다
16-11. NetworkPolicy 와 정반대다
       NetworkPolicy  정책이 없으면 전부 허용
       RBAC           바인딩이 없으면 전부 거부
16-12. system:masters 그룹만 RBAC 을 건너뛴다 (super-admin.conf)

[컴포넌트]
17. 프로그램마다 kubeconfig 가 고정돼 있다 (--kubeconfig 플래그)
18. kubelet 은 그룹(system:nodes), scheduler 는 이름으로 직접 바인딩된다
    노드는 여러 대라 그룹이 필요하고, scheduler 는 하나뿐이다
19. controller-manager 는 컨트롤러마다 별도 SA 를 쓴다
    → 1단계에서 본 40여 개 system:controller:* 바인딩의 정체
20. "권한 없음" 이 0 은 아니다
    인증만 되면 basic-user / discovery / public-info-viewer 를 받는다
    ServiceAccount 는 issuer-discovery 도 받는다 (EKS IRSA 에서 쓰인다)
```

# 실습 리소스

```text
namespace   k8s-lab   유지
apitest / apitest2      default SA
apitest3                pod-reader-sa
pod-reader              Role
pod-reader-cluster      ClusterRole
pod-reader-binding      RoleBinding
pod-reader-sa           ServiceAccount
/tmp/rbac.yaml /tmp/rbac2.yaml /tmp/rbac3.yaml /tmp/rbac4.yaml /tmp/sa-pod.yaml
```

```bash
kubectl -n k8s-lab delete pod apitest apitest2 apitest3
kubectl -n k8s-lab delete rolebinding pod-reader-binding
kubectl -n k8s-lab delete role pod-reader
kubectl -n k8s-lab delete sa pod-reader-sa
kubectl delete clusterrole pod-reader-cluster
rm -f /tmp/rbac*.yaml /tmp/sa-pod.yaml
kubectl get all,sa,role,rolebinding -n k8s-lab
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              ServiceAccount  Pod 의 신원
                                Role/ClusterRole  규칙
                                RoleBinding/ClusterRoleBinding  연결
2. 생성 시 동작하는 Controller   ServiceAccount 는 컨트롤러가 토큰 볼륨을 붙인다
                                Role/Binding 은 apiserver 가 인가 판단에 쓴다
                                (별도 컨트롤러가 만드는 게 없다)
3. 주요 Spec 과 Status 필드     Role.rules[] — apiGroups / resources / verbs
                                                resourceNames / nonResourceURLs
                                Binding — subjects[] / roleRef
                                (status 가 없는 오브젝트다)
4. 다른 오브젝트와의 연결        Pod(serviceAccountName), Secret(옛 토큰 방식),
                                Namespace(Role/RoleBinding 의 범위)
5. 장애 사례                    6절 403 / 7절 네임스페이스 경계 /
                                8절 default SA 권한 확산
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            default SA 에 권한을 주지 말 것 /
                                admin.conf 를 배포하지 말 것 /
                                ClusterRoleBinding 은 최후에 /
                                안 쓰면 automountServiceAccountToken: false
```

# 미확인 목록

```text
1. kubectl get users 를 실행하지 않았다
2. --use-service-account-credentials 플래그와 kube-system 의 SA 목록 미조회
3. ClusterRoleBinding + Role 조합의 apiserver 검증 오류를 직접 보지 않았다
4. clusterrole edit 의 aggregationRule 미조회
5. automountServiceAccountToken: false 미실습
6. 토큰의 내용(JWT payload)을 디코딩해보지 않았다
   만료 시간 / audience / bound object 확인 미실시
7. 토큰 갱신(..data 링크 교체)을 관측하지 않았다
8. Role 의 resourceNames (특정 이름만 허용) 미실습
9. nonResourceURLs 규칙 미실습
10. 사용자(User) 인증서를 직접 발급해 권한을 주는 실습 미실시
11. Node Authorizer 가 실제로 어떻게 동작하는지 미확인
    (kubelet 이 남의 노드 Secret 을 못 읽는 것을 실측하지 않았다)
12. audit log 로 누가 무엇을 했는지 추적하는 것 미실습
```

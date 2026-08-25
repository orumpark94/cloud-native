# 07. Namespace

2단계 여덟 번째. **"경계" 라고 부르는 것이 실제로 무엇을 나누는지 확인한다.**

```text
[실습 중 두 번 걸린 문제]
  kubectl get svc kubernetes  →  NotFound   (default 에 있었다)
  kubectl get svc web         →  NotFound   (이름이 web-svc 였다)

kubectl get pods
  → 사실은 kubectl get pods -n k8s-lab 이다
```

```text
[흔한 오해]
  네임스페이스가 다르면 격리된다 → 통신이 안 된다 → 팀마다 주면 안전하다

[실제]
  이름의 경계일 뿐이다. 네트워크의 경계도 보안의 경계도 아니다
```

## 이 문서의 범위

```text
[확인한 것]
  1. namespaced 와 cluster-scoped 의 차이                   ✅ ★
  2. etcd 저장 경로가 그 차이의 실체다                        ✅ ★
  3. Namespace 오브젝트는 거의 비어 있다                      ✅
  4. 같은 이름을 두 네임스페이스에 만들 수 있는가              ✅
  5. 네임스페이스를 넘어 통신이 되는가                        ✅ ★★
  6. DNS 가 이름을 가르는 방식                               ✅
  7. NetworkPolicy 로 막기                                  ✅ ★★
  8. Calico 가 그것을 iptables 로 옮기는 방식                 ✅ ★★
  9. 네임스페이스를 지우면 무엇이 함께 사라지는가              ✅

[다루지 않는 것]
  ResourceQuota / LimitRange   개념만 언급
  PodSecurity admission        미실습
  RBAC                         08 문서
  NetworkPolicy 의 Egress      Ingress 만 실험했다
  calico-node 중단 실험         영향이 커서 하지 않았다
```

---

# 1. namespaced 와 cluster-scoped

```bash
kubectl get ns
kubectl api-resources --namespaced=true -o name
kubectl api-resources --namespaced=false -o name
```

```text
namespaced = true    이 종류는 네임스페이스 안에 산다
namespaced = false   클러스터 전체에 하나뿐이다 (cluster-scoped)
```

**오브젝트 하나가 아니라 "그 종류" 의 성질이다.**

```text
[Pod — namespaced]
  k8s-lab 의 web, team-b 의 web → 둘 다 있을 수 있다
  이름표가 (네임스페이스, 이름) 두 개다

[Node — cluster-scoped]
  worker01 은 클러스터에 하나뿐이다
  "k8s-lab 의 worker01" 같은 건 없다
```

## 발견 1 — -n 이 무시된다

```text
root@master01:/# kubectl get nodes -n kube-system
NAME       STATUS   ROLES           AGE   VERSION
master01   Ready    control-plane   16d   v1.35.7
worker01   Ready    <none>          16d   v1.35.7
worker02   Ready    <none>          16d   v1.35.7
```

**`-n` 을 붙여도 결과가 같다.** cluster-scoped 리소스에는 네임스페이스 개념이 없다.

## 발견 2 — etcd 저장 경로가 실체다 ★

```text
root@master01:/# ... get /registry/services/specs/ --prefix --keys-only
/registry/services/specs/default/kubernetes
/registry/services/specs/kube-system/kube-dns
                         ^^^^^^^^^^^ 네임스페이스가 경로에 들어간다

root@master01:/# ... get /registry/minions/ --prefix --keys-only
/registry/minions/master01
/registry/minions/worker01
/registry/minions/worker02
                  ^^^^^^^^ 네임스페이스 자리가 없다
```

```text
namespaced 여부는 결국 "저장 경로에 칸이 하나 더 있느냐" 다
```

> **곁가지**: 노드가 `/registry/minions/` 에 저장된다. Kubernetes 초기에 worker 노드를
> "minion" 이라고 불렀던 흔적이다. API 이름은 `nodes` 로 바뀌었지만 경로는 그대로다.

## 발견 3 — cluster-scoped 목록이 앞 문서들과 이어진다

```text
persistentvolumes            ← false      PV 는 클러스터의 자원
persistentvolumeclaims       ← true       PVC 는 네임스페이스 안의 요청
                                          → 이 쌍이 나뉜 이유가 09 문서의 핵심

ingressclasses               ← false      컨트롤러는 클러스터에 하나 깔린다
ingresses                    ← true       규칙은 팀마다 다르다
                                          → 05 에서 -n 없이 조회했던 이유

ippools / ipamblocks         ← false      "worker01 에 10.244.5.0/26" 은 노드의 사실
blockaffinities              ← false        네임스페이스와 무관하다 (1단계에서 본 것)

storageclasses               ← false      "SSD 등급" 은 클러스터 전체의 정의
clusterroles                 ← false      권한 정의 자체는 전역 (08 문서)
customresourcedefinitions    ← false      "이런 종류가 있다" 는 클러스터의 사실
```

```text
--namespaced=false 목록이 곧
"네임스페이스로 나눌 수 없는 것들의 목록" 이다
```

**팀마다 네임스페이스를 주는 구조에서 이 목록이 충돌 지점이 된다.**

## 발견 4 — Static Pod 는 다른 축이다

```text
[축 1 — namespaced 여부]  오브젝트 종류의 성질
[축 2 — 누가 만들었나]     개별 Pod 의 사정
```

```text
root@master01:/# kubectl get pods -n kube-system ... | grep master01
kube-system   etcd-master01
kube-system   kube-apiserver-master01
kube-system   kube-controller-manager-master01
kube-system   kube-scheduler-master01
              ^^^^^^^^^^^ 전부 kube-system 소속이다
```

**Pod 는 예외 없이 namespaced 다.** Static Pod 도 kube-system 에 산다.

```text
root@master01:/# kubectl get pod etcd-master01 -n kube-system -o jsonpath='{.metadata.annotations}'
{"kubernetes.io/config.hash":"4014eb7abb6fb0c28f2dbaded53072fd",
 "kubernetes.io/config.mirror":"4014eb7abb6fb0c28f2dbaded53072fd",
 "kubernetes.io/config.seen":"2026-08-03T17:12:03+09:00",
 "kubernetes.io/config.source":"file"}
```

## 발견 5 — 06 의 미확인 항목이 풀렸다 ★

```text
"kubernetes.io/config.mirror": "4014eb7abb6fb0c28f2dbaded53072fd"
```

```text
06 문서에서 본 kubelet 디렉터리 이름과 정확히 같다
  /var/lib/kubelet/pods/4014eb7abb6fb0c28f2dbaded53072fd

config.mirror 어노테이션이 kubelet 의 로컬 ID 다
apiserver 는 "이 미러의 원본 ID 는 이것" 이라고 적어둔 것이다
→ 두 ID 를 잇는 다리가 이 어노테이션이었다
```

**06 문서 미확인 10번(Static Pod 의 UID 생성 규칙)의 절반이 풀렸다.**

---

# 2. Namespace 오브젝트는 거의 비어 있다

```text
root@master01:/# kubectl get ns k8s-lab -o yaml
apiVersion: v1
kind: Namespace
metadata:
  labels:
    kubernetes.io/metadata.name: k8s-lab     ← 자동으로 붙는다
  name: k8s-lab
spec:
  finalizers:
  - kubernetes
status:
  phase: Active
```

## 발견 6 — 설정할 게 없다

```text
Deployment 는 replicas, strategy, template 을 갖는다
Service 는 selector, ports, type 을 갖는다
Namespace 는 finalizers 하나뿐이다
```

```text
네임스페이스가 하는 일은 둘뿐이다
  1. 이름을 나눈다
  2. 삭제 단위가 된다

그 외에는 아무것도 안 한다
격리도, 권한 제한도, 자원 제한도, 네트워크 차단도 안 한다
```

## 격리는 따로 붙여야 한다 ★

```text
RBAC                  누가 이 네임스페이스를 만질 수 있나       (08 문서)
ResourceQuota         이 네임스페이스가 쓸 CPU/메모리 총량
LimitRange            Pod 하나당 기본값과 상한
NetworkPolicy         어느 네임스페이스와 통신할 수 있나        ← 이 문서 5절
PodSecurity admission 어떤 권한의 Pod 를 허용할 것인가
```

```text
네임스페이스는 문이 아니라 문패다
문패가 있어야 자물쇠를 달 수 있다
```

## 발견 7 — kubernetes.io/metadata.name 라벨

```text
모든 네임스페이스에 자기 이름이 라벨로 자동으로 붙는다
→ 그래서 네임스페이스를 라벨 셀렉터로 고를 수 있다
→ 5절 NetworkPolicy 에서 이 라벨을 쓴다
```

---

# 3. 실습 — 같은 이름을 두 곳에 (2026-08-20)

```yaml
# /tmp/ns-test.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
      - name: nginx
        image: nginx:1.27
        env:
        - name: POD_NS
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        lifecycle:
          postStart:
            exec:
              command: ["sh","-c","echo $POD_NS-$(hostname) > /usr/share/nginx/html/index.html"]
---
apiVersion: v1
kind: Service
metadata:
  name: web-svc
spec:
  selector:
    app: web
  ports:
  - port: 80
```

```bash
kubectl create namespace team-b
kubectl apply -f /tmp/ns-test.yaml -n k8s-lab
kubectl apply -f /tmp/ns-test.yaml -n team-b
```

```text
fieldRef 는 downwardAPI 다
  Pod 가 자기 정보(이름, 네임스페이스, IP, 라벨)를 값으로 받는 방식
  여기서는 네임스페이스 이름을 환경 변수로 받아 응답에 찍는다
```

## 결과

```text
[k8s-lab]
pod/web-c747ddb74-5dnq5   10.244.5.59   worker01
service/web-svc           10.99.134.87

[team-b]
pod/web-c747ddb74-27nbp   10.244.5.58   worker01
service/web-svc           10.108.184.91
```

## 발견 8 — 이름이 겹쳐도 에러가 없다

**같은 파일을 두 네임스페이스에 그대로 적용했는데 둘 다 만들어졌다.**

## 발견 9 — ReplicaSet 해시가 같다

```text
web-c747ddb74-5dnq5
web-c747ddb74-27nbp
    ^^^^^^^^^ 같다
```

**02 문서의 `pod-template-hash` 다.** template 이 완전히 같으니 해시도 같다. 네임스페이스는 해시 계산에 안 들어간다.

## 발견 10 — 같은 노드, 연속된 IP ★★

```text
k8s-lab   10.244.5.59   worker01
team-b    10.244.5.58   worker01
          ^^^^^^^^^^^   ^^^^^^^^
```

```text
스케줄러는 네임스페이스를 안 본다   → 같은 노드에 배치됐다
Calico 도 네임스페이스를 안 본다    → 연속된 IP 를 받았다
```

**1단계 IPAM 구조 그대로다.** worker01 의 `10.244.5.0/26` 블록에서 순서대로 준다.

```text
ClusterIP 만 다르다
Service IP 는 클러스터 전체에서 유일해야 하기 때문이다
```

---

# 4. 네임스페이스를 넘어 통신되는가 ★★

```bash
kubectl -n k8s-lab run nettest --image=busybox:1.36 --restart=Never -- sleep 3600
```

```text
nettest   10.244.30.98   worker02      ← 다른 노드에 떴다
```

## 실험 A — 다른 네임스페이스의 Pod IP 로 직접

```text
root@master01:/# kubectl -n k8s-lab exec nettest -- wget -qO- --timeout=3 http://10.244.5.58
team-b-web-c747ddb74-27nbp
```

## 실험 B — 다른 네임스페이스의 ClusterIP 로

```text
root@master01:/# kubectl -n k8s-lab exec nettest -- wget -qO- --timeout=3 http://10.108.184.91
team-b-web-c747ddb74-27nbp
```

## 실험 C — DNS 이름으로

```text
root@master01:/# kubectl -n k8s-lab exec nettest -- wget -qO- http://web-svc
k8s-lab-web-c747ddb74-5dnq5                    ← 자기 네임스페이스

root@master01:/# ... http://web-svc.team-b
team-b-web-c747ddb74-27nbp                     ← 다른 네임스페이스

root@master01:/# ... http://web-svc.team-b.svc.cluster.local
team-b-web-c747ddb74-27nbp
```

## 발견 11 — 전부 통한다

```text
노드도 넘고(worker02 → worker01) 네임스페이스도 넘었다
```

```text
네임스페이스는 이름을 나눌 뿐
Pod 네트워크는 클러스터 전체가 하나다
```

## 발견 12 — DNS 는 search 목록으로 가른다

```text
root@master01:/# kubectl -n k8s-lab exec nettest -- cat /etc/resolv.conf
search k8s-lab.svc.cluster.local svc.cluster.local cluster.local localdomain
       ^^^^^^^ 자기 네임스페이스가 맨 앞
nameserver 10.96.0.10
options ndots:5
```

```text
"web-svc"        → k8s-lab.svc.cluster.local 을 먼저 붙인다 → 자기 것
"web-svc.team-b" → 두 번째(svc.cluster.local)를 붙여 맞는다 → 남의 것
```

```text
막는 게 아니라 편의를 준 것이다
이름만 더 쓰면 어디든 부를 수 있다
```

> `localdomain` 은 VMware DHCP 가 노드에 준 도메인이다. 노드의 `/etc/resolv.conf` 에서 물려받았다.

---

# 4-B. 그런데 나뉘는 게 하나 있다 ★★

## 발견 12-1 — 오브젝트 참조는 네임스페이스를 못 넘는다

**3절 실험에 증거가 있었다.**

```text
두 네임스페이스의 Pod 가 둘 다 app=web 라벨
두 Service 의 셀렉터도 둘 다 app=web

[셀렉터가 네임스페이스를 넘었다면]
  web-svc(k8s-lab) 이 양쪽 Pod 를 다 잡았어야 한다 → 응답이 섞였어야 한다

[실제]
  web-svc → 항상 k8s-lab-web-...    web-svc.team-b → 항상 team-b-web-...
```

**안 섞였다.** 01 문서의 "라벨만 맞으면 잡는다" 에 "같은 네임스페이스 안에서" 라는 조건이 늘 붙어 있었다.

## 발견 12-2 — yaml 에 그 칸이 없다

```yaml
volumes:
- name: html
  configMap:
    name: web-config        # 이름만 쓴다
    # namespace: team-b     # 이런 필드가 아예 없다
```

```text
Pod   volumes.configMap.name / volumes.secret.secretName
      env.configMapKeyRef.name / serviceAccountName
Service    selector
Ingress    backend.service.name
Deployment template

→ 전부 자기 네임스페이스로 고정된다
→ 권한 문제가 아니라 문법에 칸이 없다
```

## 발견 12-3 — 왜 이렇게 설계했나 ★

```text
[이유 1 — 권한 경계가 무너진다]  결정적

  # 만약 이게 가능하다면
  volumes:
  - name: stolen
    secret:
      secretName: db-password
      namespace: team-b

  team-b 를 읽을 권한이 없어도
  내 네임스페이스에 Pod 하나 만들면 그 Secret 이 파일로 나타난다
  → "Pod 를 만들 권한" 만으로 클러스터의 모든 Secret 을 읽게 된다
  → RBAC 이 통째로 무너진다

  권한으로 막으면 검사를 빠뜨릴 수 있다
  문법에 없으면 실수할 여지가 없다
```

```text
[이유 2 — 삭제 안전성]
  k8s-lab 의 Pod 가 team-b 의 ConfigMap 을 쓰고 있었다면
  → team-b 를 지우는 순간 k8s-lab 의 Pod 가 조용히 깨진다
  참조가 못 넘으면 "이 네임스페이스만 정리하면 끝" 이 보장된다
```

```text
[이유 3 — 복제 가능성]
  3절에서 같은 파일을 두 네임스페이스에 그대로 적용할 수 있었던 이유다
  참조가 밖으로 뻗어 있으면 하나하나 고쳐야 한다
```

## 발견 12-4 — Docker 격리와 강제 방식이 다르다

```text
[Docker]      커널이 막는다. mount ns 가 달라 시야에 없다. root 여도 못 본다
[Namespace]   스키마가 막는다. yaml 에 칸이 없어 표현할 수 없다
```

```bash
kubectl get cm -n team-b     # 권한만 있으면 읽힌다
```

```text
[Docker]  권한이 있어도 못 본다 (시야에 없다)
[k8s ns]  권한이 있으면 본다   (시야에는 있다)

Docker 는 "보이지 않게", Namespace 는 "가리킬 수 없게" 만들었다
```

## 발견 12-5 — 층이 둘이다 ★★

```text
[오브젝트 층 — apiserver 를 지나간다]
  Pod 생성 / ConfigMap 참조 / Service 생성
  → apiserver 가 다 본다 → 검사할 수 있다 → 막았다

[데이터 층 — apiserver 를 안 지나간다]
  Pod 가 패킷을 보낸다
  → apiserver 는 그런 일이 있었는지도 모른다 → 막을 방법이 없다
```

```text
apiserver 가 볼 수 있는 것만 apiserver 가 막는다
```

**그래서 NetworkPolicy 를 CNI 가 구현한다.**

```text
패킷을 막으려면 패킷이 지나가는 자리에 있어야 한다
그 자리는 각 노드의 커널이다. apiserver 는 거기 없다
→ apiserver 는 선언만 저장하고, Calico 가 커널에 규칙을 심는다
```

**1단계 실험 3 의 결과도 이걸로 설명된다.** apiserver 가 죽어도 트래픽이 안 끊긴 것은 데이터 층이 apiserver 와 무관하기 때문이다.

## 발견 12-6 — 그래서 CA 가 네임스페이스마다 복사돼 있다

```text
root@master01:/# kubectl get sa,cm -n k8s-lab
NAME                     AGE
serviceaccount/default   9d

NAME                         DATA   AGE
configmap/kube-root-ca.crt   1      9d
```

```text
네임스페이스를 만들면 이 둘이 자동으로 생긴다. 둘 다 namespaced 다
default SA        Pod 가 기본으로 쓰는 계정
kube-root-ca.crt  apiserver 검증용 CA
```

```text
모든 Pod 가 ca.crt 를 마운트해야 한다 (07 문서 3종 세트)
그런데 참조가 네임스페이스를 못 넘는다
→ 네임스페이스마다 사본을 둘 수밖에 없다
```

## 내용이 바이트 단위로 같다 (실측)

```text
root@master01:/# kubectl get cm kube-root-ca.crt -n k8s-lab -o jsonpath='{.data.ca\.crt}' | head -3
-----BEGIN CERTIFICATE-----
MIIDBTCCAe2gAwIBAgIIKRxCjiwH7E4wDQYJKoZIhvcNAQELBQAwFTETMBEGA1UE
AxMKa3ViZXJuZXRlczAeFw0yNjA4MDMwODA2NTNaFw0zNjA3MzEwODExNTNaMBUx

root@master01:/# sudo head -3 /etc/kubernetes/pki/ca.crt
-----BEGIN CERTIFICATE-----
MIIDBTCCAe2gAwIBAgIIKRxCjiwH7E4wDQYJKoZIhvcNAQELBQAwFTETMBEGA1UE
AxMKa3ViZXJuZXRlczAeFw0yNjA4MDMwODA2NTNaFw0zNjA3MzEwODExNTNaMBUx
```

## 복사하는 컨트롤러가 확인된다

```text
root@master01:/# kubectl get clusterrolebindings | grep -i root-ca
system:controller:root-ca-cert-publisher  ClusterRole/system:controller:root-ca-cert-publisher  17d

root@master01:/# sudo grep -i 'root-ca-file' /etc/kubernetes/manifests/kube-controller-manager.yaml
    - --root-ca-file=/etc/kubernetes/pki/ca.crt
```

**1단계 07 문서 2라운드에서 본 40여 개 `system:controller:*` 바인딩 중 하나다.**

## 전체 사슬

```text
/etc/kubernetes/pki/ca.crt                    원본 (kubeadm init 이 만들었다)
   │  --root-ca-file 로 읽는다
   ▼
kube-controller-manager
   │  root-ca-cert-publisher 컨트롤러
   ▼
각 네임스페이스의 kube-root-ca.crt ConfigMap
   │  projected 볼륨
   ▼
Pod 안의 /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
```

```text
[원본]    master01 의 /etc/kubernetes/pki/ca.crt
[사본 1]  kubelet.conf 안             kubelet 용. join 할 때 심어졌다
[사본 2]  kube-root-ca.crt ConfigMap  Pod 용. 네임스페이스마다 하나

같은 인증서를 두 경로로 나눠 배달한다. 서로 복사하는 관계가 아니다
```

## 중간 결론 (수정)

```text
[네임스페이스가 나누는 것]
  이름          같은 이름을 양쪽에 만들 수 있다
  조회 기본값   -n 없으면 자기 것만 보인다
  삭제 단위     지우면 안의 것이 함께 지워진다
  오브젝트 참조  ★ yaml 에서 이름으로 가리키는 것은 못 넘는다

[네임스페이스가 나누지 않는 것]
  Pod 네트워크   ★ 전부 통한다
  노드 배치      스케줄러가 안 본다
  IP 할당        Calico 가 안 본다
  DNS 접근       이름만 더 쓰면 된다
```

```text
"무엇으로 만들어지는가" 는 안 나눠 쓴다
"무엇을 주고받는가" 는 자유다
```

---

# 5. NetworkPolicy 로 막기 ★★

```yaml
# /tmp/np.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-from-other-ns
  namespace: team-b
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: team-b
```

```text
namespace: team-b        NetworkPolicy 도 namespaced 다
podSelector: {}          team-b 의 "모든" Pod. 빈 셀렉터는 전체를 뜻한다
policyTypes: [Ingress]   들어오는 트래픽만 제한. 나가는 건 그대로
namespaceSelector        발견 7 의 자동 라벨을 쓴다
```

## 결과

```text
root@master01:/# kubectl -n k8s-lab exec nettest -- wget -qO- --timeout=3 http://10.244.5.58
wget: download timed out
root@master01:/# kubectl -n k8s-lab exec nettest -- wget -qO- --timeout=3 http://web-svc.team-b
wget: download timed out

root@master01:/# kubectl -n team-b exec nettest2 -- wget -qO- --timeout=3 http://web-svc
team-b-web-c747ddb74-27nbp
```

```text
k8s-lab → team-b   막힘
team-b  → team-b   통함
```

## 발견 13 — 실패가 timeout 이다 (DROP)

```text
connection refused   즉시 거부 응답 → REJECT
timed out            아무 응답 없음 → DROP        ← 이것
```

```text
왜 DROP 인가
  공격자에게 "여기 뭔가 있다" 는 정보를 안 주려는 것이다
```

**운영상 알아둘 점**

```text
"왜 빨리 실패하지 않고 느리게 타임아웃 나지?"
→ NetworkPolicy 를 의심하라
```

## 발견 14 — 차단 규칙을 우리가 쓰지 않았다 ★★

```text
우리가 쓴 것: "team-b 에서 오는 것을 허용"
그런데 k8s-lab 이 막혔다
```

```text
[Pod 에 적용되는 정책이 하나도 없으면]  전부 허용
[정책이 하나라도 생기면]                명시한 것만 허용, 나머지 전부 거부
```

```text
"허용" 을 쓰는 순간 "그 외에는 거부" 가 자동으로 따라온다
정책을 건다는 것 자체가 그 Pod 를 화이트리스트 방식으로 전환시킨다
```

---

# 6. Calico 가 그것을 iptables 로 옮긴다 ★★

## 테이블이 나뉘어 있다

```text
kube-proxy   nat 테이블      "어디로 보낼까"      DNAT / SNAT   (03/05 에서 본 것)
Calico       filter 테이블   "보내도 되는가"      ACCEPT / DROP  ← 이 문서
```

**03 에서 `-t nat` 만 봐서 Calico 규칙이 거의 안 보였다.**

## 규칙 수의 변화

```text
worker01 의 filter 테이블 cali 규칙 수
   99   정책 적용 전
  131   정책 적용 후
   69   team-b 네임스페이스 삭제 후
```

## 발견 15 — 정책 체인에는 허용만 있다

```text
:cali-pi-_qA9lGysOzwIsyVf0V23 - [0:0]
-A cali-pi-_qA9lGysOzwIsyVf0V23
   -m comment --comment "KubernetesNetworkPolicy team-b/deny-from-other-ns ingress"
   -m set --match-set cali40s:a_sG3wUbuH4Nz-nbt4Bl5Dz src
   -j MARK --set-xmark 0x10000/0x10000
```

```text
pi = policy ingress
하는 일: "이 IP 집합에서 온 것이면 허용 도장을 찍어라"
DROP 은 여기 없다
```

## 발견 16 — 차단은 그 위 체인에 있다 ★★

```text
root@worker01:/# sudo iptables-save -t filter | grep 'cali-tw-calicd14548c312'

 1. conntrack RELATED,ESTABLISHED → ACCEPT       이미 맺어진 연결은 통과
 2. conntrack INVALID → DROP
 3. MARK --set-xmark 0x0/0x30000                 도장을 지운다
 4. "Start of tier default" MARK 0x0/0x20000     ─── 정책 구간 시작
 5. mark 0x0/0x20000 → cali-pi-_qA9...           정책 체인으로 보낸다
 6. "Return if policy accepted"
    mark 0x10000/0x10000 → RETURN                도장이 있으면 통과
 7. "End of tier default. Drop if no policies passed packet"
    mark 0x0/0x20000 → DROP                      ★ 우리가 안 쓴 그 차단
                                                 ─── 정책 구간 끝
 8. → cali-pri-kns.team-b                        네임스페이스 프로파일
 9. "Return if profile accepted" → RETURN
10. → cali-pri-ksa.team-b.default                ServiceAccount 프로파일
11. "Return if profile accepted" → RETURN
12. "Drop if no profiles matched" → DROP
```

```text
0.0.0.0/0 DROP 이 아니다
"허용 도장을 못 받은 것" 을 버린다
→ 허용 조건이 몇 개든 마지막 DROP 한 줄로 끝난다
```

## 발견 17 — "정책 없음 = 규칙 없음" 이 아니다 ★★

**8~12번이 핵심이다.**

```text
cali-pri-kns.team-b
        ^^^ kns = kubernetes namespace
```

```text
Calico 는 네임스페이스마다 "프로파일" 을 자동으로 만든다
그 프로파일이 기본적으로 ACCEPT 한다
→ 그래서 아무것도 안 걸어도 통했던 것이다
```

```text
"아무 규칙도 없어서 통과" 가 아니라
"기본 프로파일이 허용해서 통과" 다
```

## 발견 18 — 정책 유무로 체인 모양이 갈린다

```text
root@worker01:/# sudo iptables-save -t filter | grep -oE 'cali-tw-[a-z0-9]+' | sort -u
cali-tw-cali89c859cc852     k8s-lab  web        정책 없음
cali-tw-calicd14548c312     team-b   web        정책 있음
cali-tw-calid1990decb01     team-b   nettest2   정책 있음
```

```text
[정책 없음 — k8s-lab/web]          [정책 있음 — team-b/web]
─────────────────────────────────────────────────────────────
ESTABLISHED → ACCEPT               ESTABLISHED → ACCEPT
INVALID → DROP                     INVALID → DROP
MARK 초기화                         MARK 초기화
                                   "Start of tier default"      ★
                                   → cali-pi-<정책>              ★
                                   도장 있으면 RETURN             ★
                                   도장 없으면 DROP               ★
→ cali-pri-kns.k8s-lab             → cali-pri-kns.team-b
통과하면 RETURN                     통과하면 RETURN
→ cali-pri-ksa.k8s-lab.default     → cali-pri-ksa.team-b.default
통과하면 RETURN                     통과하면 RETURN
Drop if no profiles → DROP         Drop if no profiles → DROP
```

```text
정책이 있으면 tier 구간 4줄이 앞에 끼어든다
그 구간에서 걸러지면 프로파일의 허용은 볼 기회도 없다
```

**Pod 하나당 체인이 하나씩 생긴다.** 인터페이스가 Pod 마다 다르기 때문이다.

## 발견 19 — 라벨은 ipset 으로 번역된다

```text
-m set --match-set cali40s:a_sG3wUbuH4Nz-nbt4Bl5Dz src
```

```text
우리가 쓴 것        namespaceSelector: kubernetes.io/metadata.name=team-b
Calico 가 만든 것    그 조건에 맞는 Pod IP 들의 집합
iptables 가 보는 것  "출발지가 이 집합에 있나"
```

```text
iptables 는 라벨을 모른다. IP 만 안다
규칙으로 다 펼치면 Pod 수만큼 규칙이 늘어난다
→ 04 의 EndpointSlice 가 겪은 문제와 같다

ipset 을 쓰면 규칙은 한 줄, 집합에만 넣고 뺀다
```

> `ipset` 명령이 설치돼 있지 않아 집합 내용은 확인하지 못했다.
> `sudo apt install ipset` 으로 볼 수 있다.

## 발견 20 — 규격과 구현이 분리돼 있다

```text
Kubernetes 가 정한 것   "정책이 걸리면 화이트리스트가 된다" 는 의미
Calico 가 구현한 것     그 의미를 mark 와 DROP 으로 옮긴 것
```

```text
Calico 의 데이터플레인 선택지
  iptables   기본. 우리 클러스터가 이것
  eBPF       iptables 를 안 쓴다. 커널에 프로그램을 직접 넣는다
  nftables   iptables 의 후속 규격
```

**05 의 Ingress 와 같은 구조다.**

```text
Ingress        → nginx / traefik / envoy ...
NetworkPolicy  → Calico(iptables/eBPF) / Cilium(eBPF) / ...
CRI            → containerd / CRI-O ...
CSI            → 스토리지 드라이버들
```

## 세 번째로 나온 구조다

```text
[03]  kube-proxy  →  nat 규칙     →  커널이 라우팅
[06]  kubelet     →  파일 갱신     →  커널이 bind mount
[07]  Calico      →  filter 규칙   →  커널이 통과/차단

전부 "선언을 읽어 커널 상태로 옮기는 자" 다
그래서 그들이 죽어도 이미 옮겨둔 것은 계속 동작한다
```

---

# 6-B. 실험 — Ingress 정책이 kubelet probe 를 막는가 ★★

## 왜 걱정되는가

```text
readinessProbe 는 kubelet 이 보낸다 (04 문서에서 확인)
  kubelet (worker01, 192.168.8.142)  →  Pod (10.244.5.x:80)
  Service 도 iptables nat 도 안 거친다. Pod IP 로 직접
```

```text
그런데 우리 정책은 "team-b 네임스페이스에서 오는 것만 허용" 이다
kubelet 은 team-b 의 Pod 가 아니다. 노드 그 자체다
→ 허용 목록에 없다
```

```text
[막힌다면]
  probe 실패 → READY 0/1 → EndpointSlice 에서 제거 → 서비스가 죽는다
  "NetworkPolicy 를 걸었더니 서비스가 통째로 죽었다" 가 된다
```

**04 에서 본 사슬이 통째로 무너지는 시나리오다.**

## 실험

```yaml
# /tmp/probe-test.yaml — team-b 에 배포
containers:
- name: nginx
  image: nginx:1.27
  readinessProbe:
    httpGet:
      path: /
      port: 80
    periodSeconds: 2
    failureThreshold: 2
```

```bash
kubectl apply -f /tmp/probe-test.yaml -n team-b
# 1/1 Running 확인 후
kubectl apply -f /tmp/np.yaml
```

## 발견 20-1 — 안 막힌다

```text
08:17:23   정책 적용
           READY 1/1 유지
```

```text
root@master01:/# kubectl describe pod -n team-b -l app=web | sed -n '/^Events/,$p'
Events:
  Normal  Scheduled  2m42s
  Normal  Pulled     2m40s
  Normal  Created    2m40s
  Normal  Started    2m40s
```

**`Unhealthy` 이벤트가 하나도 없다.** probe 가 정상 동작했다.

## 발견 20-2 — 이유는 체인이 다르기 때문이다 ★★

```text
root@worker01:/# sudo iptables-save -t filter | grep 'cali-to-wl-dispatch'
:cali-to-wl-dispatch - [0:0]
-A cali-FORWARD -o cali+ -m comment --comment "cali:4Z0Pf0byo05NFe-P" -j cali-to-wl-dispatch
-A cali-to-wl-dispatch -o cali4541f175ebf -g cali-tw-cali4541f175ebf
-A cali-to-wl-dispatch -m comment --comment "Unknown interface" -j DROP
```

```text
cali-to-wl-dispatch 를 부르는 곳이 cali-FORWARD 하나뿐이다
cali-OUTPUT 에서 부르는 줄이 없다
```

**리눅스 netfilter 의 경로가 셋으로 갈린다.**

```text
패킷이 들어온다
   ▼
PREROUTING
   ▼
라우팅 판단: 목적지가 이 노드인가?
   ├─ 그렇다   → INPUT      이 노드의 프로세스에게
   └─ 아니다   → FORWARD    다른 데로 넘긴다
                     ▼
                POSTROUTING

이 노드의 프로세스가 패킷을 만든다
   ▼
OUTPUT  →  POSTROUTING
```

```text
기준은 "출발지/목적지가 이 노드인가" 다
INPUT     목적지가 나
OUTPUT    출발지가 나
FORWARD   둘 다 내가 아니다. 지나갈 뿐
```

```text
[Pod A → Pod B]  (같은 노드여도)
  Pod 는 별도 netns 다 → veth 로 노드의 네트워크 스택에 들어온다
  → 노드 입장에서는 "밖에서 들어온 패킷"
  → 목적지가 Pod IP → 내 것이 아니다 → FORWARD
  → 정책 검사를 받는다

[kubelet → Pod B]
  kubelet 은 이 노드의 프로세스다 → OUTPUT
  → cali-to-wl-dispatch 로 가는 길이 없다 → 검사에 도달하지 않는다
```

```text
Calico 가 예외를 만들어준 게 아니라
애초에 안 지나가는 길이다
```

**07 문서 앞부분의 `/32` 구조가 여기서도 작동한다.** Pod 가 별도 netns 라 노드에게는 "외부" 이고, 그래서 같은 노드 안의 Pod 끼리도 FORWARD 를 지난다. 브리지 방식이었다면 L2 로 지나가 FORWARD 도 안 탔을 것이다.

## 발견 20-3 — cali:XXXX 는 인터페이스가 아니라 주석이다

```text
-A cali-FORWARD -o cali+ -m comment --comment "cali:4Z0Pf0byo05NFe-P" -j ...
                ^^^^^^^^              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                실제 조건              Calico 가 붙인 규칙 식별자
```

```text
Calico 는 모든 규칙에 무작위 ID 를 주석으로 단다. 나중에 찾아 고치려고
cali+ 는 와일드카드다 — 모든 Pod 인터페이스가 이 분배기를 거친다
```

**`"Unknown interface" -j DROP` 은 정책이 아니라 안전장치다.**

```text
cali+ 로 왔는데 관리 목록에 없는 인터페이스면 버린다
= Calico 가 모르는 워크로드
정상 상황에서는 걸릴 일이 없다
```

## 발견 20-4 — 규격이 정한 게 아니다

```text
Kubernetes 의 NetworkPolicy 규격은
"노드가 Pod 에 보내는 트래픽" 을 어떻게 다룰지 명확히 정하지 않았다
```

```text
[우리 환경]  Calico → 안 막힌다 (실측)
[다른 CNI]   다를 수 있다. OUTPUT 에도 걸면 막힐 것이다
```

**05 의 Ingress 와 같은 구조다.** 규격은 의미만 정하고 세부는 구현에 맡긴다.

```text
"우리 클러스터에서는 이렇더라" 로 알아두고
CNI 를 바꾸면 다시 확인해야 한다
```

## 발견 20-5 — 진짜 위험한 곳은 Egress 다

```text
[우리가 건 것]  policyTypes: [Ingress]  ← 들어오는 것만
```

```text
그래서 나가는 것은 전부 열려 있었다
  DNS 조회        CoreDNS(10.96.0.10)로 나간다     자유
  apiserver 호출  10.96.0.1:443 로 나간다           자유
```

**증거가 실험에 있었다.**

```text
kubectl -n team-b exec nettest2 -- wget -qO- http://web-svc
→ "web-svc" 를 IP 로 바꾸려면 DNS 를 불러야 한다
→ 정책이 걸린 상태인데도 됐다
```

**Egress 를 걸면 DNS 부터 끊긴다.**

```yaml
# 이렇게 쓰면
policyTypes:
- Egress
egress:
- to:
  - podSelector: {}      # 같은 네임스페이스 Pod 로만
```

```text
"같은 네임스페이스 안에서만" 이라는 뜻으로 썼는데
→ CoreDNS 는 kube-system 에 있다 → DNS 조회가 막힌다
→ 이름을 IP 로 못 바꾼다
→ 같은 네임스페이스 안의 Service 도 못 부른다   ★ 의도한 것까지 안 된다
```

```yaml
# DNS 를 명시적으로 열어줘야 한다
egress:
- to:
  - namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: kube-system
    podSelector:
      matchLabels:
        k8s-app: kube-dns
  ports:
  - protocol: UDP
    port: 53
  - protocol: TCP
    port: 53
- to:
  - podSelector: {}      # 원래 하려던 것
```

> **미확인**: Egress 를 실제로 걸어 DNS 가 끊기는 것을 재현하지 않았다.

## 발견 20-6 — hostNetwork 컴포넌트는 대상이 아니다

```text
kube-proxy / calico-node / apiserver / etcd
  → hostNetwork 로 돈다. 노드의 네트워크를 그대로 쓴다
  → Pod IP 가 없다 (IP 가 192.168.8.x 다)
  → cali* 인터페이스도 cali-tw 체인도 없다
  → NetworkPolicy 대상이 아니다
```

> 03 에서 kube-proxy 를 죽여 73초간 무중단을 실측했다. 같은 논리라면 calico-node 를
> 죽여도 기존 정책은 유지될 것이다. **추론이며 측정하지 않았다.**
> calico-node 를 죽이면 새 Pod 가 IP 를 못 받아 영향이 크다.

---

# 7. 네임스페이스를 지우면

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete namespace team-b
16:36:44
namespace "team-b" deleted

root@master01:/# kubectl get ns team-b
Error from server (NotFound): namespaces "team-b" not found
root@master01:/# kubectl get all -n team-b
No resources found in team-b namespace.
```

```text
[worker01]
  131 → 69
  cali-tw 체인 3개 → 1개 (k8s-lab 의 web 만 남음)
```

## 발견 21 — 커널 규칙까지 정리된다

```text
Pod 가 사라지면 그 Pod 전용 체인도 Calico 가 치운다
선언이 사라지면 커널 상태도 따라 사라진다
```

## 발견 22 — delete 가 기다린다

```text
kubectl delete namespace 는 기본으로 끝날 때까지 기다린다 (--wait=true)
그래서 Terminating 상태를 화면에서 못 봤다
```

```text
spec.finalizers: [kubernetes] 가 이 일을 한다
안의 것들을 다 정리했는지 확인한 뒤에야 진짜로 사라진다
```

> `kubectl get ns team-b -w` 로 감시했다면 `Terminating` 을 볼 수 있었다. **미실측.**

## 발견 23 — Terminating 에서 멈추는 경우

```text
[흔한 원인]
1. 커스텀 컨트롤러가 finalizer 를 걸어두고 죽었다
2. APIService 가 죽어 있다 — "이 종류가 남았나" 를 물어볼 수 없다
   (메트릭 서버나 웹훅이 죽었을 때 흔하다)
3. CRD 오브젝트에 finalizer 가 걸려 있다
```

```bash
kubectl get ns <이름> -o jsonpath='{.status.conditions}'
```

**무엇 때문에 못 끝내는지 여기 적혀 있다.** 우리 실험에서는 그런 컨트롤러가 없어 즉시 끝났다.

## 발견 24 — 밖에서 안을 가리키던 참조는 끊어진 채 남는다 ★

```text
[예 1 — ClusterRoleBinding]
  cluster-scoped 라 안 지워진다
  그 subject 가 team-b 의 ServiceAccount 였다면
  → 바인딩은 남고 대상만 사라진다
  → "존재하지 않는 계정에 권한을 준 바인딩" 이 남는다

[예 2 — PersistentVolume]
  PV 는 cluster-scoped, PVC 는 namespaced
  네임스페이스를 지우면 PVC 만 사라진다
  → PV 는 Released 상태로 남고 데이터도 남는다 (reclaimPolicy 에 따라)
```

```text
안에서 밖을 가리키는 것    막혀 있다 (4-B절)
밖에서 안을 가리키는 것    허용된다 → 끊어진 참조가 남는다
```

**"네임스페이스를 지웠는데 디스크는 안 지워졌다" 가 예 2 다.** 09 문서에서 다시 본다.

> **미확인**: 두 상황 모두 재현하지 않았다.

---

# 정리

```text
[무엇인가]
 1. 네임스페이스는 이름표다. 그 자체로는 아무것도 막지 않는다
 2. 하는 일은 둘뿐 — 이름을 나눈다 / 삭제 단위가 된다
 3. Namespace 오브젝트는 finalizers 하나뿐이다. 설정할 게 없다

[namespaced vs cluster-scoped]
 4. 오브젝트 "종류" 의 성질이다. 개별 오브젝트의 사정이 아니다
 5. etcd 저장 경로에 네임스페이스 칸이 있느냐가 실체다
    /registry/services/specs/<ns>/<name>  vs  /registry/minions/<name>
 6. cluster-scoped 목록이 "나눌 수 없는 것들의 목록" 이다
    PV / IngressClass / StorageClass / ClusterRole / Calico CRD
 7. Pod 는 예외 없이 namespaced 다. Static Pod 도 kube-system 에 산다
    Static Pod 는 "누가 만들었나" 라는 다른 축이다

[나누지 않는 것]
 8. 같은 이름을 두 네임스페이스에 만들 수 있다 (ReplicaSet 해시까지 같다)
 9. 스케줄러도 Calico 도 네임스페이스를 안 본다
    같은 노드에 나란히 떴고 연속된 IP 를 받았다
10. Pod IP / ClusterIP / DNS 전부 네임스페이스를 넘어 통한다
11. DNS 는 search 목록 맨 앞이 자기 네임스페이스일 뿐이다
    막는 게 아니라 편의를 준 것이다

[그런데 하나는 나눈다 ★]
11-1. 오브젝트 참조는 네임스페이스를 못 넘는다
      yaml 에 네임스페이스를 쓸 칸이 아예 없다
      Service 셀렉터가 라벨이 같은데도 안 섞인 것이 증거다
11-2. 왜 막았나 — 권한 경계가 무너지기 때문이다
      남의 Secret 을 마운트할 수 있으면
      "Pod 를 만들 권한" 만으로 모든 Secret 을 읽을 수 있다
      부수적으로 삭제가 안전해지고 복제가 쉬워진다
11-3. Docker 와 강제 방식이 다르다
      Docker 는 커널이 막아서 안 보인다
      Namespace 는 스키마가 막을 뿐이라 권한만 있으면 조회는 된다
11-4. 나뉘는 것은 "설정" 과 "데이터" 다
      설정(ConfigMap/Secret/SA)은 안 나눠 쓴다. 데이터는 자유다
11-5. apiserver 가 볼 수 있는 것만 apiserver 가 막는다
      오브젝트는 apiserver 를 지나가니 막을 수 있었다
      패킷은 안 지나가니 CNI 가 커널에 규칙을 심어야 한다
      → 1단계 실험 3(apiserver 중단에도 트래픽 유지)도 이걸로 설명된다
11-6. kube-root-ca.crt 가 네임스페이스마다 있는 이유가 이것이다
      참조가 못 넘으니 root-ca-cert-publisher 가 사본을 복사해둔다

[막으려면]
12. NetworkPolicy 를 걸어야 한다
13. 정책이 없으면 전부 허용. 하나라도 걸리면 화이트리스트가 된다
    "허용" 을 쓰면 "그 외 거부" 가 자동으로 따라온다
14. 차단 방식은 0.0.0.0/0 DROP 이 아니라 "허용 도장 없으면 DROP" 이다
15. "정책 없음 = 규칙 없음" 이 아니다
    Calico 의 네임스페이스 프로파일이 허용하고 있었던 것이다
16. 정책 유무로 cali-tw 체인 모양이 갈린다 (tier 구간 4줄)
17. 라벨 셀렉터는 ipset 으로 번역된다
18. 실패가 timeout 이다 (DROP). connection refused 가 아니다

[probe 는 안 막힌다 — 6-B절]
18-1. Ingress 정책을 걸어도 kubelet 의 probe 는 안 막혔다 (실측)
      READY 1/1 유지. Unhealthy 이벤트 없음
18-2. 이유는 netfilter 경로가 다르기 때문이다
      cali-to-wl-dispatch 가 cali-FORWARD 에서만 불린다
      probe 는 OUTPUT 을 지나므로 그 체인에 도달하지 않는다
      Calico 가 예외를 만든 게 아니라 애초에 안 지나가는 길이다
18-3. 규격이 정한 게 아니다. CNI 를 바꾸면 다시 확인해야 한다
18-4. cali:XXXX 는 인터페이스가 아니라 Calico 의 규칙 식별자 주석이다
      "Unknown interface" DROP 도 정책이 아니라 안전장치다
18-5. 진짜 위험한 곳은 Egress 다
      DNS 를 명시적으로 허용하지 않으면
      같은 네임스페이스 안의 Service 조차 못 부른다 (미실측)
18-6. hostNetwork 컴포넌트는 대상이 아니다. Pod IP 가 없다

[삭제]
19. 네임스페이스를 지우면 안의 것이 전부 사라진다
    커널의 iptables 규칙까지 정리된다 (131 → 69)
    자동 생성된 default SA 와 kube-root-ca.crt 도 함께 사라진다
20. delete 는 끝날 때까지 기다린다. finalizer 가 그 일을 한다
    안의 것을 다 정리 못 하면 Terminating 에서 멈춘다
    원인은 status.conditions 에 적힌다
20-1. 밖에서 안을 가리키던 참조는 끊어진 채 남는다
      ClusterRoleBinding 의 subject / PV 의 claimRef
      "네임스페이스를 지웠는데 디스크는 안 지워졌다" 가 그것이다

[곁가지]
21. config.mirror 어노테이션이 kubelet 의 로컬 Pod ID 다 (06 미확인 해결)
22. 노드는 /registry/minions/ 에 저장된다 (초기 명칭의 흔적)
```

# 실습 리소스

```text
namespace   k8s-lab   유지
            team-b    삭제됨
web / web-svc         양쪽에 만들었다가 정리
nettest / nettest2    삭제됨
deny-from-other-ns    NetworkPolicy. 네임스페이스와 함께 삭제됨
/tmp/ns-test.yaml, /tmp/np.yaml   삭제됨
```

```bash
kubectl -n k8s-lab delete pod nettest
kubectl delete -f /tmp/ns-test.yaml -n k8s-lab
rm -f /tmp/ns-test.yaml /tmp/np.yaml
kubectl get all -n k8s-lab
kubectl get ns
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              이름을 나누는 단위. 삭제 단위
                                격리 기능은 없다 ★
2. 생성 시 동작하는 Controller   Namespace Controller (삭제 시 정리 담당)
                                Calico 가 네임스페이스마다 프로파일을 만든다
3. 주요 Spec 과 Status 필드     spec.finalizers / status.phase
                                metadata.labels 에 자동 라벨
4. 다른 오브젝트와의 연결        namespaced 리소스 전부의 소속
                                NetworkPolicy / ResourceQuota / RBAC 의 적용 범위
5. 장애 사례                    4절 네임스페이스를 넘어 통신됨 (오해의 원인)
                                5절 정책 하나로 전부 막힘 (의도치 않은 차단)
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            네임스페이스만으로는 격리가 안 된다 /
                                NetworkPolicy 는 걸리는 순간 화이트리스트가 된다 /
                                cluster-scoped 리소스는 팀 간 충돌 지점이다 /
                                timeout 실패는 NetworkPolicy 를 의심하라
```

# 미확인 목록

```text
1. Terminating 상태를 실제로 관측하지 않았다 (-w 없이 지웠다)
2. finalizer 가 걸려 삭제가 멈추는 상황 (Terminating 무한 대기) 미재현
3. ResourceQuota / LimitRange 미실습
4. PodSecurity admission 미실습
5. NetworkPolicy 의 Egress 방향 미실험
   특히 "Egress 를 걸면 DNS 가 끊긴다" 를 재현하지 않았다
5-1. probe 실험은 Calico 기준이다. 다른 CNI 에서는 확인하지 않았다
5-2. cali-OUTPUT 체인의 내용을 직접 열어보지 않았다
     (cali-to-wl-dispatch 를 안 부른다는 것만 grep 으로 확인)
6. podSelector 로 Pod 단위로 좁히는 정책 미실험
7. ipset 내용을 직접 보지 못했다 (ipset 명령 미설치)
8. calico-node 를 중단했을 때 기존 정책이 유지되는지 미측정
9. iptables 규칙 수 변화(99 → 131 → 69)의 정확한 내역을 분해하지 않았다
10. Calico 프로파일(cali-pri-kns.*, cali-pri-ksa.*)의 내용을 열어보지 않았다
11. ServiceAccount 프로파일(ksa)이 언제 의미를 갖는지 미확인
12. kube-public / kube-node-lease 의 용도를 확인하지 않았다
13. 다른 네임스페이스의 오브젝트를 실제로 마운트 시도해보지 않았다
    (yaml 에 칸이 없다는 것만 확인. 스키마 검증 오류를 직접 보지 않았다)
14. 끊어진 참조(ClusterRoleBinding / PV) 상황을 재현하지 않았다
15. Terminating 에서 멈추는 상황을 재현하지 않았다
```

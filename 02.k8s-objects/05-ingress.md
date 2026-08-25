# 05. Ingress (와 NodePort)

2단계 여섯 번째. **여기까지 만든 것은 전부 클러스터 안에서만 통했다. 밖에서 들어오는 길을 본다.**

```text
Pod → ReplicaSet → Deployment → Service → EndpointSlice
  전부 "클러스터 안에서 클러스터 안으로" 가는 길이었다
```

## 이 문서의 범위

```text
[확인한 것]
  1. ClusterIP 는 정말 밖에서 못 닿는가                    ✅
  2. Service type 네 가지                                 ✅
  3. NodePort 로 밖에서 들어와보기                         ✅
  4. NodePort 도 프로세스가 아니라 규칙이다                 ✅ ★
  5. 입구가 둘, 몸통은 하나                                ✅ ★
  6. 클라이언트 IP 가 사라지는 것                           ✅
  7. externalTrafficPolicy: Local 의 효과와 대가            ✅
  8. NodePort 의 한계 — L4 는 HTTP 를 모른다                ✅
  9. Ingress 를 만들면 무슨 일이 일어나는가                  ✅ ★★
 10. 컨트롤러가 없으면 왜 아무 일도 안 일어나는가            ✅

[다루지 않는 것]
  Ingress Controller 설치      4단계. 여기서는 "없으면 어떻게 되는가" 까지만
  LoadBalancer 실측            온프렘이라 Pending 에 머문다. 개념만
  TLS / cert-manager           4단계 이후
  Gateway API                  Ingress 의 후속 규격. 다루지 않음
```

---

# 1. 이것이 푸는 문제

```text
ClusterIP 10.109.255.35
  → 어느 인터페이스에도 없는 주소다 (03 에서 확인)
  → 노드의 iptables 규칙을 지나야만 의미가 있다
  → 밖에서는 그 규칙에 도달할 방법이 없다
```

## 실측 (2026-08-20)

```text
[master01 — 안에서]
root@master01:/# curl -s http://10.109.255.35
web-f956df596-w7hs5

[Windows — 밖에서]
PS> curl.exe -s --max-time 5 http://10.109.255.35
(아무 응답 없음)

PS> curl.exe -s --max-time 5 http://192.168.8.143:6443
Client sent an HTTP request to an HTTPS server.

PS> ping 192.168.8.143
패킷: 보냄 = 4, 받음 = 4, 손실 = 0
```

## 발견 1 — 네트워크가 아니라 주소의 문제다

```text
ping 192.168.8.143   된다      노드까지 네트워크가 닿는다
6443 도 응답한다               포트도 열려 있다
curl ClusterIP       안 된다    "막힌 게 아니라 없는 것" 이다
```

**DNS 도 소용없다.** 이름이 `10.109.255.35` 로 풀려도 그 주소로 가는 경로가 없다.

```text
밖에서 확실히 닿는 것은 노드 IP 하나뿐이다
→ "노드의 어떤 포트" 로 들어와서 그 안에서 규칙을 타야 한다
```

---

# 2. Service type 네 가지

```text
ClusterIP      (기본값) 클러스터 안에서만
NodePort       모든 노드의 특정 포트를 연다
LoadBalancer   클라우드 로드밸런서를 붙인다 (온프렘에선 Pending)
ExternalName   DNS CNAME 만 만든다. 프록시 안 함
```

---

# 3. NodePort

```bash
kubectl patch svc web-svc -p '{"spec":{"type":"NodePort"}}'
```

```text
NAME      TYPE       CLUSTER-IP      EXTERNAL-IP   PORT(S)        AGE
web-svc   NodePort   10.109.255.35   <none>        80:31941/TCP   5m28s
                     ^^^^^^^^^^^^^ 그대로 있다      ^^^^^^^^^^ 새로 생겼다
```

## 발견 2 — ClusterIP 가 없어지지 않는다

```text
NodePort 타입이 되어도 ClusterIP 는 유지된다
  클러스터 안의 Pod   → ClusterIP 로 부른다
  클러스터 밖         → NodePort 로 부른다
둘 다 필요하니 둘 다 둔다
```

## 발견 3 — 포트 범위가 30000~32767 인 이유

```text
1~1023      특권 포트. root 권한이 필요하다
1024~29999  일반 앱이 쓰는 범위. 충돌 위험
30000~32767 Kubernetes 가 자기 몫으로 떼어둔 구간
```

## 실측 — 세 노드 전부 응답한다

```text
PS> curl.exe -s http://192.168.8.143:31941   web-f956df596-bdwz9
PS> curl.exe -s http://192.168.8.142:31941   web-f956df596-w7hs5
PS> curl.exe -s http://192.168.8.141:31941   web-f956df596-bdwz9

PS> 1..10 | ForEach-Object { curl.exe -s http://192.168.8.143:31941 }
  w7hs5 / hq7wz / hq7wz / w7hs5 / hq7wz / w7hs5 / hq7wz / hq7wz / bdwz9 / bdwz9
```

```text
Pod 가 없는 노드(master01)로 보내도 됐다
→ 그 노드의 iptables 가 다른 노드의 Pod 로 넘겨준다
부하 분산도 그대로 동작한다
```

---

# 4. 규칙을 열어본다

```text
root@master01:/# sudo iptables -t nat -L KUBE-NODEPORTS -n
Chain KUBE-NODEPORTS (1 references)
KUBE-EXT-UNTI3ZWT6KQG4YW5  6 -- 0.0.0.0/0  127.0.0.0/8  tcp dpt:31941
                                                          nfacct-name localhost_nps_accepted_pkts
KUBE-EXT-UNTI3ZWT6KQG4YW5  6 -- 0.0.0.0/0  0.0.0.0/0    tcp dpt:31941

root@master01:/# sudo iptables -t nat -L KUBE-EXT-UNTI3ZWT6KQG4YW5 -n
Chain KUBE-EXT-UNTI3ZWT6KQG4YW5 (2 references)
KUBE-MARK-MASQ             0 -- 0.0.0.0/0  0.0.0.0/0
                             /* masquerade traffic for k8s-lab/web-svc external destinations */
KUBE-SVC-UNTI3ZWT6KQG4YW5  0 -- 0.0.0.0/0  0.0.0.0/0
```

## 발견 4 — NodePort 도 프로세스가 아니다 ★

```text
root@master01:/# sudo ss -tlnp | grep 31941
(출력 없음)
```

**31941 을 듣고 있는 프로세스가 하나도 없다.**

```text
"포트가 열려 있다" 는 말이 두 가지다
  프로세스가 듣고 있다           ← 보통 그렇게 생각한다. 여기선 아니다
  그 포트로 온 걸 커널이 가로챈다  ← 실제로 이것
```

**03 의 결론이 여기서도 그대로다.**

```text
kube-proxy   규칙을 쓴다      (설정하는 자)
커널         패킷을 처리한다   (전달하는 자)
```

> **곁가지**: `kube-proxy` 라는 이름은 옛 설계의 흔적이다. 초기에는 userspace 모드가 있어
> kube-proxy 가 실제로 포트를 듣고 프록시했다. 지금은 iptables 모드가 기본이라 이름만 남았다.
> **userspace 모드가 언제 제거됐는지는 확인하지 않았다.**

## 발견 5 — 입구가 둘, 몸통은 하나 ★★

```text
KUBE-SERVICES       하나뿐이다. 클러스터 전체에 하나
                    모든 Service 의 ClusterIP 가 한 줄씩 들어있는 "목록"
                    하는 일: "이 목적지 주소가 어느 Service 냐"

KUBE-SVC-xxxxxxxx   Service 마다 하나씩
                    그 Service 의 부하 분산 규칙 (확률 + SEP 점프)

KUBE-NODEPORTS      NodePort 목록. "이 포트 번호가 어느 Service 냐"
KUBE-EXT-xxxxxxxx   밖에서 온 트래픽의 전처리 (SNAT 표시, 정책 판단)
```

```text
   [ClusterIP 로 들어옴]          [NodePort 로 들어옴]
   10.109.255.35:80               <노드IP>:31941
          │                              │
          ▼                              ▼
   KUBE-SERVICES                   KUBE-NODEPORTS
   "주소로 고른다"                   "포트로 고른다"
          │                              ▼
          │                         KUBE-EXT-...
          │                         "무조건 SNAT 표시"
          └──────────┬───────────────────┘
                     ▼
          KUBE-SVC-UNTI3ZWT6KQG4YW5      ← 여기서 합류
                     │
                 KUBE-SEP-...
                     ▼
                   DNAT
```

> **정정.** 처음에는 "NodePort 가 ClusterIP 위에 쌓인다" 고 설명했으나 부정확하다.
> **NodePort 로 들어온 패킷은 ClusterIP 주소를 한 번도 만나지 않는다.**
> API 상으로는 포함 관계로 보이지만 패킷 경로로는 나란한 두 입구다.

## 발견 6 — 여기 MASQ 에는 조건이 없다

```text
[ClusterIP 경로]
  KUBE-MARK-MASQ  !10.244.0.0/16 ...    조건이 있다

[NodePort 경로]
  KUBE-MARK-MASQ  0.0.0.0/0  0.0.0.0/0  조건이 없다. 무조건
```

```text
NodePort 로 들어온 것은 밖에서 온 것이 확실하다
→ 따질 것 없이 무조건 출발지를 바꾼다
```

## 발견 7 — 127.0.0.0/8 규칙

```text
KUBE-EXT-...  tcp dpt:31941  destination 127.0.0.0/8
              nfacct-name localhost_nps_accepted_pkts
```

```text
root@master01:/# curl -s http://127.0.0.1:31941
web-f956df596-hq7wz
root@master01:/# curl -s http://localhost:31941
web-f956df596-w7hs5
```

**노드 자신이 localhost 로 NodePort 를 부르는 경우를 따로 잡아준다.**

---

# 5. 클라이언트 IP 가 사라진다

Windows(`curl/8.21.0`)에서 접속한 뒤 nginx 접속 로그를 봤다.

```text
10.244.241.64 - - "GET / HTTP/1.1" 200 "curl/8.21.0"
10.244.5.0    - - "GET / HTTP/1.1" 200 "curl/8.21.0"
10.244.30.64  - - "GET / HTTP/1.1" 200 "curl/8.21.0"
```

## 발견 8 — 노드의 tunl0 주소로 바뀐다

```text
10.244.241.64   master01 의 tunl0     ← 192.168.8.143 으로 접속했을 때
10.244.5.0      worker01 의 tunl0     ← 192.168.8.142 로 접속했을 때
10.244.30.64    worker02 의 tunl0     ← 192.168.8.141 로 접속했을 때
```

**03 에서 확인한 MASQUERADE 동작 그대로다.** 나가는 인터페이스(tunl0)의 주소를 고른다.

```text
Windows PC 의 IP 는 로그에 한 번도 안 나온다
```

```text
[잃는 것]
  접근 로그에 진짜 클라이언트가 안 남는다
  IP 기반 접근 제한을 걸 수 없다
  지역별 통계 / 어뷰징 차단이 안 된다
```

---

# 6. externalTrafficPolicy: Local

```bash
kubectl patch svc web-svc -p '{"spec":{"externalTrafficPolicy":"Local"}}'
```

## 발견 9 — 클라이언트 IP 가 살아난다 ★

```text
192.168.8.1 - - [20/Aug/2026:02:51:58] "GET / HTTP/1.1" 200 "curl/8.21.0"
^^^^^^^^^^^ 진짜 클라이언트
```

## 발견 10 — master01 만 응답하지 않는다

```text
PS> curl.exe -s http://192.168.8.142:31941   web-f956df596-hq7wz    ✓
PS> curl.exe -s http://192.168.8.141:31941   web-f956df596-w7hs5    ✓
PS> curl.exe -s http://192.168.8.143:31941   (응답 없음)             ✗
```

```text
root@master01:/# kubectl get pods -o wide
web-f956df596-bdwz9   10.244.5.55    worker01
web-f956df596-hq7wz   10.244.5.56    worker01
web-f956df596-w7hs5   10.244.30.96   worker02

root@master01:/# kubectl describe node master01 | grep -i taint
Taints:  node-role.kubernetes.io/control-plane:NoSchedule
```

**master01 에는 `web` Pod 가 없다.** taint 때문에 일반 Pod 가 안 뜬다(1단계에서 확인).

## 발견 11 — DROP 규칙이 있는 게 아니다 ★

```text
root@master01:/# sudo iptables -t nat -L KUBE-EXT-UNTI3ZWT6KQG4YW5 -n
KUBE-SVC-...     src 10.244.0.0/16       /* pod traffic ... */
KUBE-MARK-MASQ   ADDRTYPE match src-type LOCAL   /* masquerade LOCAL traffic */
KUBE-SVC-...     ADDRTYPE match src-type LOCAL   /* route LOCAL traffic */
```

```text
1번   출발지가 Pod 대역이면 통과       클러스터 안 Pod 가 부른 경우
2·3번 출발지가 이 노드 자신이면 통과    노드에서 curl 한 경우

Windows(192.168.8.1)는 어디에도 안 걸린다
→ DNAT 이 안 된다 → 목적지가 그대로 192.168.8.143:31941
→ 그런데 31941 을 듣는 프로세스가 없다 (발견 4)
→ 갈 곳이 없다
```

**"버린다" 가 아니라 "아무 규칙에도 안 걸려서 아무 데도 못 간다".** 명시적 DROP 을 만든 게 아니라 통과 조건을 좁힌 것이다.

## 발견 12 — KUBE-SVL 은 로컬 Pod 만 담는다

```text
root@worker01:/# sudo iptables -t nat -L KUBE-SVL-UNTI3ZWT6KQG4YW5 -n
Chain KUBE-SVL-UNTI3ZWT6KQG4YW5 (1 references)
KUBE-SEP-NQSJSMDZGWSF7O7T  -> 10.244.5.55:80  probability 0.5
KUBE-SEP-BVEJZVZTDBDSRHZ3  -> 10.244.5.56:80
```

```text
SVL = Service Local
worker01 의 Pod 2개만. worker02 의 10.244.30.96 은 없다
```

## 왜 Local 이 SNAT 을 안 해도 되나

```text
다른 노드로 넘기면 SNAT 이 반드시 필요하다
  worker02 의 Pod 로 넘겼는데 출발지가 192.168.8.1 그대로면
  → 그 Pod 가 192.168.8.1 로 직접 답한다
  → 응답이 master01 을 안 거친다 → conntrack 이 없다 → 연결이 깨진다

Local 은 넘기지 않는다 → SNAT 이 필요 없다 → 클라이언트 IP 가 산다
```

```text
부하 때문에 포기한 게 아니라 "출발지 주소를 지키려고" 전달을 포기한 것이다
부하 불균형은 그 결과이지 목적이 아니다
```

## 트레이드오프

```text
[Cluster]
  ✓ 어느 노드로 보내도 된다 / 부하가 고르게 나뉜다
  ✗ 클라이언트 IP 가 사라진다

[Local]
  ✓ 클라이언트 IP 가 보인다
  ✗ Pod 없는 노드는 응답 안 한다
  ✗ 부하가 노드별로 갇힌다
     192.168.8.142 → Pod 2개가 반씩
     192.168.8.141 → Pod 1개가 전부
```

```text
실무에서는 앞에 로드밸런서가 있어 헬스체크로 응답 안 하는 노드를 뺀다
우리는 LB 가 없어 직접 노드 IP 를 쳤으므로 죽은 문을 두드린 셈이다
```

---

# 7. NodePort 의 한계

```text
1. 포트가 30000번대다
   사용자에게 http://192.168.8.142:31941 을 알려줄 수 없다

2. Service 마다 포트가 하나씩 필요하다
   서비스 10개면 포트 10개

3. 어느 노드로 갈지를 클라이언트가 정해야 한다
   그 노드가 죽으면 그 주소도 죽는다

4. Cluster / Local 둘 다 대가가 있다

5. HTTP 를 모른다 ★
```

## 5번이 결정적이다

```text
iptables 는 IP 와 포트만 본다
HTTP 헤더 안의 Host: 나 경로는 못 본다 — L4(전송 계층)의 일이라 그렇다

"api.example.com 은 A 서비스로" 를 하려면
누군가 HTTP 를 읽어야 한다 → L7 이 필요하다
```

```text
Service   L4. IP + 포트로 나눈다.       커널의 iptables 규칙
Ingress   L7. 도메인 + 경로로 나눈다.   HTTP 를 읽는 프로그램이 필요하다
```

**"프로그램이 필요하다" 가 이 문서의 핵심이다.**

---

# 8. Ingress 를 만든다 — 아무 일도 안 일어난다 ★★

## 먼저 컨트롤러가 없는 것을 확인

```text
root@master01:/# kubectl api-resources | grep -i ingress
ingressclasses         networking.k8s.io/v1   false   IngressClass
ingresses         ing  networking.k8s.io/v1   true    Ingress

root@master01:/# kubectl get ingressclass
root@master01:/# kubectl get pods -A | grep -iE 'ingress|traefik'
No resources found
```

```text
오브젝트 종류는 apiserver 가 안다        ← api-resources 에 있다
그걸 처리할 프로그램이 없다              ← 컨트롤러가 없다
```

## 만든다

```yaml
# /tmp/web-ing.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web-ing
spec:
  rules:
  - host: web.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: web-svc
            port:
              number: 80
```

```text
rules[]
  host: web.local           "이 도메인으로 온 요청이면"
  http.paths[]
    path: /                 "이 경로면"
    pathType: Prefix        Prefix / Exact / ImplementationSpecific
    backend.service         "이 Service 로 보내라"
```

```text
Ingress 는 Service 를 가리킨다. Pod 를 직접 안 가리킨다
→ Service → EndpointSlice → Pod 로 이어지는 길을 그대로 쓴다
```

**`ImplementationSpecific` 이라는 pathType 이 있다는 것 자체가 힌트다.** 표준이 동작을 다 정하지 않고 컨트롤러에 맡긴다는 뜻이다.

## 결과

```text
root@master01:/# kubectl get ingress -o wide
NAME      CLASS    HOSTS       ADDRESS   PORTS   AGE
web-ing   <none>   web.local             80      12s
                                ^^^^^^^ 비어 있다

root@master01:/# kubectl describe ingress web-ing
Address:
Ingress Class:    <none>
Default backend:  <default>
Rules:
  Host        Path  Backends
  web.local
              /   web-svc:80 (10.244.30.96:80,10.244.5.55:80,10.244.5.56:80)
Annotations:  <none>
Events:       <none>
```

```text
Pod 가 하나도 안 늘었다 (kube-system 15개 그대로)
events 에 Ingress 관련이 없다 (32분 전 Pod 이벤트가 마지막)
```

## 발견 13 — describe 의 Endpoint 목록은 함정이다 ★

```text
web-svc:80 (10.244.30.96:80,10.244.5.55:80,10.244.5.56:80)
```

**Pod IP 3개가 나와서 "뭔가 연결됐다" 고 오해하기 쉽다.** 그런데 yaml 에는 없다.

```text
root@master01:/# kubectl get ingress web-ing -o yaml | tail -15
spec:
  rules:
  - host: web.local
    http:
      paths:
      - backend:
          service:
            name: web-svc
            port:
              number: 80
        path: /
        pathType: Prefix
status:
  loadBalancer: {}        ← 비어 있다
```

```text
kubectl 이 화면에 보여주려고 그 자리에서 EndpointSlice 를 조회한 것이다
어딘가에 저장된 상태가 아니다
```

**04 에서 배운 것과 같다. "화면에 보인다 ≠ 실제가 그렇다".**

## 발견 14 — 밖에서도 아무 변화가 없다

```text
PS> curl.exe -H "Host: web.local" http://192.168.8.142:31941
web-f956df596-hq7wz          ← 된다. 그런데 NodePort 덕분이다

PS> curl.exe http://192.168.8.142
(응답 없음)                   ← 80 을 여는 게 없다
```

```text
Host 헤더를 붙였지만 아무도 안 읽는다
Ingress 를 만들었는데 80 포트가 안 열린다
```

## 발견 15 — 사슬이 끊긴 것이다 ★★

```text
[Deployment 를 만들었을 때]
  apiserver 저장 → Deployment Controller → ReplicaSet
                → ReplicaSet Controller → Pod
                → Scheduler → 노드 배정 → kubelet → 컨테이너

[Service 를 만들었을 때]
  apiserver 저장 → EndpointSlice Controller → EndpointSlice
                → kube-proxy → iptables 규칙

[Ingress 를 만들었을 때]
  apiserver 저장 → 끝
```

```text
apiserver 는 저장만 한다
그 뒤에 아무도 없으면 정말로 아무 일도 안 일어난다
```

**01 문서에서 라벨을 바꿔 Pod 를 ReplicaSet 에서 빼돌렸을 때와 같은 구조다.** 선언과 실행이 분리돼 있어, 실행하는 쪽이 없으면 선언은 그냥 텍스트다.

## 왜 컨트롤러를 기본 제공하지 않나

```text
Deployment / ReplicaSet / EndpointSlice Controller
  → kube-controller-manager 안에 있다 (kubeadm 이 Static Pod 로 띄웠다)
kube-proxy
  → kubeadm 이 DaemonSet 으로 깔았다

Ingress Controller
  → 아무도 안 깔아준다
```

```text
"HTTP 를 어떻게 처리할 것인가" 는 선택지가 많다
  nginx / traefik / haproxy / envoy / 클라우드 LB ...
Kubernetes 는 규격만 정하고 구현은 안 정했다
→ Ingress 는 "인터페이스" 에 가깝다
```

---

# 9. 컨트롤러가 있으면 무엇이 달라지나

```text
1. Ingress Controller 를 설치한다   → Deployment/DaemonSet. Pod 가 뜬다
2. 그 Pod 가 apiserver 를 watch 한다
3. Ingress 를 읽어 자기 설정으로 바꾼다
     server { server_name web.local; location / { proxy_pass ...; } }
4. 설정을 다시 읽는다 (reload)
5. 그 Pod 가 80/443 을 듣는다
6. status.loadBalancer 에 주소를 써넣는다 → ADDRESS 열이 채워진다
```

## 발견 16 — Ingress 가 Pod 를 만드는 게 아니다 ★

```text
[Deployment]  오브젝트를 만들면 → 컨트롤러가 그걸 보고 Pod 를 만든다
[Ingress]     컨트롤러가 이미 Pod 로 떠 있고 → Ingress 를 읽어 설정을 고친다
              Pod 는 새로 안 생긴다
```

```text
Ingress 는 "무엇을 만들어라" 가 아니라
"이미 있는 문지기에게 주는 지시서" 다
→ 문지기가 없으면 지시서만 남는다
```

## Ingress 는 NodePort 를 없애지 않는다

```text
Controller Pod 도 밖에서 접근할 수 있어야 한다
→ 결국 NodePort / LoadBalancer / hostNetwork 로 노출한다
```

```text
[NodePort 만]
  web-svc  → 31941
  api-svc  → 32105      서비스마다 포트 하나씩
  shop-svc → 30887

[Ingress]
  Ingress Controller → 80 하나
       ├─ web.local  → web-svc
       ├─ api.local  → api-svc
       └─ /shop      → shop-svc
```

```text
Ingress 는 NodePort 를 없애는 게 아니라
NodePort 하나 뒤에 여러 서비스를 숨기는 것이다
```

## IngressClass

```text
컨트롤러를 여러 개 깔 수 있다 (외부용 nginx, 내부용 traefik)
→ 이 Ingress 는 누가 처리할 것인가?
→ spec.ingressClassName 으로 지정한다
```

**04 의 `managed-by` 라벨과 같은 발상이다.**

```text
[EndpointSlice]  managed-by         누가 만들었는지 표시
[Ingress]        ingressClassName   누가 처리할지 지정
```

```text
지금은 <none> 이다
컨트롤러를 깔아도 기본 클래스로 지정하지 않으면 여전히 아무도 안 가져간다
```

---

# 정리

```text
[문제]
 1. ClusterIP 는 밖에서 못 닿는다 — 네트워크가 아니라 주소의 문제다
    ping 은 되는데 curl 은 안 된다. DNS 로도 해결 안 된다

[NodePort]
 2. type 은 입구를 정한다. ClusterIP / NodePort / LoadBalancer / ExternalName
 3. NodePort 가 되어도 ClusterIP 는 유지된다 (안에서도 불러야 하니까)
 4. NodePort 도 프로세스가 아니라 iptables 규칙이다 (ss 로 확인)
 5. 입구가 둘, 몸통은 하나다
    KUBE-SERVICES(주소로) / KUBE-NODEPORTS(포트로) → 같은 KUBE-SVC 로 합류
    NodePort 로 들어온 패킷은 ClusterIP 주소를 한 번도 안 만난다
 6. KUBE-EXT 의 MASQ 는 조건이 없다 (ClusterIP 경로는 !podCIDR 조건부)

[클라이언트 IP]
 7. Cluster 정책이면 클라이언트 IP 가 노드의 tunl0 주소로 바뀐다
 8. Local 정책이면 살아난다 (192.168.8.1 확인)
 9. 대신 Pod 없는 노드는 응답하지 않는다
    DROP 규칙이 있는 게 아니라 통과 조건에 안 걸려서 갈 곳이 없는 것이다
10. 부하가 노드별로 갇힌다 (KUBE-SVL 에 로컬 Pod 만 들어간다)
11. 전달을 포기한 이유는 부하가 아니라 출발지 주소를 지키기 위해서다

[Ingress]
12. iptables 는 L4 다. HTTP 의 Host 나 경로를 못 본다
    도메인/경로로 나누려면 HTTP 를 읽는 프로그램이 필요하다
13. Ingress 를 만들어도 아무 일도 안 일어난다
    ADDRESS 비어 있음 / IngressClass <none> / Events <none> / Pod 안 늘어남
    status.loadBalancer: {} 
14. describe 에 나오는 Endpoint 목록은 kubectl 의 화면용 조회다
    yaml 어디에도 저장돼 있지 않다
15. apiserver 는 저장만 한다. 읽는 자가 없으면 선언은 텍스트다
16. Ingress 는 Pod 를 만드는 게 아니라 이미 있는 문지기에게 주는 지시서다
17. Ingress 는 NodePort 를 없애지 않는다. 그 하나 뒤에 여러 서비스를 숨긴다
18. IngressClass 는 EndpointSlice 의 managed-by 와 같은 발상이다
```

# 실습 리소스

```text
namespace   k8s-lab   유지
web         Deployment (3 replicas, postStart 로 hostname 기록)
web-svc     Service    ClusterIP → NodePort(31941) → externalTrafficPolicy Local
web-ing     Ingress    아무 일도 안 일어남
/tmp/web.yaml, /tmp/web-ing.yaml
```

**정리 명령**

```bash
kubectl delete -f /tmp/web-ing.yaml
kubectl delete -f /tmp/web.yaml
rm -f /tmp/web.yaml /tmp/web-ing.yaml
kubectl get all
kubectl get ingress
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              L7 라우팅 규칙 선언. 도메인/경로 → Service
2. 생성 시 동작하는 Controller   없다. 별도로 설치해야 한다 ★
                                이 문서의 핵심 발견
3. 주요 Spec 과 Status 필드     spec: ingressClassName / rules[] /
                                       host / http.paths[] / pathType /
                                       backend.service / tls[]
                                status: loadBalancer (컨트롤러가 채운다)
4. 다른 오브젝트와의 연결        Service(backend), IngressClass, Secret(TLS)
5. 장애 사례                    8절 전체 — 만들었는데 아무 일도 안 일어남
                                6절 Local 정책으로 master01 이 응답 안 함
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            컨트롤러 없이는 무용지물 /
                                ingressClassName 을 지정하지 않으면 무시된다 /
                                describe 의 Endpoint 목록을 신뢰하지 말 것 /
                                NodePort 의 externalTrafficPolicy 트레이드오프
```

# 미확인 목록

```text
1. LoadBalancer 타입을 실제로 만들어보지 않았다 (Pending 확인 미실시)
2. ExternalName 타입 미확인
3. kube-proxy userspace 모드의 제거 시점
4. nfacct-name(localhost_nps_accepted_pkts)의 정확한 용도
5. worker02 의 KUBE-SVL 은 확인하지 않았다 (worker01 만 봤다)
6. Local 정책에서 부하 불균형을 수치로 재지 않았다
7. NodePort 를 직접 지정(spec.ports[].nodePort)했을 때의 동작
8. Ingress 의 tls 필드 / defaultBackend 미실습
9. Windows PC 의 실제 IP 를 확인하지 않았다
   로그의 192.168.8.1 이 PC 인지 게이트웨이인지 미확인
10. 컨트롤러 설치 후 이 Ingress 가 어떻게 살아나는지 → 4단계
```

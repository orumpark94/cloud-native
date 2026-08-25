# 03. Service

2단계 네 번째 오브젝트. **1단계에서 결론만 내고 근거를 보지 못한 것 하나를 여기서 직접 확인한다.**

```text
[1단계 장애 실험의 결론]
  "kube-proxy 가 이미 iptables 규칙을 깔아놨고, 그 규칙은 커널에 있다"
  → 그래서 제어 평면이 넷 다 죽어도 트래픽이 안 끊겼다

[문제]
  그 규칙을 한 번도 열어보지 않았다. 남의 말을 옮긴 셈이다
```

## 이 문서의 범위

```text
[확인한 것]
  1. 이것이 푸는 문제 — Pod IP 는 바뀐다                        ✅
  2. Service / EndpointSlice / iptables 세 층의 분업             ✅
  3. ClusterIP 는 실재하지 않는 주소다                           ✅
  4. KUBE-SERVICES → KUBE-SVC → KUBE-SEP 체인 전체 추적          ✅ ★
  5. 부하 분산은 확률 세 줄이다                                  ✅
  6. MASQ — 표시하는 곳과 실행하는 곳이 다르다                   ✅
  7. DNS 로 부르기                                              ✅
  8. Pod 가 죽으면 규칙이 몇 초 만에 바뀌나                       ✅
  9. preStop 으로 그 사이의 실패를 막을 수 있나                   ✅
 10. kube-proxy 를 죽여도 트래픽이 유지되는가                     ✅ ★★

[다루지 않는 것]
  NodePort / LoadBalancer     외부 노출. 4단계에서
  headless Service            StatefulSet 과 함께 (11 문서)
  IPVS / nftables 모드         이 클러스터는 iptables 모드
  sessionAffinity             확인 안 함
```

---

# 1. 이것이 푸는 문제

02 문서 실습 내내 Pod IP 가 계속 바뀌었다.

```text
[처음 생성]   10.244.5.37   10.244.5.38   10.244.30.89
[롤아웃 후]   10.244.5.39   10.244.30.90  10.244.5.40
[롤백 후]     10.244.5.41   10.244.5.42   10.244.30.91
```

**같은 서비스인데 주소가 세 번 전부 바뀌었다.**

```text
Pod 가 죽으면 새 Pod 는 다른 IP 를 받는다
롤링 업데이트를 하면 전부 교체된다
노드가 죽어 다른 노드로 옮겨가도 바뀐다
```

문제가 하나 더 있다.

```text
Pod 가 3개인데 어디로 보내야 하나
  → 세 주소를 다 알아야 하나
  → 그중 하나가 죽으면 누가 알려주나
  → 부하는 누가 나눠주나
```

```text
Service = 바뀌지 않는 주소 + 살아있는 Pod 목록 관리
```

---

# 2. 생성 — 세 층의 분업 (2026-08-14)

```yaml
# /tmp/web.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 3
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
        ports:
        - containerPort: 80
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
    targetPort: 80
```

## 포트가 세 개 나온다

```text
containerPort: 80    Pod 정의의 메모. 안 써도 동작한다
                     실제로 포트를 여는 건 컨테이너 안의 앱이다
port: 80             Service 가 받는 포트. 호출하는 쪽이 쓰는 번호
targetPort: 80       Pod 로 보낼 포트. 실제 목적지
```

## 발견 1 — 우리가 안 쓴 필드가 또 채워졌다

```text
root@master01:/# kubectl get svc web-svc -o jsonpath='{.spec}'
{
  "clusterIP": "10.106.225.222",
  "clusterIPs": ["10.106.225.222"],
  "internalTrafficPolicy": "Cluster",
  "ipFamilies": ["IPv4"],
  "ipFamilyPolicy": "SingleStack",
  "ports": [{"port":80,"protocol":"TCP","targetPort":80}],
  "selector": {"app":"web"},
  "sessionAffinity": "None",
  "type": "ClusterIP"
}
```

**우리가 쓴 건 `selector` 와 `ports` 뿐이다.**

`restartPolicy: Always`, `imagePullPolicy`, `strategy`, `terminationGracePeriodSeconds` 에 이은 **또 하나의 자동 주입**이다.

## 발견 2 — Service IP 는 apiserver 가 준다

```text
10.106.225.222
```

```text
Service 대역   10.96.0.0/12   →   10.96.0.0 ~ 10.111.255.255
```

```text
Pod IP       Calico(CNI)가 준다     노드별 /26 블록에서
Service IP   apiserver 가 준다      --service-cluster-ip-range 에서
```

**같은 클러스터 안에서 IP 를 나눠주는 주체가 둘이다.**

> **용어 주의.** `ClusterIP`(Service 주소, `10.96.0.0/12`)와 `cluster CIDR`(Pod 대역, `10.244.0.0/16`)은
> 완전히 다른 것이다. 이름이 비슷해 혼동하기 쉽다.

## 발견 3 — EndpointSlice 가 자동으로 생겼다

```text
root@master01:/# kubectl get endpointslices -o wide
NAME            ADDRESSTYPE   PORTS   ENDPOINTS
web-svc-52v7h   IPv4          80      10.244.5.45,10.244.5.44,10.244.30.92

root@master01:/# kubectl get endpointslices -o jsonpath='...'
web-svc-52v7h
  ownerRef: Service/web-svc
  addresses: ["10.244.5.45"] ["10.244.5.44"] ["10.244.30.92"]
```

**Pod IP 3개와 정확히 일치한다.**

```text
Deployment  8m56s
EndpointSlice  8m54s     ← 2초 뒤
```

**02 문서의 컨트롤러 사슬이 여기서도 그대로 돈다.**

```text
[02 문서]  Deployment 를 만들었다 → Deployment Controller → ReplicaSet
[여기]     Service 를 만들었다    → EndpointSlice Controller → EndpointSlice
```

07 문서 2라운드의 40개 바인딩 목록에 `system:controller:endpointslice-controller` 가 있었다. 그것이 이 일을 한다.

## 역할이 셋으로 나뉜다 ★

```text
[Service]         "app=web 인 Pod 로 보내라"        선언. 라벨만 안다
                  clusterIP: 10.106.225.222        고정 주소

[EndpointSlice]   "지금 그 Pod 들은 이것이다"        실제 목록. 계속 갱신
                  10.244.5.44 / 5.45 / 30.92

[iptables 규칙]   실제로 패킷을 바꿔치기한다         커널 안에 있다
```

**Service 자체에는 Pod IP 가 한 줄도 없다.**

```text
Pod 가 죽고 새로 뜨면
  → Service 는 그대로다 (selector 는 안 변하니까)
  → EndpointSlice 의 목록만 바뀐다
  → iptables 규칙이 그에 맞춰 다시 쓰인다
```

**이 분리가 "Service 주소는 안 바뀐다" 를 가능하게 한다.**

---

# 3. ClusterIP 는 실재하지 않는 주소다

1단계 `00-environment.md` 에 이렇게 적어뒀으나 확인은 안 했었다.

```text
10.96.0.0/12 는 실제로 어떤 인터페이스에도 붙지 않는 가상 대역이다
그래서 ping <ClusterIP> 는 실패하는 것이 정상이다
```

## 실측

```text
root@master01:/# ip -br addr
lo               UNKNOWN   127.0.0.1/8 ::1/128
ens33            UP        192.168.8.143/24 fe80::20c:29ff:fe8f:7cd4/64
tunl0@NONE       UNKNOWN   10.244.241.64/32
calidfcb2fc26f0@if3 UP     fe80::ecee:eeff:feee:eeee/64

root@master01:/# ip addr | grep 10.106
(출력 없음)

root@master01:/# ip route | grep 10.96
(출력 없음)

root@master01:/# ping -c 2 -W 2 10.106.225.222
2 packets transmitted, 0 received, 100% packet loss

root@master01:/# curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 3 http://10.106.225.222
HTTP 200
```

```text
어디에도 없는 주소인데 TCP 연결이 된다
ping 은 안 되는데 curl 은 된다
```

```text
10.106.225.222 는 "주소" 가 아니라 "규칙을 찾기 위한 이름표" 다
패킷이 실제로 그 주소로 간 적은 한 번도 없다
```

```text
ping   ICMP. 규칙에 tcp 조건이 걸려 있어 안 걸린다 → 그대로 나가서 사라진다
curl   TCP 80. 규칙에 걸린다 → DNAT → Pod 로 간다
```

**"Service 가 죽었다" 고 오진하기 쉬운 지점이다.**

## 곁가지 — 인터페이스 목록에서 보이는 것

```text
tunl0@NONE   10.244.241.64/32
```

master01 의 Calico 블록(`10.244.241.64/26`) 첫 주소가 IPIP 터널 인터페이스에 붙어 있다.

```text
calidfcb2fc26f0@if3
```

**Pod 하나당 `cali*` 인터페이스가 하나씩 생긴다.** master01 에는 CoreDNS 하나뿐이라 이것도 하나다.

```text
Pod 의 netns 안   eth0
노드 쪽           calidfcb2fc26f0     ← 이 둘이 veth 쌍
@if3              "상대편은 3번 인터페이스" 라는 뜻
```

---

# 4. iptables 체인 전체 추적 ★★

## 모드 확인

이 클러스터는 iptables 모드다(`mode` 가 비어 있으면 기본값).

## 입구 — 모든 패킷이 검문소를 거친다

```text
PREROUTING    밖에서 들어온 패킷 (Pod 에서 온 것)
OUTPUT        이 컴퓨터가 만든 패킷 (노드에서 친 curl)
```

**둘 다 맨 위에서 `KUBE-SERVICES` 로 점프한다.** kube-proxy 가 두 군데에 건다.

## 1단계 — KUBE-SERVICES

```text
root@master01:/# sudo iptables -t nat -L KUBE-SERVICES -n | head -20
Chain KUBE-SERVICES (2 references)
target                     prot  source      destination
KUBE-SVC-UNTI3ZWT6KQG4YW5  6  -- 0.0.0.0/0   10.106.225.222  /* k8s-lab/web-svc cluster IP */ tcp dpt:80
KUBE-SVC-NPX46M4PTMTKRN6Y  6  -- 0.0.0.0/0   10.96.0.1       /* default/kubernetes:https cluster IP */ tcp dpt:443
KUBE-SVC-TCOU7JCQXEZGVUNU 17  -- 0.0.0.0/0   10.96.0.10      /* kube-system/kube-dns:dns cluster IP */ udp dpt:53
KUBE-SVC-ERIFXISQEP7F7OF4  6  -- 0.0.0.0/0   10.96.0.10      /* kube-system/kube-dns:dns-tcp cluster IP */ tcp dpt:53
KUBE-SVC-JD5MR3NA4I4DYORP  6  -- 0.0.0.0/0   10.96.0.10      /* kube-system/kube-dns:metrics cluster IP */ tcp dpt:9153
```

**클러스터의 모든 Service 가 한 줄씩 있다.**

`prot` 열이 숫자다. `6` = TCP, `17` = UDP, `0` = 전체.

### 발견 4 — 1단계에서 세 번 만난 주소가 여기 있다 ★

```text
KUBE-SVC-NPX46M4PTMTKRN6Y  6  --  0.0.0.0/0  10.96.0.1  /* default/kubernetes:https */ tcp dpt:443
```

```text
[07 1라운드]  apiserver.crt 의 SAN 목록에 있었다
[07 2라운드]  Pod 안의 3종 세트로 apiserver 를 호출할 때 쓰는 주소
[08 실험 3]   calico-kube-controllers 의 오류 메시지에 나왔다
              Get "https://10.96.0.1:443/apis/..." connection refused
```

**그 주소의 실체가 이 규칙 한 줄이다.**

```text
root@master01:/# kubectl get svc kubernetes -n default -o wide
NAME         TYPE        CLUSTER-IP   PORT(S)   AGE   SELECTOR
kubernetes   ClusterIP   10.96.0.1    443/TCP   10d   <none>

root@master01:/# kubectl get endpointslices kubernetes -n default -o wide
NAME         ADDRESSTYPE   PORTS   ENDPOINTS       AGE
kubernetes   IPv4          6443    192.168.8.143   10d
```

**`SELECTOR` 가 `<none>` 이다.**

```text
apiserver 는 Pod 가 아니다
  → hostNetwork 로 도는 Static Pod 다
  → 라벨로 찾을 대상이 아니다
  → 그래서 apiserver 가 EndpointSlice 를 직접 관리한다
```

`AGE 10d` — `kubeadm init` 이 만든 것이다.

### 발견 5 — 포트마다 규칙이 하나씩

```text
kube-dns:dns        17  10.96.0.10  udp 53
kube-dns:dns-tcp     6  10.96.0.10  tcp 53
kube-dns:metrics     6  10.96.0.10  tcp 9153
```

**Service 는 하나인데 규칙이 셋이다.** `ports` 정의마다 하나씩 생긴다.

## 2단계 — KUBE-SVC (부하 분산)

```text
root@master01:/# sudo iptables -t nat -L KUBE-SVC-UNTI3ZWT6KQG4YW5 -n
Chain KUBE-SVC-UNTI3ZWT6KQG4YW5 (1 references)
KUBE-MARK-MASQ             6  -- !10.244.0.0/16  10.106.225.222  tcp dpt:80
KUBE-SEP-JYMEC5OSPHHZCZBZ  0  --  0.0.0.0/0  0.0.0.0/0  /* -> 10.244.30.92:80 */ statistic mode random probability 0.33333333349
KUBE-SEP-E3YUESXC7MXGMDES  0  --  0.0.0.0/0  0.0.0.0/0  /* -> 10.244.5.44:80  */ statistic mode random probability 0.50000000000
KUBE-SEP-OW3MII5ANTJ7K6A2  0  --  0.0.0.0/0  0.0.0.0/0  /* -> 10.244.5.45:80  */
```

## 3단계 — KUBE-SEP (실제 변환)

```text
root@master01:/# for sep in $(sudo iptables -t nat -L KUBE-SVC-UNTI3ZWT6KQG4YW5 -n | grep -o 'KUBE-SEP-[A-Z0-9]*'); do
  echo "--- $sep ---"; sudo iptables -t nat -L $sep -n | grep DNAT
done
--- KUBE-SEP-JYMEC5OSPHHZCZBZ ---
DNAT  6  --  0.0.0.0/0  0.0.0.0/0  tcp to:10.244.30.92:80
--- KUBE-SEP-E3YUESXC7MXGMDES ---
DNAT  6  --  0.0.0.0/0  0.0.0.0/0  tcp to:10.244.5.44:80
--- KUBE-SEP-OW3MII5ANTJ7K6A2 ---
DNAT  6  --  0.0.0.0/0  0.0.0.0/0  tcp to:10.244.5.45:80
```

**EndpointSlice 의 주소 3개와 정확히 일치한다.**

## 전체 경로

```text
curl http://10.106.225.222:80
        │
        ▼
   OUTPUT (또는 PREROUTING)
        ▼
   KUBE-SERVICES        "10.106.225.222 tcp 80" → KUBE-SVC-UNTI3ZWT6KQG4YW5
        ▼
   KUBE-SVC-...         ① !10.244.0.0/16 이면 MARK (SNAT 대상 표시)
                        ② 확률로 SEP 하나 선택
        ▼
   KUBE-SEP-...         DNAT to 10.244.5.44:80
        ▼
   목적지가 바뀐 패킷이 라우팅된다
        ▼
   KUBE-POSTROUTING     표시가 있으면 MASQUERADE
        ▼
   10.244.5.44:80 도착
```

---

# 5. 부하 분산은 확률 세 줄이다

```text
KUBE-SEP-JYMEC...   probability 0.33333333349
KUBE-SEP-E3YUE...   probability 0.50000000000
KUBE-SEP-OW3MI...   (확률 없음)
```

```text
첫 줄    33.3%                → 1/3
둘째 줄  남은 66.7% 중 50%    → 1/3
셋째 줄  나머지 전부           → 1/3
```

**위에서부터 훑으니 앞 규칙에 안 걸린 것만 다음으로 간다.** 그래서 확률이 점점 올라간다.

`0.33333333349` 라는 어중간한 값은 iptables 가 내부적으로 32비트 정수를 쓰기 때문이다. 1/3 을 정확히 표현할 수 없어 가장 가까운 값을 쓴다.

## 실측

```text
root@master01:/# for p in $(kubectl get pods -l app=web -o name); do
  kubectl exec $p -- sh -c "hostname > /usr/share/nginx/html/index.html"
done

root@master01:/# for i in $(seq 1 20); do curl -s http://10.106.225.222; done | sort | uniq -c
      6 web-7fc7749b56-4qp5p
      6 web-7fc7749b56-9mh72
      8 web-7fc7749b56-zngb8

root@master01:/# for i in $(seq 1 300); do curl -s http://10.106.225.222; done | sort | uniq -c
     99 web-7fc7749b56-4qp5p
    101 web-7fc7749b56-9mh72
    100 web-7fc7749b56-zngb8
```

```text
20번    6 / 6 / 8       편차가 보인다
300번   99 / 101 / 100  거의 정확히 1/3
```

**순서대로 돌리는 게 아니라 매번 주사위를 던지는 방식이다.** 표본이 커지면 수렴한다.

```text
부하 분산이 별도 프로그램이 아니다
"확률 규칙 세 줄" 이 전부다
→ 그래서 제어 평면이 죽어도 분산이 계속된다
```

---

# 6. MASQ — 표시하는 곳과 실행하는 곳이 다르다

```text
KUBE-MARK-MASQ  6  -- !10.244.0.0/16  10.106.225.222  tcp dpt:80
                      ^^^^^^^^^^^^^^^
                      "Pod 대역이 아닌 곳에서 온 것"
```

**1단계 문서를 고칠 때 한 말이 여기 실물로 있다.**

```text
[그때]  "kube-proxy 가 clusterCIDR 로 'Pod 대역인지 외부인지' 를 판단한다"
[지금]  !10.244.0.0/16 → KUBE-MARK-MASQ
```

## DNAT 과 판단 기준이 다르다

```text
DNAT   받는 사람을 바꾼다
       Service 주소를 불렀으면 무조건 한다. 누가 불렀는지는 무관

SNAT   보내는 사람을 바꾼다
       Service 를 거쳤고, 출발지가 !10.244.0.0/16 일 때만
```

**"Pod 끼리는 DNAT 이 필요 없다" 가 아니다.** Pod 가 Service 를 불러도 DNAT 은 한다. 조건부인 건 SNAT 뿐이다.

## 왜 필요한가 — 답장 경로를 맞추기 위해서다

```text
Pod 주소끼리 (10.244.x.x)     Calico 터널(tunl0)로 다닌다
노드 주소끼리 (192.168.8.x)   ens33 로 평문으로 다닌다
```

```text
[노드가 Service 를 부르고 SNAT 을 안 했다면]

  가는 길   dst 10.244.5.44 (DNAT 결과) → Pod 대역 → tunl0 (IPIP 캡슐화)
  답장      dst 192.168.8.143 (원래 출발지) → Pod 대역 아님 → ens33 평문

  갈 때 터널 / 올 때 평문 → 비대칭
```

```text
[SNAT 을 하면]

  출발지 192.168.8.143 → 10.244.241.64 (master01 의 tunl0 주소)
  답장   dst 10.244.241.64 → Pod 대역 → tunl0

  갈 때 터널 / 올 때 터널 → 대칭
```

**`!10.244.0.0/16` 은 "Pod 에서 왔나" 가 아니라 "답장이 터널로 돌아올 수 있는 주소인가" 를 묻는 것이다.**

> **2026-08-14 수정.** 이 문서 초판에는 "응답은 직행한다 → 경로가 비대칭" 이라고만 적었다.
> "직행" 이 무엇을 뜻하는지가 흐렸고, 응답이 master01 로 안 돌아온다는 오해를 준다.
> 실제 비대칭은 **터널 대 평문**이다. 위 내용으로 교체했다.

## 실측 — nginx 접속 로그로 확인 (5가지 경우)

nginx 는 접속 로그 맨 앞에 요청자 IP 를 기록한다. 이것으로 SNAT 여부를 눈으로 볼 수 있다.

```bash
kubectl run nginx-log --image=nginx:1.27
kubectl expose pod nginx-log --port=80        # ClusterIP 10.111.84.38
kubectl logs -f nginx-log
kubectl run nettest --image=busybox:1.36 --restart=Never -- sleep 3600
```

**두 Pod 모두 worker01 에 배치됐다.** `nginx-log` 10.244.5.51 / `nettest` 10.244.5.52.

```text
02:04:02  192.168.8.142   curl   worker01 → 10.244.5.51 (Pod IP 직접)
01:59:13  10.244.241.64   curl   master01 → 10.111.84.38 (Service)
02:02:37  10.244.5.52     Wget   nettest  → 10.244.5.51 (Pod IP 직접)
02:06:20  10.244.5.52     Wget   nettest  → 10.111.84.38 (Service)
02:06:53  192.168.8.142   curl   worker01 → 10.111.84.38 (Service)
```

## 발견 6-1 — 주소가 바뀐 것은 한 경우뿐이다

```text
출발             목적지 Pod 위치   Service   보이는 출발지        주소 바뀜
──────────────────────────────────────────────────────────────────────────
master01(노드)   다른 노드         O        10.244.241.64      O ★
worker01(노드)   같은 노드         O        192.168.8.142      X
worker01(노드)   -- (Pod IP 직접)  X        192.168.8.142      X
nettest(Pod)     같은 노드         O        10.244.5.52        X
nettest(Pod)     -- (Pod IP 직접)  X        10.244.5.52        X
```

```text
Pod 가 출발이면 언제나 그대로다
  → 받는 쪽이 진짜 클라이언트 Pod 를 안다
  → NetworkPolicy 와 접근 로그가 의미를 갖는다
  → SNAT 을 최소한만 하는 이유다
```

## 발견 6-2 — Service 를 안 거치면 SNAT 판단 자체를 안 한다

```text
worker01 → Service      192.168.8.142 (SNAT 함)
worker01 → Pod IP 직접  192.168.8.142 (SNAT 안 함)
```

**둘 다 노드 출발인데 하나는 하고 하나는 안 한다.**

```text
KUBE-MARK-MASQ 는 KUBE-SVC-... 체인 안에 있다
→ Service 규칙을 안 타면 표시를 붙일 기회가 없다
→ SNAT 도 안 일어난다
```

**Calico 의 `natOutgoing` 은 클러스터 밖으로 나갈 때만 동작하므로 여기서는 걸리지 않는다.**

## 발견 6-3 — 로그만 보면 SNAT 여부를 오판한다 ★

같은 노드 케이스(`192.168.8.142`)가 "SNAT 을 안 한 것" 인지 "했는데 주소가 같은 것" 인지 로그로는 구분이 안 된다. `conntrack` 으로 갈렸다.

```text
root@worker01:/# curl -s -o /dev/null http://10.111.84.38; sudo conntrack -L -d 10.111.84.38
tcp 6 119 TIME_WAIT
  src=192.168.8.142 dst=10.111.84.38  sport=56418 dport=80
  src=10.244.5.51   dst=192.168.8.142 sport=80    dport=64134
                                                  ^^^^^^^^^^^
tcp 6 113 TIME_WAIT
  src=192.168.8.142 dst=10.111.84.38  sport=39232 dport=80
  src=10.244.5.51   dst=192.168.8.142 sport=80    dport=53184
```

```text
[SNAT 이 없었다면]
  응답 방향은 원본을 뒤집은 것이어야 한다 → dport 가 원본 sport 와 같아야 한다
     56418 → 56418

[실제]
  56418 → 64134     포트가 바뀌었다 = SNAT 이 일어났다
  39232 → 53184
```

**주소는 그대로인데 포트만 바뀌었다.** `random-fully` 옵션 때문이다(아래 참조).

```text
nginx 로그는 IP 만 기록한다. 포트를 안 찍는다
→ 로그 하나만 보면 "SNAT 안 했다" 로 오판한다
```

> **미확인**: 같은 노드일 때 주소가 안 바뀌는 이유는 확인하지 못했다.
> 나가는 인터페이스가 `cali*` 인데 여기에는 IPv4 주소가 없어
> 커널이 마땅한 주소를 못 골랐을 것으로 추측하나, 근거를 확인하지 못했다.
> **관측된 사실은 "주소는 그대로, 포트는 바뀜" 뿐이다.**
>
> 다만 결과적으로는 문제가 없다. 목적지가 같은 노드면 응답이 노드 밖으로
> 나가지 않으므로 터널/평문 비대칭이 생길 여지가 없다.

## 실측

```text
root@master01:/# sudo iptables -t nat -L KUBE-MARK-MASQ -n
Chain KUBE-MARK-MASQ (15 references)
MARK  0  --  0.0.0.0/0  0.0.0.0/0  MARK or 0x4000

root@master01:/# sudo iptables -t nat -L KUBE-POSTROUTING -n
Chain KUBE-POSTROUTING (1 references)
RETURN      0  --  0.0.0.0/0  0.0.0.0/0  mark match ! 0x4000/0x4000
MARK        0  --  0.0.0.0/0  0.0.0.0/0  MARK xor 0x4000
MASQUERADE  0  --  0.0.0.0/0  0.0.0.0/0  /* kubernetes service traffic requiring SNAT */ random-fully

root@master01:/# sudo iptables -t nat -L POSTROUTING -n
Chain POSTROUTING (policy ACCEPT)
KUBE-POSTROUTING  0  --  0.0.0.0/0  0.0.0.0/0  /* kubernetes postrouting rules */
cali-POSTROUTING  0  --  0.0.0.0/0  0.0.0.0/0  /* cali:0i8pjzKKPyA34aQD */
```

## 발견 6 — 세 줄을 순서대로 읽으면

```text
1. 표시가 없으면 → 그냥 돌아가라 (SNAT 대상이 아니다)
2. 표시가 있으면 → 그 표시를 지운다
3. 그리고 MASQUERADE
```

**2번에서 표시를 지우는 이유**: 남겨두면 나중에 다른 곳에서 또 SNAT 대상으로 판정될 수 있다.

```text
표시하는 곳    KUBE-MARK-MASQ        15군데에서 부른다
실행하는 곳    KUBE-POSTROUTING      한 곳
```

**`random-fully`** 는 SNAT 시 출발지 포트를 완전 무작위로 고르는 옵션이다. 포트 충돌 확률을 줄인다.

## 발견 7 — Calico 도 같은 자리에 규칙을 건다

```text
KUBE-POSTROUTING   kube-proxy 가 건 것
cali-POSTROUTING   Calico 가 건 것
```

```text
kube-proxy   Service 트래픽의 SNAT
Calico       Pod 가 외부로 나갈 때의 SNAT (natOutgoing)
```

**접두사(`cali:`)로 주인을 구분하는 것도 어노테이션과 같은 방식이다.**

---

# 7. DNS 로 부르기

```text
root@master01:/# kubectl exec deploy/web -- curl -s --max-time 2 http://web-svc | head -3
web-7fc7749b56-zngb8
```

**IP 없이 이름만으로 됐다.**

```text
/etc/resolv.conf
  nameserver 10.96.0.10                    ← CoreDNS 의 ClusterIP
  search k8s-lab.svc.cluster.local svc.cluster.local cluster.local
  ndots:5
```

```text
"web-svc" 를 찾으면
  → search 목록을 하나씩 붙여본다
  → web-svc.k8s-lab.svc.cluster.local  ← 여기서 찾는다
```

**`nameserver 10.96.0.10` 도 Service 다.** `KUBE-SERVICES` 에서 본 그 규칙이다.

```text
DNS 조회조차 Service 를 거친다
→ CoreDNS Pod 가 죽고 새로 떠도 10.96.0.10 은 안 바뀐다
```

> **미확인**: `/etc/resolv.conf` 실물과 `nslookup` 출력은 확인하지 않았다.

---

# 8. Pod 가 죽으면 (preStop 없음)

## 타임라인

```text
09:49:33   SEP=3                                     정상
09:49:34   SEP=2                                     규칙에서 빠짐
09:49:34   === FAIL ===                              ★ 요청 실패
09:49:35   EndpointSlice → 10.244.5.44, 30.92        2개
09:49:35   EndpointSlice → 44, 30.92, 5.46           새 Pod 추가
09:49:37   SEP=3                                     복구
```

```text
Pod 가 규칙에서 빠지는 데      약 1초
새 Pod 가 규칙에 들어오는 데   약 3초
그 사이 실패                   1회
```

## 발견 8 — 삭제 순간에 요청이 실패했다 ★★

```text
kubectl delete pod 를 치면 두 경로가 동시에 출발한다

[A] kubelet 이 컨테이너에 SIGTERM → nginx 즉시 종료 → 포트 80 닫힘
[B] EndpointSlice 에서 IP 제거 → kube-proxy 감지 → iptables 에서 제거
```

```text
A 가 먼저 끝나면   규칙은 아직 그 Pod 를 가리킨다
                  → 요청이 죽은 Pod 로 간다 → connection refused

B 가 먼저 끝나면   실패 없음
```

**이번엔 A 가 이겼다.**

```text
nginx 는 SIGTERM 을 받으면 바로 죽는다 (00 문서: 1초 미만)
규칙 갱신은 apiserver 를 왕복해야 한다
  Pod 삭제 → EndpointSlice Controller → apiserver
  → kube-proxy 가 watch 로 감지 → iptables 다시 씀
```

**경로 B 가 더 길다.**

### 실무 영향

```text
[실험]   0.3초 간격 = 초당 3회 → 실패 1회 → 창이 약 0.3초
[실제]   초당 1000 요청이면 0.3초 × 1000 = 약 300건 실패
```

**Pod 하나 지울 때마다 300건이다.** 롤링 업데이트로 10개를 교체하면 3000건이 된다.

**"배포할 때만 가끔 502 가 난다" 의 정체다.**

## 발견 9 — 새 Pod 는 기본 페이지를 준다

09:49:36 부터 응답에 nginx 기본 HTML 이 섞였다.

```text
우리는 그때 있던 Pod 3개에만 index.html 을 썼다
새 Pod 는 이미지에서 그대로 시작한다 → 우리가 쓴 파일이 없다
```

**00 문서에서 확인한 overlayfs 구조 그대로다.**

```text
lowerdir   이미지 레이어. 읽기 전용. 공유
upperdir   컨테이너의 쓰기 레이어. Pod 마다 따로

kubectl exec 으로 쓴 파일 → upperdir 에 들어간다
Pod 가 죽으면 upperdir 이 사라진다
```

```text
[해서는 안 되는 것]
  컨테이너에 접속해 설정 파일을 고친다
  → 그 Pod 가 죽으면 원상복구된다
  → 다른 Pod 는 여전히 옛 설정이다

[해야 하는 것]
  설정은 ConfigMap 으로 / 데이터는 볼륨으로 / 변경은 이미지 재배포로
```

**우연히 만든 상황인데 "상태를 컨테이너에 두면 안 되는 이유" 가 눈앞에서 재현됐다.**

## 발견 10 — 확률도 다시 계산된다

Pod 가 2개였던 구간의 응답에 `9mh72` 가 한 번도 안 나온다.

```text
Pod 3개   0.333 / 0.5 / (없음)
Pod 2개   0.5 / (없음)
Pod 3개   0.333 / 0.5 / (없음)
```

**kube-proxy 는 규칙을 고치는 게 아니라 통째로 다시 쓴다.**

---

# 9. preStop 으로 막을 수 있나

```bash
kubectl patch deployment web --type=json -p='[{
  "op":"add",
  "path":"/spec/template/spec/containers/0/lifecycle",
  "value":{"preStop":{"exec":{"command":["sh","-c","sleep 5"]}}}
}]'
```

`template` 변경이라 롤링 업데이트가 일어난다.

## 타임라인

```text
10:00:48   삭제 요청                    ← T0
10:00:49   SEP=2                        규칙에서 제거 (T0+1)
10:00:50   SEP=3                        새 Pod 추가 (T0+2)
10:00:54   kubectl delete 반환          ← T0+6
```

**FAIL 0회.**

## 대비

```text
                        [preStop 없음]        [preStop 5초]
kubectl delete 소요       1초 미만              6초
규칙에서 제거             T0+1초                T0+1초
FAIL                     1회                   0회
```

## 발견 11 — preStop 은 "순서 강제" 가 아니라 "시간 벌기" 다

```text
두 경로는 여전히 동시에 출발한다
preStop 은 A(종료) 쪽을 5초 늦춰서 B(규칙 제거)가 먼저 끝나게 할 뿐이다
```

```text
T0+1   규칙에서 빠짐 → 새 요청이 안 온다
T0+5   preStop 끝 → SIGTERM
T0+6   종료
```

**4초 동안 "살아있지만 아무도 안 부르는" 상태였다.** 그게 preStop 이 만든 안전 구간이다.

**자주 오해하는 것**

```text
"앱이 처리 중인 요청을 마무리할 시간"     → 그건 SIGTERM 이후의 일
"라우팅에서 빠질 때까지 기다리는 시간"     → preStop 의 진짜 목적
```

## 발견 12 — EndpointSlice 에 순간 4개가 있었다 ★

```text
10:00:48   10.244.5.47, 10.244.30.93, 10.244.5.48
10:00:50   10.244.5.47, 10.244.30.93, 10.244.5.48 + 1 more...   ← 4개
10:00:54   10.244.30.93, 10.244.5.48, 10.244.5.49               ← 3개
```

같은 시각 iptables 는 `SEP=2` → `SEP=3` 이었다. **주소는 4개인데 규칙은 3개다.**

```text
EndpointSlice 는 주소마다 상태를 갖는다

endpoints:
- addresses: ["10.244.5.47"]
  conditions:
    ready: false          ← 죽는 중
    serving: false
    terminating: true
```

**`ENDPOINTS` 열은 상태와 무관하게 주소를 다 보여준다.** kube-proxy 는 `ready: true` 인 것만 규칙으로 만든다.

```text
목록에 있다  ≠  트래픽을 받는다
```

**왜 안 지우고 표시만 하나**

```text
이미 연결된 세션은 마저 처리해야 한다
  → 목록에서 지우면 "이 주소는 없다" 가 된다
  → terminating 으로 표시하면
     "새 연결은 주지 마라. 기존 연결은 유지해도 된다" 를 표현할 수 있다
```

```text
ready        새 요청을 받아도 되나
serving      지금 응답할 수 있나
terminating  종료 중인가
```

> **미확인**: `conditions` 필드를 직접 조회하지는 않았다.
> ```bash
> kubectl get endpointslices <이름> \
>   -o jsonpath='{range .endpoints[*]}{.addresses}{"\t"}{.conditions}{"\n"}{end}'
> ```

## 발견 13 — 새 Pod 가 2초 만에 들어왔다

```text
10:00:49   SEP=2
10:00:50   SEP=3      ← 1초 만에 복구
```

**2개였던 구간이 1초뿐이다.**

```text
01 문서 발견 6 — ReplicaSet 은 삭제 완료를 안 기다린다
  → deletionTimestamp 가 찍힌 Pod 를 "이미 없는 것" 으로 센다
  → T0 에 바로 새 Pod 를 만든다
이미지가 노드에 있다 → 2초 만에 Ready
```

**옛 Pod 가 아직 살아있는데(preStop 중) 새 Pod 가 이미 트래픽을 받았다.** 실제로는 3개가 계속 유지된 셈이다.

## preStop 값을 어떻게 잡나

```text
[관측]  규칙 제거까지 T0+1초 / preStop 5초 / 여유 4초
```

```text
규칙 갱신이 느려지는 조건
  노드가 많다        kube-proxy 가 노드마다 각자 갱신한다
  Service 가 많다    iptables 규칙 전체를 다시 쓰므로 오래 걸린다
  apiserver 가 바쁘다  watch 알림이 늦게 온다
```

```text
너무 짧으면   여전히 FAIL 이 난다
너무 길면     배포가 그만큼 느려진다 (Pod 10개 × 10초 = 100초 추가)
```

**grace period 안에 들어가야 한다.** `preStop` 은 `terminationGracePeriodSeconds` 를 늘려주지 않는다.

---

# 10. kube-proxy 를 죽여도 트래픽이 유지되는가 ★★

**1단계 결론의 최종 검증이다.**

```bash
kubectl -n kube-system patch daemonset kube-proxy \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"nonexistent":"true"}}}}}'
```

없는 라벨을 요구하게 만들어 모든 노드에서 내린다.

## 타임라인

```text
10:08:51   DaemonSet 패치
10:08:52   kube-proxy 3개 전부 Terminating → Error
10:08:52 ~ 10:10:11   kube-proxy 가 하나도 없다
                      트래픽 전부 정상. 실패 0회        ★★ 73초
10:10:04   Pod 삭제 (10.244.5.49)
10:10:12 ~ 10:10:51   약 40초간 요청의 1/3 이 실패
10:10:50   DaemonSet 복구 패치
10:10:53   kube-proxy 3개 Running
10:10:54   실패 멈춤
```

## 발견 14 — 73초간 멀쩡했다 ★★

```text
10:08:52   kube-proxy 전부 Error
10:08:52   web-ff8f86bff-bfvjn      정상
10:09:30   web-ff8f86bff-svrbl      정상
10:10:11   web-ff8f86bff-svrbl      정상
```

**한 번도 안 끊겼다. `SEP=3` 이 내내 유지됐다.**

```text
[1단계에서 한 말]
  "kube-proxy 가 이미 규칙을 깔아놨고, 그 규칙은 커널에 있다"
  "그래서 제어 평면이 넷 다 죽어도 트래픽이 안 끊긴다"

[지금 실측]
  kube-proxy 를 직접 없앴다 → 73초간 아무 영향 없음
```

```text
kube-proxy   규칙을 쓴다      ← 없어졌다
커널         패킷을 처리한다   ← 계속 일한다
```

**설정하는 자와 전달하는 자가 정말로 분리되어 있다.**

## 발견 15 — 그런데 Pod 를 지우자 무너졌다 ★★

```text
root@master01:/# kubectl get endpointslices -o wide
web-svc-52v7h   10.244.30.93, 10.244.5.48, 10.244.5.50     ← 갱신됐다

root@master01:/# sudo iptables -t nat -L KUBE-SVC-UNTI3ZWT6KQG4YW5 -n
KUBE-SEP-4HDH5GK2IWJ6YG5F  -> 10.244.30.93:80
KUBE-SEP-OEV6IWGH6BX7I3MY  -> 10.244.5.48:80
KUBE-SEP-NJYBQ557TJTEKYJK  -> 10.244.5.49:80     ← 죽은 Pod
```

**`10.244.5.49` 는 이미 없다. 새 Pod 는 `10.244.5.50` 인데 규칙에 없다.**

```text
선언(EndpointSlice)   갱신됐다      ← apiserver 안의 일. 정상 동작
실제(iptables)        안 됐다       ← 노드의 일. kube-proxy 가 해야 한다
```

**"선언은 바뀌었는데 실제가 안 따라간다" 가 눈에 보인다.**

```text
약 40초 동안 12번 실패 / 성공 23번쯤 → 약 1/3
규칙 3개 중 1개가 죽은 IP 를 가리킨다
```

실패한 요청은 `--max-time 2` 로 끊겨 2초씩 걸렸다. 타임스탬프 간격이 벌어진 것이 그 증거다.

## 발견 16 — 복구는 3~4초. 규칙을 통째로 다시 그린다

```text
10:10:50   복구 패치
10:10:52   j25hh Running        (2초)
10:10:53   79w2l, 6284z Running (3초)
10:10:54   실패 멈춤
```

복구 후 체인 이름이 바뀌었다.

```text
[중단 전]  KUBE-SEP-NJYBQ557TJTEKYJK  -> 10.244.5.49
[복구 후]  KUBE-SEP-OJD6ZUNCFTAL4HD6  -> 10.244.5.50
```

```text
kube-proxy 는 시작할 때 현재 상태를 apiserver 에서 전부 읽어
규칙을 처음부터 다시 만든다
→ "밀린 것을 따라잡는다" 가 아니라 "현재 상태를 보고 다시 그린다"
```

**컨트롤러의 조정 루프와 같은 방식이다.**

## 결론

```text
[kube-proxy 가 죽으면]
  기존 트래픽   정상. 규칙이 커널에 남아 있다
  Pod 변경      규칙이 갱신 안 된다 → 죽은 IP 로 계속 보낸다   ★
  복구          3~4초. 현재 상태를 보고 규칙을 다시 그린다
```

**"kube-proxy 가 죽어도 괜찮다" 는 절반만 맞다.**

```text
아무 일도 안 일어나면   괜찮다
Pod 가 하나라도 바뀌면  그 순간부터 트래픽이 샌다
```

**08 문서 실험 3과 같은 구조다.**

```text
[그때]  "제어 평면이 죽어도 서비스는 산다" → 다만 다음 장애에 무방비다
[지금]  "kube-proxy 가 죽어도 서비스는 산다" → 다만 Pod 가 바뀌는 순간 무너진다
```

---

# 겪은 문제 — 관측 명령의 버그

```bash
echo "$(date '+%H:%M:%S') $(curl -s --max-time 2 http://10.106.225.222 | head -1 || echo '=== FAIL ===')"
```

**실패했는데 `=== FAIL ===` 대신 빈 줄이 나왔다.**

```text
|| 가 curl 이 아니라 head 의 종료 코드를 본다

curl 실패 → 출력이 비어있음 → head 는 그걸 받아 정상 종료(0)
→ || 가 안 걸린다 → echo 가 안 실행된다 → 빈 줄
```

**`head -1` 을 추가하면서 생긴 문제다.** 그전 명령(`head` 없음)에서는 제대로 찍혔다.

```bash
while true; do
  r=$(curl -s --max-time 2 http://<ClusterIP> | head -1)
  [ -z "$r" ] && r="=== FAIL ==="
  echo "$(date '+%H:%M:%S') $r"
  sleep 0.5
done
```

> preStop 실험 출력에는 빈 줄이 하나도 없었다. **그건 진짜로 실패 0회가 맞다.**

---

# 정리

```text
 1. Service 는 "바뀌지 않는 주소 + 살아있는 Pod 목록 관리" 다

 2. 역할이 셋으로 나뉜다
    Service        고정 주소 + 라벨 셀렉터. Pod IP 를 모른다
    EndpointSlice  실제 목록. 주소마다 ready/serving/terminating
    iptables       실제 변환. 커널 안에 있다

 3. Service IP 는 apiserver 가, Pod IP 는 Calico 가 준다
    ClusterIP(10.96.0.0/12) 와 cluster CIDR(10.244.0.0/16)은 다른 것이다

 4. ClusterIP 는 어느 인터페이스에도 없다
    ping 실패 / curl 성공. "주소" 가 아니라 "규칙을 찾는 이름표" 다

 5. KUBE-SERVICES → KUBE-SVC → KUBE-SEP → DNAT
    1단계에서 세 번 만난 10.96.0.1 도 여기 한 줄로 있다

 6. 부하 분산은 확률 세 줄이다
    0.33333333349 / 0.5 / (없음) → 각각 1/3
    300번에 99 / 101 / 100

 7. MASQ 는 표시하는 곳(15군데)과 실행하는 곳(1군데)이 다르다
    !10.244.0.0/16 판단이 1단계에서 말한 그 clusterCIDR 판단이다

 7-1. DNAT 과 SNAT 은 판단 기준이 다르다
      DNAT  Service 주소를 불렀나 (누가 불렀는지 무관)
      SNAT  출발지가 Pod 대역인가 (Service 를 거쳤을 때만 판단)

 7-2. SNAT 의 목적은 답장 경로를 터널로 맞추는 것이다
      5가지 경우를 실측했고 주소가 바뀐 것은 노드→다른노드 하나뿐이다
      Pod 출발은 언제나 그대로 — 받는 쪽이 진짜 클라이언트를 안다

 7-3. 로그만 보면 SNAT 여부를 오판한다
      같은 노드일 때 주소는 그대로인데 포트가 바뀌었다 (conntrack 으로 확인)

 8. Pod 삭제 시 두 경로가 경쟁한다
    SIGTERM(빠름) vs 규칙 제거(느림) → FAIL 1회
    preStop 5초를 넣으면 FAIL 0회. 대신 삭제가 6초

 9. preStop 은 순서 강제가 아니라 시간 벌기다

10. EndpointSlice 는 terminating 주소를 지우지 않고 표시만 한다
    목록에 있다 ≠ 트래픽을 받는다

11. kube-proxy 를 죽여도 73초간 무중단이었다
    그러나 Pod 가 바뀌자 요청의 1/3 이 실패했다
    선언은 갱신되는데 실제가 안 따라간다

12. kube-proxy 는 복구 시 규칙을 통째로 다시 그린다 (체인 이름이 바뀐다)

13. 컨테이너에 직접 쓴 파일은 그 Pod 와 함께 사라진다
    새 Pod 는 이미지 상태로 시작한다
```

# 실습 리소스

```text
namespace   k8s-lab       유지
web         삭제됨        Deployment
web-svc     삭제됨        Service (EndpointSlice 도 함께)
/tmp/web.yaml             삭제됨

kube-proxy DaemonSet      nodeSelector 원상복구 확인함
                          {"kubernetes.io/os":"linux"}
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              1절 — 고정 주소 + 목록 관리
2. 생성 시 동작하는 Controller   EndpointSlice Controller
                                실제 규칙은 kube-proxy(DaemonSet)가 쓴다
3. 주요 Spec 과 Status 필드     spec: selector / ports / type / clusterIP /
                                      sessionAffinity / internalTrafficPolicy
4. 다른 오브젝트와의 연결        Pod(셀렉터), EndpointSlice(소유), Node(kube-proxy)
5. 장애 사례                    8절 삭제 시 FAIL / 10절 kube-proxy 중단
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            preStop 없으면 배포 시 요청이 샌다 /
                                셀렉터 중복 / ping 으로 판단하면 안 된다 /
                                kube-proxy 가 죽으면 변경이 반영 안 된다
```

# 미확인 목록

```text
1. /etc/resolv.conf 실물과 nslookup 출력
2. EndpointSlice 의 conditions(ready/serving/terminating) 직접 조회
3. kube-proxy 의 mode 설정값 (ConfigMap 확인 안 함. iptables 규칙 존재로 간접 확인)
4. sessionAffinity: ClientIP 로 바꿨을 때의 동작
5. port 와 targetPort 를 다르게 했을 때의 규칙 모양
6. kube-proxy 중단 중에 새 Service 를 만들면 규칙이 안 생기는지 (미실험)
7. internalTrafficPolicy: Local 의 동작
8. Pod IP 재사용 정책 (번호가 계속 올라가는 것은 관측했으나 규칙 미확인)
9. iptables 규칙 갱신에 걸리는 시간이 Service 개수에 따라 어떻게 변하는지
10. MASQUERADE 가 같은 노드 케이스에서 주소를 안 바꾸는 이유 (6-3 참조)
    cali* 에 IPv4 주소가 없어서일 것으로 추측하나 근거 미확인
11. 이 문서의 conntrack 설명은 "L3 방식 CNI" 전제 위에 있다
    브리지 방식 CNI(flannel, kubenet 등)에서는 같은 노드 Pod 간 통신이
    L2 로 지나가 netfilter 를 안 탄다. br_netfilter 가 그래서 필요하다
    Calico 는 Pod IP 를 /32 로 줘서 L2 지름길 자체를 없앴다 — 미실측
```

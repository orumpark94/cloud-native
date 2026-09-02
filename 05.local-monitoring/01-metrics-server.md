# Phase 1. Metrics Server

`05.local-monitoring/README.md` 의 **Phase 1** 작업 기록이다. 2026-09-01.

## 목적

`kubectl top` 을 되게 만든다. 그리고 4단계에서 못 잰 것을 잰다.

```text
[4단계에서 못 한 것]

  8번 글에서 메모리 limits 를 48Mi 로 낮췄더니
  기동은 됐는데 요청 30번에 Pod 셋이 전부 OOMKilled 됐다
  → 그럼 이 앱은 실제로 얼마를 쓰는가?
  → kubectl top 이 안 돼서 못 쟀다

[6단계에서 필요한 것]
  HPA 가 CPU 기준으로 스케일하려면 metrics.k8s.io 를 봐야 한다
  → 이게 없으면 CPU 기반 오토스케일링이 아예 안 된다
```

## 지표가 오는 경로

```text
[1] 컨테이너                커널의 cgroup 에 CPU/메모리 사용량이 기록된다
      ↓                    → cgroup 은 원래 "제한" 을 위해 만든 것이다
                             제한하려면 세야 하니, 세는 김에 읽을 수도 있다
                           → 측정은 제한의 부산물이다

[2] kubelet 의 cAdvisor     그 cgroup 값을 읽어 집계한다
      ↓                    → 각 노드 10250 포트의 /metrics/resource 로 노출
                           → ★ 이미 돌고 있다. 설치할 필요 없다

[3] metrics-server          세 노드의 kubelet 을 15초마다 긁어와 메모리에 들고 있다
      ↓                    → ★ 이 Phase 에서 설치하는 것

[4] APIService              "metrics.k8s.io 요청은 저 Service 로 넘겨라" 등록
      ↓
[5] kube-apiserver          요청을 받아 [3] 으로 중계한다

[6] kubectl top / HPA       물어본다
```

```text
★ metrics-server 는 측정하지 않는다. 모아둘 뿐이다
  측정은 커널이, 노출은 kubelet 이 한다
  → 그래서 metrics-server 가 죽어도 지표 자체는 안 사라진다
  → 다시 뜨면 15초 뒤부터 다시 답한다

★ pull 방식이다
  kubelet 이 보내는 게 아니라 metrics-server 가 가서 긁어온다
  → Prometheus 도 같은 방식이다
```

```text
★ 그리고 지표는 apiserver 를 안 거친다

  "모든 것이 apiserver 를 거친다" 는 오브젝트에 대한 원칙이다
  → etcd 에 직접 쓰지 않는다. 컴포넌트끼리 오브젝트를 주고받지 않는다
  → 데이터 평면 트래픽에는 적용되지 않는다

  [패턴]
    "무엇이 있는가"     apiserver 가 답한다     노드 목록, Service, EndpointSlice
    "그 안의 내용"      당사자에게 직접 간다     지표, Pod 트래픽

    metrics-server   노드 목록은 apiserver, 지표는 kubelet 직접
    kube-proxy       Service/EndpointSlice 는 watch, 트래픽은 커널 iptables
    Prometheus       타깃 목록은 apiserver, /metrics 는 Pod 직접

  [인가는 여전히 apiserver 가 한다]
    ClusterRole 의 nodes/metrics get 이 그 증거다
    → metrics-server 가 토큰을 들고 kubelet:10250 에 붙는다
    → kubelet 이 apiserver 에 TokenReview / SubjectAccessReview 를 한다
    → 허락되면 지표를 준다
    → ClusterRoleBinding system:auth-delegator 도 같은 맥락이다

  [apiserver 프록시 경로도 있다]
    kubectl get --raw "/api/v1/nodes/worker01/proxy/metrics/resource"
    → kubectl logs, exec 가 쓰는 길이다
    → metrics-server 는 안 쓴다
       15초마다 모든 노드를 긁으므로 apiserver 가 병목이 된다
       → 대신 kubelet 인증서를 스스로 검증해야 한다  ★ 4절의 x509 가 그 대가다
```

---

## 1. 사전 상태

```bash
kubectl top nodes
kubectl api-resources | grep -i metrics
kubectl get apiservice | grep -i metrics
```

```text
error: Metrics API not available
(나머지 둘은 아무것도 안 나옴)
```

---

## 2. 매니페스트를 먼저 읽는다

```bash
curl -fsSL -o metrics-server.yaml \
  https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

wc -l metrics-server.yaml
grep -n "^kind:\|^  name:" metrics-server.yaml
```

```text
202 metrics-server.yaml

  2  ServiceAccount   metrics-server
 10  ClusterRole      system:aggregated-metrics-reader
 30  ClusterRole      system:metrics-server
 53  RoleBinding      metrics-server-auth-reader
 69  ClusterRoleBinding  metrics-server:system:auth-delegator
 84  ClusterRoleBinding  system:metrics-server
 99  Service          metrics-server
116  Deployment       metrics-server
189  APIService       v1beta1.metrics.k8s.io      ★ 처음 보는 종류
```

### 2-1. APIService — API Aggregation

```yaml
apiVersion: apiregistration.k8s.io/v1
kind: APIService
metadata:
  name: v1beta1.metrics.k8s.io
spec:
  group: metrics.k8s.io
  groupPriorityMinimum: 100
  insecureSkipTLSVerify: true
  service:
    name: metrics-server
    namespace: kube-system
  version: v1beta1
  versionPriority: 100
```

```text
[kubectl top 이 어디에 물어보는가]

  kubectl top nodes
    → GET /apis/metrics.k8s.io/v1beta1/nodes
    → kube-apiserver 에게 물어본다

  그런데 kube-apiserver 에는 그런 API 가 없다
    → APIService 가 "metrics.k8s.io 요청은 이 Service 로 넘겨라" 고 등록한다
    → kube-apiserver 가 중계자가 된다
```

```text
[CRD 와의 차이]

  CRD          새 오브젝트 종류를 정의. 데이터를 etcd 에 저장
               예: ServiceMonitor, PrometheusRule

  APIService   요청을 다른 서버로 넘김. 저장하지 않음
               예: metrics.k8s.io

  ★ 지표는 실시간 값이라 저장할 이유가 없다
```

```text
[insecureSkipTLSVerify: true 가 이미 켜져 있다]

  metrics-server 는 기동할 때 자기 인증서를 스스로 만든다
  → 자체 서명이니 apiserver 가 검증하면 실패한다
  → 그래서 매니페스트가 미리 꺼놓고 나온다
```

### 2-2. Deployment 의 인자

```yaml
      - args:
        - --cert-dir=/tmp
        - --secure-port=10250
        - --kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname
        - --kubelet-use-node-status-port
        - --metric-resolution=15s
        image: registry.k8s.io/metrics-server/metrics-server:v0.9.0
```

```text
--kubelet-preferred-address-types   InternalIP 를 먼저 쓴다  ★ 나중에 문제가 된다
--metric-resolution=15s             15초마다 긁는다
--kubelet-insecure-tls              없다                     ★
```

### 2-3. probe 두 개

```yaml
        livenessProbe:
          httpGet:
            path: /livez
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /readyz
          initialDelaySeconds: 20
          periodSeconds: 10
```

```text
/livez    프로세스가 살아 있는가
/readyz   지표를 제공할 수 있는가
→ 4단계 8번 글에서 나눈 이유가 그대로 구현돼 있다
```

---

## 3. 적용 — 실패한다

```bash
kubectl apply -f metrics-server.yaml
kubectl get po -n kube-system -l k8s-app=metrics-server
```

```text
NAME                              READY   STATUS    RESTARTS   AGE
metrics-server-794dd65494-n6smj   0/1     Running   0          19s
                                  └─┬─┘   └───┬──┘  └───┬───┘
                              READY 0/1   죽지는 않음   재시작 없음
```

```bash
kubectl describe po -n kube-system -l k8s-app=metrics-server | tail -12
```

```text
Events:
  Normal  Scheduled  19s  default-scheduler  Successfully assigned
                          kube-system/metrics-server-794dd65494-n6smj to worker02
  Normal  Pulling / Pulled / Created / Started
```

```text
★ 이벤트에는 문제가 없다. worker02 에 정상적으로 떴다
  → kube-system 은 네임스페이스일 뿐이고, 노드는 스케줄러가 정한다
  → master01 에만 뜨는 것은 static Pod 뿐이다
     (etcd, kube-apiserver, kube-controller-manager, kube-scheduler)
```

```bash
kubectl logs -n kube-system -l k8s-app=metrics-server --tail=20
```

```text
I0901 00:50:27.824383  serving.go:405] Generated self-signed cert (/tmp/apiserver.crt, ...)
I0901 00:50:27.989871  handler.go:304] Adding GroupVersion metrics.k8s.io v1beta1 to ResourceManager
I0901 00:50:28.099995  secure_serving.go:214] Serving securely on [::]:10250

E0901 00:50:28.107368  scraper.go:149] "Failed to scrape node"
  err="Get \"https://192.168.8.141:10250/metrics/resource\":
       tls: failed to verify certificate: x509: cannot validate certificate for 192.168.8.141
       because it doesn't contain any IP SANs" node="worker02"
E0901 00:50:28.111874  ... node="master01"   (192.168.8.143)
E0901 00:50:28.112739  ... node="worker01"   (192.168.8.142)
```

```bash
kubectl get apiservice v1beta1.metrics.k8s.io
kubectl top nodes
```

```text
NAME                     SERVICE                      AVAILABLE
v1beta1.metrics.k8s.io   kube-system/metrics-server   False (MissingEndpoints)

error: Metrics API not available
```

---

## 4. 진단

### 4-1. 에러 메시지가 말하는 것

```text
x509: cannot validate certificate for 192.168.8.141
  because it doesn't contain any IP SANs
                       └────┬────┘
              "자체 서명이라서" 가 아니다
              "이 IP 가 인증서에 안 적혀 있어서" 다
```

```text
[SAN — Subject Alternative Name]
  인증서에 "이 이름들로 접속할 때 유효하다" 고 적어둔 목록
    DNS SAN   worker01
    IP SAN    192.168.8.141

[metrics-server 는 IP 로 붙는다]
  --kubelet-preferred-address-types=InternalIP,...
  → https://192.168.8.141:10250 으로 접속
  → kubelet 인증서의 SAN 에 그 IP 가 없다 → 거부
```

```text
★ TLS 연결 자체는 성공했다
  인증서를 받아왔고, 읽었고, SAN 을 확인했고, 안 맞아서 거부한 것
  → "연결이 안 된다" 가 아니라 "신원 확인에 실패했다" 다
```

```text
[왜 kubelet 인증서에 IP 가 없나 — 학습 데이터 기준. 확실하지 않음]

  kubeadm 은 kubelet 의 서빙 인증서를 클러스터 CA 로 발급하지 않는다
  → kubelet 이 스스로 자체 서명 인증서를 만든다
  → 거기에 IP SAN 이 안 들어간다

  제대로 발급하려면 kubelet 이 CSR 을 올리고 누가 승인해야 한다
  → 자동 승인은 "가짜 노드가 인증서를 받아가는" 위험이 있다
  → 그래서 기본값이 꺼져 있다 (serverTLSBootstrap: false)
```

### 4-2. TLS 연결이 셋인데 하나만 실패했다

```text
[1] metrics-server  →  kubelet          ✗ 실패. IP SAN 없음
[2] kube-apiserver  →  metrics-server   ✓ APIService 가 검증을 이미 꺼둠
[3] metrics-server  →  kube-apiserver   ✓ ServiceAccount 토큰으로 인증
```

```text
[3] 이 되는 이유
  Pod 에 자동 주입되는 것
    /var/run/secrets/kubernetes.io/serviceaccount/token    나는 누구다
    /var/run/secrets/kubernetes.io/serviceaccount/ca.crt   apiserver 검증용 CA
  → 그리고 ClusterRole system:metrics-server 가 권한을 준다
```

### 4-3. 실패가 아래로 번진 경로

```text
scrape 실패
  → 답할 데이터가 없다
  → /readyz 실패
  → Pod 가 Endpoints 에서 빠진다              ← 4단계 5번 글
  → Service 에 보낼 곳이 없다
  → APIService 가 MissingEndpoints 로 판정
  → kubectl top → Metrics API not available
```

```text
★ 에러는 맨 끝에서 나왔지만 원인은 맨 앞에 있다
  → 그래서 로그를 봐야 했다
```

```text
★ liveness 는 통과하고 readiness 만 실패했다

  STATUS Running / RESTARTS 0 / READY 0/1

  프로세스는 멀쩡하다 → 죽여봐야 인증서는 그대로다
  → 그래서 재시작하지 않고 트래픽만 끊는다
  → 만약 이걸 liveness 로 잡았다면 CrashLoopBackOff 로 갔을 것이다
     그리고 원인은 여전히 인증서였을 것이다
```

---

## 5. 수정 — `--kubelet-insecure-tls`

```text
[선택지 둘]

  A. metrics-server 쪽을 고친다      --kubelet-insecure-tls
                                     "kubelet 인증서를 검증하지 않겠다"
                                     암호화는 유지. 신원 확인만 생략

  B. kubelet 쪽을 고친다             serverTLSBootstrap: true + CSR 승인
                                     노드 셋의 설정 변경과 재시작이 필요
                                     → 1단계 영역의 작업

[A 를 골랐다]
  로컬 학습 클러스터. 노드 IP 가 고정이고 외부 접근이 불가능하다
  B 는 5단계 흐름을 끊는다. 12단계(운영 수준 보완)에서 다룰 주제다
```

```yaml
      - args:
        - --cert-dir=/tmp
        - --secure-port=10250
        - --kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname
        - --kubelet-use-node-status-port
        - --metric-resolution=15s
        # kubeadm 이 kubelet 서빙 인증서를 클러스터 CA 로 발급하지 않아
        # 인증서에 IP SAN 이 없다. metrics-server 는 IP 로 붙으므로 검증에 실패한다
        # 로컬 학습 클러스터라 검증을 끈다. 운영에서는 serverTLSBootstrap 을 쓴다
        - --kubelet-insecure-tls
```

```bash
kubectl apply -f metrics-server.yaml
kubectl rollout status deployment/metrics-server -n kube-system
```

```text
deployment "metrics-server" successfully rolled out
```

```text
★ 처음엔 kubectl patch 로 적용했다가 파일에도 반영했다
  patch 는 클러스터만 바꾼다
  → 파일에 없으면 다음 apply 때 되돌아가서 다시 깨진다
  → 4단계에서 반복해서 겪은 어긋남이다
```

---

## 6. 검증

```bash
kubectl get po -n kube-system -l k8s-app=metrics-server
kubectl get apiservice v1beta1.metrics.k8s.io
```

```text
NAME                              READY   STATUS    RESTARTS   AGE
metrics-server-67cbccccd9-g2ztj   1/1     Running   0          36s

NAME                     SERVICE                      AVAILABLE   AGE
v1beta1.metrics.k8s.io   kube-system/metrics-server   True        14m
```

```bash
kubectl top nodes
```

```text
NAME       CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
master01   152m         7%       2011Mi          53%
worker01   62m          3%       1246Mi          33%
worker02   48m          2%       1035Mi          27%
```

```bash
kubectl top pods -n bookstore --containers
```

```text
POD                      NAME       CPU(cores)   MEMORY(bytes)
api-7b45568cc4-l27fc     api        3m           48Mi
api-7b45568cc4-ncdxk     api        3m           47Mi
postgres-0               postgres   4m           53Mi
redis-659ff69cf4-6m4zr   redis      6m           12Mi
worker-d985f6ff4-qn8pz   worker     3m           47Mi
```

### 단위 — millicore

```text
  m = milli = 1/1000
  1000m = 1 코어(1 vCPU)

  3m  = 0.003 코어
      = 1초 중 3밀리초 동안 CPU 를 썼다는 뜻
```

```text
[노드 쪽 계산]
  worker01   2 vCPU = 2000m
             62m / 2000m = 3.1%   → 표의 CPU(%) 와 맞는다
```

```text
[왜 % 가 아니라 millicore 인가]
  노드마다 코어 수가 다르다
  → "50%" 는 어느 노드냐에 따라 다른 양이다
  → millicore 는 절대량이라 노드가 달라도 같은 뜻이다

[4단계에서 쓴 값과 같은 단위다]
  requests: cpu: 300m    0.3 코어를 확보해달라
  limits:   cpu: 500m    최대 0.5 코어

  → 그리고 그건 cgroup 의 cpu.max 에 이렇게 쓰인다
     50000 100000    100ms 주기 중 50ms
```

### metrics-server 가 주는 것과 안 주는 것

```text
[주는 것]
  노드      CPU, 메모리
  Pod       CPU, 메모리 (컨테이너 단위까지)

[안 주는 것]
  디스크 사용량, I/O
  네트워크 송수신량
  파일 디스크립터, 프로세스 수
  애플리케이션 지표 (요청 수, 응답 시간)

  → node-exporter 와 Prometheus 가 필요한 이유 (Phase 2)
```

---

## 7. 4단계의 빚 정산

```bash
kubectl exec -n bookstore deploy/api -- cat /sys/fs/cgroup/memory.current
kubectl top pod -n bookstore -l app.kubernetes.io/name=api --no-headers
```

```text
51494912                                     = 49.1 Mi
api-7b45568cc4-l27fc   3m    48Mi
```

```text
[두 숫자가 1Mi 다른 이유]

  memory.current   커널이 이 cgroup 에 물린 페이지 전부
                   → 재사용 가능한 페이지 캐시도 포함

  kubectl top      working set
                   → 그중 "지금 실제로 필요한 것" 만
                   → 회수 가능한 캐시를 뺀 값

  ★ OOM 판단은 working set 기준이다
    캐시가 많이 잡혀 있어도 그것 때문에 죽지는 않는다
    → 부족하면 커널이 캐시를 먼저 버린다
```

### 8번 글의 수수께끼가 풀렸다

```text
[4단계에서 관측한 것]
  limits: memory: 32Mi   →  기동 중 OOMKilled (2초). exit 137
  limits: memory: 48Mi   →  기동 성공, 요청 30번에 Pod 셋 전부 OOMKilled

[이제 아는 것]
  유휴 상태 사용량이 48Mi 다

  → limits 를 48Mi 로 준 건 여유를 0 으로 준 것이다
  → 겨우 뜨고, 뭔가 하는 순간 넘는다
  → 32Mi 는 뜨지도 못한다
```

```text
★ 그때는 "48Mi 로 뜬다" 만 알았다
  왜 아슬아슬한지는 몰랐다
  → 실사용량을 재고 나니 "여유 0" 이었다는 게 드러난다
```

### 그런데 부하를 줬는데 숫자가 안 움직였다

```bash
for i in $(seq 1 20); do
  kubectl exec -n bookstore deploy/api -- \
    python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/books?limit=1000').read()" 2>/dev/null
done
kubectl top pods -n bookstore
```

```text
요청 전  48Mi
요청 후  48Mi
```

```text
[세 가지가 겹쳤다]

  1. 부하가 너무 약하다
       kubectl exec 하나가 1초쯤 걸린다
       → 20번이 20초에 걸쳐 순차로 = 1 req/s

  2. 파이썬이 메모리를 돌려쓴다
       요청 하나 처리하고 해제한 걸 다음 요청에 재사용한다
       → 순차 요청이면 최고점이 안 올라간다

  3. ★ metrics-server 의 해상도가 15초다
       그 사이에 오르내린 건 안 보인다
```

---

## 8. 한계 — 그래서 Prometheus 가 필요하다

```text
[kubectl top 으로 알 수 있는 것]
  지금 이 순간 얼마 쓰는가

[알 수 없는 것]
  10분 전에는 얼마였나
  최고점이 얼마였나              ★ limits 를 정하려면 이게 필요하다
  언제 올랐고 언제 꺾였나
  15초 사이에 튀었다 내려온 것
```

```text
★ limits 를 제대로 정하려면
  "지금 48Mi" 가 아니라 "지난 일주일 최고점이 몇 Mi" 를 알아야 한다
  → kubectl top 은 그걸 답할 수 없다
```

```text
[Metrics Server 와 Prometheus 의 역할 분담]

               Metrics Server        Prometheus
  저장          메모리, 최근 값만      디스크, 시계열
  수집 대상      CPU / Memory 만       임의의 지표
  용도          kubectl top, HPA      대시보드, Alert, 커스텀 지표

  → 하나가 다른 하나를 대체하지 않는다
  → kubectl top 과 HPA 는 Prometheus 를 안 본다
```

---

## Phase 1 결과

```text
  APIService     v1beta1.metrics.k8s.io   AVAILABLE True     ✓
  kubectl top    nodes / pods 동작                            ✓
  파일 반영       metrics-server.yaml 에 --kubelet-insecure-tls  ✓
  실측           api 3m / 48Mi (유휴)                          ✓
  4단계 빚       48Mi limits 가 왜 아슬아슬했는지 규명           ✓
```

```text
[알고 넘어가는 것]
  kubelet 인증서 검증을 껐다
  → 운영이라면 serverTLSBootstrap: true 로 제대로 발급받아야 한다
  → 12단계에서 다시 볼 지점

  디스크, 네트워크, 앱 지표는 여기서 안 나온다
  → Phase 2 의 node-exporter 와 앱 /metrics 가 필요하다
```

## 확인 명령

```bash
kubectl top nodes
kubectl top pods -A --containers
kubectl get apiservice v1beta1.metrics.k8s.io

# 안 되면 순서대로
kubectl get po -n kube-system -l k8s-app=metrics-server        # READY 1/1 인가
kubectl logs -n kube-system -l k8s-app=metrics-server --tail=20 # scrape 에러가 있나
kubectl get endpointslice -n kube-system \
  -l kubernetes.io/service-name=metrics-server \
  -o jsonpath='{range .items[*].endpoints[*]}{.addresses[0]}{"  ready="}{.conditions.ready}{"\n"}{end}'

# 원본 값과 대조
kubectl exec -n <ns> deploy/<name> -- cat /sys/fs/cgroup/memory.current
kubectl exec -n <ns> deploy/<name> -- cat /sys/fs/cgroup/memory.max
kubectl exec -n <ns> deploy/<name> -- cat /sys/fs/cgroup/cpu.max
kubectl exec -n <ns> deploy/<name> -- cat /sys/fs/cgroup/cpu.stat
```

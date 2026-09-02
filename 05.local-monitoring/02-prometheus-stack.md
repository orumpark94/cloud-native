# Phase 2. kube-prometheus-stack

`05.local-monitoring/README.md` 의 **Phase 2** 작업 기록이다. 2026-09-01.

## 목적

Phase 1 이 남긴 넷을 푼다.

```text
  1. 최고점을 모른다          limits 를 정하려면 필요하다
  2. 15초 사이의 변화를 못 본다
  3. 히스토리가 없다
  4. CPU 와 메모리뿐이다       디스크, 네트워크, 오브젝트 상태가 없다
```

```text
  1~3  →  Prometheus (시계열 저장)
  4    →  node-exporter, kube-state-metrics
```

---

## 1. 차트 확인

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm search repo prometheus-community/kube-prometheus-stack
```

```text
NAME                                        CHART VERSION   APP VERSION
prometheus-community/kube-prometheus-stack  88.6.2          v0.93.1
```

```text
  CHART VERSION  88.6.2     차트 자체
  APP VERSION    v0.93.1    Prometheus Operator 버전
                            (Prometheus 본체는 v3.14.0-distroless)
```

```bash
helm show values prometheus-community/kube-prometheus-stack > values-default.yaml
wc -l values-default.yaml
grep -n "^[a-zA-Z]" values-default.yaml
```

```text
6012 values-default.yaml

 33:crds:               398:alertmanager:      2337:kubeEtcd:
174:defaultRules:      1389:grafana:           2451:kubeScheduler:
347:global:            1707:kubeApiServer:     2590:kubeProxy:
367:windowsMonitoring: 1791:kubelet:           2691:kubeStateMetrics:
                       2036:kubeControllerManager:  2696:kube-state-metrics:
                       2147:coreDns:           2723:nodeExporter:
                       2234:kubeDns:           2739:prometheus-node-exporter:
                                               2837:prometheusOperator:
                                               3626:prometheus:
                                               5327:thanosRuler:
```

```text
[하이픈이 붙은 것이 하위 차트다]
  grafana / kube-state-metrics / prometheus-node-exporter
  → 우산 차트(umbrella chart)
```

### 바꿀 항목 확인

```bash
grep -n "retention:" values-default.yaml           # 1133(am) 4591(prom) 5620(thanos)
grep -n "storageSpec\|storageClassName" values-default.yaml
grep -n "^  enabled:" values-default.yaml
```

```text
4591:    retention: 10d           기본 10일
4699:    storageSpec: {}          ★ 비면 emptyDir
4692:    resources: {}            ★ 비면 BestEffort
1480:  # adminPassword: …         주석 처리됨
```

### control-plane 지표 포트 사전 확인

```bash
sudo grep -n "bind-address" /etc/kubernetes/manifests/kube-controller-manager.yaml
sudo grep -n "bind-address" /etc/kubernetes/manifests/kube-scheduler.yaml
sudo grep -n "listen-metrics-urls" /etc/kubernetes/manifests/etcd.yaml
kubectl -n kube-system get cm kube-proxy -o yaml | grep metricsBindAddress
```

```text
16:    - --bind-address=127.0.0.1
15:    - --bind-address=127.0.0.1
24:    - --listen-metrics-urls=http://127.0.0.1:2381
54:    metricsBindAddress: ""            (기본 127.0.0.1:10249)
```

```text
[예상] 넷 다 Target DOWN 이 된다
      → 켠 채로 설치해서 직접 확인한 뒤 결정하기로 함
```

---

## 2. `values.yaml` 작성

`~/k8s/monitoring/values.yaml`

```yaml
alertmanager:
  enabled: false

grafana:
  enabled: true
  adminPassword: admin
  service:
    type: NodePort
    nodePort: 30300
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {memory: 256Mi}          # ← 나중에 768Mi 로 올림

prometheus:
  service:                            # ← 나중에 추가
    type: NodePort
    nodePort: 30090
  prometheusSpec:
    retention: 2d
    scrapeInterval: 30s
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: local-path
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 5Gi
    resources:
      requests: {cpu: 200m, memory: 512Mi}
      limits: {memory: 1500Mi}

prometheusOperator:
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {memory: 256Mi}
```

| 항목 | 기본값 | 우리 값 | 이유 |
|---|---|---|---|
| alertmanager | true | **false** | Phase 6 까지 미룸. 보낼 곳이 없음 |
| retention | 10d | **2d** | 디스크·메모리 절약 |
| scrapeInterval | `""` | **30s** | 명시해두면 나중에 조정하기 쉬움 |
| storageSpec | `{}` | **local-path 5Gi** | 비우면 emptyDir → 재시작 시 지표 소실 |
| resources | `{}` | **requests + mem limits** | 비우면 BestEffort → 먼저 쫓겨남 |
| grafana service | ClusterIP | **NodePort 30300** | 밖에서 봐야 함 |

---

## 3. 설치 전 검토

```bash
helm template kube-prom-stack prometheus-community/kube-prometheus-stack \
  -f values.yaml > rendered.yaml

wc -l rendered.yaml
grep "^kind:" rendered.yaml | sort | uniq -c | sort -rn
```

```text
6631 rendered.yaml

     35 kind: PrometheusRule
     31 kind: ConfigMap
     12 kind: ServiceMonitor
     10 kind: Service
      6 kind: ServiceAccount
      5 kind: ClusterRoleBinding
      5 kind: ClusterRole
      3 kind: Deployment
      2 kind: Job
      1 kind: ValidatingWebhookConfiguration
      1 kind: Secret
      1 kind: RoleBinding
      1 kind: Role
      1 kind: Prometheus                 ★ StatefulSet 이 아니다
      1 kind: MutatingWebhookConfiguration
      1 kind: DaemonSet
```

```bash
helm template ... --include-crds | grep -c "kind: CustomResourceDefinition"
```

```text
10
```

```text
[CRD 는 기본적으로 template 에 안 나온다]
  Helm 은 CRD 를 crds/ 에 따로 두고 install 때만 만든다
  upgrade 때는 건드리지 않는다
```

### 값 반영 확인

```bash
grep -n "retention:" rendered.yaml                    # 2194:  retention: "2d"
grep -n -B3 -A10 "storageClassName" rendered.yaml     # 2240:  storageClassName: local-path
grep -n "nodePort" rendered.yaml                      # 1294:  nodePort: 30300
grep -c "kind: Alertmanager" rendered.yaml            # 0
```

### `kind: Prometheus` 확인

```bash
grep -n -A25 "^kind: Prometheus$" rendered.yaml
```

```text
2159:kind: Prometheus
2161:  name: kube-prom-stack-kube-prome-prometheus
2175:  image: "quay.io/prometheus/prometheus:v3.14.0-distroless"
2180:  replicas: 1
2194:  retention: "2d"
2240:        storageClassName: local-path
```

```text
★ StatefulSet 이 아니라 Prometheus 라는 CRD 오브젝트다
  → 설치 후 Operator 가 이걸 보고 StatefulSet 을 만든다
```

---

## 4. 설치

```bash
helm install kube-prom-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace -f values.yaml
```

```text
NAME: kube-prom-stack
LAST DEPLOYED: Tue Sep  1 12:26:50 2026
STATUS: deployed
REVISION: 1
```

```bash
kubectl get all -n monitoring
```

```text
NAME                                                   READY   STATUS
pod/kube-prom-stack-grafana-…                          3/3     Running
pod/kube-prom-stack-kube-prome-operator-…              1/1     Running
pod/kube-prom-stack-kube-state-metrics-…               1/1     Running
pod/kube-prom-stack-prometheus-node-exporter-mcvp8     1/1     Running
pod/kube-prom-stack-prometheus-node-exporter-rc8l8     1/1     Running
pod/kube-prom-stack-prometheus-node-exporter-skx99     1/1     Running
pod/prometheus-kube-prom-stack-kube-prome-prometheus-0 2/2     Running

service/kube-prom-stack-grafana                  NodePort    80:30300/TCP
service/kube-prom-stack-kube-prome-operator      ClusterIP   443/TCP
service/kube-prom-stack-kube-prome-prometheus    ClusterIP   9090/TCP,8080/TCP
service/kube-prom-stack-kube-state-metrics       ClusterIP   8080/TCP
service/kube-prom-stack-prometheus-node-exporter ClusterIP   9100/TCP
service/prometheus-operated                      ClusterIP   None          ← Headless

daemonset.apps/kube-prom-stack-prometheus-node-exporter   3/3
deployment.apps/kube-prom-stack-grafana                   1/1
deployment.apps/kube-prom-stack-kube-prome-operator       1/1
deployment.apps/kube-prom-stack-kube-state-metrics        1/1
statefulset.apps/prometheus-kube-prom-stack-…-prometheus  1/1   ← Operator 가 만듦
```

### Operator 가 만든 것 확인

```bash
kubectl get prometheus,statefulset -n monitoring
grep -c "kind: StatefulSet" rendered.yaml       # 0
grep -c "prometheus-operated" rendered.yaml     # 0
```

```text
  helm template 에 없던 것이 설치 후에 있다
  → StatefulSet, prometheus-operated Service
  → Operator 가 Prometheus 오브젝트를 보고 만든 것
```

### PVC 자동 생성 확인

```bash
kubectl get pvc,pv -n monitoring
```

```text
persistentvolumeclaim/prometheus-…-prometheus-db-prometheus-…-prometheus-0
  Bound   pvc-8f07a878-…   5Gi   RWO   local-path

persistentvolume/pvc-8f07a878-…   5Gi   RWO   Delete   Bound   monitoring/…   local-path
```

```text
  Phase 0 에서 만든 local-path StorageClass 가 동작했다
  → WaitForFirstConsumer 라 Pod 스케줄 후에 PV 가 생성됨
```

---

## 5. 문제 1 — Grafana OOMKilled

```bash
kubectl get pods -n monitoring
```

```text
kube-prom-stack-grafana-b975c77f5-js8f4    2/3    OOMKilled    7 (102s ago)
```

```bash
kubectl describe po -n monitoring -l app.kubernetes.io/name=grafana | grep -A8 "Last State"
```

```text
    Last State:      Terminated
      Reason:        OOMKilled
      Exit Code:     137
      Started:       15:22:13
      Finished:      15:23:04
    Restart Count:   8
    Limits:
      memory:  256Mi
```

### 노드 자원부터 확인

```bash
kubectl top nodes
kubectl top pods -n monitoring --containers
```

```text
master01   125m  6%   1691Mi  44%
worker01    91m  4%   1718Mi  45%
worker02    69m  3%   1214Mi  32%
```

```text
  노드는 여유가 있다 → VM 증설이 아니라 limits 문제
  ★ Phase 1 에서 Metrics Server 를 깔아둔 게 여기서 값어치를 함
```

### 조정 이력

```text
  limits 256Mi  →  OOMKilled (restart 8)
  limits 512Mi  →  사용 488Mi   (95%)     여전히 빠듯
  limits 768Mi  →  사용 578Mi   (75%)
  (시간 경과)      사용 297Mi   (39%)
```

```text
★ 한도를 올리니 사용량도 따라 올랐다

  Go 의 GC 특성 (기본 GOGC=100, 힙이 두 배가 될 때까지 미룸)
  → "필요한 양" 이 아니라 "쓸 수 있으니 쓰는" 것
  → 시간이 지나 GC 가 돌자 297Mi 로 내려감
```

### 최종 값

```yaml
grafana:
  resources:
    requests: {cpu: 50m, memory: 256Mi}
    limits: {memory: 768Mi}
  sidecar:
    resources:
      requests: {cpu: 20m, memory: 96Mi}
      limits: {memory: 192Mi}
```

```text
[대안 — 적용하지 않음]
  env:
    - name: GOMEMLIMIT
      value: "700MiB"
  → Go 에게 한도를 알려 GC 를 더 자주 돌게 한다
```

---

## 6. 문제 2 — `port-forward` 가 끊김

Prometheus Service 가 ClusterIP 라 `port-forward` 로 접근했다.

```bash
kubectl port-forward -n monitoring \
  svc/kube-prom-stack-kube-prome-prometheus 9090:9090 --address 0.0.0.0
```

```text
  http://192.168.8.143:9090/alerts   → 됨
  잠시 후                             → ERR_CONNECTION_REFUSED
```

```text
[원인] kubectl 프로세스가 죽으면 터널도 사라진다

  port-forward   kubectl 이 연 포트 → apiserver → kubelet → Pod
                 그 노드에서만. kubectl 이 살아 있어야 함
  NodePort       kube-proxy 의 iptables. 모든 노드. kubectl 무관
```

### NodePort 로 전환

```yaml
prometheus:
  service:
    type: NodePort
    nodePort: 30090
```

```bash
helm upgrade kube-prom-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f values.yaml
kubectl get svc kube-prom-stack-kube-prome-prometheus -n monitoring
```

```text
NodePort   10.111.15.19   9090:30090/TCP,8080:31453/TCP
```

```text
  Pod 는 재시작되지 않음 (Service 만 바뀜)
```

```text
★ 운영이라면 열지 않는다
  Prometheus UI 는 인증이 없다. 클러스터 구조가 그대로 드러난다
  → 관리자가 port-forward 로 보는 게 표준
  → 여기서는 Phase 3~4 에서 Targets 를 계속 봐야 해서 연다
```

---

## 7. 문제 3 — Target DOWN 넷

```bash
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "
import sys, json, collections
d = json.load(sys.stdin)['data']['activeTargets']
c = collections.Counter()
for t in d: c[(t['labels'].get('job','?'), t['health'])] += 1
for (job, health), n in sorted(c.items()): print(f'{health:6} {n:3}  {job}')
"
```

```text
up       1  apiserver
up       2  coredns
down     1  kube-controller-manager
down     1  kube-etcd
up       1  kube-prom-stack-grafana
up       1  kube-prom-stack-kube-prome-operator
up       2  kube-prom-stack-kube-prome-prometheus
down     3  kube-proxy
down     1  kube-scheduler
up       1  kube-state-metrics
up       9  kubelet
up       3  node-exporter
```

```bash
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    if t['health'] != 'up':
        print(t['labels'].get('job'), t['scrapeUrl']); print('   ', t['lastError'][:110])
"
```

```text
kube-controller-manager https://192.168.8.143:10257/metrics
    dial tcp 192.168.8.143:10257: connect: connection refused
kube-etcd http://192.168.8.143:2381/metrics
    dial tcp 192.168.8.143:2381: connect: connection refused
kube-proxy http://192.168.8.141:10249/metrics
    dial tcp 192.168.8.141:10249: connect: connection refused
kube-proxy http://192.168.8.142:10249/metrics   (동일)
kube-proxy http://192.168.8.143:10249/metrics   (동일)
kube-scheduler https://192.168.8.143:10259/metrics
    dial tcp 192.168.8.143:10259: connect: connection refused
```

```text
[connection refused]
  패킷은 도착했는데 받는 게 없다 → 그 포트가 그 인터페이스에 안 열려 있다
  (timeout 이면 방화벽/라우팅 문제)
  → 사전 확인한 127.0.0.1 바인딩이 원인으로 확정
```

### 판단 — 인증 유무로 갈랐다

| 컴포넌트 | 포트 | 프로토콜 | 인증 | 조치 |
|---|---|---|---|---|
| kube-controller-manager | 10257 | HTTPS | 있음 | **고침** |
| kube-scheduler | 10259 | HTTPS | 있음 | **고침** |
| kube-proxy | 10249 | HTTP | 없음 | **감시 끔** |
| etcd | 2381 | HTTP | 없음 | **감시 끔** |

```text
  HTTPS + 인증이면 0.0.0.0 으로 열어도 토큰 없이는 못 읽는다
  평문 + 인증 없음이면 노드 네트워크의 누구나 읽는다
  → etcd 는 가장 민감한 컴포넌트라 배울 것 대비 위험이 큼
```

### static Pod 수정

```bash
sudo cp /etc/kubernetes/manifests/kube-controller-manager.yaml /root/kcm.bak
sudo cp /etc/kubernetes/manifests/kube-scheduler.yaml /root/ks.bak

sudo sed -i 's/--bind-address=127.0.0.1/--bind-address=0.0.0.0/' \
  /etc/kubernetes/manifests/kube-controller-manager.yaml
sudo sed -i 's/--bind-address=127.0.0.1/--bind-address=0.0.0.0/' \
  /etc/kubernetes/manifests/kube-scheduler.yaml
```

```text
  kubelet 이 /etc/kubernetes/manifests/ 를 지켜보고 있다
  → 파일이 바뀌면 그 Pod 를 자동으로 다시 만든다
  → kubectl apply 를 하지 않는다. 스케줄러도 안 거친다
```

```bash
sudo grep -n "bind-address" /etc/kubernetes/manifests/kube-controller-manager.yaml
# 16:    - --bind-address=0.0.0.0
kubectl get po -n kube-system | grep -E "controller-manager|scheduler"
```

### etcd, kube-proxy 감시 끔

```yaml
kubeEtcd:
  enabled: false
kubeProxy:
  enabled: false
```

```bash
helm upgrade kube-prom-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f values.yaml
kubectl get servicemonitor -n monitoring | grep -E "proxy|etcd"    # 없음
```

---

## 8. ★ 문제 4 — kube-proxy ConfigMap 파괴

### 무슨 일이 있었나

`metricsBindAddress` 를 patch 로 바꾸는 방법을 검토하다 아래 명령이 실행됐다.

```bash
kubectl -n kube-system patch cm kube-proxy --type merge -p \
  "$(python3 - <<'EOF'
import json
print(json.dumps({"data":{"config.conf":"__PLACEHOLDER__"}}))
EOF
)" 2>/dev/null || echo "아래 edit 방식을 쓰십시오"
```

```text
  config.conf 전체가 "__PLACEHOLDER__" 로 덮어써졌다
  2>/dev/null 이 출력을 가려 실행 결과가 안 보였다
```

### 증상

```bash
kubectl -n kube-system get cm kube-proxy -o yaml | grep -n "metricsBindAddress"
```

```text
(아무것도 안 나옴)      ← 이전에는 54번 줄에 있었다
```

```bash
kubectl get po -n kube-system | grep kube-proxy
```

```text
kube-proxy-6284z   1/1   Running   0   18d      ← 멀쩡하다
```

```text
★ 기존 Pod 는 영향이 없다

  kube-proxy 는 시작할 때 설정을 한 번 읽고 메모리에 들고 있다
  → ConfigMap 을 바꿔도 이미 뜬 Pod 는 모른다
  → 재시작되는 순간 터진다

  ★ 깨뜨린 시점과 터지는 시점이 다르다
    노드 재부팅, Pod 삭제, DaemonSet 갱신 → 그때 발현
```

### 복구

```bash
kubectl -n kube-system get cm kubeadm-config -o yaml | head -40
```

```text
  podSubnet: 10.244.0.0/16
  serviceSubnet: 10.96.0.0/12
```

```bash
sudo kubeadm init phase addon kube-proxy
```

```text
I0901 15:51:07 version.go:260] remote version is much newer: v1.37.0; falling back to: stable-1.35
[addons] Applied essential addon: kube-proxy
```

```bash
kubectl -n kube-system get cm kube-proxy -o yaml | grep -n "clusterCIDR\|metricsBindAddress\|mode:"
```

```text
13:    clusterCIDR: ""
54:    metricsBindAddress: ""
55:    mode: ""
```

```bash
kubectl -n kube-system rollout restart ds kube-proxy
kubectl -n kube-system rollout status ds kube-proxy
kubectl get po -n kube-system | grep kube-proxy
```

```text
daemon set "kube-proxy" successfully rolled out
kube-proxy-2hq8w   1/1   Running   0   5s
kube-proxy-2td9s   1/1   Running   0   3s
kube-proxy-7wq8h   1/1   Running   0   1s
```

### 검증 — 네트워크라 셋으로 확인

```bash
# 1. 로그
kubectl logs -n kube-system -l k8s-app=kube-proxy --tail=20 | grep -i "error\|fail\|invalid"
```

```text
(에러 없음)
  "Caches are synced" controller="service config"
  "Caches are synced" controller="endpoint slice config"
  "Caches are synced" controller="serviceCIDR config"
  "Caches are synced" controller="node config"
```

```bash
# 2. iptables
kubectl -n kube-system exec ds/kube-proxy -- iptables -t nat -L KUBE-SERVICES -n | head
kubectl -n kube-system exec ds/kube-proxy -- iptables -t nat -L KUBE-NODEPORTS -n | grep -E "30300|30090|30800"
```

```text
  Service 규칙 전부 존재
  NodePort 30090 / 30300 / 30800 존재
```

```bash
# 3. 실제 통신
kubectl exec -n bookstore deploy/api -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:9000/health/deps').read().decode())"
```

```json
{"dependencies":{
   "postgres":{"up":true,"latency_ms":3.02},
   "redis":{"up":true,"latency_ms":0.45}}}
```

```text
★ 앱이 두 ClusterIP 로 실제 연결에 성공 → 라우팅 정상
```

```text
[곁가지 — 실패처럼 보이는 성공]
  redis 에 HTTP 요청을 보내면
    http.client.RemoteDisconnected: Remote end closed connection
  → Redis 는 RESP 프로토콜이라 HTTP 요청에 연결을 끊는다
  → 끊으려면 일단 연결이 됐어야 하므로 경로는 정상이라는 뜻
```

### 미해결

```text
  clusterCIDR: ""
  → 사고 전 원본을 못 봤으므로 원래 값이 무엇이었는지 확인 불가
  → 로그에 serviceCIDR config controller 가 정상 동작 중이고
    통신이 전부 되므로 실용적으로는 정상으로 판단
```

### 재발 방지

```bash
mkdir -p ~/k8s/backup
kubectl -n kube-system get cm kube-proxy -o yaml > ~/k8s/backup/kube-proxy-cm.yaml
kubectl -n kube-system get ds kube-proxy -o yaml > ~/k8s/backup/kube-proxy-ds.yaml
kubectl -n kube-system get cm kubeadm-config -o yaml > ~/k8s/backup/kubeadm-config.yaml
sudo cp /etc/kubernetes/manifests/*.yaml ~/k8s/backup/
```

```text
  1. kube-system 오브젝트를 건드리기 전에 백업
  2. patch 보다 edit (저장할 때 문법 검사를 한다)
  3. 2>/dev/null 을 습관적으로 쓰지 않는다 (실패를 못 본다)
  4. 설명용 예시를 실행 가능한 형태로 쓰지 않는다
```

---

## 9. 최종 상태

```bash
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "…"
```

```text
up       1  apiserver
up       2  coredns
up       1  kube-controller-manager
up       1  kube-prom-stack-grafana
up       1  kube-prom-stack-kube-prome-operator
up       2  kube-prom-stack-kube-prome-prometheus
up       1  kube-scheduler
up       1  kube-state-metrics
up       9  kubelet
up       3  node-exporter
---- 합계 22.  DOWN 0
```

```text
[kubelet 9]      노드 3 × 엔드포인트 3 (/metrics, /metrics/cadvisor, /metrics/probes)
[prometheus 2]   9090(본체) + 8080(config-reloader)
```

```bash
curl -s http://192.168.8.143:30090/api/v1/alerts | python3 -c "…"
```

```text
firing Watchdog
firing PrometheusNotConnectedToAlertmanagers
```

```text
[Watchdog]  expr: vector(1). 항상 참. severity: none
            죽은 자 스위치. 이 알람이 안 오면 알람 체계가 죽은 것
            → 사라지면 안 되는 알람이다

[NotConnectedToAlertmanagers]
            Alertmanager 를 껐으므로 당연. Phase 6 에서 해소
```

```bash
kubectl top pods -n monitoring --containers
kubectl top nodes
```

```text
grafana                  17m   297Mi     limits 768Mi
grafana-sc-dashboard      1m    72Mi
grafana-sc-datasources    1m    72Mi
kube-prometheus-stack     4m    22Mi     Operator
kube-state-metrics        2m    17Mi
node-exporter × 3         2m   8~10Mi
config-reloader           0m    10Mi
prometheus               30m   336Mi     limits 1500Mi

master01   101m  5%   1869Mi  49%
worker01    96m  4%   1585Mi  42%
worker02    59m  2%   1590Mi  42%
```

```bash
helm history kube-prom-stack -n monitoring
```

```text
REVISION  UPDATED    DESCRIPTION
1         12:26:50   Install complete
2         15:26:03   Upgrade complete    Grafana limits 256 → 512
3         15:31:28   Upgrade complete    Grafana limits 512 → 768
4         15:40:43   Upgrade complete    Prometheus NodePort 30090
5         15:48:57   Upgrade complete    kubeEtcd 끔
6         15:58:19   Upgrade complete    kubeProxy 끔
```

---

## Phase 2 결과

```text
  설치           kube-prometheus-stack 88.6.2 / Prometheus v3.14.0
  구성           Prometheus, Grafana, Operator, kube-state-metrics,
                 node-exporter × 3
  저장소         local-path 5Gi. PV 자동 생성
  노출           Grafana 30300 / Prometheus 30090
  Target         22개 전부 UP
  자원           노드 42~49%
```

## 남은 것 / 알고 넘어가는 것

```text
  Alertmanager 를 껐다                     Phase 6 에서 켠다
  Prometheus UI 를 인증 없이 열어뒀다        운영에서는 하면 안 된다
  kube-proxy / etcd 지표를 안 본다
  clusterCIDR 원본값 확인 불가              백업이 없었다
  retention 2일이 다 찼을 때의 메모리 미측정
```

## 확인 명령

```bash
# 상태
kubectl get all -n monitoring
kubectl get prometheus,servicemonitor,prometheusrule -n monitoring
helm history kube-prom-stack -n monitoring
helm get values kube-prom-stack -n monitoring

# Operator 가 만든 설정
kubectl get secret prometheus-kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{.data.prometheus\.yaml\.gz}' | base64 -d | gunzip | head -40

# Target / Alert
curl -s http://192.168.8.143:30090/api/v1/targets
curl -s http://192.168.8.143:30090/api/v1/alerts
curl -s http://192.168.8.143:30090/api/v1/rules

# UI
http://192.168.8.143:30090        Prometheus
http://192.168.8.143:30300        Grafana (admin / admin)

# 자원
kubectl top pods -n monitoring --containers
kubectl top nodes
```

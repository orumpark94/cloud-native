# Phase 3. 우리 앱 지표 연결 — PodMonitor

작업일: 2026-09-02

## 목적

Phase 2 를 끝냈을 때 Target 은 22개였다. **전부 인프라였다.**

```text
kubelet 9  node-exporter 3  kube-state-metrics 1
apiserver 1  coredns 2  scheduler 1  controller-manager 1
grafana 1  operator 1  prometheus 2
```

노드 CPU 는 보이는데 "사용자가 겪는 것" 은 안 보인다.

```text
[지금 못 보는 것]
  API 요청이 초당 몇 건인가
  응답이 몇 초 걸리는가
  500 이 늘고 있는가
  DB 커넥션 풀이 포화됐는가
  큐가 밀리고 있는가
```

이 Phase 는 그 목록에 우리 앱을 더한다. 로드맵 원칙 2("장애 실험 전에 관측 환경을 먼저 구성한다")의 마지막 조각이다.

## 1. 앱 상태 확인 — 코드 작업이 필요 없었다

시작하면서 앱을 먼저 봤다. 3단계에서 이미 다 만들어져 있었다.

```bash
grep -n "prometheus" Books-app/requirements.txt
grep -n "metrics" Books-app/app/main.py | head
wc -l Books-app/app/metrics.py
```

```text
requirements.txt   prometheus-client>=0.20,<1.0
app/metrics.py     436줄. 지표 정의 전부
main.py:212        @app.get("/metrics")  — 관리 앱(9000)에 등록
```

클러스터에도 이미 떠 있었다.

```bash
kubectl get deploy api worker -n bookstore \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[0].ports}{"\n"}{end}'
kubectl get svc api -n bookstore -o jsonpath='{.spec.ports}'; echo
```

```text
api      [{"containerPort":8000,"name":"http"},{"containerPort":9000,"name":"admin"}]
worker   [{"containerPort":9000,"name":"admin"}]
Service  [{"name":"http","port":8000,"targetPort":"http"}]     ← 9000 이 없다
worker   Service 자체가 없다
```

```text
★ Phase 3 은 "앱을 계측한다" 가 아니다
  "이미 내고 있는 걸 Prometheus 에게 알려준다" 뿐이다
```

## 2. ServiceMonitor 를 못 쓰는 이유

```text
ServiceMonitor 는 Service 를 골라 그 Endpoints 를 긁는다
  → Endpoints 에는 Service 에 선언된 포트만 적힌다
  → api Service 에는 8000 뿐이다
```

9000 을 Service 에 넣을 수는 없다. 4단계에서 정한 것이다.

```text
07-api.yaml 27번 줄
  ★★ 관리 포트(9000)를 여기 넣지 않는다
     9000 에는 /metrics, /health/*, /debug/* 가 있다
     특히 /debug/inject/* 는 서비스를 마음대로 망가뜨릴 수 있다
```

worker 는 Service 가 아예 없다. ServiceMonitor 로는 시작조차 못 한다.

```text
→ PodMonitor 를 쓴다
  Pod 를 라벨로 직접 고르고, containerPort 를 긁는다
  Service 를 안 거치므로 9000 을 노출하지 않은 채로 긁힌다
```

앱 코드에 이미 그렇게 적혀 있었다.

```text
main.py:46-48
  Service 에 9000 을 안 넣으면 클러스터 밖에서 못 닿는다
  kubelet 은 Pod IP 로 직접 부르므로 probe 는 정상 동작한다
  Prometheus 도 Pod IP 로 긁으므로 문제없다        ← 이거였다
```

### 대안 검토 — 셋 중에 골랐다

```text
(가) PodMonitor 로 9000 을 긁는다              ← 택함
     앱 안 바꿈. 9000 은 밖에서 못 닿는 채로 유지. worker 도 됨

(나) ServiceMonitor + targetPort: 9000
     role: endpoints 는 "Endpoints 포트에 안 묶인 컨테이너 포트" 도 발견한다
     → 되긴 하지만 의도가 안 드러나고 worker 는 못 한다
     → 실제로 되는지는 확인 안 함

(다) 8000 에 /metrics 를 연다
     가장 단순하다. 대신 NodePort/Ingress 로 지표가 공개된다
     경로 목록, 에러율, DB 풀 구조, 버전이 밖에서 보인다
```

### 앱 로직으로 출발지를 막는 방법도 검토했다

```text
"8000 에 열되 앱에서 Prometheus 만 허용하면 되지 않나"
→ 우리 구조에서는 동작하지 않는다
```

```bash
grep -n "externalTrafficPolicy" k8s/09-api-nodeport.yaml
# 69:  # externalTrafficPolicy 를 기본값(Cluster)으로 둔다
```

```text
Cluster 모드 → KUBE-MARK-MASQ → SNAT
  Prometheus       10.244.x.x 그대로 도착
  Windows PC       노드 IP 로 바뀌어 도착
  Ingress 경유     ingress controller Pod IP(10.244.x.x)로 도착   ★

→ Prometheus 를 허용하려면 10.244.0.0/16 을 열어야 한다
→ 그러면 Ingress 를 타고 온 외부 요청도 통과한다
→ 막으려고 만든 규칙이 조용히 열린다
```

```text
[구조적 방어와 조건적 방어]
  포트 분리    밖에서 닿을 경로가 없다. 실수하면 "안 긁힌다" 로 드러난다
  앱 로직      조건이 틀리면 열린다. 실수해도 "잘 된다" 로 보인다
  → 실패가 보이는 쪽을 고른다
```

## 3. selector 값 확인

YAML 을 쓰기 전에 세 값을 확인했다. 이 셋이 YAML 의 세 자리를 정한다.

```bash
kubectl get prometheus kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{"pmSelector : "}{.spec.podMonitorSelector}{"\n"}{"pmNsSel   : "}{.spec.podMonitorNamespaceSelector}{"\n"}'

kubectl get pod -n bookstore -l app.kubernetes.io/name=api \
  -o jsonpath='{.items[0].metadata.labels}'; echo

kubectl get deploy api -n bookstore \
  -o jsonpath='{.spec.template.spec.containers[0].ports}'; echo
```

```text
pmSelector : {"matchLabels":{"release":"kube-prom-stack"}}
pmNsSel   : {}
{"app.kubernetes.io/name":"api","app.kubernetes.io/part-of":"bookstore","pod-template-hash":"7b45568cc4"}
[{"containerPort":8000,"name":"http","protocol":"TCP"},{"containerPort":9000,"name":"admin","protocol":"TCP"}]
```

```text
pmSelector    → PodMonitor 에 release: kube-prom-stack 이 없으면 무시된다
pmNsSel {}    → 모든 네임스페이스를 본다 → PodMonitor 를 bookstore 에 둬도 된다
Pod 라벨       → selector 는 이 중에서 고른다. pod-template-hash 는 쓰면 안 된다
포트 이름      → port: admin (번호가 아니다)
```

## 4. PodMonitor 작성

### 첫 시도 — 스키마 검증에서 걸린 것 셋

```yaml
# ✗ 처음 쓴 것
metadata:
  name: PodMonitor              # 대문자 → RFC 1123 위반
spec:
  selector:
    matchLabels:
      - app.kubernetes.io/part-of: bookstore     # 리스트. 맵이어야 한다
  namespaceSelector:
    matchNames: bookstore                        # 배열이어야 한다
  PodMonitor:                                    # 그런 필드가 없다
    - name: admin                                # port 여야 한다
```

```text
matchLabels 는 "라벨이름 → 값" 의 맵이다. 하이픈이 없다
matchNames 는 배열이다. 네임스페이스를 여러 개 지정할 수 있어서다
podMetricsEndpoints[].port 는 컨테이너 포트의 "이름" 이다
metadata.name 은 소문자 영숫자, '-', '.' 만 쓸 수 있다
```

`kubectl explain podmonitor.spec` 으로 필드 목록을 확인해 고쳤다. CRD 에 openAPIV3Schema 가 들어 있어서 explain 이 동작한다.

### 최종 — `k8s/10-app-podmonitor.yaml`

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: podmonitor-app
  namespace: bookstore
  labels:
    release: kube-prom-stack            # ★ 이게 없으면 조용히 무시된다
    monitor.kubernetes.io/name: podmonitor-app
    monitor.kubernetes.io/part-of: bookstore
spec:
  selector:
    matchLabels:
      app.kubernetes.io/part-of: bookstore
  namespaceSelector:
    matchNames:
      - bookstore
  podMetricsEndpoints:
    - port: admin
      path: /metrics
      interval: 30s
```

```text
[part-of 로 고른 이유 — api 와 worker 를 한 장으로]
  api        admin 있음  → target 생성
  worker     admin 있음  → target 생성
  postgres   admin 없음  → 아무 일 없음
  redis      admin 없음  → 아무 일 없음

  ★ 대가 — postgres 에 admin 포트가 생기면 손 안 대도 긁히기 시작한다
```

```text
[라벨이 세 층이다]
  metadata.labels           위를 향한다. Prometheus 가 나를 고르는 조건
  spec.selector             아래를 향한다. 내가 Pod 를 고르는 조건
  namespaceSelector         어느 네임스페이스에서 찾을지

  → 1층과 2층을 바꿔 쓰면 조용히 아무것도 안 긁힌다
```

## 5. 적용 — 재시작 없이 반영됐다

기준선을 먼저 쟀다. 안 재면 "재시작 없이 됐다" 를 증명할 수 없다.

```bash
curl -s http://192.168.8.143:30090/api/v1/targets \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']['activeTargets']))"
kubectl get pod -n monitoring prometheus-kube-prom-stack-kube-prome-prometheus-0
```

```text
targets: 22
prometheus-…-0   2/2   Running   0   27h
```

```bash
kubectl apply -f 10-app-podmonitor.yaml
kubectl get podmonitor -n bookstore --show-labels
```

```text
podmonitor.monitoring.coreos.com/podmonitor-app created

NAME             AGE   LABELS
podmonitor-app   5s    monitor.kubernetes.io/name=podmonitor-app,
                       monitor.kubernetes.io/part-of=bookstore,release=kube-prom-stack
```

```bash
kubectl get secret prometheus-kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{.data.prometheus\.yaml\.gz}' | base64 -d | gunzip | grep -n podMonitor
```

```text
1293:- job_name: podMonitor/bookstore/podmonitor-app/0
```

**우리가 쓴 적 없는 줄이다.** Operator 가 PodMonitor 를 읽고 생성한 것이다.

```bash
kubectl get pod -n monitoring prometheus-kube-prom-stack-kube-prome-prometheus-0
```

```text
prometheus-…-0   2/2   Running   0   27h        ★ RESTARTS 0, AGE 그대로
```

```text
★ 선언 한 장으로 수집 대상이 늘었는데 Prometheus 는 안 죽었다
  config-reloader 가 파일 변경을 감지 → POST /-/reload
```

### reload 시각 확인

```bash
curl -s http://192.168.8.143:30090/api/v1/status/config \
  | python3 -c "import sys,json; print('podMonitor' in json.load(sys.stdin)['data']['yaml'])"

curl -s http://192.168.8.143:30090/api/v1/query \
  --data-urlencode 'query=prometheus_config_last_reload_success_timestamp_seconds' | ...
date
```

```text
True
마지막 reload: 2026-09-02 15:38:14
현재:          2026-09-02 15:41:20
```

```text
[예상이 빗나간 지점]
  Secret 전파에 kubelet 동기화 주기(60초)가 걸릴 거라고 봤다
  → 실제로는 즉시였다
  → config-reloader 가 폴링이 아니라 파일 감시(watch)를 한다
     로그: "started watching config file and directories for changes"
```

### 생성된 scrape_config

```text
- job_name: podMonitor/bookstore/podmonitor-app/0
  honor_labels: false
  kubernetes_sd_configs:
  - role: pod                        ★ endpoints 가 아니다
    namespaces:
      names: [bookstore]
  scrape_interval: 30s
  metrics_path: /metrics
  relabel_configs:
  - action: drop
    source_labels: [__meta_kubernetes_pod_phase]
    regex: (Failed|Succeeded)
  - action: keep
    source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_part_of,
                    __meta_kubernetes_pod_labelpresent_app_kubernetes_io_part_of]
    regex: (bookstore);true
  - action: keep
    source_labels: [__meta_kubernetes_pod_container_port_name]
    regex: admin
  ...
```

```text
ServiceMonitor 번역과 한 글자만 다르다
  ServiceMonitor → role: endpoints
  PodMonitor     → role: pod
```

## 6. 문제 1 — Target 3개가 전부 DOWN

```bash
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "..."
```

```text
전체: 25                                          ★ 22 → 25. 발견은 성공

down http://10.244.5.50:9000/metrics    api-…-ncdxk   data does not end with # EOF
down http://10.244.30.101:9000/metrics  worker-…-qn8pz data does not end with # EOF
down http://10.244.30.100:9000/metrics  api-…-l27fc   data does not end with # EOF
```

```bash
curl -s 'http://192.168.8.143:30090/api/v1/targets?state=dropped' | python3 -c "..."
```

```text
dropped: 271
api-…-ncdxk   http           ← 같은 Pod 의 다른 포트. admin 이 아니라 탈락
postgres-0    postgres       ← admin 포트 없음
redis-…       redis          ← admin 포트 없음
```

```text
★ selector, 포트 이름, 네임스페이스는 전부 맞았다
  연결도 됐다 (connection refused 가 아니다)
  받은 내용을 파싱하다 실패했다
```

### 원인 — Content-Type 이름표가 본문과 달랐다

```python
# app/metrics.py (수정 전)
from prometheus_client import generate_latest                          # 평문 본문
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST  # OpenMetrics 이름표

def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
```

```text
본문은 평문인데 "나는 OpenMetrics 다" 라고 알려주고 있었다
→ Prometheus 가 헤더를 믿고 OpenMetrics 파서를 켰다
→ OpenMetrics 규격은 본문이 반드시 "# EOF" 로 끝나야 한다 (잘린 응답 구별용)
→ 평문에는 그런 줄이 없다 → 파싱 실패
```

```text
★ 사람이 curl 로 볼 때는 안 드러난다. Content-Type 을 안 보니까
  Prometheus 를 처음 붙인 지금에서야 드러났다
  → 3단계에서 만들어놓고 6일 동안 아무도 안 긁고 있었다
```

### 수정

```python
# app/metrics.py (수정 후)
from prometheus_client import (
    CONTENT_TYPE_LATEST,        # ★ 본문과 같은 곳에서 가져온다
    CollectorRegistry, Counter, Gauge, Histogram, Info,
    generate_latest,
)
```

```text
[OpenMetrics 로 통일하는 선택지도 있었다]
  exemplar(트레이스 ID 연결)를 쓸 수 있다
  → 지금은 그 기능이 필요 없어서 평문으로 통일했다
```

## 7. 문제 2 — build01 의 도커 브리지 네트워크 고장

배포 전에 build01 에서 확인하려다 두 가지에 막혔다. **둘 다 같은 원인이었다.**

### 증상 A — 빌드가 안 됨

```text
pip install → Temporary failure in name resolution: /simple/fastapi/
```

```text
apt-get 은 통과한 것처럼 보였지만 착시였다
  Ign:1 http://deb.debian.org/debian trixie InRelease   ← 못 가져왔다는 뜻
  apt-get update 는 실패해도 종료 코드 0 을 낸다
  ca-certificates 는 python:3.12-slim 에 이미 있어서 네트워크를 안 썼다
→ 처음부터 DNS 가 안 됐다
```

### 증상 B — 컨테이너가 응답을 안 함

```bash
docker run -d --rm --name mtest -p 9000:9000 ... bookstore:20260902-0656
curl -v --max-time 5 http://127.0.0.1:9000/health/live
```

```text
* Connected to 127.0.0.1 (127.0.0.1) port 9000
* Operation timed out after 5003 milliseconds with 0 bytes received
```

```text
docker stats   CPU 0.36%, MEM 44MB      → 뭔가 돌면서 막는 게 아니다
옛날 이미지(0839)도 동일                 → 우리 수정과 무관
```

### 판정 — `--network=host` 로 갈랐다

```bash
docker run -d --name mtest --network=host ... bookstore:20260902-0713
curl -v --max-time 5 http://127.0.0.1:9000/health/live
```

```text
< HTTP/1.1 200 OK
{"status":"ok"}
```

```text
★ 같은 이미지, 같은 명령, 네트워크만 다르다
  → 도커 브리지가 망가진 게 확정
  → 앱 문제도, DB 없어서도 아니다

  -p 9000:9000 이 왜 "Connected" 는 되고 응답은 없나
    docker-proxy 가 호스트의 9000 에서 받아준다  ← curl 은 여기 붙는다
    받은 걸 컨테이너로 넘기는 구간이 끊겨 있다
```

```bash
sysctl net.ipv4.ip_forward          # = 1  (정상. 1순위 가설이 아니었다)
sudo iptables -L FORWARD -n | head -3
```

```text
Chain FORWARD (policy DROP)
DOCKER-USER  0 -- 0.0.0.0/0  0.0.0.0/0
```

```text
[미해결]
  브리지가 끊긴 근본 원인을 못 찾았다
  build01 을 중지했다 시작한 것 외에 바꾼 게 없다고 함
  → 재부팅 전후 iptables/docker0 비교가 필요하다
  → 8단계 CI 에서 러너 컨테이너로 같은 문제를 만날 수 있다
```

```text
[우회]
  docker build --network=host
  docker run   --network=host
  → 이미지 전송(docker save | ssh)은 브리지와 무관해서 정상 동작했다
```

## 8. 문제 3 — 수정이 build01 로 안 갔다

첫 빌드(20260902-0656)를 확인하니 Content-Type 이 그대로였다.

```text
content-type: application/openmetrics-text; version=1.0.0; charset=utf-8
```

```text
고친 파일    d:\SJPARK\cloud-native\Books-app\app\metrics.py     Windows 저장소
빌드한 곳    sjpark@build01:~/Books-app                          build01 의 사본
→ 두 곳이 다른 파일이다
```

동기화 후 재빌드했다.

```bash
grep -n "CONTENT_TYPE_LATEST" ~/Books-app/app/metrics.py
docker build --network=host -t bookstore:$(date +%Y%m%d-%H%M) .
```

```text
60:    CONTENT_TYPE_LATEST,           ← prometheus_client 에서

=> CACHED [builder 4/4] RUN pip install …          재사용
=> [stage-1 6/6] COPY --chown=… ./app /srv/app     ★ 이것만 다시 실행
=> naming to docker.io/library/bookstore:20260902-0713            2.0초
```

```text
★ requirements.txt 를 코드보다 먼저 COPY 한 설계 덕에 2초에 끝났다
```

### 확인

```bash
docker run -d --name mtest --network=host \
  -e APP_COMPONENT=api -e ADMIN_PORT=9000 \
  -e DATABASE_URL='postgresql://u:p@127.0.0.1:5432/db' \
  -e REDIS_URL='redis://127.0.0.1:6379/0' \
  bookstore:20260902-0713
sleep 45
curl -sD- -o /dev/null --max-time 5 http://127.0.0.1:9000/metrics | grep -i content-type
```

```text
content-type: text/plain; version=1.0.0; charset=utf-8        ★ 고쳐졌다
```

```text
[예상과 다른 부분]
  version=0.0.4 를 예상했는데 1.0.0 이 나왔다
  prometheus_client 최신 버전이 평문 형식 버전을 1.0.0 으로 올렸다
  → 중요한 건 앞부분. text/plain 이면 # EOF 를 안 찾는다
```

```text
[DB 없이도 200 을 냈다]
  deps.startup() 이 예외를 안 던지게 만들어둔 4단계 설계가 그대로 동작했다
  "PostgreSQL 연결 실패. readiness 가 실패 상태로 남는다"
  "API 기동"
  → readiness 만 false. /health/live 와 /metrics 는 응답한다
```

## 9. 배포

```bash
./scripts/push-image.sh          # 20260902-0713 선택. worker01/02 로 전송

cd /home/sjpark/k8s
grep -n "image: bookstore" 07-api.yaml 08-worker.yaml
# 07-api.yaml:119, 08-worker.yaml:91  → 20260902-0713 으로 수정

kubectl apply -f 07-api.yaml -f 08-worker.yaml
kubectl rollout status deploy/api -n bookstore
kubectl rollout status deploy/worker -n bookstore
```

```text
deployment "api" successfully rolled out
deployment "worker" successfully rolled out
```

### 롤아웃 중 Target 상태

```text
down api-7b45568cc4-ncdxk    data does not end with # EOF      ← 옛 Pod
down api-7b45568cc4-l27fc    data does not end with # EOF      ← 옛 Pod
unknown api-86d9679588-rkmfb                                    ← 새 Pod. 아직 안 긁음
up      worker-5d66f84488-sjk44                                 ← 새 Pod
unknown api-86d9679588-k2kb5
```

```text
★ 에러 메시지가 바뀐 것이 증거다
  옛 Pod   # EOF 에러
  새 Pod   에러 없음
  잠시 뒤 옛 Pod 는 "dial tcp … " 로 바뀌었다가 사라졌다
```

```text
[PodMonitor 를 손댈 필요가 없었다]
  maxSurge: 1 로 새 Pod 가 먼저 뜨고 옛 Pod 가 빠진다
  → 라벨로 고르므로 새 Pod 가 자동으로 잡힌다
```

### 최종

```bash
kubectl get pod -n bookstore -o wide
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "..."
```

```text
api-86d9679588-k2kb5      1/1  Running  0  79s  10.244.5.24    worker01
api-86d9679588-rkmfb      1/1  Running  0  86s  10.244.30.97   worker02
worker-5d66f84488-sjk44   1/1  Running  0  86s  10.244.30.98   worker02

up api-86d9679588-k2kb5      http://10.244.5.24:9000/metrics
up api-86d9679588-rkmfb      http://10.244.30.97:9000/metrics
up worker-5d66f84488-sjk44   http://10.244.30.98:9000/metrics
```

## 10. 문제 4 — `version` 라벨이 거짓말을 하고 있었다

```bash
curl -s http://192.168.8.143:30090/api/v1/query \
  --data-urlencode 'query=app_info' | python3 -m json.tool
```

```json
{
  "component": "api",
  "node": "worker02",
  "pod": "api-86d9679588-rkmfb",
  "exported_pod": "api-86d9679588-rkmfb",
  "exported_namespace": "bookstore",
  "version": "20260826-0839"
}
```

```text
지표      version: "20260826-0839"
실제 코드  bookstore:20260902-0713
```

### 원인 — APP_VERSION 은 이미지 태그와 무관하다

```bash
grep -n "app_version" Books-app/app/config.py
grep -n "APP_VERSION" k8s/01-configmap.yaml
kubectl get cm bookstore-config -n bookstore -o jsonpath='{.data.APP_VERSION}'; echo
```

```text
config.py:206     app_version=loader.string("APP_VERSION", "dev")
01-configmap.yaml APP_VERSION: "20260826-0301"      ← Windows 저장소
클러스터           20260826-0839                     ← 실제 적용된 값

→ 세 곳이 전부 다르다
```

```text
★ 관측에서 가장 위험한 종류의 오류다
  "지금 무슨 버전이 돌고 있나" 를 지표로 본다
  → 배포 후 문제가 나면 이걸로 범인을 찾는다
  → 그게 틀리면 잘못된 배포를 지목한다
```

### 수정

```bash
# 01-configmap.yaml 의 APP_VERSION 을 20260902-0713 으로
kubectl apply -f 01-configmap.yaml
kubectl rollout restart deploy/api deploy/worker -n bookstore
```

```text
★ apply 만으로는 반영이 안 된다
  envFrom 으로 들어가는 값은 프로세스 시작 시점에 박힌다
  → 07-api.yaml 에 적어둔 그 주석이다
     bookstore/config-note: "ConfigMap 변경 시 kubectl rollout restart 필요"
```

```text
worker worker-5d66f84488-sjk44   20260826-0839     ← 사라진 Pod
api    api-86d9679588-k2kb5      20260826-0839     ← 사라진 Pod
api    api-6c5c799d6-7lb9k       20260902-0713     ★
api    api-6c5c799d6-kv8fw       20260902-0713     ★
worker worker-6c989db87d-9xkl5   20260902-0713     ★
```

```text
[죽은 Pod 가 5분간 보인다]
  PromQL 순간 조회는 최근 5분 안의 마지막 값을 본다 (lookback delta)
  → "지금 도는 Pod 수" 를 app_info 로 세면 5분간 과대 계상된다
  → Phase 4 대시보드에서 부딪힐 지점이다
```

```text
[8단계 GitOps 가 푸는 문제다]
  사람이 세 곳을 맞춰야 한다
    07-api.yaml image / 08-worker.yaml image / 01-configmap.yaml APP_VERSION
  → 하나라도 빠뜨리면 지표가 거짓말을 한다
```

## 11. 최종 확인 — 한 바퀴 돌리기

```bash
curl -s 'http://192.168.8.143:30090/api/v1/label/__name__/values' | python3 -c "..."
```

```text
55 종류

  app_info  app_ready  app_start_time_seconds
  books_stock_negative_total
  cache_operations_total  cache_operation_duration_seconds_{bucket,count,sum}
  db_pool_{size,available,waiting}  db_pool_wait_seconds_*
  db_query_duration_seconds_*  db_transaction_duration_seconds_*
  debug_endpoints_enabled
  dependency_up  dependency_check_duration_seconds_*
  http_requests_total  http_errors_total  http_request_duration_seconds_*
  order_process_duration_seconds_*  order_queue_wait_seconds_*
  queue_{length,enqueued_total,dequeued_total}
  worker_in_flight  worker_last_poll_timestamp_seconds
  (+ _created 13종)
```

```bash
for i in $(seq 1 30); do curl -s -o /dev/null http://192.168.8.143:30800/books; done
sleep 35
curl -s http://192.168.8.143:30090/api/v1/query \
  --data-urlencode 'query=sum by (route_class, status) (http_requests_total)' | python3 -c "..."
```

```text
{'route_class': 'internal', 'status': '200'} 158
{'route_class': 'read', 'status': '200'} 30        ★ 보낸 수와 정확히 일치
```

```text
★ 코드 → 지표 → 수집 → 조회가 한 바퀴 돌았다

  internal 이 따로 세어진 것도 확인됐다
  → /metrics 스크레이프와 probe 가 실제 트래픽과 안 섞인다
  → 3단계 00 문서에서 route_class 를 넣은 이유가 여기서 값을 한다
```

## Phase 3 결과

```text
[만든 것]
  k8s/10-app-podmonitor.yaml       선언 한 장
  app/metrics.py                   import 한 줄
  k8s/01-configmap.yaml            APP_VERSION 갱신

[상태]
  Target      22 → 25   (api 2, worker 1 전부 up)
  앱 지표      55종
  이미지       bookstore:20260902-0713
```

```text
[확인한 것]
  PodMonitor 로 Service 없이 containerPort 를 긁을 수 있다
  선언 한 장으로 수집 대상이 늘고 Prometheus 는 재시작하지 않는다
  Operator 가 prometheus.yaml 에 job 을 추가한다 (1293번 줄)
  config-reloader 는 폴링이 아니라 파일 감시다 (즉시 reload)
  role: pod 는 Endpoints 를 안 본다
  DB 가 죽어도 /health/live 와 /metrics 는 응답한다
  route_class 로 internal 과 실제 트래픽이 분리된다
```

## 남은 것 / 알고 넘어가는 것

```text
[6단계 실험 소재로 남긴 것]
  ★ psycopg 풀 재시도 버그
    attempt 3~6   "pool has already been opened/closed and cannot be reused"
    → 1번 시도 실패로 풀이 닫히고, 이후 재시도가 죽은 풀을 재사용한다
    → 지수 백오프가 무의미해졌다. DB 가 늦게 떠도 복구가 안 된다
    → dependency_watcher 도 같은 죽은 풀을 본다
    → "Postgres 를 죽였다 살리면 복구되는가" 실험에서 정면으로 부딪힌다
    → 지금 고치면 그 장면을 못 본다. 실험 뒤에 고친다

[6단계 전에 고칠 것]
  REGISTRY = CollectorRegistry() 가 새 레지스트리를 만든다
    주석은 "기본 레지스트리를 그대로 쓴다" 인데 코드가 반대다
    → process_*, python_* 가 하나도 안 나온다 (확인: grep -c = 0)
    → 메모리 누수 실험에 필요한 지표다

  _created 13종
    55종 중 24%. 거의 안 쓰는 값이다
    → prometheus_client.disable_created_metrics() 로 끌 수 있다
    → "평문으로 가면 _created 가 안 생긴다" 는 예상은 틀렸다. 형식과 무관하다

[정리해도 되는 것]
  app_info 의 pod, namespace 가 Prometheus 라벨과 충돌한다
    honor_labels: false → 앱 값이 exported_ 접두사로 밀린다
    → Prometheus 가 반드시 붙이므로 앱에서 뺄 수 있다
    → node, version, component 는 남겨야 한다 (Prometheus 가 안 붙인다)

  k8s/ 폴더의 번호 충돌
    03-postgres-pv.yaml / 03-service.yaml
    10-app-podmonitor.yaml / 10-test-create-namespace.yaml
    → 실습용(10~12)이 실제 앱 매니페스트와 섞여 있다

[별개 문제 — 미해결]
  build01 도커 브리지 네트워크
    컨테이너가 밖으로도 안 나가고 안으로도 안 들어온다
    ip_forward=1, FORWARD policy DROP + DOCKER-USER 는 정상으로 보인다
    → 재부팅 전후 비교를 안 했다
    → 우회: --network=host

[확인 안 한 것]
  ServiceMonitor + targetPort 로도 9000 을 긁을 수 있는가
    role: endpoints 가 "Endpoints 에 안 묶인 컨테이너 포트" 도 발견한다는
    문서상의 동작. 실제로 되는지 확인하지 않았다
```

## 확인 명령

```bash
# PodMonitor 와 라벨
kubectl get podmonitor -n bookstore --show-labels

# Prometheus 가 어떤 PodMonitor 를 보는가
kubectl get prometheus kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{.spec.podMonitorSelector}{"\n"}{.spec.podMonitorNamespaceSelector}{"\n"}'

# Operator 가 만든 job
kubectl get secret prometheus-kube-prom-stack-kube-prome-prometheus -n monitoring \
  -o jsonpath='{.data.prometheus\.yaml\.gz}' | base64 -d | gunzip | grep -n podMonitor

# Target
curl -s http://192.168.8.143:30090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    if 'podMonitor' in t.get('scrapePool',''):
        print(t['health'], t['labels'].get('pod'), t['scrapeUrl'], t['lastError'][:60])
"

# 걸러진 대상 — "발견 후 탈락" 과 "발견 자체가 안 됨" 을 가른다
curl -s 'http://192.168.8.143:30090/api/v1/targets?state=dropped' | python3 -c "
import sys, json
d = json.load(sys.stdin)['data'].get('droppedTargets', [])
print('dropped:', len(d))
for t in d[:10]:
    l = t.get('discoveredLabels', {})
    print(l.get('__meta_kubernetes_pod_name'), l.get('__meta_kubernetes_pod_container_port_name'))
"

# 버전 확인 — 이미지 태그와 맞는가
curl -s http://192.168.8.143:30090/api/v1/query \
  --data-urlencode 'query=app_info' | python3 -c "
import sys,json
for r in json.load(sys.stdin)['data']['result']:
    m=r['metric']; print(m['component'], m['pod'], m['version'])
"
kubectl get cm bookstore-config -n bookstore -o jsonpath='{.data.APP_VERSION}'; echo
kubectl get deploy api -n bookstore -o jsonpath='{.spec.template.spec.containers[0].image}'; echo

# 앱이 내는 Content-Type — 이름표와 본문이 맞는가
kubectl port-forward -n bookstore deploy/api 9000:9000 &
curl -sD- -o /dev/null http://127.0.0.1:9000/metrics | grep -i content-type

# 한 바퀴 돌리기
for i in $(seq 1 30); do curl -s -o /dev/null http://192.168.8.143:30800/books; done
sleep 35
curl -s http://192.168.8.143:30090/api/v1/query \
  --data-urlencode 'query=sum by (route_class, status) (http_requests_total)'
```

## 다음

```text
Phase 4  PromQL 과 대시보드
         55종의 지표로 무엇을 볼 것인가
         app_info 로 Pod 를 세면 안 되는 이유(5분 lookback)를 여기서 다룬다
```

# 5단계. 장애 테스트 전 Observability 구성

`cloud-native-learning-roadmap.md` **5단계**의 작업 기록이다.

## 이 단계의 목적

**대시보드를 예쁘게 만드는 것이 아니다.** 4단계에서 `kubectl` 로 하나씩 눈으로 확인하던 것을 **숫자와 시계열로 남기는 것**이다.

```text
[관측 없이 Pod 를 지우면]
  Pod 삭제 → 새 Pod 생성 → 복구 완료
  → 이게 전부다

[관측이 있으면]
  Pod 삭제
  → Available Replica 감소
  → 일부 요청 실패/지연
  → 새 Pod 생성 → Container 시작
  → Readiness Probe 통과          ← 4단계 8번 글에서 본 그것
  → Service Endpoint 등록          ← 4단계 5번 글에서 본 그것
  → 트래픽 유입
  → 오류율과 지연시간 정상화
  → 복구까지 몇 초 걸렸는지 숫자로 남는다
```

로드맵 원칙 2 가 이 단계의 존재 이유다. **장애를 발생시키기 전에 관측 환경을 구성한다.**

## 4단계에서 못 재고 넘어간 것들

이 단계는 그 빚을 갚는 단계이기도 하다.

```text
8번 글 (probe / resources)
  실제 메모리 사용량         container_memory_working_set_bytes
  → 48Mi 로 뜨는 건 봤지만 실제로 얼마 쓰는지는 몰랐다
  → limits 를 제대로 정하려면 이게 필요하다

  CPU throttle              container_cpu_cfs_throttled_seconds_total
  → "CPU limits 를 걸면 p99 가 튄다" 를 실측 못 했다

5번 글 (Service / Endpoints)
  Endpoints 가 비어 있는 것을 알람으로
  → kube_endpoint_address_available == 0
  → Kubernetes 가 이벤트를 안 남기니 관측 도구가 대신 잡아야 한다

9번 글 (외부 노출)
  Local 의 부하 불균형을 실제 요청으로 재기
  → 규칙으로는 25/25/50 이 나온다는 걸 계산했다
  → 실제로 그런지는 안 봤다
```

## Helm 을 쓴다 — 4단계와 다르다

```text
[4단계에서 Helm 을 안 쓴 이유]
  Template 안에 리소스의 실제 구조가 가려진다
  → 무엇이 왜 그렇게 됐는지 모른 채 넘어간다

[5단계에서 Helm 을 쓰는 이유]
  kube-prometheus-stack 을 매니페스트로 풀면 CRD 만 수천 줄이다
  → 손으로 쓸 수 있는 양이 아니고
  → 쓴다 해도 배우는 게 없다. 베끼는 것이다
```

```text
★ 원칙 1 의 취지는 "Helm 을 쓰지 마라" 가 아니라
  "편의성 때문에 이해를 건너뛰지 마라" 다

  4단계에서 매니페스트를 직접 썼기 때문에
  → 이제 Helm 이 만든 오브젝트를 읽을 수 있다
  → 그게 순서를 그렇게 잡은 이유다
```

```text
[그래서 이렇게 나눈다]

  Helm 으로 깐다     Prometheus, Grafana, Alertmanager, Loki
                     → 대신 values.yaml 을 한 줄씩 읽고 왜 그런지 정리한다
                     → 깐 뒤에 만들어진 오브젝트를 4단계의 눈으로 뜯어본다

  직접 쓴다          ServiceMonitor          "Prometheus 가 우리 앱을 어떻게 찾는가"
                     PrometheusRule          알람 조건
                     PVC / StorageClass 확인
                     → 이건 남이 안 해준다. 우리 앱에 관한 것이라서
```

---

## 클러스터 사전 조건과 결정 사항

### 결정 1 — 노드 자원

```text
[가진 것]
  worker01   2 vCPU / 4GB
  worker02   2 vCPU / 4GB

[모니터링 스택이 요구하는 것 — 대략]
  Prometheus         1~2 GB     시계열을 메모리에 들고 있다
  Grafana            ~200 MB
  Alertmanager       ~100 MB
  kube-state-metrics ~100 MB
  node-exporter      노드마다 ~50 MB
  Loki + Promtail    ~500 MB
```

```text
★ 기본값으로 깔면 Pending 이나 OOMKilled 가 날 가능성이 크다
  → 4단계 8번 글에서 본 그 상황을 이번엔 모니터링 스택이 겪는다
```

```text
[대응]

  1. bookstore-lab 을 줄인다
       testdeploy replicas 3 → 1
       회수: cpu 600m / memory 1000Mi
       → 파일(03-deployment.yaml)도 함께 1 로 고친다
         안 그러면 apply 할 때 다시 3 이 된다

  2. 스택을 가볍게 설정한다
       retention 을 짧게        7d → 2d
       scrape interval 을 늘림   30s → 60s
       안 쓰는 컴포넌트를 끔
       → values.yaml 을 읽으며 배우는 게 목적이니 오히려 좋다

  3. 그래도 모자라면 worker VM 메모리를 4GB → 6GB
       호스트가 32GB 라 여유는 있다
```

```text
[전부 지우는 선택지도 있다]
  kubectl delete namespace bookstore-lab      회수: 900m / 1500Mi
  → 되살리기는 kubectl apply -f k8s-lab/
  → 매니페스트 7개가 Git 에 있으므로 손실이 없다
```

### 결정 2 — Prometheus 의 저장소 → **local-path-provisioner**

```text
Prometheus 는 시계열을 디스크에 쓴다. PV 가 필요하다
emptyDir 로 두면 Pod 가 죽을 때 데이터가 날아간다
→ 6단계에서 "장애 전후 비교" 를 해야 하는데 비교 대상이 사라진다
```

```text
[고른 것] local-path-provisioner 를 설치해 StorageClass 를 만든다

  4단계에서는 PV 를 손으로 만들었다
  → 이번엔 PVC 만 쓰면 PV 가 자동으로 생기는 방식을 본다
  → "정적 프로비저닝" 과 "동적 프로비저닝" 의 차이를 대비로 이해한다
  → 4단계 7번 글에서 안 다룬 주제다
```

```text
[알아둘 것]
  local-path 도 결국 노드의 디렉터리다
  → nodeAffinity 로 그 노드에 묶인다
  → 4단계 실험 E(노드 장애)의 문제가 그대로 남아 있다
  → 그 실험을 이 단계 뒤에 하기로 미룬 이유이기도 하다
```

### 결정 3 — Grafana 노출 → **NodePort 로 시작, 셋이 되면 Ingress**

```text
[1차] NodePort
  Grafana 하나만 열어 시작한다. 가장 단순하다

[2차] Ingress Controller 를 설치한다
  Grafana / Prometheus / Alertmanager 로 웹 UI 가 셋이 되면
  → 포트 번호 셋을 외우게 된다
  → 4단계 9번 글에서 "그때 도입하면 이래서 쓰는구나가 된다" 고 쓴 그 시점이다
```

```text
★ 그리고 그때 labingress 가 갑자기 동작하기 시작한다

  4단계에서 만들어두고 아무 일도 안 일어나던 그 Ingress 다
  → ADDRESS 가 채워지고 80 이 열린다
  → "선언 + 실행자" 구조의 후속 확인이 된다
  → 그래서 지우지 않고 남겨뒀다
```

---

## 진행 순서

| Phase | 내용 | 방식 | 상태 |
|---|---|---|---|
| 0 | 준비 — 자원 정리, helm 설치, StorageClass | 직접 | **완료** |
| 1 | Metrics Server | 매니페스트 | **완료** |
| 2 | kube-prometheus-stack | Helm | **완료** |
| 3 | 앱 지표 연결 (PodMonitor) | **직접 작성** | **완료** |
| 4 | PromQL 과 대시보드 | 직접 | 예정 |
| 5 | Loki + Promtail | Helm | 예정 |
| 6 | Alert Rule + Alertmanager | **직접 작성** | 예정 |

```text
★ Phase 3 이 이 단계의 핵심이다

  Phase 1, 2 는 설치다. Helm 이 다 해준다
  Phase 3 은 "내 앱을 관측 대상으로 등록하는" 일이다
  → 이건 남이 안 해준다
  → 그리고 4단계에서 배운 라벨/selector 가 그대로 쓰인다
```

### Phase 0 — 준비

```text
  bookstore-lab 자원 정리         replicas 조정 또는 네임스페이스 삭제
  monitoring 네임스페이스 생성
  VM 에 helm 설치
  local-path-provisioner 설치     StorageClass 확인
  → PVC 를 하나 만들어 PV 가 자동 생성되는지 검증
```

### Phase 1 — Metrics Server

```text
  가장 작다. Helm 이 필요 없다. 매니페스트 하나
  → kubectl top 이 된다

★ kubeadm 클러스터에서는 --kubelet-insecure-tls 가 필요할 수 있다
  kubelet 인증서가 자체 서명이라 검증에 실패한다
  → 그냥 옵션을 넣지 말고 왜 필요한지 확인하고 넣는다

[여기서 갚는 빚]
  4단계 8번 글에서 못 잰 실제 메모리 사용량
```

```text
[Metrics Server 와 Prometheus 를 둘 다 까는 이유]

  kubelet 의 cAdvisor
    → cgroup 에서 Pod 별 CPU/Memory 를 집계
         ↓
  Metrics Server        메모리에만 보관. 히스토리 없음
    → kubectl top, HPA 가 "현재 값" 을 조회할 때 사용

  Prometheus            시계열 저장
    → 추세 분석, 과거 조회, 애플리케이션 커스텀 지표

  → 하나가 다른 하나를 대체하지 않는다
```

### Phase 2 — kube-prometheus-stack

```text
  Prometheus + Grafana + Alertmanager
  + kube-state-metrics + node-exporter + Prometheus Operator

[읽으면서 정리할 것 — values.yaml]
  retention / retentionSize
  scrapeInterval / evaluationInterval
  resources (requests, limits)
  persistence (StorageClass, 크기)
  각 subchart 를 켜고 끄는 스위치

[설치 후 뜯어볼 것 — 4단계의 눈으로]
  kubectl get all -n monitoring
  StatefulSet 인가 Deployment 인가, 왜 그런가
  ServiceAccount 와 RBAC 이 무엇을 허용하는가
  Prometheus Operator 가 만든 CRD 는 무엇인가
  probe 와 resources 는 어떻게 잡혀 있는가
```

```text
★ kube-state-metrics 와 node-exporter 의 역할 구분

  node-exporter        노드 자체의 자원      CPU, 메모리, 디스크, 네트워크
                       DaemonSet 으로 뜬다
  kube-state-metrics   오브젝트의 상태       Deployment 3/3, Pod Phase, PVC Bound
                       → API 서버를 읽어 지표로 바꾼다
                       → "kubectl get 을 지표로 만든 것" 에 가깝다
```

### Phase 3 — 앱 지표 연결 ★

```text
우리 앱은 이미 /metrics 를 낸다 (관리 포트 9000)
→ Prometheus 가 그걸 찾아가게 만들어야 한다
```

```text
[예상했던 것]
  ServiceMonitor 를 쓴다
  → 막힐 만한 지점: 관리 포트(9000)가 Service 에 안 열려 있다
  → 4단계에서 "9000 은 Service 에 절대 안 넣는다" 고 했다

[실제]
  ★ ServiceMonitor 로는 불가능했다. PodMonitor 를 썼다
    ServiceMonitor 는 Service 의 Endpoints 를 긁는다
    → Endpoints 에는 Service 에 선언된 포트만 적힌다
    → worker 는 Service 자체가 없다

  PodMonitor 는 Pod 를 라벨로 직접 고르고 containerPort 를 긁는다
  → Service 에 9000 을 노출하지 않은 채로 긁힌다
```

```text
[겪은 것 — 03-app-metrics.md 참조]
  앱 코드 작업은 없었다. 3단계에서 이미 다 만들어져 있었다
  Target 은 잡혔는데 셋 다 DOWN — Content-Type 이름표가 본문과 달랐다
  app_info 의 version 이 이미지 태그와 어긋나 있었다
  빌드 서버의 도커 브리지가 망가져 배포에 세 번 막혔다
```

```text
[결과]
  Target 22 → 25 (api 2, worker 1)
  앱 지표 55종
  요청 30건 → 30초 뒤 지표에 정확히 30
```

### Phase 4 — PromQL 과 대시보드

```text
[인프라 지표]
  Node Ready 상태 / CPU, Memory, Disk, Network
  Pod Phase / Restart Count / Container 종료 사유
  Deployment Desired vs Available Replica
  Pending Pod 수
  CPU·Memory Request / Limit / 실제 사용량
  PersistentVolume 사용량

[애플리케이션 지표]
  초당 요청 수 / 상태 코드별 요청 수 / 오류율
  평균 응답시간 / p95 / p99
  현재 처리 중인 요청 수
  DB Connection 수와 대기 수
  Redis Queue 길이
  Worker 처리량 / 실패 수 / 소요시간
```

```text
★ 4단계의 빚을 갚는 자리

  container_memory_working_set_bytes         실제 메모리 사용량
  container_cpu_cfs_throttled_seconds_total  CPU throttle
  kube_endpoint_address_available            Endpoints 가 비었는지
```

### Phase 5 — Loki

```text
Application stdout/stderr → 수집 에이전트 → Loki → Grafana

  Promtail 또는 Grafana Alloy
```

```text
[우리 앱이 이미 갖춘 것]
  구조화된 JSON 로그
  ctx_method / ctx_path / ctx_status / ctx_duration_ms / ctx_route_class

[빠진 것]
  ctx_pod, ctx_node 가 없다  →  pod=unknown 으로 찍힌다
  → Downward API 를 안 넣어서다 (4단계 8번 글에서 확인)
  → 이걸 먼저 고쳐야 로그에서 Pod 를 구별할 수 있다
```

### Phase 6 — Alert

```text
  Prometheus Alert Rule
  Alertmanager
  Email 또는 Webhook

[4단계에서 나온 알람 후보]
  Endpoints 가 5분 이상 비어 있음        Kubernetes 는 이벤트도 안 남긴다
  Pod 재시작이 반복됨                    liveness 오설정 탐지
  OOMKilled 발생
  Deployment Available < Desired 지속
  PVC 사용량 임계 초과
```

---

## 단계 결과물

```text
  Grafana Dashboard
  Prometheus Target 상태 확인
  주요 PromQL 정리
  애플리케이션 Metrics 정의
  Loki LogQL 예제
  장애별 Alert Rule 초안
```

## 문서

| 파일 | 내용 |
|---|---|
| [00-preparation.md](00-preparation.md) | Phase 0 — 자원 정리, helm, StorageClass |
| [01-metrics-server.md](01-metrics-server.md) | Phase 1 — kubectl top, API Aggregation, x509 진단 |
| [02-prometheus-stack.md](02-prometheus-stack.md) | Phase 2 — Helm 설치, Operator, Target DOWN, kube-proxy 사고와 복구 |
| [03-app-metrics.md](03-app-metrics.md) | Phase 3 — PodMonitor, Content-Type 불일치, APP_VERSION 어긋남 |
| (예정) `04-dashboards.md` | Phase 4 |
| (예정) `04-promql.md` | Phase 4 |
| (예정) `05-loki.md` | Phase 5 |
| (예정) `06-alert.md` | Phase 6 |

```text
★ 빈 템플릿을 미리 만들지 않는다
  실제로 진행할 때 명령과 출력을 담아 작성한다
```

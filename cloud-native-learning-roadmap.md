# DevOps 엔지니어를 위한 Cloud Native 실전 학습 로드맵

> VMware 기반 Kubernetes 구축부터 애플리케이션 개발, 장애 실험, Observability, GitOps, Terraform, AWS EKS 운영까지 직접 경험하는 장기 학습 프로젝트

## 들어가며

현재 내가 근무하는 환경에서는 Kubernetes와 AWS EKS를 중심으로 한 Cloud Native 운영을 충분히 경험하기 어렵다.

하지만 DevOps 엔지니어를 목표로 한다면 단순히 Docker나 Kubernetes 명령어를 사용할 줄 아는 수준을 넘어, 다음과 같은 전체 흐름을 이해해야 한다고 생각한다.

- 애플리케이션이 컨테이너 이미지로 만들어지는 과정
- 이미지가 Registry에 저장되고 Kubernetes에 배포되는 과정
- Kubernetes Controller가 선언된 상태를 유지하는 방식
- 장애가 발생했을 때 시스템이 이를 감지하고 복구하는 과정
- Metrics, Logs, Events를 통해 장애 원인을 분석하는 방식
- GitOps와 Infrastructure as Code를 이용해 운영 상태를 재현하는 방식
- AWS EKS 환경에서 네트워크, IAM, 스토리지, 오토스케일링이 연결되는 구조

따라서 실무에서 경험하기 어렵다는 이유로 학습을 미루기보다, 로컬 환경에서 Kubernetes를 직접 구축하고 AWS EKS까지 확장하는 개인 프로젝트를 진행하기로 했다.

이 프로젝트의 목적은 단순히 Kubernetes 클러스터를 한 번 설치해보는 것이 아니다.

애플리케이션 개발부터 배포, 관측, 장애 발생, 복구, 자동화까지 하나의 운영 흐름으로 연결하여 Cloud Native 환경을 간접적으로 경험하는 것이 핵심이다.

---

## 프로젝트의 전체 방향

이 프로젝트는 다음 순서로 진행한다.

1. VMware 환경에서 Kubernetes 클러스터를 직접 구축하고 기본 원리를 학습한다.
2. Kubernetes에 실제 애플리케이션을 배포하고 다양한 장애 상황을 재현한다.
3. Prometheus, Grafana, Loki 등을 구성하여 Metrics와 Logs를 기반으로 장애 원인을 분석한다.
4. Helm, GitHub Actions, Argo CD를 적용하여 CI/CD와 GitOps 구조를 만든다.
5. Terraform으로 AWS EKS 환경을 구성하고 로컬에서 학습한 내용을 운영형 구조로 확장한다.
6. AWS CloudWatch, SNS, SES, Prometheus, Grafana, Alertmanager 등을 연결하여 모니터링과 알림 체계를 구성한다.

최종 목표는 다음과 같다.

> 컨테이너 기반 애플리케이션을 직접 개발하고 AWS EKS에 배포한 뒤, GitOps, 장애 실험과 복구, Observability, 오토스케일링, 운영 자동화를 경험하여 실제 Cloud Native 운영 환경의 전체 흐름을 이해한다.

---

## 최종 아키텍처 개요

```text
개발자
  │
  │ Git Push
  ▼
Application Repository
  │
  │ GitHub Actions
  │ Unit Test → Image Build → Vulnerability Scan → ECR Push
  ▼
Amazon ECR
  │
  │ Image Tag 또는 Digest 갱신
  ▼
GitOps Repository
  │
  │ Argo CD가 변경 감지
  ▼
Kubernetes / Amazon EKS
  │
  ├─ Ingress / AWS Load Balancer Controller
  ├─ API Pod
  ├─ Worker Pod
  ├─ Redis
  └─ PostgreSQL 또는 Amazon RDS
       │
       ├─ Prometheus / Grafana
       ├─ Loki
       ├─ Alertmanager
       └─ CloudWatch / SNS / SES
```

이 구조에서 중요한 점은 각 도구를 개별적으로 사용하는 것이 아니라, 하나의 변경이 실제 운영 환경에 반영되는 전체 흐름을 이해하는 것이다.

```text
코드 변경
→ 테스트
→ 이미지 생성
→ Registry 저장
→ GitOps 선언 변경
→ Kubernetes 배포
→ 상태 관측
→ 장애 감지
→ 복구 및 원인 분석
```

---

# 학습 원칙

## 1. 자동화하기 전에 직접 구성한다

처음부터 모든 것을 자동화하면 내부 동작을 이해하기 어렵다.

따라서 Kubernetes 설치, Manifest 작성, 장애 분석을 먼저 직접 수행한 뒤 반복 작업을 Ansible, Helm, Terraform, GitHub Actions, Argo CD로 자동화한다.

## 2. 장애를 발생시키기 전에 관측 환경을 구성한다

Pod를 삭제하고 다시 생성되는 모습만 확인하는 것은 충분한 장애 실험이 아니다.

장애 전후의 요청 성공률, 응답 시간, Replica 수, Pod 상태, Node 상태, 로그 변화를 함께 관찰해야 운영 관점의 학습이 된다.

## 3. 로컬 환경에서 원리를 이해한 뒤 AWS로 확장한다

EKS부터 시작하면 AWS가 대신 처리해주는 영역과 Kubernetes 자체 기능을 구분하기 어렵다.

먼저 kubeadm 기반 클러스터에서 Kubernetes의 기본 동작을 확인하고, 이후 EKS에서 AWS가 추가로 제공하는 기능을 비교한다.

## 4. 도구별 책임 범위를 명확하게 나눈다

- GitHub Actions: 테스트, 빌드, 이미지 검사, Registry Push
- Argo CD: Kubernetes Desired State 배포 및 동기화
- Terraform: AWS Infrastructure 생성 및 변경
- Prometheus: Metrics 수집
- Grafana: Metrics와 Logs 시각화
- Loki: 애플리케이션 및 컨테이너 로그 저장
- Alertmanager: Prometheus Alert 전달
- CloudWatch: AWS 리소스 Metrics, Logs, Events 수집
- SNS와 SES: AWS 알림 전달

같은 리소스를 여러 도구가 동시에 관리하지 않도록 소유권을 분리하는 것이 중요하다.

## 5. 모든 과정을 기록하고 증명 가능한 결과물로 남긴다

각 단계에서 다음 자료를 GitHub와 블로그에 남긴다.

- 구성 목적과 아키텍처
- 설치 및 설정 과정
- 사용한 Manifest와 Terraform 코드
- 장애 시나리오
- 장애 전후 Metrics와 Logs
- 원인 분석 과정
- 복구 방법
- 운영 환경에서의 주의점
- 개선할 수 있는 부분

---

# 1단계. VMware 기반 Kubernetes 클러스터 구축

## 구성 환경

VMware Workstation에 Linux VM 3대를 생성한다.

### control-plane-01

- kube-apiserver
- kube-scheduler
- kube-controller-manager
- etcd
- kubelet
- containerd

### worker-01

- kubelet
- kube-proxy
- containerd

### worker-02

- kubelet
- kube-proxy
- containerd

클러스터 구축 도구는 `kubeadm`을 사용한다.

Minikube나 kind는 빠르게 Kubernetes를 체험하기에는 좋지만, Control Plane 구성요소, 인증서, kubeconfig, kubelet 등록, CNI 설치 과정이 상당 부분 추상화된다.

반면 kubeadm을 사용하면 최소 기능을 갖춘 표준 Kubernetes 클러스터를 직접 구성하면서 클러스터의 초기화와 Node Join 과정을 확인할 수 있다.

## 구축 순서

```text
OS 기본 설정
→ Swap 비활성화
→ Kernel Module과 sysctl 설정
→ containerd 설치 및 구성
→ kubelet, kubeadm, kubectl 설치
→ kubeadm init
→ kubectl용 kubeconfig 설정
→ CNI Plugin 설치
→ Worker Node kubeadm join
```

모든 명령은 가능한 한 직접 수행한다.

목적은 설치 명령을 외우는 것이 아니라 다음 질문에 답할 수 있는 상태가 되는 것이다.

- kubeadm은 어떤 인증서와 kubeconfig를 생성하는가
- kubelet은 API Server를 어떻게 찾고 인증하는가
- Worker Node는 어떤 절차를 거쳐 클러스터에 등록되는가
- CNI 설치 전에는 왜 Pod 간 통신이 정상적으로 동작하지 않는가
- Control Plane 구성요소는 실제로 어디에서 실행되는가
- Static Pod는 일반 Pod와 무엇이 다른가
- etcd에는 어떤 정보가 저장되는가
- API Server가 중단되었을 때 기존 Pod와 신규 스케줄링은 어떻게 달라지는가
- kubelet이나 containerd가 중단되면 Node와 Pod 상태는 어떻게 변하는가

## 단계 결과물

- 3 Node Kubernetes 구성도
- kubeadm init 및 join 과정 정리
- `/etc/kubernetes` 디렉터리 분석
- Static Pod Manifest 분석
- kubeconfig와 인증서 구조 정리
- CNI 설치 전후 Pod Network 비교
- Control Plane 장애 실험 기록

---

# 2단계. Kubernetes 오브젝트와 내부 동작 학습

애플리케이션을 배포하기 전에 Kubernetes의 기본 오브젝트를 단계적으로 학습한다.

## 학습 순서

```text
Pod
→ ReplicaSet
→ Deployment
→ Service
→ EndpointSlice
→ Ingress
→ ConfigMap / Secret
→ Namespace
→ ServiceAccount / RBAC
→ PersistentVolume / PersistentVolumeClaim
→ StatefulSet
→ DaemonSet
→ Job / CronJob
```

각 오브젝트를 단순히 정의만 암기해서는 부족하다.

오브젝트를 생성했을 때 어떤 Controller가 반응하고, 어떤 상태가 etcd에 저장되며, 실제 컨테이너가 어느 과정을 거쳐 실행되는지 이해해야 한다.

예를 들어 Deployment 생성 과정은 다음과 같다.

```text
사용자가 Deployment 생성 요청
→ API Server가 인증, 인가, Admission, Schema 검증 수행
→ Desired State를 etcd에 저장
→ Deployment Controller가 ReplicaSet 생성
→ ReplicaSet Controller가 Pod 생성
→ Scheduler가 Pod를 실행할 Node 선택
→ 선택된 Node의 kubelet이 PodSpec 확인
→ kubelet이 containerd에 컨테이너 생성 요청
→ CNI가 Pod Network 구성
→ CSI 또는 Volume Plugin이 필요한 Volume 연결
→ kubelet이 Pod 상태를 API Server에 보고
```

## 주요 확인 명령어

```bash
kubectl get deployment,rs,pod -o wide
kubectl describe deployment <deployment-name>
kubectl describe pod <pod-name>
kubectl get events --sort-by=.metadata.creationTimestamp
kubectl logs <pod-name>
kubectl logs <pod-name> --previous
kubectl get service
kubectl get endpointslices
kubectl get pod -o yaml
kubectl api-resources
kubectl explain deployment.spec
```

## 단계 결과물

각 오브젝트마다 다음 내용을 정리한다.

1. 오브젝트의 역할
2. 생성 시 동작하는 Controller
3. 주요 Spec과 Status 필드
4. 다른 오브젝트와의 연결 관계
5. 장애 또는 잘못된 설정 사례
6. 확인 명령어
7. 운영 시 주의할 점

---

# 3단계. 장애 실험을 위한 애플리케이션 개발

Kubernetes의 기능을 제대로 실험하려면 단순한 정적 웹 페이지보다 여러 구성요소가 연결된 애플리케이션이 필요하다.

다음과 같은 작업 처리 시스템을 개발한다.

```text
Client
  │
  ▼
Ingress
  │
  ▼
Backend API
  ├─ PostgreSQL
  ├─ Redis Queue
  └─ Worker
```

## Backend API

예시 API는 다음과 같다.

```text
POST /tasks
GET  /tasks
GET  /tasks/:id
GET  /health/live
GET  /health/ready
GET  /metrics
```

### 주요 역할

- 사용자 요청 수신
- 작업 정보를 PostgreSQL에 저장
- 처리할 작업을 Redis Queue에 등록
- 작업 상태 조회
- Liveness와 Readiness 상태 제공
- Prometheus Metrics 제공

## Redis

API가 요청한 작업을 Queue에 저장한다.

확인할 주요 항목은 다음과 같다.

- Queue 길이
- Queue 입력 속도
- Queue 소비 속도
- Redis 연결 실패
- Worker 처리량보다 요청량이 많을 때의 적체 현상

## Worker

```text
Redis Queue에서 작업 조회
→ 일정 시간 작업 처리
→ PostgreSQL에 처리 결과 저장
```

Worker는 처리 지연, 재시도, 실패 작업, 동시 처리량을 실험할 수 있도록 만든다.

## PostgreSQL

다음 정보를 저장한다.

- Task ID
- 작업 상태
- 생성 시간
- 처리 시작 시간
- 처리 완료 시간
- 실패 사유
- 처리 결과

## 이 구조를 선택한 이유

이 애플리케이션은 다음과 같은 다양한 장애를 의도적으로 만들 수 있다.

```text
API Pod 장애
Redis 연결 장애
PostgreSQL 연결 장애
Worker 처리 지연
Queue 적체
DB 응답 지연
Connection Pool 고갈
메모리 누수
CPU 과부하
잘못된 환경변수
Readiness 실패
부분 장애
```

## 단계 결과물

- 애플리케이션 아키텍처 문서
- API 명세
- Dockerfile
- 로컬 Docker Compose 개발 환경
- Health Check 설계
- Prometheus Metrics 설계
- 장애 유발용 테스트 Endpoint 또는 설정

---

# 4단계. 순수 Kubernetes Manifest로 애플리케이션 배포

처음에는 Helm을 사용하지 않고 순수 Kubernetes Manifest로 배포한다.

```text
k8s/
├─ namespace.yaml
├─ api-deployment.yaml
├─ api-service.yaml
├─ worker-deployment.yaml
├─ redis-deployment.yaml
├─ redis-service.yaml
├─ postgres-statefulset.yaml
├─ postgres-service.yaml
├─ pvc.yaml
├─ configmap.yaml
├─ secret.yaml
├─ ingress.yaml
├─ hpa.yaml
└─ pdb.yaml
```

처음부터 Helm을 사용하면 Template 안에 Kubernetes 리소스의 실제 구조가 가려질 수 있다.

따라서 다음 순서로 진행한다.

```text
순수 Manifest 작성
→ 각 리소스의 동작 이해
→ 환경별 중복과 설정 차이 경험
→ Template과 Packaging 필요성 체감
→ Helm Chart로 전환
```

## 이 단계에서 확인할 내용

- Deployment와 Service Selector 연결
- Service와 EndpointSlice 관계
- ConfigMap과 Secret 주입 방식
- Environment Variable과 Volume Mount 비교
- Resource Request와 Limit
- Liveness, Readiness, Startup Probe
- RollingUpdate 전략
- PodDisruptionBudget
- PersistentVolume과 PersistentVolumeClaim
- StatefulSet의 Pod 이름과 Volume 유지 방식
- Ingress를 통한 외부 접근

## 단계 결과물

- 전체 Kubernetes Manifest
- 리소스 관계도
- Deployment Rolling Update 실험
- ConfigMap과 Secret 변경 실험
- PVC 기반 데이터 유지 테스트
- 잘못된 Selector로 인한 장애 분석

---

# 5단계. 장애 테스트 전 Observability 구성

관측 환경 없이 Pod를 삭제하면 다음 정도만 확인할 수 있다.

```text
Pod 삭제
→ 새로운 Pod 생성
→ 복구 완료
```

그러나 이것만으로는 서비스 영향과 복구 과정을 충분히 설명할 수 없다.

Observability 환경을 구성하면 다음 흐름을 확인할 수 있다.

```text
Pod 삭제 발생
→ Available Replica 감소
→ 일부 요청 실패 또는 지연 발생
→ 신규 Pod 생성
→ Container 시작
→ Readiness Probe 통과
→ Service Endpoint 등록
→ 트래픽 유입
→ 오류율과 지연시간 정상화
```

## 1차 Metrics 구성

- Metrics Server
- Prometheus
- Grafana
- kube-state-metrics
- node-exporter
- Prometheus Adapter (Custom Metrics API)

### Metrics Server와 Prometheus는 역할이 다르다

둘 다 설치하는 이유가 있다. 하나가 다른 하나를 대체하지 않는다.

```text
kubelet의 cAdvisor
  → cgroup에서 Pod별 CPU/Memory를 집계
       ↓
Metrics Server        메모리에만 보관. 히스토리 없음
  → kubectl top, HPA가 "현재 값"을 조회할 때 사용

Prometheus            시계열 저장
  → 추세 분석, 과거 조회, 애플리케이션 커스텀 지표
```

| | Metrics Server | Prometheus |
|---|---|---|
| 저장 | 메모리, 최근 값만 | 디스크, 시계열 |
| 수집 대상 | CPU / Memory만 | 임의의 지표 |
| 용도 | `kubectl top`, CPU 기반 HPA | 대시보드, Alert, 커스텀 지표 |

### Prometheus Adapter를 추가하는 이유

Prometheus가 수집한 애플리케이션 지표(요청 수, Queue 길이 등)를
**HPA가 읽을 수 있는 형태로 노출**하는 어댑터다.

```text
[어댑터 없음]
  Prometheus에 Queue 길이가 쌓여 있음
  → 대시보드로 볼 수는 있음
  → 하지만 HPA는 이 값을 모름. CPU/Memory만 볼 수 있음

[어댑터 있음]
  Prometheus 지표 → Custom Metrics API로 노출
  → HPA가 "Queue 길이 100 초과 시 스케일" 같은 규칙을 쓸 수 있음
```

**이것이 없으면 5단계에서 모은 애플리케이션 지표가 6단계 HPA와 연결되지 않는다.**
지표는 보기만 하고 스케일링은 CPU로만 하게 된다.

## Kubernetes 인프라 지표

- Node Ready 상태
- Node CPU, Memory, Disk, Network
- Pod Phase
- Pod Restart Count
- Container 종료 사유
- Deployment Desired Replica
- Deployment Available Replica
- Pending Pod 수
- CPU와 Memory Request
- CPU와 Memory Limit
- 실제 CPU와 Memory 사용량
- HPA Current Replica와 Desired Replica
- PersistentVolume 사용량

## 애플리케이션 지표

- 초당 요청 수
- HTTP 상태 코드별 요청 수
- 오류율
- 평균 응답시간
- p95, p99 응답시간
- 현재 처리 중인 요청 수
- DB Connection 수
- DB Connection 대기 수
- Redis Queue 길이
- Worker 작업 처리량
- 작업 실패 수
- 작업 처리 소요시간

## 2차 Logging 구성

Loki를 추가하여 애플리케이션과 컨테이너 로그를 수집한다.

```text
Application stdout/stderr
→ Log Collection Agent
→ Loki
→ Grafana
```

필요에 따라 Promtail 또는 Grafana Alloy를 사용할 수 있다.

## Alert 구성

- Prometheus Alert Rule
- Alertmanager
- Email 또는 Webhook 알림
- AWS 환경에서는 CloudWatch Alarm, SNS, SES와 비교

## 단계 결과물

- Grafana Dashboard
- Prometheus Target 상태 확인
- 주요 PromQL 정리
- 애플리케이션 Metrics 정의
- Loki LogQL 예제
- 장애별 Alert Rule 초안

---

# 6단계. 로컬 Kubernetes 장애 테스트

부하는 k6를 사용하여 단계적으로 증가시킨다.

```text
1분: Virtual User 10명
3분: Virtual User 50명
3분: Virtual User 100명
2분: Virtual User 200명
1분: 0명까지 감소
```

각 장애 실험은 다음 순서로 기록한다.

```text
정상 상태 기준값 확인
→ 장애 발생
→ 사용자 영향 확인
→ Kubernetes 상태 확인
→ Metrics와 Logs 확인
→ 원인 분석
→ 복구 수행
→ 복구 시간 측정
→ 재발 방지 방법 정리
```

## 시나리오 A. API Pod 삭제

```bash
kubectl delete pod <api-pod>
```

확인할 내용:

- ReplicaSet이 언제 새 Pod를 생성하는가
- 요청 오류가 발생하는가
- Pod가 Ready 상태가 되기까지 얼마나 걸리는가
- 단일 Replica와 다중 Replica의 차이는 무엇인가
- Service Endpoint에서 삭제된 Pod가 언제 제외되는가
- 로그와 Metrics에서 장애가 어떻게 표현되는가

## 시나리오 B. Worker VM 강제 종료

```text
worker-01 전원 종료
```

확인할 내용:

- Node가 언제 NotReady 상태가 되는가
- 해당 Node의 Pod는 어떤 상태로 남는가
- Pod가 다른 Worker에 언제 재스케줄링되는가
- 모든 Pod가 하나의 Node에 몰려 있지는 않았는가
- Pod Anti-Affinity 또는 Topology Spread가 필요한가
- 서비스 중단 시간은 얼마인가

## 시나리오 C. 계획된 Node 유지보수

```bash
kubectl cordon worker-01
kubectl drain worker-01 \
  --ignore-daemonsets \
  --delete-emptydir-data
```

강제 종료와 cordon/drain의 차이를 비교한다.

```text
강제 종료
→ 예고되지 않은 비자발적 장애
```

```text
cordon / drain
→ 계획된 유지보수
```

확인할 내용:

- cordon이 기존 Pod에 미치는 영향
- drain이 Eviction API를 사용하는 방식
- PodDisruptionBudget이 drain에 미치는 영향
- DaemonSet Pod가 제외되는 이유
- emptyDir 데이터가 삭제되는 이유

## 시나리오 D. OOMKilled

Pod의 Memory Limit을 낮추고 메모리를 많이 사용하는 요청을 발생시킨다.

확인할 내용:

- Container 종료 사유
- Exit Code
- Restart Count
- `kubectl logs --previous`
- CrashLoopBackOff 전환 과정
- Memory Request와 Limit의 차이
- Liveness Probe 실패와 OOMKilled의 차이

## 시나리오 E. CPU 과부하와 HPA

HPA는 Deployment와 같은 워크로드의 Replica 수를 관측된 Metrics에 따라 조정한다.

CPU 사용률 기반 HPA는 일반적으로 컨테이너의 `resources.requests.cpu`를 기준으로 사용률을 계산하므로 Request 설정이 중요하다.

```text
부하 증가
→ Pod CPU 사용량 증가
→ HPA Desired Replica 증가
→ ReplicaSet이 Pod 생성
→ Scheduler가 Worker에 Pod 배치
→ 새 Pod가 Ready 상태로 전환
→ 트래픽 분산
→ 부하 감소
→ Stabilization Window 이후 Scale-In
```

확인할 내용:

- Request가 없는 경우 HPA가 어떻게 동작하는가
- Scale-Out 반응 시간
- 새 Pod가 Ready 되기 전까지의 서비스 영향
- Scale-In Stabilization
- HPA가 증가해도 Node 자원이 부족하면 어떤 일이 발생하는가

## 시나리오 E-2. CPU가 보지 못하는 부하 — 커스텀 메트릭 기반 HPA

시나리오 E는 CPU 사용률이 실제 부하를 대표한다는 전제 위에 있다.
그런데 이 전제가 깨지는 경우가 실무에서 더 흔하다.

```text
CPU를 많이 쓴다  ≠  요청을 많이 받는다
```

CPU 기반 HPA가 반응하지 못하는 상황들이다.

```text
DB Connection Pool이 고갈되어 요청이 대기 중    → CPU는 한가함 (I/O 대기)
Redis Queue에 작업이 적체됨                    → Worker는 대기 중이라 CPU 낮음
외부 API 응답이 느려져 요청이 밀림              → 전부 대기 상태
요청 수는 폭증했으나 각 요청이 가벼움            → CPU는 거의 안 오름
```

**3단계에서 만든 애플리케이션이 정확히 이 상황을 만들 수 있도록 설계되어 있다.**
API → Redis Queue → Worker → PostgreSQL 구조는 전 구간이 I/O 중심이다.

### 실험 설계

```text
1. Worker의 작업 처리에 인위적 지연을 준다 (건당 2초)

2. k6로 요청을 투입한다

3. 관찰 — 여기가 핵심
     Redis Queue 길이       급증        ← 실제 부하
     작업 완료까지 소요시간   급증        ← 사용자 체감 장애
     Worker CPU 사용률      20% 수준     ← 한가하다
     CPU 기반 HPA           Replica 유지 ← 아무 반응 없음

4. Prometheus Adapter로 Queue 길이를 Custom Metrics API에 노출

5. Queue 길이 기반 HPA 적용
     → Worker Replica 증가 → Queue 소진 → 지연 정상화

6. 두 HPA의 반응을 Grafana에서 나란히 비교
```

3번이 이 실험의 목적이다. **지표는 멀쩡한데 서비스는 죽어 있는 상태**를 눈으로 확인한다.

### 확인할 내용

- CPU 사용률과 Queue 길이가 어떻게 다르게 움직이는가
- CPU 기반 HPA가 반응하지 않는 동안 사용자 체감은 어떠했는가
- Prometheus 지표가 Custom Metrics API에 어떤 형태로 노출되는가
  - `kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1`
- Queue 길이 기반 HPA의 Scale-Out 반응 시간
- 임계값을 어떻게 정해야 하는가 (너무 낮으면 진동, 너무 높으면 늦음)
- CPU와 커스텀 메트릭을 **함께** 쓸 수 있는가 (HPA는 여러 지표를 동시에 지정 가능)

### 웹 트래픽 기반 스케일링도 함께 확인

같은 방식으로 API Pod에 대해서도 실험한다.

```text
CPU 기반          부하가 CPU에 나타날 때만 반응
초당 요청 수 기반   요청량 자체에 반응
p95 응답시간 기반   사용자 체감 지연에 반응
```

**어떤 지표를 기준으로 삼느냐에 따라 스케일링 시점과 결과가 달라진다.**
같은 부하 패턴에서 세 가지 HPA가 각각 언제 반응하는지 비교 기록한다.

## 시나리오 F. 존재하지 않는 이미지 배포

```text
잘못된 Image Tag 배포
→ ErrImagePull
→ ImagePullBackOff
```

확인할 내용:

- Event 메시지
- Deployment Rollout 상태
- 기존 Pod 유지 여부
- maxUnavailable과 maxSurge
- `kubectl rollout status`
- `kubectl rollout undo`

## 시나리오 G. Readiness 실패

DB 연결이 끊기면 `/health/ready`가 실패하도록 구성한다.

```text
API Process: Running
Pod Phase: Running
Ready: False
Service Endpoint: 제외
```

이 실험을 통해 다음 차이를 이해한다.

> 프로세스가 실행 중인 것과 실제 서비스 요청을 처리할 준비가 된 것은 서로 다르다.

## 시나리오 H. 데이터 영속성

```text
PostgreSQL Pod 삭제
→ 새 Pod 생성
→ PVC 재연결
→ 기존 데이터 유지 여부 확인
```

확인할 내용:

- Pod와 Volume의 수명주기 차이
- StatefulSet Pod 이름 유지
- PVC Retention
- Node 변경 시 Volume 재연결
- 데이터 손상 가능성과 Backup 필요성

## 단계 결과물

장애별 Incident Report를 작성한다.

```text
장애 개요
영향 범위
발생 시각
탐지 시각
복구 시각
사용자 영향
Kubernetes 상태
Metrics 변화
Logs와 Events
근본 원인
복구 과정
재발 방지 대책
```

---

# 7단계. Helm Chart로 전환

순수 Manifest를 충분히 다룬 후 Helm으로 전환한다.

```text
charts/task-platform/
├─ Chart.yaml
├─ values.yaml
├─ values-local.yaml
├─ values-eks.yaml
└─ templates/
   ├─ api-deployment.yaml
   ├─ api-service.yaml
   ├─ worker-deployment.yaml
   ├─ configmap.yaml
   ├─ serviceaccount.yaml
   ├─ ingress.yaml
   ├─ hpa.yaml
   └─ pdb.yaml
```

환경별 차이는 Values 파일로 분리한다.

## values-local.yaml

```yaml
ingress:
  className: nginx

image:
  repository: local-registry/task-api

database:
  external: false
```

## values-eks.yaml

```yaml
ingress:
  className: alb

image:
  repository: <account-id>.dkr.ecr.ap-northeast-2.amazonaws.com/task-api

database:
  external: true
```

동일한 Chart를 로컬 Kubernetes와 AWS EKS에서 재사용하는 것이 핵심이다.

## 이 단계에서 확인할 내용

- Chart와 Release의 차이
- Values Merge 우선순위
- Template Function
- Named Template과 Helper
- Helm Upgrade와 Rollback
- Rendered Manifest 확인
- 환경별 설정 분리
- Secret 관리 한계

## 단계 결과물

- 재사용 가능한 Helm Chart
- Local과 EKS Values 분리
- Helm Upgrade와 Rollback 실험
- `helm template` 결과 분석
- Chart Version과 Application Version 정책

---

# 8단계. GitHub Actions 기반 CI 파이프라인 구축

GitOps를 구성하기 전에 CI 파이프라인을 먼저 만든다.

```text
Source Code Push
→ Unit Test
→ Static Analysis
→ Docker Image Build
→ Vulnerability Scan
→ Amazon ECR Push
→ GitOps Repository의 Image Tag 또는 Digest 수정
```

CI의 역할은 애플리케이션을 검증하고 배포 가능한 불변 Artifact를 만드는 것이다.

## 이미지 태그 정책

`latest` 태그는 사용하지 않는다.

잘못된 예:

```text
task-api:latest
```

권장 예:

```text
task-api:git-8f12a6c
```

또는 Image Digest를 사용할 수 있다.

```text
task-api@sha256:...
```

이렇게 구성해야 다음 질문에 답할 수 있다.

- 현재 운영 중인 코드는 어느 Git Commit인가
- 어떤 Container Image가 배포되었는가
- 이전 버전으로 어떻게 되돌아가는가
- 같은 이미지를 다시 배포했을 때 동일한 결과가 보장되는가
- 취약점 검사를 통과한 이미지인가

## 단계 결과물

- GitHub Actions Workflow
- Unit Test 자동화
- Docker Build 자동화
- Trivy 등의 Image Scan
- ECR Push
- Git Commit과 Image Tag 연결
- GitOps Repository 자동 업데이트

---

# 9단계. Argo CD를 이용한 로컬 GitOps 구성

EKS로 이동하기 전에 VMware Kubernetes에 Argo CD를 적용한다.

```text
Application Repository
→ GitHub Actions
→ Container Registry
→ GitOps Repository 수정
→ Argo CD
→ Local Kubernetes
```

## 자동 동기화

GitOps Repository 변경 후 Argo CD가 변경을 감지하여 자동으로 배포한다.

## Self Heal

다음과 같이 클러스터를 수동 변경한다.

```bash
kubectl scale deployment api --replicas=1
```

Git에는 Replica가 3으로 선언되어 있다면 다음 흐름이 발생한다.

```text
클러스터 수동 변경
→ Git Desired State와 차이 발생
→ Argo CD가 OutOfSync 감지
→ Self Heal 수행
→ Replica 3으로 복구
```

## Git Revert 기반 Rollback

```text
잘못된 버전 Commit
→ 배포 실패
→ 이전 Commit Revert
→ Argo CD Sync
→ 정상 버전 복구
```

## Drift 확인

```text
Git Desired State
≠
Cluster Actual State
```

GitOps의 핵심은 `kubectl apply`를 자동화하는 것이 아니다.

Git을 운영 상태의 기준으로 삼고, 실제 클러스터 상태가 Git의 선언과 일치하도록 지속적으로 조정하는 것이다.

## 단계 결과물

- Argo CD 설치
- Application과 AppProject 구성
- Auto Sync와 Self Heal 실험
- Prune 동작 실험
- Git Revert Rollback
- Drift 탐지 기록
- Sync 실패 원인 분석

---

# 10단계. Terraform 기반 AWS EKS 환경 구성

로컬 환경에서 Kubernetes와 GitOps 흐름을 충분히 이해한 후 AWS EKS로 확장한다.

```text
AWS
├─ VPC
│  ├─ Public Subnet
│  ├─ Private Subnet
│  ├─ Internet Gateway
│  ├─ NAT Gateway
│  └─ Route Table
├─ EKS Cluster
├─ Managed Node Group
├─ ECR
├─ IAM Role / Policy
├─ EKS Access Entry
├─ EKS Pod Identity
├─ Security Group
└─ EKS Add-on
```

## Terraform이 관리할 리소스

### AWS Infrastructure

- VPC
- Subnet
- Route Table
- Internet Gateway
- NAT Gateway
- EKS Cluster
- Managed Node Group
- ECR
- IAM Role과 Policy
- EKS Access Entry
- Pod Identity Association
- Security Group
- EKS Add-on

## Argo CD가 관리할 리소스

### Kubernetes Resources

- Namespace
- Deployment
- Service
- Ingress
- HPA
- PodDisruptionBudget
- NetworkPolicy
- Monitoring Stack
- Application

Terraform과 Argo CD가 동일한 Kubernetes 리소스를 동시에 관리하면 소유권 충돌이 발생할 수 있다.

따라서 기본 원칙은 다음과 같이 정한다.

```text
AWS Infrastructure는 Terraform
Kubernetes 내부 Application과 Platform Resource는 Argo CD
```

Argo CD 자체는 Terraform, Helm, Bootstrap Script 중 하나로 최초 설치할 수 있다.

이후 App of Apps 또는 ApplicationSet 구조를 사용하여 Platform 구성요소와 애플리케이션을 Argo CD가 관리하도록 확장한다.

## 단계 결과물

- Terraform Module 구조
- Dev와 Prod-like 환경 분리
- Remote State와 State Locking
- VPC와 EKS 구성도
- IAM Role과 Policy 설계
- EKS Access Entry 구성
- Argo CD Bootstrap 과정

---

# 11단계. EKS에서 추가로 학습할 AWS 요소

로컬 Kubernetes와 EKS의 차이는 단순히 클러스터가 실행되는 위치만 달라지는 것이 아니다.

EKS에서는 AWS Load Balancer, IAM, EBS, Auto Scaling Group, CloudWatch 등 AWS 서비스와 Kubernetes가 연결된다.

## AWS Load Balancer Controller

```text
Kubernetes Ingress 생성
→ AWS Load Balancer Controller가 Resource 감지
→ AWS API 호출
→ ALB 생성
→ Listener와 Target Group 구성
→ Pod 또는 Node로 트래픽 전달
```

확인할 내용:

- IngressClass
- Annotation
- IP Target과 Instance Target 차이
- ALB Health Check와 Kubernetes Readiness 차이
- Security Group 연결
- Public ALB와 Internal ALB

## EKS Pod Identity

애플리케이션 Container에 AWS Access Key를 직접 저장해서는 안 된다.

```text
Pod
→ Kubernetes ServiceAccount
→ EKS Pod Identity Association
→ IAM Role
→ AWS API 호출
```

예를 들어 API Pod가 S3에 접근해야 한다면 다음과 같이 구성한다.

```text
api-service-account
→ task-api-s3-role
→ 특정 S3 Bucket의 GetObject와 PutObject만 허용
```

이를 통해 Pod별 최소 권한을 적용한다.

## EBS CSI Driver

EBS CSI Driver를 사용하여 Kubernetes PersistentVolume과 AWS EBS Volume을 연결한다.

확인할 내용:

- StorageClass
- Dynamic Provisioning
- EBS Volume 생성과 삭제
- Availability Zone 제약
- Pod 재스케줄링 시 Volume Attach
- Stateful Workload의 한계

## PostgreSQL StatefulSet과 Amazon RDS 비교

### 학습 목적의 구성

```text
PostgreSQL StatefulSet
+ EBS PersistentVolumeClaim
```

### 운영형 구성

```text
Stateless API와 Worker on EKS
+ Amazon RDS for PostgreSQL
```

두 방식을 직접 비교한다.

비교 항목:

- 고가용성
- Backup과 Point-in-Time Recovery
- Patch와 Version Upgrade
- 장애 복구
- 운영 복잡도
- 비용
- 성능
- 데이터 책임 범위

## AWS Monitoring과 Alerting

EKS 내부 Metrics는 Prometheus로 수집하고 AWS 리소스 Metrics와 Events는 CloudWatch를 활용한다.

```text
Kubernetes / Application Metrics
→ Prometheus
→ Grafana
→ Alertmanager
```

```text
AWS Infrastructure Metrics / Logs / Events
→ CloudWatch
→ CloudWatch Alarm 또는 EventBridge
→ SNS
→ SES 또는 기타 알림 채널
```

Prometheus와 CloudWatch는 경쟁 관계라기보다 관측 대상과 책임 범위가 다른 도구로 이해한다.

---

# 12단계. EKS 장애 테스트와 오토스케일링

로컬에서 수행한 장애 실험을 EKS에서 다시 수행하고 결과 차이를 분석한다.

## Pod 삭제

동일한 Kubernetes Controller 동작을 확인한다.

```text
Pod 삭제
→ ReplicaSet이 신규 Pod 생성
→ Scheduler가 EKS Worker Node 선택
→ kubelet이 Container 실행
→ Readiness 통과
→ ALB Target Health 정상화
```

로컬 환경과 달리 ALB Target 등록과 Health Check 시간도 함께 확인한다.

## EC2 Worker 종료

EKS Managed Node Group은 EC2 Auto Scaling Group을 기반으로 Worker Node를 관리한다.

```text
EC2 Worker 1대 종료
→ Node NotReady
→ Pod 재스케줄링
→ Auto Scaling Group이 대체 EC2 생성
→ 새 EC2가 EKS Cluster에 Join
→ Desired Capacity 복구
```

확인할 내용:

- Node NotReady 감지 시간
- Pod Eviction과 재스케줄링 시간
- 새 EC2가 Ready 상태가 되는 시간
- 서비스 중단 시간
- PodDisruptionBudget과 Topology Spread의 효과

## HPA와 Node Autoscaling 비교

두 기능은 역할이 다르다.

```text
HPA
→ Pod 수를 늘리거나 줄임
```

```text
Cluster Autoscaler 또는 Karpenter
→ Node 수를 늘리거나 줄임
```

HPA로 Pod가 증가하더라도 Worker Node의 CPU와 Memory가 부족하면 Pod는 Pending 상태가 된다.

Node Autoscaler가 있어야 새로운 Node가 추가되고 Pending Pod가 배치될 수 있다.

## 권장 실험 순서

```text
1. HPA만 구성
2. Node 자원 부족으로 Pending Pod 재현
3. Cluster Autoscaler 구성
4. Node 증가와 Pod 스케줄링 확인
5. 부하 감소 후 Pod와 Node 축소 확인
6. 동일한 실험을 Karpenter로 수행
7. Cluster Autoscaler와 Karpenter의 동작 차이 비교
```

Karpenter는 마지막 심화 단계에서 다룬다.

처음부터 Karpenter를 적용하면 Managed Node Group, Scheduler, Pending Pod, Auto Scaling Group의 기본 관계를 이해하기 어려울 수 있다.

## 추가 EKS 장애 시나리오

- ALB Health Check 실패
- Security Group 오설정
- Pod Identity 권한 부족
- ECR Image Pull 권한 실패
- EBS Volume Attach 실패
- RDS 연결 실패
- NAT Gateway 또는 VPC Endpoint 경로 문제
- CoreDNS 장애
- CNI IP 부족
- Node Group Rolling Update
- Argo CD Sync 실패
- Terraform 잘못된 변경과 복구

## 단계 결과물

- EKS Incident Report
- 로컬 Kubernetes와 EKS 장애 차이 비교
- HPA와 Node Autoscaling 비교 자료
- Managed Node Group 복구 시간 측정
- ALB, IAM, EBS, RDS 연계 장애 분석
- 운영 개선안

---

# GitHub 저장소 구성

최종적으로 저장소는 역할별로 분리한다.

## 1. cloud-native-app

```text
cloud-native-app/
├─ api/
├─ worker/
├─ Dockerfile
├─ docker-compose.yaml
├─ tests/
└─ .github/workflows/
```

역할:

- 애플리케이션 Source Code
- Unit Test
- Docker Image Build
- Image Scan
- CI Workflow

## 2. cloud-native-infra

```text
cloud-native-infra/
├─ modules/
│  ├─ vpc/
│  ├─ eks/
│  ├─ ecr/
│  ├─ iam/
│  └─ monitoring/
└─ environments/
   ├─ dev/
   └─ prod-like/
```

역할:

- AWS Infrastructure
- Terraform Module
- 환경별 Variable
- Remote State

## 3. cloud-native-gitops

```text
cloud-native-gitops/
├─ apps/
├─ platform/
├─ environments/
│  ├─ local/
│  └─ eks/
└─ charts/
```

역할:

- Kubernetes Desired State
- Helm Chart
- 환경별 Values
- Argo CD Application
- Monitoring Stack

## 4. kubernetes-lab

```text
kubernetes-lab/
├─ kubeadm/
├─ ansible/
├─ exercises/
├─ incidents/
└─ docs/
```

역할:

- 로컬 Kubernetes 구축
- 반복 작업 자동화
- 오브젝트 실습
- 장애 실험
- 학습 문서

처음에는 하나의 Monorepo로 시작해도 괜찮다.

다만 프로젝트가 커지면 애플리케이션, 인프라, GitOps 선언의 변경 주기와 책임이 달라지므로 저장소를 분리하는 편이 전체 변경 흐름을 설명하기 쉽다.

---

# 프로젝트 완료 후 답할 수 있어야 하는 질문

이 프로젝트를 완료한 뒤에는 최소한 다음 질문에 스스로 답할 수 있어야 한다.

## Kubernetes

- Deployment를 생성하면 내부에서 어떤 순서로 Pod가 실행되는가
- Pod가 Running인데도 서비스 요청을 받지 못하는 이유는 무엇인가
- API Server가 중단되면 기존 Pod와 신규 배포는 어떻게 달라지는가
- Node가 종료되었을 때 Pod는 언제 다른 Node로 이동하는가
- Request와 Limit은 Scheduler와 Runtime에 각각 어떤 영향을 미치는가
- HPA가 Replica 수를 계산하는 기준은 무엇인가
- StatefulSet과 Deployment는 무엇이 다른가

## CI/CD와 GitOps

- CI와 CD의 책임은 어떻게 나누는가
- GitHub Actions와 Argo CD는 각각 무엇을 관리하는가
- GitOps에서 Git이 Source of Truth라는 의미는 무엇인가
- 클러스터를 수동 변경하면 Argo CD는 어떻게 반응하는가
- 잘못된 배포를 어떤 방식으로 Rollback하는가
- 현재 운영 중인 Commit과 Image를 어떻게 추적하는가

## Observability

- Metrics, Logs, Events는 각각 어떤 정보를 제공하는가
- Pod 장애가 사용자 요청에 미친 영향을 어떻게 확인하는가
- 오류율과 응답시간은 어떤 방식으로 측정하는가
- Infrastructure Metric과 Application Metric의 차이는 무엇인가
- Alert는 어떤 조건에서 발생하도록 설계해야 하는가
- **CPU와 Memory는 정상인데 서비스가 느린 상황을 어떻게 탐지하는가**
- Metrics Server와 Prometheus는 각각 무엇을 담당하는가
- 애플리케이션 지표를 HPA가 읽게 하려면 무엇이 필요한가

## AWS EKS

- EKS가 Kubernetes에서 대신 관리해주는 영역은 무엇인가
- AWS Load Balancer Controller가 ALB를 만드는 과정은 무엇인가
- Pod가 AWS API에 안전하게 접근하는 방법은 무엇인가
- EBS Volume이 특정 Availability Zone에 종속되는 이유는 무엇인가
- HPA와 Karpenter는 각각 어떤 문제를 해결하는가
- Prometheus와 CloudWatch를 함께 사용하는 이유는 무엇인가

---

# 마무리

이 프로젝트의 핵심은 많은 도구를 설치해보는 것이 아니다.

Kubernetes, Helm, GitHub Actions, Argo CD, Terraform, Prometheus, Grafana, Loki, AWS EKS가 각각 어떤 문제를 해결하며 서로 어떻게 연결되는지를 이해하는 것이 중요하다.

또한 정상적인 배포만 경험해서는 운영 역량을 충분히 쌓기 어렵다.

직접 장애를 발생시키고, Metrics와 Logs를 통해 문제를 확인하고, Kubernetes와 AWS가 어떤 방식으로 복구하는지 분석해야 한다.

최종적으로는 다음 전체 흐름을 스스로 설계하고 설명할 수 있는 상태를 목표로 한다.

```text
애플리케이션 개발
→ 테스트
→ 컨테이너 이미지 생성
→ Registry 저장
→ GitOps Repository 변경
→ Kubernetes 배포
→ Metrics와 Logs 수집
→ 장애 탐지
→ 원인 분석
→ 자동 또는 수동 복구
→ 재발 방지
```

실무에서 Cloud Native 환경을 경험할 기회가 부족하더라도, 충분히 구체적인 목표와 장애 시나리오를 설계한다면 개인 환경에서도 운영에 가까운 경험을 만들 수 있다.

이 프로젝트의 모든 과정은 GitHub와 블로그에 기록하여 단순히 Kubernetes를 공부했다는 말이 아니라, 직접 구축하고 관찰하고 장애를 분석한 결과로 역량을 증명할 계획이다.

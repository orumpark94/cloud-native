# 2단계. Kubernetes 오브젝트와 내부 동작 학습

`cloud-native-learning-roadmap.md` **2단계**의 작업 기록이다. **2026-08-25 완료.**

## 목표

오브젝트의 정의를 외우는 것이 아니다. **오브젝트를 만들었을 때 어떤 Controller가 반응하고, 어떤 상태가 etcd에 저장되며, 실제 컨테이너가 어느 과정을 거쳐 실행되는지**를 확인하는 것이 목표였다.

로드맵이 제시한 Deployment 생성 흐름에서, 시작할 때 `❌`와 `△`였던 칸을 전부 채웠다.

```text
사용자가 Deployment 생성 요청
→ API Server 가 인증, 인가, Admission, 검증        ✅ 1단계 + 08 (RBAC 로 확장)
→ Desired State 를 etcd 에 저장                    ✅ 1단계 + 06 (평문 저장 실측)
→ Deployment Controller 가 ReplicaSet 생성          ✅ 02
→ ReplicaSet Controller 가 Pod 생성                 ✅ 01
→ Scheduler 가 Pod 를 실행할 Node 선택              ✅ 09·10·12 (볼륨·taint·자동 주입)
→ 선택된 Node 의 kubelet 이 PodSpec 확인            ✅ 1단계 + 12 (Static Pod 와의 경계)
→ kubelet 이 containerd 에 컨테이너 생성 요청        ✅ 1단계 + 12 (층의 분리)
→ CNI 가 Pod Network 구성                          ✅ 03 (규칙을 열어봄) + 12 (hostNetwork)
→ CSI 또는 Volume Plugin 이 Volume 연결             ✅ 09 (누가 포맷하나) + 13 (RWO 의 단위)
→ kubelet 이 Pod 상태를 API Server 에 보고           ✅ 1단계 + 10 (보고가 끊기면)
```

## 1단계에서 확인하지 못한 채 넘어간 것 — 셋 다 확인 완료

1단계 장애 실험에서 **결론은 냈는데 근거를 직접 보지 못한 것**이 셋 있었다. 관측 도구가 없어 `kubectl get` 수준에서 멈췄기 때문이다.

| # | 1단계에서 이렇게 결론냈다 | 무엇이 부족했나 | 확인한 문서 | 상태 |
|---|---|---|---|---|
| 1 | "kube-proxy를 죽여도 트래픽이 안 끊긴 것은, 규칙이 이미 커널에 들어가 있기 때문이다" | **그 규칙을 한 번도 열어본 적이 없다.** 설명이 그럴듯할 뿐 확인한 게 없었다 | [03-service.md](03-service.md) | ✅ 확인 |
| 2 | "선언은 4개인데 13분 동안 6개가 돌았다. Deployment가 그렇게 하도록 만들어졌다" | **왜 그런 선택을 하는지 설명하지 못했다.** 다른 워크로드는 어떻게 다른지도 몰랐다 | [10-statefulset.md](10-statefulset.md) | ✅ 확인 |
| 3 | "데이터베이스는 Kubernetes 밖에 두는 편이 낫다" | **스토리지를 하나도 안 본 상태의 판단이었다.** 볼륨이 어떻게 붙고 노드가 죽으면 어떻게 되는지 모르고 내린 결론 | [11-storage.md](11-storage.md) | ✅ 확인 |

**1번은 두 단계로 확인했다.** 규칙을 직접 열어본 것(4절)에 더해, **kube-proxy를 다시 죽여** 73초간 무중단임을 시계로 쟀다(10절). 그리고 그 결론이 절반만 맞다는 것 — Pod가 하나라도 바뀌면 요청의 1/3이 실패한다 — 까지 확인했다.

**2번은 같은 조건에서 Deployment와 StatefulSet을 나란히 놓고 재현했다.** worker02 전원을 강제로 내려 "선언 4개인데 목록에 6개"를 그대로 만들어냈고, 그 원인이 컨트롤러의 셈법 차이임을 확인했다.

**3번은 결론이 유지되되 근거가 완전히 바뀌었다.** "데이터가 사라진다"는 근거는 무너졌고, 대신 "노드가 죽으면 옮길 수 없다 / 아무도 안 알려준다 / StatefulSet만으로는 페일오버가 안 된다"가 진짜 이유로 드러났다. **기술적 불가능이 아니라 운영 비용의 문제**로 성격이 바뀌었다.

---

## 문서 목록

| 문서 | 오브젝트 | 핵심 발견 | 블로그 원고 |
|---|---|---|---|
| [00-pod.md](00-pod.md) | Pod | 생성 전 과정 추적, 종료 절차, initContainer | 2026-08-11 |
| [01-replicaset.md](01-replicaset.md) | ReplicaSet | ownerReference, **라벨 셀렉터로 소유권을 판단한다** | 2026-08-13 |
| [02-deployment.md](02-deployment.md) | Deployment | 컨트롤러 사슬, revision, maxSurge/maxUnavailable | 2026-08-13-(2) |
| [03-service.md](03-service.md) | Service | **iptables 체인 전체를 열어봄. kube-proxy 죽여도 73초 무중단** ★ | 2026-08-14 |
| [04-endpointslice.md](04-endpointslice.md) | EndpointSlice | ready/serving/terminating, probe가 성공해도 실제로는 503 | 2026-08-20 |
| [05-ingress.md](05-ingress.md) | Ingress | 컨트롤러 없으면 오브젝트만 존재. NodePort에 리스닝 프로세스가 없다 | 2026-08-20-(2) |
| [06-configmap-secret.md](06-configmap-secret.md) | ConfigMap / Secret | **etcd에 Secret이 평문으로 저장돼 있다** ★ / `..data` 심볼릭 링크 교체 | 2026-08-20-(3) |
| [07-namespace.md](07-namespace.md) | Namespace | 네임스페이스는 이름표일 뿐. **다만 오브젝트 참조는 못 넘는다** | 2026-08-20-(4) |
| [08-serviceaccount-rbac.md](08-serviceaccount-rbac.md) | ServiceAccount / RBAC | Role만으로는 안 되고 RoleBinding까지. **403→200을 재시작 없이** | 2026-08-21 |
| [09-pv-pvc.md](09-pv-pvc.md) | PV / PVC | **hostPath는 다른 노드에서 조용히 빈 디렉터리를 준다** ★ / 파일시스템은 누가 만드나 | 2026-08-21-(2) |
| [10-statefulset.md](10-statefulset.md) | StatefulSet | **노드 장애 시 Deployment 6개 vs StatefulSet 3개** ★ / 이름이 고정되면 디스크가 따라온다 | 2026-08-21-(3) |
| [11-storage.md](11-storage.md) | (종합) | 1단계 결론 3 재검토. **"가능하다. 감당할 수 있느냐다"** | 2026-08-24 |
| [12-daemonset.md](12-daemonset.md) | DaemonSet | **DESIRED 0인데 모든 지표가 정상** ★ / tolerations·nodeAffinity 자동 주입 | 2026-08-24-(2) |
| [13-job-cronjob.md](13-job-cronjob.md) | Job / CronJob | **OnFailure는 실패 로그를 잃는다** ★ / CronJob 기본 시간대가 UTC | 2026-08-25 |

> 블로그 원고는 [../작업다이어리/02.k8s-objects/](../작업다이어리/02.k8s-objects/) 에 있다.

---

## 이 단계를 관통한 발견 — 조용한 실패 ★★★

**오브젝트마다 따로 발견한 것인데 결국 같은 문제였다.**

| 문서 | 무슨 일이 일어났나 | 그런데 화면에는 |
|---|---|---|
| 09 | Pod가 다른 노드에 떠서 빈 디렉터리를 봤다 | Pod Running / PVC Bound / 이벤트 없음 |
| 10 | db-1이 6분째 뜨지 못하고 멈춰 있었다 | 이벤트가 하나도 없음. READY 2/3만 |
| 12 | 라벨 오타로 로그 수집기가 전부 사라졌다 | DESIRED 0 / READY 0 — 숫자상 완전 정상 |
| 13 | 백업 Job이 실패했다 | 기록은 남지만 아무도 안 봄 |

```text
공통점은 "에러가 안 난다" 는 것이다
실패가 실패처럼 보이지 않는다

kubectl get 으로는 안 보인다
→ 무엇을 봐야 하는지를 미리 정해두지 않으면 모른다
```

**이것이 로드맵 원칙 2 — "장애 실험 전에 관측 환경을 먼저 구성한다" — 의 근거다.** 우리가 직접 네 번 겪었다.

### 5단계에서 감시할 항목 (여기서 도출됨)

```text
[09]  PVC 가 Bound 인데 Pod 가 실제로 데이터를 읽는지
      → 앱 수준의 헬스체크가 필요하다. 오브젝트 상태로는 알 수 없다

[10]  StatefulSet 의 READY < replicas 가 일정 시간 이상 지속
      → 이벤트가 없으므로 숫자를 직접 봐야 한다

[12]  DaemonSet 의 DESIRED 를 노드 수와 비교
      → "READY < DESIRED" 만 보면 DESIRED 0 을 못 잡는다

[13]  kube_job_status_failed
      CronJob 이 예정된 시각에 안 돈 경우도 감지
```

---

## 반복해서 나온 구조

오브젝트가 달라도 같은 설계가 계속 나왔다.

### 1. "원하는 것" 과 "실제" 를 나눈다

```text
Service      "app=web 인 Pod 로"      EndpointSlice  "지금 그건 이 IP 셋"
Deployment   "이 template 을 3개"     ReplicaSet     "지금 이 Pod 셋이 그것"
PVC          "10Gi RWO 하나"          PV             "그건 이 디스크다"
```

**셋 다 짝이 없으면 기다린다.** 그리고 그 둘을 잇는 컨트롤러가 따로 있다.

### 2. 선언이 먼저 바뀌고 상태가 뒤따른다

```text
[09]  PVC   spec.volumeName 을 먼저 쓰고 → status 를 Bound 로
[12]  DS    spec.nodeSelector 가 먼저 바뀌고 → DESIRED 를 다시 계산
```

`-w` 로 보지 않으면 놓치는 순간이다.

### 3. 선언에는 "무엇을", 컨트롤러가 "어떻게"

```text
Deployment    "4개"           → 어디에 둘지는 스케줄러가
StatefulSet   "3개, 순서대로"   → 이름과 볼륨 연결은 컨트롤러가
DaemonSet     (개수 없음)      → 노드를 세는 건 컨트롤러가
PVC           "10Gi RWO"      → 어느 디스크인지는 컨트롤러가
```

**`kind` 한 줄이 담당 컨트롤러를 정한다.** "노드마다 하나씩" 같은 동작은 yaml에 없다.

### 4. 안전한 쪽이 기본값이다

```text
[09]  Released PV 를 자동 재사용하지 않는다     남의 데이터를 볼 위험 > 사람이 확인하는 불편
[10]  축소해도 PVC 를 안 지운다                디스크가 남는 불편 > 데이터 소실
[13]  restartPolicy 에 기본값이 없다           어느 쪽을 기본으로 둬도 위험하다
```

### 5. 컨트롤러 사슬은 대부분 1단이다

```text
Deployment  → ReplicaSet → Pod    2단   ← 두 세대를 동시에 굴려야 하므로
StatefulSet → Pod                 1단   ← 순번이 곧 진행 상황
DaemonSet   → Pod                 1단   ← 노드가 곧 신원
Job         → Pod                 1단   ← 세대 개념이 없다
Static Pod  → (주인이 Node)              ← 컨트롤러가 없다
```

**단수는 "오브젝트를 몇 번 거치느냐" 일 뿐이다.** 컨테이너를 만드는 건 언제나 kubelet과 containerd다.

### 6. 닭과 달걀을 층마다 다르게 푼다

```text
Static Pod    apiserver 를 띄우려면 apiserver 가 필요 → 파일로 우회
hostNetwork   CNI 를 설치하려면 CNI 가 필요 → 노드 네트워크로 우회
```

---

## 3단계로 넘기는 것

### 바로 쓸 수 있는 것

```text
[13편에서 만든 백업 CronJob 구조]
  PVC 를 readOnly 로 마운트 → tar → 저장
  concurrencyPolicy: Forbid / restartPolicy: Never / timeZone: Asia/Seoul

  → PostgreSQL 로 바꾸고 pg_dump 를 쓰면 된다

[10편에서 만든 local PV + nodeAffinity 구성]
  → PostgreSQL StatefulSet 에 그대로 적용
```

### 반드시 채워야 하는 것

```text
[백업]  13편 7절에서 다섯 가지 문제를 확인했다
  1. 원본과 백업이 같은 노드에 있다      → 다른 곳에 둔다
  2. 보관 정책이 없다                   → find -mtime +N -delete
  3. 복구를 검증한 적이 없다             → 복구 리허설
  4. 파일 복사로는 정합성이 안 맞는다     → pg_dump
  5. 실패를 아무도 모른다                → 5단계
```

### 현재 클러스터의 제약 (11편에서 정리)

```text
worker 가 2대뿐이다        복제본 3개를 서로 다른 노드에 못 둔다
StorageClass 가 없다       PV 를 손으로 만들어야 한다
control-plane 이 1대다     master01 이 죽으면 클러스터 전체가 멈춘다
백업 체계가 없다           3단계에서 만든다
```

### 단계별 결정 (11편 5절)

```text
3~6단계   클러스터 안 StatefulSet 으로 직접 굴린다. 백업도 직접 만든다
10단계    EKS + RDS 로 옮기며 무엇이 사라지고 무엇이 생기는지 비교한다
          → "직접 운영 vs 관리형" 비교 문서를 10단계 결과물로 남긴다
```

---

## 다음 단계에서 다룰 것

```text
3단계    애플리케이션 개발 + 컨테이너화
         PostgreSQL StatefulSet + 백업 CronJob + 복구 절차

4단계    CI/CD 와 GitOps
         → 12편에서 나온 문제: "정의의 원본이 어디에 있는가"
           kube-proxy 는 yaml 파일이 아예 없다. etcd 가 원본이다
           ~/manifests 도 master01 한 대에만 있다
         Ingress Controller 설치도 여기서

5단계    Observability
         → 위 "조용한 실패" 네 개를 여기서 해결한다
         Prometheus 자체가 StatefulSet 대상이기도 하다 (10편)

7단계 이후  Helm / CRD / Operator
         → 11편에서 예고: StatefulSet 위에 한 층이 더 있다
```

---

## 각 문서의 공통 형식

로드맵 "단계 결과물"이 요구하는 7항목을 담는다.

```text
1. 오브젝트의 역할
2. 생성 시 동작하는 Controller
3. 주요 Spec 과 Status 필드
4. 다른 오브젝트와의 연결 관계
5. 장애 또는 잘못된 설정 사례      ← 직접 만들어본다
6. 확인 명령어
7. 운영 시 주의할 점
```

**5번을 반드시 직접 만들었다.** 1단계에서 "실패한 출력 자체가 학습 대상"이었던 것과 같은 이유다. 그리고 실제로 **가장 값어치 있는 발견은 전부 5번에서 나왔다.**

## 이 단계에서 하지 않은 것

```text
Helm                    7단계
Ingress Controller 설치  4단계. 여기서는 "없으면 어떻게 되는가" 까지만
Metrics Server          5단계
Operator / CRD          7단계 이후
애플리케이션 개발        3단계
```

## 실습 환경

1단계에서 구축한 클러스터를 그대로 썼다.

```text
master01  192.168.8.143  control-plane   Kubernetes v1.35.7
worker01  192.168.8.142  worker          containerd 2.2.1
worker02  192.168.8.141  worker          Calico v3.32.1 (IPIP)
```

- `kubectl`은 **master01에서만** 사용한다.
- 실습용 리소스는 `k8s-lab` 네임스페이스에 만들었고, **2단계 종료 시 정리했다.**
- 상세 환경은 [../01.local-cluster/00-environment.md](../01.local-cluster/00-environment.md) 참조.

### 실습 중 노드에 만든 것

```text
worker01   /mnt/disks/vol-a   /mnt/disks/vol-b   local PV 실습용
           /mnt/backup                            13편 백업 실습용
worker02   /mnt/disks/vol-c

→ hostPath 와 local 볼륨은 Kubernetes 가 지우지 않는다
  필요 없으면 노드에서 직접 지운다
```

## 작업 원칙

1단계와 같다.

1. **명령은 직접 실행한다.** AI 도우미는 실행할 명령과 그 이유, 정상 출력의 모습을 제시한다.
2. **예상과 다른 출력이 나오면 우회하지 않는다.** 원인을 먼저 분석한다.
3. **실제 출력을 문서에 남긴다.** 명령만 적힌 문서는 재현할 때 쓸모가 없다.
4. 오브젝트 하나를 마치면 **번호 문서와 블로그 원고를 모두** 작성한다.

### 이 단계에서 지킨 것 하나 더

**틀린 것은 정정 표시와 함께 남겼다.** 지운 게 아니라 무엇이 틀렸고 왜 틀렸는지를 적었다.

```text
03  "응답은 직행한다" → 실제 비대칭은 터널 대 평문이다
05  "NodePort 가 ClusterIP 위에 쌓인다" → 병렬 진입점이 KUBE-SVC 에서 합류한다
09  "컨트롤러가 12~15초 주기 루프라 늦다" → 이벤트에 즉시 반응한다
12  "kube-proxy 가 특별해서 축출 안 된다" → 모든 DaemonSet Pod 가 그렇다
```

**틀린 추론을 남겨두는 것이 나중에 더 쓸모 있다.** 왜 그렇게 생각했는지가 함께 남기 때문이다.

# Phase 0. 준비 — 자원 정리, helm, StorageClass

`05.local-monitoring/README.md` 의 **Phase 0** 작업 기록이다. 2026-08-31.

## 목적

모니터링 스택을 올리기 전에 세 가지를 갖춘다.

```text
1. 자원          스택이 들어갈 여유가 있는가
2. helm          kube-prometheus-stack 을 설치할 도구
3. StorageClass  Prometheus 가 시계열을 쓸 저장소
```

---

## 1. 노드 자원 확인

```bash
kubectl describe node worker01 | grep -A8 "Allocated resources"
kubectl describe node worker02 | grep -A8 "Allocated resources"
```

```text
worker01
  cpu     550m (27%)   Limits 1 (50%)
  memory  454Mi (12%)  Limits 938Mi (24%)

worker02
  cpu     500m (25%)   Limits 1300m (65%)
  memory  320Mi (8%)   Limits 768Mi (20%)
```

```text
[모니터링 스택이 요구하는 것 — 대략]
  Prometheus         1~2 GB
  Grafana            ~200 MB
  Alertmanager       ~100 MB
  kube-state-metrics ~100 MB
  node-exporter      노드마다 ~50 MB
  Loki + Promtail    ~500 MB
```

```text
[판단]
  memory 8~12% 면 Prometheus 1~2GB 를 넣어도 40~50% 선이다
  → worker VM 메모리(4GB)를 늘릴 필요가 없다
  → 대신 values.yaml 로 retention 과 scrape interval 을 조인다
```

### 디스크

```bash
# worker01, worker02 각각
df -h /
```

```text
worker01   /dev/mapper/ubuntu--vg-ubuntu--lv   24G  6.5G  16G  29%
worker02   /dev/mapper/ubuntu--vg-ubuntu--lv   24G  6.1G  17G  28%
```

```text
local-path 는 노드의 루트 파티션(/opt)을 쓴다
→ 16~17G 여유. Prometheus 2일치는 충분하다
```

---

## 2. bookstore-lab 정리

4단계 실습용 네임스페이스가 `cpu 900m / memory 1500Mi` 를 예약하고 있었다.

```bash
kubectl get all,ingress,cm,secret -n bookstore-lab   # 지워질 것을 먼저 확인
kubectl delete namespace bookstore-lab
```

```bash
kubectl get ns
```

```text
NAME              STATUS   AGE
bookstore         Active   4d21h
default           Active   27d
kube-node-lease   Active   27d
kube-public       Active   27d
kube-system       Active   27d
```

```text
[손실 없음]
  매니페스트 7개가 k8s-lab/ 에 있고 Git 에 커밋돼 있다
  → 되살리기: kubectl apply -f k8s-lab/
```

```text
[남겨뒀던 labingress 도 함께 사라졌다]
  "Ingress Controller 를 설치하면 갑자기 동작하는 걸 본다" 는 계획이었다
  → 그때 k8s-lab/07-ingress.yaml 을 다시 apply 하면 된다
```

---

## 3. helm 설치 (master01)

```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh
helm version
```

```text
Downloading https://get.helm.sh/helm-v3.21.4-linux-amd64.tar.gz
Verifying checksum... Done.
Preparing to install helm into /usr/local/bin
helm installed into /usr/local/bin/helm

version.BuildInfo{Version:"v3.21.4", GitCommit:"813176c51bb...", GoVersion:"go1.26.5"}
```

```text
[master01 에만 설치한다]
  helm 은 kubeconfig 를 그대로 쓴다
  → kubectl 이 되는 곳이면 helm 도 된다
  → worker 에는 필요 없다
```

---

## 4. StorageClass 구성

Prometheus 는 시계열을 디스크에 쓴다. PV 가 필요한데 클러스터에 StorageClass 가 없었다.

```bash
kubectl get storageclass
```

```text
No resources found
```

```text
4단계 방식(PV 를 손으로 작성)으로도 할 수는 있다
→ 그런데 앞으로 Grafana, Alertmanager, Loki 까지 붙는다
→ 그때마다 노드에 접속해 mkdir 을 할 수는 없다
→ 동적 프로비저닝을 먼저 구성한다
```

**작업 내용과 검증 기록은 [04.k8s-manifest/03-dynamic-provisioning.md](../04.k8s-manifest/03-dynamic-provisioning.md) 에 정리했다.** 스토리지 주제라 4단계 문서로 뺐다.

### 결과만 옮기면

```bash
kubectl get storageclass
```

```text
NAME         PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION
local-path   rancher.io/local-path   Delete          WaitForFirstConsumer   false
```

```text
[모니터링 스택에 영향을 주는 성질 셋]

  기본 StorageClass 가 아니다
    → values.yaml 에 storageClassName: local-path 를 명시해야 한다

  reclaimPolicy 가 Delete 다
    → Prometheus 를 재설치하면 지표가 날아간다

  local 저장소다
    → PV 가 특정 노드에 묶인다
    → Prometheus Pod 도 그 노드에 묶인다
    → 그 노드가 죽으면 지표를 못 본다
       ★ 관측 도구가 관측 대상과 함께 죽는 구조다
       → 6단계 노드 장애 실험에서 다시 볼 지점
```

---

## 5. 겪은 문제 — kubectl 기본 네임스페이스

검증 중에 `apply` 는 성공했는데 `get` 이 NotFound 를 냈다.

```bash
kubectl config view --minify | grep namespace
```

```text
    namespace: bookstore
```

```text
컨텍스트의 기본 네임스페이스가 bookstore 로 잡혀 있었다
→ 앞으로 monitoring 네임스페이스를 다룰 때 계속 문제가 된다
```

```bash
kubectl config set-context --current --namespace=default
```

```text
Context "kubernetes-admin@kubernetes" modified.
```

```text
[상세 경위는 03-dynamic-provisioning.md 의 5절에 있다]
```

---

## Phase 0 결과

```text
  자원                     cpu 25~27% / memory 8~12%      ✓
  디스크                    16~17G 여유                    ✓
  bookstore-lab            삭제. 900m/1500Mi 회수          ✓
  helm                     v3.21.4                        ✓
  StorageClass             local-path                     ✓
                           Delete / WaitForFirstConsumer
  kubectl 기본 네임스페이스   bookstore → default            수정
```

## Phase 1 로 넘어가기 전 확인

```bash
kubectl get storageclass
kubectl get po -n local-path-storage
helm version
kubectl config view --minify | grep namespace

# 지금은 안 되는 것 — Phase 1 에서 해결한다
kubectl top nodes
```

```text
[예상]
  error: Metrics API not available
  → 4단계 8번 글에서 실제 메모리를 못 잰 원인이 이것이다
```

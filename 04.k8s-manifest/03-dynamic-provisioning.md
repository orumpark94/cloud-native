# 03. 동적 프로비저닝 — StorageClass 도입

`04.k8s-manifest/README.md` 의 후속 작업 기록이다. 2026-09-01.

## 목적

4단계에서는 PV 를 손으로 만들었다. `01-manifests.md` 의 `03-postgres-pv.yaml` 이 그것이다.

```text
[손으로 만드는 방식의 한계]

  1. 노드에 접속해 mkdir 을 해야 한다
  2. PV 매니페스트를 30줄 써야 한다
  3. 어느 노드에 만들지 사람이 미리 정해야 한다
     → nodeAffinity 를 손으로 적으므로
     → Pod 가 그 노드에만 뜰 수 있다
```

저장소가 필요한 워크로드가 늘어나면 이 과정을 반복하게 된다. **PVC 만 만들면 PV 가 자동으로 생기는 구조**를 만든다.

```text
[진행 시점]
  이 작업은 5단계 준비 중에 했다 (Prometheus 가 저장소를 필요로 해서)
  → 그때의 시간순 기록은 05.local-monitoring/00-preparation.md 에 있다
  → 이 문서는 스토리지 주제만 떼어 정리한 것이다
```

---

## 1. 사전 상태

```bash
kubectl get storageclass
```

```text
No resources found
```

```bash
kubectl get pv
```

```text
NAME               CAPACITY  ACCESS  RECLAIM  STATUS  CLAIM                      STORAGECLASS
bookstore-pgdata   5Gi       RWO     Retain   Bound   bookstore/data-postgres-0  (비어 있음)
                                                                                  └───┬───┘
                                                                          StorageClass 없이 만든 PV
```

```text
StorageClass 가 하나도 없다
→ PVC 를 만들어도 PV 를 만들어줄 주체가 없다
→ 그래서 손으로 만들었던 것이다
```

---

## 2. local-path-provisioner — 먼저 읽는다

Rancher 의 `local-path-provisioner` 를 쓴다. 186줄이라 읽고 적용한다.

```bash
curl -fsSL -o local-path-storage.yaml \
  https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml

wc -l local-path-storage.yaml
grep -n "^kind:\|^  name:" local-path-storage.yaml
```

```text
186 local-path-storage.yaml

  2  Namespace              local-path-storage
  8  ServiceAccount         local-path-provisioner-service-account
 15  Role                   local-path-provisioner-role
 26  ClusterRole            local-path-provisioner-role
 45  RoleBinding
 60  ClusterRoleBinding
 74  Deployment             local-path-provisioner
141  StorageClass           local-path
149  ConfigMap              local-path-config
```

```text
4단계에서 다룬 오브젝트만 나온다
Deployment / ConfigMap / ServiceAccount / RBAC
```

### 2-1. StorageClass

```bash
sed -n '138,146p' local-path-storage.yaml
```

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: local-path
provisioner: rancher.io/local-path
volumeBindingMode: WaitForFirstConsumer
reclaimPolicy: Delete
```

```text
provisioner            이름표. 프로비저너가 이걸 보고 자기 일인지 판단한다
                       → ingressClassName 과 같은 발상

volumeBindingMode      WaitForFirstConsumer
                       Pod 가 스케줄될 때까지 기다렸다 그 노드에 만든다
                       → local 저장소에 필수

reclaimPolicy: Delete  PVC 를 지우면 PV 와 데이터가 함께 사라진다
                       → bookstore-pgdata 는 Retain 이었다

is-default-class 어노테이션 없음
                       → PVC 에서 storageClassName 을 매번 적어야 한다
```

### 2-2. ConfigMap

```bash
sed -n '149,186p' local-path-storage.yaml
```

```text
  nodePathMap
    node   DEFAULT_PATH_FOR_NON_LISTED_NODES
    paths  ["/opt/local-path-provisioner"]

  setup     mkdir -m 0777 -p "$VOL_DIR"
  teardown  rm -rf "$VOL_DIR"

  helperPod.yaml
    kind: Pod
    image: docker.io/library/busybox
    priorityClassName: system-node-critical
    tolerations: node.kubernetes.io/disk-pressure
```

```text
[helper Pod 가 필요한 이유]

  프로비저너 Pod 는 노드 하나에만 떠 있다
  PV 는 아무 노드에나 만들어야 한다
  → 다른 노드의 파일시스템에 접근할 방법이 없다
  → 대상 노드에 busybox Pod 를 띄워 mkdir 을 시키고 지운다

[toleration 과 priorityClassName 이 붙은 이유]
  디스크가 부족한 노드에도 들어가야 teardown 을 할 수 있다
  자원이 부족해도 쫓겨나면 안 된다
```

### 2-3. 권한

```bash
sed -n '15,25p' local-path-storage.yaml     # Role
sed -n '26,44p' local-path-storage.yaml     # ClusterRole
```

```yaml
# Role — local-path-storage 네임스페이스 안에서만
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get","list","watch","create","patch","update","delete"]
```

```yaml
# ClusterRole — 클러스터 전체
  - resources: ["nodes","persistentvolumeclaims","configmaps","pods","pods/log"]
    verbs: ["get","list","watch"]
  - resources: ["persistentvolumes"]
    verbs: ["get","list","watch","create","patch","update","delete"]
  - resources: ["events"]
    verbs: ["create","patch"]
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["get","list","watch"]
```

```text
[persistentvolumes 의 create 가 자동 생성의 실체다]
  사람 대신 이 Pod 가 PV 오브젝트를 만들 수 있게 허용한 것

[권한을 두 겹으로 나눈 이유]
  PV 는 클러스터 범위 오브젝트다 → ClusterRole 이 필요
  helper Pod 는 자기 네임스페이스에만 띄우면 된다 → Role 로 좁힘
  → ClusterRole 의 pods 에는 create 가 없다

[권한 목록이 곧 동작 설명서다]
  pvc watch          새 PVC 가 생기는지 지켜본다
  storageclasses get 그 PVC 가 내 StorageClass 를 쓰는지 확인
  nodes get          어느 노드에 만들지 판단
  pods/log get       helper Pod 의 결과를 확인
  events create      진행 상황을 이벤트로 남긴다
```

---

## 3. 적용

```bash
kubectl apply -f local-path-storage.yaml
```

```text
namespace/local-path-storage created
serviceaccount/local-path-provisioner-service-account created
role.rbac.authorization.k8s.io/local-path-provisioner-role created
clusterrole.rbac.authorization.k8s.io/local-path-provisioner-role created
rolebinding.rbac.authorization.k8s.io/local-path-provisioner-bind created
clusterrolebinding.rbac.authorization.k8s.io/local-path-provisioner-bind created
deployment.apps/local-path-provisioner created
storageclass.storage.k8s.io/local-path created
configmap/local-path-config created
```

```bash
kubectl get all -n local-path-storage
kubectl get storageclass
```

```text
NAME                                          READY   STATUS    RESTARTS   AGE
pod/local-path-provisioner-79b7b99b5d-5g5gq   1/1     Running   0          21s

NAME         PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE      ALLOWVOLUMEEXPANSION
local-path   rancher.io/local-path   Delete          WaitForFirstConsumer   false
```

```text
DEFAULT 표시가 없다 — is-default-class 어노테이션이 없어서다
```

---

## 4. 검증

### 4-1. PVC 와 Pod

```yaml
# /tmp/test-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
  namespace: default
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: local-path
  resources:
    requests:
      storage: 1Gi
```

```yaml
# /tmp/test-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: test-pvc-user
  namespace: default
spec:
  containers:
    - name: c
      image: busybox
      command: ["sh","-c","echo hello > /data/test.txt && sleep 3600"]
      volumeMounts:
        - name: d
          mountPath: /data
  volumes:
    - name: d
      persistentVolumeClaim:
        claimName: test-pvc
```

```bash
kubectl apply -f /tmp/test-pvc.yaml
kubectl apply -f /tmp/test-pod.yaml
```

```text
[4단계 방식과 비교]
  03-postgres-pv.yaml   PV 30줄 + PVC
  이번                  PVC 13줄. 경로도 노드도 안 적었다
```

### 4-2. PV 가 자동으로 생겼다

```bash
kubectl get pvc,po -n default
kubectl get pv
```

```text
NAME                             STATUS  VOLUME                                     CAPACITY  STORAGECLASS
persistentvolumeclaim/test-pvc   Bound   pvc-346680a7-c377-4fb6-9501-b485d975c59e   1Gi       local-path

NAME                READY   STATUS    RESTARTS   AGE
pod/test-pvc-user   1/1     Running   0          2m57s

NAME                                       CAPACITY  RECLAIM  STATUS  CLAIM              STORAGECLASS
bookstore-pgdata                           5Gi       Retain   Bound   bookstore/…        (비어 있음)
pvc-346680a7-c377-4fb6-9501-b485d975c59e   1Gi       Delete   Bound   default/test-pvc   local-path
```

### 4-3. nodeAffinity 가 자동으로 채워졌다

```bash
kubectl get pv -o yaml | grep -A8 nodeAffinity
```

```yaml
# bookstore-pgdata — 사람이 손으로 쓴 것
    nodeAffinity:
      required:
        nodeSelectorTerms:
        - matchExpressions:
          - key: kubernetes.io/hostname
            operator: In
            values:
            - worker01
    persistentVolumeReclaimPolicy: Retain
```

```yaml
# pvc-346680a7-… — 프로비저너가 채운 것
    nodeAffinity:
      required:
        nodeSelectorTerms:
        - matchExpressions:
          - key: kubernetes.io/hostname
            operator: In
            values:
            - worker02
    persistentVolumeReclaimPolicy: Delete
```

```text
★ WaitForFirstConsumer 가 하는 일
  Pod 가 worker02 로 스케줄된 뒤에 만들었으므로 정확히 맞출 수 있다
  → 4단계에서 "어느 노드일지 사람이 미리 정해야 했던" 문제를
    순서를 뒤집어 없앤 것
```

### 4-4. 노드에 디렉터리가 생겼다

```bash
# worker02
ls -la /opt/local-path-provisioner/
```

```text
total 12
drwxr-xr-x 3 root root 4096 Aug 31 11:36 .
drwxr-xr-x 5 root root 4096 Aug 31 11:36 ..
drwxrwxrwx 2 root root 4096 Aug 31 11:36 pvc-346680a7-c377-4fb6-9501-b485d975c59e_default_test-pvc
```

```bash
# worker01
ls -la /opt/local-path-provisioner/
```

```text
ls: cannot access '/opt/local-path-provisioner/': No such file or directory
```

```text
[확인된 것]
  1. 권한이 777 이다             ConfigMap 의 setup 이 mkdir -m 0777
  2. 이름이 자기설명적            PV이름_네임스페이스_PVC이름
  3. worker01 에는 아무것도 없다  필요한 노드에만, 필요할 때 만든다
```

### 4-5. 삭제하면 데이터도 사라진다

```bash
kubectl delete pod test-pvc-user -n default
kubectl delete pvc test-pvc -n default
kubectl get pv
```

```text
# 삭제 직후
pvc-346680a7-…   1Gi   Delete   Released   default/test-pvc   local-path

# 잠시 후
NAME               CAPACITY   RECLAIM   STATUS   CLAIM
bookstore-pgdata   5Gi        Retain    Bound    bookstore/data-postgres-0
```

```text
[PV 상태 흐름]
  Bound      PVC 와 묶여 있다
  Released   PVC 는 사라졌는데 PV 는 남아 있다
             Delete  프로비저너가 데이터를 지우고 PV 도 지운다
             Retain  Released 인 채로 남는다. 사람이 처리한다
  (사라짐)
```

---

## 5. 겪은 문제 — 네임스페이스

`apply` 는 성공했는데 `get` 과 `describe` 가 NotFound 를 냈다.

```text
kubectl apply -f /tmp/test-pvc.yaml
  → persistentvolumeclaim/test-pvc created

kubectl get pvc
  → data-postgres-0 만 나온다

kubectl describe pvc test-pvc
  → Error from server (NotFound): persistentvolumeclaims "test-pvc" not found
```

```bash
kubectl config view --minify | grep namespace
```

```text
    namespace: bookstore
```

```text
매니페스트에는 namespace: default 라고 적혀 있었다
→ default 에 만들어졌다
→ get/describe/delete 는 -n 없이 쳤다 → bookstore 에서 찾았다 → 없다
```

```bash
kubectl get pvc,po -n default                              # 여기 있었다
kubectl config set-context --current --namespace=default   # 해결
```

```text
[교훈]
  NotFound 를 보면 "없다" 가 아니라 "여기엔 없다" 를 먼저 의심한다
```

### 부수적으로 본 것

PVC 없이 Pod 만 다시 만들었더니 Pod 가 `Pending` 에 머물렀다.

```text
[같은 Pending, 다른 원인]
  PVC 가 없다                  참조할 대상이 없다
  PVC 는 있는데 소비자가 없다   WaitForFirstConsumer 대기
  → describe 로 Events 를 봐야 구별된다
```

---

## 결과

```text
  StorageClass   local-path                     ✓
                 Delete / WaitForFirstConsumer
  검증           PV 자동 생성                    ✓
                 nodeAffinity 자동 기입 (worker02)
                 노드 디렉터리 자동 생성 (777)
                 삭제 시 데이터까지 제거
```

## 정적 방식과의 대비

| | 4단계 (정적) | 이번 (동적) |
|---|---|---|
| PV 생성 | 사람이 매니페스트 작성 | 프로비저너가 생성 |
| 디렉터리 | 사람이 노드에 mkdir | helper Pod 가 생성 |
| 이름 | `bookstore-pgdata` | `pvc-<uuid>` |
| nodeAffinity | 사람이 손으로 적음 | 스케줄 결과를 보고 자동 |
| reclaimPolicy | Retain | Delete |
| 순서 | PV 먼저 → PVC → Pod | PVC → Pod → PV |

## 남은 한계

```text
local-path 는 결국 노드의 디렉터리다
  → 데이터가 그 노드에만 있다
  → 노드가 죽으면 그 데이터에 못 간다
  → Pod 가 다른 노드로 갈 수도 없다

  ★ 자동화된 것은 "누가 만드는가" 이지 "어디에 있는가" 가 아니다
  → 02-experiments.md 의 실험 E(노드 장애)가 그대로 유효하다
```

## 확인 명령

```bash
kubectl get storageclass
kubectl get po -n local-path-storage
kubectl get pv
kubectl get pvc -A

# PVC 가 Pending 이면
kubectl describe pvc <이름> -n <ns> | tail -8
kubectl logs -n local-path-storage deploy/local-path-provisioner --tail=30

# PV 가 어느 노드에 묶였는지
kubectl get pv <이름> -o jsonpath='{.spec.nodeAffinity}' | head -c 300

# 노드의 실제 디렉터리
ls -la /opt/local-path-provisioner/

# NotFound 가 나오면
kubectl config view --minify | grep namespace
```

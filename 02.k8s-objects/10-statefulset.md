# 10. StatefulSet — 이름이 고정되면 디스크가 따라온다

`cloud-native-learning-roadmap.md` 2단계.

이 문서는 두 가지 물음에 답한다.

```text
[물음 1] 09(PV/PVC)에서 남긴 것
  볼륨을 쓰는 앱을 여러 대 띄우려면 어떻게 하는가

  Deployment 로는 안 된다
    Pod 이름이 매번 바뀐다 (mysql-c747ddb74-5dnq5)
    PVC 는 하나뿐이라 3개가 나눠 쓸 수 없다
    RWO 라 애초에 여러 노드에서 동시에 못 쓴다


[물음 2] 1단계 장애 실험에서 설명하지 못한 것
  worker 노드를 강제로 껐더니 이런 일이 있었다

    Deployment 에 replicas: 4 라고 선언했는데
    kubectl get pod 에 13분 동안 6개가 보였다

  그때는 "Deployment 가 그렇게 하도록 만들어졌다" 고만 적었다
  왜 그런 선택을 하는지, 다른 워크로드는 어떻게 다른지 설명하지 못했다
```

실험은 셋이다.

```text
실험 A   볼륨 없이 — 이름 / 순서 / DNS / 재생성
실험 B   volumeClaimTemplates + local PV 3개
실험 C   worker02 강제 종료 — Deployment 와 나란히 비교
         → 물음 2 의 "6개" 를 같은 조건에서 재현하고 원인을 밝힌다
```

---

## 0. 전체 흐름

```text
StatefulSet 이 하는 일은 한 줄이다

    이름을 고정한다

거기서 나머지가 자동으로 따라온다

    이름이 고정된다          db-1
      ↓
    PVC 이름이 결정된다      data-db-1        <템플릿>-<sts>-<번호>
      ↓
    PVC 가 살아남는다        Pod 와 수명이 다르다
      ↓
    같은 PV 에 묶여 있다     local-pv-c
      ↓
    노드가 결정된다          worker02         nodeAffinity
      ↓
    같은 데이터를 본다       written by db-1
```

```text
Deployment 와의 갈림길

  Deployment    Pod 들이 서로를 몰라도 된다  →  동시에 만들어도 된다
                                             →  이름이 랜덤이어도 된다
                                             →  하나 죽으면 아무 데나 새로 띄운다

  StatefulSet   Pod 들이 서로를 안다          →  순서가 의미를 가진다
                                             →  이름을 미리 알아야 한다
                                             →  같은 번호가 둘이면 안 된다
```

---

## 1. 왜 필요한가 — MySQL 복제로 보면

```text
db-0   마스터    쓰기를 받는다
db-1   복제본    db-0 을 따라 복사한다
db-2   복제본
```

복제본 설정에는 마스터 주소가 들어간다.

```text
CHANGE MASTER TO MASTER_HOST='???'
```

### Deployment 로는 여기서 막힌다

```text
mysql-c747ddb74-5dnq5
mysql-c747ddb74-9kx2m
mysql-c747ddb74-pt8vn

1. 어느 게 마스터인지 이름으로 알 수 없다
2. 이름을 미리 알 수도 없다
3. 재시작하면 이름이 또 바뀐다
```

Service 를 쓰면 부하분산되어 **마스터를 지목할 수 없다.** 복제본이 다른 복제본을 마스터로 삼게 된다.

---

## 2. 실험 A-1 — 순차 생성

### manifest

```yaml
apiVersion: v1
kind: Service
metadata:
  name: db
  namespace: k8s-lab
spec:
  clusterIP: None          # Headless
  selector:
    app: db
  ports:
  - port: 80
    name: web
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: db
  namespace: k8s-lab
spec:
  serviceName: db          # 위 Headless Service 이름
  replicas: 3
  selector:
    matchLabels:
      app: db
  template:
    metadata:
      labels:
        app: db
    spec:
      terminationGracePeriodSeconds: 5
      containers:
      - name: nginx
        image: nginx:alpine
        ports:
        - containerPort: 80
          name: web
```

```text
Deployment 에 없는 필드 셋

  clusterIP: None       ClusterIP 를 만들지 않는다
  serviceName: db       Pod 들의 DNS 이름을 어느 Service 아래에 만들지. 필수다
  terminationGracePeriodSeconds: 5
                        기본 30초. 삭제 실험을 반복할 것이라 줄였다
```

### 실제 출력

```text
db-0   0/1   Pending              0s
db-0   0/1   ContainerCreating    0s
db-0   1/1   Running             17s
db-1   0/1   Pending              0s      ← db-0 이 Running 이 된 뒤에야 나타난다
db-1   1/1   Running             16s
db-2   0/1   Pending              0s
db-2   1/1   Running              3s
```

**발견 1.** 하나가 Running 이 될 때까지 다음을 만들지 않는다.

대조군(실험 C 에서 만든 Deployment)은 이랬다.

```text
web-769d9cfbdb-jmcs2   0/1   Pending   0s
web-769d9cfbdb-rxj6g   0/1   Pending   0s      ← 4개가 동시에
web-769d9cfbdb-tm9g2   0/1   Pending   0s
web-769d9cfbdb-2zxhn   0/1   Pending   0s
```

```text
왜 순서를 지키나
  db-0 이 마스터가 되어 준비를 끝낸 뒤에야 db-1 이 복제를 시작할 수 있다
  셋이 동시에 뜨면 서로가 서로를 찾다가 꼬인다
```

---

## 3. 실험 A-2 — 컨트롤러 사슬이 한 단이다

```bash
kubectl -n k8s-lab get pod db-0 -o jsonpath='{.metadata.ownerReferences}'
```

```json
[{"apiVersion":"apps/v1","blockOwnerDeletion":true,"controller":true,
  "kind":"StatefulSet","name":"db","uid":"6c2e602e-c9bc-44eb-b41f-989bd6c295d4"}]
```

```bash
kubectl -n k8s-lab get rs
No resources found in k8s-lab namespace.
```

**발견 2.** ReplicaSet 이 없다. Pod 의 주인이 StatefulSet 직접이다.

```text
Deployment    Deployment → ReplicaSet → Pod     2단
StatefulSet   StatefulSet → Pod                 1단
```

```text
왜 Deployment 는 중간 그릇이 필요했나
  롤링업데이트 중 두 세대가 동시에 존재한다
  구버전 2개 + 신버전 1개
  → "이 Pod 는 어느 세대냐" 를 담을 그릇이 필요하다 → ReplicaSet

왜 StatefulSet 은 필요 없나
  db-2 → db-1 → db-0 순으로 하나씩 교체한다
  → 순번 자체가 진행 상황이다
```

### 그런데 revision 기록은 따로 있다

```text
NAME            CONTROLLER            REVISION   AGE
db-77c4b67bf8   statefulset.apps/db   1          2m44s
```

**발견 3.** ControllerRevision 이라는 오브젝트가 있다. ReplicaSet 의 두 역할이 쪼개졌다.

```text
[ReplicaSet 이 하던 일]
  1. Pod 를 소유하고 개수를 맞춘다
  2. 그 세대의 template 을 보관한다 (롤백용)

[StatefulSet]
  1 → StatefulSet 이 직접 한다
  2 → ControllerRevision 이 맡는다. Pod 를 소유하지 않는다
```

`db-77c4b67bf8` 의 해시는 ReplicaSet 이름의 그것과 같은 방식 — template 을 해싱한 값이다.

---

## 4. 실험 A-3 — Headless Service 와 DNS

```text
NAME   TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)
db     ClusterIP   None         <none>        80/TCP
       ^^^^^^^^^   ^^^^
```

**발견 4.** Headless 는 별도 타입이 아니다. `TYPE` 은 여전히 `ClusterIP` 이고 IP 를 안 받았을 뿐이다.

### 예상 밖 — EndpointSlice 가 있다

```text
NAME       ADDRESSTYPE   PORTS   ENDPOINTS
db-mn8kh   IPv4          80      10.244.5.5,10.244.30.101,10.244.5.7
```

**발견 5. ★** 가상 IP 가 없는데 EndpointSlice 는 정상적으로 만들어진다.

```text
[Service 편에서 본 구조]
  Service → ClusterIP → iptables → Pod 로 DNAT
             ^^^^^^^^^^^^^^^^^^^^ 이게 통째로 없다

[그런데 EndpointSlice 는 남아 있다]
  → 목록은 유지되는데 iptables 가 안 쓴다
  → CoreDNS 가 쓴다
```

```text
04 편에서 EndpointSlice 를 "kube-proxy 가 읽는 목록" 으로만 봤다
절반만 본 것이었다

  재료(EndpointSlice)는 하나
  소비자가 둘 — kube-proxy 와 CoreDNS
```

### DNS 세 갈래

```bash
kubectl -n k8s-lab exec db-0 -- nslookup kubernetes.default.svc.cluster.local
kubectl -n k8s-lab exec db-0 -- nslookup db.k8s-lab.svc.cluster.local
kubectl -n k8s-lab exec db-0 -- nslookup db-0.db.k8s-lab.svc.cluster.local
```

```text
kubernetes.default.svc.cluster.local  →  10.96.0.1
    일반 ClusterIP Service
    이 IP 는 어떤 Pod 의 것도 아니다. 커널이 잡아채 DNAT 한다

db.k8s-lab.svc.cluster.local          →  10.244.30.101 / 10.244.5.5 / 10.244.5.7
    Headless Service
    전부 진짜 Pod IP. 응답마다 순서가 섞인다
    → 분배 판단을 클라이언트가 한다

db-0.db.k8s-lab.svc.cluster.local     →  10.244.5.5
    개별 Pod 지목                            ★ StatefulSet 만 되는 것
```

**발견 6. ★** 이 이름은 Pod 를 만들기 전부터 알 수 있다. StatefulSet 이름이 `db` 고 0번이면 무조건 그 이름이다. 그래서 설정 파일을 미리 써둘 수 있다.

```text
CHANGE MASTER TO MASTER_HOST='db-0.db.k8s-lab.svc.cluster.local'
```

### 진단 도구가 거짓말을 했다

처음에 짧은 이름으로 물었더니 이랬다.

```text
kubectl -n k8s-lab exec db-0 -- nslookup db-0.db
** server can't find db-0.db: NXDOMAIN

kubectl -n k8s-lab exec db-0 -- nslookup db
Name:   db.k8s-lab.svc.cluster.local
Address: 10.244.30.101 / 10.244.5.5 / 10.244.5.7      ← 이건 됐다
```

```text
resolv.conf
  search k8s-lab.svc.cluster.local svc.cluster.local cluster.local localdomain
  nameserver 10.96.0.10
  options ndots:5
```

차이는 점 하나였다.

```text
db          점이 없다  →  search 도메인이 붙었다  →  성공
db-0.db     점이 있다  →  안 붙었다              →  NXDOMAIN
                          에러 메시지에 확장 안 된 원본 이름이 그대로 찍혔다
```

교차 확인.

```text
kubectl -n k8s-lab exec db-0 -- getent hosts db-0.db
10.244.5.5   db-0.db.k8s-lab.svc.cluster.local ... db-0.db      ← 같은 짧은 이름인데 된다
```

**발견 7. ★** alpine 의 busybox `nslookup` 이 `ndots` 를 보지 않고 점이 있으면 FQDN 으로 취급한다. `getent` 는 OS resolver 를 쓰므로 정상 동작한다.

```text
DNS 레코드는 처음부터 다 있었다
진단 도구가 거짓 음성(false negative)을 냈다

→ nslookup 이 NXDOMAIN 을 뱉는다고 DNS 장애로 단정하면 엉뚱한 곳을 판다
→ 앱 설정에는 FQDN 을 다 적는 게 안전하다
```

> 이 결론은 출력에서 읽어낸 추론이다. busybox 소스를 확인한 것은 아니다.

---

## 5. 실험 A-4 — 이름은 돌아오고 IP 는 바뀐다

```bash
kubectl -n k8s-lab delete pod db-1
```

```text
db-1   1/1   Terminating   20m   10.244.30.101   worker02
db-1   0/1   Completed     20m   10.244.30.101   worker02
db-1   0/1   Pending        0s   <none>          <none>
db-1   0/1   Pending        0s   <none>          worker02
db-1   1/1   Running        2s   10.244.30.102   worker02
```

```text
              지우기 전            다시 뜬 뒤
  이름         db-1                db-1              그대로
  IP          10.244.30.101       10.244.30.102     바뀌었다
  AGE         20m                 0s                완전히 새 객체
  db-0/db-2   ─                   줄이 안 나타났다   전혀 안 흔들렸다
```

**발견 8.** StatefulSet 이 고정하는 것은 이름이지 IP 가 아니다.

```text
"안정적인 네트워크 신원(stable network identity)" 이라는 표현 때문에
IP 가 고정된다고 오해하기 쉽다
실제로 고정되는 건 DNS 이름이고, 그 이름이 새 IP 를 가리키게 갱신된다
```

DNS 갱신 확인.

```text
kubectl -n k8s-lab exec db-0 -- getent hosts db-1.db
10.244.30.102 ...                              ← 조금 전까지 30.101 이었다

kubectl -n k8s-lab exec db-0 -- nslookup db.k8s-lab.svc.cluster.local
10.244.30.102 / 10.244.5.5 / 10.244.5.7        ← 목록도 갱신됐다
```

**발견 9.** Pod 변화가 DNS 까지 전달되는 사슬.

```text
Pod 삭제/재생성
  → EndpointSlice 컨트롤러가 목록을 고친다
  → CoreDNS 가 그 목록을 읽고 응답을 바꾼다
```

### `Completed` 는 무엇인가

```text
db-1   1/1   Terminating
db-1   0/1   Completed      ← 지웠는데 왜 "완료" 인가
```

```text
kubelet 이 SIGTERM 을 보낸다 → nginx 가 정상 종료 → 종료 코드 0
→ "컨테이너가 정상적으로 끝났다" 는 뜻으로 Completed 로 표시된다
→ 그다음 Pod 객체가 사라진다
```

정상 종료였으므로 유예 5초를 다 쓰지 않았다.

### 노드가 같은 것은 우연이다

```text
db-0   worker01
db-1   worker02   ← 다시 여기로 왔다
db-2   worker01
```

**발견 10.** StatefulSet 이 노드를 기억한 게 아니다. worker01 에 이미 2개가 있어 worker02 가 덜 붐볐을 뿐이다. **볼륨이 붙으면 얘기가 달라진다(실험 B).**

---

## 6. 실험 B-1 — volumeClaimTemplates 는 나중에 못 붙인다

돌고 있는 StatefulSet 에 볼륨 관련 필드를 추가해 `apply` 했다.

```text
The StatefulSet "db" is invalid: spec: Forbidden: updates to statefulset spec
for fields other than 'replicas', 'ordinals', 'template', 'updateStrategy',
'revisionHistoryLimit', 'persistentVolumeClaimRetentionPolicy' and
'minReadySeconds' are forbidden
```

**발견 11.** 에러 메시지가 바꿀 수 있는 필드 목록을 통째로 알려준다.

```text
[바꿀 수 있는 것들의 공통점]
  Pod 를 다시 만들면 해결된다

[volumeClaimTemplates 가 다른 이유]
  이미 만들어진 PVC 는 디스크에 묶여 있다
  템플릿을 바꾸면
    PVC 를 다시 만든다 → 데이터가 날아간다
    그냥 둔다          → 선언과 실제가 영영 어긋난다
  어느 쪽도 안전하지 않다 → 아예 못 바꾸게 했다
```

목록에 `persistentVolumeClaimRetentionPolicy` 가 있는 것을 기억해 둔다(10절에서 회수).

---

## 7. 실험 B-2 — local PV 준비와 바인딩

우리 클러스터에는 프로비저너가 없다(09 확인). PVC 가 자동 생성되면 붙을 PV 가 없으므로 먼저 만든다.

### 노드에 디렉터리와 표식

```bash
# worker01
sudo mkdir -p /mnt/disks/vol-a /mnt/disks/vol-b
echo "worker01 / vol-a" | sudo tee /mnt/disks/vol-a/marker.txt
echo "worker01 / vol-b" | sudo tee /mnt/disks/vol-b/marker.txt

# worker02
sudo mkdir -p /mnt/disks/vol-c
echo "worker02 / vol-c" | sudo tee /mnt/disks/vol-c/marker.txt
```

```text
표식을 넣는 이유
  "db-1 이 재시작 후에도 같은 물리 디스크를 잡았다" 를 증명하려면
  디스크마다 구분되는 내용이 있어야 한다
```

### PV 3개 — 09 와 달라진 곳 둘

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: local-pv-a
spec:
  capacity:
    storage: 1Gi
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  local:                              # [1] hostPath 가 아니다
    path: /mnt/disks/vol-a
  nodeAffinity:                       # [2] local 은 이게 필수다
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values:
          - worker01
```

`local-pv-b` 는 worker01, `local-pv-c` 는 worker02 로 같은 형식.

```text
hostPath   "그냥 이 노드의 이 경로". 없으면 만들어버린다(DirectoryOrCreate)
           → 09 에서 데이터가 조용히 사라진 원인
local      "특정 노드에 붙어 있는 디스크". nodeAffinity 없이는 만들 수 없다
           디렉터리가 없으면 마운트에서 실패한다
```

이름을 `a/b/c` 로 지은 이유: 번호가 대응한다고 착각하지 않기 위해서다.

### StatefulSet 에 볼륨 추가

```yaml
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes:
      - ReadWriteOnce
      resources:
        requests:
          storage: 1Gi
```

```text
volumeClaimTemplates 의 metadata.name: data
volumeMounts 의 name: data
→ 이름이 같아야 연결된다
```

### 결과

```text
NAME        STATUS   VOLUME       CAPACITY   ACCESS MODES   AGE
data-db-0   Bound    local-pv-a   1Gi        RWO            51s
data-db-1   Bound    local-pv-c   1Gi        RWO            48s
data-db-2   Bound    local-pv-b   1Gi        RWO            46s
```

**발견 12.** PVC 이름 규칙은 `<템플릿이름>-<StatefulSet이름>-<번호>` 다.

**발견 13.** 바인딩은 번호 순이 아니다. `data-db-1` 이 `local-pv-c` 에 붙었다.

```text
조건(1Gi / RWO)이 셋 다 같으므로 컨트롤러 입장에서 구별할 이유가 없다
"번호가 맞는 것" 이 아니라 "조건이 맞는 것 중 하나" 다
```

### 바인딩이 즉시였다 — 09 와 다르다

```text
data-db-0   Pending                     0s
data-db-0   Pending   local-pv-a   0    0s
data-db-0   Bound     local-pv-a   1Gi  0s      전부 같은 초
```

```text
[09]    PVC 를 먼저 만들었다 → 붙을 게 없어 반복 실패 → 나중에 PV 생성 → 65초
[여기]  PV 를 먼저 만들어뒀다 → PVC 가 생기자마자 짝이 있었다 → 즉시
```

**발견 14.** 09 문서의 "약 12~15초 주기의 루프" 는 정확하지 않다. 컨트롤러는 주기적으로만 도는 게 아니라 PVC 생성 이벤트에 즉시 반응한다.

> 09 에서 65초가 걸린 이유는 그 PVC 가 여러 번 실패해 재시도 간격이 벌어져 있었기 때문으로 보인다(이벤트에 `x6 over 74s` 로 간격이 늘어난 것이 찍혀 있었다). **확인하지 않았다. 미확인 항목이다.**

### 중간 상태가 잡혔다

```text
data-db-0   Pending   local-pv-a   0      ← VOLUME 은 찼는데 아직 Pending, 용량 0
data-db-0   Bound     local-pv-a   1Gi
```

**발견 15.** 바인딩도 선언과 상태가 분리돼 있다.

```text
1. 컨트롤러가 PVC 의 spec.volumeName 에 local-pv-a 를 쓴다   (선언)
2. 그다음 status 를 Bound 로 바꾸고 용량을 복사한다           (상태)
```

`-w` 로 보지 않았으면 놓칠 순간이다.

---

## 8. 실험 B-3 — 스케줄러가 nodeAffinity 를 지킨다 ★

```text
  PVC          PV           PV 의 nodeAffinity     Pod 가 실제로 뜬 노드
  data-db-0    local-pv-a   worker01          →    db-0   worker01   ✓
  data-db-1    local-pv-c   worker02          →    db-1   worker02   ✓
  data-db-2    local-pv-b   worker01          →    db-2   worker01   ✓
```

**발견 16. ★★** 세 개가 전부 일치한다. `db-1` 만 worker02 로 간 것은 `data-db-1` 이 하필 worker02 의 PV 에 묶였기 때문이다.

```text
[스케줄러가 노드를 고르는 순서]
  1. 이 Pod 가 쓰는 PVC 를 찾는다        →  data-db-1
  2. 그 PVC 가 묶인 PV 를 본다           →  local-pv-c
  3. 그 PV 의 nodeAffinity 를 읽는다     →  worker02
  4. 후보에서 worker01 을 탈락시킨다
  5. worker02 에 배치한다
```

```text
[09 hostPath]         PV 에 노드 정보가 없다 → 2~4 단계를 할 수 없다
                      → 아무 노드에나 배치 → 빈 디렉터리 → 조용한 데이터 소실
[local + nodeAffinity] PV 가 노드 정보를 들고 있다
                      → 데이터 없는 노드로 갈 수가 없다
```

09 에서 결론만 냈던 "local 볼륨은 nodeAffinity 가 필수" 를 여기서 실측했다.

### 실제 마운트 확인

```bash
for i in 0 1 2; do kubectl -n k8s-lab exec db-$i -- cat /data/marker.txt; done
```

```text
db-0   worker01 / vol-a
db-1   worker02 / vol-c
db-2   worker01 / vol-b
```

**Kubernetes 오브젝트 사슬 = 실제 리눅스 마운트.** 중간에 어긋난 곳이 없다.

각 Pod 에 흔적을 남겨 다음 실험 준비.

```bash
for i in 0 1 2; do
  kubectl -n k8s-lab exec db-$i -- sh -c "echo 'written by db-$i' > /data/who.txt"
done
```

---

## 9. 실험 B-4 — 재생성 시 같은 디스크를 다시 잡는다 ★

삭제 전 PVC AGE 를 기록해 둔다.

```text
data-db-0   Bound   local-pv-a   5m45s
data-db-1   Bound   local-pv-c   5m42s
data-db-2   Bound   local-pv-b   5m40s
```

```bash
kubectl -n k8s-lab delete pod db-1
```

```text
db-1   1/1   Terminating   6m9s   10.244.30.103   worker02
db-1   0/1   Completed     6m9s
db-1   0/1   Pending         0s   <none>          <none>
db-1   0/1   Pending         0s   <none>          worker02
db-1   1/1   Running          2s   10.244.30.104  worker02
```

```text
              삭제 전            삭제 후
  이름         db-1              db-1                  같다
  IP          10.244.30.103     10.244.30.104         바뀐다
  노드         worker02          worker02              ★ 이번엔 필연이다
  PVC AGE     5m45s             6m28s                 ★ 리셋 안 됐다
  marker      worker02/vol-c    worker02/vol-c        같은 물리 디스크
  who.txt     written by db-1   written by db-1       ★ 데이터가 살아남았다
```

**발견 17. ★★** PVC 는 Pod 와 수명이 다르다. AGE 가 계속 증가한다.

```text
Pod 는 갈아끼우는 부품이다
PVC 는 그 자리에 붙박이로 남는다

StatefulSet 이 db-1 을 다시 만들 때
PVC 를 새로 만들지 않고 "data-db-1 이 이미 있네" 하고 그걸 쓴다
→ local-pv-c 에 묶여 있다 → worker02 → 같은 데이터
```

**발견 18.** 실험 A-4 와 달리 노드가 같은 것이 우연이 아니다. 볼륨이 노드를 결정한다.

---

## 10. 실험 B-5 — 축소해도 PVC 는 남는다

```bash
kubectl -n k8s-lab scale statefulset db --replicas=1
```

```text
db-2   1/1   Terminating
db-2   0/1   Completed        ← db-2 가 완전히 끝난 뒤에야
db-1   1/1   Terminating      ← db-1 이 시작한다
db-1   0/1   Completed
```

**발견 19.** 삭제도 순차이고 역순이다.

```text
생성   0 → 1 → 2      마스터부터 세운다
삭제   2 → 1          가장 나중에 합류한 것부터 뺀다
```

```text
0 번부터 지웠다면 마스터가 먼저 죽고 복제본들이 고아가 된다
살아남을 db-0 이 마지막까지 남는 게 맞다
```

### PVC 는 하나도 안 지워졌다

```text
Pod    db-0 하나만 남았다
PVC    data-db-0 / data-db-1 / data-db-2      셋 다. AGE 11m 그대로
PV     셋 다 Bound. Released 가 아니다
```

**발견 20. ★** 주인 없는 PVC 둘이 생겼다. `data-db-1`, `data-db-2` 를 쓰는 Pod 가 없는데 Bound 다.

```text
왜 이렇게 설계했나
  축소는 되돌릴 수 있다   → replicas 를 다시 올리면 끝
  데이터 삭제는 못 되돌린다

  게다가 축소는 실수로도 일어난다 (yaml 오타 / HPA 자동 축소)
  → 그때마다 DB 데이터가 날아가면 재앙이다
```

```text
[09 와 같은 원칙]
  09    Released PV 를 자동 재사용하지 않는다
        → 남의 데이터를 볼 위험보다 사람이 확인하는 게 낫다
  여기  축소해도 PVC 를 안 지운다
        → 디스크가 남는 불편보다 데이터 소실이 훨씬 나쁘다
```

```text
[대가]
  안 쓰는 디스크가 쌓인다
  온프렘   용량만 차지한다
  클라우드  EBS 요금이 계속 나간다      ← 실무에서 자주 새는 돈
```

**발견 21.** `persistentVolumeClaimRetentionPolicy` 로 바꿀 수 있다(6절의 그 필드).

```yaml
persistentVolumeClaimRetentionPolicy:
  whenScaled:  Retain | Delete      # 축소했을 때
  whenDeleted: Retain | Delete      # StatefulSet 자체를 지웠을 때
```

기본값은 둘 다 `Retain`. **안전한 쪽이 기본이고 위험한 쪽을 쓰려면 명시해야 한다.**

### 다시 늘리면 데이터가 돌아온다

```bash
kubectl -n k8s-lab scale statefulset db --replicas=3
```

```text
db-1   1/1   Running   worker02   10.244.30.105   written by db-1
db-2   1/1   Running   worker01   10.244.5.9      written by db-2
```

**발견 22.** 축소 → PVC 유지 → 재확장 → 원래 디스크 재부착. IP 만 바뀐다.

---

## 11. 실험 C — 노드가 죽으면 무슨 일이 벌어지는가 ★★

1단계에서 관찰만 하고 넘어간 그 장면을 재현한다.

```text
"replicas: 4 로 선언했는데 13분 동안 6개가 보였다"

  1단계에서는 여기까지였다
  이번에는 같은 노드 장애를 만들어놓고
  Deployment 와 StatefulSet 을 나란히 놓고 본다
```

### 설계

같은 조건에서 두 워크로드를 나란히 본다. 비교군으로 Deployment 를 추가했다.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: k8s-lab
spec:
  replicas: 4
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      terminationGracePeriodSeconds: 5
      containers:
      - name: nginx
        image: nginx:alpine
```

```text
왜 4개인가   worker 2대에 대략 2:2 로 나뉜다
             2개면 우연히 둘 다 worker01 로 갈 수 있다
왜 볼륨 없이  Deployment 의 행동 자체를 보려는 것이다
             볼륨을 붙이면 "볼륨 때문인지 Deployment 라서인지" 가 섞인다
```

### 장애 전 상태

```text
worker01    db-0, db-2, web-jmcs2, web-rxj6g
worker02    db-1, web-2zxhn, web-tm9g2        ← 이 노드를 강제 종료
```

### 왜 강제 종료(Power Off)인가

```text
정상 종료하면 kubelet 이 종료 신호를 받고 "나 내려갑니다" 를 보고한다
→ 노드 장애가 아니라 계획된 종료가 된다
→ 우리가 보려는 상황이 안 만들어진다
```

### 타임라인

```text
15:56:23   worker02 의 마지막 Lease 갱신               ← 진짜 T0
16:00:18   taint 확인. Pod 들은 아직 Running           ← 유예 구간
16:02:22   db-1 의 deletionTimestamp (07:02:22Z)       ← 축출
           = T0 + 5분 59초
```

```text
예상   40초(NotReady 판정) + 300초(유예) = 5분 40초
실측   5분 59초
```

> 40초·300초는 kubeadm 기본값 기준의 학습 데이터다. 차이는 컨트롤러 확인 주기로 보인다.

### 노드가 죽은 것을 어떻게 알았나 — Lease

```text
Lease:
  HolderIdentity:  worker02
  RenewTime:       Fri, 21 Aug 2026 15:56:23 +0900     ← 여기서 멈췄다
```

**발견 23.** kubelet 은 Lease 오브젝트를 10초마다 갱신한다. Control Plane 은 그 시각만 본다.

```text
Node 오브젝트를 통째로 갱신하지 않는 이유는 부하다
노드가 수천 대면 10초마다 Node 를 전부 쓰는 것을 etcd 가 감당하기 어렵다
```

### Taint 가 둘 붙었다

```text
node.kubernetes.io/unreachable:NoExecute
node.kubernetes.io/unreachable:NoSchedule
```

**발견 24.** 대상이 다르다.

```text
NoSchedule    "앞으로 이 노드에 새 Pod 를 넣지 마라"      미래
NoExecute     "이미 있는 Pod 도 내보내라"                현재
```

```text
NoExecute 는 즉시 내보내지 않는다
모든 Pod 에 기본 toleration 이 붙어 있다

  tolerations:
  - key: node.kubernetes.io/unreachable
    effect: NoExecute
    tolerationSeconds: 300

왜 5분을 참나
  네트워크가 잠깐 끊긴 것일 수 있다
  그때마다 Pod 를 다 옮기면 클러스터가 요동친다
```

16:00:18 의 출력이 그 유예 구간이다. taint 는 붙었는데 Pod 는 아직 `Running` 이었다.

### 결과 — 16:02:40

```text
[Deployment]  선언 replicas: 4        →  목록에 6개      ★ 1단계의 그 장면
  web-clrpr    Running       worker01   ← 새로 만든 것
  web-g7jxc    Running       worker01   ← 새로 만든 것
  web-jmcs2    Running       worker01
  web-rxj6g    Running       worker01
  web-2zxhn    Terminating   worker02   ← 유령
  web-tm9g2    Terminating   worker02   ← 유령

[StatefulSet]  선언 replicas: 3        →  목록에 3개
  db-0         Running       worker01
  db-2         Running       worker01
  db-1         Terminating   worker02   ← 대체 Pod 를 만들지 않았다
```

**발견 25. ★★** 같은 노드가 죽었는데 반응이 정반대다. **1단계에서 본 "선언 4개인데 6개" 가 그대로 재현됐고, 이번에는 이유를 알 수 있다.**

```text
[왜 Deployment 는 6개가 됐나]
  Terminating 이 안 끝나는 이유
    Pod 를 정말 지우려면 그 노드의 kubelet 이 "정리 끝" 을 보고해야 한다
    kubelet 이 전원과 함께 죽었다 → 보고가 영영 안 온다
    → deletionTimestamp 만 찍힌 채 etcd 에 남는다

  ReplicaSet 은 "지워지는 중인 Pod 는 없는 셈" 친다
    → 4개 중 2개가 없다 → 2개를 새로 만든다
    → 지워지는 중인 것도 목록에는 보인다 → 6개
```

```text
[왜 StatefulSet 은 안 만들었나]
  db-1 이 완전히 사라진 걸 확인하기 전에는 새 db-1 을 만들지 않는다

  만약 만들었다면
    worker02 가 실은 네트워크만 끊긴 거였다면?
    → 저쪽 db-1 이 아직 /mnt/disks/vol-c 에 쓰고 있다
    → 이쪽에도 db-1 이 뜬다 → 한 디스크에 둘이 쓴다
    → 09 6절에서 본 그것. ext4 는 단독 사용을 전제한다 → 파일시스템 파손
```

**"at most one" 보장** — 같은 번호의 Pod 는 클러스터 전체에 최대 하나.

```text
Deployment    가용성을 지킨다. 중복을 감수한다
StatefulSet   정합성을 지킨다. 중단을 감수한다

어느 쪽이 우월한 게 아니라 지키는 것이 다르다
```

### 컨트롤러가 자기 상태를 보는 방식

```text
statefulset.apps/db    READY  2/3      ← 하나 모자란 걸 안다
deployment.apps/web    READY  4/4      ← 이미 만족했다
```

### db-1 의 진짜 상태

```bash
kubectl -n k8s-lab get pod db-1 -o jsonpath='{.metadata.deletionTimestamp}{"\n"}{.status.phase}{"\n"}'
```

```text
2026-08-21T07:02:22Z
Running
```

**발견 26. ★** `kubectl get` 에서 `Terminating` 으로 보이지만 실제 phase 는 `Running` 이다.

```text
Terminating 은 Pod 의 상태값이 아니다
kubectl 이 "deletionTimestamp 가 찍혀 있네" 를 보고 그렇게 표시하는 것이다

진짜 phase 를 바꾸는 건 그 노드의 kubelet 인데 그 kubelet 이 죽었다
→ Running 인 채로 굳는다
```

```text
Control Plane 이 기록하는 것   deletionTimestamp   (지우라는 지시)
노드의 kubelet 이 기록하는 것   status.phase        (실제로 어떻게 됐는지)
→ 노드가 죽으면 후자가 영영 갱신되지 않는다
```

### 그리고 아무도 말해주지 않는다

```text
kubectl -n k8s-lab describe sts db | sed -n '/^Events/,$p'

Events:
  Normal  SuccessfulCreate  24m (x3 over 37m)  statefulset-controller  Create Pod db-1 ...
  (그 이후 아무것도 없다)
```

**발견 27. ★** db-1 이 6분째 멈춰 있는데 이벤트가 하나도 없다.

```text
"db-1 을 만들 수 없습니다" 도 없고
"노드가 죽어서 기다립니다" 도 없다

운영자가 알아차릴 방법
  kubectl get sts 의 READY 2/3 을 직접 보거나
  모니터링이 그 숫자를 지켜보고 있거나
```

**09 hostPath 사고와 같은 성격이다. 에러가 안 나는 게 제일 위험하다.** 5단계에서 Observability 를 먼저 구성하는 근거가 된다.

---

## 12. 강제 삭제 — 그래도 갈 곳이 없다 ★★

```bash
kubectl -n k8s-lab delete pod db-1 --force --grace-period=0
```

```text
Warning: Immediate deletion does not wait for confirmation that the running
resource has been terminated. The resource may continue to run on the cluster
indefinitely.
pod "db-1" force deleted from k8s-lab namespace
```

```text
--grace-period=0   유예 없이
--force            kubelet 의 "정리 끝" 보고를 기다리지 않고 etcd 에서 지운다

Kubernetes 는 저쪽 Pod 가 진짜 죽었는지 모른다. 그래서 안 지우고 있는 것이다
--force 는 "내가 확인했다. 저건 죽었다" 를 사람이 보증하는 것이다

보증이 틀리면 → 같은 디스크에 둘이 쓴다 → 파일시스템 파손
실무에서는 IPMI / 클라우드 콘솔로 전원 상태를 확인한 뒤에만 쓴다
```

### 결과 — Pending 에서 멈춘다

```text
db-1   0/1   Pending   68s   <none>   <none>
```

```text
Warning  FailedScheduling  default-scheduler
  0/3 nodes are available:
    1 node(s) didn't match PersistentVolume's node affinity,
    2 node(s) had untolerated taint(s).
```

**발견 28. ★★** 노드 3대가 각각 다른 이유로 탈락했다.

```text
master01   taint      control-plane 에는 일반 Pod 를 안 넣는다
worker01   볼륨       local-pv-c 는 worker02 전용이다
worker02   taint      unreachable:NoSchedule. 죽었다
```

```text
worker02 로 가야 하는데 worker02 는 죽어 있다
다른 데로 가면 데이터가 없다
→ 갈 곳이 없다. 영원히 Pending
```

### 같은 장치가 안전장치이자 족쇄다

**발견 29. ★★★ (11-storage.md 로 이어지는 핵심)**

```text
[실험 B — nodeAffinity 가 구해준 상황]
  09 hostPath 는 노드 정보가 없어 다른 노드에서 빈 디렉터리를 조용히 줬다
  → nodeAffinity 를 붙이니 그런 배치가 아예 불가능해졌다

[실험 C — 같은 nodeAffinity 가 막는 상황]
  노드가 죽었는데 그 노드로만 갈 수 있다 → 옮길 수가 없다
```

```text
"데이터가 있는 곳으로만 간다" 는 규칙 하나가
평상시엔 데이터를 지키고, 장애 시엔 이동을 막는다
```

09 편에서 던진 질문 — "local 볼륨을 쓰면 Kubernetes 를 쓰는 의미가 없지 않나" — 의 실물이 이것이다.

### worker02 가 영영 안 돌아온다면

```text
[local 볼륨]
  그 디스크의 데이터는 그 서버와 함께 사라진다
  PVC 와 PV 를 지우고 새 PV 를 만들어 db-1 을 빈 상태로 세운다
  → 데이터 복구는 앱(복제본)이나 백업의 몫

[네트워크 스토리지였다면]
  EBS / Ceph 는 노드에 붙어 있지 않다
  → 볼륨을 worker01 에 붙이면 db-1 이 거기서 뜬다
  → 다만 EBS 는 AZ 를 못 넘는다. AZ 가 통째로 죽으면 같은 문제다
```

---

## 13. 복구

worker02 Power On.

```text
worker02   NotReady → Ready

web-tm9g2   Terminating   10.244.30.106
web-tm9g2   Terminating   <none>            ← IP 가 사라진다
web-tm9g2   Unknown       <none>
web-tm9g2   (삭제됨)

db-1   0/1   Pending             <none>     4m5s 까지 갈 곳이 없었다
db-1   0/1   Pending             worker02   ← taint 가 걷히자 즉시 결정
db-1   0/1   ContainerCreating   worker02
db-1   1/1   Running             worker02   10.244.30.108
```

**발견 30.** 25분 동안 아무도 못 지우던 유령 Pod 가 노드가 돌아오자 몇 초 만에 정리됐다.

```text
Pod 를 지우는 마지막 단계는 그 노드의 kubelet 이 한다
노드가 죽어 있는 동안   보고할 주체가 없다 → 영원히 Terminating
노드가 살아나면        kubelet 이 상태를 다시 보고한다 → 정리된다
```

> `Unknown` 을 거친 것은, 부팅된 kubelet 이 그 Pod 들을 모르는 상태에서 상태를 확정할 수 없어 잠깐 거친 단계로 보인다. **정확한 내부 순서는 확인하지 않았다.**

### 최종 상태

```text
db-0                   Running   worker01   10.244.5.8
db-1                   Running   worker02   10.244.30.108     ← IP 만 또 바뀌었다
db-2                   Running   worker01   10.244.5.9
web-clrpr / g7jxc / jmcs2 / rxj6g   Running  worker01          ← 6개에서 4개로
```

### 데이터 확인

```bash
kubectl -n k8s-lab exec db-1 -- cat /data/who.txt
kubectl -n k8s-lab exec db-1 -- cat /data/marker.txt
```

```text
written by db-1
worker02 / vol-c
```

**발견 31.** 전원이 갑자기 끊겼는데 데이터가 온전하다.

```text
[살아남은 이유]
  전원 차단 시점에 쓰기 작업이 없었다. 파일은 이미 디스크에 반영돼 있었다

[실제 DB 였다면 다르다]
  쓰는 도중에 전원이 끊기면 일부만 기록된다
  그래서 DB 는 저널이나 WAL 을 둔다
  → 재시작할 때 미완성 트랜잭션을 되돌려 정합성을 맞춘다

  "디스크를 다시 붙여준다"      까지가 Kubernetes 의 일
  "그 디스크 내용이 온전한가"    는 앱의 일
```

---

## 14. 이 오브젝트의 범위 — 어디까지가 StatefulSet 의 일인가

실험을 마치고 정리하다 나온 질문이다. **"StatefulSet 은 DB 용인가?"**

### 대상은 "DB" 가 아니라 "신원이 필요한 앱" 이다

```text
StatefulSet 이 주는 것은 셋뿐이다
  1. 이름이 고정된다
  2. 순서가 지켜진다
  3. Pod 마다 자기 디스크
```

이 중 하나라도 필요하면 대상이다. DB 는 셋 다 필요해서 대표로 불릴 뿐이다.

```text
[DB 가 아닌 사례]
  Kafka          브로커마다 고유 ID 와 자기 로그 디렉터리
  ZooKeeper      myid 파일이 노드마다 달라야 한다. 순번이 곧 신원이다
  etcd           멤버 목록에 서로의 주소를 적어둔다
  Elasticsearch  샤드가 어느 노드에 있는지가 의미를 가진다
  Prometheus     그 자체가 시계열 DB. 자기 디스크가 필요하다   ← 5단계에서 만난다
  MinIO          노드마다 디스크를 들고 있다
```

### 거꾸로 DB 인데 안 쓰는 경우

```text
[단일 인스턴스]
  복제본이 없다 → 서로를 지목할 일이 없다
  → Deployment + PVC 하나로도 된다

  다만 strategy: Recreate 로 바꿔야 한다
    RollingUpdate 는 새 Pod 를 먼저 띄운다
    → 잠깐 둘이 같은 RWO 볼륨을 잡으려 한다
    → 09 6절에서 본 그것. 새 Pod 가 마운트 못 하고 멈춘다

  이 경우 StatefulSet 이 오히려 안전하다. 구조적으로 안 생기는 문제다

[클라우드]
  RDS / Cloud SQL — 아예 클러스터 밖에 둔다
```

### StatefulSet 만으로는 DB 클러스터가 되지 않는다 ★★

```text
[해주는 것]        자리(이름) / 사물함(볼륨) / 순서

[안 해주는 것]     누가 마스터인지 정하기
                  복제 설정 걸기
                  마스터가 죽었을 때 복제본 승격 (failover)
                  백업과 복구
                  버전 업그레이드 시 데이터 마이그레이션
```

**이 실험에서 만든 것은 사실 nginx 3개다.** `db-0` 이라는 이름을 붙였을 뿐 마스터도 복제본도 아니다.

```text
그래서 실무에서는 Operator 를 쓴다
  Percona XtraDB Cluster Operator / Zalando Postgres Operator / Strimzi(Kafka)

Operator = 그 앱의 운영 지식을 코드로 만든 컨트롤러
  → 내부적으로 StatefulSet 을 만든다
  → 그 위에 마스터 선출·페일오버·백업을 얹는다

StatefulSet   "자리와 사물함" 을 제공하는 기반
Operator      그 위에서 실제 DB 운영을 하는 층
```

11~13절이 이 경계를 정확히 보여준다.

```text
노드가 죽었다

Kubernetes      db-1 을 안 띄웠다. 데이터 정합성을 지켰다 (발견 25)
                그런데 2/3 로 돌고 있고 이벤트가 하나도 없다 (발견 27)

Operator 가 있다면
                "마스터가 죽었네" 를 판단하고 복제본을 승격시킨다
```

**Kubernetes 는 앱이 무엇인지 모른다.** 그게 설계상 옳지만, 그래서 앱을 아는 층이 하나 더 필요하다.

### 판단 기준

```text
상태가 없다 (웹, API)               →  Deployment
상태가 있는데 인스턴스 하나          →  Deployment(Recreate) + PVC
                                      또는 StatefulSet 이 더 안전
상태가 있고 여러 대가 서로를 안다     →  StatefulSet
그 위에 운영 자동화까지 필요하다      →  Operator (내부에 StatefulSet)
노드마다 하나씩                     →  DaemonSet   (12-daemonset.md)
```

> Operator 는 7단계(Helm) 이후, CRD 와 함께 다룬다. 여기서는 "StatefulSet 위에 한 층이 더 있다" 는 것까지만 짚는다.

---

## 정리

```text
[범위]
 0. StatefulSet 은 DB 용 도구가 아니라 Pod 에 신원을 부여하는 도구다
    그리고 이것만으로는 DB 클러스터가 되지 않는다. Operator 가 그 위층이다

[정체성]
 1. 생성은 순차다. 하나가 Running 이 될 때까지 다음을 안 만든다
    (대조군 Deployment 는 4개가 동시에 Pending)
 2. 컨트롤러 사슬이 1단이다. ReplicaSet 이 없다
 3. revision 기록은 ControllerRevision 이 따로 맡는다. Pod 를 소유하지 않는다

[네트워크]
 4. Headless 는 별도 타입이 아니다. ClusterIP 를 안 받은 ClusterIP Service 다
 5. 가상 IP 가 없어도 EndpointSlice 는 만들어진다 ★
    → 재료는 하나, 소비자가 둘 (kube-proxy / CoreDNS)
 6. DNS 세 갈래
      일반 Service   이름 → 가상 IP 하나  → 커널이 분배
      Headless       이름 → 진짜 IP 여럿  → 클라이언트가 분배
      개별 Pod       이름 → 특정 Pod 하나  ★ StatefulSet 만 되는 것
 7. busybox nslookup 은 ndots 를 보지 않는다. getent 로 교차 확인해야 한다

[재생성]
 8. 고정되는 것은 이름이지 IP 가 아니다
 9. Pod 변화 → EndpointSlice → CoreDNS 로 DNS 가 자동 갱신된다
10. Completed 는 SIGTERM 을 받고 정상 종료(코드 0)했다는 표시다

[볼륨]
11. volumeClaimTemplates 는 나중에 못 바꾼다. 에러가 가변 필드 목록을 알려준다
12. PVC 이름 규칙  <템플릿>-<StatefulSet>-<번호>
13. 바인딩은 번호 순이 아니다. 조건이 맞는 것 중 하나다
14. PV 가 미리 있으면 바인딩이 즉시다. 09 의 65초는 재시도 지연 때문으로 보인다
15. 바인딩도 선언(spec.volumeName) → 상태(Bound) 순으로 나뉘어 있다
16. 스케줄러가 PV 의 nodeAffinity 를 지킨다 ★★
    → 09 의 조용한 데이터 소실이 구조적으로 불가능해진다
17. PVC 는 Pod 와 수명이 다르다. Pod 를 지워도 AGE 가 리셋되지 않는다 ★★
18. 그래서 같은 이름 → 같은 PVC → 같은 PV → 같은 노드 → 같은 데이터

[확장·축소]
19. 삭제는 역순이고 하나씩이다 (2 완료 → 1 시작)
20. 축소해도 PVC 는 안 지워진다. 주인 없는 PVC 가 남는다 ★
    → 되돌릴 수 있는 쪽으로 기운다. 대신 안 쓰는 디스크 비용이 샌다
21. persistentVolumeClaimRetentionPolicy 로 바꿀 수 있다. 기본은 Retain
22. 재확장하면 원래 디스크를 다시 잡는다

[노드 장애]
23. kubelet 은 Lease 를 10초마다 갱신한다. Control Plane 은 그 시각만 본다
24. unreachable taint 는 둘이다. NoSchedule(미래) / NoExecute(현재)
    NoExecute 도 tolerationSeconds 300 만큼 참는다
25. Deployment 는 6개, StatefulSet 은 3개 ★★
    1단계의 "선언 4개인데 6개" 를 재현하고 원인까지 확인했다
    Deployment    가용성을 지킨다. 중복을 감수한다
    StatefulSet   정합성을 지킨다. 중단을 감수한다
26. Terminating 은 상태값이 아니다. 실제 phase 는 Running 인 채로 굳는다 ★
27. 그런데 이벤트가 하나도 없다 ★  → 5단계 Observability 의 근거
28. 강제 삭제해도 Pending. 노드 3대가 각각 다른 이유로 탈락했다 ★★
29. 같은 nodeAffinity 가 평상시엔 안전장치, 장애 시엔 족쇄다 ★★★
30. 노드가 돌아오면 유령 Pod 가 몇 초 만에 정리된다. 6개 → 4개
31. 전원 차단에도 데이터는 온전했다. 다만 그건 쓰기가 없었기 때문이다
```

## 확인 명령

```bash
# 정체성
kubectl -n k8s-lab get pod -o wide -w                     # 생성 순서
kubectl -n k8s-lab get pod db-0 -o jsonpath='{.metadata.ownerReferences}'
kubectl -n k8s-lab get rs
kubectl -n k8s-lab get controllerrevision

# 네트워크
kubectl -n k8s-lab get svc db                             # CLUSTER-IP None
kubectl -n k8s-lab get endpointslice -l kubernetes.io/service-name=db
kubectl -n k8s-lab exec db-0 -- cat /etc/resolv.conf
kubectl -n k8s-lab exec db-0 -- nslookup db-0.db.k8s-lab.svc.cluster.local
kubectl -n k8s-lab exec db-0 -- getent hosts db-0.db      # 짧은 이름은 이쪽으로

# 볼륨
kubectl -n k8s-lab get pvc -w                             # 자동 생성과 바인딩
kubectl get pv                                            # CLAIM 칸
kubectl -n k8s-lab exec db-1 -- cat /data/marker.txt      # 실제 물리 디스크

# 노드 장애
kubectl get nodes -w
kubectl describe node worker02 | grep -A6 Taints
kubectl -n k8s-lab get pod db-1 -o jsonpath='{.metadata.deletionTimestamp}{"\n"}{.status.phase}{"\n"}'
kubectl -n k8s-lab describe pod db-1 | sed -n '/^Events/,$p'
kubectl -n k8s-lab get sts,deploy                         # READY 2/3 vs 4/4
```

## 미확인

```text
 1. busybox nslookup 이 ndots 를 무시하는 것이 맞는지 (소스 확인 안 함)
 2. 09 의 65초 지연이 재시도 백오프 때문인지 (추론만 함)
 3. 유령 Pod 가 Unknown 을 거치는 내부 순서
 4. NotReady 판정까지 걸린 정확한 시각 (get nodes -w 에 타임스탬프가 없었다)
 5. tolerationSeconds 300 이 어디에 기본 주입되는지 (Admission 인지 확인 안 함)
 6. ControllerRevision 이 여러 개 쌓이는 모습 (업데이트를 안 해봤다)
 7. StatefulSet 롤링업데이트가 정말 역순(2→1→0)인지 (미실행)
 8. persistentVolumeClaimRetentionPolicy: Delete 의 실제 동작 (미실행)
 9. podManagementPolicy: Parallel 로 바꿨을 때 순차성이 사라지는지 (미실행)
10. 노드 오브젝트를 삭제(kubectl delete node)했을 때의 동작
11. StatefulSet 을 지웠을 때 PVC 가 남는지 (whenDeleted 기본값 확인)
12. 두 노드의 시계가 실제로 어긋나 있는지 (chrony 상태 미확인)
```

## 다음

```text
11-storage.md   1단계에서 "데이터베이스는 Kubernetes 밖에 두는 게 낫다" 고
                결론냈다. 스토리지를 하나도 안 본 상태의 판단이었다
                09·10 에서 실측한 것을 근거로 다시 판단한다
                발견 29 가 핵심 재료다
```

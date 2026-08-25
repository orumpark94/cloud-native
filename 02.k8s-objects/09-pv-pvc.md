# 09. PersistentVolume / PersistentVolumeClaim

2단계 열 번째. **지금까지 만든 것은 전부 "사라져도 되는 것" 이었다.**

```text
[04 에서 실측한 것]
  Pod 3개에 index.html 을 써놨다
  하나를 지우니 새 Pod 는 nginx 기본 페이지를 줬다
  → 컨테이너의 쓰기 레이어(upperdir)는 Pod 와 함께 사라진다

[06 에서 확인한 것]
  ConfigMap 볼륨은 ro 다. 앱이 쓸 수 없다
  설정을 넣는 통로지 저장소가 아니다
```

```text
그럼 사라지면 안 되는 것은 어디에 두는가
  데이터베이스의 데이터 / 업로드된 파일 / 보관해야 하는 로그
```

## 07 에서 예고한 것이 여기서 풀린다

```text
kubectl api-resources --namespaced=false
  persistentvolumes            ← cluster-scoped
  persistentvolumeclaims       ← namespaced (true 목록에 있었다)
```

```text
왜 이 쌍이 나뉘어 있는가
네임스페이스를 지웠는데 디스크는 왜 안 지워지는가
```

## 이 문서의 범위

```text
[확인한 것]
  1. 볼륨 종류의 계단 — 어디까지 살아남는가                 ✅
  2. PVC 를 만들면 왜 Pending 에 머무는가                   ✅ ★
  3. PV 를 만들면 컨트롤러가 자동으로 짝지어준다              ✅
  4. 바인딩이 즉시가 아니다                                 ✅
  5. PV 볼륨은 rw 다 (ConfigMap 은 ro 였다)                 ✅
  6. Pod 를 지워도 데이터가 남는다                          ✅
  7. hostPath 는 노드가 바뀌면 조용히 빈 디렉터리를 준다      ✅ ★★
  8. PVC 삭제를 finalizer 가 막는다                         ✅
  9. PVC 가 사라져도 PV 와 데이터는 남는다 (Retain)          ✅ ★
 10. Released PV 는 자동으로 재사용되지 않는다               ✅ ★
 11. PV 와 PVC 를 왜 나눴는가                               ✅

[다루지 않는 것]
  StorageClass / 동적 프로비저닝   프로비저너가 없어 실습 불가. 개념만
  NFS / Ceph / EBS                 10단계 이후
  reclaimPolicy: Delete            미실습
  volumeMode: Block                미실습
  StatefulSet 의 볼륨              11 문서
```

---

# 0. 전체 흐름 — 셋이 각각 무엇인가 ★

```text
[PVC]  "10Gi 짜리 하나 주세요. 읽기·쓰기로"    요청. 개발자가 쓴다. namespaced
[PV]   "그건 worker01 의 /mnt/data-pv 다"     실제 자원. cluster-scoped
[바인딩] persistentvolume-controller 가 자동으로 짝지어준다
```

**08 의 RBAC 과 다르다.**

```text
[RBAC]     Role(규칙) + RoleBinding(연결)   → 연결도 사람이 만든다
[PV/PVC]   PVC(요청) + PV(자원)             → 연결은 컨트롤러가 한다
```

## 길이 둘이다

```text
[동적 프로비저닝]
  PVC 를 만든다 → StorageClass 를 본다 → 프로비저너가 실제 디스크를 만든다
  → PV 오브젝트가 자동으로 생긴다 → 바인딩

[정적 바인딩]
  관리자가 미리 PV 를 만들어둔다
  → PVC 가 조건에 맞는 PV 를 찾아 묶인다
```

**우리 클러스터는 프로비저너가 없어 정적 방식으로 실습했다.**

---

# 1. 볼륨의 계단

```text
[1] 컨테이너 파일시스템
    컨테이너 재시작 → 사라진다
    Pod 삭제        → 사라진다        (04 에서 실측)

[2] emptyDir
    컨테이너 재시작 → 남는다          ★ [1]과 다른 지점
    Pod 삭제        → 사라진다
    → "이 Pod 사는 동안만" 필요한 것에 쓴다

[3] hostPath
    Pod 삭제        → 남는다
    그런데 그 노드에만 있다            ★ 7절에서 실측

[4] PV / PVC + 네트워크 스토리지
    Pod 삭제        → 남는다
    노드가 바뀌어도 따라간다
```

```text
emptyDir 는 Pod 의 디렉터리다 (노드의 /var/lib/kubelet/pods/<uid>/volumes/ 아래)
컨테이너 파일시스템은 컨테이너의 것이다
→ 컨테이너가 죽고 다시 떠도 Pod 가 살아있으면 emptyDir 는 그대로다
```

> **미확인**: emptyDir 가 컨테이너 재시작에 살아남는 것을 실측하지 않았다.

---

# 2. 아무것도 없는 상태 (2026-08-21)

```text
root@master01:/# kubectl get storageclass
No resources found
root@master01:/# kubectl get pv
No resources found
root@master01:/# kubectl get pvc -A
No resources found
```

```text
kubeadm 은 StorageClass 를 안 깔아준다
클라우드가 아니면 "어떤 디스크를 쓸 것인가" 를 Kubernetes 가 알 수 없다
→ 환경마다 다르니 직접 정하라는 것이다
```

**05 의 Ingress Controller 와 같은 구조다.**

```text
Ingress        규격만 있고 컨트롤러는 직접 깐다
StorageClass   규격만 있고 프로비저너는 직접 깐다
```

---

# 3. PVC 를 만들면 Pending ★

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: data-pvc
  namespace: k8s-lab
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
```

```text
accessModes    어떻게 붙일 것인가
  ReadWriteOnce (RWO)      한 노드에서 읽기·쓰기
  ReadOnlyMany  (ROX)      여러 노드에서 읽기만
  ReadWriteMany (RWX)      여러 노드에서 읽기·쓰기   ← NFS 같은 것만 가능
  ReadWriteOncePod         한 Pod 에서만. 가장 엄격

resources.requests.storage   "최소 이만큼". 더 큰 PV 에 붙을 수도 있다
storageClassName             안 쓰면 기본 SC 가 채워진다. 없으면 빈 채로 남는다
```

## 발견 1 — 컨트롤러가 이유를 말해준다

```text
root@master01:/# kubectl describe pvc data-pvc -n k8s-lab
Status:      Pending
Finalizers:  [kubernetes.io/pvc-protection]
Events:
  Normal  FailedBinding  7s (x6 over 74s)  persistentvolume-controller
          no persistent volumes available for this claim and no storage class is set
```

```text
길이 둘인데 둘 다 막혔다
  동적: StorageClass 가 없다
  정적: PV 가 하나도 없다
```

## 발견 2 — 05 의 Ingress 와 다르다 ★

```text
[Ingress]
  오브젝트를 만들었다 → 아무 반응이 없다 → Events: <none>
  → 처리할 컨트롤러가 아예 없다

[PVC]
  오브젝트를 만들었다 → 컨트롤러가 반응한다
  → Events 에 이유를 적는다. x6 over 74s = 계속 재시도한다
  → 컨트롤러는 있는데 재료가 없다
```

```text
Ingress   "아무도 안 본다"
PVC       "보고 있는데 줄 게 없다"
```

**진단 방법이 달라진다.**

```text
Ingress 가 안 될 때  → 컨트롤러가 깔려 있나부터 본다
PVC 가 Pending 일 때 → describe 의 Events 를 읽으면 이유가 나온다
```

## 발견 3 — Pod 도 Pending 인데 이유가 다르다

```text
root@master01:/# kubectl describe pod data-pod -n k8s-lab
Warning  FailedScheduling  default-scheduler
         0/3 nodes are available: pod has unbound immediate PersistentVolumeClaims
```

```text
스케줄러가 노드를 못 고른다
"이 Pod 는 그 볼륨이 있어야 하는데 아직 어디 있는지 모른다"
→ 노드를 정할 수가 없다
```

```text
[00 에서 본 Pending]  이미지 받는 중 → ContainerCreating
[여기]                볼륨이 안 정해져서 스케줄링 자체가 안 된다
                      0/3 nodes are available = 노드 셋 다 후보에서 탈락
```

---

# 4. PV 를 만들면 컨트롤러가 짝지어준다

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: data-pv          # namespace 필드가 없다. cluster-scoped 니까
spec:
  capacity:
    storage: 1Gi
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain
  hostPath:
    path: /mnt/data-pv
    type: DirectoryOrCreate
```

```text
persistentVolumeReclaimPolicy
  Retain  PVC 가 지워져도 PV 와 데이터를 그대로 둔다   ← 8절에서 확인
  Delete  PV 와 실제 저장소를 함께 지운다
```

> **hostPath 는 실무에서 쓰면 안 되는 방식이다.** 노드에 묶이기 때문이다(7절).
> 프로비저너 없이 실습하려는 것뿐이다.

## 발견 4 — 바인딩이 즉시가 아니다

```text
11:46:21   PV 생성 → Available
    +9초   여전히 Available / PVC 는 Pending
   +65초   Bound
```

```text
앞의 이벤트에 간격이 찍혀 있다
FailedBinding  7s (x6 over 74s)   → 약 12~15초 간격
```

> **2026-08-21 정정.** 이 문서 초판에는 "persistentvolume-controller 는 주기적으로 도는
> 루프라서 다음 주기에야 반응한다" 고 적었다. **정확하지 않다.**
>
> 10-statefulset.md 실험 B 에서 **PV 를 먼저 만들어두고 PVC 를 나중에 만들었더니
> 바인딩이 같은 초 안에 끝났다.**
>
> ```text
> data-db-0   Pending                     0s
> data-db-0   Pending   local-pv-a   0    0s
> data-db-0   Bound     local-pv-a   1Gi  0s
> ```
>
> 컨트롤러는 주기적으로만 도는 게 아니라 **PVC 생성 이벤트에 즉시 반응한다(watch).**
> 여기서 65초가 걸린 것은 순서가 반대였기 때문 — 이 PVC 는 이미 여섯 번 실패해
> 재시도 간격이 벌어져 있었다. 위 `x6 over 74s` 가 그 간격이다.
>
> 다만 **"재시도 백오프 때문" 이라는 것은 추론이고 확인하지 않았다.** 미확인 항목 참조.

**우리가 바인딩 명령을 친 적이 없다.**

```text
pv    Available → Bound (CLAIM: k8s-lab/data-pvc)
pvc   Pending   → Bound (VOLUME: data-pv)
pod   Pending   → Running on worker01
```

## 발견 5 — 양쪽에 서로를 적어둔다

```text
PV  의 CLAIM   k8s-lab/data-pvc      ← 네임스페이스까지 적는다
PVC 의 VOLUME  data-pv               ← 이름만
```

```text
PV 는 cluster-scoped 라 "어느 네임스페이스의 어느 PVC" 인지 적어야 한다
PVC 는 자기 네임스페이스 안에 있으니 이름만 적으면 된다
```

**07 의 그 구조가 여기 그대로 있다.**

## 곁가지 — 03 의 미확인 하나가 풀렸다 ★

```text
data-pod   10.244.5.1   worker01
```

```text
[03 미확인 8번]
  Pod IP 재사용 정책. 번호가 계속 올라가는 것은 관측했으나 규칙 미확인
```

```text
오전까지 worker01 의 Pod 가 10.244.5.62 까지 갔다
전부 지웠더니 이번엔 10.244.5.1 이 나왔다
→ 번호가 계속 올라가는 게 아니라 "비어 있는 것 중 낮은 번호" 를 준다
→ 앞서 번호가 올라간 건 앞의 것들이 사용 중이었기 때문이다
```

```text
다만 방금 지운 것을 바로 쓰지는 않는다
  .1 을 쓰던 Pod 를 지우고 새로 만드니 .3 이 나왔다
  → 03 의 "주소가 즉시 재사용되지 않는다" 와 맞는다
```

---

# 5. 데이터를 써본다

```text
root@master01:/# kubectl -n k8s-lab exec data-pod -- sh -c 'echo "hello persistent" > /data/test.txt'
root@master01:/# kubectl -n k8s-lab exec data-pod -- cat /data/test.txt
hello persistent
```

## 발견 6 — rw 다 (ConfigMap 은 ro 였다)

```text
root@master01:/# kubectl -n k8s-lab exec data-pod -- cat /proc/self/mountinfo | grep /data
565 555 252:0 /mnt/data-pv /data rw,relatime - ext4 /dev/mapper/ubuntu--vg-ubuntu--lv rw
                                 ^^
```

```text
[06 ConfigMap 볼륨]  ro    kubelet 이 관리하니 컨테이너는 못 쓴다
[여기 PV]            rw    쓰라고 만든 볼륨이다
```

## 발견 7 — 노드에 진짜 파일이 있다

```text
root@worker01:/mnt/data-pv# ls
test.txt
root@worker01:/mnt/data-pv# cat test.txt
hello persistent
```

**06 에서 본 bind mount 구조 그대로다.** 다만 원본을 kubelet 이 만든 게 아니라 우리가 지정한 경로다.

## 발견 8 — Pod 를 지워도 남는다

```text
Pod 삭제 → PVC/PV 는 Bound 그대로
새 Pod   → cat /data/test.txt → hello persistent
```

**04 와 정확히 대비된다.**

```text
[04]  컨테이너에 직접 쓴 index.html → Pod 를 지우니 사라졌다
[09]  PV 에 쓴 test.txt            → Pod 를 지워도 남는다
```

---

# 6. hostPath 의 한계 ★★

**지금까지 Pod 가 계속 worker01 에 떴다. 그래서 데이터가 남아 보였다.**

```yaml
spec:
  nodeName: worker02       # 스케줄러를 건너뛰고 강제 배정
```

## 발견 9 — 조용히 빈 디렉터리를 준다 ★★

```text
root@master01:/# kubectl -n k8s-lab get pod data-pod -o wide
data-pod   1/1   Running   10.244.30.100   worker02

root@master01:/# kubectl -n k8s-lab exec data-pod -- ls -la /data
total 8
drwxr-xr-x  2 root root 4096 Aug 21 02:55 .
drwxr-xr-x  1 root root 4096 Aug 21 02:55 ..

root@master01:/# kubectl -n k8s-lab exec data-pod -- cat /data/test.txt
cat: can't open '/data/test.txt': No such file or directory
```

```text
root@worker02:/# ls -la /mnt/data-pv
drwxr-xr-x 2 root root 4096 Aug 21 11:55 .      ← 방금 만들어진 빈 디렉터리
```

```text
Pod       Running
PVC       Bound
이벤트     아무것도 없다
그런데    /data 가 비어 있다
```

**에러가 안 난다. 데이터만 없다.** DB 였다면 빈 데이터베이스로 시작했을 것이다.

## 발견 10 — PV 에 노드 정보가 없다

```text
root@master01:/# kubectl get pv data-pv -o yaml | grep -A5 'spec:'
spec:
  accessModes:
  - ReadWriteOnce
  capacity:
    storage: 1Gi
  claimRef:
  ...
  (nodeAffinity 가 없다)
```

```text
hostPath 는 "이 노드의 이 경로" 라는 뜻이다
그런데 PV 오브젝트에는 "어느 노드" 라는 정보가 없다
→ 스케줄러가 볼륨을 고려하지 않고 노드를 고른다
→ 다른 노드로 가면 같은 경로의 다른 디렉터리를 본다
```

**이 문서 전체를 관통하는 주제와 같다.**

```text
[04] EndpointSlice 목록에 있다 ≠ 트래픽을 받는다
[05] describe 에 보인다        ≠ 저장돼 있다
[06] 파일이 바뀌었다            ≠ 앱에 반영됐다
[09] PVC 가 Bound 다            ≠ 데이터가 거기 있다
```

## 해결책 — nodeAffinity / local 볼륨

```yaml
spec:
  nodeAffinity:
    required:
      nodeSelectorTerms:
      - matchExpressions:
        - key: kubernetes.io/hostname
          operator: In
          values: [worker01]
```

```text
hostPath   노드 제약이 없다. 실무에서 쓰면 안 된다
local      nodeAffinity 가 필수다. 노드에 묶인 디스크를 제대로 쓰는 방법
```

> **미확인**: nodeAffinity 를 걸어 스케줄러가 그 노드로만 보내는지 실측하지 않았다.

## 근본 문제는 남는다

```text
노드가 죽으면 그 데이터에 접근할 수 없다
Pod 가 다른 노드로 못 간다 → 고가용성이 안 된다
```

**그럼 어떻게 하나 — 세 갈래다.**

```text
[1] 데이터를 노드 밖에 둔다 (정석)
    NFS / Ceph / EBS / EFS
    → 노드가 죽으면 볼륨을 다른 노드에 붙인다
    → 대가: 네트워크를 타므로 느리다

[2] 앱이 스스로 복제한다
    Kafka / Cassandra / Elasticsearch / etcd
    → 노드 하나 = 복제본 하나
    → 그럴 때는 local 이 오히려 맞다 (빠르니까)
    → "Kubernetes 가 아니라 앱이 고가용성을 책임진다"

[3] Kubernetes 밖에 둔다
    RDS / Cloud SQL / 별도 DB 서버
    → 상태를 가진 것을 아예 클러스터 밖에 둔다
```

**1단계에서 내린 결론 하나를 다시 판단할 재료가 여기서 모였다.**

```text
[1단계에서 낸 결론]  "DB 는 Kubernetes 밖에 두는 게 낫다"
                     근거: Pod 가 죽으면 데이터가 사라진다 (그때는 PV 를 몰랐다)

[오늘 확인한 것]     PV 를 쓰면 Pod 가 죽어도 데이터가 남는다
                     다만 그게 어디 있느냐에 따라 이야기가 완전히 달라진다
                       local     노드에 묶인다. 노드가 죽으면 끝
                       네트워크   따라간다. 대신 느리다

→ "DB 를 밖에 둔다" 는 "Kubernetes 가 못 해서" 가 아니라
  "스토리지와 운영 부담을 감당할 것인가" 의 문제였다
```

**`10-storage.md` 에서 제대로 정리한다.**

---

# 7. PVC 를 지우면 ★

## 발견 11 — finalizer 가 막는다

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete pvc data-pvc -n k8s-lab --wait=false
12:04:38
persistentvolumeclaim "data-pvc" deleted from k8s-lab namespace

root@master01:/# kubectl get pvc -n k8s-lab
data-pvc   Terminating   data-pv   1Gi   RWO   23m

root@master01:/# kubectl get pvc data-pvc -n k8s-lab -o jsonpath='{.metadata.finalizers}'
["kubernetes.io/pvc-protection"]
```

```text
PVC 를 쓰는 Pod 가 있으면 삭제가 안 끝난다
"쓰고 있는데 지워서 Pod 가 깨지는" 일을 막는 것이다
```

**07 에서 네임스페이스가 finalizer 때문에 기다리던 것과 같은 구조다.**

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete pod data-pod -n k8s-lab
12:05:07
root@master01:/# kubectl get pvc -n k8s-lab
No resources found         ← Pod 를 지우니 그제야 사라졌다
```

## 발견 12 — PV 는 Released, 데이터는 남는다

```text
root@master01:/# kubectl get pv
data-pv   1Gi   RWO   Retain   Released   k8s-lab/data-pvc   19m
                               ^^^^^^^^
```

```text
root@worker01:/# cat /mnt/data-pv/test.txt
hello persistent          ← 그대로 있다
```

```text
Released 는 "묶여 있던 PVC 가 사라졌다" 는 뜻이다
Available 이 아니다. 다시 쓸 준비가 된 게 아니다
```

**07 에서 예고한 그 상황이다.**

```text
네임스페이스를 지우면 → PVC 는 namespaced 라 사라진다
                     → PV 는 cluster-scoped 라 남는다
                     → 데이터도 남는다
```

---

# 8. Released PV 는 자동으로 재사용되지 않는다 ★

## 발견 13 — 같은 이름의 PVC 를 다시 만들어도 안 붙는다

```text
root@master01:/# kubectl apply -f /tmp/pvc.yaml
root@master01:/# sleep 20; kubectl get pvc,pv -n k8s-lab
persistentvolumeclaim/data-pvc   Pending
persistentvolume/data-pv         Released
```

## 발견 14 — claimRef 에 옛 PVC 의 UID 가 남아 있다

```text
root@master01:/# kubectl get pv data-pv -o jsonpath='{.spec.claimRef}'
{"apiVersion":"v1","kind":"PersistentVolumeClaim","name":"data-pvc",
 "namespace":"k8s-lab","resourceVersion":"2308631",
 "uid":"05add777-6afc-4252-8c57-f2e70e4e1706"}
```

```text
PV 에 "나는 그 PVC 것이다" 라는 기록이 남아 있다
그런데 그 PVC 는 지워졌다
새로 만든 PVC 는 이름이 같아도 UID 가 다르다
→ 짝이 안 맞는다 → 안 붙는다
```

**07 에서 말한 "끊어진 참조" 가 이것이다.**

## 왜 이렇게 만들었나

```text
[자동으로 붙게 했다면]
  A 팀이 쓰던 PV 가 Released 됐다
  B 팀이 PVC 를 만들었다 → 자동으로 붙는다
  → B 팀이 A 팀의 데이터를 보게 된다     ★ 사고
```

```text
그래서 사람이 확인하고 풀어주게 만들었다
```

## 발견 15 — claimRef 를 지우면 풀린다

```text
root@master01:/# kubectl patch pv data-pv -p '{"spec":{"claimRef":null}}'
persistentvolume/data-pv patched

root@master01:/# kubectl get pv
data-pv   1Gi   RWO   Retain   Available

root@master01:/# sleep 20; kubectl get pvc,pv -n k8s-lab
persistentvolumeclaim/data-pvc   Bound   data-pv
persistentvolume/data-pv         Bound   k8s-lab/data-pvc
```

```text
root@master01:/# kubectl -n k8s-lab exec data-pod -- cat /data/test.txt
hello persistent          ← 데이터는 그대로
```

**한 바퀴가 완결됐다.**

---

# 9. PV 와 PVC 를 왜 나눴나

## 이유 1 — 역할이 다르다

```text
[PVC — 개발자가 쓴다]
  "10Gi 짜리 하나 주세요. 읽기·쓰기로"
  → 디스크가 NFS 인지 EBS 인지 hostPath 인지 몰라도 된다

[PV — 관리자나 프로비저너가 만든다]
  "그건 worker01 의 /mnt/data-pv 다"
  또는 "그건 EBS 볼륨 vol-abc123 이다"
```

## 이유 2 — 이식성

```text
같은 Pod yaml 을 여러 환경에 쓸 수 있다
  PVC 에는 "10Gi RWO" 만 적혀 있다
  → dev 에서는 hostPath 로 채워진다
  → prod 에서는 EBS 로 채워진다
  → yaml 은 그대로다
```

**07 에서 같은 파일을 두 네임스페이스에 그대로 적용했던 것과 같은 발상이다.**

## 이유 3 — 네임스페이스 경계

```text
PV   cluster-scoped   실제 디스크는 클러스터의 자원이다
PVC  namespaced       "쓰고 싶다" 는 요청은 팀의 것이다
```

```text
[만약 Pod 가 PV 를 직접 가리킬 수 있다면]
  다른 팀이 쓰는 디스크를 이름만 알면 가리킬 수 있다
  → 08 에서 본 "남의 Secret 을 마운트" 와 같은 문제
```

```text
Pod → PVC (같은 네임스페이스만)
PVC → PV  (컨트롤러가 짝지어준다)
```

## 발견 16 — 세 번째로 나온 모양이다 ★

```text
[Service / EndpointSlice]
  Service       "app=web 인 Pod 로 보내라"      원하는 것
  EndpointSlice "지금 그건 이 IP 셋이다"        실제

[Deployment / ReplicaSet]
  Deployment    "이 template 을 3개"            원하는 것
  ReplicaSet    "지금 이 Pod 셋이 그것이다"      실제

[PVC / PV]
  PVC           "10Gi RWO 하나"                 원하는 것
  PV            "그건 이 디스크다"               실제
```

```text
전부 "원하는 것" 과 "실제로 그게 뭔지" 를 나눠뒀다
그리고 그 둘을 잇는 컨트롤러가 따로 있다
→ 그래서 셋 다 "짝이 없으면 기다린다" 는 동작이 같다
```

---

# 정리

```text
[문제]
 1. 컨테이너에 쓴 것은 Pod 와 함께 사라진다 (04 에서 실측)
    ConfigMap 볼륨은 ro 라 쓸 수도 없다 (06)
 2. 볼륨의 계단이 넷이다
    컨테이너FS < emptyDir < hostPath < PV(네트워크)

[Pending]
 3. kubeadm 은 StorageClass 를 안 깔아준다 (05 의 Ingress Controller 와 같다)
 4. PVC 를 만들면 Pending. 동적·정적 두 길이 다 막혀 있다
 5. Ingress 와 달리 컨트롤러가 이벤트로 이유를 말해준다
    "아무도 안 본다" 가 아니라 "보고 있는데 줄 게 없다"
 6. Pod 도 Pending. 스케줄러가 볼륨을 몰라 노드를 못 고른다

[바인딩]
 7. PV 를 만들면 컨트롤러가 자동으로 짝지어준다. 사람이 안 한다
 8. 이 실험에서는 65초 걸렸다. 다만 "주기적 루프라서" 가 아니다 (2026-08-21 정정)
    PV 를 먼저 만들어두면 PVC 생성과 동시에 붙는다 — 10-statefulset.md 실험 B
    여기서 느렸던 것은 PVC 가 먼저 만들어져 재시도 간격이 벌어져 있었기 때문으로 보인다
 9. PV 는 CLAIM 에 네임스페이스까지, PVC 는 VOLUME 에 이름만 적는다

[데이터]
10. PV 볼륨은 rw 다 (ConfigMap 은 ro 였다)
11. 노드에 진짜 파일이 있다 (bind mount)
12. Pod 를 지워도 데이터가 남는다

[hostPath 의 한계]
13. 노드가 바뀌면 조용히 빈 디렉터리를 준다 ★
    Pod Running / PVC Bound / 이벤트 없음 / 그런데 데이터 없음
14. PV 에 nodeAffinity 가 없어서다. 스케줄러가 볼륨을 고려하지 않는다
15. local 볼륨은 nodeAffinity 가 필수다
16. 근본 해결은 셋 — 네트워크 스토리지 / 앱이 복제 / 클러스터 밖

[삭제]
17. PVC 삭제를 finalizer(pvc-protection)가 막는다. Pod 를 먼저 지워야 한다
18. PVC 가 사라지면 PV 는 Released. 데이터는 남는다 (Retain)
    → "네임스페이스를 지웠는데 디스크는 안 지워졌다" 가 이것이다
19. Released PV 는 자동으로 재사용되지 않는다
    claimRef 에 옛 PVC 의 UID 가 남아 있어 새 PVC 와 안 맞는다
    → 남의 데이터를 보게 되는 사고를 막으려는 것
20. claimRef 를 지우면 Available → 다음 주기에 Bound

[구조]
21. PV/PVC 를 나눈 이유 셋 — 역할 분리 / 이식성 / 네임스페이스 경계
22. "원하는 것 vs 실제" 패턴이 세 번째로 나왔다
    Service/EndpointSlice, Deployment/ReplicaSet, PVC/PV

[곁가지]
23. Pod IP 는 "비어 있는 것 중 낮은 번호" 를 준다 (.62 → .1)
    다만 방금 지운 것을 바로 쓰지는 않는다 (.1 → .3)
    → 03 미확인 8번이 풀렸다
```

# 실습 리소스

```text
data-pvc    PVC          k8s-lab
data-pv     PV           cluster-scoped
data-pod    Pod          k8s-lab
/tmp/pvc.yaml /tmp/pv.yaml /tmp/pvc-pod.yaml /tmp/pvc-pod2.yaml
worker01 의 /mnt/data-pv
worker02 의 /mnt/data-pv   (7절 실험 중 생성. rmdir 로 정리)
```

```bash
kubectl -n k8s-lab delete pod data-pod
kubectl -n k8s-lab delete pvc data-pvc
kubectl delete pv data-pv
rm -f /tmp/pvc.yaml /tmp/pv.yaml /tmp/pvc-pod.yaml /tmp/pvc-pod2.yaml
kubectl get pv,pvc -A

# worker01 에서 — PV 오브젝트를 지워도 데이터는 안 지워진다
sudo rm -rf /mnt/data-pv
```

**마지막 줄도 오늘의 교훈이다.** `Retain` 이면 사람이 직접 치워야 한다.

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              PVC  "이만큼 필요하다" 는 요청
                                PV   실제 저장 자원
2. 생성 시 동작하는 Controller   persistentvolume-controller (바인딩)
                                동적이면 프로비저너가 PV 를 만든다
3. 주요 Spec 과 Status 필드     PVC: accessModes / resources.requests.storage /
                                     storageClassName / volumeMode
                                     status.phase (Pending/Bound/Lost)
                                PV : capacity / accessModes / claimRef /
                                     persistentVolumeReclaimPolicy / nodeAffinity
                                     status.phase (Available/Bound/Released/Failed)
4. 다른 오브젝트와의 연결        Pod(volumes.persistentVolumeClaim),
                                StorageClass, Namespace(PVC 만)
5. 장애 사례                    3절 Pending / 6절 hostPath 노드 이동 /
                                7절 finalizer / 8절 Released 재사용 실패
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            hostPath 를 실무에서 쓰지 말 것 /
                                PVC 가 Bound 라고 데이터가 있는 건 아니다 /
                                Retain 이면 사람이 치워야 한다 /
                                Released PV 는 claimRef 를 지워야 재사용된다
```

# 미확인 목록

```text
1. StorageClass 와 동적 프로비저닝 미실습 (프로비저너가 없다)
2. reclaimPolicy: Delete 의 동작 미확인
   hostPath 에서 실제 디렉터리가 지워지는지도 미확인
3. nodeAffinity 를 걸어 스케줄러가 제한되는지 미실측
4. local 볼륨 타입 미실습
5. emptyDir 가 컨테이너 재시작에 살아남는 것 미실측
6. RWX(ReadWriteMany) 미실습. NFS 등이 필요하다
7. volumeMode: Block 미실습
8. PVC 의 크기를 늘리는 것(확장) 미실습
9. 네임스페이스를 통째로 지웠을 때 PV 가 Released 로 남는 것 미재현
   (PVC 만 지워서 확인했다)
10. 여러 PVC 가 하나의 PV 를 두고 경쟁할 때의 동작 미확인
11. storageClassName 을 명시했을 때(""와 이름 지정)의 차이 미확인
12. persistentvolume-controller 의 실제 동기화 주기 설정값 미조회
```

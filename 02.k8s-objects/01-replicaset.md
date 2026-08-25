# 01. ReplicaSet

2단계 두 번째 오브젝트. 로드맵 학습 순서의 `Pod → ReplicaSet → Deployment` 중 가운데다.

## 이 문서의 범위

```text
[확인한 것]
  1. 이것이 푸는 문제 — 맨 Pod 로는 운영이 안 된다              ✅
  2. Pod 를 만드는 방식 — 동시 생성, generateName             ✅
  3. 배치 — 같은 소유자의 Pod 를 흩어놓는다                    ✅
  4. 소유권을 무엇으로 판단하는가 — 라벨 셀렉터                 ✅
  5. 방출과 입양                                              ✅
  6. 삭제 우선순위 — 누구를 지우는가                           ✅
  7. template 을 바꿔도 기존 Pod 는 안 바뀐다                  ✅
  8. cascade / orphan — 지울 때 자식은 어떻게 되나             ✅

[다루지 않는 것]
  minReadySeconds 의 실제 효과   6단계에서 부하와 함께
  ReplicaSet 을 직접 운영에 쓰는 법   쓰지 않는다. Deployment 를 쓴다
```

**7번이 이 문서의 결론이고 다음 문서(Deployment)로 가는 다리다.**

---

# 1. 이것이 푸는 문제

00 문서에서 만든 Pod 는 전부 **맨 Pod** 였다.

```text
kubectl delete pod pod-basic
→ 아무도 다시 만들지 않는다
```

운영에서는 이것으로 부족하다.

```text
Pod 가 떠 있던 노드가 죽으면?   아무도 다시 안 만든다 → 서비스 종료
3개로 늘리고 싶으면?            manifest 를 세 벌 복사해 이름을 바꿔가며 만든다
줄이고 싶으면?                  하나씩 지운다
```

```text
Pod          "이 컨테이너를 실행해라"            일회성 선언
ReplicaSet   "이런 Pod 가 항상 N개 있어야 한다"   지속적 선언
```

08 문서 장애 실험에서 반대 현상을 이미 봤다. worker01 의 Pod 가 축출되자 worker02 에 대체 Pod 2개가 즉시 생성됐다. `kubectl create deployment` 로 만들었기 때문이다.

---

# 2. manifest 구조 (2026-08-13)

```yaml
# /tmp/rs-demo.yaml
apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: rs-demo
spec:
  replicas: 3
  selector:
    matchLabels:
      app: rs-demo
  template:
    metadata:
      labels:
        app: rs-demo
    spec:
      containers:
      - name: nginx
        image: nginx:1.27
```

```text
replicas    "이런 Pod 가 3개 있어야 한다"
selector    "내 Pod 를 어떻게 찾을 것인가"      ← 세는 기준
template    "부족하면 이걸로 만들어라"          ← 찍는 도장
```

**`app: rs-demo` 가 두 번 나오는데 역할이 다르다.** `selector` 는 세는 기준이고 `template.metadata.labels` 는 새로 만들 때 붙이는 라벨이다. 둘이 안 맞으면 만들자마자 셀렉터에 안 걸려 무한히 만들게 되므로 apiserver 가 검증에서 거부한다.

**태그를 `nginx:1.27` 로 고정했다.** 00 문서에서 확인한 `imagePullPolicy` 기본값 규칙을 적용한 것이고, 그 효과를 4절에서 확인한다.

---

# 3. 생성 — 무엇이 만들어지나

## 발견 1 — 셋을 동시에 만든다

```text
2026-08-13T01:37:03Z   SuccessfulCreate   ReplicaSet   rs-demo   Created pod: rs-demo-ss4gq
2026-08-13T01:37:03Z   SuccessfulCreate   ReplicaSet   rs-demo   Created pod: rs-demo-n558j
2026-08-13T01:37:03Z   SuccessfulCreate   ReplicaSet   rs-demo   Created pod: rs-demo-r5mw8
```

**같은 초에 세 개.** 하나 만들고 뜨는 걸 기다렸다가 다음을 만드는 게 아니다.

```text
"지금 0개다. 목표는 3개다. 3개 부족하다" → 한 번에 3개를 만든다
```

**ReplicaSet 자체에는 순서 개념이 없다.** Deployment 의 롤링 업데이트는 일부러 순서를 만드는 것이다.

## 이름은 generateName 으로 만들어진다

```text
rs-demo-ss4gq / rs-demo-n558j / rs-demo-r5mw8
```

`generateName: rs-demo-` 에 무작위 문자열이 붙는다. apiserver 가 채운다.

```text
07 문서 3라운드와 같은 구조
  Static Pod   이름을 kubelet 이 붙인다 (kube-scheduler-master01)
  일반 Pod     apiserver 가 generateName 으로 붙인다
```

**이름에 `rs-demo` 가 들어가지만 그것은 흔적일 뿐 관계를 나타내지 않는다.** 8절에서 소유권을 뗀 뒤에도 이름은 그대로 남는 것으로 확인된다.

## 발견 2 — 소유권 표시

```text
root@master01:/# kubectl get pod -l app=rs-demo \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{.metadata.ownerReferences}{"\n\n"}{end}'

rs-demo-ss4gq
[{"apiVersion":"apps/v1","blockOwnerDeletion":true,"controller":true,
  "kind":"ReplicaSet","name":"rs-demo",
  "uid":"9b241099-f046-4176-943f-eae1b68e7e01"}]
```

```text
controller: true          이 소유자가 나를 관리한다
blockOwnerDeletion: true  소유자를 지울 때 나부터 정리해라
uid                       이름이 아니라 uid 로 가리킨다
```

**00 문서의 맨 Pod 는 이 필드가 비어 있었다.** 그 차이가 "지우면 끝"과 "지워도 다시 생김"을 가른다.

`uid` 로 가리키는 것이 중요하다. 같은 이름으로 다시 만들면 uid 가 달라지므로 옛 Pod 들은 없는 소유자를 가리키게 되고 고아로 정리된다.

## 발견 3 — 이벤트의 대상으로 주체가 갈린다

```text
KIND         OBJ             REASON
ReplicaSet   rs-demo         SuccessfulCreate     ← ReplicaSet Controller
Pod          rs-demo-ss4gq   Scheduled            ← scheduler
Pod          rs-demo-ss4gq   Pulling / Created / Started   ← kubelet
```

**컨트롤러 체인이 이벤트 대상으로 드러난다.** 나중에 `SuccessfulDelete` 를 볼 때 이 구분이 결정적이 된다(6절).

---

# 4. 배치와 이미지

## 발견 4 — 분산됐다 ★

```text
worker01   ss4gq, r5mw8    2개
worker02   n558j           1개
```

**자원 점수만으로는 설명이 안 된다.**

00 문서에서 `pod-basic` 하나를 만들었을 때는 이랬다.

```text
worker01  250m 사용 → 높은 점수
worker02  350m 사용 → 낮은 점수
→ worker01 선택
```

그런데 우리 Pod 는 `resources` 를 안 썼다.

```text
resources 를 안 쓰면 requests = 0
→ Pod 를 올려도 "할당된 양" 이 안 늘어난다
→ LeastAllocated 기준으로는 worker01 이 계속 이긴다
→ 3개가 전부 worker01 로 갔어야 한다
```

**그런데 2:1 로 갈렸다.**

```text
scheduler 의 scoring 은 항목이 하나가 아니다

  자원 여유          덜 쓰는 노드 선호
  이미지 보유 여부    이미 이미지가 있는 노드 선호
  분산               같은 소유자의 Pod 가 없는 노드 선호   ← 이것
```

`master01` 은 taint 로 filtering 에서 탈락했으므로 후보가 둘이고, 그래서 `2:1` 이 됐다.

```text
[맨 Pod 하나]      분산할 대상이 없다 → 자원 점수만으로 결정
[ReplicaSet 3개]   같은 소유자끼리 흩어진다
```

> **미확인**: 정확한 플러그인 이름과 기본 설정은 확인하지 않았다.
> `replicas` 를 늘리면 분산 패턴이 더 뚜렷해질 것이다.

## 발견 5 — 이미지 다운로드는 노드당 한 번, 절차는 Pod 마다 한 번

```text
10:37:05   Pulling   r5mw8   Pulling image "nginx:1.27"
10:37:05   Pulling   ss4gq   Pulling image "nginx:1.27"
10:37:26   Pulled    r5mw8   in 21.148s (21.148s including waiting)
10:37:28   Pulled    ss4gq   in  1.57s  (22.713s including waiting)
```

**`Pulling` 이벤트가 두 번이지만 데이터를 두 번 받은 것이 아니다.**

```text
10:37:05   두 Pod 모두 "이미지가 노드에 없다" 고 판정
           (r5mw8 의 다운로드가 진행 중이라 실제로 아직 없다)
           kubelet 이 요청을 줄 세운다

~10:37:26  r5mw8 차례. 실제로 72MB 다운로드 (21초)
~10:37:28  ss4gq 차례. 레이어가 이미 저장소에 있다
           → 레지스트리에 다이제스트만 확인 → 1.57초
```

**괄호 안팎의 숫자가 그것을 말한다.** `1.57s (22.713s including waiting)` 는 21초 대기 후 1.57초 작업이라는 뜻이다.

확인:

```text
root@worker01:/# sudo crictl images | grep nginx
docker.io/library/nginx   1.27     1e5f3c5b981a9   72.4MB
docker.io/library/nginx   latest   5253dc86cc93a   63.1MB

root@worker02:/# sudo crictl images | grep nginx
docker.io/library/nginx   1.27     1e5f3c5b981a9   72.4MB      ← IMAGE ID 가 같다
docker.io/library/nginx   latest   5253dc86cc93a   63.1MB
```

**두 노드의 `IMAGE ID` 가 같다.** 이미지 ID 가 내용의 해시이기 때문이다(content-addressable). 07 문서 4라운드의 etcd 가 key 로 내용을 찾았듯, 여기서는 해시로 내용을 식별한다.

```text
같은 내용 → 같은 해시 → 어느 노드에서 받든 같은 ID
이름이 달라도 내용이 같으면 하나만 저장한다
```

### 저장 구조

```text
root@worker01:/# sudo ls /var/lib/containerd
io.containerd.content.v1.content          레이어 원본 (압축 blob).     418M
io.containerd.snapshotter.v1.overlayfs    풀어놓은 레이어. 마운트 대상. 1.2G
io.containerd.metadata.v1.bolt            메타데이터. meta.db
```

**`bolt` 는 07 문서 4라운드에서 etcd 를 파다 만난 `bbolt` 와 같은 것이다.** 둘 다 임베디드 key-value 저장소가 필요했고 같은 라이브러리를 골랐다.

```text
[etcd]        /var/lib/etcd/member/snap/db
[containerd]  /var/lib/containerd/io.containerd.metadata.v1.bolt/meta.db
```

### overlayfs 실물

```text
root@worker01:/# mount | grep overlay | head -2
overlay on /run/.../1ed33c1a9bdb1.../rootfs type overlay
  (rw,relatime,
   lowerdir=/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/1/fs,
   upperdir=.../snapshots/26/fs,
   workdir=.../snapshots/26/work,...)

overlay on /run/.../69e9a54727cb5.../rootfs type overlay
  (rw,relatime,
   lowerdir=.../snapshots/1/fs,        ← 같다
   upperdir=.../snapshots/27/fs,       ← 다르다
   workdir=.../snapshots/27/work,...)
```

**`lowerdir` 이 같고 `upperdir` 이 다르다.** 레이어 공유가 실물로 확인된다.

저 두 마운트는 컨테이너가 아니라 **sandbox** 다. ID 가 `crictl pods` 의 POD ID 와 같고, 이미지 목록의 `registry.k8s.io/pause 320kB` 가 그 정체다. **모든 sandbox 가 pause 이미지 하나를 공유하므로 `lowerdir` 이 같다.**

```text
lowerdir   읽기 전용 이미지 레이어. 공유
upperdir   컨테이너의 쓰기 레이어. 각자

컨테이너 안에서 파일을 수정하면
  → lowerdir 에서 원본을 찾아 upperdir 로 복사 (copy-up)
  → 사본을 수정한다. 이미지 레이어는 그대로
```

**컨테이너를 지우면 `upperdir` 만 사라지고 이미지 레이어는 남는다.** 그래서 재생성이 빠르다.

```text
[노드마다 따로]  이미지 저장소 자체. worker01 과 worker02 는 각각 받는다
[노드에서 공유]  이미지 레이어 전부
[Pod 마다 따로]  컨테이너의 쓰기 레이어
```

`n558j` 가 worker02 에서 별도로 15.5초를 쓴 것이 첫 줄의 결과다.

---

# 5. Pod 를 지우면 다시 만든다

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete pod rs-demo-r5mw8
10:44:29
```

```text
10:44:29   rs-demo-r5mw8   Terminating       ← 아직 안 죽었다
10:44:29   rs-demo-9fs8c   Pending           ← 벌써 새 Pod 를 만들었다
10:44:30   rs-demo-r5mw8   Completed
10:44:31   rs-demo-9fs8c   Running
```

```text
[이벤트]
2026-08-13T01:44:29Z   Killing            Pod          rs-demo-r5mw8   Stopping container nginx
2026-08-13T01:44:29Z   SuccessfulCreate   ReplicaSet   rs-demo         Created pod: rs-demo-9fs8c
```

**같은 초에 반응했다.** ReplicaSet Controller 가 apiserver 에 watch 를 걸고 있어 폴링이 아니라 이벤트로 즉시 안다. 08 문서에서 정리한 "controller-manager 는 etcd 를 직접 안 보고 apiserver 에 watch 를 건다"가 여기서 동작한다.

## 발견 6 — 삭제 완료를 기다리지 않는다 ★★

```text
r5mw8 이 죽기 전에 9fs8c 를 만들었다. 잠깐 Pod 가 4개였다
```

```text
ReplicaSet 은 deletionTimestamp 가 찍힌 Pod 를 "이미 없는 것" 으로 센다
  → Terminating 을 빼고 세면 2개
  → 목표 3개니 1개 부족 → 즉시 만든다
```

**이것이 08 문서 실험 1의 "선언 4개인데 6개가 돌았다"를 설명한다.**

```text
[08 문서]  worker01 의 Pod 2개에 deletionTimestamp 가 찍혔다
           → ReplicaSet: "2개 없어졌네" → worker02 에 2개 생성
           → 그런데 worker01 의 kubelet 이 죽어 실제 컨테이너는 안 죽었다
           → 클러스터는 4개로 알고, 실제로는 6개

[지금]     같은 원리. 다만 간격이 1~2초라 문제가 안 된다
```

```text
"삭제 요청" 과 "삭제 완료" 사이의 간격이
곧 개수가 어긋나는 구간이다
```

```text
replicas: 3 의 정확한 의미
  "항상 정확히 3개"                    ✗
  "셀렉터에 맞는 게 최소 3개는 되게"    ✓
```

## 발견 7 — 태그 고정의 효과

```text
[처음 생성]
Pulled   rs-demo-r5mw8   Successfully pulled image "nginx:1.27" in 21.148s

[재생성]
Pulled   rs-demo-9fs8c   Container image "nginx:1.27" already present on machine
                         and can be accessed by the pod
```

**메시지 자체가 다르다.** 두 번째는 레지스트리에 묻지도 않았다.

```text
nginx:1.27   → imagePullPolicy: IfNotPresent → 있으면 안 받는다
nginx        → imagePullPolicy: Always       → 매번 레지스트리에 확인한다
```

**00 문서 4절의 규칙이 실측으로 확인됐다.**

```text
[pod-basic, nginx:latest]   재생성 때마다 Pulling → 1.6초
[rs-demo, nginx:1.27]       재생성 때 pull 없음 → 2초 만에 Running
```

**`Always` 의 비용은 재다운로드가 아니라 레지스트리 왕복이다.** 그래도 문제는 남는다.

```text
1. 레지스트리가 죽으면 노드에 이미지가 있어도 Pod 가 못 뜬다
2. 태그가 가리키는 대상이 바뀌면 같은 manifest 인데 다른 버전이 뜬다
```

## 발견 8 — 컨테이너 이름

```text
Killing   rs-demo-r5mw8   Stopping container nginx
                                             ^^^^^
```

manifest 에 `name: nginx` 라고 썼으니 그 이름이 쓰인다. 00 문서에서 `kubectl run` 으로 만들었을 때는 Pod 이름이 컨테이너 이름이었다.

```text
Pod 이름        rs-demo-r5mw8   generateName + 무작위
컨테이너 이름    nginx           우리가 지정
이미지          nginx:1.27
```

---

# 6. 소유권은 무엇으로 판단하는가 ★

Pod 에는 두 정보가 다 있다.

```text
metadata:
  labels:
    app: rs-demo                    ← 라벨
  ownerReferences:
  - kind: ReplicaSet, name: rs-demo ← 소유자
```

```text
[가설 A] ownerReferences 를 보고 안다
[가설 B] 라벨 셀렉터로 찾는다
```

**라벨만 바꿔보면 갈린다.**

## 실험 — 방출 (11:15)

```text
root@master01:/# kubectl get pods --show-labels
rs-demo-9fs8c   1/1   Running   29m   app=rs-demo
rs-demo-n558j   1/1   Running   36m   app=rs-demo
rs-demo-ss4gq   1/1   Running   36m   app=rs-demo

root@master01:/# date '+%H:%M:%S'; kubectl label pod rs-demo-9fs8c app=orphan --overwrite
11:15:28
pod/rs-demo-9fs8c labeled
```

```text
11:15:28   라벨 변경
11:15:29   rs-demo-m8rmv Pending      ← 1초 뒤
11:15:30   Running
```

```text
root@master01:/# kubectl get pods --show-labels
rs-demo-9fs8c   1/1   Running   31m   app=orphan
rs-demo-m8rmv   1/1   Running   14s   app=rs-demo
rs-demo-n558j   1/1   Running   38m   app=rs-demo
rs-demo-ss4gq   1/1   Running   38m   app=rs-demo
```

**가설 B 가 맞다.** `9fs8c` 는 죽지도 옮겨지지도 않았다. 라벨 한 값이 바뀌었을 뿐인데 ReplicaSet 의 시야에서 사라졌다.

## 발견 9 — `kubectl get rs` 는 정상이라고 말한다 ★★

```text
root@master01:/# kubectl get rs
NAME      DESIRED   CURRENT   READY   AGE
rs-demo   3         3         3       38m
```

**전부 3. 아무 문제 없어 보인다. 그런데 실제로는 nginx 가 4개 떠 있다.**

```text
ReplicaSet 이 보는 세계   app=rs-demo 인 Pod 3개. 목표 3개. 정상
실제 클러스터            nginx Pod 4개가 자원을 쓰고 있다
```

**셀렉터 밖의 것은 아예 안 보인다.** `kubectl get rs` 만 보면 이상을 발견할 수 없다.

## 발견 10 — ownerReferences 가 제거됐다

```text
root@master01:/# kubectl get pod rs-demo-9fs8c -o jsonpath='{.metadata.ownerReferences}{"\n"}'
(빈 출력)

root@master01:/# kubectl get pod rs-demo-m8rmv -o jsonpath='{.metadata.ownerReferences}{"\n"}'
[{"apiVersion":"apps/v1","blockOwnerDeletion":true,"controller":true,
  "kind":"ReplicaSet","name":"rs-demo","uid":"9b241099-..."}]
```

**우리는 라벨만 바꿨는데 `ownerReferences` 도 사라졌다.** Pod 오브젝트가 두 번 수정된 것이다. 첫 번째는 우리가, 두 번째는 컨트롤러가.

### 조정 루프에는 동작이 둘 있다

```text
1. 내 셀렉터에 맞는 Pod 를 전부 찾는다
2. 나를 소유자로 지목한 Pod 도 전부 찾는다
3. 두 목록을 비교해 어긋난 것을 바로잡는다

  [입양 adopt]    셀렉터에 맞는데 소유자가 없다   → ownerReferences 에 나를 추가
  [방출 release]  나를 지목했는데 셀렉터에 안 맞다 → ownerReferences 에서 나를 뺀다
```

**같은 조정 한 번에 방출과 생성이 동시에 일어났다.**

```text
라벨              사람이 정하는 것. 판단의 기준
ownerReferences   컨트롤러가 라벨을 보고 채워 넣는 것. 파생된 값
```

**`ownerReferences` 를 직접 고쳐도 소용없다.** 다음 조정 때 라벨 기준으로 다시 덮어쓴다.

## 실험 — 입양 (11:28)

```text
root@master01:/# date '+%H:%M:%S'; kubectl label pod rs-demo-9fs8c app=rs-demo --overwrite
11:28:23
```

```text
root@master01:/# kubectl get pod rs-demo-9fs8c -o jsonpath='{.metadata.ownerReferences}{"\n"}'
[{"apiVersion":"apps/v1","blockOwnerDeletion":true,"controller":true,
  "kind":"ReplicaSet","name":"rs-demo","uid":"9b241099-f046-4176-943f-eae1b68e7e01"}]
                                            ^^^^^^^^^ 방출 전과 같은 uid

root@master01:/# kubectl get pods --show-labels
rs-demo-9fs8c   1/1   Running   44m   app=rs-demo
rs-demo-n558j   1/1   Running   52m   app=rs-demo
rs-demo-ss4gq   1/1   Running   52m   app=rs-demo
```

**입양됐고, `m8rmv` 가 지워졌다. 라벨 실험 전과 정확히 같은 세 개다.**

```text
11:15:28   라벨 변경   → 9fs8c 방출, m8rmv 생성   (4개)
11:28:23   라벨 복구   → 9fs8c 입양, m8rmv 삭제   (3개)
```

**최종 상태만 보면 아무 일도 없었던 것 같다.** 흔적은 이벤트에만 남고, 그 이벤트도 TTL 이 지나면 사라진다.

```text
선언이 같으면 결과도 같다
→ 중간에 무엇을 했든 선언을 되돌리면 상태도 돌아온다
→ 다만 "어떻게 여기 도달했는지" 는 상태에 안 남는다
```

## 발견 11 — 삭제 우선순위

```text
2026-08-13T02:28:23Z   SuccessfulDelete   ReplicaSet   rs-demo   Deleted pod: rs-demo-m8rmv
```

**`SuccessfulDelete` 의 대상이 `ReplicaSet` 이다.** 우리가 지운 게 아니라 컨트롤러가 지웠다는 기록이다.

```text
[우리가 지운 것]      Killing  Pod  ...  Stopping container nginx
[컨트롤러가 지운 것]   SuccessfulDelete  ReplicaSet  ...  Deleted pod: ...
```

왜 `m8rmv` 였나. 컨트롤러에는 삭제 우선순위가 있다.

```text
1. 노드에 배정 안 된 Pod
2. Pending / Unknown
3. Ready 가 아닌 Pod
4. 같은 ReplicaSet 의 Pod 가 많이 몰린 노드의 Pod    ← 분산 유지
5. Ready 가 된 지 얼마 안 된 Pod
6. 재시작이 많은 Pod
7. 최근에 만들어진 Pod                               ← 어린 것부터
```

```text
worker01   9fs8c(44m), ss4gq(52m), m8rmv(12m)   3개 몰림
worker02   n558j(52m)                           1개

m8rmv   4번과 7번에 모두 걸린다
```

**두 규칙이 같은 답을 가리켜 어느 쪽이 결정적이었는지는 구분되지 않는다.**

```text
"오래 살아남은 Pod 를 남긴다"
→ 오래 버텼다는 것은 안정적이라는 신호다
```

> 위 우선순위 목록은 학습 데이터 기준이며 정확한 순서는 버전 문서 확인이 필요하다.
> 다만 **"어린 것부터 지운다"** 는 관측으로 확인됐다.

---

# 7. template 을 바꿔도 기존 Pod 는 안 바뀐다 ★★

이 문서의 결론이다.

```text
root@master01:/# sed -i 's/nginx:1.27/nginx:1.28/' /tmp/rs-demo.yaml
root@master01:/# date '+%H:%M:%S'; kubectl apply -f /tmp/rs-demo.yaml
11:30:56
replicaset.apps/rs-demo configured

root@master01:/# kubectl get rs rs-demo -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
nginx:1.28

root@master01:/# kubectl get pods -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[0].image'
NAME            IMAGE
rs-demo-9fs8c   nginx:1.27
rs-demo-n558j   nginx:1.27
rs-demo-ss4gq   nginx:1.27
```

**`configured` 라고 나왔으니 선언은 분명히 바뀌었다. 그런데 Pod 는 하나도 안 바뀌었다.**

```text
ReplicaSet 이 보는 것       "셀렉터에 맞는 Pod 가 3개인가?"
ReplicaSet 이 안 보는 것    "그 3개가 지금 template 과 같은가?"
```

## 지워야만 바뀐다

```text
root@master01:/# kubectl delete pod rs-demo-9fs8c
root@master01:/# kubectl get pods -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[0].image'
NAME            IMAGE
rs-demo-n558j   nginx:1.27
rs-demo-qqfbs   nginx:1.28     ← 새로 생긴 것만
rs-demo-ss4gq   nginx:1.27
```

```text
2026-08-13T02:31:48Z   Pulling   rs-demo-qqfbs   Pulling image "nginx:1.28"
2026-08-13T02:31:57Z   Pulled    rs-demo-qqfbs   in 9.077s. Image size: 62916597 bytes
```

**같은 ReplicaSet 안에서 버전이 섞였다.**

```text
사용자 요청이 세 Pod 에 분산된다
  → 어떤 요청은 1.27 이, 어떤 요청은 1.28 이 처리한다
  → 사용자마다 다른 동작을 겪는다
  → 누가 Pod 를 지우기 전까지 영원히 섞여 있다
```

```text
[사람이 직접 하려면]
  하나씩 지우고 뜰 때까지 기다리기를 반복해야 한다
  한 번에 다 지우면 잠깐 서비스가 0개가 된다

[되돌리려면]
  ReplicaSet 은 옛 template 을 어디에도 갖고 있지 않다
  사람이 기억해서 다시 써야 한다
```

**그래서 Deployment 가 있다.**

```text
Deployment
  ├── ReplicaSet (nginx:1.27)   replicas 3 → 2 → 1 → 0
  └── ReplicaSet (nginx:1.28)   replicas 0 → 1 → 2 → 3
                                 한쪽을 줄이며 한쪽을 늘린다

옛 ReplicaSet 을 안 지우고 남겨둔다 → 되돌리려면 다시 늘리면 된다
```

**이것이 02-deployment.md 의 주제다.**

> 이미지 크기가 태그마다 다르다.
> `nginx:1.27` 72,406,859 / `nginx:1.28` 62,916,597 / `nginx:latest` 63,135,215 bytes.
> **태그가 다르면 완전히 다른 이미지다.**

---

# 8. 지울 때 자식은 어떻게 되나

```text
root@master01:/# date '+%H:%M:%S'; kubectl delete rs rs-demo --cascade=orphan
11:34:51
replicaset.apps "rs-demo" deleted from k8s-lab namespace

root@master01:/# kubectl get pods -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[0].image'
rs-demo-n558j   nginx:1.27
rs-demo-qqfbs   nginx:1.28
rs-demo-ss4gq   nginx:1.27

root@master01:/# kubectl get pod -o jsonpath='{.items[0].metadata.ownerReferences}{"\n"}'
(빈 출력)

root@master01:/# kubectl get rs
No resources found in k8s-lab namespace.
```

**Pod 는 살아남았고 소유권만 사라졌다.** 버전이 섞인 채로 고아가 됐다.

```text
kubectl delete rs rs-demo                    기본 — Pod 도 같이 지운다
kubectl delete rs rs-demo --cascade=orphan   자식은 두고 부모만
```

**`blockOwnerDeletion: true` 가 기본 동작을 만들고, `--cascade=orphan` 은 그것을 일부러 무시하겠다는 선언이다.**

## 발견 12 — 소유권을 뗀 주체가 다르다

```text
[라벨을 바꿨을 때]   ReplicaSet Controller
                    "셀렉터에 안 맞으니 내 것이 아니다"

[orphan 삭제 때]    Garbage Collector
                    "부모가 사라지는데 자식은 남기라 했으니 연결을 끊는다"
```

**같은 필드를 서로 다른 컨트롤러가 각자의 이유로 고친다.**

## 이름은 관계를 나타내지 않는다

```text
rs-demo-n558j / rs-demo-qqfbs / rs-demo-ss4gq
```

**ReplicaSet 이 사라졌는데 이름에는 `rs-demo` 가 남아 있다.** 생성 당시 `generateName` 에서 온 문자열일 뿐이다.

## 발견 13 — 삭제는 세 단계다

고아가 된 Pod 를 지우면서 노드 쪽을 관찰했다.

```text
root@worker01:/# crictl pods        # kubectl delete pods 직후
28dd2910e9b2b   8 minutes ago       NotReady   rs-demo-qqfbs   k8s-lab
c669dd9890b36   About an hour ago   NotReady   rs-demo-ss4gq   k8s-lab

root@worker01:/# crictl pods        # 잠시 뒤
(두 줄이 사라짐)
```

```text
[1] Pod 오브젝트가 사라진다     kubectl get pods → 즉시
[2] sandbox 가 멈춘다           crictl pods → NotReady
       네트워크 네임스페이스 해체 / IP 를 Calico 블록에 반납
[3] sandbox 기록이 정리된다     crictl pods → 목록에서 사라짐
```

**`kubectl` 로는 [1]만 보인다.**

같은 출력의 `calico-node` ATTEMPT 0 sandbox 는 9일째 `NotReady` 로 남아 있는데, 차이는 Pod 오브젝트의 생존 여부다.

```text
Pod 가 사라진 sandbox                → 치울 대상. 곧 정리된다
Pod 는 살아있고 옛 sandbox 만 죽은 것  → 증거. --previous 로그 보존을 위해 남는다
```

상세는 [00-pod.md](00-pod.md) 10절 발견 34 참조.

---

# 정리

```text
 1. ReplicaSet 은 "개수를 유지" 하는 오브젝트다
    Pod 는 일회성 선언, ReplicaSet 은 지속적 선언

 2. Pod 를 한 번에 여러 개 만든다. 순서 개념이 없다
    이름은 generateName + 무작위 문자열

 3. 배치는 자원 점수만으로 정해지지 않는다
    같은 소유자의 Pod 를 흩어놓는 채점 항목이 있다 (2:1 로 갈렸다)

 4. 이미지 다운로드는 노드당 한 번, Pulling 이벤트는 Pod 마다 한 번
    IMAGE ID 는 내용의 해시라 노드가 달라도 같다
    lowerdir 공유 / upperdir 개별 — overlayfs 로 확인

 5. Pod 를 지우면 같은 초에 반응한다 (watch 기반)

 6. 삭제 완료를 기다리지 않는다
    deletionTimestamp 가 찍힌 Pod 를 "없는 것" 으로 센다
    → 08 문서의 "4개 선언에 6개 실행" 과 같은 구조

 7. 소유권은 라벨 셀렉터로 판단한다. ownerReferences 는 그 결과다
    방출과 입양이 대칭으로 일어난다

 8. 라벨이 어긋난 Pod 는 kubectl get rs 에서 안 보인다
    RS 는 3/3/3 정상인데 실제로는 4개가 돌고 있었다

 9. 삭제 우선순위가 있다. 어리고 몰린 것부터 지운다

10. template 을 바꿔도 기존 Pod 는 안 바뀐다        ★
    지워야만 새 template 으로 만들어진다
    그동안 버전이 섞이고, 되돌릴 방법도 없다
    → Deployment 가 필요한 이유

11. 삭제 시 기본은 cascade. --cascade=orphan 으로 자식을 남길 수 있다
    소유권을 떼는 주체가 상황에 따라 다르다 (RS Controller / GC)
```

# 실습 리소스

```text
namespace   k8s-lab      유지
rs-demo     삭제됨       --cascade=orphan
Pod 3개     삭제됨       kubectl delete pods -l app=rs-demo
/tmp/rs-demo.yaml        정리
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              1절 — 개수를 유지하는 지속적 선언
2. 생성 시 동작하는 Controller   ReplicaSet Controller
                                이벤트 대상(KIND)으로 주체가 갈린다 (3절)
3. 주요 Spec 과 Status 필드     replicas / selector / template
                                status: replicas, readyReplicas, availableReplicas,
                                        fullyLabeledReplicas, observedGeneration
4. 다른 오브젝트와의 연결        Pod(ownerReferences), Node(배치), Deployment(상위)
5. 장애 사례                    6절 라벨 어긋남 — RS 는 정상인데 Pod 가 하나 더
                                7절 버전 섞임
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            셀렉터 중복 / kubectl get rs 만 보면 안 됨 /
                                template 변경은 반영되지 않음
```

# 미확인 목록

```text
1. 분산 채점의 정확한 플러그인 이름과 기본 설정
2. 삭제 우선순위의 정확한 순서 (버전 문서 확인 필요)
   "어린 것부터" 는 관측으로 확인, 4번과 7번의 분리는 미확인
3. managedFields 로 "누가 ownerReferences 를 고쳤는지" 직접 확인 (미실행)
   kubectl get pod X -o jsonpath='{range .metadata.managedFields[*]}{.manager}{"\t"}{.operation}{"\t"}{.time}{"\n"}{end}'
4. minReadySeconds 를 줬을 때 availableReplicas 가 실제로 늦게 오르는지
5. fullyLabeledReplicas 가 template 과 라벨이 다른 Pod 를 어떻게 세는지
6. selector 와 template 라벨을 일부러 다르게 썼을 때 apiserver 의 거부 메시지
7. kubelet 의 이미지 pull 직렬화 설정값
   /var/lib/kubelet/config.yaml 의 serializeImagePulls / maxParallelImagePulls
8. 두 ReplicaSet 이 같은 셀렉터를 쓸 때의 실제 동작 (미실험)
```

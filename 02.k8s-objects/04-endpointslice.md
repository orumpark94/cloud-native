# 04. EndpointSlice

2단계 다섯 번째 오브젝트. **03 에서 겉만 보고 남겨둔 것을 마무리한다.**

```text
[03 에서 본 것]
  EndpointSlice Controller 가 만든다
  ownerRef 가 Service 다
  주소 목록이 Pod IP 와 일치한다
  Pod 삭제 시 1초 만에 빠지고 3초 만에 새 것이 들어온다

[03 에서 못 본 것]
  conditions 를 직접 조회하지 않았다 — "+ 1 more" 를 보고 추측만 했다
  Pod 를 지우는 경우만 봤다 — Pod 가 살아있는데 앱만 죽는 경우는 안 봤다
  왜 Endpoints 가 아니라 EndpointSlice 인지 모른다
```

## 이 문서의 범위

```text
[확인한 것]
  1. 이것이 푸는 문제 — Pod 는 Running 인데 앱이 응답을 못 할 때        ✅
  2. livenessProbe 와 readinessProbe 의 차이                          ✅
  3. conditions 세 필드 직접 조회                                     ✅
  4. readinessProbe 를 실패시켜 라우팅에서 빼기                        ✅ ★
  5. 나갈 때와 들어올 때의 시간 측정                                   ✅
  6. probe 를 잘못 짜면 생기는 일                                     ✅ (개념)
  7. 왜 Endpoints 가 아니라 EndpointSlice 인가                        ✅
  8. 라벨과 소유권                                                    ✅

[다루지 않는 것]
  startupProbe 실측        개념만. 기동이 느린 앱이 없다
  topology aware routing   nodeName 필드의 용도만 언급
  조각이 실제로 나뉘는 것   Pod 가 100개 넘어야 볼 수 있다
  probe 설계 실습          4단계에서 실제 앱으로
```

---

# 1. 이것이 푸는 문제

03 의 실험은 전부 **Pod 가 죽을 때**였다. 반대 경우가 남아 있다.

```text
[03 — Pod 가 죽을 때]      kubectl delete pod → SIGTERM → 라우팅에서 제거
[여기 — Pod 가 새로 뜰 때]  언제 라우팅에 넣을 것인가
```

## readiness 의 주된 용도는 "안 넣기" 다 ★

**이름이 그 뜻이다.**

```text
readiness = 준비됨
"고장났으니 빼라" 가 아니라 "아직 준비가 안 됐으니 넣지 마라"
```

```text
[배포할 때마다 — 안 넣기]      Pod 를 교체할 때마다 거치는 과정
[가끔 — 빼기]                 돌던 Pod 가 응답을 못 하게 된 경우
```

**이 문서의 실험(5절)은 두 번째를 인위적으로 만든 것이다.** 실제로 더 자주 동작하는 것은 첫 번째다.

## "넣는다 / 뺀다" 의 대상 — 최종적으로는 iptables 규칙 한 줄이다

```text
kubelet 이 probe 결과를 판정한다
   │
   ▼
Pod 의 Ready 조건                    kubectl get pods 의 READY 열 (1/1 ↔ 0/1)
   │
   ▼
EndpointSlice 의 conditions.ready    "이 주소로 보내도 되나"
   │
   ▼
kube-proxy 가 ready:true 인 것만 규칙으로 만든다
   │
   ▼
iptables 의 KUBE-SEP 규칙 한 줄       실제로 패킷이 가느냐 마느냐
```

```text
KUBE-SEP-DHPDNDPX7SXWVAIJ  ... /* k8s-lab/web-svc -> 10.244.5.53:80 */
                                                     ^^^^^^^^^^^ 이 Pod
```

**5·6절에서 `SEP=3 → SEP=2` 로 세는 것이 이 줄 수다.**

## 다만 kube-proxy 는 한 줄만 지우지 않는다 ★

```text
[그렇게 보이는 것]  "10.244.5.53 규칙을 지운다"

[실제]  지금 ready:true 인 주소가 뭐뭐지? → 2개
        → 그 2개로 KUBE-SVC 체인을 처음부터 다시 만든다
```

**근거는 03 문서 발견 10이다.**

```text
[Pod 3개]  0.33333333349 / 0.5 / (없음)
[Pod 2개]  0.5 / (없음)                    ← 확률이 재계산됐다
[Pod 3개]  0.33333333349 / 0.5 / (없음)

한 줄만 지웠다면 남은 줄의 확률이 그대로여야 한다
```

03 문서 발견 16(kube-proxy 복구 시 SEP 체인 이름이 전부 바뀜)도 같은 근거다.

```text
"바뀐 것을 반영한다" 가 아니라 "현재 상태를 보고 다시 만든다"

ReplicaSet   "몇 개여야 하지? 지금 몇 개지?"  → 차이만큼 조정
Deployment   "어떤 RS 가 몇 개여야 하지?"     → 조정
kube-proxy   "지금 ready 인 주소가 뭐지?"     → 규칙 재작성

→ 중간에 죽었다 살아나도 복구된다. 놓친 변경을 따라잡을 필요가 없다
```

## 근거 — 발견 4의 8초

```text
Started 2m41s → Unhealthy(connection refused) → 약 8초 뒤 Ready
```

```text
고장이 아니다. nginx 가 아직 포트를 안 연 것이다
Kubernetes 는 "컨테이너가 떴다" 까지만 안다
그다음 걸리는 시간은 앱마다 다르다
```

## 롤링 업데이트가 이것 위에 서 있다 ★

```text
maxUnavailable 0 → "Ready 인 Pod 가 3개 밑으로 안 떨어지게 하라"
                          ^^^^^  Running 이 아니다
```

```text
readinessProbe 가 없으면
  → 새 Pod 가 Started 되는 순간 Ready 로 친다
  → Deployment 가 옛 Pod 를 죽인다
  → 새 Pod 는 아직 기동 중이다 → 트래픽이 갈 곳이 없다

readinessProbe 없는 롤링 업데이트는 무중단이 아니다
```

**02 문서의 롤아웃이 매끄러웠던 것은 nginx 가 빨리 뜨는 이미지였기 때문이다.**

## 그리고 돌다가 문제가 생기는 경우

```text
Pod 는 Running 이다. 컨테이너도 안 죽었다.
그런데 앱이 응답을 못 한다.

  DB 커넥션 풀이 고갈됐다
  스레드가 전부 물려 있다
  힙이 차서 요청 처리를 못 한다
```

```text
Kubernetes 입장에서는 아무 일도 안 일어난 상태다
컨테이너가 살아있으니 kubelet 이 재시작할 이유도 없다
        ↓
그런데 트래픽은 계속 그 Pod 로 간다
```

```text
"살아있음" 과 "일할 수 있음" 을 따로 판정해야 한다
```

> **노드 리소스 부족은 여기 해당하지 않는다.**
> 노드 압박(pressure)은 kubelet 이 별도로 처리하며 Pod 를 축출(evict)한다.
> readiness 는 앱 안쪽만 본다. 노드 사정은 안 본다.

---

# 2. livenessProbe 와 readinessProbe

## 발견 1 — 정의하는 자리는 같다

```yaml
spec:
  containers:
  - name: nginx
    livenessProbe:      # ← 같은 위치
    readinessProbe:     # ← 같은 위치
```

**"readiness 는 컨테이너, liveness 는 Pod" 가 아니다.** 둘 다 컨테이너 단위 필드이고 kubelet 이 검사한다.

## 발견 2 — 차이는 "실패하면 무엇을 하느냐" 다

```text
livenessProbe 실패
  → kubelet 이 그 컨테이너를 죽이고 다시 띄운다
  → RESTARTS 가 올라간다
  → 노드 안에서 끝난다. apiserver 없이도 동작한다

readinessProbe 실패
  → 아무것도 안 죽인다
  → kubelet 이 apiserver 에 보고
  → EndpointSlice Controller 가 ready:false 로 고친다
  → kube-proxy 가 iptables 에서 뺀다
```

```text
liveness    "고쳐야 하나?"    → 재시작으로 답한다
readiness   "보내도 되나?"    → 라우팅으로 답한다
```

## 발견 3 — 작용 단위는 반대다 ★

```text
liveness 실패    그 컨테이너 하나만 재시작
                 같은 Pod 의 다른 컨테이너는 멀쩡히 돈다
                 → 컨테이너 단위

readiness 실패   Pod 전체가 라우팅에서 빠진다
                 컨테이너 3개 중 1개만 실패해도 Pod 통째로
                 → Pod 단위
```

**이유는 IP 가 Pod 에 붙어 있기 때문이다.** `00-pod.md` 9절에서 확인한 것이다.

```text
Pod 안의 컨테이너들은 netns 를 공유한다 → IP 가 하나다
EndpointSlice 에 들어가는 것은 Pod IP 다
→ "이 컨테이너만 빼기" 가 불가능하다
→ 하나라도 안 되면 Pod 통째로 뺄 수밖에 없다
```

```text
트래픽의 단위는 Pod 다. 컨테이너가 아니다.
```

## 표

```text
                    livenessProbe          readinessProbe
──────────────────────────────────────────────────────────
쓰는 자리            컨테이너                컨테이너 (같다)
검사 주체            kubelet                kubelet (같다)
실패하면             컨테이너 재시작         EndpointSlice 에서 제거
무엇이 죽나           컨테이너               아무것도 안 죽는다
작용 범위            그 컨테이너만           Pod 전체
결과가 가는 곳        노드 안에서 끝          apiserver → 컨트롤러 → kube-proxy
RESTARTS 증가        O                      X
```

---

# 3. 실습 환경 (2026-08-20)

```yaml
# /tmp/web.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
      - name: nginx
        image: nginx:1.27
        ports:
        - containerPort: 80
        lifecycle:
          postStart:
            exec:
              command:
              - sh
              - -c
              - "echo ok > /usr/share/nginx/html/healthz; hostname > /usr/share/nginx/html/index.html"
        readinessProbe:
          httpGet:
            path: /healthz
            port: 80
          initialDelaySeconds: 2
          periodSeconds: 2
          failureThreshold: 2
---
apiVersion: v1
kind: Service
metadata:
  name: web-svc
spec:
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 80
```

**실험 방법이 여기서 나온다.**

```text
postStart 가 healthz 파일을 만든다
readinessProbe 가 GET /healthz 를 한다

healthz 를 지우면
  → nginx 가 404 를 준다 → 2xx 가 아니다 → 실패
  → 2초 뒤 또 실패 → failureThreshold 2 도달 → Not Ready

컨테이너는 안 죽는다. index.html 은 그대로라 GET / 는 여전히 200 이다
```

**`periodSeconds: 2` / `failureThreshold: 2` 로 줄인 이유**: 기본값(10초, 3회)이면 최대 30초가 걸려 관측이 지루하다.

## 발견 4 — 기동 중에 readiness 가 실패하는 것은 정상이다

```text
Normal   Scheduled  3m15s   Successfully assigned ... to worker01
Normal   Pulled     2m43s   Container image "nginx:1.27" already present
Normal   Created    2m42s   Container created
Normal   Started    2m41s   Container started
Warning  Unhealthy  2m37s (x3 over 2m39s)
         Readiness probe failed: Get "http://10.244.5.53:80/healthz":
         dial tcp 10.244.5.53:80: connect: connection refused
```

```text
Started    컨테이너가 시작됐다 — 아직 nginx 가 포트를 안 열었다
2초 뒤     첫 검사 (initialDelaySeconds: 2) → connection refused
그 뒤      nginx 가 뜬다 → 성공 → Ready
```

**"컨테이너가 시작됐다" 와 "요청을 받을 수 있다" 사이에 간격이 있다.**

```text
readinessProbe 가 없었다면
  → Started 되자마자 EndpointSlice 에 들어간다
  → 아직 포트도 안 열린 Pod 로 트래픽이 간다
```

**03 의 preStop 과 짝이다.**

```text
[03 — Pod 가 죽을 때]      preStop 이 없으면 죽는 Pod 로 요청이 간다
[여기 — Pod 가 새로 뜰 때]  readinessProbe 가 없으면 아직 못 뜬 Pod 로 요청이 간다
```

## 발견 5 — 실패 메시지가 세 종류다

```text
connection refused    포트 자체가 안 열렸다        기동 중
404 / 500             포트는 열렸는데 응답이 틀렸다  이번 실험에서 만들 것
timeout               열렸는데 응답이 안 온다       앱이 멈춘 경우
```

**셋 다 "Not Ready" 로 똑같이 처리되지만 원인 진단은 완전히 다르다.**

---

# 4. 기준선 — 네 층이 다 일치하는 상태

```text
root@master01:/# kubectl get pods -o wide
web-7f9bd7b4d7-dhvjr   1/1   Running   0   10.244.5.53    worker01
web-7f9bd7b4d7-rnh8b   1/1   Running   0   10.244.5.54    worker01
web-7f9bd7b4d7-z8psg   1/1   Running   0   10.244.30.94   worker02

root@master01:/# kubectl get endpointslices -o jsonpath='...'
["10.244.30.94"]  {"ready":true,"serving":true,"terminating":false}  ...-z8psg
["10.244.5.53"]   {"ready":true,"serving":true,"terminating":false}  ...-dhvjr
["10.244.5.54"]   {"ready":true,"serving":true,"terminating":false}  ...-rnh8b

root@master01:/# sudo iptables -t nat -L KUBE-SVC-UNTI3ZWT6KQG4YW5 -n
KUBE-SEP-M5SE3ZPRKH72OHFK  -> 10.244.30.94:80  probability 0.33333333349
KUBE-SEP-DHPDNDPX7SXWVAIJ  -> 10.244.5.53:80   probability 0.50000000000
KUBE-SEP-IHK5LAH22MDEV3OZ  -> 10.244.5.54:80

root@master01:/# for i in $(seq 1 30); do curl -s http://10.98.126.22; done | sort | uniq -c
      6 web-7f9bd7b4d7-dhvjr
     12 web-7f9bd7b4d7-rnh8b
     12 web-7f9bd7b4d7-z8psg
```

## 발견 6 — 체인 이름은 ClusterIP 로 만들지 않는다 ★

```text
[03 실습 — 8/14]
  ClusterIP  10.106.225.222
  체인        KUBE-SVC-UNTI3ZWT6KQG4YW5

[오늘 — Service 를 지웠다 새로 만듦]
  ClusterIP  10.98.126.22               ← 다르다
  체인        KUBE-SVC-UNTI3ZWT6KQG4YW5  ← 같다
```

```text
체인 이름은 네임스페이스 + Service 이름 + 포트 + 프로토콜 로 만든다
  k8s-lab / web-svc / 80 / TCP

ClusterIP 는 들어가지 않는다
```

**운영상 쓸모가 있다.**

```text
Service 를 다시 만들어도 KUBE-SVC 체인 이름은 안 바뀐다
→ 감시 스크립트에 박아둬도 계속 쓸 수 있다

SEP 체인 이름은 Pod IP 기반이라 매번 바뀐다
→ 03 에서 kube-proxy 복구 후 SEP 이름이 바뀐 것이 이 이유다
```

## 발견 7 — 확률 분포는 표본이 작으면 흔들린다

```text
30번 → 기대값 10/10/10 → 실제 6/12/12
```

03 에서 20번에 6/6/8, 300번에 99/101/100 이었던 것과 같다.

---

# 5. 실험 — 앱만 죽인다 ★

```bash
kubectl exec web-7f9bd7b4d7-dhvjr -- rm /usr/share/nginx/html/healthz
```

## 결과 (첫 회 — 전이 순간은 놓침)

```text
예측                            실측                     결과
──────────────────────────────────────────────────────────────
STATUS Running 유지              0/1 Running              ✓
RESTARTS 0 유지                  0                        ✓
READY 1/1 → 0/1                  0/1                      ✓
conditions ready true → false    10.244.5.53=false        ✓
SEP=3 → 2                        SEP=2                    ✓
FAIL 안 남                       80초간 0회                ✓
```

## 발견 8 — Pod 는 멀쩡한데 라우팅에서 빠졌다 ★★

```text
STATUS    Running       ← 컨테이너는 멀쩡히 돈다
RESTARTS  0             ← 아무도 재시작 안 했다
READY     0/1           ← 그런데 트래픽은 안 간다
```

**liveness 였다면 RESTARTS 가 1이 됐을 것이다.**

## 발견 9 — conditions 조합이 03 과 다르다 ★

```text
root@master01:/# kubectl get endpointslices -o jsonpath='...'
["10.244.30.94"]  {"ready":true,"serving":true,"terminating":false}
["10.244.5.53"]   {"ready":false,"serving":false,"terminating":false}   ★
["10.244.5.54"]   {"ready":true,"serving":true,"terminating":false}
```

```text
[03 — Pod 를 지웠을 때]   ready:false  serving:false  terminating:true
[여기 — probe 실패]       ready:false  serving:false  terminating:false
                                                       ^^^^^^^^^^^^^^^
                                                       Pod 는 안 죽는다
```

**kube-proxy 는 `ready` 만 보므로 규칙에서 빠지는 것은 같다.** 하지만 운영상 완전히 다른 상황이고 `terminating` 이 그것을 구분한다.

```text
terminating: true   곧 사라진다. 새 Pod 가 대신 뜬다
terminating: false  안 사라진다. 앱이 회복되면 다시 들어온다
```

**옛 `Endpoints` 로는 이 구분을 표현할 수 없다.** 7절 참조.

---

# 6. 시간 측정 — 양방향

## 실측

```text
[라우팅에 들어올 때 — healthz 생성]
  09:31:14   echo ok > healthz
  09:31:16   ready=true / READY 1/1 / SEP=3 / 트래픽에 dhvjr 등장
             ──── 2초

[라우팅에서 빠질 때 — healthz 삭제]
  09:31:40   rm healthz
  09:31:44   ready=false / READY 0/1 / SEP=2
             ──── 4초
```

```text
예측                                 실측
─────────────────────────────────────────
빠질 때   failureThreshold 2 → 최대 4초   4초  ✓
들어올 때  successThreshold 1 → 최대 2초   2초  ✓
```

## 발견 10 — 지연의 대부분은 probe 설정이다 ★★

```text
[빠질 때 4초]
  probe 두 번 실패            최대 4초  (2초 주기 × 2회)
  kubelet → apiserver → 컨트롤러 → kube-proxy → iptables
                              1초 미만  ← 나머지 전부

[들어올 때 2초]
  probe 한 번 성공            최대 2초
  전파                        1초 미만
```

**세 층이 같은 초에 바뀌었다.**

```text
09:31:16  ready=true  /  READY 1/1  /  SEP=3
09:31:44  ready=false /  READY 0/1  /  SEP=2
```

```text
"라우팅 반영이 느리다" 는 대개 인프라 문제가 아니다
probe 설정이 그 시간을 정한다
```

**03 의 preStop 과 성격이 다르다.**

```text
[03 preStop]  네트워크 전파가 느려서 시간을 벌어줬다
[여기]        전파는 빠르다. 판정을 일부러 늦게 하는 것이다
```

## 발견 11 — 기본값이면 최대 30초다

```text
[우리 설정]  periodSeconds 2,  failureThreshold 2   →  최대 4초
[기본값]     periodSeconds 10, failureThreshold 3   →  최대 30초 ★
```

```text
초당 1000 요청 / Pod 3개 중 1개 고장
→ 30초 × 333건 = 약 10,000건이 그 Pod 로 간다
```

**"장애가 났는데 왜 30초나 에러가 나갔지" 의 흔한 원인이다.**

## 발견 12 — 비대칭은 의도된 것이다

```text
failureThreshold 3 (기본)   세 번 봐야 뺀다
successThreshold 1 (기본)   한 번 되면 넣는다
```

```text
probe 는 일시적으로 실패할 수 있다 (GC, 순간적 지연)

한 번 실패로 뺐다면
  → 모든 Pod 가 동시에 잠깐 느려지면 전부 빠진다
  → EndpointSlice 가 비어버린다 → 서비스 전체 다운

준비된 Pod 를 늦게 넣으면
  → 남은 Pod 가 더 받는다. 손해지만 죽지는 않는다
```

**덜 위험한 쪽으로 기울인 기본값이다.**

## 발견 13 — probe 가 실패해도 요청은 성공했다 ★

```text
09:31:40   rm healthz          앱이 "준비 안 됨" 이 된 시점
09:31:40   dhvjr 가 응답        그런데 정상 200
09:31:44   SEP=2 (제거됨)
```

**FAIL 은 전 구간 0회였다.**

```text
지운 것은 /healthz 뿐. GET / 는 index.html 을 그대로 준다
→ "probe 는 실패인데 서비스는 정상" 인 Pod 를 뺀 것이다
```

```text
readinessProbe 는 앱의 자기 신고다
진짜 일할 수 있는지와 다를 수 있다

  probe 실패 + 실제 정상   멀쩡한 Pod 를 뺀다     ← 실험 A
  probe 성공 + 실제 고장   고장난 Pod 로 계속 보낸다  ← 실험 B
```

---

# 5-B. 실험 B — probe 는 성공하는데 앱이 고장난 경우 ★★

실험 A 의 정확한 반대다. **probe 가 보는 파일은 두고, 실제 서비스 파일만 지운다.**

```bash
date '+%H:%M:%S'; kubectl exec web-7f9bd7b4d7-z8psg -- rm /usr/share/nginx/html/index.html
```

```text
GET /healthz  → 파일이 있다 → 200 → probe 성공
GET /         → 인덱스 파일이 없다 → nginx 가 403 (디렉터리 목록은 기본 off)
```

## 타임라인

```text
11:14:16   rm index.html
11:14:17   첫 403                    ← 1초 안에
```

```text
11:14:03 ~ 11:14:28   1/1 1/1 1/1  SEP=3    ← 한 번도 안 변했다
```

```text
root@master01:/# curl -s -o /dev/null -w '%{http_code}\n' http://10.244.30.94
403

root@master01:/# kubectl describe pod web-7f9bd7b4d7-z8psg | sed -n '/^Events/,$p'
Events:      <none>
```

> Pod 나이가 144분이고 이벤트 기본 보관은 1시간이므로 기동 때의 이벤트는 만료됐다.
> **5분 전에 일으킨 이 장애가 이벤트를 남기지 않았다는 뜻이다.**
> 실험 A 는 `Warning Unhealthy` 를 남겼다.

## 발견 9-1 — 반응 시간이 0초다

```text
[실험 A]  4초    검사하고 판정하는 단계가 있었다
[실험 B]  0초    판정하는 단계가 아예 없다. 요청이 그냥 실패한다
```

**아무도 판단하지 않으니 지연도 없다.**

## 발견 9-2 — 네 층 중 세 층이 "정상" 이라고 한다 ★★

```text
kubectl get pods       1/1 Running    정상이라고 한다
EndpointSlice          ready: true    정상이라고 한다
iptables               SEP=3          정상이라고 한다
curl                   1/3 이 403     ← 여기만 안다
```

```text
11:14:17 이후 23회 중 <html>(403 에러 페이지)이 10회
z8psg 라는 이름은 한 번도 안 나온다 → 그 Pod 로 간 요청이 전부 403
```

**지금 가진 도구로는 이 장애를 못 잡는다.** 로드맵이 "장애 실험 전에 관측 환경을 먼저 구성한다" 고 정한 근거가 실측으로 나왔다.

```text
[5단계에서 붙일 것]
  응답 코드별 비율 (2xx / 4xx / 5xx)
  → 403 이 33% 나오는 것을 본다
  → 그때 처음 "뭔가 잘못됐다" 를 안다
```

## 발견 9-3 — 두 실험 대조표

```text
                    실험 A (/healthz 삭제)   실험 B (index.html 삭제)
──────────────────────────────────────────────────────────────────
probe               실패                     성공
READY               1/1 → 0/1                1/1 유지
EndpointSlice       ready:false              ready:true
iptables            SEP 3 → 2                SEP=3 유지
실제 응답            전부 200                  1/3 이 403
반응 시간            4초                       0초
kubectl 로 보이나    보인다                    안 보인다 ★
Events              Warning Unhealthy         <none>
결과                멀쩡한 Pod 를 뺐다         고장난 Pod 로 계속 보낸다
```

## 발견 9-4 — probe 와 관측의 역할이 갈린다

앞의 교훈("probe 에 이것저것 넣지 마라")과 충돌하는 것처럼 보이지만 역할이 다르다.

```text
probe    "이 Pod 를 라우팅에서 뺄까 말까"
         → 자동으로 조치한다
         → 잘못 판단하면 서비스를 죽인다
         → 그래서 보수적이어야 한다

관측     "지금 서비스가 제대로 되고 있나"
         → 사람에게 알린다
         → 잘못 울려도 사람이 판단한다
         → 그래서 넓게 봐도 된다
```

```text
어떤 probe 도 모든 고장을 잡을 수는 없다
probe 는 "이 Pod 가 아예 못 쓰게 됐는가" 를 잡는 도구다
"모든 응답이 정상인가" 는 관측의 영역이다
```

---

# 7. probe 설계 원칙 (개념)

## 검사 방법 네 가지

```text
httpGet     경로를 GET. 응답이 200~399 면 성공
            성공 코드 범위는 바꿀 수 없다 (ALB 와 다른 점)
tcpSocket   TCP 연결이 되면 성공. DB, 메시지큐 등 HTTP 가 아닌 것에
exec        컨테이너 안에서 명령 실행. 종료 코드 0 이면 성공
grpc        gRPC 헬스체크 프로토콜
```

> gRPC probe 의 정확한 도입 버전은 확인하지 않았다. 공식 문서 확인 필요.

**`tcpSocket` 은 주의해야 한다.**

```text
앱이 완전히 멈췄는데 포트는 열려 있을 수 있다
→ tcpSocket 은 성공한다 → "죽었는데 살아있다고 판정"
```

## 안 쓰면 어떻게 되나

```text
readinessProbe 없음   컨테이너가 Started 되는 즉시 Ready
                      → 발견 4 의 상황이 그대로 사고가 된다

livenessProbe 없음    프로세스가 살아있으면 살아있다고 본다
                      → 데드락에 빠져도 아무도 모른다
                      → 00-pod.md 의 PID 1 문제와 이어진다
```

## 무엇을 넣어야 하나

```text
livenessProbe — "재시작하면 나아지는가?" 만

  넣어도 되는 것   데드락 / 이벤트 루프 정지 / 힙 고갈
  넣으면 안 되는 것 ★  DB 연결 확인, 외부 API 호출
    → DB 가 죽으면 모든 Pod 가 동시에 재시작
    → 재시작해도 DB 는 여전히 죽어 있다 → CrashLoopBackOff
```

```text
readinessProbe — "지금 요청을 받아도 되는가"

  넣어도 되는 것   기동 완료 / 캐시 로딩 완료 / 커넥션 풀 준비
  신중해야 할 것 ★  외부 시스템 상태
    → 느려지면 모든 Pod 가 동시에 빠진다 → 서비스 전체 다운
```

**가장 흔한 사고 패턴**

```text
/health 하나에 DB, Redis, 외부 API 를 전부 확인하게 짜고
liveness 와 readiness 양쪽에 똑같이 걸었다
        ↓
Redis 가 3초 느려진다 → 모든 Pod Not Ready → 서비스 다운
동시에 liveness 도 실패 → 전부 재시작 → 더 악화
```

```text
경로를 나눈다
  /livez   내 프로세스가 정상인가만. 외부 의존성 없음
  /readyz  요청을 받을 준비가 됐나
```

**Kubernetes 자체도 이 두 경로를 쓴다.** 1단계에서 `kubectl get --raw='/readyz?verbose'` 로 확인한 것이다.

## startupProbe

```text
[문제]  Java 앱이 뜨는 데 90초 걸린다
        livenessProbe 를 30초로 잡으면 기동 중에 재시작 → 무한 루프

[해결]  startupProbe 가 성공할 때까지 liveness/readiness 를 시작하지 않는다
        기동 중에는 느슨하게, 기동 후에는 촘촘하게
```

```yaml
startupProbe:
  periodSeconds: 10
  failureThreshold: 30      # 최대 300초까지 기다린다
livenessProbe:
  periodSeconds: 5          # 기동 후에는 5초마다
```

```text
                 무엇을 판단하나        실패하면          외부 의존성
─────────────────────────────────────────────────────────────────
startupProbe     기동이 끝났나          다른 probe 보류    X
livenessProbe    재시작이 필요한가       컨테이너 재시작    절대 X
readinessProbe   요청을 받아도 되나      라우팅에서 제외    신중히
```

> 로드맵 4단계에 `Liveness, Readiness, Startup Probe` 가 명시돼 있다.
> 실제 앱으로 다시 다룬다. 여기서는 EndpointSlice 문맥에 필요한 만큼만.

---

# 8. 왜 Endpoints 가 아니라 EndpointSlice 인가

## 발견 14 — 둘 다 있고, 옛것은 폐기 예정이다

```text
root@master01:/# kubectl api-resources | grep -i endpoint
endpoints          ep   v1                        true   Endpoints
hostendpoints           crd.projectcalico.org/v1  false  HostEndpoint
endpointslices          discovery.k8s.io/v1       true   EndpointSlice

root@master01:/# kubectl get endpoints
Warning: v1 Endpoints is deprecated in v1.33+; use discovery.k8s.io/v1 EndpointSlice
NAME      ENDPOINTS
web-svc   10.244.30.94:80,10.244.5.53:80,10.244.5.54:80
```

**이 클러스터는 v1.35 이므로 이미 폐기 이후다.** 호환을 위해 자동 생성만 유지된다.

## 담긴 정보의 차이

```text
[Endpoints]
  주소와 포트만

[EndpointSlice]
- addresses: [10.244.30.94]
  conditions: {ready: true, serving: true, terminating: false}
  nodeName: worker02
  targetRef: {kind: Pod, name: ...-z8psg, namespace: k8s-lab, uid: 1d2e...}
```

## 바꾼 이유 1 — 크기

```text
Service 하나 = Endpoints 오브젝트 하나
Pod 가 1000개면 오브젝트 하나에 주소 1000개

etcd 는 오브젝트 하나의 크기에 제한이 있다 (기본 약 1.5MB)
→ 수천 개에서 한계에 부딪힌다
```

## 바꾼 이유 2 — 갱신 비용 ★ 가장 큰 이유

```text
Pod 하나가 바뀌면 오브젝트를 다시 쓴다
그런데 이 오브젝트는 "전체" 다
→ 주소 1000개가 통째로 저장되고
→ watch 하는 모든 노드에게 통째로 전송된다

kube-proxy 는 노드마다 하나씩 있다 (03 에서 확인)
노드가 500대면 500곳이 watch 한다

Pod 하나 바뀔 때마다  100KB × 500 = 50MB
1000개 롤링 업데이트   1000 × 50MB = 50GB
```

> 위 수치는 규모를 보이기 위한 계산이고 **실제 한계값은 확인하지 않았다.**
> Kubernetes 공식 문서의 EndpointSlice 항목 확인 필요.

## 해결 — 조각으로 나눈다

```text
[Endpoints]
  web-svc ─── 주소 1000개 (오브젝트 1개)
          Pod 1개 변경 → 1000개 전부 재전송

[EndpointSlice]
  web-svc-abc12 ─── 100개
  web-svc-def34 ─── 100개    Pod 1개 변경 → 그 조각만 재전송
  ...  (조각 10개)                          → 1/10
```

```text
조각 크기 기본값 100
kube-controller-manager 의 --max-endpoints-per-slice (최대 1000)
```

**"Slice" 는 이것을 말한다.** 이번 실습은 Pod 가 3개라 조각도 1개뿐이었다.

## 나뉜 것은 저장과 전송뿐이다

```text
바뀐 것    apiserver 가 etcd 에 저장하고 노드에 전달하는 단위
안 바뀐 것  ClusterIP / KUBE-SVC 체인 / 부하 분산 방식

조각이 10개여도 iptables 규칙은 한 체인에 다 들어간다
```

## 바꾼 이유 3 — 표현력

```yaml
# Endpoints 의 구조
subsets:
- addresses:            # 준비된 것
  - ip: 10.244.5.53
  notReadyAddresses:    # 준비 안 된 것
  - ip: 10.244.5.54
```

```text
"준비됨 / 안 됨" 두 칸이 전부다

발견 9 의 구분을 표현할 수 없다
  [03]   ready:false  terminating:true    죽는 중
  [오늘]  ready:false  terminating:false   안 죽는다
  → Endpoints 로는 둘 다 notReadyAddresses 다
```

**`nodeName` 도 새로 생긴 것이다.**

```text
"이 Pod 는 worker02 에 있다"
→ "같은 노드/존의 Pod 로 우선 보내라" 같은 판단이 가능해진다
→ Endpoints 에는 이 정보 자체가 없어 그런 기능을 만들 수 없었다
```

---

# 9. 라벨과 소유권

```text
root@master01:/# kubectl get endpointslices --show-labels
NAME            ADDRESSTYPE  PORTS  ENDPOINTS                             LABELS
web-svc-872zc   IPv4         80     10.244.30.94,10.244.5.53,10.244.5.54
  endpointslice.kubernetes.io/managed-by=endpointslice-controller.k8s.io,
  kubernetes.io/service-name=web-svc

root@master01:/# kubectl get endpointslices web-svc-872zc -o jsonpath='{.metadata.ownerReferences}'
[{"apiVersion":"v1","blockOwnerDeletion":true,"controller":true,
  "kind":"Service","name":"web-svc","uid":"183e3d10-caa9-44ac-a2e5-71c4e57c1e25"}]
```

## 발견 15 — 조각을 모으는 것도 라벨이다

```text
kubernetes.io/service-name=web-svc
```

```text
kube-proxy 는 이 라벨로 조각 전부를 한 번에 가져온다
→ 조각이 10개든 100개든 상관없다
```

**02 문서에서 정리한 그 이야기다.**

```text
라벨은 검색할 수 있다. 어노테이션은 못 한다

Service → Pod         라벨
Service → EndpointSlice  라벨
```

## 발견 16 — managed-by 가 왜 있나

```text
endpointslice.kubernetes.io/managed-by=endpointslice-controller.k8s.io
```

```text
같은 Service 의 EndpointSlice 를 만들 수 있는 주체가 여럿이다
  endpointslice-controller.k8s.io            셀렉터 기반 (지금 이것)
  endpointslicemirroring-controller.k8s.io   수동 Endpoints 를 복사한 것
  서비스 메시 등 커스텀 컨트롤러

→ 각자 자기 것이라고 표시해둔다. 남의 것을 건드리지 않기 위해서다
```

## 소유권은 01 문서와 같은 구조다

```text
ownerReferences:
  kind: Service
  name: web-svc
  controller: true
  blockOwnerDeletion: true
```

**Service 를 지우면 조각도 함께 지워진다.**

---

# 정리

```text
[문제]
 1. Pod 는 Running 인데 앱이 응답을 못 하는 상황이 실무에서 더 흔하다
    "살아있음" 과 "일할 수 있음" 을 따로 판정해야 한다

[probe]
 2. liveness 와 readiness 는 정의하는 자리도 검사 주체도 같다
    차이는 실패했을 때의 동작이다
 3. liveness 실패 → 컨테이너 재시작 (컨테이너 단위)
    readiness 실패 → 라우팅에서 제외 (Pod 단위)
 4. readiness 만 Pod 단위인 이유는 IP 가 Pod 에 붙어 있기 때문이다
 5. probe 는 앱의 자기 신고다. 진짜 상태와 다를 수 있다
    실험 A(/healthz 삭제)     probe 실패 + 실제 정상 → 멀쩡한 Pod 를 뺐다
    실험 B(index.html 삭제)   probe 성공 + 실제 고장 → 1/3 이 403
 5-1. 실험 B 는 kubectl / EndpointSlice / iptables 어디에도 안 보였다
      Events 도 <none>. 반응 시간 0초 — 판정하는 단계가 없으니까
      "관측 환경을 먼저 구성한다" 는 로드맵 원칙의 실측 근거다
 5-2. 어떤 probe 도 모든 고장을 잡을 수 없다
      probe 는 자동 조치용 — 보수적이어야 한다
      "모든 응답이 정상인가" 는 관측의 영역이다
 6. liveness 에 외부 의존성을 넣으면 전체가 재시작 루프에 빠진다
    readiness 에 넣으면 전체가 동시에 라우팅에서 빠진다
 6-1. "넣는다 / 뺀다" 의 최종 대상은 iptables 의 KUBE-SEP 규칙 한 줄이다
      kubelet 판정 → Pod 의 Ready → EndpointSlice 의 ready → kube-proxy → 규칙
 6-2. kube-proxy 는 한 줄을 지우는 게 아니라
      현재 ready 목록으로 규칙을 통째로 다시 그린다 (확률 재계산이 근거)

[시간]
 7. 라우팅에서 빠질 때 4초 / 들어올 때 2초. 실측이 예측과 일치했다
 8. 지연의 대부분은 probe 설정이다. 전파는 1초 미만
 9. 기본값(10초 × 3회)이면 최대 30초. 고장난 Pod 로 그동안 트래픽이 간다
10. 비대칭(빼기 3회 / 넣기 1회)은 덜 위험한 쪽으로 기울인 기본값이다

[EndpointSlice]
11. conditions 조합이 03 과 다르다
    ready:false + terminating:false = 안 죽는데 트래픽만 끊긴 상태
12. Endpoints 는 v1.33+ 에서 폐기 예정. 호환용으로 자동 생성만 유지
13. 바꾼 이유 셋 — 크기 / 갱신 비용 / 표현력
    가장 큰 것은 갱신 비용. 하나 바뀌면 전체를 재전송했다
14. 나뉜 것은 저장과 전송뿐. ClusterIP 와 iptables 체인은 그대로 하나다
15. 조각을 모으는 것도 라벨(kubernetes.io/service-name)이다

[곁가지]
16. KUBE-SVC 체인 이름은 ClusterIP 가 아니라
    네임스페이스+Service이름+포트+프로토콜 로 만든다
    Service 를 다시 만들어도 이름이 안 바뀐다
```

# 실습 리소스

```text
namespace   k8s-lab   유지
web         Deployment (readinessProbe + postStart)
web-svc     Service    ClusterIP 10.98.126.22
/tmp/web.yaml
```

**정리 명령**

```bash
kubectl delete -f /tmp/web.yaml
rm -f /tmp/web.yaml
kubectl get all
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              Service 의 "지금 살아있는 대상 목록"
2. 생성 시 동작하는 Controller   EndpointSlice Controller
                                판정 자체는 각 노드의 kubelet 이 한다
3. 주요 Spec 과 Status 필드     addressType / endpoints[] /
                                  addresses / conditions / nodeName / targetRef
                                ports[] / metadata.labels
                                (spec-status 구분이 없는 오브젝트다)
4. 다른 오브젝트와의 연결        Service(소유), Pod(targetRef), Node(nodeName)
5. 장애 사례                    5절 readinessProbe 실패 / 7절 probe 설계 사고
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            probe 기본값이면 최대 30초 /
                                외부 의존성을 probe 에 넣지 말 것 /
                                probe 성공이 실제 정상을 뜻하지 않음
```

# 미확인 목록

```text
1. 조각이 실제로 나뉘는 모습 (Pod 100개 이상 필요)
2. --max-endpoints-per-slice 기본값을 설정에서 직접 확인하지 않았다
3. etcd 오브젝트 크기 한계의 정확한 값
4. gRPC probe 의 정식 도입 버전
5. startupProbe 실측
6. topology aware routing 에서 nodeName 이 실제로 쓰이는 모습
7. Endpoints 를 만드는 컨트롤러가 지금도 별도로 도는지 (라벨로 간접 확인만)
8. serving 이 ready 와 갈라지는 경우 (종료 중인데 응답은 되는 상태) 미관측
9. timeoutSeconds 를 넘겼을 때의 동작 (지금은 404 로만 실패시켰다)
```

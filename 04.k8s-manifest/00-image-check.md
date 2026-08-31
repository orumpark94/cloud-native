# 00. 이미지 검증 — Manifest 를 쓰기 전에

작업일: 2026-08-26

## 왜 이것부터 하는가

3단계 마지막에 이미지를 워커 노드로 밀어넣었다. `ctr import` 는 성공했고 `crictl images` 에도 보였다.
그런데 **확인하지 못한 게 하나 남아 있었다.**

```text
빌드 로그에 이런 줄이 있었다

  => exporting attestation manifest sha256:...
  => exporting manifest list sha256:...

Docker 29 의 BuildKit 이 기본으로 서명 정보를 붙인다
→ 이미지가 단일 manifest 가 아니라 OCI index 가 된다
→ kubelet 이 이 구조에서 linux/amd64 를 제대로 골라 쓰는지 모른다
```

```text
★ 이걸 먼저 확인하는 이유
  Manifest 를 10개 다 쓰고 나서 "이미지가 안 뜬다" 를 알면
  → Dockerfile 을 고치고, 재빌드하고, 다시 노드에 밀어넣어야 한다
  → 그 사이에 쓴 Manifest 가 맞는지도 확신할 수 없다

  막힐 가능성이 있는 것부터 확인한다
```

---

## 방법 — 환경변수를 하나도 주지 않는다

```yaml
# 00-image-check.yaml
apiVersion: v1
kind: Pod
metadata:
  name: image-check-w1
spec:
  restartPolicy: Never
  nodeName: worker01          # 노드를 지정한다. 어느 노드에 이미지가 있는지 보려는 것
  containers:
    - name: app
      image: bookstore:20260826-0301
      imagePullPolicy: IfNotPresent
```

```text
환경변수를 일부러 안 준다

  DATABASE_URL / REDIS_URL 이 필수인데 없다
  → config.py 가 즉시 죽어야 정상이다 (3단계 설계)
  → 종료 코드 78 (EX_CONFIG)

한 번으로 네 가지를 동시에 확인한다
  1. kubelet 이 로컬 이미지를 찾아 쓰는가      ← 가장 중요
  2. attestation 이 붙은 OCI index 가 문제없는가
  3. exec 형식 CMD 가 도는가 (python 이 PID 1)
  4. 설정 검증이 설계대로 동작하는가
```

```text
왜 nodeName 을 쓰는가
  Scheduler 에 맡기면 어느 노드에 뜰지 모른다
  → 특정 노드의 이미지를 확인하려면 직접 지정해야 한다

  실무에서는 nodeName 을 거의 안 쓴다
  Scheduler 를 무력화하기 때문이다 (리소스 검사도 건너뛴다)
  → 진단 목적으로만 쓴다
```

---

## 결과

```bash
kubectl apply -f 00-image-check.yaml
kubectl get pod image-check-w1
```

```text
NAME             READY   STATUS   RESTARTS   AGE
image-check-w1   0/1     Error    0          67s
```

```text
Error 가 정상이다. 설정이 없으니 죽는 게 맞다
ImagePullBackOff 였다면 이미지 문제였다
```

```bash
kubectl logs image-check-w1
```

```text
[FATAL] 설정이 잘못됐다. 기동을 멈춘다:
  - DATABASE_URL 이(가) 없다. 필수 값이다
  - REDIS_URL 이(가) 없다. 필수 값이다
```

```bash
kubectl get pod image-check-w1 \
  -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}'
```

```text
78
```

### 가장 중요한 줄 ★

```bash
kubectl describe pod image-check-w1 | tail -20
```

```text
Events:
  Normal  Pulled   63s  kubelet  Container image "bookstore:20260826-0301"
                                 already present on machine
                                 and can be accessed by the pod
  Normal  Created  62s  kubelet  Container created
  Normal  Started  61s  kubelet  Container started
```

```text
"already present on machine and can be accessed by the pod"

  already present            → 레지스트리에 안 물어봤다 (IfNotPresent 동작)
  can be accessed by the pod → kubelet 이 이 이미지를 쓸 수 있다   ★

→ attestation 이 붙은 OCI index 가 문제없다
→ --provenance=false 로 재빌드할 필요가 없다
```

---

## 확인된 것

| 확인 항목 | 결과 |
|---|---|
| kubelet 이 로컬 이미지를 쓰는가 | 확인 (worker01) |
| OCI index + attestation 처리 | 문제없음 |
| `imagePullPolicy: IfNotPresent` | 동작 (already present) |
| exec 형식 CMD | 동작 (python 이 실행됨) |
| 설정 검증 즉시 종료 | 동작 (exit 78) |
| 에러를 모아서 보고 | 동작 (두 개가 한 번에 나옴) |

## 확인하지 못한 것

```text
worker02
  ContainerCreating 상태에서 Pod 를 지웠다
  이미지 문제는 아니다 (없었으면 ImagePullBackOff 로 갔을 것)
  → Phase 4 에서 API 를 replicas 2 로 띄우면 자연히 확인된다
```

---

## 겪은 문제 — kubectl 컨텍스트의 네임스페이스

```text
pod "image-check-w1" deleted from k8s-lab namespace
                                  ↑ default 가 아니었다
```

```text
2단계 실습 때 컨텍스트의 기본 네임스페이스를 k8s-lab 으로 바꿔둔 상태였다
→ -n 을 안 붙이면 전부 k8s-lab 으로 간다
→ 그런데 그 네임스페이스를 정리하며 지웠다
→ 이후 -n 없는 명령이 없는 네임스페이스를 가리키게 된다
```

```bash
# 확인
kubectl config view --minify -o jsonpath='{..namespace}'

# 되돌리기
kubectl config set-context --current --namespace=default
```

```text
재발 방지
  네임스페이스를 지우기 전에 컨텍스트가 그걸 가리키는지 확인한다
  또는 -n 을 항상 명시한다
```

---

## 2단계 잔여물 정리

```bash
kubectl delete namespace k8s-lab
```

```text
사라진 것
  StatefulSet db (3/3)
  Pod db-0, db-1, db-2
  Service db (Headless)
  PVC data-db-0/1/2

남은 것
  PV local-pv-a/b/c → Released 상태
  노드 디스크의 데이터 → 그대로
```

```text
reclaimPolicy: Retain 이라 자동으로 안 지워진다
"실수로 지운 데이터를 되살릴 수 있게" 하는 정책이다

★ Released 상태의 PV 는 재사용할 수 없다
  claimRef 에 지워진 PVC 정보가 남아 새 PVC 가 못 붙는다
  → 지우고 새로 만드는 게 깔끔하다
```

---

## 다음

```text
Phase 1  Namespace + ConfigMap + Secret
Phase 2  PostgreSQL — StatefulSet + Headless Service + PV/PVC
Phase 3  Redis — Deployment + Service
Phase 4  API — Deployment + Service + probe
Phase 5  Worker — Deployment
Phase 6  외부 접근 — NodePort
Phase 7  검증과 실험
```

```text
StorageClass 가 없다
→ 동적 프로비저닝이 안 된다
→ 2단계처럼 hostPath PV 를 손으로 만든다
→ PostgreSQL Pod 가 특정 노드에 묶인다는 제약이 따라온다

Ingress 컨트롤러가 없다
→ NodePort 로 먼저 간다
→ "NodePort 로 되는데 Ingress 를 왜 쓰나" 를 겪은 뒤 도입한다
```

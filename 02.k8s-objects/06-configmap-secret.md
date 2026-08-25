# 06. ConfigMap / Secret

2단계 일곱 번째. **설정을 어디에 두는가. 그리고 1단계가 남긴 "etcd 안의 Secret" 을 직접 확인한다.**

```text
[04 에서 확인한 것]
  컨테이너에 직접 쓴 파일은 Pod 와 함께 사라진다
  postStart 로 만든 index.html 이 Pod 삭제 후 사라졌다

[이미지에 굽는 것의 문제]
  dev / staging / prod 가 DB 주소가 다르다
  → 이미지가 셋이 된다 → "같은 이미지를 배포한다" 가 깨진다
```

```text
설정은 이미지 밖에 있어야 하고, Pod 밖에서 관리돼야 한다
```

## 이 문서의 범위

```text
[확인한 것]
  1. 주입 방식 둘 — 환경 변수 vs 볼륨                      ✅
  2. ConfigMap 을 고치면 반영되는가 (양쪽 대비)             ✅ ★★
  3. ..data 링크 구조와 원자적 교체                        ✅ ★
  4. 볼륨의 정체 — Docker 와 같은 bind mount               ✅ ★
  5. kubelet 과 containerd 의 역할 분담                    ✅ ★
  6. ro 인 진짜 이유                                       ✅
  7. ConfigMap 은 ext4, Secret 계열은 tmpfs                ✅
  8. etcd 안의 Secret 이 평문인가                          ✅ ★★ (1단계 숙제)
  9. base64 는 무엇인가                                    ✅

[다루지 않는 것]
  EncryptionConfiguration 실제 적용   설정만 확인. 켜지 않았다
  외부 비밀 관리 시스템(Vault 등)     10단계 이후
  subPath 함정                       미실습
  immutable ConfigMap/Secret         미실습
```

---

# 1. 주입 방식이 둘이다

```yaml
# 방법 A — 환경 변수
env:
- name: APP_MESSAGE
  valueFrom:
    configMapKeyRef:
      name: web-config
      key: APP_MESSAGE

# 방법 B — 볼륨 마운트
volumeMounts:
- name: html
  mountPath: /usr/share/nginx/html
volumes:
- name: html
  configMap:
    name: web-config
    items:
    - key: index.html
      path: index.html
```

```text
items 를 생략하면 모든 키가 파일로 나타난다
→ APP_MESSAGE 도 파일이 되므로 골라냈다
```

## 실험 전 예측

```text
[환경 변수]
  프로세스를 exec 할 때 커널이 한 번 넘겨준다
  돌고 있는 프로세스의 환경 변수를 밖에서 바꿀 방법이 없다
  → Kubernetes 가 못 하는 게 아니라 OS 가 그렇게 생겼다
  → 안 바뀔 것이다

[파일]
  kubelet 이 그 파일을 관리한다
  → 바뀔 것이다. 다만 동기화 주기가 있으니 1~2분 걸릴 것이다
```

---

# 2. 실습 환경 (2026-08-20)

```yaml
# /tmp/cm.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-config
data:
  APP_MESSAGE: "version-1"
  index.html: |
    version-1
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 2
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
        env:
        - name: APP_MESSAGE
          valueFrom:
            configMapKeyRef:
              name: web-config
              key: APP_MESSAGE
        volumeMounts:
        - name: html
          mountPath: /usr/share/nginx/html
      volumes:
      - name: html
        configMap:
          name: web-config
          items:
          - key: index.html
            path: index.html
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
```

```text
web-b95ff8f4-nkpzx   10.244.5.57    worker01
web-b95ff8f4-nx2pw   10.244.30.97   worker02
web-svc              10.101.209.9
```

## 발견 1 — 파일이 아니라 링크 3단이다 ★

```text
root@master01:/# kubectl exec deploy/web -- ls -la /usr/share/nginx/html/
drwxrwxrwx 3 root root 4096 Aug 20 04:56 .
drwxr-xr-x 3 root root 4096 Jun 10  2025 ..
drwxr-xr-x 2 root root 4096 Aug 20 04:56 ..2026_08_20_04_56_59.2913324425
lrwxrwxrwx 1 root root   32 Aug 20 04:56 ..data -> ..2026_08_20_04_56_59.2913324425
lrwxrwxrwx 1 root root   17 Aug 20 04:56 index.html -> ..data/index.html
```

```text
..2026_08_20_04_56_59.2913324425     실제 내용이 든 디렉터리 (타임스탬프)
..data -> 위 디렉터리                 그것을 가리키는 링크
index.html -> ..data/index.html      우리가 보는 파일. 링크의 링크
```

## 왜 이렇게 만들었나 — 원자적 교체

```text
[단순히 파일을 덮어쓴다면]
  파일이 3개인 ConfigMap 을 고쳤다
  → a.conf 를 새로 썼다
  → b.conf 를 쓰는 중이다        ← 이 순간 앱이 읽으면?
  → 옛 b.conf 와 새 a.conf 를 섞어 읽는다 → 깨진다

[링크 방식]
  1. 새 타임스탬프 디렉터리를 만들고 내용을 전부 쓴다   ← 앱은 아직 옛것을 본다
  2. ..data 링크만 갈아끼운다                        ← 이 순간 전부 동시에 바뀐다
  3. 옛 디렉터리를 지운다

링크 교체는 원자적이다 → "여러 파일이 동시에 바뀐다" 를 보장한다
```

---

# 3. 실험 — ConfigMap 을 고친다 ★★

```bash
date '+%H:%M:%S'; kubectl patch cm web-config --type=merge \
  -p '{"data":{"APP_MESSAGE":"version-2","index.html":"version-2\n"}}'
```

## 타임라인

```text
14:07:54 env=version-1 file=version-1
14:08:00 env=version-1 file=version-1
14:08:05 env=version-1 file=version-1     ← patch
14:08:11 env=version-1 file=version-1
14:08:16 env=version-1 file=version-1
14:08:22 env=version-1 file=version-2     ← 파일만 바뀜
...
14:09:05 env=version-1 file=version-2
```

```text
14:07:58 ~ 14:09:09   RESTARTS 0 유지 (두 Pod 모두)
```

```text
예측                              실측
──────────────────────────────────────────────
env 는 안 바뀐다                   version-1 유지        ✓
RESTARTS 0 유지                    0 유지                ✓
파일은 바뀐다                      version-2             ✓
파일 반영에 1~2분                  17초 이내             ✗
```

## 발견 2 — 환경 변수는 절대 안 바뀐다

```text
환경 변수는 프로세스를 exec 할 때 커널이 한 번 넘겨준다
그 뒤로는 밖에서 바꿀 방법이 없다
→ 새 프로세스를 띄우는 수밖에 없다 = Pod 를 다시 만드는 수밖에 없다
```

```bash
kubectl rollout restart deployment web
```

```text
실무 패턴: ConfigMap 내용의 해시를 Pod 어노테이션에 넣어둔다
→ ConfigMap 이 바뀌면 해시가 바뀐다 → template 변경이 되어 자동 롤아웃
(Helm 의 checksum/config 어노테이션)
```

## 발견 3 — Pod 는 재시작하지 않는다

```text
Deployment 는 자기 template 만 본다
ConfigMap 이 바뀐 것은 template 변경이 아니다
→ 롤아웃이 안 일어난다 (02 문서에서 확인한 그대로)
```

## 발견 4 — 시간 예측이 틀렸다

```text
[예측 근거]  kubelet 동기화 주기 1분 + 캐시 TTL
[실측]       17초 이내
```

```text
kubelet 이 ConfigMap 변경을 감지하는 방식이 몇 가지 있다
  Watch      apiserver 를 watch 해서 즉시 감지
  TTLCache   캐시가 만료되면 다시 조회
  Get        매번 조회
```

> **미확인.** 최근 버전은 `Watch` 가 기본이라 알고 있으나 이 클러스터의 실제 설정은 확인하지 않았다.
> `sudo grep -iE 'configMapAndSecret|syncFrequency' /var/lib/kubelet/config.yaml`

## 발견 5 — 링크 타임스탬프가 메커니즘을 증명한다 ★★

**worker02 에서 (실험 후):**

```text
drwxr-xr-x 2 root root 4096 Aug 20 14:08  ..2026_08_20_05_08_42.3835707378
lrwxrwxrwx 1 root root   32 Aug 20 14:08  ..data -> ..2026_08_20_05_08_42.3835707378
lrwxrwxrwx 1 root root   17 Aug 20 13:56  index.html -> ..data/index.html
                                  ^^^^^
```

```text
..2026_08_20_05_08_42...   14:08   새로 만든 디렉터리
..data                     14:08   이때 갈아끼웠다
index.html                 13:56   처음 만든 그대로. 안 건드렸다

그리고 옛 디렉터리(..2026_08_20_04_56_59...)는 사라졌다
```

**patch 시각(14:08:05)과 디렉터리 생성 시각이 일치한다.** 추측한 메커니즘이 파일 타임스탬프로 확인됐다.

## 발견 5-1 — kubelet 이 먼저 연결을 열어둔다 (watch) ★

```text
[헷갈리기 쉬운 흐름 — push]
  etcd 가 바뀐다 → apiserver 가 알아챈다
  → "worker02 야, 이거 바꿔라" 하고 연락한다

[실제 — watch]
  kubelet 이 미리 apiserver 에 연결을 열어둔다
    "web-config 가 바뀌면 이 연결로 알려주세요"
  → 연결을 끊지 않고 계속 열어둔다
  → 변경이 생기면 apiserver 가 그 연결로 흘려보낸다
```

```text
전화를 거는 쪽은 언제나 kubelet 이다
```

**정확한 흐름**

```text
1. kubectl patch cm → apiserver
2. apiserver 가 검증하고 etcd 에 쓴다
3. apiserver 는 자기가 썼으니 무엇이 바뀌었는지 안다
4. 이미 열려 있는 watch 연결들에 흘려보낸다
5. kubelet 이 받아서 파일을 새로 쓰고 ..data 링크를 갈아끼운다
```

## 발견 5-2 — kubelet 은 etcd 를 직접 못 본다

```text
kubelet  →  apiserver  →  etcd
             ^^^^^^^^^  etcd 와 이야기하는 것은 apiserver 뿐이다
```

```text
kubelet 은 etcd 주소도 모르고 인증서도 없다
apiserver 뒤에 무엇이 있는지 알 필요가 없다
→ k3s 는 etcd 대신 SQLite 를 쓰는데도 kubelet 코드는 그대로다
```

**8절에서 etcd 를 볼 때 `etcd-master01` Pod 안에서 실행한 이유가 그것이다.**

## 발견 5-3 — 자기 노드의 것만 감시한다

```text
kubelet 이 watch 하는 것
  = 자기 노드의 Pod 가 참조하는 ConfigMap / Secret 만

클러스터에 1000개가 있어도 자기 것만 받는다
→ 안 그러면 노드마다 전부를 받게 되어 낭비다
```

**04 문서에서 EndpointSlice 를 조각낸 이유와 같은 발상이다.**

## 왜 이렇게 설계했나

```text
[apiserver 가 찾아가는 방식이라면]
  노드가 500대면 500곳의 주소를 알아야 한다
  누가 무엇을 쓰는지도 관리해야 한다
  노드가 잠깐 죽으면 재시도 목록을 들고 있어야 한다
  → apiserver 가 상태를 잔뜩 갖게 된다

[watch 방식이면]
  apiserver 는 "열려 있는 연결" 만 알면 된다
  연결이 끊기면 그냥 끝이다. 다시 여는 것은 kubelet 의 책임이다
```

```text
Lease        중앙이 "살아있냐" 묻지 않는다. 노드가 "나 살아있다" 를 적는다
Calico 블록  Pod 마다 중앙에 IP 를 묻지 않는다. 미리 받아두고 혼자 쓴다
watch        중앙이 알려주러 가지 않는다. 각자 와서 연결을 열어둔다

→ 전부 "중앙이 적게 일하도록" 설계돼 있다
```

> **미확인**: `sudo ss -tnp | grep 6443` 으로 kubelet 의 지속 연결을 확인하려 했으나 실행하지 않았다.

## 발견 6 — 파일이 바뀌는 것과 앱에 반영되는 것은 다르다 ★

```text
실험이 잘 된 것은 nginx 가 정적 파일을 요청마다 읽기 때문이다
```

```text
[요청마다 읽는 것]     index.html 같은 정적 파일 → 즉시 반영
[시작할 때만 읽는 것]  nginx.conf, application.yml, my.cnf
                      → 파일은 바뀌었는데 앱은 옛 설정으로 돈다
[타임아웃]             앱이 멈춘 경우
```

```text
1. 앱이 파일 변경을 감지한다      → 볼륨 마운트로 충분 (Prometheus, Envoy 등)
2. 앱이 reload 신호를 받는다      → 사이드카가 SIGHUP 을 보낸다
3. 앱이 시작할 때만 읽는다        → Pod 를 다시 만드는 수밖에 없다  ← 대부분
```

**이 문서 전체를 관통하는 주제와 같다.**

```text
[04]  EndpointSlice 목록에 있다 ≠ 트래픽을 받는다
[04]  probe 가 성공한다 ≠ 서비스가 정상이다
[05]  describe 에 보인다 ≠ 저장돼 있다
[06]  파일이 바뀌었다 ≠ 앱에 반영됐다
```

---

# 4. 볼륨의 정체 — Docker 와 같은 bind mount ★

## Kubernetes 의 volume 은 저장소가 아니다

```text
[Docker volume]
  목적: 데이터를 컨테이너보다 오래 살게 한다
  대상: 호스트 디렉터리 또는 Docker 가 관리하는 저장 공간

[Kubernetes volume]
  목적: "컨테이너 안의 어떤 경로에 무언가를 나타나게 한다"
  대상: 종류가 많고, 저장소가 아닌 것도 있다
```

```text
emptyDir                임시 디렉터리. Pod 가 죽으면 사라진다
hostPath                노드의 디렉터리        ← Docker volume 에 가장 가깝다
configMap / secret      apiserver 의 데이터를 파일로   ← 저장과 무관하다
persistentVolumeClaim   외부 스토리지 (09 문서)
downwardAPI             Pod 자기 정보를 파일로
projected               위 여러 개를 한 디렉터리에 합침
```

## 실측 — mountinfo

```text
root@master01:/# kubectl exec web-b95ff8f4-nx2pw -- cat /proc/self/mountinfo | grep html
861 824 252:0 /var/lib/kubelet/pods/ec8ae84d-.../volumes/kubernetes.io~configmap/html
    /usr/share/nginx/html ro,relatime - ext4 /dev/mapper/ubuntu--vg-ubuntu--lv rw
    ^^^^^^^^^^^^^^^^^^^^^ ^^                                                   ^^
    붙인 자리              이 마운트는 ro                          파일시스템은 rw
```

```text
[Docker]      /var/lib/docker/volumes/<이름>/_data     → 컨테이너 경로
[Kubernetes]  /var/lib/kubelet/pods/<uid>/volumes/...  → 컨테이너 경로
```

**같은 기술이다. 원본을 누가 만드느냐만 다르다.**

## 발견 7 — 파일시스템은 rw 인데 마운트만 ro 다

```text
파일시스템 자체는 rw  →  kubelet 은 그 디렉터리에 쓸 수 있다
이 마운트만 ro       →  컨테이너는 못 쓴다
```

```text
root@master01:/# kubectl exec web-b95ff8f4-nx2pw -- rm /usr/share/nginx/html/index.html
rm: cannot remove '...': Read-only file system
command terminated with exit code 1
```

**04 실험과 대비된다.**

```text
[04 — postStart 로 쓴 파일]  컨테이너의 쓰기 레이어(upperdir) → 지워졌다
[06 — ConfigMap 볼륨]        ro 마운트 → 못 지운다
```

## 발견 8 — 권한은 0644 다

```text
root@worker02:/# ls -la .../kubernetes.io~configmap/html/..data/
-rw-r--r-- 1 root root 10 Aug 20 14:08 index.html
^^^^^^^^^^
```

```text
0644 — 그 외 사용자도 읽기 가능
→ non-root 컨테이너(runAsUser: 1000)여도 읽을 수 있다
```

**다만 `root/root` 이 항상 안전한 것은 아니다.**

```text
[문제가 되는 경우]
  defaultMode: 0600 으로 좁히면 → non-root 는 못 읽는다
  ssh 키처럼 권한을 까다롭게 보는 파일 → 0644 면 "Permissions too open" 거부
```

> **미확인**: `fsGroup` 이 ConfigMap/Secret 볼륨에 적용되는지 확인하지 않았다. 09 문서에서 다시 볼 지점.

---

# 5. 누가 무엇을 하나 — kubelet vs containerd ★

```text
[kubelet]
  1. apiserver 에서 ConfigMap 내용을 받아온다
  2. /var/lib/kubelet/pods/<uid>/volumes/... 에 파일을 쓴다
  3. ConfigMap 이 바뀌면 ..data 링크를 갈아끼운다
  4. containerd 에게 "이 경로를 이 컨테이너 경로에 ro 로 붙여라" 라고 넘긴다

[containerd → runc]
  5. OCI 스펙의 mounts 항목에 넣는다
  6. runc 가 mount() 시스템콜을 실제로 호출한다
```

## 발견 9 — containerd 는 ConfigMap 을 모른다

```text
root@worker02:/# sudo crictl inspect e53ae8b1ce109 | grep -B3 -A8 'nginx/html'

"mounts": [
  {
    "container_path": "/usr/share/nginx/html",
    "host_path": "/var/lib/kubelet/pods/.../volumes/kubernetes.io~configmap/html",
    "readonly": true
  },
  {
    "container_path": "/var/run/secrets/kubernetes.io/serviceaccount",
    "host_path": "/var/lib/kubelet/pods/.../kubernetes.io~projected/kube-api-access-xt9p5",
    "readonly": true
  },
...
{
  "destination": "/usr/share/nginx/html",
  "options": ["rbind", "rprivate", "ro"],
  "source": "/var/lib/kubelet/pods/.../volumes/kubernetes.io~configmap/html",
  "type": "bind"
}
```

```text
두 층이 다 보인다
  위쪽   CRI 가 받은 것      kubelet → containerd
  아래쪽 OCI 스펙            containerd → runc
         "type": "bind"     진짜 bind mount 다

ConfigMap 이라는 단어가 어디에도 없다
CRI 인터페이스에는 그 개념 자체가 없다
```

```text
옵션 세 개의 의미
  rbind      하위 마운트까지 통째로 붙인다
  rprivate   마운트 이벤트가 호스트와 컨테이너 사이에 전파되지 않는다  ← 격리
  ro         읽기 전용
```

## 결정적 근거 — 재시작 없이 내용이 바뀌었다

```text
14:08:05   ConfigMap patch
14:08:22   파일이 version-2
RESTARTS   0 유지

→ 컨테이너를 다시 안 만들었다 = containerd 는 아무 일도 안 했다
→ mount 를 새로 걸지도 않았다
→ 이미 붙어 있는 원본 디렉터리 안에서 링크가 갈아끼워진 것이다
```

```text
mount 는 한 번 걸리고 그대로 있다      containerd 의 일. 컨테이너 생성 시 한 번
그 안의 내용은 계속 바뀐다             kubelet 의 일. 계속한다
```

> `root/root` 소유는 판별 근거가 못 된다. kubelet 도 containerd 도 root 로 돈다.

---

# 6. ro 인 진짜 이유

**호스트 보호가 아니다. 다른 볼륨은 rw 이기 때문이다.**

```text
emptyDir / hostPath / PVC     보통 rw
configMap / secret / projected  ro
```

```text
이 셋의 공통점: kubelet 이 내용을 계속 관리한다
  ConfigMap 이 바뀌면 → 다시 쓴다
  토큰이 만료되면     → 다시 쓴다
```

```text
컨테이너가 그 파일을 고칠 수 있다면
  → 다음 동기화 때 kubelet 이 덮어쓴다
  → "고쳤는데 잠시 뒤 되돌아가는" 상태가 생긴다 → 원인 찾기가 어렵다
아예 못 쓰게 막아서 그 혼란을 없앤 것이다
```

**호스트 보호는 mount namespace 자체가 한다.**

```text
컨테이너는 노드의 파일시스템을 못 본다 (mount ns 격리)
bind mount 는 그 격리에 구멍을 내는 것이다
→ 그 Pod 전용 디렉터리 하나만 보인다
→ 탈취돼도 다른 Pod 의 볼륨은 못 본다. rw 였어도 마찬가지다
```

```text
"어느 디렉터리를 보여줄까" 가 격리의 핵심이고
"ro 냐 rw 냐" 는 그다음 문제다
```

---

# 7. ConfigMap 은 디스크, Secret 계열은 메모리

```text
root@worker02:/# findmnt | grep -i kube-api-access
.../kubernetes.io~projected/kube-api-access-xt9p5  tmpfs  tmpfs  rw,relatime,size=3858464k,inode64,noswap
.../kube-api-access-hdbqc                          tmpfs  tmpfs  rw,relatime,size=3858464k,inode64,noswap
.../kube-api-access-k8wtn                          tmpfs  tmpfs  rw,relatime,size=3858500k,inode64,noswap
.../kube-api-access-w646r                          tmpfs  tmpfs  rw,relatime,size=174080k,inode64,noswap
.../kube-api-access-7cjcl                          tmpfs  tmpfs  rw,relatime,size=3858464k,inode64,noswap
```

## 발견 10 — tmpfs + noswap

```text
[ConfigMap 볼륨]  ext4    노드 디스크에 남는다     (mountinfo 로 확인)
[Secret 계열]     tmpfs   메모리. 노드가 꺼지면 사라진다
                  noswap  스왑으로도 안 내려간다
```

**토큰이 디스크에 흔적을 남기지 않게 하려는 것이다.**

## 발견 11 — tmpfs 크기가 Pod 의 메모리 한도에 묶인다

```text
size=3858464k   (약 3.7GB)   대부분
size=174080k    (170MB)      하나만 다르다
```

```text
tmpfs 는 메모리를 쓴다 → 그 Pod 의 limits.memory 에 묶인다
170MB 는 CoreDNS 의 기본 메모리 한도(170Mi)와 같다
```

> 추론이다. `kubectl get pods -A -o wide` 로 UID `8e55df91` 을 찾아 확인할 수 있다.

---

# 8. Secret — etcd 안을 본다 ★★

**1단계에서 적어두고 확인 안 한 것이다.**

```text
[1단계]  "etcd 백업 파일에는 Secret 이 평문으로 들어있다"
         → 그래서 실험 후 백업 파일을 지웠다
         → 그런데 실제로 열어보지는 않았다
```

## 만들기

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-secret
type: Opaque
stringData:
  DB_PASSWORD: "s3cr3t-p@ssw0rd"
  DB_USER: "appuser"
```

```text
root@master01:/# kubectl get secret db-secret -o yaml
data:
  DB_PASSWORD: czNjcjN0LXBAc3N3MHJk
  DB_USER: YXBwdXNlcg==
metadata:
  annotations:
    kubectl.kubernetes.io/last-applied-configuration: |
      {"stringData":{"DB_PASSWORD":"s3cr3t-p@ssw0rd","DB_USER":"appuser"}, ...}
                                    ^^^^^^^^^^^^^^^ 인코딩도 안 된 평문
```

```text
root@master01:/# kubectl get secret db-secret -o jsonpath='{.data.DB_PASSWORD}' | base64 -d
s3cr3t-p@ssw0rd
```

## 발견 12 — stringData 로 써도 data 로 저장된다

```text
stringData   쓸 때만 쓰는 편의 필드. 조회하면 없다
data         실제 저장 필드. base64 로 표시된다
```

## 발견 13 — etcd 에는 base64 도 아닌 평문이다 ★★

**etcd 이미지는 distroless 라 셸이 없다. `etcdctl` 을 직접 호출한다.**

```bash
kubectl -n kube-system exec etcd-master01 -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/k8s-lab/db-secret | grep -a -oE 's3cr3t[^ ]*|appuser|DB_[A-Z]*'
```

```text
DB_PASSWORD
s3cr3t-p@ssw0rd","DB_USER":"appuser"},"type":"Opaque"}     ← 1. 어노테이션
DB_PASSWORD
DB_USER
DB_PASSWORD
s3cr3t-p@ssw0rd                                            ← 2. data 필드
DB_USER
appuser
```

```text
평문이 두 군데 있다
  1. last-applied-configuration 어노테이션 (kubectl apply 가 남긴 JSON)
  2. data 필드 (실제 값)
```

```text
kubectl get -o yaml   czNjcjN0LXBAc3N3MHJk   ← 여기만 base64
etcd 안               s3cr3t-p@ssw0rd        ← 아예 평문
```

**`base64` 는 API 가 YAML/JSON 으로 보여줄 때만 씌우는 옷이다.**

```text
Secret 의 data 는 원래 이진(bytes) 타입이다
텍스트 형식에 담으려고 인코딩하는 것뿐이다
etcd 는 protobuf 라 이진을 그대로 담는다 → 옷을 안 입는다
```

```text
"base64 라서 읽힌다" 가 아니라 "애초에 아무것도 안 씌웠다"
```

## 발견 14 — 암호화가 꺼져 있다

```text
root@master01:/# sudo grep -i 'encryption-provider' /etc/kubernetes/manifests/kube-apiserver.yaml
(출력 없음)
```

```text
--encryption-provider-config 플래그가 없다
→ apiserver 가 etcd 에 쓸 때 암호화하지 않는다
kubeadm 은 기본으로 이것을 안 켠다
```

## 대책은 층별로 다르다

```text
[etcd 암호화가 막는 것]
  etcd 데이터 파일이나 백업이 유출됐을 때 → 열어도 못 읽는다

[여전히 못 막는 것]
  kubectl get secret -o yaml
  → apiserver 가 복호화해서 준다 → base64 만 벗기면 보인다
```

```text
권한 있는 사람   → RBAC 이 더 근본적이다 (08 문서)
etcd 파일 유출   → EncryptionConfiguration
근본적으로       → 외부 비밀 관리 시스템 (Vault, AWS Secrets Manager)
                   → Kubernetes 는 참조만 하고 값은 안 갖는다. 10~11단계

어노테이션 문제  → kubectl apply --server-side 는 이 어노테이션을 안 남긴다
```

```text
Kubernetes 의 Secret 은 "비밀을 보관하는 금고" 가 아니라
"비밀을 주입하는 통로" 에 가깝다
```

---

# 9. ConfigMap 과 Secret 의 실제 차이

```text
저장 방식만 보면 거의 같다. 실제 차이는 이것들이다

1. 볼륨이 tmpfs 다        Secret 은 노드 디스크에 안 남는다   ✅ 확인
2. 화면에 바로 안 보인다   base64 라 어깨너머로는 못 읽는다     ✅ 확인
3. RBAC 을 따로 걸 수 있다 "ConfigMap 은 읽되 Secret 은 못 읽게"  (08 문서)
4. etcd 암호화 대상이다    EncryptionConfiguration 으로 켤 수 있다
5. 로그/이벤트에 안 찍힌다  컴포넌트들이 배려한다
```

---

# 곁가지 — Static Pod 의 UID 가 둘이다

```text
root@master01:/var/lib/kubelet/pods# ls
3b4c00f9-3cb1-4a33-be1c-cf698127bf75     ← UUID 형식
5fb3782e-0ec4-45a2-a46b-2f234d8b4ad9
ae670b0b-c712-40e7-90a9-a9a2c98d5a5e

4014eb7abb6fb0c28f2dbaded53072fd         ← 하이픈 없는 32자리
82a5bfafa937b7054c1a68e50c78a28b
db8e975fb8836dc499a4ff2be4680956
dcefdf84232060db703cb0098efa5bf6
```

```text
root@master01:/# kubectl get pods -n kube-system -o custom-columns='NAME:.metadata.name,UID:.metadata.uid' | grep master01
etcd-master01                      7341c4b3-13e1-4bc6-96d3-1a7af06d9d78
kube-apiserver-master01            da0cab8f-f8fc-49ff-89e0-72c26c7339e5
kube-controller-manager-master01   1377b728-6968-451e-82c8-9140f5c53469
kube-scheduler-master01            22bd7954-d0df-4fc3-9e00-2b6f41ec0bfe
```

```text
디렉터리 이름과 apiserver 의 UID 가 완전히 다른 값이다
하이픈 없는 것이 정확히 4개 — /etc/kubernetes/manifests/ 의 4개와 일치한다

kubelet 은 manifest 파일을 읽어 자기 방식으로 ID 를 만든다
apiserver 는 나중에 미러 Pod 를 등록하며 자기 UID 를 따로 매긴다
→ 같은 Pod 인데 ID 가 둘이다
```

> 정확한 생성 규칙은 확인하지 않았다. **관측된 사실은 "형식이 다르고 값도 다르다" 이다.**
> 1단계 `analysis/static-pod.md` 에 이어붙일 관찰이다.

---

# 정리

```text
[무엇인가]
 1. 이미지 밖에서 관리하면서 컨테이너 안에서 읽게 하는 통로다
    Pod 밖에서 바뀔 수 있는 값이라 kubelet 이 지켜본다

[주입 방식]
 2. 환경 변수는 절대 안 바뀐다 — 리눅스가 그렇게 생겼다
    바꾸려면 Pod 를 다시 만드는 수밖에 없다
 3. 볼륨은 17초 만에 바뀌었다 (예측 1~2분은 틀림)
 4. ConfigMap 을 고쳐도 Pod 는 재시작하지 않는다 (RESTARTS 0)
    Deployment 는 자기 template 만 보기 때문이다
 5. 파일이 바뀌어도 앱이 다시 안 읽으면 소용없다
    대부분의 앱은 시작할 때만 읽는다 → 결국 롤아웃한다

[어떻게 알았나]
 5-1. kubelet 이 미리 연결을 열어두고 apiserver 가 그리로 흘려보낸다 (watch)
      apiserver 가 노드를 찾아가는 게 아니다. 전화를 거는 쪽은 kubelet 이다
 5-2. kubelet 은 etcd 를 직접 못 본다. apiserver 뒤에 뭐가 있는지 모른다
 5-3. 자기 노드의 Pod 가 쓰는 것만 감시한다. 전부를 받지 않는다

[볼륨의 정체]
 6. Docker 와 같은 bind mount 다
    원본: /var/lib/kubelet/pods/<uid>/volumes/kubernetes.io~configmap/<이름>/
 7. ..data 링크를 갈아끼워 원자적으로 바꾼다
    타임스탬프가 증거 (..data 14:08 / index.html 13:56)
 8. 파일시스템은 rw 인데 이 마운트만 ro 다
    kubelet 은 쓸 수 있고 컨테이너는 못 쓴다
 9. 권한은 0644 — non-root 컨테이너도 읽을 수 있다

[역할 분담]
10. kubelet   원본을 만들고 갱신한다. 무엇을 붙일지 정한다
11. containerd/runc   실제 mount() 를 호출한다. 한 번 걸면 끝
12. CRI 에는 ConfigMap 개념이 없다 (crictl inspect 로 확인)
    "type": "bind", "options": ["rbind","rprivate","ro"]
13. ro 인 이유는 호스트 보호가 아니라 kubelet 이 관리 주체라서다
    다른 볼륨(emptyDir 등)은 rw 다

[저장 위치]
14. ConfigMap 볼륨   ext4. 노드 디스크에 남는다
15. Secret 계열      tmpfs + noswap. 메모리에만 있다
16. tmpfs 크기가 Pod 의 메모리 한도에 묶인다 (CoreDNS 170Mi 관측)

[Secret]
17. etcd 에 평문이다. base64 도 아니다
18. base64 는 API 가 텍스트로 보여줄 때만 씌우는 옷이다
19. last-applied-configuration 어노테이션에도 평문이 남는다
20. encryption-provider 미설정 — kubeadm 기본값
21. etcd 암호화는 "파일 유출" 만 막는다. 권한 있는 사람은 여전히 본다
    → RBAC 이 더 근본적이다
```

# 실습 리소스

```text
namespace   k8s-lab   유지
web-config  ConfigMap   삭제됨
db-secret   Secret      삭제됨
web / web-svc           삭제됨
/tmp/cm.yaml, /tmp/sec.yaml   삭제됨

남은 것: configmap/kube-root-ca.crt
  네임스페이스마다 자동으로 생긴다
  Pod 가 apiserver 를 검증할 때 쓰는 CA 인증서
  07 문서 3종 세트의 ca.crt 원본이다. 지우면 안 된다
```

# 로드맵 2단계 결과물 7항목 대응

```text
1. 오브젝트의 역할              이미지 밖의 설정을 컨테이너 안으로 주입하는 통로
2. 생성 시 동작하는 Controller   없다. 만들 때는 apiserver 가 저장만 한다
                                주입 시점에 kubelet 이 관여한다
3. 주요 Spec 과 Status 필드     data / binaryData / immutable
                                Secret: type / stringData(쓰기 전용) / data
                                (status 가 없는 오브젝트다)
4. 다른 오브젝트와의 연결        Pod(env, volume), ServiceAccount(토큰), Ingress(TLS)
5. 장애 사례                    3절 env 가 안 바뀜 / 6절 앱이 안 읽음 /
                                8절 etcd 평문
6. 확인 명령어                  각 절에 기록
7. 운영 시 주의할 점            env 로 넣으면 롤아웃 없이는 안 바뀐다 /
                                볼륨은 바뀌지만 앱이 읽어야 반영된다 /
                                Secret 은 암호화가 아니다 /
                                apply 어노테이션에 평문이 남는다
```

# 미확인 목록

```text
1. kubelet 의 configMapAndSecretChangeDetectionStrategy 실제 설정값
2. 반영 시간 17초를 더 좁혀서 재측정 (감시 주기가 5초라 그 이상 못 좁힘)
3. subPath 로 마운트했을 때 갱신이 안 되는 문제 (링크 구조 때문)
4. immutable: true 를 설정했을 때의 동작
5. fsGroup 이 ConfigMap/Secret 볼륨에 적용되는지
6. defaultMode 를 0600 으로 하고 non-root 로 돌렸을 때의 실패
7. EncryptionConfiguration 을 실제로 적용해보지 않았다
8. binaryData 필드 미사용
9. Secret 을 볼륨으로 마운트해보지 않았다 (tmpfs 는 토큰으로 간접 확인)
10. Static Pod 의 kubelet 로컬 UID 생성 규칙
11. tmpfs 174080k 가 정말 CoreDNS 인지 (UID 로 미확인)
12. 1MB 크기 제한을 넘겼을 때의 동작
```

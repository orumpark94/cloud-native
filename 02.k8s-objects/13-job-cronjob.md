# 13. Job / CronJob — 끝나는 것이 정상인 워크로드

`cloud-native-learning-roadmap.md` 2단계의 마지막 오브젝트.

11(스토리지 종합)에서 **"데이터베이스를 클러스터 안에 둔다면 백업이 반드시 있어야 한다"** 고 결론냈다. 그 백업을 만드는 도구가 여기 있다.

```text
실험 A   Job 기본 — 끝나면 어떻게 되는가
실험 B   실패하는 Job — backoffLimit 과 재시도 간격        ★
실험 C   CronJob — 3단 사슬, 시간대, 이력
실험 D   백업 CronJob — 도는 DB 의 볼륨을 읽는다           ★★
```

---

## 0. 큰 틀 — 지금까지와 성격이 다르다

```text
Deployment    죽으면 다시 띄운다
StatefulSet   죽으면 다시 띄운다
DaemonSet     죽으면 다시 띄운다

전부 "계속 돌아야 하는 것" 이다
```

```text
그런데 이런 일들이 있다
  데이터베이스 백업 / 로그 압축 / 월말 정산 / 스키마 마이그레이션

전부 "끝나야 하는 것" 이다. 계속 돌면 그게 이상하다
```

### Deployment 로는 안 된다

```text
Deployment 의 Pod 는 restartPolicy 가 Always 다
  → 컨테이너가 끝나면 "죽었다" 고 판단하고 다시 띄운다

백업 스크립트를 Deployment 로 돌리면
  백업 끝 → 종료 → 다시 띄움 → 백업 → 종료 → ...
  → 무한 백업
```

**"정상 종료" 라는 개념 자체가 없다.**

### 유일하게 "포기" 가 있다

```text
[다른 워크로드]  실패하면 될 때까지 다시 띄운다
                CrashLoopBackOff 로 영원히 반복한다

[Job]           backoffLimit — 몇 번까지 재시도하고 포기할 것인가
```

```text
백업 스크립트가 잘못됐다면 100번 돌려도 100번 실패한다
계속 시도하는 것보다 멈추고 사람을 부르는 게 낫다
```

### crontab 과 무엇이 다른가

```text
[리눅스 crontab]
  0 3 * * *  /usr/local/bin/backup.sh

  실행하고 끝. 실패해도 그냥 실패한다
  기록은 로그 파일에만 남는다
  이전 실행이 안 끝났는데 다음 시간이 와도 그냥 또 실행한다
  서버가 꺼져 있었으면 그 시간은 건너뛴다
```

```text
[CronJob]  같은 문법을 쓰지만

  실패하면 재시도한다              backoffLimit
  실행마다 오브젝트가 남는다        Job + Pod
  겹치면 어떻게 할지 정한다         concurrencyPolicy
  일시정지할 수 있다               suspend
  시간대를 지정할 수 있다           timeZone
```

---

## 1. 실험 A — Job 기본

### 발견 1. restartPolicy 는 기본값이 없다

Deployment 쓰던 습관대로 `restartPolicy` 를 빼고 적용해봤다.

```text
The Job "hello-bad" is invalid:
  spec.template.spec.restartPolicy: Required value: valid values: "OnFailure", "Never"
```

**"Always 는 안 된다" 가 아니라 "반드시 골라라" 다.**

```text
[Deployment]  안 적어도 된다. 기본값 Always
[Job]         기본값이 없다. Never 나 OnFailure 중 하나를 반드시 적는다
```

```text
[왜 기본값을 안 뒀나]
  Always 를 기본으로 두면 Job 의 목적과 정면 충돌한다
  Never 를 기본으로 두면 "실패해도 재시도 안 함" 이 조용히 적용된다
  → 어느 쪽도 안전하지 않으니 사람이 고르게 했다
```

### manifest

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: hello
  namespace: k8s-lab
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: hello
        image: nginx:alpine
        command: ["sh", "-c", "echo start; sleep 5; echo done"]
```

### 발견 2. 끝나고도 Pod 가 남는다

```text
hello-bxk7j   1/1   Running     23s
hello-bxk7j   0/1   Completed   28s      ← sleep 5 가 끝났다
hello-bxk7j   0/1   Completed   2m47s    ← 그대로 남아 있다
```

```text
[Deployment 였다면]  끝났다 → "죽었다" → 새 Pod → 무한 반복
[Job]               끝난 것이 정상 → 그대로 둔다
```

```text
Job 은 결과를 남겨야 한다
  성공했는지 실패했는지 / 로그에 뭐가 찍혔는지 / 얼마나 걸렸는지
Pod 를 지우면 확인할 방법이 없다
```

로그도 그래서 볼 수 있다.

```text
kubectl -n k8s-lab logs hello-bxk7j
start
done
```

### 발견 3. ★ 상태 해석이 정반대다

```text
"ready": 0
"succeeded": 1
"terminating": 0
```

```text
[Deployment 라면]  ready 0 = 서비스가 죽었다. 장애다
[Job]             ready 0 = 다 끝났다. 정상이다
```

```text
succeeded: 1     누적이다. 줄어들지 않는다
ready: 0         지금 도는 것은 없다
```

**"계속 도는 것" 과 "끝나는 것" 은 세는 대상이 다르다.**

### 발견 4. DURATION 에 준비 시간이 섞인다

```text
NAME    STATUS     COMPLETIONS   DURATION   AGE
hello   Complete   1/1           31s        5m15s
```

```text
startTime       23:49:45
completionTime  23:50:16       = 31초

그런데 실제 작업은 sleep 5, 즉 5초였다
나머지 26초는 이미지 받고 컨테이너 만드는 시간이다
```

```text
[실무에서 오해하기 쉽다]
  "백업 Job 이 5분 걸렸다" 를 DURATION 으로 보면
  실제 백업이 5분인지, 이미지 받느라 4분 쓴 건지 구분이 안 된다
  → 작업 시간 자체는 앱이 로그로 남겨야 한다
```

실제로 실험 C 에서 같은 이미지를 다시 쓰니 DURATION 이 3초였다.

### 발견 5. 컨트롤러가 한 단이다

```json
[{"kind":"Job","name":"hello","controller":true,...}]
```

```text
Deployment  → ReplicaSet → Pod    2단
Job         → Pod                 1단
```

```text
ReplicaSet 이 필요했던 이유는 "두 세대를 동시에 굴리는 롤링업데이트" 였다
Job 은 한 번 실행하고 끝이다 → 세대 개념이 없다
```

StatefulSet · DaemonSet 과 같다. **2단인 건 Deployment 뿐이다.**

### 발견 6. 조건과 완료 기준

```text
type: SuccessCriteriaMet   reason: CompletionsReached
type: Complete             reason: CompletionsReached
message: Reached expected number of succeeded pods
```

```text
COMPLETIONS 1/1 의 분모는 spec.completions 의 기본값 1이다
"1개가 성공하면 끝" 이라는 뜻이다
5로 하면 5개가 성공해야 끝난다 (병렬 배치 처리)
```

```text
Events
  Normal  SuccessfulCreate  job-controller  Created pod: hello-bxk7j
  Normal  Completed         job-controller  Job completed
```

> `status` 에 `uncountedTerminatedPods: {}` 라는 필드가 있다. 끝난 Pod 중 아직 `succeeded`/`failed` 에 반영 못 한 것을 임시로 담는 곳으로 **이해하고 있다.** 컨트롤러가 세는 도중 재시작해도 중복/누락이 없게 하려는 장치로 보인다. **확인하지 않았다.**

---

## 2. 실험 B — 실패와 backoffLimit ★★

두 가지를 나란히 만들었다. `backoffLimit: 3`, 명령은 `exit 1`.

```yaml
# fail-never
      restartPolicy: Never
# fail-onfailure
      restartPolicy: OnFailure
```

### 발견 7. 재시도 간격이 두 배씩 늘어난다

`fail-never` 의 Pod 생성 시각을 나이로 역산했다.

```text
c2hnl   T+0초
mffxw   T+15초     ← 간격 15
nj5mj   T+38초     ← 간격 23
x8vm6   T+81초     ← 간격 43
```

```text
각 Pod 가 도는 데 약 6초 (시작 + sleep 2 + 종료)
그걸 빼면 대기 시간이 나온다

  약 10초 → 약 20초 → 약 40초
```

```text
[왜]
  같은 실패를 1초 간격으로 반복하는 건 낭비다
  이미지가 없다 / 스크립트 오타 / DB 주소가 틀렸다
  → 1초 뒤에 해도 똑같이 실패한다
```

### 발견 8. ★★ Never 와 OnFailure 는 재시도 주체가 다르다

```text
[fail-never]  Pod 를 새로 만든다
  fail-never-c2hnl   RESTARTS 0
  fail-never-mffxw   RESTARTS 0
  fail-never-nj5mj   RESTARTS 0
  fail-never-x8vm6   RESTARTS 0
  → Pod 4개, 각각 재시작 0회

[fail-onfailure]  같은 Pod 안에서 컨테이너만 다시 켠다
  fail-onfailure-9klcv   RESTARTS 0 → 1 → 2 → 3
  → Pod 1개, 재시작 3회
```

```text
[Never]      Job 컨트롤러가 새 Pod 를 만든다
[OnFailure]  kubelet 이 그 자리에서 컨테이너를 다시 켠다
```

`CrashLoopBackOff` 가 그 증거다.

```text
fail-onfailure-9klcv   Error              RESTARTS 1
fail-onfailure-9klcv   CrashLoopBackOff   RESTARTS 1 (10s ago)   ← kubelet 이 대기 중
fail-onfailure-9klcv   Running            RESTARTS 2
fail-onfailure-9klcv   CrashLoopBackOff   RESTARTS 2 (28s ago)
fail-onfailure-9klcv   Running            RESTARTS 3
```

**두 층에 각각 backoff 가 있다.**

```text
Job 컨트롤러의 backoff    Pod 를 다시 만드는 간격
kubelet 의 backoff        컨테이너를 다시 켜는 간격
```

### 발견 9. ★★★ OnFailure 는 로그를 잃는다

```text
kubectl -n k8s-lab get pod | grep fail

fail-never-c2hnl   0/1   Error   4m23s
fail-never-mffxw   0/1   Error   4m8s
fail-never-nj5mj   0/1   Error   3m45s
fail-never-x8vm6   0/1   Error   3m2s
                   (fail-onfailure 의 Pod 는 없다)
```

`-w` 에 그 순간이 잡혔다.

```text
fail-onfailure-9klcv   Running       RESTARTS 3   50s
fail-onfailure-9klcv   Terminating   RESTARTS 3   50s     ← Job 이 포기하며 지웠다
fail-onfailure-9klcv   Error         RESTARTS 3   52s
                       (그리고 사라졌다)
```

```text
[왜 지웠나]
  OnFailure 는 Pod 가 계속 "살아 있는" 상태다
  kubelet 이 계속 다시 켜려 한다
  → Job 이 포기하려면 그 Pod 를 지워야 재시작이 멈춘다

[Never 는 지울 필요가 없다]
  각 Pod 가 이미 Error 로 끝나 있다
```

```text
[결과]
  Never       실패한 Pod 4개가 남는다 → 각 시도의 로그를 전부 볼 수 있다
  OnFailure   Pod 가 지워진다 → 로그가 사라진다
```

**배치 작업에는 `Never` 를 권한다.** 이 문서의 실용적 결론 하나다.

```text
[Never]      실패 원인을 추적해야 하는 작업 — 백업, 마이그레이션, 정산
[OnFailure]  일시적 실패가 예상되고 로그가 덜 중요한 작업
             Pod 오브젝트가 쌓이는 게 싫을 때
```

### 발견 10. backoffLimit: 3 = 총 4번

```text
"failed": 4
```

```text
처음 1번 + 재시도 3번 = 4
"limit" 은 재시도 횟수이지 총 횟수가 아니다
```

```text
conditions
  type: FailureTarget   reason: BackoffLimitExceeded
  type: Failed          reason: BackoffLimitExceeded

Events
  Warning  BackoffLimitExceeded  job-controller  Job has reached the specified backoff limit
```

**`Warning` 으로 남는다.** 성공 시에는 `Normal Completed` 였다. 5단계 감시 대상이다.

### 발견 11. 실패한 Job 의 DURATION 은 계속 늘어난다

```text
NAME             STATUS     COMPLETIONS   DURATION   AGE
fail-never       Failed     0/1           4m23s      4m23s     ← 같다
fail-onfailure   Failed     0/1           4m23s      4m23s     ← 같다
hello            Complete   1/1           31s        13m       ← 멈춰 있다
```

```text
실제로는 86초 만에 끝났다
  startTime           23:59:00
  Failed 로 바뀐 시각   00:00:26
```

```text
완료된 Job 은 completionTime 이 찍혀 DURATION 이 고정된다
실패한 Job 은 completionTime 이 없다 → 현재 시각까지 계속 센다

→ 실패한 Job 의 DURATION 은 소요 시간이 아니다
→ conditions 의 lastTransitionTime 을 봐야 한다
```

---

## 3. 실험 C — CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: cron-hello
  namespace: k8s-lab
spec:
  schedule: "*/1 * * * *"
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  jobTemplate:                      # Job 을 찍어내는 틀
    spec:
      backoffLimit: 2
      template:                     # 그 Job 이 Pod 를 찍어내는 틀
        spec:
          restartPolicy: Never
          containers:
          - name: hello
            image: nginx:alpine
            command: ["sh", "-c", "date '+%H:%M:%S  backup done'"]
```

```text
틀이 두 겹이다
  CronJob → jobTemplate → template → Pod
10편 StatefulSet 의 volumeClaimTemplates 와 같은 "틀 안의 틀" 구조다
```

### 발견 12. 3단 사슬

```text
NAME         SCHEDULE      TIMEZONE   SUSPEND   ACTIVE   LAST SCHEDULE   AGE
cron-hello   */1 * * * *   <none>     False     0        14s             2m44s

NAME                  STATUS     COMPLETIONS   DURATION   AGE
cron-hello-29793608   Complete   1/1           3s         2m14s
cron-hello-29793609   Complete   1/1           3s         74s
cron-hello-29793610   Complete   1/1           4s         14s

cron-hello-29793608-s4lwb   0/1   Completed   2m14s
cron-hello-29793609-zghpj   0/1   Completed   74s
cron-hello-29793610-p7wc2   0/1   Completed   14s
```

```text
CronJob → Job → Pod

[crontab]   시간이 되면 명령을 실행한다. 그걸로 끝
[CronJob]   시간이 되면 Job 오브젝트를 만든다. 그 Job 이 Pod 를 만든다
            → 실행 하나하나가 오브젝트로 남는다
```

### 발견 13. ★★★ 시간대가 9시간 어긋난다

```text
[명령을 실행한 시각 — master01]   09:07:30
[컨테이너 안에서 찍은 시각]        00:08:01 / 00:09:01 / 00:10:01
```

```text
TIMEZONE 칸이 <none> 이다
지정하지 않으면 kube-controller-manager 의 시간대를 따른다. 대부분 UTC 다
```

```text
[사고 시나리오]
  "매일 새벽 3시에 백업"     schedule: "0 3 * * *"
  의도    한국 시간 새벽 3시. 트래픽이 없는 시간
  실제    UTC 3시 = 한국 시간 낮 12시    ← 점심시간에 백업이 돈다
```

**9시간이나 어긋나는데 Job 은 정상적으로 성공한다.** 아무도 알려주지 않는다.

```yaml
spec:
  schedule: "0 3 * * *"
  timeZone: "Asia/Seoul"        # ← 이 필드로 지정한다
```

```text
그리고 컨테이너 안의 시각은 별개 문제다
  alpine 에는 타임존 데이터가 없어 항상 UTC 로 찍는다
  → TZ 환경변수를 넣거나 tzdata 를 설치해야 한다
```

실험 D 에서 `timeZone: "Asia/Seoul"` 을 넣어 필드가 유효함을 확인했다. **다만 `*/2` 는 시간대와 무관한 스케줄이라 실제 효과는 확인하지 못했다.**

### 발견 14. ★ Job 이름의 숫자가 시각이다

```text
cron-hello-29793608    00:08 실행
cron-hello-29793609    00:09 실행
cron-hello-29793610    00:10 실행
```

```text
29793608 분 × 60 = 1787616480 초
1970-01-01 부터 그만큼 지난 시점 = 2026-08-25 00:08

"기준 시각으로부터 몇 분째인가"
```

실험 D 에서 확인됐다. 2분 간격 CronJob 의 번호는 2씩 늘었다.

```text
db-backup-29793616
db-backup-29793618
```

```text
[왜 이렇게 짓나]
  Job 이름이 곧 "몇 분 스케줄의 실행인가" 를 뜻한다
  같은 이름을 두 번 만들 수 없다 (API Server 가 거부한다)
  → 같은 시각에 두 번 실행되는 일이 원천적으로 막힌다
```

```text
[crontab 이라면]
  cron 데몬이 두 번 실행하면 두 번 돈다. 막을 방법이 없다
[CronJob]
  이름 자체가 중복 방지 장치다
```

### 발견 15. 1초씩 늦는다

```text
00:08:01 / 00:09:01 / 00:10:01
```

```text
정각이 아니라 1초 뒤에 돈다
컨트롤러가 "지금 실행할 게 있나" 를 주기적으로 확인하기 때문이다
→ 초 단위 정밀도가 필요한 작업에는 CronJob 이 맞지 않는다
```

### 발견 16. 이력이 정확히 제한된다

```text
[2분 전]  29793608 / 609 / 610
[지금]    29793611 / 612 / 613
```

`successfulJobsHistoryLimit: 3` 대로 608~610 이 사라졌다. **Pod 도 함께 사라졌다**(Job 이 주인이므로).

```text
[제한이 없다면]
  1분마다 도는 CronJob 을 한 달 두면 Job 이 43200개
  → etcd 에 그만큼 쌓인다

기본값은 성공 3개 / 실패 1개다
```

### 발견 17. crontab 에 없는 칸들

```text
SUSPEND        False    일시정지. true 면 스케줄이 멈춘다
ACTIVE         0        지금 도는 Job 수
LAST SCHEDULE  14s      마지막 실행 시각
```

```text
[crontab 에서 잠깐 멈추려면]  줄 앞에 # 을 붙이고 저장한다. 파일을 고친다
[CronJob]  kubectl patch cronjob <이름> -p '{"spec":{"suspend":true}}'
           → 선언을 바꾸는 것으로 처리된다
```

---

## 4. 실험 D — 도는 DB 의 볼륨을 백업한다 ★★

### manifest

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: db-backup
  namespace: k8s-lab
spec:
  schedule: "*/2 * * * *"
  timeZone: "Asia/Seoul"            # 발견 13 적용
  concurrencyPolicy: Forbid         # 이전 실행이 안 끝났으면 건너뛴다
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: Never      # 발견 9 — 실패 로그를 남기려고
          containers:
          - name: backup
            image: nginx:alpine
            command:
            - sh
            - -c
            - |
              set -e
              TS=$(date '+%Y%m%d-%H%M%S')
              echo "=== backup start $TS"
              ls -la /data
              tar czf /backup/db-0-$TS.tar.gz -C /data .
              ls -lh /backup/db-0-$TS.tar.gz
              echo "=== backup done"
            volumeMounts:
            - name: data
              mountPath: /data
              readOnly: true        # 백업은 읽기만 한다
            - name: backup
              mountPath: /backup
          volumes:
          - name: data
            persistentVolumeClaim:
              claimName: data-db-0  # db-0 이 쓰고 있는 그 PVC
              readOnly: true
          - name: backup
            hostPath:
              path: /mnt/backup
              type: Directory
```

### concurrencyPolicy

```text
이전 실행이 아직 안 끝났는데 다음 시간이 왔을 때

  Allow (기본)   그냥 또 실행한다. crontab 과 같다
  Forbid         이번 차례를 건너뛴다
  Replace        이전 것을 죽이고 새로 시작한다
```

```text
백업이 10분 걸리는데 2분마다 돌면?
  Allow  → 5개가 동시에 돈다 → 디스크 I/O 가 폭발한다
  Forbid → 하나가 끝날 때까지 건너뛴다
```

```text
crontab 에는 이 개념이 없다
스크립트 안에 flock 이나 pid 파일로 직접 처리해야 한다
```

> `Forbid` 가 실제로 겹침을 막는지는 확인하지 않았다. 백업이 3초 만에 끝나 겹칠 일이 없었다.

### 발견 18. ★★ RWO 볼륨을 두 Pod 가 동시에 붙었다

```text
db-0                       Running     worker01    ← 쓰고 있다
db-backup-29793618-mkxqq   Completed   worker01    ← 같은 PVC 를 읽었다
```

**09편의 정의가 확인됐다.**

```text
ReadWriteOnce = "한 노드에서 읽기·쓰기"
→ Pod 하나가 아니라 노드 하나다
→ 같은 노드라면 여러 Pod 가 붙을 수 있다
```

```text
"Once" 를 "Pod 하나" 로 오해하기 쉽다
진짜 Pod 하나를 원하면 ReadWriteOncePod 를 써야 한다
```

#### 왜 Pod 가 아니라 노드 단위인가

09편 6절의 CSI 두 단계가 그 이유다.

```text
[1단계] NodeStageVolume    디스크를 노드에 붙이고 포맷하고 스테이징에 마운트
                           → 볼륨당 한 번. 노드 작업이다
[2단계] NodePublishVolume  스테이징을 Pod 디렉터리로 bind mount
                           → Pod 마다. Pod 작업이다
```

```text
1단계가 끝나면 볼륨은 이미 노드에 마운트돼 있다
2단계는 그걸 bind mount 로 나눠주는 것뿐이다
→ 같은 노드의 Pod 를 하나 더 붙이는 건 bind mount 하나 더 하는 것
```

#### 다만 파일시스템만 안전하다

```text
[같은 노드]  커널 하나가 캐시와 잠금을 조율한다 → 파일시스템은 안 깨진다
[다른 노드]  커널 둘이 서로를 모른다 → 파일시스템이 깨진다
```

```text
그런데 앱 데이터는 별개다. Kubernetes 가 안 막는다
같은 노드에서 MySQL 두 개가 같은 디렉터리를 쓰면
  파일시스템은 안 깨지지만 MySQL 데이터는 깨진다
→ DB 는 잠금 파일로 스스로 막는다
```

**우리 백업이 안전했던 이유는 `readOnly: true` 로 읽기만 했기 때문이다.**

### 발견 19. ★★ 스케줄러가 볼륨을 따라간다

백업 Pod 에 `nodeSelector` 를 적지 않았는데 worker01 에 떴다.

```text
[1] Pod  →  volumes.persistentVolumeClaim.claimName: data-db-0
[2] 스케줄러가 그 Pod 가 쓰는 PVC 를 찾는다
[3] data-db-0.spec.volumeName = local-pv-a
[4] local-pv-a.spec.nodeAffinity = kubernetes.io/hostname In [worker01]
[5] 그 조건을 Pod 의 배치 조건에 합친다
      master01 탈락 / worker02 탈락 / worker01 통과
```

**PV 의 조건이 Pod 의 조건이 된다.** Pod yaml 에 한 글자도 안 적었는데도.

10편 12절의 에러 메시지가 같은 규칙의 반대편이다.

```text
[10편 — 노드가 죽었을 때]
  1 node(s) didn't match PersistentVolume's node affinity  → 갈 곳이 없어 Pending
[여기 — 정상]
  같은 규칙이 worker01 하나만 남겼다 → 갈 곳이 정해져 성공
```

### 발견 20. ★ hostPath 는 이 사슬에 참여하지 않는다 — 숨은 위험

백업 Pod 는 볼륨을 둘 마운트했다.

```text
[PVC]       스케줄러가 nodeAffinity 를 읽어 노드를 좁힌다
[hostPath]  노드 정보가 없다. 스케줄러가 고려하지 않는다   ← 09편의 그 문제
```

```text
[만약 PVC 가 없었다면]
  스케줄러가 아무 노드나 고른다 → worker02 로 간다
  → /mnt/backup 이 거기 없다
  → type: Directory 라 마운트 실패 → ContainerCreating 에서 멈춘다
```

**우연히 맞았다.** PVC 가 worker01 로 강제했고 마침 `/mnt/backup` 도 거기 있었다.

```text
[제대로 하려면]
  hostPath 대신 별도 PVC 를 쓴다
  또는 nodeSelector 를 명시한다
  또는 local PV 로 만들어 nodeAffinity 를 붙인다
```

### 실측 결과

```text
=== backup start 20260825-001801
--- source
drwxr-xr-x  4096  Aug 21 06:33  .
-rw-r--r--    17  Aug 21 06:20  marker.txt
-rw-r--r--    16  Aug 21 06:33  who.txt
--- result
-rw-r--r--   188  Aug 25 00:18  /backup/db-0-20260825-001801.tar.gz
=== backup done
```

```text
worker01:/mnt/backup
  db-0-20260825-001607.tar.gz   188  Aug 25 09:16
  db-0-20260825-001801.tar.gz   188  Aug 25 09:18
```

**10편에서 넣은 `marker.txt` / `who.txt` 가 그대로 보인다.** 전원 차단과 재시작을 거치고도 남아 있던 파일이 이제 백업까지 됐다.

```text
파일명 시각   001801 (UTC)
노드 파일 시각  09:18 (KST)
→ timeZone 은 "언제 실행할지" 에만 적용된다. 컨테이너 안 시각은 별개다
```

---

## 5. 이 백업은 안전한가 ★★★

**백업이 만들어졌다고 끝이 아니다.** 지금 만든 것에는 문제가 다섯 있다.

### 문제 1 — 원본과 백업이 같은 디스크에 있다

```text
원본    worker01 : /mnt/disks/vol-a
백업    worker01 : /mnt/backup
```

```text
worker01 이 죽으면 원본도 백업도 못 읽는다
```

```text
[백업이 막아주는 것]
  실수로 데이터를 지웠다   ← 같은 노드에 있어도 막아준다
  잘못된 쿼리로 덮어썼다   ← 막아준다
  서버가 죽었다           ← 못 막는다
  디스크가 망가졌다        ← 못 막는다
```

10편에서 worker02 전원을 내렸을 때를 떠올리면 된다. 그 노드가 살아나기 전까지 아무것도 못 꺼냈다.

### 문제 2 — 무한히 쌓인다

```text
hostPath 는 Kubernetes 가 관리하지 않는다
  용량 제한이 없다 / 오래된 파일 삭제 정책도 없다

2분마다 백업하면 하루에 720개 → 노드 디스크가 찬다
→ 그러면 그 노드의 모든 Pod 가 영향을 받는다
```

**Job 이력은 3개로 제한했지만 백업 파일은 제한이 없다.** 다른 것이다.

### 문제 3 — 복구를 해본 적이 없다

```text
tar 파일이 188바이트다
안에 뭐가 들었는지 확인 안 했다. 풀어봤는데 깨져 있으면?
```

```text
"백업은 매일 돌고 있었습니다"
"그런데 복구해보니 3개월째 빈 파일이었습니다"
```

### 문제 4 — 실제 DB 라면 이 방식은 위험하다

```text
지금은 파일 두 개를 tar 로 묶었다. 아무도 안 쓰고 있으니 안전하다

실제 DB 라면
  tar 를 뜨는 동안에도 DB 는 계속 쓴다
  → 파일마다 다른 시점이 섞인다
  → 복구해도 정합성이 깨진 상태다
```

```text
[그래서 DB 는 전용 도구를 쓴다]
  pg_dump / mysqldump   DB 에게 일관된 시점을 요청한다
  스냅샷                 스토리지 층에서 순간을 얼린다
```

09편에서 본 저널·WAL 이야기와 같은 맥락이다.

### 문제 5 — 실패해도 아무도 모른다

```text
백업 Job 이 Failed 가 되면 기록은 남는다. 그런데 아무도 안 본다
```

**이 시리즈에서 네 번째다.**

```text
09편   PVC 는 Bound 인데 데이터가 없다
10편   Pod 가 멈춰 있는데 이벤트가 없다
12편   DaemonSet 이 0개인데 지표가 정상이다
13편   백업이 실패했는데 아무도 안 알려준다
```

---

## 6. 3단계에서 채워야 할 것

```text
[1] 백업을 다른 곳에 둔다
      NFS / S3 / 다른 노드. 최소한 원본과 다른 물리 디스크

[2] 보관 정책을 만든다
      N일 지난 파일 삭제. Job 안에서 find -mtime +7 -delete

[3] 복구 절차를 문서로 만들고 실제로 해본다
      "복구 리허설" 을 정기적으로

[4] DB 전용 도구를 쓴다
      pg_dump 로 일관된 시점을 뜬다

[5] 실패를 감지한다  → 5단계
      kube_job_status_failed 감시
      CronJob 이 예정된 시각에 안 돌았을 때도 알아야 한다
```

---

## 정리

```text
[성격]
 1. restartPolicy 는 기본값이 없다. Never 나 OnFailure 를 반드시 적는다
    Always 는 Job 의 목적과 충돌하고, 나머지 둘도 조용히 정하면 위험하다
 2. 끝나고도 Pod 가 남는다. 결과와 로그를 남겨야 하기 때문이다
 3. 상태 해석이 정반대다 ★
    Deployment 의 ready 0 = 장애 / Job 의 ready 0 = 정상
    succeeded 는 누적이고 줄어들지 않는다
 4. DURATION 에 이미지 받는 시간이 섞인다. 작업 시간은 앱이 로그로 남겨야 한다
 5. 컨트롤러가 1단이다. 세대 개념이 없어 중간 그릇이 필요 없다

[실패]
 6. 재시도 간격이 두 배씩 늘어난다 (10초 → 20초 → 40초)
 7. Never 와 OnFailure 는 재시도 주체가 다르다 ★★
    Never      Job 컨트롤러가 새 Pod 를 만든다
    OnFailure  kubelet 이 그 자리에서 컨테이너를 다시 켠다 (CrashLoopBackOff)
    → 두 층에 각각 backoff 가 있다
 8. OnFailure 는 포기할 때 Pod 를 지운다 → 로그가 사라진다 ★★★
    배치 작업에는 Never 를 권한다
 9. backoffLimit: 3 = 총 4번. limit 은 재시도 횟수다
10. 포기하면 Warning BackoffLimitExceeded 가 남는다
11. 실패한 Job 의 DURATION 은 계속 늘어난다. 소요 시간이 아니다

[CronJob]
12. 3단 사슬이다. CronJob → Job → Pod
    실행 하나하나가 오브젝트로 남는다 (crontab 은 안 남는다)
13. 시간대가 UTC 다 ★★★
    "매일 3시" 가 한국 시간 낮 12시가 된다. Job 은 정상 성공한다
    → timeZone 필드로 지정한다. 컨테이너 안 시각은 또 별개다
14. Job 이름의 숫자는 "기준 시각부터 몇 분째" 다 ★
    같은 이름을 두 번 못 만들므로 중복 실행이 원천 차단된다
15. 정각이 아니라 1초쯤 뒤에 돈다. 초 단위 정밀도에는 안 맞는다
16. 이력이 제한된다 (기본 성공 3 / 실패 1). Job 을 지우면 Pod 도 사라진다
17. crontab 에 없는 것들 — suspend / concurrencyPolicy / ACTIVE / LAST SCHEDULE

[백업 실험]
18. RWO 볼륨을 같은 노드의 두 Pod 가 동시에 붙었다 ★★
    "Once" 는 Pod 가 아니라 노드 단위다 (09편 정의 확인)
    CSI 가 "노드당 1회 마운트 + Pod 마다 bind mount" 구조라서 그렇다
    파일시스템은 안전하지만 앱 데이터는 앱이 지켜야 한다
19. 스케줄러가 PVC → PV → nodeAffinity 를 따라가 노드를 정했다 ★★
    Pod 에 아무것도 안 적었는데 worker01 로 갔다
20. hostPath 는 그 사슬에 참여하지 않는다 ★
    PVC 가 없었다면 엉뚱한 노드로 가서 마운트에 실패했을 것이다

[백업이 백업이 되려면]
21. 원본과 같은 노드에 두면 백업이 아니다 ★★★
    실수는 막아주지만 서버·디스크 장애는 못 막는다
22. hostPath 는 용량 제한도 보관 정책도 없다. 무한히 쌓인다
23. 복구를 검증하지 않은 백업은 백업이 아니다
24. 실제 DB 는 tar 로 뜨면 정합성이 깨진다. pg_dump 나 스냅샷을 쓴다
25. 실패해도 아무도 안 알려준다 → 이 시리즈에서 네 번째다
```

## 확인 명령

```bash
# Job
kubectl -n <ns> get job
kubectl -n <ns> get job <이름> -o jsonpath='{.status}' | tr ',' '\n'
kubectl -n <ns> logs <pod>                       # 남아 있으므로 볼 수 있다
kubectl -n <ns> describe job <이름> | sed -n '/^Events/,$p'

# 실패 원인
kubectl -n <ns> get pod | grep <job이름>          # Never 면 시도마다 Pod 가 남는다
kubectl -n <ns> logs <pod>

# CronJob
kubectl -n <ns> get cronjob                      # TIMEZONE / SUSPEND / ACTIVE / LAST SCHEDULE
kubectl -n <ns> get job | grep <cronjob이름>
kubectl -n <ns> logs -l job-name --tail=30 --prefix

# 일시정지 / 재개
kubectl -n <ns> patch cronjob <이름> -p '{"spec":{"suspend":true}}'
kubectl -n <ns> patch cronjob <이름> -p '{"spec":{"suspend":false}}'

# 스케줄을 안 기다리고 즉시 한 번 돌려보기
kubectl -n <ns> create job manual-run --from=cronjob/<이름>
```

## 미확인

```text
 1. uncountedTerminatedPods 의 정확한 역할
 2. concurrencyPolicy: Forbid / Replace 의 실제 동작 (겹칠 일이 없어 미확인)
 3. timeZone 이 실제로 스케줄을 옮기는지 (*/2 는 시간대와 무관했다)
 4. startingDeadlineSeconds — 클러스터가 멈춰 있다 살아났을 때의 동작
 5. completions / parallelism 을 2 이상으로 뒀을 때의 병렬 처리
 6. ttlSecondsAfterFinished — 끝난 Job 을 자동으로 지우는 설정
 7. activeDeadlineSeconds — 시간 초과로 중단시키는 설정
 8. OnFailure 에서 Job 이 Pod 를 지우는 정확한 조건
 9. kubelet 의 CrashLoopBackOff 간격 상한 (5분으로 알고 있으나 미확인)
10. suspend: true 로 두는 동안 놓친 스케줄이 나중에 몰려 실행되는지
```

## 정리 명령

```bash
kubectl -n k8s-lab delete cronjob db-backup
kubectl -n k8s-lab delete job --all
rm -f ~/manifests/job-bad.yaml ~/manifests/job-hello.yaml \
      ~/manifests/job-fail.yaml ~/manifests/cron-hello.yaml
# worker01 의 /mnt/backup 파일은 남는다 (hostPath 라 자동 정리 안 됨)
# → "Kubernetes 가 관리하지 않는다" 의 증거이기도 하다
```

## 다음

```text
2단계 완료. 3단계로 넘긴다

3단계   PostgreSQL StatefulSet + 백업 CronJob (여기서 만든 것을 제대로)
        pg_dump / 보관 정책 / 복구 절차
5단계   kube_job_status_failed 감시 — 문제 5의 해답
```

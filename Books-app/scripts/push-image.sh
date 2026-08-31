#!/usr/bin/env bash
#
# build01 에서 만든 이미지를 Kubernetes 워커 노드로 보낸다.
#
# 왜 이 스크립트가 필요한가
#   build01 에서 docker build 를 하면 그 이미지는 build01 에만 있다
#   worker01/02 의 containerd 는 그 이미지를 모른다
#   → Pod 를 만들면 ImagePullBackOff 가 난다
#
#   Registry 가 있으면 노드가 알아서 받아간다
#   지금은 Registry 가 없다 → 우리가 직접 밀어넣는다
#
#   → 이 번거로움을 겪은 뒤 4단계에서 로컬 Registry 를 세운다
#   → 그것마저 8단계에서 CI 가 하게 만든다
#
#
# ★ "반 수동" 이다. 판단은 사람이 한다
#   어느 이미지를 보낼지 매번 고른다
#   배포(kubectl apply)는 하지 않는다 → 사람이 Manifest 를 고쳐서 한다
#
#   CI 는 아티팩트를 만들고, CD 는 배포 상태를 반영한다
#   그 책임 분리를 스크립트 범위로도 지킨다
#
#
# 사용법
#   ./scripts/push-image.sh
#   NODES="worker01" ./scripts/push-image.sh      # 특정 노드만
#
# 사전 조건
#   각 노드에 sudoers 설정이 되어 있어야 한다
#     sjpark ALL=(ALL) NOPASSWD: /usr/bin/ctr
#   없으면 sudo 가 비밀번호를 물으려 하는데
#   stdin 이 tar 스트림이라 물을 수가 없다 → "no tty present" 로 실패한다

set -euo pipefail
# set -e   명령이 실패하면 즉시 멈춘다. 실패를 지나치지 않는다
# set -u   정의 안 된 변수를 쓰면 멈춘다. 오타를 잡는다
# set -o pipefail
#          파이프 중간이 실패해도 전체를 실패로 본다      ★ 여기서 중요하다
#          docker save 가 실패해도 ssh 가 성공하면
#          pipefail 이 없으면 "성공" 으로 보인다

SSH_USER="${SSH_USER:-sjpark}"
NODES="${NODES:-worker01 worker02}"

# containerd 의 네임스페이스                              ★★ 가장 흔한 함정
#   ctr 는 기본으로 "default" 네임스페이스를 쓴다
#   kubelet 은 "k8s.io" 만 본다
#   → -n 을 빼면 import 는 성공하는데 Pod 는 ImagePullBackOff
#   → 둘 다 정상처럼 보이는 조용한 실패다
CTR_NS="k8s.io"

# master01 은 대상이 아니다
#   control-plane 에 NoSchedule taint 가 걸려 있어 앱 Pod 가 안 뜬다
#   → 이미지를 보낼 이유가 없다
#   확인:  kubectl describe node master01 | grep -i taint


# ─────────────────────────────────────────────────────────────
# 1. 이미지 고르기
# ─────────────────────────────────────────────────────────────

echo "== build01 의 bookstore 이미지 =="
docker images bookstore
echo

mapfile -t TAGS < <(
  docker images --format '{{.Repository}}:{{.Tag}}' bookstore \
    | grep -v ':<none>$' \
    | sort -r
)

if [ ${#TAGS[@]} -eq 0 ]; then
  echo "bookstore 이미지가 없다. 먼저 빌드해야 한다:"
  echo "  docker build -t bookstore:\$(date +%Y%m%d-%H%M) ."
  exit 1
fi

echo "== 보낼 이미지를 고른다 =="
for i in "${!TAGS[@]}"; do
  printf "  [%d] %s\n" "$((i + 1))" "${TAGS[$i]}"
done
echo

read -rp "번호: " CHOICE
if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -gt ${#TAGS[@]} ]; then
  echo "잘못된 번호다"
  exit 1
fi
IMAGE="${TAGS[$((CHOICE - 1))]}"

# ★ dev 태그 경고
#   같은 태그를 계속 덮어쓰면 노드마다 다른 코드가 돌 수 있다
#   → worker01 은 새 이미지, worker02 는 옛날 이미지
#   → "이 Pod 만 이상해요" 가 된다. 원인 파악이 매우 어렵다
if [[ "$IMAGE" == *":dev" ]] || [[ "$IMAGE" == *":latest" ]]; then
  echo
  echo "!! 경고: '$IMAGE' 는 고정 태그다"
  echo "   노드마다 다른 내용이 같은 이름으로 들어갈 수 있다"
  echo "   날짜 태그를 권한다:"
  echo "     docker tag $IMAGE bookstore:\$(date +%Y%m%d-%H%M)"
  echo
  read -rp "그래도 진행할까? (yes 를 입력) " CONFIRM
  [ "$CONFIRM" = "yes" ] || exit 1
fi


# ─────────────────────────────────────────────────────────────
# 2. 대상 확인
#
# 그냥 진행하지 않는다
#   노드에 이미지를 밀어넣는 건 되돌리기 번거로운 작업이다
#   무엇을 어디로 보내는지 한 번 보고 넘어간다
# ─────────────────────────────────────────────────────────────

echo
echo "== 확인 =="
echo "  이미지 : $IMAGE"
echo "  대상   : $NODES"
echo "  계정   : $SSH_USER"
echo "  네임스페이스 : $CTR_NS"
echo
read -rp "진행할까? (yes 를 입력) " CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "취소했다"; exit 0; }


# ─────────────────────────────────────────────────────────────
# 3. 전송
#
# 파일을 안 만들고 파이프로 바로 보낸다
#   임시 tar 를 만들면 build01 과 노드 양쪽에 남는다
#   지우는 걸 잊으면 디스크가 찬다
#
# 순차로 돈다. 병렬(&)로 하지 않는다
#   비밀번호 인증이라 프롬프트가 서로 엉킨다
#   → 어느 노드 비밀번호를 묻는지 알 수 없게 된다
# ─────────────────────────────────────────────────────────────

FAILED=()

for NODE in $NODES; do
  echo
  echo "── $NODE ──────────────────────────────"
  START=$SECONDS

  # import 와 확인을 한 번의 ssh 로 묶는다
  #   따로 하면 비밀번호를 두 번 물어본다
  if docker save "$IMAGE" \
     | ssh "$SSH_USER@$NODE" \
         "sudo ctr -n $CTR_NS images import - >/dev/null \
          && sudo ctr -n $CTR_NS images ls -q | grep -F '$IMAGE'"
  then
    echo "   완료 ($((SECONDS - START))초)"
  else
    echo "   실패"
    FAILED+=("$NODE")
  fi
done


# ─────────────────────────────────────────────────────────────
# 4. 결과
# ─────────────────────────────────────────────────────────────

echo
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "!! 실패한 노드: ${FAILED[*]}"
  echo
  echo "   확인해볼 것"
  echo "     1) SSH 접속이 되는가        ssh $SSH_USER@<노드>"
  echo "     2) NOPASSWD 가 걸렸는가     sudo -n ctr --version"
  echo "     3) 디스크 여유가 있는가      df -h /var/lib/containerd"
  exit 1
fi

echo "== 전 노드 완료 =="
echo
echo "Manifest 에 이렇게 쓴다 (4단계)"
echo
echo "        image: $IMAGE"
echo "        imagePullPolicy: IfNotPresent"
echo
echo "  ★ Always 로 두면 안 된다"
echo "    레지스트리에서 다시 받으려 하는데 레지스트리가 없다 → ErrImagePull"
echo
echo "노드에서 직접 확인하려면"
echo "  sudo ctr -n $CTR_NS images ls | grep bookstore"
echo "  sudo crictl images | grep bookstore      # crictl 은 항상 k8s.io 를 본다"

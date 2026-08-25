"""로그를 stdout 으로 JSON 한 줄씩 내보낸다.

02-cloud-portability.md 의 규칙

  로그는 stdout / stderr 로만. 파일에 쓰지 않는다
    컨테이너의 파일시스템은 사라진다
    로컬이든 EKS 든 로그 수집은 stdout 을 읽는 방식이다

  구조화(JSON)한다
    5단계에서 필드로 거를 수 있다
    {"level":"error","request_id":"...","code":"DB_UNAVAILABLE"}
    → "code=DB_UNAVAILABLE 인 로그만" 같은 검색이 된다

05-metrics.md 와의 역할 분담

  지표   "어디가" 를 좁힌다. 집계다. 라벨에 무한한 값을 넣으면 안 된다
  로그   "무엇이" 를 찾는다. 개별 건이다. request_id / book_id 를 여기 넣는다
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

# 요청 하나가 API → Redis → DB → 큐 → Worker 로 흐른다.
# 그 전 구간을 잇는 유일한 키가 request_id 다. (01 문서)
#
# contextvars 를 쓰는 이유
#   전역 변수로 두면 동시 요청끼리 섞인다
#   contextvars 는 비동기 작업마다 따로 값을 갖는다
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

# 로그 레코드에 우리가 얹는 필드들. 파이썬 기본 필드와 구분하려고 접두사를 쓴다
_EXTRA_PREFIX = "ctx_"

# LogRecord 가 원래 갖고 있는 속성들. 이걸 빼야 우리가 넣은 것만 남는다
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"asctime", "message", "taskName"}


class JsonFormatter(logging.Formatter):
    """로그 레코드를 JSON 한 줄로 만든다."""

    def __init__(self, *, service: str, pod: str, node: str, version: str) -> None:
        super().__init__()
        self.service = service
        self.pod = pod
        self.node = node
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # 시각은 UTC 로 남긴다 (02 문서)
            # 노드 시간대에 의존하면 로컬과 클러스터가 다르게 찍힌다
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "service": self.service,
            "pod": self.pod,
            "node": self.node,
            "version": self.version,
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        # logger.info("...", extra={"ctx_book_id": 1}) 로 넘긴 값들을 꺼낸다
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS:
                continue
            if key.startswith(_EXTRA_PREFIX):
                payload[key[len(_EXTRA_PREFIX):]] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # ensure_ascii=False 여야 한글이 \uXXXX 로 안 깨진다
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    *,
    level: str,
    service: str,
    pod: str,
    node: str,
    version: str,
) -> None:
    """루트 로거를 JSON 출력으로 바꾼다. 기동 시 한 번만 부른다."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(service=service, pod=pod, node=node, version=version)
    )

    root = logging.getLogger()
    # 기본 핸들러를 지운다. 안 지우면 같은 로그가 두 번 찍힌다
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(_parse_level(level))

    # uvicorn 이 자기 핸들러를 붙여둔다. 그대로 두면 JSON 이 아닌 줄이 섞인다
    #   propagate=True 로 바꿔 루트 핸들러(우리 것)를 타게 한다
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # 접속 로그는 우리가 직접 남긴다 (request_id 를 붙여야 하므로)
    # uvicorn 의 access 로그는 꺼둔다. 두 벌이면 지저분하다
    logging.getLogger("uvicorn.access").disabled = True


def _parse_level(level: str) -> int:
    mapping = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    # 모르는 값이면 info 로 떨어뜨린다. 여기서 죽일 만큼 중요하진 않다
    return mapping.get(level.lower(), logging.INFO)

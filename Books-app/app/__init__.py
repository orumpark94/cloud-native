"""서점 APP — 장애 실험을 위한 애플리케이션.

설계 문서는 ../03.bookstore-app/ 에 있다.

  00-architecture.md      세 경로 (읽기 / 동기쓰기 / 비동기)
  01-api-spec.md          엔드포인트와 응답 코드
  02-cloud-portability.md 같은 이미지가 EKS 에서도 돌게 하는 제약
  03-data-model.md        books / orders
  04-health-check.md      live 와 ready 의 판단 기준
  05-metrics.md           무엇을 재야 문제를 알 수 있는가
  06-fault-injection.md   장애를 일부러 일으키는 수단
  07-dockerfile.md        이미지
  08-compose.md           로컬 개발 환경
"""

__version__ = "0.1.0"

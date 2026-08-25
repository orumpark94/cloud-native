# 03. 데이터 모델 — 테이블 둘

01 에서 정한 API 를 테이블로 옮긴다. **02 의 이식성 제약 아래에서 설계한다.**

---

## 0. 전체

```text
books     책. 재고를 포함한다
orders    주문. 처리 상태와 시각을 담는다

테이블은 이 둘뿐이다
users 는 만들지 않는다 (01 문서 — 인증을 만들지 않으므로)
```

---

## 1. books

```sql
CREATE TABLE books (
    id     BIGSERIAL PRIMARY KEY,
    title  TEXT      NOT NULL,
    price  INTEGER   NOT NULL,
    stock  INTEGER   NOT NULL
);
```

### 판단 1 — 금액은 정수다

```text
[선택지]
  INTEGER        원 단위 정수. 15000 = 15,000원
  NUMERIC(10,2)  소수점을 다루는 정확한 십진수
  FLOAT / REAL   부동소수점                    ← 절대 안 된다
```

```text
[FLOAT 이 안 되는 이유]
  0.1 + 0.2 가 0.30000000000000004 가 된다
  금액을 더하다 보면 1원씩 어긋난다
  → 금융 데이터에 부동소수점을 쓰면 안 된다
```

```text
[INTEGER 를 고른 이유]
  한국 원화는 소수점이 없다
  → 원 단위 정수면 충분하다

  다국가 통화를 다룬다면 NUMERIC 이나 "최소 단위 정수"(센트)를 쓴다
  이 프로젝트는 그럴 일이 없다
```

### 판단 2 — 재고를 books 에 둔다 ★

```text
[선택지]
  A. books.stock                  같은 행에 둔다
  B. book_stocks 별도 테이블       분리한다
  C. 재고 변동 이력 테이블          재고 = 변동의 합계
```

**A 를 고른다.**

```text
[B 가 A 보다 나은 게 별로 없다]
  분리해도 책 하나당 행 하나다
  → 주문이 몰릴 때 그 행에 잠금이 집중되는 건 똑같다
  → 구조만 복잡해진다

[C 는 잠금이 없다]
  주문마다 INSERT 만 하면 된다 → 경합이 없다
  대신 재고를 알려면 매번 합계를 낸다 → 조회가 비싸다
  → 실무에서는 스냅샷을 같이 둔다. 복잡해진다
```

```text
[A 의 문제를 알면서도 고른 이유]
  인기 있는 책 하나에 주문이 몰리면
  → 그 행에 잠금이 집중된다
  → 주문이 줄을 서서 하나씩 처리된다 (직렬화)
  → Pod 를 늘려도 처리량이 안 오른다

  이게 실무에서 "hot row" 라고 부르는 문제다
```

**그 병목 자체가 실험 재료다.**

```text
[6단계 실험]
  책 1번에만 주문을 몰아넣는다 vs 여러 책에 고르게 넣는다
  → 처리량과 응답 시간이 얼마나 다른지 잰다
  → "Pod 를 늘렸는데 왜 안 빨라지나" 를 직접 겪는다
  → 그다음 C 같은 구조를 논의한다
```

### 판단 3 — 재고 음수 방지 CHECK 를 지금은 걸지 않는다 ★

```sql
-- 나중에 추가할 것
-- ALTER TABLE books ADD CONSTRAINT books_stock_non_negative CHECK (stock >= 0);
```

```text
[걸면]
  잠금이 없어도 DB 가 음수를 막아준다
  → 재고가 -1 이 되는 상황을 만들 수 없다

[00 문서에서 정한 것]
  1차   잠금 없이 만든다 → 부하를 넣어 재고를 음수로 만든다
  2차   잠금을 넣는다 → 음수가 안 나오는지 확인한다
```

```text
CHECK 를 처음부터 걸면 1차 실험이 불가능하다
→ 음수를 관찰한 뒤에 건다
```

**"실패한 출력 자체가 학습 대상" 이라는 원칙 그대로다.**

```text
[다만 문서에 남긴다]
  실무라면 처음부터 건다
  애플리케이션 잠금이 실패해도 DB 가 마지막 방어선이 되기 때문이다
```

### 인덱스

```text
PRIMARY KEY (id) 뿐이다
```

```text
[title 검색을 안 만들었으므로 인덱스도 없다]
  01 문서에서 검색 기능을 빼기로 했다
  나중에 넣는다면 그때 판단한다
```

---

## 2. orders

```sql
CREATE TABLE orders (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         BIGINT      NOT NULL,
    book_id         BIGINT      NOT NULL REFERENCES books(id),
    quantity        INTEGER     NOT NULL,
    unit_price      INTEGER     NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    failure_reason  TEXT,

    CONSTRAINT orders_status_valid
        CHECK (status IN ('pending','processing','completed','failed')),
    CONSTRAINT orders_quantity_positive
        CHECK (quantity > 0)
);
```

### 판단 4 — 시각은 전부 TIMESTAMPTZ ★

```text
TIMESTAMP     시간대 정보가 없다. "2026-08-25 10:00" 만 저장한다
TIMESTAMPTZ   내부적으로 UTC 로 저장하고, 읽을 때 세션 시간대로 변환한다
```

```text
[TIMESTAMP 가 위험한 이유]
  저장한 사람과 읽는 사람의 시간대가 다르면 값이 달라진다

  로컬 노드   KST 로 저장
  EKS 노드    UTC 로 해석
  → 9시간 어긋난다. 그런데 아무 에러도 안 난다
```

**13편에서 겪은 그 문제다.** CronJob 이 UTC 로 돌아 "새벽 3시" 가 낮 12시가 됐는데 Job 은 정상 성공했다.

```text
[02 문서의 규칙]
  시각은 UTC 로 다루고 표시만 바꾼다
  → TIMESTAMPTZ 가 그 규칙을 DB 층에서 강제한다
```

### 판단 5 — 시각을 셋 나눠 담는다

```text
created_at    접수 시각        API 가 기록
started_at    Worker 가 집은 시각
finished_at   처리를 끝낸 시각
```

```text
created → started    큐에서 기다린 시간      ★ 적체를 재는 값
started → finished   실제 처리 시간
```

```text
[하나로 뭉치면]
  "주문 처리가 5초 걸렸다" 만 안다
  큐에서 4.9초 기다린 건지, 처리가 4.9초 걸린 건지 모른다
  → 원인을 못 찾는다
```

**13편의 그 문제와 같다.** Job 의 `DURATION` 에 이미지 받는 시간이 섞여 있어 실제 작업 시간을 알 수 없었다.

### 판단 6 — unit_price 를 저장한다. total_price 는 계산한다 ★

```text
[문제]
  책값이 15,000원일 때 주문했다
  나중에 책값이 18,000원으로 바뀌었다
  → 그 주문의 금액은 얼마인가?
```

```text
[books.price 를 조인하면]
  과거 주문 금액이 18,000원으로 바뀐다   ← 틀렸다
  영수증이 나중에 바뀌는 셈이다

[unit_price 를 저장하면]
  주문 시점 가격이 박제된다
```

```text
[total_price 는 왜 저장 안 하나]
  unit_price × quantity 로 계산하면 된다
  중복 저장하면 둘이 어긋날 수 있다

  실무에서는 저장하기도 한다
  → 할인·쿠폰·배송비가 붙으면 단순 곱이 아니기 때문이다
  → 이 앱에는 그런 게 없다
```

**API 응답의 `total_price` 는 앱에서 계산해서 준다.**

### 판단 7 — status 는 TEXT + CHECK

```text
[선택지]
  ENUM 타입     타입 안전. 값을 추가하려면 ALTER TYPE 이 필요하다
  TEXT + CHECK  유연하다. 제약 이름으로 검증한다
```

```text
[TEXT + CHECK 를 고른 이유]
  4단계에서 스키마 변경을 배포하는 방법을 다룬다
  ENUM 은 변경이 까다로워 그 실습이 복잡해진다

  그리고 CHECK 제약은 위반 시 에러 메시지가 명확하다
```

```text
[상태 전이]
  pending ──→ processing ──→ completed
                    └──────→ failed
```

```text
[전이 규칙을 DB 로 강제하지 않는다]
  트리거로 막을 수 있지만 안 한다
  → 잘못된 전이가 생기는 상황도 관찰 대상이다
  → 예: Worker 가 중간에 죽으면 processing 인 채로 남는다
```

### 판단 8 — book_id 에 외래 키를 건다. user_id 에는 안 건다

```text
book_id  REFERENCES books(id)     ← 건다
user_id  BIGINT                   ← 안 건다. users 테이블이 없다
```

```text
[book_id 에 거는 이유]
  없는 책으로 주문이 들어오면 안 된다
  앱이 확인하지만 DB 가 마지막 방어선이 된다

[user_id 에 안 거는 이유]
  users 테이블을 안 만들기로 했다 (01 문서)
  → 참조할 대상이 없다
```

---

## 3. 인덱스 — 지금은 안 만든다 ★

```text
books    PRIMARY KEY (id)
orders   PRIMARY KEY (id)
         book_id 의 외래 키       ← PostgreSQL 은 FK 에 인덱스를 자동으로 안 만든다
```

**필요해 보이는 인덱스를 알면서도 지금은 만들지 않는다.**

```text
[나중에 필요할 것으로 예상되는 것]
  orders (user_id, created_at DESC)   GET /orders — 내 주문 목록
  orders (status, created_at)         적체 확인 — pending 이 몇 개인가
  orders (book_id)                    FK 조회
```

### 왜 미리 안 만드나

```text
[만들고 시작하면]
  "인덱스 덕분에 빠르다" 를 확인할 수 없다
  왜 그 인덱스가 필요한지도 설명 못 한다

[없이 시작하면]
  데이터를 늘린다 → 느려진다 → EXPLAIN 으로 확인한다
  → 인덱스를 추가한다 → 얼마나 빨라졌는지 잰다
```

```text
[측정 계획 — 5단계]
  1. 주문 10만 건을 넣는다
  2. GET /orders 응답 시간을 잰다
  3. EXPLAIN ANALYZE 로 Seq Scan 을 확인한다
  4. 인덱스를 추가한다
  5. 다시 잰다. 몇 배 빨라졌는지 기록한다
```

**이게 `/debug/slow-query` 보다 현실적인 느린 쿼리다.** 인위적인 `sleep` 이 아니라 진짜 원인이 있는 지연이다.

```text
[실무라면 처음부터 만든다]
  이건 학습을 위한 선택이다. 문서에 남긴다
```

---

## 4. 초기 데이터

```text
[기본]     책 1,000권
[부하용]   책 100,000권 / 주문 100,000건 (별도 스크립트)
```

```text
[1,000권으로 시작하는 이유]
  기능 확인에는 충분하다
  Compose 를 띄울 때마다 오래 걸리면 개발이 느려진다

[10만 건이 필요한 이유]
  OFFSET 이 커질 때 느려지는 걸 보려면 데이터가 많아야 한다 (01 문서)
  인덱스 유무의 차이도 데이터가 적으면 안 보인다
```

```text
[생성 방식]
  기본 데이터   초기화 SQL 에 포함
  부하 데이터   별도 스크립트. 필요할 때만 실행
```

---

## 5. 스키마를 언제 적용할 것인가 ★

**여기에 4단계로 넘길 문제가 하나 있다.**

```text
[3단계 — Docker Compose]
  PostgreSQL 컨테이너가 처음 뜰 때 초기화 SQL 을 실행한다
  → 간단하다
```

```text
[4단계 — Kubernetes]
  API Pod 가 3개다. 각자 기동하며 스키마를 만들려 하면?
  → 셋이 동시에 CREATE TABLE 을 한다
  → 하나는 성공하고 둘은 에러가 난다
  → 재시작을 반복할 수도 있다
```

```text
[선택지 — 4단계에서 다룬다]
  A. initContainer 로 마이그레이션          Pod 마다 돈다. 여전히 경쟁
  B. 별도 Job 으로 한 번만                  13편에서 배운 Job
  C. 앱이 기동 시 잠금을 잡고 실행           복잡하다
```

```text
[지금 정하는 것]
  스키마 적용을 앱 코드에 넣지 않는다
  → SQL 파일로 분리해둔다
  → 4단계에서 Job 으로 실행하는 방법을 실습한다
```

**13편에서 만든 Job 이 여기서 쓰인다.**

---

## 6. DB 사용자와 권한

```text
[만들 것]
  books_app    앱이 쓰는 계정. 테이블 읽기·쓰기만
  books_owner  스키마를 만드는 계정. 마이그레이션용
```

```text
[왜 나누나]
  앱 계정으로 DROP TABLE 이 되면 안 된다
  → 08편에서 본 최소 권한 원칙과 같은 발상이다
```

```text
[10단계로 이어진다]
  RDS 에서는 IAM 인증도 쓸 수 있다
  → 비밀번호 없이 IAM 역할로 붙는다
  → 지금은 비밀번호. 계정을 나눠두면 나중에 옮기기 쉽다
```

```text
[3단계에서 어디까지 하나]
  계정을 나누는 것까지만 한다
  비밀번호는 환경변수로 주입한다 (02 문서)
  → 4단계에서 Secret 으로 옮긴다
```

---

## 7. 스키마 SQL

```sql
-- 01_schema.sql

CREATE TABLE IF NOT EXISTS books (
    id     BIGSERIAL PRIMARY KEY,
    title  TEXT      NOT NULL,
    price  INTEGER   NOT NULL,
    stock  INTEGER   NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         BIGINT      NOT NULL,
    book_id         BIGINT      NOT NULL REFERENCES books(id),
    quantity        INTEGER     NOT NULL,
    unit_price      INTEGER     NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    failure_reason  TEXT,

    CONSTRAINT orders_status_valid
        CHECK (status IN ('pending','processing','completed','failed')),
    CONSTRAINT orders_quantity_positive
        CHECK (quantity > 0)
);
```

```sql
-- 나중에 추가할 것들 — 지금은 실행하지 않는다
--
-- 재고 음수 방지 (음수를 관찰한 뒤에)
-- ALTER TABLE books ADD CONSTRAINT books_stock_non_negative CHECK (stock >= 0);
--
-- 인덱스 (느린 걸 측정한 뒤에)
-- CREATE INDEX idx_orders_user_created  ON orders (user_id, created_at DESC);
-- CREATE INDEX idx_orders_status_created ON orders (status, created_at);
-- CREATE INDEX idx_orders_book          ON orders (book_id);
```

**주석으로 남겨둔다.** 나중에 추가할 때 "왜 지금 넣는가" 를 문서와 함께 설명할 수 있다.

---

## 8. 주문 처리 SQL — 경로 2

01 문서의 처리 순서를 SQL 로 옮기면 이렇다.

### 1차 — 잠금 없이 (의도적으로)

```sql
BEGIN;

SELECT id, price, stock FROM books WHERE id = $1;
-- 앱에서 stock >= quantity 를 확인한다

UPDATE books SET stock = stock - $2 WHERE id = $1;

INSERT INTO orders (user_id, book_id, quantity, unit_price)
VALUES ($3, $1, $2, $4)
RETURNING id;

-- 여기서 Redis 큐에 넣는다 (01 문서 판단)

COMMIT;
```

```text
[문제가 생기는 지점]
  두 요청이 동시에 SELECT 하면 둘 다 stock=1 을 본다
  둘 다 "1권 있으니 팔아도 된다" 고 판단한다
  둘 다 UPDATE 한다 → stock = -1
```

### 2차 — 행 잠금 (문제를 본 뒤에)

```sql
SELECT id, price, stock FROM books WHERE id = $1 FOR UPDATE;
```

```text
FOR UPDATE 를 붙이면
  → 첫 요청이 그 행을 잠근다
  → 두 번째 요청은 커밋될 때까지 기다린다
  → 순서대로 처리된다
```

```text
[대가]
  같은 책 주문이 직렬화된다 → 처리량이 떨어진다
  → 얼마나 떨어지는지 5·6단계에서 잰다
```

### 3차 — 조건부 UPDATE (더 나은 방법)

```sql
UPDATE books
   SET stock = stock - $2
 WHERE id = $1 AND stock >= $2
RETURNING stock;
```

```text
갱신된 행이 0개면 재고가 부족했던 것이다
→ SELECT 없이 한 번의 UPDATE 로 판단과 차감을 동시에 한다
→ FOR UPDATE 보다 잠금 구간이 짧다
```

**세 방식을 순서대로 겪고 지표로 비교하는 것이 이 앱의 학습 목표 중 하나다.**

---

## 정리 — 이 문서에서 내린 판단

```text
 1. 금액은 INTEGER (원 단위). 부동소수점은 절대 안 쓴다
 2. 재고를 books 에 둔다. hot row 병목을 알면서도 고른다 ★
    그 병목 자체가 6단계 실험 재료다
 3. 재고 음수 방지 CHECK 를 지금은 안 건다 ★
    음수가 되는 걸 관찰한 뒤에 건다
 4. 시각은 전부 TIMESTAMPTZ. 환경마다 다르게 해석되면 안 된다 ★
 5. 시각을 셋 나눠 담는다 (created / started / finished)
    큐 대기 시간과 실제 처리 시간을 분리해서 재려고
 6. unit_price 를 저장한다. 책값이 바뀌어도 과거 주문은 그대로여야 한다
    total_price 는 계산한다 (할인이 없으므로 중복 저장할 이유가 없다)
 7. status 는 TEXT + CHECK. ENUM 은 변경이 까다롭다
    상태 전이를 DB 로 강제하지 않는다 — 잘못된 전이도 관찰 대상이다
 8. book_id 에 외래 키를 건다. user_id 는 users 테이블이 없어 안 건다
 9. 인덱스를 지금은 안 만든다 ★
    느려지는 걸 측정한 뒤에 추가하고 효과를 기록한다
10. 스키마 적용을 앱 코드에 넣지 않는다
    Pod 여러 개가 동시에 만들려 하는 문제 → 4단계에서 Job 으로
11. DB 계정을 앱용과 마이그레이션용으로 나눈다 (최소 권한)
12. 주문 SQL 을 세 번에 걸쳐 발전시킨다
    잠금 없음 → FOR UPDATE → 조건부 UPDATE
    각 단계의 처리량을 비교한다
```

## 다음

```text
04-health-check.md   live 와 ready 를 무엇으로 판단할 것인가
                     → 04편의 "probe 는 성공하는데 실제로는 503" 을 안 만들려면
                     → DB 가 죽었을 때 ready 를 실패시켜야 하는가?
                       그러면 Pod 가 전부 빠져 서비스가 통째로 멈춘다
                       그게 맞는가?
```

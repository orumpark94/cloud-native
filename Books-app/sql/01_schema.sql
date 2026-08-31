-- 서점 APP 스키마
--
-- 03-data-model.md 의 판단을 그대로 옮긴 것이다.
-- 여기서 "안 만든 것" 이 만든 것만큼 중요하다.
--
--   재고 음수 방지 CHECK   안 건다 → 음수가 되는 걸 관찰한 뒤에 건다
--   인덱스                 안 만든다 → 느려지는 걸 측정한 뒤에 만든다
--
--   둘 다 처음부터 넣으면 "덕분에 좋아졌다" 를 확인할 수 없다
--
--
-- 누가 이 파일을 실행하는가                              ★ 03 문서
--
--   [3단계 — Compose]
--     postgres 이미지의 /docker-entrypoint-initdb.d/ 에 넣는다
--     → 컨테이너가 처음 뜰 때 한 번만 실행된다
--     → 볼륨에 데이터가 이미 있으면 실행되지 않는다
--
--   [4단계 — Kubernetes]
--     API Pod 가 3개다. 각자 기동하며 스키마를 만들면
--     → 셋이 동시에 CREATE TABLE → 하나만 성공, 둘은 에러
--     → 앱이 스키마를 만들면 안 된다는 뜻이다
--     → 별도 Job 이나 initContainer 가 한 번만 실행한다
--
--   그래서 앱 코드에는 CREATE TABLE 이 한 줄도 없다.
--   IF NOT EXISTS 를 붙이는 건 그래도 두 번 실행될 여지를 막기 위한 것이다.

-- ─────────────────────────────────────────────────────────────
-- books
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS books (
    id     BIGSERIAL PRIMARY KEY,
    title  TEXT      NOT NULL,
    -- 금액은 INTEGER, 원 단위다                          ★ 03 문서
    --   부동소수점(FLOAT/REAL)을 쓰지 않는다
    --   0.1 + 0.2 != 0.3 이 되는 타입으로 돈을 다루면 안 된다
    --   원 단위 정수면 소수점 자체가 없다
    price  INTEGER   NOT NULL,
    -- 재고를 books 에 둔다. hot row 병목이 생기는 걸 알면서 고른 것이다
    --   인기 있는 책 하나에 주문이 몰리면 그 행이 병목이 된다
    --   → 그 병목 자체가 6단계 실험 재료다
    stock  INTEGER   NOT NULL
);

-- ─────────────────────────────────────────────────────────────
-- orders
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         BIGINT      NOT NULL,
    book_id         BIGINT      NOT NULL REFERENCES books(id),
    quantity        INTEGER     NOT NULL,
    -- 주문 시점의 가격을 박아둔다                         ★ 03 문서
    --   나중에 책값이 바뀌어도 과거 주문 금액이 바뀌면 안 된다
    --   → 영수증이 나중에 바뀌는 셈이 된다
    --   total_price 는 저장하지 않는다. 앱에서 곱해서 준다
    unit_price      INTEGER     NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',

    -- 시각을 셋으로 나눈다                                ★★ 03·05 문서
    --   created  → started   큐에서 기다린 시간
    --   started  → finished  실제 처리 시간
    --
    --   하나로 뭉치면 "5초 걸렸다" 만 안다
    --   큐에서 4.9초 기다린 건지, 처리가 4.9초 걸린 건지 모른다
    --   → 대응이 정반대다 (Worker 를 늘린다 / 로직을 고친다)
    --
    -- 전부 TIMESTAMPTZ 다. TIMESTAMP 가 아니다
    --   TIMESTAMP 는 "몇 시" 만 담는다. 어느 시간대인지 모른다
    --   VM 은 KST, EKS 는 UTC → 같은 값이 다르게 해석된다
    --   → TIMESTAMPTZ 는 시점을 담는다. 해석이 흔들리지 않는다
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    failure_reason  TEXT,

    -- status 는 TEXT + CHECK 다. ENUM 이 아니다
    --   ENUM 은 값 추가에 ALTER TYPE 이 필요해 스키마 변경 실습이 복잡해진다
    --   4단계에서 "스키마 변경을 어떻게 배포하는가" 를 다룰 예정이다
    CONSTRAINT orders_status_valid
        CHECK (status IN ('pending','processing','completed','failed')),
    CONSTRAINT orders_quantity_positive
        CHECK (quantity > 0)
);

-- ★ 상태 전이를 DB 로 강제하지 않는다                     (03 문서)
--   completed 인 주문이 다시 processing 이 되는 것을 막지 않았다
--   → 그런 일이 실제로 일어나는지 관찰하려는 것이다
--   → 막아버리면 Worker 의 중복 처리 문제를 발견할 수 없다
--   앱에서 UPDATE ... WHERE status='pending' 으로 막는다 (repositories/orders.py)

-- ─────────────────────────────────────────────────────────────
-- 초기 데이터 — 책 1,000권
--
-- generate_series 로 만든다
--   INSERT 를 1000줄 쓰지 않는다. 파일이 길어지고 수정이 어렵다
--
-- 재고를 일부러 불균등하게 준다                           ★
--   전부 100권이면 특정 책에 주문이 몰리는 상황을 못 만든다
--   id % 7 = 0 인 책은 재고를 5권만 둔다
--   → 부하 테스트에서 그 책들이 먼저 품절된다 → 409 를 관찰한다
--   → id % 97 = 0 인 책은 1권 → 동시 주문으로 음수를 만들기 쉽다
-- ─────────────────────────────────────────────────────────────

INSERT INTO books (title, price, stock)
SELECT
    '테스트 도서 ' || i,
    -- 가격도 흩뜨린다. 전부 같으면 total_price 검산이 무의미하다
    8000 + (i % 40) * 500,
    CASE
        WHEN i % 97 = 0 THEN 1        -- 동시성 실험용 (음수 만들기 쉬움)
        WHEN i % 7  = 0 THEN 5        -- 품절 실험용
        ELSE 100
    END
FROM generate_series(1, 1000) AS i
-- 이미 데이터가 있으면 넣지 않는다
--   initdb 스크립트는 한 번만 돌지만, 손으로 다시 실행할 수 있다
WHERE NOT EXISTS (SELECT 1 FROM books);

-- ─────────────────────────────────────────────────────────────
-- 나중에 추가할 것들 — 지금은 실행하지 않는다             ★★
--
-- 주석으로 남겨두는 이유
--   나중에 추가할 때 "왜 지금 넣는가" 를 문서와 함께 설명할 수 있다
--   그냥 없으면 "빠뜨린 것" 인지 "일부러 뺀 것" 인지 구분이 안 된다
-- ─────────────────────────────────────────────────────────────

-- [1] 재고 음수 방지 — 음수를 실제로 관찰한 뒤에 건다
--
--   지금 걸면 STOCK_STRATEGY=none 실험이 불가능하다
--   DB 가 막아버리면 "앱에 잠금이 없어서 음수가 된다" 를 볼 수 없다
--   books_stock_negative_total 이 0보다 커지는 걸 확인한 뒤 건다
--
-- ALTER TABLE books ADD CONSTRAINT books_stock_non_negative CHECK (stock >= 0);

-- [2] 인덱스 — 느린 것을 측정한 뒤에 만든다
--
--   GET /orders 는 지금 user_id 로 풀 스캔한다
--   데이터를 늘려 EXPLAIN 으로 Seq Scan 을 확인하고
--   인덱스를 넣은 뒤 몇 배 빨라졌는지 기록한다 (5단계)
--
-- CREATE INDEX idx_orders_user_created   ON orders (user_id, created_at DESC);
-- CREATE INDEX idx_orders_status_created ON orders (status, created_at);
-- CREATE INDEX idx_orders_book           ON orders (book_id);

-- [3] 갇힌 주문 찾기 — 5단계 정합성 검증 Job 이 쓸 쿼리
--
--   worker.py 가 result="stuck" 으로 세는 그 상황을 DB 에서 확인한다
--
-- SELECT id, started_at FROM orders
--  WHERE status = 'processing' AND started_at < now() - INTERVAL '5 minutes';

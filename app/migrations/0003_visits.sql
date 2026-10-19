-- 伝票（1組のお客様の来店）を追加する。1つの伝票に複数回の注文（追加注文）をまとめ、会計は伝票単位で行う。
-- 既存の注文は、1注文につき1伝票として引き継ぐ。

CREATE TABLE visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id        INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    visit_number    TEXT    NOT NULL,
    table_label     TEXT    NOT NULL DEFAULT '' CHECK (length(table_label) <= 20),
    party_size      INTEGER NOT NULL CHECK (typeof(party_size) = 'integer' AND party_size BETWEEN 1 AND 1000),
    payment_status  TEXT    NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'checked', 'paid')),
    opened_at       TEXT    NOT NULL,
    checked_at      TEXT,
    paid_at         TEXT,
    paid_amount     INTEGER CHECK (paid_amount IS NULL OR paid_amount >= 0),
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    legacy_order_id INTEGER,
    UNIQUE (store_id, visit_number),
    UNIQUE (id, store_id)
);

CREATE INDEX idx_visits_store_opened_at ON visits(store_id, opened_at);

INSERT INTO visits (store_id, visit_number, table_label, party_size, payment_status, opened_at, checked_at, paid_at,
                    paid_amount, created_at, updated_at, legacy_order_id)
SELECT store_id,
       'V' || substr(order_number, 1, 8) || '-' ||
           printf('%03d', ROW_NUMBER() OVER (PARTITION BY store_id, substr(order_number, 1, 8) ORDER BY id)),
       '', party_size,
       CASE payment_status WHEN 'paid' THEN 'paid' WHEN 'checked' THEN 'checked' ELSE 'unpaid' END,
       ordered_at, checked_at, paid_at,
       CASE WHEN payment_status = 'paid' THEN total_amount END,
       created_at, updated_at, id
FROM orders;

ALTER TABLE orders ADD COLUMN visit_id INTEGER REFERENCES visits(id);

UPDATE orders SET visit_id = (SELECT v.id FROM visits v WHERE v.legacy_order_id = orders.id);

ALTER TABLE visits DROP COLUMN legacy_order_id;

CREATE INDEX idx_orders_visit ON orders(visit_id);

-- 注文と伝票は同じ店舗でなければならない（ALTER TABLE では複合外部キーを足せないためトリガーで保証する）。
CREATE TRIGGER trg_orders_visit_same_store
BEFORE INSERT ON orders
WHEN NEW.visit_id IS NOT NULL
 AND (SELECT store_id FROM visits WHERE id = NEW.visit_id) IS NOT NEW.store_id
BEGIN
    SELECT RAISE(ABORT, 'orders: visit belongs to another store');
END;

CREATE TRIGGER trg_orders_visit_immutable
BEFORE UPDATE OF visit_id ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders: visit cannot be changed');
END;

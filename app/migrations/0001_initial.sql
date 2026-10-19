-- 初期スキーマ。日時はすべて日本時間の ISO 8601 文字列（例: 2026-10-19T12:34:56+09:00）で保存する。
-- 金額はすべて円単位の整数。

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 30),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE products (
    id                  TEXT    PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 32),
    category_id         INTEGER NOT NULL REFERENCES categories(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    name                TEXT    NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 60),
    description         TEXT    NOT NULL DEFAULT '' CHECK (length(description) <= 200),
    price               INTEGER NOT NULL CHECK (typeof(price) = 'integer' AND price BETWEEN 0 AND 1000000),
    image_path          TEXT    NOT NULL DEFAULT '',
    is_available        INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
    popularity_score    INTEGER NOT NULL DEFAULT 0 CHECK (typeof(popularity_score) = 'integer' AND popularity_score BETWEEN 0 AND 100),
    recommendation_tags TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE INDEX idx_products_category ON products(category_id);

CREATE TABLE orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number        TEXT    NOT NULL UNIQUE,
    request_key         TEXT    NOT NULL UNIQUE,
    request_fingerprint TEXT    NOT NULL,
    ordered_at          TEXT    NOT NULL,
    party_size          INTEGER NOT NULL CHECK (typeof(party_size) = 'integer' AND party_size BETWEEN 1 AND 1000),
    total_amount        INTEGER NOT NULL CHECK (typeof(total_amount) = 'integer' AND total_amount >= 0),
    order_status        TEXT    NOT NULL DEFAULT 'received'
                        CHECK (order_status IN ('received', 'cooking', 'served', 'cancelled')),
    payment_status      TEXT    NOT NULL DEFAULT 'unpaid'
                        CHECK (payment_status IN ('unpaid', 'checked', 'paid', 'cancelled')),
    checked_at          TEXT,
    paid_at             TEXT,
    cancelled_at        TEXT,
    cancel_reason       TEXT,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);

CREATE INDEX idx_orders_ordered_at ON orders(ordered_at);
CREATE INDEX idx_orders_status ON orders(order_status, payment_status);

CREATE TABLE order_items (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id              INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    product_id            TEXT    NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name_snapshot TEXT    NOT NULL,
    unit_price            INTEGER NOT NULL CHECK (typeof(unit_price) = 'integer' AND unit_price >= 0),
    quantity              INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity BETWEEN 1 AND 99),
    subtotal              INTEGER NOT NULL,
    CHECK (subtotal = unit_price * quantity),
    UNIQUE (order_id, product_id)
);

CREATE INDEX idx_order_items_order ON order_items(order_id);

CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    detail      TEXT
);

CREATE INDEX idx_audit_logs_target ON audit_logs(target_type, target_id);
CREATE INDEX idx_audit_logs_occurred_at ON audit_logs(occurred_at);

-- 確定済み注文の金額・人数・番号は後から変更させない。
CREATE TRIGGER trg_orders_immutable
BEFORE UPDATE OF order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders: confirmed order values are immutable');
END;

-- 注文明細は注文時のスナップショットなので更新・削除させない。
CREATE TRIGGER trg_order_items_no_update
BEFORE UPDATE ON order_items
BEGIN
    SELECT RAISE(ABORT, 'order_items: rows are immutable');
END;

CREATE TRIGGER trg_order_items_no_delete
BEFORE DELETE ON order_items
BEGIN
    SELECT RAISE(ABORT, 'order_items: rows are immutable');
END;

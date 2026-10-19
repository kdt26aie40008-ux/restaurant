-- 複数店舗（店舗アカウント）と運営者アカウント、メール認証コードに対応する。
-- 既存の単一店舗データがある場合は「店舗ID 1（メール未設定）」として引き継ぐ。
-- このマイグレーションは外部キー検査を一時停止した状態で実行され、最後に整合性を検査する（app/db.py）。

CREATE TABLE stores (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    email              TEXT    UNIQUE COLLATE NOCASE CHECK (email IS NULL OR length(email) BETWEEN 3 AND 254),
    password_hash      TEXT,
    status             TEXT    NOT NULL CHECK (status IN ('unclaimed', 'invited', 'active', 'suspended')),
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       INTEGER,
    password_changed_at TEXT,
    last_login_at      TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    CHECK (status = 'unclaimed' OR email IS NOT NULL),
    CHECK (status != 'active' OR password_hash IS NOT NULL)
);

CREATE TABLE operators (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    email              TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(email) BETWEEN 3 AND 254),
    password_hash      TEXT    NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       INTEGER,
    last_login_at      TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);

-- 認証コードは平文で保存せず、data/ 外の秘密鍵による HMAC だけを保存する。
CREATE TABLE verification_codes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_type   TEXT    NOT NULL CHECK (account_type IN ('store', 'operator')),
    account_id     INTEGER NOT NULL,
    purpose        TEXT    NOT NULL CHECK (purpose IN ('store_password', 'operator_login')),
    code_hash      TEXT    NOT NULL,
    challenge_hash TEXT    UNIQUE,
    attempts       INTEGER NOT NULL DEFAULT 0,
    expires_at     INTEGER NOT NULL,
    verified_at    INTEGER,
    consumed_at    INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE INDEX idx_verification_codes_account ON verification_codes(account_type, account_id, purpose, id);

INSERT INTO stores (id, name, email, password_hash, status, created_at, updated_at)
SELECT 1,
       COALESCE((SELECT substr(value, 1, 40) FROM settings WHERE key = 'store_name'), '既存の店舗'),
       NULL, NULL, 'unclaimed',
       strftime('%Y-%m-%dT%H:%M:%S', 'now', '+9 hours') || '+09:00',
       strftime('%Y-%m-%dT%H:%M:%S', 'now', '+9 hours') || '+09:00'
WHERE EXISTS (SELECT 1 FROM categories) OR EXISTS (SELECT 1 FROM orders);

CREATE TABLE categories_v2 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id    INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 30),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (store_id, name),
    UNIQUE (id, store_id)
);

INSERT INTO categories_v2 (id, store_id, name, sort_order, is_active, created_at, updated_at)
SELECT id, 1, name, sort_order, is_active, created_at, updated_at FROM categories;

CREATE TABLE products_v2 (
    store_id            INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    id                  TEXT    NOT NULL CHECK (length(id) BETWEEN 1 AND 32),
    category_id         INTEGER NOT NULL,
    name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    description         TEXT    NOT NULL DEFAULT '' CHECK (length(description) <= 200),
    price               INTEGER NOT NULL CHECK (typeof(price) = 'integer' AND price BETWEEN 0 AND 1000000),
    image_path          TEXT    NOT NULL DEFAULT '',
    is_available        INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
    popularity_score    INTEGER NOT NULL DEFAULT 0 CHECK (typeof(popularity_score) = 'integer' AND popularity_score BETWEEN 0 AND 100),
    recommendation_tags TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (store_id, id),
    UNIQUE (store_id, name),
    FOREIGN KEY (category_id, store_id) REFERENCES categories(id, store_id) ON DELETE RESTRICT
);

INSERT INTO products_v2 (store_id, id, category_id, name, description, price, image_path, is_available,
                         popularity_score, recommendation_tags, created_at, updated_at)
SELECT 1, id, category_id, name, description, price, image_path, is_available,
       popularity_score, recommendation_tags, created_at, updated_at FROM products;

CREATE TABLE orders_v2 (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id            INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    order_number        TEXT    NOT NULL,
    request_key         TEXT    NOT NULL,
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
    updated_at          TEXT    NOT NULL,
    UNIQUE (store_id, order_number),
    UNIQUE (store_id, request_key),
    UNIQUE (id, store_id)
);

INSERT INTO orders_v2 (id, store_id, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount,
                       order_status, payment_status, checked_at, paid_at, cancelled_at, cancel_reason, created_at, updated_at)
SELECT id, 1, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount,
       order_status, payment_status, checked_at, paid_at, cancelled_at, cancel_reason, created_at, updated_at FROM orders;

CREATE TABLE order_items_v2 (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id              INTEGER NOT NULL,
    store_id              INTEGER NOT NULL,
    product_id            TEXT    NOT NULL,
    product_name_snapshot TEXT    NOT NULL,
    unit_price            INTEGER NOT NULL CHECK (typeof(unit_price) = 'integer' AND unit_price >= 0),
    quantity              INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity BETWEEN 1 AND 99),
    subtotal              INTEGER NOT NULL,
    CHECK (subtotal = unit_price * quantity),
    UNIQUE (order_id, product_id),
    FOREIGN KEY (order_id, store_id) REFERENCES orders(id, store_id) ON DELETE RESTRICT,
    FOREIGN KEY (store_id, product_id) REFERENCES products(store_id, id) ON DELETE RESTRICT
);

INSERT INTO order_items_v2 (id, order_id, store_id, product_id, product_name_snapshot, unit_price, quantity, subtotal)
SELECT id, order_id, 1, product_id, product_name_snapshot, unit_price, quantity, subtotal FROM order_items;

CREATE TABLE store_settings (
    store_id   INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    updated_at TEXT    NOT NULL,
    PRIMARY KEY (store_id, key)
);

INSERT INTO store_settings (store_id, key, value, updated_at)
SELECT 1, key, value, updated_at FROM settings
WHERE key IN ('max_party_size', 'csv_bom_default') AND EXISTS (SELECT 1 FROM stores WHERE id = 1);

-- 旧版の単一パスワードは使わなくなるため削除する（残しておくと古い認証情報が残り続ける）。
DELETE FROM settings WHERE key IN ('store_name', 'max_party_size', 'csv_bom_default', 'admin_password_hash', 'seed_completed_at');

DROP TABLE order_items;
DROP TABLE orders;
DROP TABLE products;
DROP TABLE categories;

ALTER TABLE categories_v2 RENAME TO categories;
ALTER TABLE products_v2 RENAME TO products;
ALTER TABLE orders_v2 RENAME TO orders;
ALTER TABLE order_items_v2 RENAME TO order_items;

CREATE INDEX idx_categories_store ON categories(store_id, sort_order, id);
CREATE INDEX idx_products_category ON products(store_id, category_id);
CREATE INDEX idx_orders_store_ordered_at ON orders(store_id, ordered_at);
CREATE INDEX idx_orders_status ON orders(store_id, order_status, payment_status);
CREATE INDEX idx_order_items_order ON order_items(order_id);

ALTER TABLE audit_logs ADD COLUMN store_id INTEGER;
UPDATE audit_logs SET store_id = 1 WHERE EXISTS (SELECT 1 FROM stores WHERE id = 1);
CREATE INDEX idx_audit_logs_store ON audit_logs(store_id, id);

CREATE TRIGGER trg_orders_immutable
BEFORE UPDATE OF store_id, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount ON orders
BEGIN
    SELECT RAISE(ABORT, 'orders: confirmed order values are immutable');
END;

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

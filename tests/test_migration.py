"""旧版（単一店舗）の DB から複数店舗版への移行テスト。"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.db import connect, migrate, transaction
from app.timeutil import now_iso


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="restaurant-migration-")
        self.conn = connect(Path(self.tmp) / "old.db")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_store_data_moves_to_store_1(self):
        migrate(self.conn, target_version="0001")
        ts = now_iso()
        with transaction(self.conn):
            self.conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('store_name', '旧店舗', ?), "
                              "('max_party_size', '12', ?), ('admin_password_hash', 'pbkdf2_sha256$1$00$00', ?)", (ts, ts, ts))
            self.conn.execute("INSERT INTO categories (id, name, sort_order, is_active, created_at, updated_at) VALUES (1, 'フード', 1, 1, ?, ?)", (ts, ts))
            self.conn.execute("INSERT INTO products (id, category_id, name, price, created_at, updated_at) VALUES ('P001', 1, 'ハンバーガー', 680, ?, ?)", (ts, ts))
            self.conn.execute("INSERT INTO orders (id, order_number, request_key, request_fingerprint, ordered_at, party_size, total_amount, "
                              "created_at, updated_at) VALUES (7, '20261019-0001', 'k1', 'f', ?, 2, 1360, ?, ?)", (ts, ts, ts))
            self.conn.execute("INSERT INTO order_items (order_id, product_id, product_name_snapshot, unit_price, quantity, subtotal) "
                              "VALUES (7, 'P001', 'ハンバーガー', 680, 2, 1360)")
            self.conn.execute("INSERT INTO audit_logs (occurred_at, actor, action) VALUES (?, 'staff', 'order.create')", (ts,))

        self.assertEqual(migrate(self.conn)[0], "0002_multi_store_accounts.sql")
        store = self.conn.execute("SELECT * FROM stores").fetchone()
        self.assertEqual((store["id"], store["name"], store["status"], store["email"]), (1, "旧店舗", "unclaimed", None))
        self.assertEqual(self.conn.execute("SELECT store_id, total_amount FROM orders WHERE id = 7").fetchone()[:], (1, 1360))
        self.assertEqual(self.conn.execute("SELECT store_id FROM order_items").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT store_id, price FROM products").fetchone()[:], (1, 680))
        self.assertEqual(self.conn.execute("SELECT value FROM store_settings WHERE store_id = 1 AND key = 'max_party_size'").fetchone()[0], "12")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM settings WHERE key = 'admin_password_hash'").fetchone())
        self.assertEqual(self.conn.execute("SELECT store_id FROM audit_logs").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE orders SET total_amount = 1 WHERE id = 7")  # 不変トリガーも引き継がれている
        self.assertEqual(migrate(self.conn), [])

    def test_fresh_database_has_no_stores(self):
        migrate(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

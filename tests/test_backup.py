"""バックアップと復元のテスト（仕様のテスト項目 23 に対応）。"""

from pathlib import Path

from app import backup, orders
from app.db import connect
from app.errors import AppError
from tests.helpers import DbTestCase, order_payload


class BackupTest(DbTestCase):
    def setUp(self):
        super().setUp()
        self.backup_dir = Path(self.tmp) / "backups"

    def test_backup_and_restore_round_trip(self):
        first, _ = orders.create_order(self.conn, self.store_id, order_payload([("P001", 1)]), 30)
        path = backup.create_backup(self.config.db_path, self.backup_dir)
        self.assertTrue(path.exists())
        self.assertEqual(backup.verify_database_file(path), (True, ""))

        orders.create_order(self.conn, self.store_id, order_payload([("P002", 1)]), 30)
        self.assertEqual(self.count("orders"), 2)

        result = backup.restore_backup(self.config.db_path, self.backup_dir, path.name)
        self.conn.close()
        self.conn = connect(self.config.db_path)
        self.assertEqual(self.count("orders"), 1)
        self.assertEqual(self.conn.execute("SELECT order_number FROM orders").fetchone()[0], first["id"])

        # 復元前のデータも自動で保存されている（注文2件の状態）
        safety = self.backup_dir / result["safety_copy"]
        self.assertTrue(safety.name.endswith("-before-restore.db"))
        safety_conn = connect(safety)
        self.assertEqual(safety_conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 2)
        safety_conn.close()
        self.assertEqual([b["filename"] for b in backup.list_backups(self.backup_dir)].count(path.name), 1)

    def test_restore_rejects_unsafe_names_and_invalid_files(self):
        for name in ["../restaurant.db", "restaurant.db", "C:\\Windows\\x.db", "restaurant-20260101-000000.db"]:
            with self.subTest(name=name):
                with self.assertRaises(AppError):
                    backup.restore_backup(self.config.db_path, self.backup_dir, name)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        broken = self.backup_dir / "restaurant-20260101-000000.db"
        broken.write_bytes(b"this is not sqlite")
        with self.assertRaises(AppError) as ctx:
            backup.restore_backup(self.config.db_path, self.backup_dir, broken.name)
        self.assertEqual(ctx.exception.code, "BACKUP_INVALID")
        self.assertEqual(self.count("products"), 72)  # 現在の DB は変更されていない（2店舗 × 36商品）

    def test_backup_dir_inside_public_folder_is_rejected(self):
        from app.config import STATIC_DIR

        with self.assertRaises(AppError):
            backup.check_backup_dir_allowed(str(STATIC_DIR / "backups"))
        with self.assertRaises(AppError):
            backup.check_backup_dir_allowed("relative/path")

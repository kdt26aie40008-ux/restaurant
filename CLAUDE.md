# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

`restaurant` — 飲食関係の悩み解決を目的としたプロジェクト。現在は飲食店向けのタブレット用メニューオーダーシステム（仮版）。1台のサーバーに複数店舗を登録し、運営者サイトで店舗を管理する。仕様・仮仕様・セキュリティ対策・制限事項は README.md にまとめてある。

ユーザーへの応答は日本語で行うこと。

## コマンド（Windows では `python` が Store スタブのため `py` を使う）

```
py run.py                          # サーバー起動（/ 注文、/admin 店舗管理、/account パスワード設定、/operator 運営者）
py run.py create-operator          # 運営者アカウント作成・再設定（対話入力）
py run.py backup | list-backups | restore <file>
py -m unittest discover -s tests -t .                 # Python テスト一式
py -m unittest tests.test_orders.OrderTest.test_xxx   # 単体実行
node --test tests/js/logic.test.mjs                   # 画面ロジックのテスト（ディレクトリ指定は不可）
```

依存ライブラリはゼロ（Python 標準ライブラリ＋素の JS）。追加する場合は理由を README に書くこと。SMTP 未設定時は開発モードで認証コードを stderr に出す（ログファイルには書かない）。

## アーキテクチャ

- `app/server.py`: `ThreadingHTTPServer` で JSON API と `static/` を配信。ルートは `App._register_routes` に集約し、`auth=` で認証段階（public / store / store_admin / operator / operator_auth）を指定する。`_authorize` が Cookie セッションを検証して `request.store_id` / `operator_id` を設定する。GET 以外は `X-Requested-With: fetch` と Origin 一致が必須、Host は `config.allowed_hosts` で許可リスト制。
- **マルチテナント**: 店舗データ（categories / products / orders / order_items / store_settings / audit_logs）は `store_id` を持つ。業務関数はすべて `store_id` を受け取り、SQL で必ず絞り込むこと（他店舗のものは not found として扱う）。複合外部キーで DB 側でも店舗をまたぐ参照を拒否している。
- 注文は API・画面で店舗ごとの `order_number` を `id` として扱う（全店共通の内部 `orders.id` は外に出さない）。
- 認証: `app/accounts.py`（招待・認証コード・ログイン・ロック）、`app/auth.py`（PBKDF2・HMAC・セッション・回数制限）、`app/mailer.py`。認証コード・トークンは `conn.secret_key`（`data/secret.key`）の HMAC だけを保存する。セッションはメモリ上で、`credential_tag`（パスワードハッシュ由来）が DB と一致しないと無効。ログイン失敗の応答はアカウントの有無で変えないこと。
- 業務エラーは `AppError(status, code, message, hint)` を投げると `{"error": {...}}` で日本語表示される。500 系はエラー番号付きでログ出力し、詳細は画面に出さない。
- 書き込みは `db.transaction(conn)`（`BEGIN IMMEDIATE`、例外時ロールバック）の中で行い、重要操作は `write_audit(..., store_id=)` で記録する。
- 注文確定は画面が発行する `request_key` で冪等。価格・販売状態はサーバーで再取得し明細にスナップショット保存。`orders` の金額系列と `order_items` は DB トリガーで更新・削除禁止。会計・状態変更は「現在の状態が想定どおりなら UPDATE」で競合を防ぐ。
- スキーマ変更は `app/migrations/NNNN_name.sql` を追加（起動時に自動適用、適用中は外部キー検査を止め最後に `foreign_key_check`）。適用済みファイルは編集しない。
- 日時は日本時間固定オフセット `+09:00` の ISO 文字列（`app/timeutil.py`）。ロック・コード期限は UNIX 秒の整数。
- 画面: `static/js/logic.js` は DOM 非依存の純粋関数（node でテスト）。DOM は `dom.js` の `h()` で組み立て、`innerHTML` は使わない（CSP も `script-src 'self'`）。`Element.append` に配列を渡すと文字列化されるので展開すること。ログイン切れなどは `api.js` の `onApiError(code, handler)` で各画面が共通処理する。
- テスト: `tests/helpers.py` の `DbTestCase`（店舗 A・B を作成）、`ServerTestCase`（実サーバー＋`MemoryMailer`、店舗 A でログイン済み）を使う。テスト中は PBKDF2 の反復回数を下げている。

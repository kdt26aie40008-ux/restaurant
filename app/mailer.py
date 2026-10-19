"""認証コードのメール送信。

SMTP（Gmail のアプリパスワードなど無料で使えるもの）を環境変数で設定すると実際に送信する。
未設定のときは「開発モード」として、サーバーを動かしている画面（標準エラー出力）にだけ表示する。
認証コードはログファイルには書かない。
"""

import logging
import smtplib
import ssl
import sys
import threading
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger(__name__)


def mask_email(email):
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


class Mailer:
    def __init__(self, config):
        self.host = config.smtp_host
        self.port = config.smtp_port
        self.user = config.smtp_user
        self.password = config.smtp_password
        self.sender = config.mail_from or config.smtp_user
        self.security = config.smtp_security

    @property
    def configured(self):
        return bool(self.host and self.sender)

    def _build(self, to, subject, body):
        message = EmailMessage()
        message["From"] = formataddr(("メニューオーダー", self.sender or "no-reply@localhost"))
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid()
        message.set_content(body)
        return message

    def send(self, to, subject, body):
        """送信に失敗したら例外を投げる。"""
        if not self.configured:
            # 開発モード: 画面にだけ出す（ログファイルには残さない）。
            print(f"\n===== [開発モード] メール送信の代わりに表示 =====\n宛先: {to}\n件名: {subject}\n\n{body}\n"
                  "=================================================\n", file=sys.stderr, flush=True)
            return
        message = self._build(to, subject, body)
        context = ssl.create_default_context()
        if self.security == "ssl":
            with smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context) as smtp:
                self._login_and_send(smtp, message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)  # 暗号化できない場合は例外になり、平文では送らない
                smtp.ehlo()
                self._login_and_send(smtp, message)
        log.info("メールを送信しました: %s", mask_email(to))

    def _login_and_send(self, smtp, message):
        if self.user:
            smtp.login(self.user, self.password)
        smtp.send_message(message)

    def send_in_background(self, to, subject, body):
        """応答を待たせない（応答時間から登録の有無を推測されない）よう、別スレッドで送る。"""
        def run():
            try:
                self.send(to, subject, body)
            except (smtplib.SMTPException, OSError, ssl.SSLError):
                log.exception("メールの送信に失敗しました: %s", mask_email(to))

        threading.Thread(target=run, daemon=True).start()


class MemoryMailer(Mailer):
    """テスト用。送信内容をリストに貯める。"""

    def __init__(self):
        self.outbox = []
        self.fail = False

    @property
    def configured(self):
        return True

    def send(self, to, subject, body):
        if self.fail:
            raise smtplib.SMTPException("テスト用の送信失敗")
        self.outbox.append({"to": to, "subject": subject, "body": body})

    def send_in_background(self, to, subject, body):
        try:
            self.send(to, subject, body)
        except smtplib.SMTPException:
            log.exception("メールの送信に失敗しました: %s", mask_email(to))

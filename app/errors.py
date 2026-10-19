"""利用者に日本語で表示できる業務エラー。"""


class AppError(Exception):
    """message は「何が起きたか」、hint は「次に何をすればよいか」。"""

    def __init__(self, status, code, message, hint="", details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self):
        body = {"code": self.code, "message": self.message, "hint": self.hint}
        if self.details is not None:
            body["details"] = self.details
        return body


def bad_request(code, message, hint="", details=None):
    return AppError(400, code, message, hint, details)


def not_found(code, message, hint="", details=None):
    return AppError(404, code, message, hint, details)


def conflict(code, message, hint="", details=None):
    return AppError(409, code, message, hint, details)

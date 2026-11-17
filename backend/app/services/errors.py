"""Errors raised by the data layer and turned into clear HTTP responses in main.py."""


class DataUnavailable(Exception):
    """A processed data file or manifest needed for the request is missing or unreadable."""

    def __init__(self, file: str, reason: str = "file not found", hint: str | None = None):
        super().__init__(f"{file}: {reason}")
        self.file = file
        self.reason = reason
        self.hint = hint or "Run the data-processing stage that creates this file (see pipeline/README.md), then retry."


class NotFound(Exception):
    """A requested station or layer does not exist."""

    def __init__(self, message: str, available: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.available = available or []


class BadQuery(Exception):
    """A query parameter is syntactically valid but not acceptable (reported as HTTP 422)."""

    def __init__(self, message: str, allowed: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.allowed = allowed or []


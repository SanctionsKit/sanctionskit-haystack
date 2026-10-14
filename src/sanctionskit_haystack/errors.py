"""Errors never replace an unavailable result with a no-match decision."""


class SanctionsKitAPIError(RuntimeError):
    """An HTTP error, carrying safe machine-readable correlation fields."""

    def __init__(self, status_code: int, code: str, request_id: str | None = None):
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        super().__init__(f"SanctionsKit returned HTTP {status_code} ({code}).")


class SanctionsKitProtocolError(RuntimeError):
    """The response cannot safely be used as screening evidence."""


class SanctionsKitTransportError(RuntimeError):
    """Transport failed; a POST may still have reached the service."""

"""Typed errors. Callers branch on type, never on message strings."""


class AiArchError(Exception):
    """Base class for all errors raised by this package."""


class ProviderError(AiArchError):
    """An LLM/embedding provider failed. `retryable` drives the gateway's fallback logic."""

    def __init__(self, message: str, *, provider: str, retryable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class RateLimited(ProviderError):
    def __init__(self, message: str, *, provider: str, retry_after_s: float | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True)
        self.retry_after_s = retry_after_s


class AllProvidersFailed(AiArchError):
    """Every route in the fallback chain failed. The RAG service turns this into a DEGRADED answer."""


class BudgetExceeded(AiArchError):
    """A per-request or per-tenant token budget would be exceeded."""


class GuardrailViolation(AiArchError):
    def __init__(self, rule: str, detail: str = "") -> None:
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule


class ToolNotAllowed(AiArchError):
    """An agent tried to call a tool outside its allowlist."""

"""Project-wide exception types."""


class BoututlnError(Exception):
    """Base class for all project errors."""


class ConfigError(BoututlnError):
    """Invalid or missing configuration."""


class UnsafeEnvironmentError(ConfigError):
    """A dangerous environment variable combination was detected."""


class LiveTradingLockedError(BoututlnError):
    """Live trading was requested. It is not available in this build (P0)."""


class OrderEndpointBlockedError(BoututlnError):
    """An order API endpoint was about to be called. Never allowed in P0."""


class ExternalCallBlockedError(BoututlnError):
    """An outbound call to a host that is not on the allowlist."""


class PaidServiceBlockedError(BoututlnError):
    """A provider whose cost class is paid or unknown was used without approval."""


class QuotaExceededError(BoututlnError):
    """A free-tier quota was reached; calls stop until the window resets."""


class NetworkNotApprovedError(BoututlnError):
    """Real network access to a broker is not approved in this build."""

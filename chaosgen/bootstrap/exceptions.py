"""
Bootstrap exceptions for ChaosGen.

These provide structured, human-readable error messages instead of raw
FileNotFoundError / subprocess.CalledProcessError tracebacks.
"""


class PrerequisiteError(RuntimeError):
    """
    Raised when a required binary or system dependency is absent.
    Always includes the install URL so the user knows how to resolve it.
    """


class BootstrapError(RuntimeError):
    """
    Raised when an observability installation step fails after
    prerequisites have been confirmed present.
    """


class TelemetryNotReadyError(RuntimeError):
    """
    Raised by connection_verifier when telemetry endpoints are still
    unreachable after a bootstrap attempt, blocking experiment execution.
    """

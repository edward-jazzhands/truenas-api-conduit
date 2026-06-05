class ConduitError(Exception):
    """Base class for all errors raised by the conduit"""

    pass


class ProgrammerError(ConduitError, RuntimeError):
    """Raised when the programmer has made a mistake. Useful to differentiate from
    user/validation errors."""

    pass

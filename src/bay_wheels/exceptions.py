"""Custom exceptions for the Bay Wheels client."""


class BayWheelsError(Exception):
    """Base exception for Bay Wheels API errors."""

    pass


class AuthenticationError(BayWheelsError):
    """Raised when authentication fails."""

    pass


class ReservationError(BayWheelsError):
    """Raised when a reservation operation fails."""

    pass

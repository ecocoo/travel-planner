from backend.app.shared.exceptions import ApplicationError


class NotFoundError(ApplicationError):
    error_code = "not_found"
    status_code = 404


class InvalidRequestError(ApplicationError):
    error_code = "invalid_request"
    status_code = 422


class ConflictError(ApplicationError):
    error_code = "conflict"
    status_code = 409


class AuthenticationError(ApplicationError):
    error_code = "unauthorized"
    status_code = 401


class InvalidGuestTokenError(AuthenticationError):
    error_code = "invalid_guest_token"
    status_code = 401


class DatabaseUnavailableError(ApplicationError):
    error_code = "database_unavailable"
    status_code = 503

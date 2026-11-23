class ApplicationError(Exception):
    error_code: str
    status_code: int

    def __init__(self, details: str) -> None:
        super().__init__(details)
        self.details = details


class ModelUnavailableError(ApplicationError):
    error_code = "model_unavailable"
    status_code = 503


class InvalidModelResponseError(ApplicationError):
    error_code = "invalid_model_response"
    status_code = 502

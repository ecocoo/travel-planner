class ApplicationError(Exception):
    error_code: str
    status_code: int

    def __init__(self, details: str) -> None:
        super().__init__(details)
        self.details = details

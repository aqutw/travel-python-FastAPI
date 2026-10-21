class APIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def invalid(field: str, message: str):
    raise APIError(400, "invalid_query", f"{field}: {message}")


def not_found(resource: str):
    raise APIError(404, "not_found", f"{resource} not found")

"""A move the state machines refuse, which the routes turn into its status."""


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def conflict(detail: str) -> Refused:
    return Refused(409, detail)


def invalid(detail: str) -> Refused:
    return Refused(422, detail)


def not_found(detail: str) -> Refused:
    return Refused(404, detail)

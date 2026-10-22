"""Errors raised by job sources. Messages are shown to the user, so they are in Romanian."""


class SourceUnavailable(Exception):
    """The source cannot run right now (missing API key, not implemented yet)."""


class SourceError(RuntimeError):
    """A source ran but failed: HTTP error, block, robots.txt, unexpected page format."""


class FetchError(SourceError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class BlockedError(FetchError):
    """The site refused automated access (e.g. a 403 from Cloudflare). Never work around it."""


class DisallowedByRobots(FetchError):
    pass


class ParseError(SourceError):
    """The page no longer has the structure the parser expects."""

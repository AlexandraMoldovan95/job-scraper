"""Polite HTTP for direct scrapers: robots.txt, per-domain rate limit, disk cache, retry.

The rules come from CLAUDE.md ("Reguli de scraping"): one fixed, honest User-Agent,
at least `min_delay` seconds between requests to the same domain, robots.txt respected,
and a block (403) is reported, never worked around.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from protego import Protego

from jobscraper.sources.errors import BlockedError, DisallowedByRobots, FetchError

log = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 60.0


class DomainRateLimiter:
    """Keeps requests to one domain at least `delay` seconds apart, across all clients."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, domain: str, delay: float) -> None:
        with self._lock:
            last = self._last.get(domain)
            if last is not None:
                remaining = last + delay - self._clock()
                if remaining > 0:
                    self._sleep(remaining)
            self._last[domain] = self._clock()


# Shared by every client in the process, so two quick searches in the UI still wait.
_SHARED_LIMITER = DomainRateLimiter()


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str,
        min_delay: float,
        timeout: float = 20.0,
        cache_dir: Path | None = None,
        cache_ttl_hours: float = 12.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._min_delay = min_delay
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_hours * 3600
        self._max_retries = max_retries
        self._limiter = limiter or _SHARED_LIMITER
        self._robots: dict[str, Protego] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.5"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def get_text(self, url: str, *, use_cache: bool = True) -> str:
        """GET `url` and return the body. Raises FetchError (or a subclass) on failure."""
        if use_cache and (cached := self._read_cache(url)) is not None:
            return cached
        robots = self._robots_for(url)
        if not robots.can_fetch(url, self._user_agent):
            raise DisallowedByRobots(f"robots.txt nu permite accesul la {url}")
        body = self._request(url, delay=max(self._min_delay, robots.crawl_delay(self._user_agent) or 0))
        if use_cache:
            self._write_cache(url, body)
        return body

    # --- internals ---------------------------------------------------------------

    def _robots_for(self, url: str) -> Protego:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            text = self._read_cache(robots_url)
            if text is None:
                try:
                    text = self._request(robots_url, delay=self._min_delay)
                except BlockedError:
                    raise  # a site that blocks even robots.txt blocks us
                except FetchError as exc:
                    # No robots.txt (404 etc.) means everything is allowed; 429/5xx/network: stop.
                    if exc.status is None or not 400 <= exc.status < 500 or exc.status == 429:
                        raise
                    text = ""
                self._write_cache(robots_url, text)
            self._robots[origin] = Protego.parse(text)
        return self._robots[origin]

    def _request(self, url: str, *, delay: float) -> str:
        domain = urlsplit(url).netloc
        for attempt in range(self._max_retries + 1):
            self._limiter.wait(domain, delay)
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise FetchError(f"Nu am putut accesa {domain}: {exc}") from exc
                delay = self._backoff(attempt, None)  # measured from the failed request
                continue

            if response.is_success:
                return response.text
            if response.status_code in (401, 403):
                raise BlockedError(
                    f"{domain} a blocat accesul automat ({response.status_code}). "
                    "Nu ocolim blocajul; sursa trebuie dezactivată sau folosită prin altă cale.",
                    status=response.status_code,
                )
            if response.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                delay = self._backoff(attempt, response.headers.get("Retry-After"))
                log.warning("%s answered %s, retrying in %.0fs", domain, response.status_code, delay)
                continue
            raise FetchError(f"{domain} a răspuns cu {response.status_code} pentru {url}", status=response.status_code)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        wait = self._min_delay * 2 ** (attempt + 1)
        if retry_after:
            wait = max(wait, _parse_retry_after(retry_after))
        return min(wait, _MAX_BACKOFF_SECONDS)

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self._cache_ttl:
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, body: str) -> None:
        path = self._cache_path(url)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")


def _parse_retry_after(value: str) -> float:
    """Retry-After is either seconds or an HTTP date."""
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return 0.0

import os
import time

import httpx
import pytest

from jobscraper.sources.errors import BlockedError, DisallowedByRobots, FetchError
from jobscraper.sources.http import DomainRateLimiter, PoliteClient

# Same shape as ejobs.ro's robots.txt: wildcards and $ anchors
ROBOTS = """User-agent: *
Allow: */pagina2$
Disallow: */pagina
Disallow: */sort-publish
Crawl-delay: 5
"""


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def make_client(handler, tmp_path=None, fake_time=None, **kwargs) -> PoliteClient:
    fake_time = fake_time or FakeTime()
    return PoliteClient(
        user_agent="JobScraperPersonal/0.1",
        min_delay=2.5,
        cache_dir=tmp_path / "cache" if tmp_path else None,
        transport=httpx.MockTransport(handler),
        limiter=DomainRateLimiter(clock=fake_time.clock, sleep=fake_time.sleep),
        **kwargs,
    )


def site(pages: dict[str, httpx.Response], robots: str | int = ROBOTS, log: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(robots) if isinstance(robots, int) else httpx.Response(200, text=robots)
        return pages.get(request.url.path, httpx.Response(404))

    return handler


def test_robots_wildcards_and_dollar():
    client = make_client(site({"/locuri-de-munca/pagina2": httpx.Response(200, text="ok")}))
    assert client.get_text("https://x.ro/locuri-de-munca/pagina2") == "ok"
    with pytest.raises(DisallowedByRobots):
        client.get_text("https://x.ro/locuri-de-munca/pagina11")
    with pytest.raises(DisallowedByRobots):
        client.get_text("https://x.ro/locuri-de-munca/sort-publish")


def test_rate_limit_uses_crawl_delay_per_domain():
    fake_time = FakeTime()
    pages = {"/a": httpx.Response(200, text="a"), "/b": httpx.Response(200, text="b")}
    client = make_client(site(pages), fake_time=fake_time)

    client.get_text("https://x.ro/a")
    client.get_text("https://x.ro/b")

    # robots.txt first, then /a and /b each 5 s apart: the site's Crawl-delay beats our 2.5 s
    assert fake_time.sleeps == [5.0, 5.0]
    client.get_text("https://y.ro/a")  # another domain does not wait for x.ro
    assert fake_time.sleeps == [5.0, 5.0, 5.0]


def test_missing_robots_allows_everything():
    client = make_client(site({"/a": httpx.Response(200, text="a")}, robots=404))
    assert client.get_text("https://x.ro/a") == "a"


def test_blocked_robots_means_blocked_site():
    client = make_client(site({"/a": httpx.Response(200, text="a")}, robots=403))
    with pytest.raises(BlockedError, match="403"):
        client.get_text("https://x.ro/a")


def test_403_is_reported_not_retried():
    log: list[str] = []
    client = make_client(site({"/a": httpx.Response(403, text="Cloudflare")}, log=log))
    with pytest.raises(BlockedError, match="Nu ocolim"):
        client.get_text("https://x.ro/a")
    assert log == ["/robots.txt", "/a"]


def test_retries_5xx_with_backoff_and_retry_after():
    fake_time = FakeTime()
    answers = iter([httpx.Response(503, headers={"Retry-After": "12"}), httpx.Response(200, text="ok")])

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return next(answers)

    client = make_client(handler, fake_time=fake_time)
    assert client.get_text("https://x.ro/a") == "ok"
    assert fake_time.sleeps == [2.5, 12.0]


def test_gives_up_after_max_retries():
    client = make_client(site({"/a": httpx.Response(500)}), max_retries=1)
    with pytest.raises(FetchError, match="500"):
        client.get_text("https://x.ro/a")


def test_cache_serves_second_request(tmp_path):
    log: list[str] = []
    client = make_client(site({"/a": httpx.Response(200, text="Bucureşti")}, log=log), tmp_path)
    assert client.get_text("https://x.ro/a") == "Bucureşti"
    assert client.get_text("https://x.ro/a") == "Bucureşti"
    assert log == ["/robots.txt", "/a"]


def test_cache_can_be_skipped_and_expires(tmp_path):
    log: list[str] = []
    client = make_client(site({"/a": httpx.Response(200, text="a")}, log=log), tmp_path, cache_ttl_hours=1)
    client.get_text("https://x.ro/a")
    client.get_text("https://x.ro/a", use_cache=False)
    for path in (tmp_path / "cache").iterdir():
        old = time.time() - 2 * 3600
        os.utime(path, (old, old))
    client.get_text("https://x.ro/a")
    assert log == ["/robots.txt", "/a", "/a", "/a"]  # robots.txt stays parsed in memory

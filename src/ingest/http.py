"""Shared cached-GET helper for Gamma/CLOB pulls.

Retries transient failures (429/5xx, connection errors) with exponential
backoff via tenacity, paces between calls per API etiquette, and skips the
HTTP call entirely when the response is already cached on disk — every pull
built on top of this is resumable and safe to re-run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.ingest.dns_resolve import mount_dns_pinning

USER_AGENT = "pm-calibration/0.1 (research; github.com/angelomarano/pm-calibration)"
PACING_SECONDS = 0.25


class RetryableStatus(Exception):
    """Raised for 429 / 5xx responses so tenacity treats them as retryable.
    Carries the response body so callers (e.g. gamma_markets.py's gap log)
    can record what the server actually said, not just the status code."""

    def __init__(self, status_code: int, url: str, body: str = ""):
        self.status_code = status_code
        self.url = url
        self.body = body
        super().__init__(f"{status_code} from {url}: {body}")


def make_session(user_agent: str = USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    mount_dns_pinning(session)
    return session


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception_type(
        (requests.exceptions.ConnectionError, requests.exceptions.Timeout, RetryableStatus)
    ),
    reraise=True,
)
def _get(session: requests.Session, url: str, params) -> requests.Response:
    resp = session.get(url, params=params, timeout=30)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableStatus(resp.status_code, url, resp.text)
    resp.raise_for_status()
    return resp


def get_json_cached(session: requests.Session, url: str, params, cache_path: Path):
    """GETs `url` with `params`, caching the parsed JSON at `cache_path`.
    If `cache_path` already exists, returns its contents without any HTTP
    call."""
    if cache_path.exists():
        return json.loads(cache_path.read_text())

    resp = _get(session, url, params)
    time.sleep(PACING_SECONDS)
    data = resp.json()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data))
    return data

"""Helpers shared by all sources: polite HTTP with retries, and time parsing."""
import time
from datetime import datetime, timezone

import requests

USER_AGENT = "central-ohio-outage-tracker (newsroom outage monitoring project)"


class SourceError(Exception):
    """Raised when the source is down or its format changed."""


def get_json(session, url, retries=3, timeout=30):
    """GET JSON with exponential backoff (2s, 4s). Gives up after `retries`."""
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
    raise SourceError(f"{url}: {last}")


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s



def iso_utc(value):
    """Convert an ISO string with offset, or Unix milliseconds, to ISO 8601 UTC ('' if empty)."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / 1000, timezone.utc)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

"""In-memory sliding-window rate limits. Counts live in this process, so each API worker limits on its own."""

import time
from collections import deque

from fastapi import HTTPException, Request


class Limit:
    def __init__(self, count: int, window: int):
        self.count = count
        self.window = window
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str):
        """Counts a hit for key, or raises 429 if key is over its limit."""
        now = time.monotonic()
        hits = self.hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.count:
            retry = int(hits[0] + self.window - now) + 1
            raise HTTPException(
                status_code=429, detail="too many requests, try again later", headers={"Retry-After": str(retry)}
            )
        hits.append(now)
        if len(self.hits) > 10_000:
            self.hits = {k: v for k, v in self.hits.items() if v and v[-1] > now - self.window}

    def reset(self):
        self.hits.clear()


LOGIN_PER_IP = Limit(20, 60)
LOGIN_PER_EMAIL = Limit(10, 60)
SIGNUP_PER_IP = Limit(10, 60 * 60)
API_KEY = Limit(600, 60)
LIMITS = [LOGIN_PER_IP, LOGIN_PER_EMAIL, SIGNUP_PER_IP, API_KEY]


def client_ip(request: Request) -> str:
    # behind a reverse proxy this is the forwarded address only when uvicorn trusts the proxy (FORWARDED_ALLOW_IPS)
    return request.client.host if request.client else "unknown"

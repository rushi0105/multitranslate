"""Free translation providers. Each exposes translate(text, source, target) -> str and raises
ProviderError on failure. Order of preference is decided by the engine: Google -> Bing -> MyMemory."""
from __future__ import annotations

import re
import threading
import time

import requests

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 30


class ProviderError(RuntimeError):
    pass


class RateLimiter:
    """Global minimum gap between requests, shared by all worker threads."""

    def __init__(self, requests_per_second: float):
        self._gap = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next - now
            self._next = max(now, self._next) + self._gap
        if sleep_for > 0:
            time.sleep(sleep_for)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


class GoogleProvider:
    """Google's free web endpoints - no key, ~5 req/s, ~5000 chars/request, keeps newlines.

    Google rate-limits each endpoint per IP independently, so we rotate through three of them:
    when one answers 429/403 the provider moves to the next and stays there for the rest of the run."""
    name = "google"
    max_chars = 4000
    ENDPOINTS = (
        ("single", "https://translate.googleapis.com/translate_a/single", "gtx"),
        ("single", "https://translate.googleapis.com/translate_a/single", "dict-chrome-ex"),
        ("t", "https://clients5.google.com/translate_a/t", "dict-chrome-ex"),
    )

    def __init__(self, limiter: RateLimiter):
        self._limiter = limiter
        self._local = threading.local()
        self._lock = threading.Lock()
        self._endpoint = 0

    def _s(self) -> requests.Session:
        if not hasattr(self._local, "s"):
            self._local.s = _session()
        return self._local.s

    def _rotate(self, failed_index: int) -> None:
        with self._lock:
            if self._endpoint == failed_index:
                self._endpoint = (failed_index + 1) % len(self.ENDPOINTS)

    def translate(self, text: str, source: str, target: str) -> str:
        last: str = "no endpoint"
        for _ in range(len(self.ENDPOINTS)):
            index = self._endpoint
            kind, url, client = self.ENDPOINTS[index]
            self._limiter.wait()
            params = {"client": client, "sl": source, "tl": target, "q": text}
            if kind == "single":
                params["dt"] = "t"
            try:
                resp = self._s().get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                raise ProviderError(f"google network error: {exc}") from exc
            if resp.status_code in (429, 403):
                last = f"google endpoint {index + 1} blocked ({resp.status_code})"
                self._rotate(index)
                continue
            if resp.status_code != 200:
                raise ProviderError(f"google http {resp.status_code}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise ProviderError(f"google bad json: {exc}") from exc
            return self._extract(kind, data)
        raise ProviderError(last)

    @staticmethod
    def _extract(kind: str, data) -> str:
        if kind == "t":                                   # [["text","en"]]  or  ["text"] when sl is fixed
            if isinstance(data, list) and data:
                first = data[0]
                return first[0] if isinstance(first, list) else first
            raise ProviderError("google empty response")
        if not data or not data[0]:
            raise ProviderError("google empty response")
        return "".join(seg[0] for seg in data[0] if seg and seg[0])


class BingProvider:
    """bing.com/translator web endpoint. Best effort: Microsoft changes its anti-bot checks often;
    on any failure the engine disables this provider for the rest of the run."""
    name = "bing"
    max_chars = 1000
    _lang_map = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "no": "nb", "he": "iw", "auto": "auto-detect"}

    def __init__(self, limiter: RateLimiter):
        self._limiter = limiter
        self._lock = threading.Lock()
        self._s = _session()
        self._ig = self._iid = self._key = self._token = None

    def _handshake(self) -> None:
        page = self._s.get("https://www.bing.com/translator", timeout=TIMEOUT).text
        ig = re.search(r'IG:"([^"]+)"', page)
        iid = re.search(r'data-iid="([^"]+)"', page)
        aph = re.search(r"params_AbusePreventionHelper\s*=\s*\[([^\]]+)\]", page)
        if not (ig and iid and aph):
            raise ProviderError("bing handshake failed")
        parts = [p.strip().strip('"') for p in aph.group(1).split(",")]
        self._ig, self._iid, self._key, self._token = ig.group(1), iid.group(1), parts[0], parts[1]

    def translate(self, text: str, source: str, target: str) -> str:
        with self._lock:
            if self._token is None:
                self._handshake()
        self._limiter.wait()
        resp = self._s.post(
            f"https://www.bing.com/ttranslatev3?isVertical=1&IG={self._ig}&IID={self._iid}",
            data={"fromLang": self._lang_map.get(source, source), "to": self._lang_map.get(target, target),
                  "text": text, "key": self._key, "token": self._token},
            headers={"Referer": "https://www.bing.com/translator"}, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise ProviderError(f"bing http {resp.status_code}")
        try:
            return resp.json()[0]["translations"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"bing bad response: {exc}") from exc


class MyMemoryProvider:
    """api.mymemory.translated.net - free, 500 chars/request, ~5000 words/day anonymous. Reliable fallback."""
    name = "mymemory"
    max_chars = 450
    url = "https://api.mymemory.translated.net/get"

    def __init__(self, limiter: RateLimiter):
        self._limiter = limiter
        self._local = threading.local()

    def _s(self) -> requests.Session:
        if not hasattr(self._local, "s"):
            self._local.s = _session()
        return self._local.s

    def translate(self, text: str, source: str, target: str) -> str:
        src = "autodetect" if source == "auto" else source
        out = []
        for line in text.split("\n"):
            if not line.strip():
                out.append(line)
                continue
            self._limiter.wait()
            resp = self._s().get(self.url, params={"q": line, "langpair": f"{src}|{target}"}, timeout=TIMEOUT)
            if resp.status_code != 200:
                raise ProviderError(f"mymemory http {resp.status_code}")
            body = resp.json()
            translated = (body.get("responseData") or {}).get("translatedText")
            if not translated or str(body.get("responseStatus")) != "200":
                raise ProviderError(f"mymemory: {body.get('responseDetails', 'error')}")
            out.append(translated)
        return "\n".join(out)


class FakeProvider:
    """Deterministic offline provider for tests/benchmarks: each line becomes '[<target>] <line>'.
    Enabled by setting MT_FAKE_PROVIDER=1 (no rate limiting, no network)."""
    name = "fake"
    max_chars = 4000
    fail_times = 0                      # tests: raise ProviderError this many times first

    def __init__(self, limiter: RateLimiter | None = None):
        self._limiter = limiter
        self._failures_left = self.fail_times
        self.calls = 0

    def translate(self, text: str, source: str, target: str) -> str:
        self.calls += 1
        if self._failures_left > 0:
            self._failures_left -= 1
            raise ProviderError("fake failure")
        lines = text.split("\n")
        return "\n".join(f"[{target}] {line}" if line.strip() else line for line in lines)

import threading

import pytest

from mt.cache import SqliteCache
from mt.engine import TranslationError, Translator, should_skip
from mt.providers import FakeProvider, ProviderError, RateLimiter


class FailingProvider:
    name = "boom"
    max_chars = 4000

    def __init__(self):
        self.calls = 0

    def translate(self, text, source, target):
        self.calls += 1
        raise ProviderError("down")


def test_should_skip_rules():
    assert should_skip("") and should_skip("  ") and should_skip("12.5%") and should_skip("=SUM(A1)")
    assert should_skip("https://a.b/c") and should_skip("x@y.com") and should_skip("|---|---|")
    assert not should_skip("Hello 123") and not should_skip("नमस्ते")


def test_translate_many_keeps_order_and_skips(translator):
    out = translator.translate_many(["Hello", "42", "", "World"], "hi")
    assert out == ["[hi] Hello", "42", "", "[hi] World"]


def test_dedup_and_cache_avoid_requests(translator, fake):
    translator.translate_many(["same", "same", "other"], "hi")
    assert fake.calls == 1                       # one batch for all unique strings
    translator.translate_many(["same", "other", "same"], "hi")
    assert fake.calls == 1                       # everything cached
    translator.translate_many(["same"], "pa")
    assert fake.calls == 2                       # different target -> new request


def test_cache_survives_new_translator(cache_path):
    t1 = Translator(cache_path=cache_path, log=lambda m: None)
    t1.translate_many(["persist me"], "hi")
    t1.close()
    t2 = Translator(cache_path=cache_path, log=lambda m: None)
    assert t2.translate_many(["persist me"], "hi") == ["[hi] persist me"]
    assert t2.providers[0].calls == 0
    t2.close()


def test_batching_respects_max_chars(translator, fake):
    fake.max_chars = 30
    texts = [f"sentence number {i}" for i in range(10)]
    out = translator.translate_many(texts, "hi")
    assert out == [f"[hi] {t}" for t in texts]
    assert fake.calls > 1


def test_multiline_and_long_strings(translator, fake):
    fake.max_chars = 40
    long = "\n".join(f"line {i} of a long paragraph" for i in range(6))
    out = translator.translate_many([long, "short"], "hi")
    assert out[0] == "\n".join(f"[hi] line {i} of a long paragraph" for i in range(6))
    assert out[1] == "[hi] short"


def test_retry_then_success(translator, fake):
    fake._failures_left = 2                      # first two calls raise, third succeeds
    out = translator.translate_many(["retry me"], "hi")
    assert out == ["[hi] retry me"] and fake.calls == 3


def test_provider_fallback(cache_path):
    limiter = RateLimiter(1000)
    bad, good = FailingProvider(), FakeProvider(limiter)
    t = Translator(cache_path=cache_path, log=lambda m: None, providers=[bad, good])
    assert t.translate_many(["x"], "hi") == ["[hi] x"]
    assert bad.calls == 3 and good.calls == 1    # MAX_RETRIES on primary, then fallback
    t.close()


def test_all_providers_fail_raises(cache_path):
    t = Translator(cache_path=cache_path, log=lambda m: None, providers=[FailingProvider()])
    with pytest.raises(TranslationError):
        t.translate_many(["x"], "hi")
    t.close()


def test_line_merge_falls_back_to_single_requests(cache_path):
    class Merging(FakeProvider):
        def translate(self, text, source, target):
            self.calls += 1
            return "merged " + text.replace("\n", " ")
    p = Merging()
    t = Translator(cache_path=cache_path, log=lambda m: None, providers=[p])
    out = t.translate_many(["a", "b"], "hi")
    assert out == ["merged a", "merged b"] and p.calls == 3
    t.close()


def test_stop_event_aborts(translator, fake):
    translator.stop_event.set()
    with pytest.raises(TranslationError):
        translator.translate_many(["a", "b"], "hi")


def test_progress_callback(cache_path):
    seen = []
    t = Translator(cache_path=cache_path, log=lambda m: None, progress=lambda d, tot: seen.append((d, tot)))
    t.providers[0].max_chars = 10
    t.translate_many([f"w{i}" for i in range(6)], "hi")
    assert seen[0] == (0, 6) and seen[-1] == (6, 6)
    t.close()


def test_placeholders_go_through_engine(translator):
    out = translator.translate_many(["Hi {name} see https://a.io"], "hi")
    assert out == ["[hi] Hi {name} see https://a.io"]


def test_sqlite_cache_thread_safety(cache_path):
    cache = SqliteCache(cache_path)

    def writer(n):
        cache.put_many("auto", "hi", [(f"t{n}-{i}", f"x{i}") for i in range(200)])
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    cache.commit()
    assert cache.count() == 800
    cache.close()

from mt.hinglish import Transliterator, is_hinglish, hinglish_score


def test_detection():
    assert is_hinglish("Sir aapke liye dua hai aap aise hi kaam karte raho")
    assert is_hinglish("Public aapke saath hai sir aage badho")
    assert not is_hinglish("Arvind Kejriwal has redefined governance in modern India")
    assert not is_hinglish("केजरीवाल जी ज़िंदाबाद")            # already Devanagari
    assert not is_hinglish("ok")
    assert hinglish_score("this is plain english text") == 0.0


def test_transliterate_many_keeps_punctuation_numbers_and_acronyms(translator, monkeypatch):
    tr = translator.transliterator
    calls = []

    def fake_request(words):
        calls.append(list(words))
        return {w: f"<{w}>" for w in words}
    monkeypatch.setattr(tr, "_request", fake_request)
    out = tr.transliterate_many(["Bhai, aap 71,000 naukri! #AAP BJP se aage", "aap great ho keep it up"])
    # English words (great, keep it up) stay Latin for the translator; Hinglish words are transliterated
    assert out == ["<Bhai>, <aap> 71,000 <naukri>! #AAP BJP <se> <aage>", "<aap> great <ho> keep it up"]
    assert calls and "aap" in calls[0] and "BJP" not in calls[0] and "great" not in calls[0]
    tr.transliterate_many(["aap naukri"])
    assert len(calls) == 1                                            # cached, no new request


def test_engine_pivot_uses_hindi_source(cache_path, monkeypatch):
    from mt.engine import Translator
    t = Translator(cache_path=cache_path, log=lambda m: None)
    monkeypatch.setattr(t.transliterator, "_request", lambda words: {w: "देव" for w in words})
    assert t.translate_many(["aap bahut acche ho sir"], "hi") == ["देव देव देव देव देव"]
    assert t.translate_many(["aap bahut acche ho sir"], "pa") == ["[pa] देव देव देव देव देव"]
    assert t.translate_many(["plain english here"], "pa") == ["[pa] plain english here"]
    t.close()

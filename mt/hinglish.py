"""Hinglish (Hindi typed in Latin letters, e.g. "aap bahut acche ho sir") support.

Google detects such text as Hindi, so hi->hi returns it unchanged and pa/en translations lose words.
The fix is a pivot: detect romanised Hindi, transliterate it to Devanagari with Google's free Input
Tools endpoint (the same engine behind "Hinglish typing" keyboards), then translate that proper Hindi.

Input Tools truncates long strings, so words are sent in batches of <= 25 and cached individually.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import requests

from .cache import SqliteCache
from .providers import USER_AGENT, RateLimiter

INPUT_TOOLS_URL = "https://inputtools.google.com/request"
TIMEOUT = 20
WORDS_PER_REQUEST = 25
CHARS_PER_REQUEST = 140
CACHE_SOURCE, CACHE_TARGET = "latin", "translit-hi"       # namespace inside the shared SQLite cache

# Function words and very common tokens of romanised Hindi/Urdu. A line is Hinglish when enough of
# its Latin words are in this set - English text scores near zero, Hinglish scores 0.3-0.7.
MARKERS = frozenset("""
aap aapka aapke aapki aapko aapne ap apka apke apki apko apne hai hain hai. h he ho hu hun hoon tha thi the
thay ka ke ki ko se me mein main mai mera mere meri hum humko humne hamara hamare hamari tum tumhe tumhara
tumhari ye yeh yah wo woh vo voh unka unke unki uska uske uski inka inke iska iske jo jab tab kab kyu kyun
kyon kya kaise kaisa kaisi kahan kaha nahi nahin na mat bhi aur ya par per lekin magar kyunki kyuki toh to
hi bas bahut bahot bohot bohat kaam kam karo kar kare karke karta karti karte kiya kiye kijiye raho rahe
rahi raha rakho rakha dekho dekh dena do diya de lo le liya liye lie wala wale wali waala sab sabko sabse
sabhi log logon logo desh janta ji sir sahab saheb bhai bhaiya didi accha acha achha achhe acche achi acchi
sahi galat theek thik bilkul zindabad jindabad jai dhanyavad dhanyawad shukriya dua dil khush pyar pyaar
saath sath aage age piche neta naukri paisa paise ghar bijli pani sarkar sarkaar vikas jeet chunav vote
""".split())

# Common English words (google-10000-english, filtered). A Latin word in this set is real English and is
# left for the translator; anything else in a Hinglish line is treated as romanised Hindi.
ENGLISH_WORDS = frozenset((Path(__file__).parent / "english_words.txt").read_text(encoding="utf-8").split())
# Words that are both English and Hinglish ("the" = थे, "is" = इस, "me" = में ...): decided by neighbours.
AMBIGUOUS = frozenset("""the is me he to so do in on us or an be no am we it up as at by if of my go
har bas bill din der dal gum hum jag jal kal kam log man mar mat nag pal par pan ram sat tan tar teen chal
bat ban baat ghar pass sir koi phir kya aur are yeh woh jo toh was has had""".split())
_BAD_START = re.compile(r"^[ा-्ॢॣ]")   # output starting with a vowel sign = broken

_HAS_INDIC = re.compile(r"[ऀ-ൿ]")               # Devanagari .. Malayalam blocks
_LATIN_WORD = re.compile(r"[A-Za-z']+")
_TOKEN = re.compile(r"\s+|\S+")
_CORE = re.compile(r"^([^A-Za-z]*)([A-Za-z][A-Za-z']*)([^A-Za-z]*)$")


def hinglish_score(text: str) -> float:
    words = [w.lower().strip("'") for w in _LATIN_WORD.findall(text)]
    if len(words) < 2:
        return 0.0
    return sum(1 for w in words if w in MARKERS) / len(words)


def is_hinglish(text: str, threshold: float = 0.2) -> bool:
    """True for Latin-script text that reads as romanised Hindi (not for Devanagari, not for English)."""
    if _HAS_INDIC.search(text):
        return False
    words = _LATIN_WORD.findall(text)
    if len(words) < 2:
        return False
    hits = sum(1 for w in words if w.lower().strip("'") in MARKERS)
    return hits >= 2 and hits / len(words) >= threshold


def _keep_as_is(core: str) -> bool:
    """Acronyms (BJP, AAP, CM) and single letters are left in Latin script."""
    return len(core) == 1 or (core.isupper() and len(core) <= 5)


def classify_words(cores: list[str]) -> list[bool]:
    """For each Latin word: True = English (keep for the translator), False = Hinglish (transliterate).
    Ambiguous words follow their neighbours, so "keep it up" stays English and "hum the" stays Hindi."""
    lows = [c.lower().strip("'") for c in cores]
    sure = [lw in ENGLISH_WORDS and lw not in MARKERS for lw in lows]
    out = list(sure)
    for _ in range(2):                                     # let chains like "keep it up" settle
        for i, lw in enumerate(lows):
            if not sure[i] and lw in AMBIGUOUS:
                out[i] = (i > 0 and out[i - 1]) or (i + 1 < len(lows) and out[i + 1])
    return out


LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z' ,.!?-]*[A-Za-z]|[A-Za-z]")


class Transliterator:
    def __init__(self, cache: SqliteCache, limiter: RateLimiter, log=print):
        self.cache = cache
        self._limiter = limiter
        self.log = log
        self._local = threading.local()
        self.requests_made = 0

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "s"):
            self._local.s = requests.Session()
            self._local.s.headers["User-Agent"] = USER_AGENT
        return self._local.s

    # ------------------------------------------------------------------ public
    def transliterate_many(self, texts: list[str]) -> list[str]:
        """Latin-script Hindi -> Devanagari for each text. Punctuation, numbers, placeholder tokens,
        acronyms and real English words are left as they are (English is later translated, not
        spelled out phonetically)."""
        tokenised = [_TOKEN.findall(t) for t in texts]
        plans = []                                   # per text: list of (token, core|None)
        cores: set[str] = set()
        for tokens in tokenised:
            matches = [_CORE.match(tok) for tok in tokens]
            word_idx = [i for i, m in enumerate(matches) if m and not _keep_as_is(m.group(2))]
            english = classify_words([matches[i].group(2) for i in word_idx])
            plan = [(tok, None) for tok in tokens]
            for i, is_en in zip(word_idx, english):
                if not is_en:
                    plan[i] = (tokens[i], matches[i].group(2))
                    cores.add(matches[i].group(2))
            plans.append(plan)
        mapping = self._lookup(sorted(cores))
        out = []
        for plan in plans:
            pieces = []
            for tok, core in plan:
                if core is None:
                    pieces.append(tok)
                else:
                    m = _CORE.match(tok)
                    pieces.append(m.group(1) + mapping.get(core, core) + m.group(3))
            out.append("".join(pieces))
        return out

    # ----------------------------------------------------------------- internal
    def _lookup(self, words: list[str]) -> dict[str, str]:
        found = self.cache.get_many(CACHE_SOURCE, CACHE_TARGET, words)
        todo = [w for w in words if w not in found]
        if todo:
            self.log(f"  hinglish: {len(words)} unique words, {len(found)} cached, {len(todo)} to transliterate")
        batch: list[str] = []
        size = 0
        for w in todo:
            if batch and (len(batch) >= WORDS_PER_REQUEST or size + len(w) + 1 > CHARS_PER_REQUEST):
                self._store(found, self._request(batch))
                batch, size = [], 0
            batch.append(w)
            size += len(w) + 1
        if batch:
            self._store(found, self._request(batch))
        self.cache.commit()
        return found

    def _store(self, found: dict[str, str], result: dict[str, str]) -> None:
        found.update(result)
        # identity results mean the service failed for that word - do not cache those
        self.cache.put_many(CACHE_SOURCE, CACHE_TARGET, [(k, v) for k, v in result.items() if v != k])

    def _request(self, words: list[str]) -> dict[str, str]:
        self._limiter.wait()
        self.requests_made += 1
        try:
            resp = self._session().get(INPUT_TOOLS_URL, params={
                "text": " ".join(words), "itc": "hi-t-i0-und", "num": 1, "cp": 0, "cs": 1,
                "ie": "utf-8", "oe": "utf-8"}, timeout=TIMEOUT)
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            self.log(f"  hinglish transliteration failed ({exc}) - keeping Latin text")
            return {w: w for w in words}
        if not data or data[0] != "SUCCESS":
            self.log("  hinglish transliteration refused - keeping Latin text")
            return {w: w for w in words}
        out = data[1][0][1][0].split()
        out = [o if not _BAD_START.match(o) else w for w, o in zip(words, out)]
        if len(out) != len(words):                       # word count changed: fall back one word at a time
            if len(words) == 1:
                return {words[0]: words[0]}
            result = {}
            for w in words:
                result.update(self._request([w]))
            return result
        return dict(zip(words, out))

# MultiTranslate

Translate files, folders and spreadsheets into many languages at once — free, on your own Windows PC.
Built for Hinglish-first creators: comment dumps, product catalogues, scripts, subtitles.

| | |
|---|---|
| Formats | `.xlsx` `.csv` `.tsv` `.docx` `.txt` `.md` `.json` `.srt` `.vtt` — single file or whole folder (recursive) |
| Languages | any Google Translate code (`hi`, `pa`, `en`, `mr`, `gu`, `ta`, `te`, `bn`, `ur`, `es`, `fr`, `ar`, `zh-CN` …) |
| Output | **separate** (one file per language) or **combined** (all languages in ONE file: extra columns / sheets / sections) |
| Engines | Google free web endpoints (3, auto-rotated) → Bing (best effort) → MyMemory. No API keys. |
| Big data | 100k+ rows: multi-threaded, batched, deduplicated, SQLite cache, auto-save, crash-safe resume |
| UI | Web app (laptop window + phone over Wi-Fi), Android APK shell, CLI |

## Run

```
MultiTranslate.bat                      # starts the server and opens the app window (Edge/Chrome app mode)
python web.py [--lan]                   # same, by hand; --lan lets phones on the Wi-Fi connect
python translate.py <file|folder> --to hi,pa,en [--mode combined] [--out DIR]
```

Install once (Python 3.12 required): `powershell -ExecutionPolicy Bypass -File install_laptop.ps1`
→ installs packages and puts **MultiTranslate** on the Desktop and Start Menu.

### CLI examples

```
python translate.py comments.xlsx --to hi,pa                       # hi/comments_hi.xlsx, pa/comments_pa.xlsx
python translate.py comments.xlsx --to hi,pa,en --mode combined    # comments_multi.xlsx: Comment | Comment (Hindi) | ...
python translate.py sheet.xlsx --to hi --mode combined --sheet-mode sheets   # a translated sheet copy per language
python translate.py D:\data --to hi,pa --out D:\data_translated    # whole folder, structure mirrored
python translate.py big.csv --to hi --workers 8 --rps 4 --autosave 500
python translate.py names.xlsx --to hi,pa --glossary glossary.csv  # fixed spellings for names / brand terms
python translate.py --list-languages
python translate.py --clear-cache
```

Stop with Ctrl+C; run the same command again to resume (finished files are skipped, translated lines
come from the cache with zero network requests).

## How translation works

1. **Skip filter** — numbers, emails, URLs, Excel formulas, blank cells are copied through.
2. **Protection** — `{placeholders}`, `{{vars}}`, `%s`, `<tags>`, links, emails, `#hashtags`, `` `code` `` and
   glossary terms are swapped for `⟦N⟧` tokens (verified to survive every engine), then restored.
3. **Hinglish pivot** — romanised Hindi ("aap bahut acche ho sir") is detected, transliterated to Devanagari
   with Google Input Tools (word-level, cached), real English words inside it are translated as phrases,
   and *that* proper Hindi is what gets translated to Punjabi/English. Without this, Google returns
   Hinglish unchanged for Hindi and drops words for other targets. Disable with `--no-hinglish`.
4. **Dedup + cache** — identical lines are translated once; everything is stored in
   `~/.multitranslate/cache.sqlite3` keyed by (source, target, protected text).
5. **Batching** — lines are joined into ≤4000-char requests, grouped by script so language detection is
   consistent; a line that comes back unchanged is retried alone. 6 worker threads, ≤4 requests/s.
6. **Fallback** — Google endpoint 1 → 2 → 3 → Bing → MyMemory, with retries and back-off.

### Glossary

CSV with a header row: `term,hi,pa,...` — a blank cell keeps the term as written.
JSON: `{"Mann": {"hi": "मान", "pa": "ਮਾਨ"}, "Ayurveda": {}}`. See `samples/glossary_example.csv`.

## Web UI

`web.py` (Flask, port 5055). Drag-drop files or a folder, or type a local path for big batches.
Live preview of the output layout, progress bar, stop / resume, job history, zip download.
State-changing requests need the `X-Requested-With: fetch` header and a same-origin `Origin`
(CSRF guard); uploads are sanitised; downloads are confined to the job folder;
`MT_ALLOWED_ROOTS=D:\data;E:\work` restricts which local paths may be translated.

## Phone

`android/` — a WebView shell (no AndroidX, ~600 KB). Build: `cd android && gradlew.bat assembleRelease`
(needs the Android SDK path in `local.properties`; the release keystore is in `keystore/`).
The APK is in `FINAL/MultiTranslate.apk`. On first launch it asks for the laptop address shown at the
top of the web page. Any phone browser can also "Add to Home Screen" (PWA manifest included).

## Tests

```
python -m pytest            # 47 offline tests (fake provider), ~1 min
MT_INTEGRATION=1 python -m pytest -m integration    # real engines (network)
```

## Layout

```
translate.py          CLI              web.py              Flask app + job history
mt/engine.py          pipeline         mt/providers.py     Google / Bing / MyMemory / Fake
mt/hinglish.py        pivot            mt/protect.py       placeholders + glossary
mt/cache.py           SQLite           mt/runner.py        folder walk, modes, resume
mt/handlers/          one file per format          templates/index.html   the UI
android/              phone app        tests/              pytest suite
```

## Known limitations

- Bing's free web endpoint currently answers 401 to non-browser clients; it is kept as best effort and
  skipped automatically. MyMemory allows ~5000 words/day anonymously.
- Google's free endpoints are rate limited per IP (~5 req/s). Very large jobs (100k+ rows) take time:
  roughly 500 rows × 3 languages ≈ 45 s from a cold cache; re-runs are instant.
- Hinglish transliteration is word-level: names can come out wrong ("Mann" → मैं). Fix with a glossary.
- Machine translation: review anything public-facing.

## Licences of dependencies

Flask (BSD-3), requests (Apache-2.0), openpyxl (MIT), python-docx (MIT), Pillow (MIT-CMU),
pytest (MIT). Word list from `first20hours/google-10000-english` (no licence stated, derived from
Google's public n-gram data). Translation engines are the providers' public web endpoints — no
service-level guarantees; use within their fair-use limits.

## Website (multitranslate.in)

`site/` is a static site: `index.html`, `privacy.html`, `terms.html`, `refund.html`, `cookies.html`, `logo.png`,
`favicon.png`. No cookies, no analytics, no forms. Upload the folder to any static host (Cloudflare Pages,
Netlify, GitHub Pages, or the hosting that comes with the domain) and point `multitranslate.in` at it.
Before going live, replace every `[BRACKET]` placeholder (business name, address, email, hosting provider)
in the four policy pages and in `index.html` — search for `[` to find them.

## Hosting the online version (free)

The same app runs as a public website with `MT_PUBLIC=1` (Dockerfile + `render.yaml` included):
`/` = landing page, `/app` = the tool, `/download/MultiTranslate.apk` = the Android app, `/health`.
Public mode disables local paths, limits uploads to 60 MB, keeps each browser's jobs private
(ids live in its localStorage) and deletes jobs after `MT_JOB_TTL_HOURS` (default 12).

Render.com free plan (no card): push this folder to a GitHub repo → render.com → **New + → Blueprint** →
select the repo → service name `multitranslate` → Deploy. URL: `https://multitranslate.onrender.com`
(the APK opens this by default). Free instances sleep after 15 min idle and wake in ~30-60 s on the
next visit; the app shows "Starting…" and retries on its own. 750 free hours/month cover a whole month.
Alternative: Hugging Face Spaces (Docker Space, port 7860) — same Dockerfile works.

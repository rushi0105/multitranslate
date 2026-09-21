"""Language codes supported by the Google endpoint (subset shown in GUI, any code accepted in CLI)."""

LANGUAGES = {
    "af": "Afrikaans", "ar": "Arabic", "bn": "Bengali", "bg": "Bulgarian", "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "fi": "Finnish", "fr": "French", "de": "German", "el": "Greek", "gu": "Gujarati",
    "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "kn": "Kannada", "ko": "Korean", "ms": "Malay", "ml": "Malayalam", "mr": "Marathi", "ne": "Nepali",
    "no": "Norwegian", "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pa": "Punjabi", "ro": "Romanian",
    "ru": "Russian", "sr": "Serbian", "es": "Spanish", "sw": "Swahili", "sv": "Swedish", "ta": "Tamil",
    "te": "Telugu", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
}


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


def parse_language_list(raw: str) -> list[str]:
    """'hi, mr,es' -> ['hi', 'mr', 'es'] (dedup, order kept)."""
    seen: list[str] = []
    for part in raw.replace(";", ",").split(","):
        code = part.strip()
        if code and code not in seen:
            seen.append(code)
    return seen


# How each language writes its own name - shown on the picker so a user recognises it instantly.
NATIVE_NAMES = {
    "en": "English", "hi": "हिन्दी", "pa": "ਪੰਜਾਬੀ", "mr": "मराठी", "gu": "ગુજરાતી", "ta": "தமிழ்", "te": "తెలుగు",
    "kn": "ಕನ್ನಡ", "ml": "മലയാളം", "bn": "বাংলা", "ur": "اردو", "ne": "नेपाली", "es": "Español", "fr": "Français",
    "de": "Deutsch", "ar": "العربية", "zh-CN": "中文 (简体)", "zh-TW": "中文 (繁體)", "ja": "日本語", "ko": "한국어",
    "ru": "Русский", "pt": "Português", "it": "Italiano", "tr": "Türkçe", "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt", "th": "ไทย", "nl": "Nederlands", "pl": "Polski", "uk": "Українська", "fa": "فارسی",
    "sw": "Kiswahili", "ms": "Bahasa Melayu", "sv": "Svenska", "no": "Norsk", "da": "Dansk", "fi": "Suomi",
    "el": "Ελληνικά", "he": "עברית", "hu": "Magyar", "cs": "Čeština", "ro": "Română", "bg": "Български",
    "hr": "Hrvatski", "sr": "Српски", "af": "Afrikaans",
}

# The picker's first row: the languages an Indian creator reaches for most.
FEATURED = ["en", "hi", "pa", "mr", "gu", "ta", "te", "bn", "ur", "kn", "ml"]


def native_name(code: str) -> str:
    return NATIVE_NAMES.get(code, LANGUAGES.get(code, code))

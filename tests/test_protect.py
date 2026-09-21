from pathlib import Path

from mt.protect import Glossary, Protector


def test_placeholders_are_tokenised_and_restored():
    p = Protector()
    text = "Hi {name}, see <b>{{ item }}</b> at https://x.io or mail a@b.co #deal %(n)s"
    prot = p.protect(text)
    assert "{name}" not in prot.text and "https://x.io" not in prot.text and "<b>" not in prot.text
    assert len(prot.slots) == 8
    restored, warnings = p.restore(prot.text, prot, "hi")
    assert restored == text and warnings == []


def test_missing_token_is_appended_with_warning():
    p = Protector()
    prot = p.protect("Order {id} shipped")
    restored, warnings = p.restore("ऑर्डर भेज दिया", prot, "hi")      # provider dropped the token
    assert restored.endswith("{id}") and len(warnings) == 1


def test_disabled_protection_leaves_text_alone():
    p = Protector(enabled=False)
    prot = p.protect("Hi {name}")
    assert prot.text == "Hi {name}" and prot.slots == []


def test_glossary_translation_and_keep_terms():
    g = Glossary({"VedaSoaps": {"hi": "वेदा साबुन"}, "Ayurveda": {}})
    p = Protector(glossary=g)
    prot = p.protect("VedaSoaps uses Ayurveda. vedasoaps rocks")
    assert prot.text.count("⟦") == 3           # case-insensitive, whole-word matches
    hi, _ = p.restore(prot.text, prot, "hi")
    assert hi == "वेदा साबुन uses Ayurveda. वेदा साबुन rocks"
    pa, _ = p.restore(prot.text, prot, "pa")        # no Punjabi entry -> term kept
    assert pa == "VedaSoaps uses Ayurveda. vedasoaps rocks"   # original casing kept


def test_glossary_longest_match_wins_and_no_partial_words():
    g = Glossary({"Soap": {"hi": "S"}, "Soap Bar": {"hi": "SB"}})
    p = Protector(glossary=g)
    prot = p.protect("Soap Bar and Soapstone")
    assert len(prot.slots) == 1 and prot.slots[0][0] == "Soap Bar"


def test_glossary_load_csv_and_json(tmp_path: Path):
    csv_file = tmp_path / "g.csv"
    csv_file.write_text("term,hi,pa\nBrand,ब्रांड,\nKeepMe,,\n", encoding="utf-8")
    g = Glossary.load(csv_file)
    assert g.entries == {"Brand": {"hi": "ब्रांड"}, "KeepMe": {}}
    json_file = tmp_path / "g.json"
    json_file.write_text('{"Brand": {"hi": "ब्रांड", "pa": ""}, "X": null}', encoding="utf-8")
    g2 = Glossary.load(json_file)
    assert g2.entries == {"Brand": {"hi": "ब्रांड"}, "X": {}}

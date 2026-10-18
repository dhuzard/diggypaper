"""NTS identifier / national authorization extraction (deterministic, SYNTHETIC and real-shaped text)."""

# ruff: noqa: E501  (long synthetic sentences)

from app.nts.identifiers import extract_authorizations, extract_nts


def test_exact_identifier_with_version_and_date():
    (m,) = extract_nts("The ALURES code for the research is: NTS-RO-406074 v.1, 8 October 2025.")
    assert (m.normalized, m.country, m.version_cited, m.date_cited) == (
        "NTS-RO-406074",
        "RO",
        "1",
        "8 October 2025",
    )


def test_lowercase_hash_form_is_normalized_but_verbatim_kept():
    (m,) = extract_nts("approved (#3/11/2021/171/A and ALURES #nts-es-414463) and performed")
    assert m.normalized == "NTS-ES-414463"
    assert m.verbatim == "nts-es-414463"
    assert m.version_cited is None


def test_non_breaking_hyphen_and_spaces():
    assert extract_nts("NTS‑ES‑000123")[0].normalized == "NTS-ES-000123"
    assert extract_nts("NTS ES 000123")[0].normalized == "NTS-ES-000123"


def test_national_numbers_are_not_nts_ids():
    text = (
        "project authorization was issued by the National Sanitary Veterinary and Food Safety "
        "Authority (406/29 April 2024). Registered under APAFIS#6573-2016 v2. "
        "Project Licence PPL PA03F7F07."
    )
    assert extract_nts(text) == []
    kinds = {a.kind: a.value for a in extract_authorizations(text)}
    assert kinds["ro-project-authorization"] == "406/29 April 2024"
    assert kinds["apafis"] == "6573-2016"
    assert kinds["uk-ppl"] == "PA03F7F07"


def test_no_nts_in_text_gives_no_mention():
    assert extract_nts("Animals were housed under Directive 2010/63/EU.") == []


def test_ambiguous_numeric_overlap_between_systems_stays_two_identifiers():
    text = "Authority (406/29 April 2024) project authorization was issued by X. NTS-RO-406074 v.1"
    (nts,) = extract_nts(text)
    assert nts.normalized == "NTS-RO-406074"
    assert all(a.value != "406074" for a in extract_authorizations(text))

"""JATS reader (SYNTHETIC XML)."""

# ruff: noqa: E501

import pytest

from app.nts import jats

XML = b"""<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink"><front>
<article-meta><article-id pub-id-type="doi">10.5555/x</article-id><article-id pub-id-type="pmcid">PMC1</article-id>
<title-group><article-title>Synthetic <i>title</i></article-title></title-group>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"><license-p>x</license-p></license></permissions>
</article-meta></front><body>
<sec><title>Introduction</title><p>skipped</p></sec>
<sec><title>Materials and Methods</title><p>A total of 12 mice were used.</p><p>Second.</p></sec>
<sec><title>Results</title><p>Rates rose.</p></sec></body></article>"""


def test_parse_keeps_methods_and_results_paragraphs_and_reads_licence():
    s = jats.parse(
        XML, "pmc1", "2026-01-01T00:00:00Z", "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/"
    )
    assert [x["kind"] for x in s["sections"]] == ["methods", "methods", "results"]
    assert s["sections"][0]["text"] == "A total of 12 mice were used."
    assert s["license"]["license_id"] == "CC-BY-4.0" and s["title"] == "Synthetic title"
    assert s["identifiers"]["pmcid"] == "PMC1"


def test_dtd_and_entities_are_refused():
    with pytest.raises(ValueError, match="refused"):
        jats.parse(b'<!DOCTYPE a [<!ENTITY x "y">]><a/>', "x", "t", "u")


def test_unlicensed_text_is_not_marked_redistributable():
    s = jats.parse(b"<article><body/></article>", "x", "2026-01-01T00:00:00Z", "https://e.org")
    assert s["license"]["license_id"] == "unknown" and s["license"]["evidence_url"] is None

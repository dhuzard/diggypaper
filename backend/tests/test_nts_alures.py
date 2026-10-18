"""ALURES handling on SYNTHETIC CSVs (not the real export): schema discovery and exact joins."""

import hashlib

from nts_support import write_csv

from app.nts import alures

HEADER = ["NTS ID", "Version", "Country", "Title"]


def _index(tmp_path, rows, header=HEADER):
    path = write_csv(tmp_path / "synthetic_nts.csv", header, rows)
    return alures.load_table(path, header[0], header[1], header[2], header[3]), path


def test_schema_is_discovered_not_assumed(tmp_path):
    path = write_csv(
        tmp_path / "odd.csv",
        ["Project reference", "Revision", "MS", "Number of animals", "Species name"],
        [
            ["NTS-ES-000001", "1", "Spain", "10", "Mice"],
            ["NTS-RO-000002", "2", "Romania", "5", "Rats"],
        ],
    )
    schema = alures.discover_schema(path)
    assert schema["id_column_candidates"] == ["Project reference"]
    assert schema["version_column_candidates"] == []  # "Revision" is not guessed to be a version
    assert schema["count_column_candidates"] == ["Number of animals"]
    assert schema["species_column_candidates"] == ["Species name"]


def test_exact_match_confirmed(tmp_path):
    idx, _ = _index(tmp_path, [["NTS-ES-414463", "1", "Spain", "T"]])
    r = alures.join_exact("NTS-ES-414463", "1", "ES", idx)
    assert r["state"] == "confirmed-exact" and r["records"][0]["row_numbers"] == [2]


def test_missing_id_is_unmatched_never_fabricated(tmp_path):
    idx, _ = _index(tmp_path, [["NTS-ES-000001", "1", "Spain", "T"]])
    r = alures.join_exact("NTS-RO-406074", "1", "RO", idx)
    assert r["state"] == "unmatched" and r["records"] == []


def test_near_identifier_is_candidate_only(tmp_path):
    idx, _ = _index(tmp_path, [["NTS-RO-406075", "1", "Romania", "T"]])
    r = alures.join_exact("NTS-RO-406074", "1", "RO", idx)
    assert r["state"] == "candidate" and r["records"] == []
    assert r["candidates"] == ["NTS-RO-406075"]


def test_same_digits_other_country_is_candidate_only(tmp_path):
    idx, _ = _index(tmp_path, [["NTS-ES-406074", "1", "Spain", "T"]])
    assert alures.join_exact("NTS-RO-406074", "1", "RO", idx)["state"] == "candidate"


def test_amended_version_is_distinguished_from_cited(tmp_path):
    idx, _ = _index(
        tmp_path, [["NTS-RO-406074", "1", "Romania", "T"], ["NTS-RO-406074", "2", "Romania", "T"]]
    )
    r = alures.join_exact("NTS-RO-406074", "1", "RO", idx)
    assert r["state"] == "confirmed-exact-amended"
    assert r["current_version"] == "2" and "newer than cited" in r["reasons"][0]


def test_cited_version_absent_from_export(tmp_path):
    idx, _ = _index(tmp_path, [["NTS-RO-406074", "2", "Romania", "T"]])
    assert (
        alures.join_exact("NTS-RO-406074", "1", "RO", idx)["state"]
        == "confirmed-exact-cited-version-absent"
    )


def test_no_cited_version_with_several_is_ambiguous(tmp_path):
    idx, _ = _index(
        tmp_path, [["NTS-ES-000009", "1", "Spain", "T"], ["NTS-ES-000009", "2", "Spain", "T"]]
    )
    assert alures.join_exact("NTS-ES-000009", None, "ES", idx)["state"] == "ambiguous"


def test_conflicting_country_or_title_is_ambiguous(tmp_path):
    idx, _ = _index(
        tmp_path, [["NTS-ES-000009", "1", "Spain", "T"], ["NTS-ES-000009", "1", "France", "T"]]
    )
    assert alures.join_exact("NTS-ES-000009", "1", "ES", idx)["state"] == "ambiguous"


def test_identical_duplicate_rows_are_merged_and_reported(tmp_path):
    idx, _ = _index(
        tmp_path, [["NTS-ES-000009", "1", "Spain", "T"], ["NTS-ES-000009", "1", "Spain", "T"]]
    )
    r = alures.join_exact("NTS-ES-000009", "1", "ES", idx)
    assert r["state"] == "duplicate-rows" and r["records"][0]["row_numbers"] == [2, 3]


def test_no_dataset_means_unavailable():
    assert alures.join_exact("NTS-ES-000009", "1", "ES", None)["state"] == "alures-unavailable"


def test_receipt_has_sha256_and_rows(tmp_path):
    _, path = _index(tmp_path, [["NTS-ES-000001", "1", "Spain", "T"]])
    r = alures.receipt_for(path, "https://example.org/x.csv", "2026-01-01T00:00:00Z", 200)
    assert r.sha256 == hashlib.sha256(path.read_bytes()).hexdigest() and r.rows == 1


def test_planned_animals_are_ceilings_and_non_numeric_is_not_reported():
    rows = [{"Species": "Mice", "N": "120"}, {"Species": "Rats", "N": "n/a"}]
    out = alures.planned_animals(rows, "Species", "N")
    assert out[0]["authorized_number"] == 120
    assert "not animals used" in out[0]["meaning"]
    assert out[1]["authorized_number"] == "not reported"


def test_csv_links_extracts_only_csv_and_zip():
    html = '<a href="/d/alures_nts_projects.csv">x</a><a href="a.html">y</a><a href="b.zip">z</a>'
    links = alures.csv_links(html, "https://example.org/alures/datasets.html")
    assert links == [
        "https://example.org/alures/b.zip",
        "https://example.org/d/alures_nts_projects.csv",
    ]

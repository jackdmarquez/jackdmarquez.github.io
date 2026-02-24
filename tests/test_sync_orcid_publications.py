import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import importlib.util
import sys


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_orcid_publications.py"
spec = importlib.util.spec_from_file_location("sync_orcid_publications", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class OrcidSyncTests(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(
            mod.normalize_doi("https://doi.org/10.1234/ABC.def"),
            "10.1234/abc.def",
        )
        self.assertEqual(mod.normalize_doi("doi:10.1000/XYZ"), "10.1000/xyz")
        self.assertIsNone(mod.normalize_doi("not-a-doi"))

    def test_parse_orcid_works(self):
        payload = {
            "group": [
                {
                    "work-summary": [
                        {
                            "put-code": 1,
                            "path": "/0000-0000-0000-0000/work/1",
                            "title": {"title": {"value": "My Paper"}},
                            "journal-title": {"value": "Journal X"},
                            "publication-date": {
                                "year": {"value": "2024"},
                                "month": {"value": "7"},
                                "day": {"value": "9"},
                            },
                            "external-ids": {
                                "external-id": [
                                    {"external-id-type": "doi", "external-id-value": "10.1111/TEST"}
                                ]
                            },
                            "url": {"value": "https://example.org/paper"},
                        }
                    ]
                }
            ]
        }
        records = mod.parse_orcid_works(payload)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.orcid_put_code, "1")
        self.assertEqual(rec.title, "My Paper")
        self.assertEqual(rec.venue, "Journal X")
        self.assertEqual(rec.doi, "10.1111/test")
        self.assertEqual((rec.year, rec.month, rec.day), (2024, 7, 9))

    def test_crossref_abstract_cleaning_and_citation(self):
        base = mod.PublicationRecord(
            orcid_put_code="9",
            orcid_path=None,
            title="Fallback title",
            doi="10.1111/test",
            year=2024,
            month=None,
            day=None,
            venue="Fallback Journal",
            authors=[],
            url=None,
            abstract=None,
            citation=None,
            source_quality="orcid",
        )
        payload = {
            "message": {
                "DOI": "10.1111/TEST",
                "URL": "https://doi.org/10.1111/TEST",
                "title": ["Crossref Title"],
                "container-title": ["Crossref Journal"],
                "author": [{"given": "Ada", "family": "Lovelace"}],
                "issued": {"date-parts": [[2024, 2, 3]]},
                "abstract": "<jats:p>Hello <b>world</b></jats:p>",
            }
        }
        rec = mod.parse_crossref_message(payload, base)
        self.assertEqual(rec.title, "Crossref Title")
        self.assertEqual(rec.venue, "Crossref Journal")
        self.assertEqual(rec.authors, ["Ada Lovelace"])
        self.assertEqual((rec.year, rec.month, rec.day), (2024, 2, 3))
        self.assertEqual(rec.abstract, "Hello world")
        self.assertIn("doi:10.1111/test", rec.citation)

    def test_duplicate_detection_finds_doi_in_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            pub_dir = Path(td)
            (pub_dir / "2024-01-01-existing.md").write_text(
                """---
title: "Existing Paper"
date: 2024-01-01
paperurl: "https://doi.org/10.5555/ABC"
---

Body
""",
                encoding="utf-8",
            )
            idx = mod.build_existing_index(pub_dir)
            rec = mod.PublicationRecord(
                orcid_put_code="100",
                orcid_path=None,
                title="Another Title",
                doi="10.5555/abc",
                year=2024,
                month=1,
                day=1,
                venue=None,
                authors=[],
                url=None,
                abstract=None,
                citation=None,
                source_quality="orcid",
            )
            self.assertTrue(mod.is_duplicate(rec, idx))

    def test_slug_collision_adds_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            pub_dir = Path(td)
            rec = mod.PublicationRecord(
                orcid_put_code="10",
                orcid_path=None,
                title="A Title",
                doi=None,
                year=2024,
                month=1,
                day=1,
                venue=None,
                authors=[],
                url=None,
                abstract=None,
                citation=None,
                source_quality="orcid",
            )
            first = mod.choose_unique_output_path(pub_dir, rec)
            first.write_text("x", encoding="utf-8")
            second = mod.choose_unique_output_path(pub_dir, rec)
            self.assertTrue(second.name.endswith("-2.md"))

    def test_bold_author_name_in_bibtex(self):
        raw = (
            "@article{key,\n"
            "  author = {Alice Doe and J. Marquez and Marquez, J. and Bob Roe},\n"
            "  title = {X}\n"
            "}\n"
        )
        patched = mod.bold_author_name_in_bibtex(raw)
        self.assertIn(r"\textbf{J. Marquez}", patched)
        self.assertIn(r"\textbf{Marquez, J.}", patched)
        self.assertIn("Alice Doe", patched)

    def test_parse_bibtex_index_and_duplicate_detection(self):
        text = (
            "@article{foo2025bar,\n"
            "  title = {Interesting Result},\n"
            "  year = {2025},\n"
            "  DOI = {10.9999/ABC}\n"
            "}\n"
        )
        idx = mod.parse_bibtex_index(text)
        self.assertIn("foo2025bar", idx.keys)
        self.assertIn("10.9999/abc", idx.dois)
        rec = mod.PublicationRecord(
            orcid_put_code="3",
            orcid_path=None,
            title="Interesting Result",
            doi="10.9999/abc",
            year=2025,
            month=None,
            day=None,
            venue=None,
            authors=[],
            url=None,
            abstract=None,
            citation=None,
            source_quality="crossref",
        )
        self.assertTrue(mod.is_bib_duplicate(rec, idx))

    def test_append_bibtex_entries_appends_and_skips_duplicates(self):
        rec = mod.PublicationRecord(
            orcid_put_code="4",
            orcid_path=None,
            title="Fresh Paper",
            doi="10.1234/fresh",
            year=2026,
            month=1,
            day=2,
            venue="Journal Y",
            authors=["Jane Doe", "J. Marquez"],
            url="https://doi.org/10.1234/fresh",
            abstract=None,
            citation=None,
            source_quality="crossref",
        )
        with tempfile.TemporaryDirectory() as td:
            bib = Path(td) / "publications.bib"
            bib.write_text(
                "@article{existing,\n  title = {Existing},\n  year = {2024},\n  doi = {10.1111/existing}\n}\n",
                encoding="utf-8",
            )
            with patch.object(mod, "fetch_doi_bibtex", return_value='@article{crossrefkey,\n author = {J. Marquez and Jane Doe},\n title = {Fresh Paper},\n year = {2026},\n doi = {10.1234/fresh}\n}\n'):
                added, skipped, warnings, titles = mod.append_bibtex_entries(bib, [rec], dry_run=False)
            self.assertEqual((added, skipped), (1, 0))
            self.assertEqual(titles, ["Fresh Paper"])
            out = bib.read_text(encoding="utf-8")
            self.assertIn(r"\textbf{J. Marquez}", out)
            # second run should skip duplicate DOI
            with patch.object(mod, "fetch_doi_bibtex", return_value=None):
                added2, skipped2, warnings2, _ = mod.append_bibtex_entries(bib, [rec], dry_run=False)
            self.assertEqual((added2, skipped2), (0, 1))
            self.assertTrue(any("duplicate" in w.lower() for w in warnings2))

    def test_format_publication_markdown_contains_required_fields(self):
        rec = mod.PublicationRecord(
            orcid_put_code="77",
            orcid_path="/a/b",
            title='A "Quoted" Paper',
            doi="10.1111/test",
            year=2025,
            month=4,
            day=5,
            venue="Venue",
            authors=["A B"],
            url="https://doi.org/10.1111/test",
            abstract="Short abstract.",
            citation="A B. (2025). A Paper. Venue. doi:10.1111/test",
            source_quality="crossref",
        )
        md = mod.format_publication_markdown(rec, "a-quoted-paper")
        self.assertIn('collection: publications', md)
        self.assertIn('permalink: "/publication/2025-04-05-a-quoted-paper"', md)
        self.assertIn('orcid_put_code: "77"', md)
        self.assertIn('doi: "10.1111/test"', md)
        self.assertIn("_Generated from ORCID/Crossref metadata by orcid-bot._", md)

    def test_sync_dry_run_adds_only_missing(self):
        orcid_payload = {
            "group": [
                {
                    "work-summary": [
                        {
                            "put-code": 1,
                            "path": "/orcid/work/1",
                            "title": {"title": {"value": "Existing Paper"}},
                            "publication-date": {"year": {"value": "2024"}},
                            "external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.2000/existing"}]},
                        }
                    ]
                },
                {
                    "work-summary": [
                        {
                            "put-code": 2,
                            "path": "/orcid/work/2",
                            "title": {"title": {"value": "New Paper"}},
                            "journal-title": {"value": "Journal N"},
                            "publication-date": {"year": {"value": "2025"}, "month": {"value": "3"}},
                            "external-ids": {"external-id": [{"external-id-type": "doi", "external-id-value": "10.2000/new"}]},
                            "url": {"value": "https://example.org/new"},
                        }
                    ]
                },
            ]
        }
        crossref_payload = {
            "message": {
                "DOI": "10.2000/new",
                "URL": "https://doi.org/10.2000/new",
                "title": ["New Paper (Crossref)"],
                "container-title": ["Journal N"],
                "issued": {"date-parts": [[2025, 3, 2]]},
                "author": [{"given": "Jane", "family": "Doe"}],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            pub_dir = Path(td)
            (pub_dir / "2024-01-01-existing-paper.md").write_text(
                """---
title: "Existing Paper"
date: 2024-01-01
doi: "10.2000/existing"
---
""",
                encoding="utf-8",
            )
            cv_bib = pub_dir / "publications.bib"
            cv_bib.write_text("", encoding="utf-8")
            with patch.object(mod, "fetch_orcid_works", return_value=orcid_payload), patch.object(
                mod, "fetch_crossref_by_doi", return_value=crossref_payload
            ), patch.object(mod, "fetch_doi_bibtex", return_value="@article{newpaper,\n  author={Jane Doe and J. Marquez},\n  title={New Paper (Crossref)},\n  year={2025},\n  doi={10.2000/new}\n}\n"):
                res = mod.sync_publications(
                    orcid_id="0000-0000-0000-0000",
                    publications_dir=pub_dir,
                    dry_run=True,
                    verbose=False,
                    cv_bib_path=cv_bib,
                    cv_dry_run=True,
                )
            self.assertEqual(res.skipped_existing, 1)
            self.assertEqual(len(res.created_files), 1)
            self.assertFalse(res.created_files[0].exists())  # dry-run
            self.assertEqual(res.created_records[0].title, "New Paper (Crossref)")
            self.assertEqual(res.created_records[0].source_quality, "crossref")
            self.assertEqual(res.cv_bib_entries_added, 1)


if __name__ == "__main__":
    unittest.main()

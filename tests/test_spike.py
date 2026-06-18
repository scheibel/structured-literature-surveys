from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sls.bibtex import record_to_bibtex
from sls.eg import deduplicate, normalize_pages
from sls.identity import assign_candidate_ids
from sls.pdfs import register_pdf, refresh_run_pdf_status, write_missing_pdf_report
from sls.query import translate_for_eg


class SpikeTests(unittest.TestCase):
    def test_translate_generic_query_for_eg(self) -> None:
        translation = translate_for_eg("('temporal' OR 'dynamic' OR 'animated') AND 'treemap'")

        self.assertEqual(translation.translated_query, "(temporal OR dynamic OR animated) AND treemap")
        self.assertTrue(any("Single-quoted" in note for note in translation.semantics_notes))

    def test_normalize_deduplicate_and_bibtex(self) -> None:
        page = {
            "_embedded": {
                "searchResult": {
                    "_embedded": {
                        "objects": [
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "abc",
                                        "name": "Treemap Literacy: A Classroom-Based Investigation",
                                        "handle": "10.2312/eged20201032",
                                        "metadata": {
                                            "dc.title": [{"value": "Treemap Literacy: A Classroom-Based Investigation", "place": 0}],
                                            "dc.contributor.author": [
                                                {"value": "Firat, Elif E.", "place": 0},
                                                {"value": "Denisova, Alena", "place": 1},
                                            ],
                                            "dc.date.issued": [{"value": "2020", "place": 0}],
                                            "dc.identifier.doi": [{"value": "10.2312/eged.20201032", "place": 0}],
                                            "dc.identifier.uri": [
                                                {"value": "https://doi.org/10.2312/eged.20201032", "place": 0},
                                                {"value": "https://diglib.eg.org:443/handle/10.2312/eged20201032", "place": 1},
                                            ],
                                            "dc.description.seriesinformation": [{"value": "Eurographics 2020 - Education Papers", "place": 0}],
                                            "dc.identifier.pages": [{"value": "29-38", "place": 0}],
                                            "dc.publisher": [{"value": "The Eurographics Association", "place": 0}],
                                        },
                                        "_links": {"self": {"href": "https://diglib.eg.org/server/api/core/items/abc"}},
                                    }
                                }
                            },
                            {
                                "_embedded": {
                                    "indexableObject": {
                                        "uuid": "duplicate",
                                        "metadata": {
                                            "dc.title": [{"value": "Treemap Literacy: A Classroom-Based Investigation", "place": 0}],
                                            "dc.contributor.author": [{"value": "Firat, Elif E.", "place": 0}],
                                            "dc.date.issued": [{"value": "2020", "place": 0}],
                                            "dc.identifier.doi": [{"value": "https://doi.org/10.2312/eged.20201032", "place": 0}],
                                        },
                                        "_links": {"self": {"href": "https://diglib.eg.org/server/api/core/items/duplicate"}},
                                    }
                                }
                            },
                        ]
                    }
                }
            }
        }

        records = normalize_pages([page])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["doi"], "10.2312/eged.20201032")
        self.assertEqual(records[0]["canonical_url"], "https://diglib.eg.org/handle/10.2312/eged20201032")

        candidates = deduplicate(records)
        self.assertEqual(len(candidates), 1)
        assign_candidate_ids(candidates)
        self.assertEqual(candidates[0]["candidate_id"], "firat2020treemapliteracyclassroom")

        bibtex = record_to_bibtex(candidates[0])
        self.assertIn("@inproceedings{firat2020treemapliteracyclassroom,", bibtex)
        self.assertIn("doi = {10.2312/eged.20201032}", bibtex)

    def test_register_pdf_and_refresh_missing_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                run_dir = root / "searches" / "2026-06-19_test"
                run_dir.mkdir(parents=True)
                candidate = {
                    "candidate_id": "firat2020treemapliteracyclassroom",
                    "bibtex_key": "firat2020treemapliteracyclassroom",
                    "title": "Treemap Literacy: A Classroom-Based Investigation",
                    "authors": "Firat, Elif E.",
                    "year": "2020",
                    "doi": "10.2312/eged.20201032",
                    "canonical_url": "https://diglib.eg.org/handle/10.2312/eged20201032",
                    "has_local_pdf": "no",
                    "pdf_path": "",
                }
                import csv

                with (run_dir / "merged_candidates.csv").open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(candidate.keys()))
                    writer.writeheader()
                    writer.writerow(candidate)

                missing = write_missing_pdf_report(root / "library" / "manifests" / "missing.csv", run_dir)
                self.assertEqual(len(missing), 1)

                source_pdf = root / "downloaded.pdf"
                source_pdf.write_bytes(b"%PDF-1.4\nfake\n")
                registration = register_pdf(
                    candidate_id="firat2020treemapliteracyclassroom",
                    source_pdf=source_pdf,
                )
                self.assertTrue(registration.pdf_path.exists())

                mapped = refresh_run_pdf_status(run_dir)
                self.assertEqual(mapped, 1)
                missing = write_missing_pdf_report(root / "library" / "manifests" / "missing.csv", run_dir)
                self.assertEqual(missing, [])
            finally:
                import os

                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()

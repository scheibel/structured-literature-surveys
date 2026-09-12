from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sls.bibtex import record_to_bibtex
from sls.acm import import_acm_export, parse_acm_export, prepare_acm_search
from sls.dblp import normalize_pages as normalize_dblp_pages
from sls.eg import deduplicate, normalize_pages, write_validation_report
from sls.identity import assign_candidate_ids, normalize_doi
from sls.pdfs import register_pdf, refresh_run_pdf_status, write_missing_pdf_report
from sls.query import translate_for_acm, translate_for_dblp, translate_for_eg, translate_for_springer
import sls.springer as springer


class SpikeTests(unittest.TestCase):
    def test_doi_url_normalization(self) -> None:
        variants = [
            " 10.1111/CGF.15087 ",
            "doi: 10.1111/CGF.15087",
            "https://doi.org/10.1111/CGF.15087",
            "HTTP://DX.DOI.ORG/10.1111/CGF.15087",
            "https://onlinelibrary.wiley.com/doi/10.1111/CGF.15087",
            "https://publisher.example/content/doi/10.1111/CGF.15087?tracking=123#references",
            "https://publisher.example/doi/10.1111%2FCGF.15087",
        ]
        for value in variants:
            with self.subTest(value=value):
                self.assertEqual(normalize_doi(value), "10.1111/cgf.15087")

    def test_doi_normalization_preserves_suffix_and_ignores_unrelated_urls(self) -> None:
        value = "10.1234/part/doi/10.5678/item(2)?x#y"
        self.assertEqual(normalize_doi(value), value)
        self.assertEqual(normalize_doi("https://publisher.example/doi/10.1234/part(2)/item"), "10.1234/part(2)/item")
        for value in ("https://publisher.example/doi/not-a-doi", "https://publisher.example/search?q=/doi/10.1234/example"):
            with self.subTest(value=value):
                self.assertEqual(normalize_doi(value), value)

    def test_translate_generic_query_for_eg(self) -> None:
        translation = translate_for_eg("('temporal' OR 'dynamic' OR 'animated') AND 'treemap'")

        self.assertEqual(translation.translated_query, "(temporal OR dynamic OR animated) AND treemap")
        self.assertTrue(any("Single-quoted" in note for note in translation.semantics_notes))

    def test_translate_generic_query_for_acm(self) -> None:
        translation = translate_for_acm("('temporal' OR 'dynamic' OR 'animated') AND 'treemap'")

        self.assertEqual(translation.translated_query, "(temporal OR dynamic OR animated) AND treemap")
        self.assertEqual(translation.source, "acm")

    def test_translate_generic_query_for_springer(self) -> None:
        translation = translate_for_springer("('temporal' OR 'dynamic' OR 'animated') AND 'treemap'")

        self.assertEqual(
            translation.translated_query,
            '(keyword:"temporal" OR keyword:"dynamic" OR keyword:"animated") AND keyword:"treemap"',
        )
        self.assertEqual(translation.source, "springer")

    def test_translate_generic_query_for_dblp(self) -> None:
        translation = translate_for_dblp("('temporal' OR 'dynamic' OR 'animated') AND 'treemap'")

        self.assertEqual(translation.translated_query, "(temporal|dynamic|animated) treemap")
        self.assertEqual(translation.source, "dblp")
        self.assertTrue(any("1000" in note for note in translation.semantics_notes))

        without_not = translate_for_dblp("'treemap' NOT 'survey'")
        self.assertEqual(without_not.translated_query, "treemap")
        self.assertTrue(any("NOT terms were omitted" in note for note in without_not.semantics_notes))

    def test_normalize_dblp_api_response(self) -> None:
        page = {
            "result": {
                "hits": {
                    "@total": "1",
                    "@sent": "1",
                    "@first": "0",
                    "hit": [
                        {
                            "@id": "1617109",
                            "info": {
                                "authors": {
                                    "author": [
                                        {"@pid": "198/9374", "text": "Chang Han"},
                                        {"@pid": "153/7495", "text": "Jaemin Jo"},
                                    ]
                                },
                                "title": "SizePairs: Achieving Stable and Balanced Temporal Treemaps using Hierarchical Size-based Pairing.",
                                "venue": "IEEE Trans. Vis. Comput. Graph.",
                                "volume": "29",
                                "number": "1",
                                "pages": "193-202",
                                "year": "2023",
                                "type": "Journal Articles",
                                "key": "journals/tvcg/HanJLLDW23",
                                "doi": "10.1109/TVCG.2022.3209450",
                                "ee": "https://doi.org/10.1109/TVCG.2022.3209450",
                                "url": "https://dblp.org/rec/journals/tvcg/HanJLLDW23",
                            },
                            "url": "URL#1617109",
                        }
                    ],
                }
            }
        }

        records = normalize_dblp_pages([page])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "dblp")
        self.assertEqual(records[0]["source_record_id"], "journals/tvcg/HanJLLDW23")
        self.assertEqual(records[0]["authors"], "Chang Han; Jaemin Jo")
        self.assertEqual(records[0]["doi"], "10.1109/tvcg.2022.3209450")
        self.assertEqual(records[0]["canonical_url"], "https://doi.org/10.1109/TVCG.2022.3209450")
        self.assertEqual(records[0]["source_api_url"], "https://dblp.org/rec/journals/tvcg/HanJLLDW23")

        candidates = deduplicate(records)
        assign_candidate_ids(candidates)
        self.assertEqual(candidates[0]["candidate_id"], "han2023sizepairsachievingstable")

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

    def test_validation_report_flags_suspected_export_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "validation_report.md"
            write_validation_report(
                report,
                source_reported_count=1267,
                imported_count=1000,
                deduplicated_count=1000,
                max_results=None,
                candidates=[],
            )

            text = report.read_text(encoding="utf-8")
            self.assertIn("Count mismatch: warning", text)
            self.assertIn("Suspected export cap", text)

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

    def test_parse_acm_bibtex_and_ris_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bib = root / "acm.bib"
            bib.write_text(
                """@inproceedings{10.1145/1234567.1234568,
  author = {Smith, Ada and Doe, Ben},
  title = {Dynamic Treemaps Revisited},
  year = {2024},
  booktitle = {Proceedings of the ACM Test Conference},
  doi = {10.1145/1234567.1234568},
  pages = {1--10},
  publisher = {Association for Computing Machinery}
}
""",
                encoding="utf-8",
            )
            records = parse_acm_export(bib, "bibtex")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "acm")
            self.assertEqual(records[0]["doi"], "10.1145/1234567.1234568")
            self.assertEqual(records[0]["canonical_url"], "https://dl.acm.org/doi/10.1145/1234567.1234568")
            self.assertEqual(records[0]["authors"], "Smith, Ada; Doe, Ben")

            ris = root / "acm.ris"
            ris.write_text(
                """TY  - CPAPER
AU  - Smith, Ada
AU  - Doe, Ben
TI  - Dynamic Treemaps Revisited
PY  - 2024
DO  - 10.1145/1234567.1234568
T2  - Proceedings of the ACM Test Conference
SP  - 1
EP  - 10
PB  - Association for Computing Machinery
ER  -
""",
                encoding="utf-8",
            )
            records = parse_acm_export(ris, "ris")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["pages"], "1-10")
            self.assertEqual(records[0]["venue"], "Proceedings of the ACM Test Conference")

    def test_acm_bibtex_latex_accents_normalize_for_candidate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bib = root / "acm.bib"
            bib.write_text(
                r"""@inproceedings{10.1145/9999999,
  author = {D{\"o}llner, J{\"u}rgen and S{\u{a}}lcianu, Alexandru and Maga\~na, Maria},
  title = {A&nbsp;Treemap Study},
  year = {2024},
  booktitle = {Proceedings of the ACM Test Conference},
  doi = {10.1145/9999999}
}
""",
                encoding="utf-8",
            )
            records = parse_acm_export(bib, "bibtex")
            self.assertEqual(records[0]["authors"], "D\u00f6llner, J\u00fcrgen; S\u0103lcianu, Alexandru; Maga\u00f1a, Maria")
            self.assertEqual(records[0]["title"], "A Treemap Study")

            candidates = deduplicate(records)
            assign_candidate_ids(candidates)
            self.assertEqual(candidates[0]["candidate_id"], "dollner2024treemapstudy")

    def test_prepare_and_import_acm_manual_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                export = root / "downloaded-acm.bib"
                export.write_text(
                    """@inproceedings{10.1145/1234567.1234568,
  author = {Smith, Ada and Doe, Ben},
  title = {Dynamic Treemaps Revisited},
  year = {2024},
  booktitle = {Proceedings of the ACM Test Conference},
  doi = {10.1145/1234567.1234568},
  pages = {1--10},
  publisher = {Association for Computing Machinery}
}
""",
                    encoding="utf-8",
                )

                prepared = prepare_acm_search(
                    generic_query="('dynamic') AND 'treemap'",
                    slug="acm-test",
                    run_date="2026-06-19",
                )
                self.assertEqual(prepared.status, "manual_export_required")
                self.assertTrue((prepared.run_dir / "query.md").exists())

                imported = import_acm_export(
                    run_dir=prepared.run_dir,
                    export_path=export,
                    export_format="bibtex",
                    reported_count=1,
                    source_url="https://dl.acm.org/action/doSearch?AllField=dynamic+AND+treemap",
                )
                self.assertEqual(imported.status, "ok")
                self.assertEqual(imported.imported_count, 1)
                self.assertTrue((prepared.run_dir / "sources" / "acm" / "raw" / export.name).exists())

                reimported = import_acm_export(
                    run_dir=prepared.run_dir,
                    export_path=imported.raw_export_path,
                    export_format="bibtex",
                    reported_count=1,
                )
                self.assertEqual(reimported.imported_count, 1)

                candidates = (prepared.run_dir / "merged_candidates.csv").read_text(encoding="utf-8")
                self.assertIn("smith2024dynamictreemapsrevisited", candidates)
                bibtex = (root / "library" / "bibtex" / "candidates.bib").read_text(encoding="utf-8")
                self.assertIn("@inproceedings{smith2024dynamictreemapsrevisited,", bibtex)
            finally:
                import os

                os.chdir(old_cwd)

    def test_normalize_springer_metadata_records(self) -> None:
        page = {
            "result": [{"total": "1", "start": "1", "pageLength": "1", "recordsDisplayed": "1"}],
            "records": [
                {
                    "identifier": "doi:10.1007/978-3-030-12345-6_7",
                    "title": "Dynamic Treemap Layouts",
                    "creators": [{"creator": "Smith, Ada"}, {"creator": "Doe, Ben"}],
                    "publicationDate": "2024-05-01",
                    "doi": "10.1007/978-3-030-12345-6_7",
                    "url": [
                        {
                            "format": "html",
                            "platform": "springer",
                            "value": "https://link.springer.com/chapter/10.1007/978-3-030-12345-6_7",
                        }
                    ],
                    "publicationName": "Lecture Notes in Computer Science",
                    "publisher": "Springer",
                    "startingPage": "10",
                    "endingPage": "20",
                    "volume": "12345",
                }
            ],
        }

        records = springer.normalize_pages([page])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "springer")
        self.assertEqual(records[0]["authors"], "Smith, Ada; Doe, Ben")
        self.assertEqual(records[0]["year"], "2024")
        self.assertEqual(records[0]["pages"], "10-20")
        self.assertEqual(records[0]["canonical_url"], "https://link.springer.com/chapter/10.1007/978-3-030-12345-6_7")

    def test_springer_missing_credentials_writes_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            import os

            old_key = os.environ.pop("SPRINGER_API_KEY", None)
            try:
                os.chdir(root)
                result = springer.run_springer_search(
                    generic_query="('dynamic') AND 'treemap'",
                    slug="springer-test",
                    run_date="2026-06-19",
                )

                self.assertEqual(result.status, "missing_credentials")
                manifest = (result.run_dir / "sources" / "springer" / "source_manifest.json").read_text(encoding="utf-8")
                self.assertIn('"status": "missing_credentials"', manifest)
                self.assertIn('"api_key_env": "SPRINGER_API_KEY"', manifest)
                self.assertNotIn("api_key=", manifest)
            finally:
                os.chdir(old_cwd)
                if old_key is not None:
                    os.environ["SPRINGER_API_KEY"] = old_key

    def test_parse_springer_bibtex_and_ris_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bib = root / "springer.bib"
            bib.write_text(
                """@inproceedings{10.1007/test,
  author = {Smith, Ada and Doe, Ben},
  title = {Dynamic Treemap Layouts},
  year = {2024},
  booktitle = {Lecture Notes in Computer Science},
  doi = {10.1007/test},
  url = {https://link.springer.com/chapter/10.1007/test},
  pages = {10--20},
  publisher = {Springer}
}
""",
                encoding="utf-8",
            )
            records = springer.parse_springer_export(bib, "bibtex")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "springer")
            self.assertEqual(records[0]["doi"], "10.1007/test")
            self.assertEqual(records[0]["canonical_url"], "https://link.springer.com/chapter/10.1007/test")
            self.assertEqual(records[0]["authors"], "Smith, Ada; Doe, Ben")

            ris = root / "springer.ris"
            ris.write_text(
                """TY  - CPAPER
AU  - Smith, Ada
AU  - Doe, Ben
TI  - Dynamic Treemap Layouts
PY  - 2024
DO  - 10.1007/test
UR  - https://link.springer.com/chapter/10.1007/test
T2  - Lecture Notes in Computer Science
SP  - 10
EP  - 20
PB  - Springer
ER  -
""",
                encoding="utf-8",
            )
            records = springer.parse_springer_export(ris, "ris")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["pages"], "10-20")
            self.assertEqual(records[0]["venue"], "Lecture Notes in Computer Science")

    def test_parse_springer_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "springer.csv"
            export.write_text(
                """Item Title,Publication Title,Book Series Title,Journal Volume,Journal Issue,Item DOI,Authors,Publication Year,URL,Content Type
Understanding transitions in animated bar charts,Visual Intelligence,,,,10.1007/s44267-023-00015-w,Datong WeiCan LiuXiaolong (Luke) ZhangXiaoru Yuan,2023,https://link.springer.com/article/10.1007/s44267-023-00015-w,Article
Procedural texture patterns for encoding changes in color in 2.5D treemap visualizations,Journal of Visualization,,,,10.1007/s12650-022-00874-3,Daniel LimbergerWilly ScheibelJan van DiekenJ\u00fcrgen D\u00f6llner,2022,https://link.springer.com/article/10.1007/s12650-022-00874-3,Article
\"TreeMap 2016 Dataset Generates CONUS-Wide Maps of Forest Characteristics Including Live Basal Area, Aboveground Carbon, and Number of Trees per Acre\",Journal of Forestry,,,,10.1093/jofore/fvac022,Karin L RileyIsaac C GrenfellJohn D ShawMark A Finney,2022,https://link.springer.com/article/10.1093/jofore/fvac022,Article
""",
                encoding="utf-8",
            )

            self.assertEqual(springer.detect_export_format(export, "auto"), "csv")
            records = springer.parse_springer_export(export, "csv")
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0]["source"], "springer")
            self.assertEqual(records[0]["title"], "Understanding transitions in animated bar charts")
            self.assertEqual(records[0]["venue"], "Visual Intelligence")
            self.assertEqual(records[0]["doi"], "10.1007/s44267-023-00015-w")
            self.assertEqual(records[0]["year"], "2023")
            self.assertEqual(records[0]["canonical_url"], "https://link.springer.com/article/10.1007/s44267-023-00015-w")
            self.assertEqual(records[0]["authors"], "Datong Wei; Can Liu; Xiaolong (Luke) Zhang; Xiaoru Yuan")
            self.assertEqual(records[1]["authors"], "Daniel Limberger; Willy Scheibel; Jan van Dieken; J\u00fcrgen D\u00f6llner")

    def test_prepare_and_import_springer_manual_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                export = root / "downloaded-springer.bib"
                export.write_text(
                    """@inproceedings{10.1007/test,
  author = {Smith, Ada and Doe, Ben},
  title = {Dynamic Treemap Layouts},
  year = {2024},
  booktitle = {Lecture Notes in Computer Science},
  doi = {10.1007/test},
  url = {https://link.springer.com/chapter/10.1007/test},
  pages = {10--20},
  publisher = {Springer}
}
""",
                    encoding="utf-8",
                )

                prepared = springer.prepare_springer_manual_search(
                    generic_query="('dynamic') AND 'treemap'",
                    slug="springer-manual-test",
                    run_date="2026-06-19",
                )
                self.assertEqual(prepared.status, "manual_export_required")
                self.assertTrue((prepared.run_dir / "query.md").exists())

                imported = springer.import_springer_export(
                    run_dir=prepared.run_dir,
                    export_path=export,
                    export_format="bibtex",
                    reported_count=1,
                    source_url="https://link.springer.com/search?query=dynamic+AND+treemap",
                )
                self.assertEqual(imported.status, "ok")
                self.assertEqual(imported.imported_count, 1)
                self.assertTrue((prepared.run_dir / "sources" / "springer" / "raw" / export.name).exists())

                candidates = (prepared.run_dir / "merged_candidates.csv").read_text(encoding="utf-8")
                self.assertIn("smith2024dynamictreemaplayouts", candidates)
                manifest = (prepared.run_dir / "sources" / "springer" / "source_manifest.json").read_text(encoding="utf-8")
                self.assertIn('"manual_export": true', manifest)
                self.assertIn('"export_format": "bibtex"', manifest)
                bibtex = (root / "library" / "bibtex" / "candidates.bib").read_text(encoding="utf-8")
                self.assertIn("@inproceedings{smith2024dynamictreemaplayouts,", bibtex)
            finally:
                import os

                os.chdir(old_cwd)

    def test_prepare_and_import_springer_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            try:
                import os

                os.chdir(root)
                export = root / "downloaded-springer.csv"
                export.write_text(
                    """Item Title,Publication Title,Book Series Title,Journal Volume,Journal Issue,Item DOI,Authors,Publication Year,URL,Content Type
Procedural texture patterns for encoding changes in color in 2.5D treemap visualizations,Journal of Visualization,,,,10.1007/s12650-022-00874-3,Daniel LimbergerWilly ScheibelJan van DiekenJ\u00fcrgen D\u00f6llner,2022,https://link.springer.com/article/10.1007/s12650-022-00874-3,Article
""",
                    encoding="utf-8",
                )

                prepared = springer.prepare_springer_manual_search(
                    generic_query="('dynamic') AND 'treemap'",
                    slug="springer-csv-test",
                    run_date="2026-06-19",
                )
                imported = springer.import_springer_export(
                    run_dir=prepared.run_dir,
                    export_path=export,
                    export_format="auto",
                    reported_count=1,
                    source_url="https://link.springer.com/search?query=dynamic+AND+treemap",
                )

                self.assertEqual(imported.status, "ok")
                self.assertEqual(imported.imported_count, 1)
                candidates = (prepared.run_dir / "merged_candidates.csv").read_text(encoding="utf-8")
                self.assertIn("limberger2022proceduraltexturepatterns", candidates)
                manifest = (prepared.run_dir / "sources" / "springer" / "source_manifest.json").read_text(encoding="utf-8")
                self.assertIn('"export_format": "csv"', manifest)
                results = (prepared.run_dir / "sources" / "springer" / "springer_results.csv").read_text(encoding="utf-8")
                self.assertIn("Daniel Limberger; Willy Scheibel; Jan van Dieken; J\u00fcrgen D\u00f6llner", results)
            finally:
                import os

                os.chdir(old_cwd)

    def test_run_springer_search_with_mocked_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            import os

            old_key = os.environ.get("SPRINGER_API_KEY")
            old_fetch = springer.fetch_springer_json
            try:
                os.chdir(root)
                os.environ["SPRINGER_API_KEY"] = "secret-test-key"

                def fake_fetch(url: str) -> dict[str, object]:
                    self.assertIn("api_key=secret-test-key", url)
                    return {
                        "apiKey": "secret-test-key",
                        "result": [{"total": "1", "start": "1", "pageLength": "1", "recordsDisplayed": "1"}],
                        "records": [
                            {
                                "identifier": "doi:10.1007/test",
                                "title": "Dynamic Treemap Layouts",
                                "creators": [{"creator": "Smith, Ada"}],
                                "publicationDate": "2024",
                                "doi": "10.1007/test",
                                "url": [
                                    {
                                        "format": "html",
                                        "platform": "springer",
                                        "value": "https://link.springer.com/article/10.1007/test",
                                    }
                                ],
                                "publicationName": "Springer Test Journal",
                                "publisher": "Springer",
                            }
                        ],
                    }

                springer.fetch_springer_json = fake_fetch
                result = springer.run_springer_search(
                    generic_query="('dynamic') AND 'treemap'",
                    slug="springer-test",
                    run_date="2026-06-19",
                    page_size=1,
                )

                self.assertEqual(result.status, "ok")
                self.assertEqual(result.imported_count, 1)
                raw = (result.run_dir / "sources" / "springer" / "raw" / "page_0000.json").read_text(encoding="utf-8")
                self.assertIn('"apiKey": "<redacted>"', raw)
                self.assertNotIn("secret-test-key", raw)
                manifest = (result.run_dir / "sources" / "springer" / "source_manifest.json").read_text(encoding="utf-8")
                self.assertIn("api_key=%3Credacted%3E", manifest)
                self.assertNotIn("secret-test-key", manifest)
                candidates = (result.run_dir / "merged_candidates.csv").read_text(encoding="utf-8")
                self.assertIn("smith2024dynamictreemaplayouts", candidates)
            finally:
                springer.fetch_springer_json = old_fetch
                os.chdir(old_cwd)
                if old_key is None:
                    os.environ.pop("SPRINGER_API_KEY", None)
                else:
                    os.environ["SPRINGER_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()

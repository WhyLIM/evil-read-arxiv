import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import search_biomed_papers as sbp


class SearchBiomedPapersTests(unittest.TestCase):
    def test_deduplicate_prefers_pmid_then_doi_then_title(self):
        papers = [
            {
                "pmid": "100",
                "doi": "10.1/a",
                "title": "Cancer Immunotherapy in Practice",
                "abstract": "short",
            },
            {
                "pmid": "100",
                "doi": "10.1/a",
                "title": "Cancer Immunotherapy in Practice",
                "abstract": "longer abstract",
            },
            {
                "pmid": "",
                "doi": "10.2/b",
                "title": "Tumor microenvironment study",
            },
            {
                "pmid": "",
                "doi": "10.2/b",
                "title": "Tumor microenvironment study",
                "abstract": "has abstract",
            },
            {
                "pmid": "",
                "doi": "",
                "title": "Single-cell atlas of disease",
            },
            {
                "pmid": "",
                "doi": "",
                "title": "Single cell atlas of disease",
                "abstract": "title-normalized duplicate",
            },
        ]

        deduped = sbp.deduplicate_papers(papers)

        self.assertEqual(len(deduped), 3)
        self.assertEqual(deduped[0]["abstract"], "longer abstract")
        self.assertEqual(deduped[1]["abstract"], "has abstract")
        self.assertEqual(deduped[2]["abstract"], "title-normalized duplicate")

    def test_score_paper_rewards_relevance_recency_and_open_access(self):
        paper = {
            "title": "Cancer immunotherapy for melanoma",
            "abstract": "Tumor microenvironment response and cancer immunotherapy signals.",
            "publication_date": "2026-04-01",
            "is_open_access": True,
            "article_types": ["clinical trial"],
        }
        config = {
            "keywords": ["cancer immunotherapy", "tumor microenvironment"],
            "article_types": ["clinical trial"],
        }

        score = sbp.score_paper(paper, config, current_date="2026-04-10")

        self.assertGreater(score["relevance"], 0)
        self.assertGreater(score["recency"], 0)
        self.assertGreater(score["access"], 0)
        self.assertGreater(score["article_type"], 0)
        self.assertGreater(score["total"], 0)

    def test_build_markdown_note_renders_core_fields(self):
        papers = [
            {
                "title": "Cancer Immunotherapy for Melanoma",
                "authors": ["A. Author", "B. Author"],
                "journal": "Nature Medicine",
                "publication_date": "2026-04-01",
                "pmid": "12345",
                "pmcid": "PMC12345",
                "doi": "10.1000/test",
                "is_open_access": True,
                "source": "pubmed+europepmc",
                "summary_line": "A concise biomedical summary.",
                "scores": {"total": 8.5},
            }
        ]

        note = sbp.build_markdown_note(
            papers=papers,
            config={"keywords": ["cancer immunotherapy"]},
            target_date="2026-04-10",
            lookback_days=90,
            total_found=12,
            total_filtered=1,
        )

        self.assertIn("# Biomedical Paper Recommendations", note)
        self.assertIn("Cancer Immunotherapy for Melanoma", note)
        self.assertIn("PMID", note)
        self.assertIn("Europe PMC", note)
        self.assertIn("cancer immunotherapy", note)

    def test_compute_recency_accepts_pubmed_style_month_dates(self):
        score = sbp.compute_recency("2026 Dec", "2026-04-10")
        self.assertGreater(score, 0)

    def test_summarize_paper_strips_html_tags(self):
        paper = {
            "abstract": "<h4>Background</h4>Immune response <sup>2</sup> improves outcomes."
        }
        summary = sbp.summarize_paper(paper)
        self.assertEqual(summary, "BackgroundImmune response 2 improves outcomes.")

    def test_serialize_result_for_stdout_uses_ascii_for_gbk(self):
        payload = {"text": "CD8⁺ T cells"}
        rendered = sbp.serialize_result_for_stdout(payload, encoding="gbk")
        self.assertIn("\\u207a", rendered)

    def test_extract_pubmed_article_details_prefers_electronic_date(self):
        article_xml = """
        <PubmedArticle>
          <MedlineCitation>
            <PMID>41928453</PMID>
            <Article>
              <Journal>
                <JournalIssue>
                  <PubDate>
                    <Year>2026</Year><Month>Dec</Month><Day>31</Day>
                  </PubDate>
                </JournalIssue>
              </Journal>
              <Abstract>
                <AbstractText>Example abstract.</AbstractText>
              </Abstract>
              <ArticleDate DateType="Electronic">
                <Year>2026</Year><Month>04</Month><Day>02</Day>
              </ArticleDate>
            </Article>
          </MedlineCitation>
        </PubmedArticle>
        """

        details = sbp.extract_pubmed_article_details(article_xml)

        self.assertEqual(details["pmid"], "41928453")
        self.assertEqual(details["abstract"], "Example abstract.")
        self.assertEqual(details["publication_date"], "2026-04-02")
        self.assertEqual(details["publication_date_source"], "electronic")
        self.assertEqual(details["issue_publication_date"], "2026-12-31")

    def test_format_pubmed_display_date_preserves_full_day(self):
        self.assertEqual(sbp.format_pubmed_display_date("2026-12-31"), "2026 Dec 31")
        self.assertEqual(sbp.format_pubmed_display_date("2026-04-02"), "2026 Apr 2")

    def test_normalize_text_unescapes_entities_and_normalizes_unicode(self):
        text = "B&uuml;ll C and P\u0065\u0301rez-Ruiz E"
        normalized = sbp.normalize_text(text)
        self.assertEqual(normalized, "Büll C and Pérez-Ruiz E")

    def test_clean_html_text_removes_tags_and_keeps_unicode(self):
        text = "Sialic acid <i>cis</i>-ligand and IFN-<i>α</i> with CD4⁺ cells"
        cleaned = sbp.clean_html_text(text)
        self.assertEqual(cleaned, "Sialic acid cis-ligand and IFN-α with CD4⁺ cells")

    def test_parse_keyword_overrides_accepts_commas_and_repeated_flags(self):
        keywords = sbp.parse_keyword_overrides([
            "single-cell lung cancer, tumor microenvironment",
            "immune checkpoint inhibitor",
        ])

        self.assertEqual(
            keywords,
            [
                "single-cell lung cancer",
                "tumor microenvironment",
                "immune checkpoint inhibitor",
            ],
        )

    def test_build_markdown_note_shows_epub_and_issue_dates(self):
        papers = [
            {
                "title": "Example paper",
                "authors": ["A. Author"],
                "journal": "Example Journal",
                "publication_date": "2026-04-02",
                "publication_date_source": "electronic",
                "issue_publication_date": "2026-12-31",
                "pmid": "12345",
                "summary_line": "Summary.",
                "scores": {"total": 8.5},
            }
        ]

        note = sbp.build_markdown_note(
            papers=papers,
            config={"keywords": ["tumor microenvironment"]},
            target_date="2026-04-10",
            lookback_days=90,
            total_found=1,
            total_filtered=1,
        )

        self.assertIn("- Date: 2026 Apr 2 (Epub)", note)
        self.assertIn("- Issue date: 2026 Dec 31", note)


if __name__ == "__main__":
    unittest.main()

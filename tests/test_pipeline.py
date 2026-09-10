import importlib.util
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location("literature_rss", os.path.join(ROOT, "literature_rss.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as handle:
            self.config = json.load(handle)

    def test_relevant_paper_outscores_derived_carbon_noise(self):
        with open(os.path.join(ROOT, "tests", "fixture.json"), encoding="utf-8") as handle:
            papers = json.load(handle)
        relevant = MODULE.score_paper(papers[0], self.config)
        noise = MODULE.score_paper(papers[1], self.config)
        self.assertGreater(relevant["score"], noise["score"])
        self.assertGreater(relevant["score"], self.config["search"]["minimum_score"])

    def test_fixture_run_writes_valid_rss(self):
        with tempfile.TemporaryDirectory() as output:
            archive = os.path.join(output, "issues")
            code = MODULE.run(os.path.join(ROOT, "config.json"), output, os.path.join(ROOT, "tests", "fixture.json"), "2026-07-20", archive)
            self.assertEqual(code, 0)
            tree = ET.parse(os.path.join(output, "feed.xml"))
            items = tree.findall("./channel/item")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].findtext("title"), "2026-07-20 文献日报｜1篇")
            description = items[0].findtext("description")
            self.assertIn("精简速览", description)
            self.assertIn("重要研究内容", description)
            self.assertIn("关键发现", description)
            self.assertIn("证据边界", description)
            self.assertIn("10.0000/example.1", description)
            issue_path = os.path.join(archive, "2026", "2026-07-20.md")
            self.assertTrue(os.path.exists(issue_path))
            with open(issue_path, encoding="utf-8") as handle:
                issue = handle.read()
            self.assertIn("10.0000/example.1", issue)
            self.assertIn("### 精简速览", issue)
            self.assertIn("### 重要研究内容", issue)
            self.assertIn("### 关键发现", issue)
            self.assertIn("### 证据边界", issue)
            with open(os.path.join(archive, "README.md"), encoding="utf-8") as handle:
                index = handle.read()
            self.assertIn("2026-07-20 文献日报（1篇）", index)

    def test_empty_day_still_writes_one_digest(self):
        with tempfile.TemporaryDirectory() as output:
            MODULE.run(os.path.join(ROOT, "config.json"), output, os.path.join(ROOT, "tests", "fixture.json"), "2026-07-19", os.path.join(output, "issues"))
            tree = ET.parse(os.path.join(output, "feed.xml"))
            items = tree.findall("./channel/item")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].findtext("title"), "2026-07-19 文献日报｜0篇")

    def test_expanded_topics_require_quality_or_method_signal(self):
        with open(os.path.join(ROOT, "tests", "expanded_fixture.json"), encoding="utf-8") as handle:
            papers = json.load(handle)
        classified = {}
        for paper in papers:
            scored = MODULE.score_paper(paper, self.config)
            classified[paper["doi"]] = MODULE.classify_paper(scored, self.config)
        self.assertEqual(classified["10.0000/hof.1"], "高质量框架推荐")
        self.assertEqual(classified["10.0000/meso.1"], "拓展推荐")
        self.assertEqual(classified["10.0000/assembly.1"], "拓展推荐")
        self.assertEqual(classified["10.0000/cof.1"], "拓展推荐")
        self.assertEqual(classified["10.0000/noise.2"], "")

    def test_expanded_daily_digest_caps_adjacent_papers(self):
        with tempfile.TemporaryDirectory() as output:
            archive = os.path.join(output, "issues")
            MODULE.run(
                os.path.join(ROOT, "config.json"),
                output,
                os.path.join(ROOT, "tests", "expanded_fixture.json"),
                "2026-07-26",
                archive
            )
            with open(os.path.join(output, "digests.json"), encoding="utf-8") as handle:
                digest = json.load(handle)[0]
            self.assertEqual(len(digest["papers"]), 4)
            tiers = [p["selection_tier"] for p in digest["papers"]]
            self.assertEqual(tiers.count("高质量框架推荐"), 1)
            self.assertEqual(tiers.count("拓展推荐"), 3)

    def test_premium_ionic_framework_paper_is_not_missed(self):
        paper = MODULE.score_paper({
            "title": "Ionic clusters as high-connectivity secondary building units for crystalline porous organic salts",
            "abstract": (
                "The solid-state assembly of crystalline porous materials is dictated by node-linker interactions. "
                "Ammonium halide ion pairs undergo Coulombic aggregation into ionic clusters that function as "
                "secondary building units for non-metal organic frameworks with large voids and polar channels."
            ),
            "authors": [],
            "institutions": [],
            "published": "2026-09-08",
            "journal": "Nature Chemistry",
            "doi": "10.1038/s41557-026-02248-w",
            "sources": ["Fixture"]
        }, self.config)
        self.assertIn("porous organic salts", paper["hits"]["framework"])
        self.assertIn("solid-state assembly", paper["hits"]["assembly"])
        self.assertEqual(MODULE.classify_paper(paper, self.config), "核心推荐")

    def test_crossref_journal_watch_does_not_require_title_keywords(self):
        response = {"message": {"items": [{
            "DOI": "10.1038/s41557-026-02248-w",
            "title": ["Ionic clusters as high-connectivity secondary building units"],
            "container-title": ["Nature Chemistry"],
            "published-online": {"date-parts": [[2026, 9, 8]]},
            "author": [],
            "URL": "https://doi.org/10.1038/s41557-026-02248-w"
        }]}}
        with mock.patch.object(MODULE, "http_get_json", return_value=response) as getter:
            papers = MODULE.search_crossref_journal("Nature Chemistry", "2026-09-06", "2026-09-08", 40, self.config)
        self.assertEqual(papers[0]["doi"], "10.1038/s41557-026-02248-w")
        requested_url = getter.call_args[0][0]
        self.assertIn("query.container-title=Nature+Chemistry", requested_url)
        self.assertNotIn("query.title", requested_url)

    def test_late_arrival_is_added_once(self):
        paper = [{
            "title": "Late indexed porous organic salt assembly",
            "abstract": "A crystalline porous organic salt forms through solid-state assembly.",
            "authors": [], "institutions": [], "published": "2026-07-25",
            "journal": "Nature Chemistry", "doi": "10.0000/late.1", "sources": ["Fixture"]
        }]
        with tempfile.TemporaryDirectory() as output:
            fixture = os.path.join(output, "late.json")
            with open(fixture, "w", encoding="utf-8") as handle:
                json.dump(paper, handle)
            archive = os.path.join(output, "issues")
            MODULE.run(os.path.join(ROOT, "config.json"), output, fixture, "2026-07-26", archive)
            with open(os.path.join(output, "digests.json"), encoding="utf-8") as handle:
                first = json.load(handle)[0]
            self.assertEqual(len(first["papers"]), 1)
            self.assertTrue(first["papers"][0]["late_arrival"])
            MODULE.run(os.path.join(ROOT, "config.json"), output, fixture, "2026-07-27", archive)
            with open(os.path.join(output, "digests.json"), encoding="utf-8") as handle:
                second = json.load(handle)[0]
            self.assertEqual(len(second["papers"]), 0)

    def test_evidence_limited_fallback_has_detailed_delivery_fields(self):
        paper = MODULE.score_paper({
            "title": "Mesoporous MOF Assembly",
            "abstract": "",
            "authors": [],
            "institutions": [],
            "published": "2026-07-26",
            "journal": "Example Journal",
            "doi": "10.0000/fallback.1",
            "sources": ["fixture"]
        }, self.config)
        content = MODULE.fallback_chinese_content(paper)
        self.assertTrue(content["summary_zh"])
        self.assertTrue(content["research_details_zh"])
        self.assertIsInstance(content["key_findings_zh"], list)
        self.assertIn("仅元数据级记录", content["evidence_note_zh"])

    def test_fallback_extracts_available_abstract_instead_of_generic_template(self):
        paper = MODULE.score_paper({
            "title": "Mesoporous MOF Assembly",
            "abstract": "We synthesized a mesoporous MOF by surfactant-directed assembly. The material exhibited a 35% increase in conductivity and retained crystallinity.",
            "authors": [], "institutions": [], "published": "2026-08-19",
            "journal": "Example Journal", "doi": "10.0000/fallback.2", "sources": ["fixture"]
        }, self.config)
        content = MODULE.fallback_chinese_content(paper)
        self.assertIn("surfactant-directed assembly", content["research_details_zh"])
        self.assertTrue(any("35%" in item for item in content["key_findings_zh"]))
        self.assertIn("自动摘录", content["evidence_note_zh"])

    def test_deepseek_chat_output_text_parser(self):
        response = {"choices": [{"message": {"content": '[{"doi":"10.1/test"}]'}}]}
        parsed = MODULE.parse_model_json(MODULE.model_response_text(response))
        self.assertEqual(parsed[0]["doi"], "10.1/test")


if __name__ == "__main__":
    unittest.main()

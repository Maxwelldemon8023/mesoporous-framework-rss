import importlib.util
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET


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
            self.assertIn("中文内容介绍", description)
            self.assertIn("10.0000/example.1", description)
            issue_path = os.path.join(archive, "2026", "2026-07-20.md")
            self.assertTrue(os.path.exists(issue_path))
            with open(issue_path, encoding="utf-8") as handle:
                issue = handle.read()
            self.assertIn("10.0000/example.1", issue)
            self.assertIn("中文内容介绍", issue)
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


if __name__ == "__main__":
    unittest.main()

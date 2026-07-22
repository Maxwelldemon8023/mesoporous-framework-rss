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
            code = MODULE.run(os.path.join(ROOT, "config.json"), output, os.path.join(ROOT, "tests", "fixture.json"))
            self.assertEqual(code, 0)
            tree = ET.parse(os.path.join(output, "feed.xml"))
            items = tree.findall("./channel/item")
            self.assertEqual(len(items), 1)
            self.assertIn("Electrochemical assembly", items[0].findtext("title"))


if __name__ == "__main__":
    unittest.main()

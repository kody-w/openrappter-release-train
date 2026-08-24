import json, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from candidate_url import CandidateUrlError, build_candidate_url, candidate_id_for_tag, parse_candidate_url

class CandidateUrlTests(unittest.TestCase):
    def setUp(self):
        self.contract=json.loads((ROOT/"contracts/candidate-url-v1.json").read_text())
        self.fixture=self.contract["fixture"]
    def test_exact_fixture_and_tag_encoding(self):
        self.assertEqual(candidate_id_for_tag("v1.13.0"),"tag-djEuMTMuMA")
        self.assertEqual(parse_candidate_url(self.fixture["url"]),{
            "ref":self.fixture["ref"],"source_commit":self.fixture["source_commit"],
            "kind":"release","candidate_id":self.fixture["candidate_id"],"sha256":self.fixture["sha256"],
        })
        self.assertEqual(build_candidate_url(self.fixture["ref"],self.fixture["source_commit"],"release",self.fixture["candidate_id"],self.fixture["sha256"]),self.fixture["url"])
    def test_all_injection_and_shape_variants_fail(self):
        valid=self.fixture["url"]
        bad=[
            valid+"?x=1",valid+"#x",valid.replace("raw.githubusercontent.com","evil.example"),
            valid.replace("raw.githubusercontent.com","RAW.GITHUBUSERCONTENT.COM"),
            valid.replace("raw.githubusercontent.com","raw.githubusercontent.com:443"),
            valid.replace("https://","https://user@"),valid.replace("/kody-w/openrappter/","/other/openrappter/"),
            valid.replace("/release/","/beta/"),valid.replace("/tag-djEuMTMuMA/","/../"),
            valid.replace("/tag-djEuMTMuMA/","/%2e%2e/"),valid.replace("/tag-djEuMTMuMA/","/täg/"),
            valid.replace("/tag-djEuMTMuMA/","/a/b/"),valid.replace(".tar.gz","/extra.tar.gz"),
            valid.replace(self.fixture["ref"],"main"),valid.replace(self.fixture["sha256"],"d"*63),
        ]
        for value in bad:
            with self.subTest(value=value),self.assertRaises((CandidateUrlError,ValueError)):
                parse_candidate_url(value)
if __name__=="__main__":unittest.main()

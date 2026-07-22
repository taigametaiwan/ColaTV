import json
import tempfile
import unittest
from pathlib import Path

import main


class MetadataTests(unittest.TestCase):
    def test_slug_with_random_suffix(self):
        value = main.slug_to_metadata(
            "https://colatv77.live/truc-tiep/bologna-fc-1909-vs-heidenheimer-luc-2100-ngay-22-07-2026-l6kegi82xrnbv75"
        )
        self.assertEqual(value["date"], "22/07/2026")
        self.assertEqual(value["time"], "21:00")
        self.assertEqual(value["home_name"], "Bologna Fc 1909")
        self.assertEqual(value["away_name"], "Heidenheimer")

    def test_discover_match_links_deduplicates(self):
        rows = main.discover_match_links_from_values(
            [
                "/truc-tiep/a-vs-b-luc-2100-ngay-22-07-2026-x",
                "https://colatv77.live/truc-tiep/a-vs-b-luc-2100-ngay-22-07-2026-x?utm=1",
                "/tin-tuc/abc",
            ],
            "https://colatv77.live/",
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("?", rows[0])

    def test_load_authorized_streams(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "authorized_streams.json"
            path.write_text(
                json.dumps(
                    {
                        "streams": [
                            {
                                "match_url": "https://example.com/truc-tiep/a-vs-b-luc-2100-ngay-22-07-2026-x",
                                "stream_url": "https://media.example.com/live/a.m3u8",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rows = main.load_authorized_streams(path)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].enabled)

    def test_logo_scoring_prefers_team(self):
        images = [
            {"url": "https://x/banner.jpg", "alt": "banner ads", "width": 1200, "height": 300},
            {"url": "https://x/bologna.png", "alt": "Bologna FC 1909 team logo", "width": 120, "height": 120},
        ]
        logo = main.choose_team_logo(images, "Bologna FC 1909", "home", set())
        self.assertEqual(logo, "https://x/bologna.png")


if __name__ == "__main__":
    unittest.main()

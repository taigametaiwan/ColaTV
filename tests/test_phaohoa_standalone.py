from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sources import phaohoa

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MATCH_URL = "https://phaohoa1.live/truc-tiep/malisheva-vs-hibernian-23-07-2026-776573"
SIMPLE_URL = "https://phaohoa1.live/truc-tiep/tho-nhi-ki-w-vs-canada-w"


class StandaloneTests(unittest.TestCase):
    def test_home_domain(self) -> None:
        self.assertEqual(phaohoa.HOME_URLS[0], "https://phaohoa1.live/")

    def test_dated_url(self) -> None:
        name, time_value, _ = phaohoa.derive_match_info(MATCH_URL)
        self.assertEqual(name, "Malisheva vs Hibernian")
        self.assertEqual(time_value, "")
        self.assertEqual(phaohoa.extract_date(MATCH_URL), "23/07")
        self.assertEqual(phaohoa.match_id_from_url(MATCH_URL), "776573")

    def test_short_route_has_stable_match_id_and_channel_id(self) -> None:
        match_id = phaohoa.match_id_from_url(SIMPLE_URL)
        self.assertRegex(match_id, r"^[a-f0-9]{12}$")
        result = {"url": SIMPLE_URL}
        placeholder_id = phaohoa.channel_id_for(result, SIMPLE_URL, 1)
        stream_id = phaohoa.channel_id_for(result, "https://cdn.example/live.m3u8", 1)
        self.assertEqual(placeholder_id, stream_id)

    def test_no_match_count_limit_for_metadata_playlist(self) -> None:
        rows = []
        for index in range(25):
            rows.append({
                "url": f"https://phaohoa1.live/truc-tiep/doi-{index}-vs-doi-{index + 1}",
                "raw_title": f"Đội {index} VS Đội {index + 1}",
                "raw_time": "20:00 - 23-07",
                "card_text": f"20:00 - 23-07 Bóng đá Đội {index} VS Sắp diễn ra Đội {index + 1} BLV {index}",
                "blv": f"BLV {index}",
                "home_logo": f"https://cdn.example/{index}-home.png",
                "away_logo": f"https://cdn.example/{index}-away.png",
                "sport_group": "Bóng đá",
                "streams": [],
            })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"), \
                 patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"), \
                 patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"), \
                 patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                matches, real_links, metadata_only = phaohoa.write_outputs(rows)
            content = (root / "phaohoa_live.m3u").read_text(encoding="utf-8")
            self.assertEqual((matches, real_links, metadata_only), (25, 0, 25))
            self.assertEqual(content.count("#EXTINF:"), 25)

    def test_card_schedule(self) -> None:
        rows = [{
            "url": SIMPLE_URL,
            "raw_title": "Thổ Nhĩ Kì (W) VS Canada (W)",
            "raw_time": "15:00 - 23-07",
            "card_text": "15:00 - 23-07 Bóng chuyền Nations League Thổ Nhĩ Kì (W) VS Sắp diễn ra Canada (W) KaKa",
        }]
        kept, _ = phaohoa.filter_links_by_scan_window(rows, now=datetime(2026, 7, 23, 14, 0, tzinfo=TZ))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["time"], "15:00")
        self.assertEqual(kept[0]["date"], "23/07")

    def test_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"), \
                 patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"), \
                 patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"), \
                 patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                phaohoa.write_outputs([])
            for name in ("phaohoa_live.m3u", "phaohoa_live_pipe.m3u", "phaohoa_live_vlc.m3u"):
                self.assertEqual((root / name).read_text(encoding="utf-8"), "#EXTM3U\n")

    def test_card_identity_keeps_accents_and_commentator(self) -> None:
        card = (
            "15:00 - 23-07 Bóng chuyền Nations League "
            "Thổ Nhĩ Kì (W) VS Sắp diễn ra Canada (W) KaKa"
        )
        identity = phaohoa.extract_card_identity(SIMPLE_URL, card, card)
        self.assertEqual(identity["match_name"], "Thổ Nhĩ Kì (W) VS Canada (W)")
        self.assertEqual(identity["home_name"], "Thổ Nhĩ Kì (W)")
        self.assertEqual(identity["away_name"], "Canada (W)")
        self.assertEqual(identity["blv"], "KaKa")

    def test_every_detected_match_is_written_without_stream(self) -> None:
        row = {
            "url": SIMPLE_URL,
            "raw_title": "Thổ Nhĩ Kì (W) VS Canada (W)",
            "raw_time": "15:00 - 23-07",
            "card_text": (
                "15:00 - 23-07 Bóng chuyền Nations League "
                "Thổ Nhĩ Kì (W) VS Sắp diễn ra Canada (W) KaKa"
            ),
            "blv": "KaKa",
            "home_logo": "https://cdn.example/home.png",
            "away_logo": "https://cdn.example/away.png",
            "team_logos": [
                "https://cdn.example/home.png",
                "https://cdn.example/away.png",
            ],
            "logo_candidates": [],
            "sport_group": "Bóng chuyền",
            "streams": [],
        }
        phaohoa.hydrate_discovered_match_metadata(
            row, now=datetime(2026, 7, 23, 12, 0, tzinfo=TZ)
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"), \
                 patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"), \
                 patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"), \
                 patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                matches, real_links, metadata_only = phaohoa.write_outputs([row])

            content = (root / "phaohoa_live.m3u").read_text(encoding="utf-8")
            self.assertEqual((matches, real_links, metadata_only), (1, 0, 1))
            self.assertIn("[15:00 23/07] Thổ Nhĩ Kì (W) VS Canada (W) [BLV KaKa]", content)
            self.assertIn('phaohoa-entry="metadata-only"', content)
            self.assertIn('phaohoa-home-logo="https://cdn.example/home.png"', content)
            self.assertIn('phaohoa-away-logo="https://cdn.example/away.png"', content)
            self.assertIn(f'phaohoa-page-url="{SIMPLE_URL}"', content)
            self.assertIn(
                "http://127.0.0.1:9/__phaohoa_metadata__/d42658950ab7.m3u8",
                content,
            )
            self.assertNotIn(f"\n{SIMPLE_URL}\n", content)
            self.assertNotIn("CHỜ PHÁT", content)

    def test_real_stream_replaces_metadata_placeholder(self) -> None:
        discovered = [{
            "url": SIMPLE_URL,
            "raw_title": "Thổ Nhĩ Kì (W) VS Canada (W)",
            "raw_time": "15:00 - 23-07",
            "card_text": "Thổ Nhĩ Kì (W) VS Sắp diễn ra Canada (W) KaKa",
            "blv": "KaKa",
            "home_logo": "https://cdn.example/home.png",
            "away_logo": "https://cdn.example/away.png",
            "sport_group": "Bóng chuyền",
        }]
        scanned = [{
            **discovered[0],
            "streams": [{
                "url": "https://cdn.example/live.m3u8",
                "content_type": "application/vnd.apple.mpegurl",
                "playability": "verified",
            }],
        }]
        merged = phaohoa.merge_discovered_with_scan_results(discovered, scanned)
        self.assertEqual(merged[0]["playlist_mode"], "stream")
        self.assertEqual(merged[0]["streams"][0]["url"], "https://cdn.example/live.m3u8")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"), \
                 patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"), \
                 patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"), \
                 patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                stats = phaohoa.write_outputs(merged)
            content = (root / "phaohoa_live.m3u").read_text(encoding="utf-8")
            self.assertEqual(stats, (1, 1, 0))
            self.assertIn('phaohoa-entry="stream"', content)
            self.assertNotIn('phaohoa-entry="metadata-only"', content)
            self.assertIn("https://cdn.example/live.m3u8", content)
            self.assertNotIn(f"\n{SIMPLE_URL}\n", content)

    def test_current_home_cards_keep_exact_names_and_blv(self) -> None:
        cases = [
            (
                "https://phaohoa1.live/truc-tiep/viet-nam-vs-thai-lan",
                "15:30 - 23-07 Bóng chuyền Sea V Cup Việt Nam VS Sắp diễn ra Thái Lan Chim Nhỏ",
                "Việt Nam VS Thái Lan",
                "Chim Nhỏ",
            ),
            (
                "https://phaohoa1.live/truc-tiep/my-w-vs-trung-quoc-w",
                "18:30 - 23-07 Bóng chuyền Nations League Mỹ (W) VS Sắp diễn ra Trung Quốc (W) KaKa",
                "Mỹ (W) VS Trung Quốc (W)",
                "KaKa",
            ),
            (
                "https://phaohoa1.live/truc-tiep/dyn-kyiv-vs-paok-24-07-2026-778921",
                "00:00 - 24-07 Bóng đá UEFA Europa League Dyn. Kyiv VS Sắp diễn ra PAOK Văn Minh",
                "Dyn. Kyiv VS PAOK",
                "Văn Minh",
            ),
            (
                "https://phaohoa1.live/truc-tiep/st-gallen-vs-benfica-24-07-2026-770777",
                "01:00 - 24-07 Bóng đá UEFA Europa League St. Gallen VS Sắp diễn ra Benfica Kevin",
                "St. Gallen VS Benfica",
                "Kevin",
            ),
        ]
        for url, card, expected_name, expected_blv in cases:
            with self.subTest(url=url):
                identity = phaohoa.extract_card_identity(url, card, card)
                self.assertEqual(identity["match_name"], expected_name)
                self.assertEqual(identity["blv"], expected_blv)

    def test_grid_text_is_not_misread_as_one_card(self) -> None:
        page_grid = (
            "15:00 - 23-07 BÓNG CHUYỀN Nations League Thổ Nhĩ Kì (W) "
            "VS Sắp diễn ra Canada (W) KaKa "
            "15:30 - 23-07 BÓNG CHUYỀN Sea V Cup Việt Nam "
            "VS Sắp diễn ra Thái Lan Chim Nhỏ "
            "18:30 - 23-07 BÓNG CHUYỀN Nations League Mỹ (W) "
            "VS Sắp diễn ra Trung Quốc (W) KaKa"
        )
        row = {
            "url": "https://phaohoa1.live/truc-tiep/viet-nam-vs-thai-lan",
            "raw_title": (
                "15:30 - 23-07 BÓNG CHUYỀN Sea V Cup Việt Nam "
                "VS Sắp diễn ra Thái Lan Chim Nhỏ"
            ),
            "card_text": page_grid,
            "home_logo": "https://phaohoa1.live/media/sports/icons/bchuyen.png",
            "away_logo": "https://phaohoa1.live/media/sports/icons/bóng_đá_tXyI0zo.png",
            "logo_candidates": [
                {
                    "url": "https://cdn.example/vietnam.png",
                    "context": "Việt Nam max-h-full team logo",
                    "source": "home-card",
                    "score": 5,
                },
                {
                    "url": "https://cdn.example/thailand.png",
                    "context": "Thái Lan max-h-full team logo",
                    "source": "home-card",
                    "score": 5,
                },
                {
                    "url": "https://phaohoa1.live/media/sports/icons/bchuyen.png",
                    "context": "BÓNG CHUYỀN sport icon",
                    "source": "home-card",
                    "score": 80,
                },
            ],
        }
        phaohoa.hydrate_discovered_match_metadata(
            row, now=datetime(2026, 7, 23, 14, 0, tzinfo=TZ)
        )
        self.assertEqual(row["card_text"], row["raw_title"])
        self.assertEqual(row["match_name"], "Việt Nam VS Thái Lan")
        self.assertEqual(row["blv"], "Chim Nhỏ")
        self.assertEqual(row["time"], "15:30")
        self.assertEqual(row["date"], "23/07")
        self.assertEqual(row["home_logo"], "https://cdn.example/vietnam.png")
        self.assertEqual(row["away_logo"], "https://cdn.example/thailand.png")

    def test_m3u_has_exactly_one_url_line_per_entry_and_no_match_page_url(self) -> None:
        rows = [
            {
                "url": f"https://phaohoa1.live/truc-tiep/doi-{index}-vs-doi-{index + 1}",
                "raw_title": f"20:0{index} - 23-07 BÓNG ĐÁ Đội {index} VS Sắp diễn ra Đội {index + 1} BLV {index}",
                "card_text": f"20:0{index} - 23-07 BÓNG ĐÁ Đội {index} VS Sắp diễn ra Đội {index + 1} BLV {index}",
                "sport_group": "Bóng đá",
                "streams": [],
            }
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"),                  patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"),                  patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"),                  patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                phaohoa.write_outputs(rows)
            lines = (root / "phaohoa_live.m3u").read_text(encoding="utf-8").splitlines()
        extinf = [line for line in lines if line.startswith("#EXTINF:")]
        urls = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(len(extinf), 3)
        self.assertEqual(len(urls), 3)
        self.assertTrue(all(url.startswith("http://127.0.0.1:9/__phaohoa_metadata__/") for url in urls))
        self.assertTrue(all(url.endswith(".m3u8") for url in urls))
        self.assertFalse(any("phaohoa1.live/truc-tiep/" in url for url in urls))
        self.assertTrue(all("\n" not in line and "\r" not in line for line in lines))

    def test_unicode_line_separator_is_removed_from_m3u_name(self) -> None:
        row = {
            "url": "https://phaohoa1.live/truc-tiep/dyn-kyiv-vs-paok-24-07-2026-778921",
            "raw_title": "00:00 - 24-07 BÓNG ĐÁ Dyn.\u2028Kyiv VS Sắp diễn ra PAOK Văn Minh",
            "card_text": "00:00 - 24-07 BÓNG ĐÁ Dyn.\u2028Kyiv VS Sắp diễn ra PAOK Văn Minh",
            "streams": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(phaohoa, "OUTPUT_M3U", root / "phaohoa_live.m3u"),                  patch.object(phaohoa, "OUTPUT_PIPE_M3U", root / "phaohoa_live_pipe.m3u"),                  patch.object(phaohoa, "OUTPUT_VLC_M3U", root / "phaohoa_live_vlc.m3u"),                  patch.object(phaohoa, "OUTPUT_DEBUG", root / "phaohoa_debug.json"):
                phaohoa.write_outputs([row])
            content = (root / "phaohoa_live.m3u").read_text(encoding="utf-8")
        self.assertNotIn("\u2028", content)
        self.assertEqual(content.count("#EXTINF:"), 1)


if __name__ == "__main__":
    unittest.main()

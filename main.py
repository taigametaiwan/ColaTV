from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from playwright.async_api import BrowserContext, Page, async_playwright

VERSION = "0.1.0-COLATV-METADATA-AUTHORIZED-PLAYLIST"
ROOT = Path(__file__).resolve().parent
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_START_URL = "https://colatv77.live/"
DEFAULT_INPUT = ROOT / "authorized_streams.json"
OUTPUT_M3U = ROOT / "colatv.m3u"
OUTPUT_PIPE_M3U = ROOT / "colatv_pipe.m3u"
OUTPUT_DEBUG = ROOT / "colatv_debug.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

MATCH_PATH_RE = re.compile(r"/truc-tiep/", re.I)
MEDIA_RE = re.compile(r"\.(?:m3u8|mpd)(?:[?#]|$)", re.I)
DATE_SLUG_RE = re.compile(
    r"^(?P<name>.*?)-luc-(?P<hour>\d{2})(?P<minute>\d{2})-ngay-"
    r"(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{4})(?:-[a-z0-9]+)?$",
    re.I,
)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def absolute_url(base: str, value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    return urljoin(base, value)


def canonical_match_url(url: str) -> str:
    parsed = urlparse(clean_text(url))
    path = re.sub(r"/+$", "", parsed.path)
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def slug_to_metadata(url: str, fallback_title: str = "") -> dict[str, str]:
    slug = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    match = DATE_SLUG_RE.match(slug)
    date_value = ""
    time_value = ""
    raw_name = slug
    if match:
        raw_name = match.group("name")
        date_value = f"{match.group('day')}/{match.group('month')}/{match.group('year')}"
        time_value = f"{match.group('hour')}:{match.group('minute')}"

    normalized = re.sub(r"[-_]+", " ", raw_name)
    normalized = re.sub(r"\bvs\b", " vs ", normalized, flags=re.I)
    normalized = clean_text(normalized)
    name = normalized.title().replace(" Vs ", " vs ")

    if not name or " vs " not in name.lower():
        title = clean_text(fallback_title)
        title = re.sub(r"\s*[-|–]\s*Cola\s*TV.*$", "", title, flags=re.I)
        title = re.sub(r"^Trực tiếp\s+", "", title, flags=re.I)
        title = re.sub(r"\s+(?:lúc|vào lúc)\s+.*$", "", title, flags=re.I)
        if title:
            name = title

    home_name = ""
    away_name = ""
    parts = re.split(r"\s+vs\s+", name, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        home_name, away_name = map(clean_text, parts)

    return {
        "name": name or "Trận đấu",
        "home_name": home_name,
        "away_name": away_name,
        "date": date_value,
        "time": time_value,
    }


def normalize_name(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9à-ỹ]+", " ", value)
    return clean_text(value)


def image_score(row: dict[str, Any], team_name: str, side: str) -> int:
    url = clean_text(row.get("url"))
    if not url:
        return -1000
    haystack = normalize_name(
        " ".join(
            [
                str(row.get("alt", "")),
                str(row.get("title", "")),
                str(row.get("class_name", "")),
                str(row.get("parent_text", "")),
                url,
            ]
        )
    )
    team = normalize_name(team_name)
    score = 0
    if team and team in haystack:
        score += 100
    for token in team.split():
        if len(token) >= 3 and token in haystack:
            score += 12
    if side in haystack:
        score += 8
    if any(key in haystack for key in ("team", "club", "logo", "crest", "badge")):
        score += 20
    width = int(row.get("width") or 0)
    height = int(row.get("height") or 0)
    if width and height:
        ratio = width / max(height, 1)
        if 0.55 <= ratio <= 1.8:
            score += 8
        if 24 <= width <= 500 and 24 <= height <= 500:
            score += 8
    if any(key in haystack for key in ("banner", "ads", "advert", "quang cao", "favicon", "avatar")):
        score -= 60
    if re.search(r"(?:sprite|blank|loading|placeholder)", url, re.I):
        score -= 60
    return score


def choose_team_logo(images: list[dict[str, Any]], team_name: str, side: str, used: set[str]) -> str:
    ranked = sorted(images, key=lambda row: image_score(row, team_name, side), reverse=True)
    for row in ranked:
        url = clean_text(row.get("url"))
        if url and url not in used and image_score(row, team_name, side) > 10:
            used.add(url)
            return url
    return ""


def parse_datetime_iso(value: str) -> tuple[str, str]:
    value = clean_text(value)
    if not value:
        return "", ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        dt = dt.astimezone(VN_TZ)
        return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")
    except ValueError:
        return "", ""


@dataclass
class AuthorizedStream:
    match_url: str
    stream_url: str
    channel_name: str = ""
    logo: str = ""
    user_agent: str = UA
    referer: str = ""
    origin: str = ""
    enabled: bool = True


@dataclass
class MatchMetadata:
    match_url: str
    name: str
    home_name: str
    away_name: str
    date: str
    time: str
    league: str = ""
    home_logo: str = ""
    away_logo: str = ""
    page_title: str = ""
    extraction_notes: list[str] | None = None

    def __post_init__(self) -> None:
        if self.extraction_notes is None:
            self.extraction_notes = []


def load_authorized_streams(path: Path) -> list[AuthorizedStream]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("streams", [])
    if not isinstance(payload, list):
        raise ValueError("authorized_streams.json phải là một mảng hoặc có khóa streams.")
    result: list[AuthorizedStream] = []
    for index, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Dòng {index} không phải object JSON.")
        item = AuthorizedStream(
            match_url=clean_text(row.get("match_url")),
            stream_url=clean_text(row.get("stream_url")),
            channel_name=clean_text(row.get("channel_name")),
            logo=clean_text(row.get("logo")),
            user_agent=clean_text(row.get("user_agent")) or UA,
            referer=clean_text(row.get("referer")),
            origin=clean_text(row.get("origin")),
            enabled=bool(row.get("enabled", True)),
        )
        if not item.match_url or not item.stream_url:
            raise ValueError(f"Dòng {index} thiếu match_url hoặc stream_url.")
        if not MEDIA_RE.search(item.stream_url):
            raise ValueError(f"Dòng {index}: stream_url không phải M3U8/MPD.")
        result.append(item)
    return result


def discover_match_links_from_values(values: list[str], base_url: str) -> list[str]:
    unique: list[str] = []
    for value in values:
        url = canonical_match_url(absolute_url(base_url, value))
        if MATCH_PATH_RE.search(urlparse(url).path) and url not in unique:
            unique.append(url)
    return unique


async def discover_match_links(page: Page, start_url: str) -> list[str]:
    await page.goto(start_url, wait_until="domcontentloaded", timeout=45_000)
    try:
        await page.wait_for_timeout(1500)
        values = await page.locator("a[href]").evaluate_all(
            "nodes => nodes.map(node => node.getAttribute('href') || node.href || '')"
        )
    except Exception:
        values = []
    return discover_match_links_from_values(values, page.url or start_url)


async def page_payload(page: Page) -> dict[str, Any]:
    return await page.evaluate(
        r"""
        () => {
          const abs = value => {
            try { return new URL(value, location.href).href; } catch (_) { return ''; }
          };
          const txt = node => (node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
          const images = Array.from(document.querySelectorAll('img')).map(img => ({
            url: abs(img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || ''),
            alt: img.alt || '',
            title: img.title || '',
            class_name: img.className || '',
            parent_text: txt(img.parentElement).slice(0, 300),
            width: img.naturalWidth || img.width || 0,
            height: img.naturalHeight || img.height || 0
          })).filter(row => row.url);
          const jsonld = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
            .map(node => node.textContent || '').filter(Boolean);
          const meta = {};
          for (const node of document.querySelectorAll('meta[property], meta[name]')) {
            const key = node.getAttribute('property') || node.getAttribute('name');
            const value = node.getAttribute('content') || '';
            if (key && value && !(key in meta)) meta[key] = value;
          }
          const headings = Array.from(document.querySelectorAll('h1,h2,h3')).map(txt).filter(Boolean);
          return {
            title: document.title || '',
            images,
            jsonld,
            meta,
            headings,
            body_text: txt(document.body).slice(0, 40000)
          };
        }
        """
    )


def iter_jsonld_nodes(value: Any):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_jsonld_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_jsonld_nodes(item)


def apply_jsonld(metadata: dict[str, str], raw_blocks: list[str], notes: list[str]) -> None:
    for raw in raw_blocks:
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        for node in iter_jsonld_nodes(parsed):
            node_type = clean_text(node.get("@type")).lower()
            if node_type not in {"sportsevent", "event", "broadcastEvent".lower()}:
                continue
            name = clean_text(node.get("name"))
            if name and (" vs " in name.lower() or not metadata.get("name")):
                metadata["name"] = name
            date_value, time_value = parse_datetime_iso(clean_text(node.get("startDate")))
            if date_value:
                metadata["date"] = date_value
                metadata["time"] = time_value
            location = node.get("location")
            if isinstance(location, dict):
                league = clean_text(location.get("name"))
                if league:
                    metadata["league"] = league
            notes.append("Đã đọc JSON-LD Event/SportsEvent.")
            return


async def extract_match_metadata(context: BrowserContext, match_url: str) -> MatchMetadata:
    page = await context.new_page()
    try:
        await page.goto(match_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1200)
        payload = await page_payload(page)
    finally:
        await page.close()

    page_title = clean_text(payload.get("title"))
    base = slug_to_metadata(match_url, page_title)
    notes: list[str] = ["Ngày giờ ưu tiên lấy từ slug URL."]
    apply_jsonld(base, payload.get("jsonld", []), notes)

    if not base.get("name") or " vs " not in base.get("name", "").lower():
        og_title = clean_text(payload.get("meta", {}).get("og:title"))
        if og_title:
            base.update(slug_to_metadata(match_url, og_title))
            notes.append("Tên trận lấy bổ sung từ og:title.")

    home_name = clean_text(base.get("home_name"))
    away_name = clean_text(base.get("away_name"))
    if (not home_name or not away_name) and " vs " in clean_text(base.get("name")).lower():
        parts = re.split(r"\s+vs\s+", clean_text(base.get("name")), maxsplit=1, flags=re.I)
        if len(parts) == 2:
            home_name, away_name = map(clean_text, parts)

    images = payload.get("images", [])
    used: set[str] = set()
    home_logo = choose_team_logo(images, home_name, "home", used)
    away_logo = choose_team_logo(images, away_name, "away", used)
    if home_logo or away_logo:
        notes.append("Logo đội được chấm điểm theo tên đội, alt/title, class và kích thước ảnh.")

    league = clean_text(base.get("league"))
    if not league:
        body = clean_text(payload.get("body_text"))
        match = re.search(r"(?:giải đấu|league|competition)\s*[:\-]\s*([^|•]{2,100})", body, re.I)
        if match:
            league = clean_text(match.group(1))

    return MatchMetadata(
        match_url=canonical_match_url(match_url),
        name=clean_text(base.get("name")),
        home_name=home_name,
        away_name=away_name,
        date=clean_text(base.get("date")),
        time=clean_text(base.get("time")),
        league=league,
        home_logo=home_logo,
        away_logo=away_logo,
        page_title=page_title,
        extraction_notes=notes,
    )


def m3u_escape(value: Any) -> str:
    return clean_text(value).replace('"', "'")


def stable_tvg_id(match_url: str, stream_url: str) -> str:
    digest = hashlib.sha1(f"{match_url}|{stream_url}".encode("utf-8")).hexdigest()[:14]
    return f"colatv-{digest}"


def display_name(metadata: MatchMetadata, stream: AuthorizedStream) -> str:
    pieces = []
    if metadata.date or metadata.time:
        pieces.append(clean_text(f"{metadata.time} {metadata.date}"))
    pieces.append(stream.channel_name or metadata.name)
    return " | ".join(piece for piece in pieces if piece)


def probe_stream(stream: AuthorizedStream, timeout: int = 12) -> tuple[bool, str]:
    headers = {
        "User-Agent": stream.user_agent or UA,
        "Accept": "*/*",
        "Cache-Control": "no-cache",
    }
    if stream.referer:
        headers["Referer"] = stream.referer
    if stream.origin:
        headers["Origin"] = stream.origin
    request = urllib.request.Request(stream.stream_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()) or 0)
            content_type = response.headers.get("Content-Type", "")
            data = response.read(256)
            if stream.stream_url.lower().split("?", 1)[0].endswith(".m3u8"):
                ok = status == 200 and b"#EXTM3U" in data.upper()
            elif stream.stream_url.lower().split("?", 1)[0].endswith(".mpd"):
                ok = status == 200 and b"<MPD" in data.upper()
            else:
                ok = status == 200
            return ok, f"HTTP {status}; content-type={content_type or 'unknown'}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def write_outputs(
    metadata_by_url: dict[str, MatchMetadata],
    streams: list[AuthorizedStream],
    verification: dict[str, dict[str, Any]],
    discovery: dict[str, Any],
) -> int:
    standard = ["#EXTM3U"]
    pipe = ["#EXTM3U"]
    emitted = 0

    for stream in streams:
        if not stream.enabled:
            continue
        key = canonical_match_url(stream.match_url)
        metadata = metadata_by_url.get(key)
        if metadata is None:
            fallback = slug_to_metadata(stream.match_url)
            metadata = MatchMetadata(match_url=key, **fallback)
        logo = stream.logo or metadata.home_logo or metadata.away_logo
        title = display_name(metadata, stream)
        group = metadata.league or "Bóng đá"
        extinf = (
            f'#EXTINF:-1 tvg-id="{stable_tvg_id(key, stream.stream_url)}" '
            f'tvg-name="{m3u_escape(metadata.name)}" '
            f'tvg-logo="{m3u_escape(logo)}" '
            f'group-title="{m3u_escape(group)}",{m3u_escape(title)}'
        )
        standard.extend([extinf, stream.stream_url])
        header_parts = [f"User-Agent={stream.user_agent or UA}"]
        if stream.referer:
            header_parts.append(f"Referer={stream.referer}")
        if stream.origin:
            header_parts.append(f"Origin={stream.origin}")
        pipe.extend([extinf, f"{stream.stream_url}|{'&'.join(header_parts)}"])
        emitted += 1

    OUTPUT_M3U.write_text("\n".join(standard) + "\n", encoding="utf-8")
    OUTPUT_PIPE_M3U.write_text("\n".join(pipe) + "\n", encoding="utf-8")

    debug = {
        "version": VERSION,
        "generated_at_vietnam": datetime.now(VN_TZ).isoformat(),
        "summary": {
            "discovered_match_pages": len(discovery.get("match_urls", [])),
            "metadata_pages_read": len(metadata_by_url),
            "authorized_stream_rows": len(streams),
            "playlist_entries": emitted,
            "verified_ok": sum(1 for row in verification.values() if row.get("ok")),
        },
        "discovery": discovery,
        "matches": {key: asdict(value) for key, value in metadata_by_url.items()},
        "authorized_streams": [asdict(item) for item in streams],
        "verification": verification,
        "notice": (
            "Công cụ chỉ ghép URL phát trong authorized_streams.json. "
            "Nó không bắt request Network, giải mã player hoặc tự trích xuất URL phát từ website."
        ),
    }
    OUTPUT_DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
    return emitted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quét metadata trận đấu và tạo M3U từ URL phát được cấp phép."
    )
    parser.add_argument("start_url", nargs="?", default=DEFAULT_START_URL)
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="File authorized_streams.json")
    parser.add_argument("--max-matches", type=int, default=30)
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--headful", action="store_true")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    input_path = Path(args.input).resolve()
    streams = load_authorized_streams(input_path)
    print(f"ColaTV metadata playlist builder {VERSION}", flush=True)
    print(f"Input hợp pháp: {input_path}", flush=True)

    target_urls: list[str] = []
    if args.max_matches <= 0:
        target_urls = []
    elif MATCH_PATH_RE.search(urlparse(args.start_url).path):
        target_urls = [canonical_match_url(args.start_url)]
    else:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=not args.headful,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = await browser.new_context(user_agent=UA, locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh")
            page = await context.new_page()
            try:
                target_urls = await discover_match_links(page, args.start_url)
            finally:
                await page.close()
                await context.close()
                await browser.close()

    for stream in streams:
        url = canonical_match_url(stream.match_url)
        if url not in target_urls:
            target_urls.append(url)
    target_urls = target_urls[: max(args.max_matches, 0)]

    metadata_by_url: dict[str, MatchMetadata] = {}
    errors: dict[str, str] = {}
    if target_urls:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=not args.headful,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = await browser.new_context(user_agent=UA, locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh")
            for index, url in enumerate(target_urls, start=1):
                print(f"[{index}/{len(target_urls)}] Metadata: {url}", flush=True)
                try:
                    metadata_by_url[url] = await extract_match_metadata(context, url)
                except Exception as exc:
                    errors[url] = f"{type(exc).__name__}: {exc}"
                    fallback = slug_to_metadata(url)
                    metadata_by_url[url] = MatchMetadata(match_url=url, **fallback, extraction_notes=[errors[url]])
                    print(f"  WARN: {errors[url]}", flush=True)
            await context.close()
            await browser.close()

    verification: dict[str, dict[str, Any]] = {}
    if args.verify:
        for index, stream in enumerate(streams, start=1):
            if not stream.enabled:
                continue
            ok, reason = await asyncio.to_thread(probe_stream, stream)
            verification[stream.stream_url] = {"ok": ok, "reason": reason}
            print(f"[{index}/{len(streams)}] {'OK' if ok else 'FAIL'} {reason}", flush=True)

    discovery = {
        "start_url": args.start_url,
        "match_urls": target_urls,
        "errors": errors,
    }
    count = write_outputs(metadata_by_url, streams, verification, discovery)
    print(f"Đã tạo {OUTPUT_M3U.name} với {count} mục.", flush=True)
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main(parse_args()))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

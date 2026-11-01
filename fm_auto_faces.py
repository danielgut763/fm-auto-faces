#!/usr/bin/env python3
"""
FM Auto-Faces Prototype
=======================
Identifies real Football Manager players missing a photo in your `graphics` folder,
scrapes their real player portrait from Transfermarkt / TheSportsDB,
converts it to FM's standard 180x180 PNG format (with optional background removal),
and generates the `config.xml` file so FM loads them immediately.
"""

import argparse
import io
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

# Optional cutout background removal via `rembg`
try:
    from rembg import remove as rembg_remove
    HAS_REMBG = True
except ImportError:
    HAS_REMBG = False


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class PlayerRecord:
    uid: str
    name: str
    nationality: str = ""
    club: str = ""
    dob: str = ""


def scan_existing_facepacks(graphics_dir: Path) -> Set[str]:
    """
    Recursively scans all `config.xml` files inside the user's FM `graphics` folder
    and returns a set of all player UIDs that already have a portrait mapped.
    """
    existing_uids: Set[str] = set()
    if not graphics_dir.exists():
        print(f"[!] Graphics directory '{graphics_dir}' does not exist yet. Assuming 0 existing faces.")
        return existing_uids

    config_files = list(graphics_dir.rglob("config.xml"))
    print(f"[*] Scanning {len(config_files)} config.xml file(s) in '{graphics_dir}'...")

    # Regex fallback in case an XML file in a user's facepack is slightly malformed
    record_pattern = re.compile(r'to="graphics/pictures/person/(\d+)/portrait"')

    for config_path in config_files:
        try:
            tree = ET.parse(config_path)
            root = tree.getroot()
            for record in root.iter("record"):
                to_attr = record.attrib.get("to", "")
                match = re.search(r"person/(\d+)/portrait", to_attr)
                if match:
                    existing_uids.add(match.group(1))
        except ET.ParseError:
            content = config_path.read_text(encoding="utf-8", errors="ignore")
            for match in record_pattern.finditer(content):
                existing_uids.add(match.group(1))

    print(f"[+] Found {len(existing_uids)} players with existing photos.")
    return existing_uids


def is_real_player(uid: str, max_real_uid: Optional[int] = None) -> bool:
    """
    Checks if a player UID belongs to a real database player vs. a generated Newgen.
    - In FM23/FM24+, exported Newgen UIDs are prefixed with 'r-' (e.g. 'r-20020891').
    - Older FM versions use numeric UIDs above a high threshold (typically ~2002042000).
    """
    uid_clean = uid.strip()
    if uid_clean.lower().startswith("r-"):
        return False
    if not uid_clean.isdigit():
        return False
    if max_real_uid is not None and int(uid_clean) >= max_real_uid:
        return False
    return True


def parse_fm_export(export_file: Path, max_real_uid: Optional[int] = None) -> List[PlayerRecord]:
    """
    Parses an FM exported text/RTF table (Ctrl+P -> Text File in FM Player Search).
    Expected pipe-delimited columns:
    | UID | Name | Nat | Club | Age | DoB |
    """
    players: List[PlayerRecord] = []
    lines = export_file.read_text(encoding="utf-8", errors="ignore").splitlines()

    header_map: Dict[str, int] = {}

    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue

        cols = [c.strip() for c in line.strip("|").split("|")]
        if not cols or len(cols) < 2:
            continue

        first_col_lower = cols[0].lower()
        if first_col_lower in ("uid", "unique id", "id"):
            for idx, col_name in enumerate(cols):
                header_map[col_name.lower()] = idx
            continue

        uid = cols[header_map.get("uid", header_map.get("unique id", 0))]
        name = cols[header_map.get("name", 1)] if len(cols) > 1 else ""
        nat = cols[header_map.get("nat", 2)] if len(cols) > 2 else ""
        club = cols[header_map.get("club", 3)] if len(cols) > 3 else ""
        dob = cols[header_map.get("dob", 5)] if len(cols) > 5 else ""

        if not name:
            continue

        if is_real_player(uid, max_real_uid=max_real_uid):
            players.append(
                PlayerRecord(
                    uid=uid.strip(),
                    name=name.strip(),
                    nationality=nat.strip(),
                    club=club.strip(),
                    dob=dob.strip(),
                )
            )

    print(f"[+] Parsed {len(players)} real player(s) from '{export_file.name}'.")
    return players


class PhotoScraper:
    """
    Scrapes real player portrait photos using:
    1. Transfermarkt quick-search (primary source for real football players)
    2. TheSportsDB free API (secondary fallback)
    """

    def __init__(self, delay: float = 1.2):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.delay = delay

    def fetch_player_photo_bytes(self, player: PlayerRecord) -> Optional[bytes]:
        img_url = self._search_transfermarkt(player)
        if not img_url:
            img_url = self._search_thesportsdb(player)

        if not img_url:
            return None

        try:
            resp = self.session.get(img_url, timeout=15)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            print(f"    [!] Failed downloading image from {img_url}: {exc}")
            return None

    def _search_transfermarkt(self, player: PlayerRecord) -> Optional[str]:
        """
        Searches Transfermarkt for the player name and extracts the high-res portrait URL.
        """
        query = urllib.parse.quote(player.name)
        url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={query}"

        try:
            time.sleep(self.delay)
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    [!] Transfermarkt request error for '{player.name}': {exc}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        for img in soup.select("table.items img.bilderrahmen-fixed"):
            src = img.get("src", "")
            alt = img.get("alt", "")
            if not src or "default.jpg" in src or "portrait" not in src:
                continue

            # Upgrade Transfermarkt thumbnail URL ('small' or 'medium') to 'header' resolution
            high_res_url = re.sub(r"/portrait/(small|medium)/", "/portrait/header/", src)
            print(f"    [+] Found Transfermarkt portrait for {player.name} ({alt}): {high_res_url}")
            return high_res_url

        return None

    def _search_thesportsdb(self, player: PlayerRecord) -> Optional[str]:
        """
        Fallback lookup via TheSportsDB's free v1 test API (looks for strCutout or strThumb).
        """
        query = urllib.parse.quote(player.name)
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={query}"

        try:
            time.sleep(self.delay)
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        results = data.get("player") or []
        for entry in results:
            if entry.get("strSport") != "Soccer":
                continue
            img_url = entry.get("strCutout") or entry.get("strThumb")
            if img_url:
                print(f"    [+] Found TheSportsDB portrait for {player.name}: {img_url}")
                return img_url

        return None


def process_and_save_portrait(
    image_bytes: bytes,
    output_path: Path,
    size: int = 180,
    remove_bg: bool = False,
) -> bool:
    """
    Converts raw image bytes into a square (180x180) RGBA PNG suitable for Football Manager.
    Optionally removes background via `rembg` if installed and requested.
    """
    try:
        if remove_bg:
            if HAS_REMBG:
                image_bytes = rembg_remove(image_bytes)
            else:
                print("    [!] --remove-bg requested, but 'rembg' is not installed. Saving standard crop.")

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGBA")
            fitted = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.2))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            fitted.save(output_path, format="PNG")
        return True
    except Exception as exc:
        print(f"    [!] Image processing failed: {exc}")
        return False


def generate_config_xml(output_dir: Path) -> Path:
    """
    Generates a Football Manager `config.xml` mapping every `<UID>.png` in `output_dir`
    to `graphics/pictures/person/<UID>/portrait`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    png_files = sorted(output_dir.glob("*.png"))

    lines = [
        "<record>",
        '\t<!-- resource manager options -->',
        '\t<boolean id="preload" value="false"/>',
        '\t<boolean id="amap" value="false"/>',
        "",
        '\t<!-- picture mappings -->',
        '\t<list id="maps">',
    ]

    for png in png_files:
        uid = png.stem
        if uid.isdigit():
            lines.append(
                f'\t\t<record from="{uid}" to="graphics/pictures/person/{uid}/portrait"/>'
            )

    lines.extend(
        [
            "\t</list>",
            "</record>",
            "",
        ]
    )

    config_path = output_dir / "config.xml"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[+] Wrote '{config_path}' with {len(png_files)} player face mapping(s).")
    return config_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find real FM players without photos, scrape their faces, and build a facepack."
    )
    parser.add_argument(
        "--export-file",
        type=Path,
        required=True,
        help="Path to the RTF/text player list exported from Football Manager.",
    )
    parser.add_argument(
        "--graphics-dir",
        type=Path,
        required=True,
        help="Path to your FM 'graphics' directory (used to detect existing faces).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Folder where scraped faces & config.xml will be saved (defaults to <graphics-dir>/faces/auto_scraped_faces).",
    )
    parser.add_argument(
        "--remove-bg",
        action="store_true",
        help="Remove background using 'rembg' for a cutout-style facepack.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.2,
        help="Polite delay in seconds between web requests (default: 1.2s).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of missing players to scrape in one run (0 = no limit).",
    )

    args = parser.parse_args()
    output_dir: Path = args.output_dir or (args.graphics_dir / "faces" / "auto_scraped_faces")

    existing_uids = scan_existing_facepacks(args.graphics_dir)
    real_players = parse_fm_export(args.export_file)

    missing_players = [p for p in real_players if p.uid not in existing_uids]
    print(f"[*] {len(missing_players)} real player(s) are missing a face.")

    if args.limit > 0:
        missing_players = missing_players[: args.limit]
        print(f"[*] Limiting run to first {len(missing_players)} missing player(s).")

    scraper = PhotoScraper(delay=args.delay)
    success_count = 0

    for idx, player in enumerate(missing_players, start=1):
        print(f"[{idx}/{len(missing_players)}] Searching photo for {player.name} (UID: {player.uid}, Club: {player.club})...")
        img_bytes = scraper.fetch_player_photo_bytes(player)
        if not img_bytes:
            print(f"    [-] No photo found online for {player.name}.")
            continue

        out_png = output_dir / f"{player.uid}.png"
        if process_and_save_portrait(img_bytes, out_png, remove_bg=args.remove_bg):
            print(f"    [+] Saved {out_png}")
            success_count += 1

    generate_config_xml(output_dir)
    print(
        f"\n[Done] Added {success_count} new player photo(s) to '{output_dir}'.\n"
        "In Football Manager: go to Preferences -> Interface -> Clear Cache & Reload Skin!"
    )


if __name__ == "__main__":
    main()

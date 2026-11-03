#!/usr/bin/env python3
"""
FM Auto-Faces (FM26 Ready)
==========================
1. Automatically locates your real Football Manager 26 installation, user data
   (`Documents/Sports Interactive/Football Manager 26`), existing facepacks (`Graphics`),
   and real player databases/saves.
2. Creates a full safety backup of existing `config.xml` files (and any target output
   files) before making any changes.
3. Scans all existing facepacks (540,000+ faces in seconds) to find which real players
   are missing a portrait.
4. Supports filtering by league (e.g. `--league "Premier League"`) to get all real
   players on the squads of that league who are missing a photo.
5. Scrapes real player portraits from Transfermarkt, Wikipedia, and TheSportsDB,
   isolates the player by removing the background (`rembg` human segmentation),
   converts to FM26's 180x180 RGBA PNG format, and updates `config.xml`.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import difflib
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
import urllib3.util.connection as urllib3_cn
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

# Ensure UTF-8 output on Windows consoles so international player/club names never crash
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Force IPv4 to avoid 21-second Windows IPv6 fallback timeouts on Transfermarkt/Wikipedia
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

# Lazy-loaded `rembg` sessions for cutout background removal
HAS_REMBG = True
_REMBG_REMOVE = None
_REMBG_NEW_SESSION = None
_REMBG_SESSIONS: Dict[str, object] = {}
_REMBG_LOCK = threading.Lock()


def get_rembg_session(model_name: str = "u2net_human_seg"):
    global HAS_REMBG, _REMBG_REMOVE, _REMBG_NEW_SESSION
    if not HAS_REMBG:
        return None
    if model_name not in _REMBG_SESSIONS:
        with _REMBG_LOCK:
            if model_name not in _REMBG_SESSIONS:
                try:
                    if _REMBG_NEW_SESSION is None:
                        from rembg import remove as rembg_remove, new_session as rembg_new_session
                        _REMBG_REMOVE = rembg_remove
                        _REMBG_NEW_SESSION = rembg_new_session
                    _REMBG_SESSIONS[model_name] = _REMBG_NEW_SESSION(model_name)
                except ImportError:
                    HAS_REMBG = False
                    return None
    return _REMBG_SESSIONS.get(model_name)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
WIKIMEDIA_USER_AGENT = (
    "FMAutoFaces/1.0 (Football Manager 26 Facepack Tool; python-requests)"
)

TM_DOMAINS = (
    "www.transfermarkt.com",
    "www.transfermarkt.co.uk",
    "www.transfermarkt.us",
    "www.transfermarkt.co.in",
    "www.transfermarkt.co.za",
    "www.transfermarkt.de",
    "www.transfermarkt.at",
    "www.transfermarkt.ch",
)

DIMINUTIVE_MAP: Dict[str, str] = {
    "dimitris": "dimitrios",
    "demetris": "dimitrios",
    "dimitrios": "dimitrios",
    "kostas": "konstantinos",
    "costas": "konstantinos",
    "konstantinos": "konstantinos",
    "giannis": "ioannis",
    "yannis": "ioannis",
    "iannis": "ioannis",
    "ioannis": "ioannis",
    "giorgos": "georgios",
    "yorgos": "georgios",
    "georgios": "georgios",
    "nikos": "nikolaos",
    "nicos": "nikolaos",
    "nikolaos": "nikolaos",
    "vasilis": "vasileios",
    "vassilis": "vasileios",
    "vasileios": "vasileios",
    "panos": "panagiotis",
    "panagiotis": "panagiotis",
    "vangelis": "evangelos",
    "evangelos": "evangelos",
    "thanasis": "athanasios",
    "athanasios": "athanasios",
    "michalis": "michail",
    "michail": "michail",
    "stelios": "stylianos",
    "stylianos": "stylianos",
    "tasos": "anastasios",
    "anastasios": "anastasios",
    "babis": "charalampos",
    "charalampos": "charalampos",
    "christos": "chrystos",
    "chrystos": "chrystos",
    "aleksandr": "alexander",
    "aleksandar": "alexander",
    "oleksandr": "alexander",
    "alexander": "alexander",
    "mohamed": "mohammed",
    "muhammad": "mohammed",
    "muhamed": "mohammed",
    "mohamad": "mohammed",
    "mohammed": "mohammed",
    "youssef": "yusuf",
    "yousef": "yusuf",
    "yusuf": "yusuf",
    "sergey": "sergei",
    "sergiy": "sergei",
    "sergei": "sergei",
    "andrey": "andrei",
    "andriy": "andrei",
    "andrei": "andrei",
    "dmitriy": "dmitri",
    "dmytro": "dmitri",
    "dmitri": "dmitri",
    "artem": "artyom",
    "artyom": "artyom",
    "evgeniy": "evgeni",
    "yevhen": "evgeni",
    "evgeni": "evgeni",
    "mykola": "nikolay",
    "nikolai": "nikolay",
    "nikolay": "nikolay",
}

FORMAL_NAME_EXPANSIONS: Dict[str, str] = {
    "dimitris": "Dimitrios",
    "demetris": "Dimitrios",
    "kostas": "Konstantinos",
    "costas": "Konstantinos",
    "giannis": "Ioannis",
    "yannis": "Ioannis",
    "iannis": "Ioannis",
    "giorgos": "Georgios",
    "yorgos": "Georgios",
    "nikos": "Nikolaos",
    "nicos": "Nikolaos",
    "vasilis": "Vasileios",
    "vassilis": "Vasileios",
    "panos": "Panagiotis",
    "vangelis": "Evangelos",
    "thanasis": "Athanasios",
    "michalis": "Michail",
    "stelios": "Stylianos",
    "tasos": "Anastasios",
    "babis": "Charalampos",
}


@dataclass
class PlayerRecord:
    uid: str
    name: str
    nationality: str = ""
    club: str = ""
    dob: str = ""
    ca: int = 0
    pa: int = 0
    is_female: bool = False
    alt_names: Tuple[str, ...] = ()


@dataclass
class FM26Environment:
    user_docs_dir: Optional[Path] = None
    graphics_dir: Optional[Path] = None
    game_install_dir: Optional[Path] = None
    official_db_dir: Optional[Path] = None
    editor_data_dir: Optional[Path] = None
    saves_dir: Optional[Path] = None
    live_sqlite_dbs: List[Path] = None
    bepinex_facepack_index: Optional[Path] = None

    def __post_init__(self) -> None:
        if self.live_sqlite_dbs is None:
            self.live_sqlite_dbs = []


def discover_fm26_environment() -> FM26Environment:
    """
    Probes the system for real Football Manager 26 directories, game installation,
    databases, save files, and graphics folders.
    """
    env = FM26Environment()
    home = Path.home()

    # 1. Locate User Documents folder for FM26
    doc_candidates = [
        home / "Documents" / "Sports Interactive" / "Football Manager 26",
        home / "Documents" / "Sports Interactive" / "Football Manager 2026",
        home / "OneDrive" / "Documents" / "Sports Interactive" / "Football Manager 26",
        home / "Documents" / "Sports Interactive" / "Football Manager 2024",
    ]
    for cand in doc_candidates:
        if cand.is_dir():
            env.user_docs_dir = cand
            break

    if env.user_docs_dir:
        for gname in ("Graphics", "graphics"):
            gdir = env.user_docs_dir / gname
            if gdir.is_dir():
                env.graphics_dir = gdir
                break
        if env.graphics_dir is None:
            env.graphics_dir = env.user_docs_dir / "Graphics"

        ed_dir = env.user_docs_dir / "editor data"
        if ed_dir.is_dir():
            env.editor_data_dir = ed_dir

        games_dir = env.user_docs_dir / "games"
        if games_dir.is_dir():
            env.saves_dir = games_dir

    # 2. Locate Steam installation of Football Manager 26
    steam_roots: List[Path] = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]
    for drive_letter in "CDEFGHIJ":
        drive_root = Path(f"{drive_letter}:\\")
        if drive_root.exists():
            steam_roots.append(drive_root / "SteamLibrary")
            steam_roots.append(drive_root / "Steam")
            steam_roots.append(drive_root / "Games" / "Steam")

    vdf_paths = [r / "steamapps" / "libraryfolders.vdf" for r in steam_roots if r.exists()]
    for vdf in vdf_paths:
        if vdf.is_file():
            try:
                content = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"path"\s+"([^"]+)"', content):
                    p = Path(m.group(1).replace("\\\\", "\\"))
                    if p not in steam_roots:
                        steam_roots.append(p)
            except OSError:
                pass

    for sroot in steam_roots:
        for game_folder_name in ("Football Manager 26", "Football Manager 2026"):
            cand_game = sroot / "steamapps" / "common" / game_folder_name
            if cand_game.is_dir() and (cand_game / "fm.exe").is_file():
                env.game_install_dir = cand_game
                break
        if env.game_install_dir:
            break

    if env.game_install_dir:
        db_root = env.game_install_dir / "shared" / "data" / "database" / "db"
        if db_root.is_dir():
            db_versions = sorted([d for d in db_root.iterdir() if d.is_dir()], key=lambda x: x.name, reverse=True)
            if db_versions:
                env.official_db_dir = db_versions[0]

        idx_file = env.game_install_dir / "BepInEx" / "config" / "PitchHUD.facepack-index.txt"
        if idx_file.is_file():
            env.bepinex_facepack_index = idx_file

    # 3. Locate FM26 Live/Snapshot SQLite databases (FM Season Hub / companion dbs)
    local_appdata = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    fmsh_dados = local_appdata / "Programs" / "FM Season Hub" / "dados"
    if fmsh_dados.is_dir():
        cache_dir = fmsh_dados / "cache"
        if cache_dir.is_dir():
            live_dbs = sorted(
                cache_dir.glob("leitura_ao_vivo_*.db"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            env.live_sqlite_dbs.extend(live_dbs)
        snap_db = fmsh_dados / "fm26_snapshots.db"
        if snap_db.is_file():
            env.live_sqlite_dbs.append(snap_db)

    return env


def find_facepack_config_files(graphics_dir: Path) -> List[Path]:
    """
    Fast directory-based discovery of `config.xml` files inside `graphics_dir`.
    Avoids `Path.rglob("config.xml")` which enumerates 500,000+ individual PNG files.
    """
    config_files: List[Path] = []
    if not graphics_dir.exists():
        return config_files

    dirs_to_visit = [graphics_dir]
    while dirs_to_visit:
        cur_dir = dirs_to_visit.pop()
        cfg = cur_dir / "config.xml"
        if cfg.is_file():
            config_files.append(cfg)
        try:
            with os.scandir(cur_dir) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        dirs_to_visit.append(Path(entry.path))
        except OSError:
            pass

    return sorted(config_files)


def create_backup(
    graphics_dir: Path,
    output_dir: Path,
    backup_root: Path,
    extra_files: Optional[List[Path]] = None,
) -> Path:
    """
    Creates a timestamped safety backup before running the tool:
    - Backs up all existing `config.xml` files in `graphics_dir`
    - Backs up any existing `.png` and `config.xml` files inside `output_dir`
    - Backs up any extra configuration files (such as `PitchHUD.facepack-index.txt`)
    - Writes a `backup_manifest.json` with original paths, sizes, and SHA-256 checksums
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = backup_root / f"backup_fm26_{timestamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, object] = {
        "created_at": datetime.now().isoformat(),
        "graphics_dir": str(graphics_dir),
        "output_dir": str(output_dir),
        "files": [],
    }

    files_to_backup: List[Path] = []
    if graphics_dir.exists():
        files_to_backup.extend(find_facepack_config_files(graphics_dir))

    today_prefix = f"backup_fm26_{datetime.now().strftime('%Y%m%d')}_"
    has_today_backup = backup_root.exists() and any(
        d.is_dir() and d.name.startswith(today_prefix) for d in backup_root.iterdir()
    )
    if output_dir.exists() and not has_today_backup:
        for item in output_dir.iterdir():
            if item.is_file() and item not in files_to_backup:
                files_to_backup.append(item)

    if extra_files:
        for ef in extra_files:
            if ef and ef.is_file() and ef not in files_to_backup:
                files_to_backup.append(ef)

    for src_path in files_to_backup:
        try:
            try:
                rel = src_path.relative_to(graphics_dir)
                target_path = dest_dir / "graphics" / rel
            except ValueError:
                target_path = dest_dir / "extra" / src_path.name

            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, target_path)

            sha256 = hashlib.sha256(src_path.read_bytes()).hexdigest()
            manifest["files"].append(
                {
                    "original_path": str(src_path),
                    "backup_path": str(target_path),
                    "size_bytes": src_path.stat().st_size,
                    "sha256": sha256,
                }
            )
        except OSError as exc:
            print(f"[!] Warning: could not back up '{src_path}': {exc}")

    manifest_path = dest_dir / "backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Safety backup created at '{dest_dir}' ({len(manifest['files'])} file(s) backed up).")
    return dest_dir


def scan_existing_facepacks(graphics_dir: Path) -> Set[str]:
    """
    Scans all `config.xml` files inside the user's FM `graphics` folder
    and returns a set of all player UIDs that already have a portrait mapped.
    Optimized with regex streaming so 40MB+ mega-facepack XML files parse in ~1-2s.
    """
    existing_uids: Set[str] = set()
    if not graphics_dir.exists():
        print(f"[!] Graphics directory '{graphics_dir}' does not exist yet. Assuming 0 existing faces.")
        return existing_uids

    t0 = time.time()
    config_files = find_facepack_config_files(graphics_dir)
    print(f"[*] Scanning {len(config_files)} config.xml file(s) in '{graphics_dir}'...")

    person_pattern = re.compile(r"person/(\d+)/portrait")

    for config_path in config_files:
        try:
            content = config_path.read_text(encoding="utf-8", errors="ignore")
            matches = person_pattern.findall(content)
            if matches:
                existing_uids.update(matches)
                print(f"    - {config_path.parent.name}/config.xml: {len(matches):,} face mapping(s)")
        except OSError as exc:
            print(f"    [!] Failed reading {config_path}: {exc}")

    elapsed = time.time() - t0
    print(f"[+] Found {len(existing_uids):,} unique players with existing photos in {elapsed:.2f}s.")
    return existing_uids


def is_real_player(uid: str, max_real_uid: Optional[int] = None) -> bool:
    """
    Checks if a player UID belongs to a real database player vs. a generated Newgen.
    - In FM23/FM24/FM26+, exported Newgen UIDs are prefixed with 'r-' (e.g. 'r-20020891').
    - In FM26 with custom editor data (.fmf), real players go up to ~2002100000 while
      generated regens start above ~2002107000.
    """
    uid_clean = uid.strip()
    if uid_clean.lower().startswith("r-"):
        return False
    if not uid_clean.isdigit():
        return False
    if max_real_uid is not None and int(uid_clean) >= max_real_uid:
        return False
    return True


def load_players_from_fm26_sqlite(
    db_paths: List[Path],
    gender_filter: str = "men",
    min_ca: int = 125,
    league_filter: Optional[str] = None,
    include_all_youth: bool = False,
) -> List[PlayerRecord]:
    """
    Loads real players (`is_regen = 0`) directly from FM26's live/snapshot SQLite database,
    ordered by Current Ability (CA) and Potential Ability (PA) descending.
    If `league_filter` is specified (e.g. 'Premier League'), loads all real players on the
    squads of clubs in that league.
    """
    for db_path in db_paths:
        if not db_path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            row = cur.execute(
                """
                SELECT s.id, s.name, s.save_name, s.game_date, count(p.uid) as cnt
                FROM snapshots s
                JOIN players p ON p.snapshot_id = s.id
                GROUP BY s.id
                ORDER BY cnt DESC, s.id DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.close()
                continue

            snapshot_id, snap_name, save_name, game_date, total_cnt = row
            print(
                f"[*] Reading real FM26 players from '{db_path.name}' "
                f"(Snapshot: '{snap_name}', Save: '{save_name}', Game Date: {game_date}, Total Players: {total_cnt:,})..."
            )

            params: List[object] = [snapshot_id]
            league_join = ""
            league_where = ""

            if league_filter:
                league_clean = league_filter.strip()
                league_alias_map = {
                    "premier league": [11],
                    "english premier league": [11],
                    "epl": [11],
                    "laliga": [67],
                    "la liga": [67],
                    "laliga ea sports": [67],
                    "serie a": [32],
                    "serie a tim": [32],
                    "bundesliga": [22],
                    "ligue 1": [16],
                    "ligue 1 mcdonald's": [16],
                    "big5": [11, 67, 32, 22, 16],
                    "big 5": [11, 67, 32, 22, 16],
                    "big five": [11, 67, 32, 22, 16],
                }
                lower_key = league_clean.lower()
                if lower_key in ("top25", "top 25", "top30", "top 30", "all", "top_countries"):
                    # Discover the top 25 countries by max league reputation (including all nations tied at the 25th rank's reputation, i.e. top 30)
                    nations_rows = cur.execute(
                        """
                        SELECT c.nation, MAX(c.reputation) as max_rep, COUNT(DISTINCT cl.club_uid) as n_clubs
                        FROM club_leagues cl
                        JOIN comps c ON cl.snapshot_id = c.snapshot_id AND cl.league_uid = c.uid
                        WHERE cl.snapshot_id = ? AND COALESCE(c.is_female, 0) = 0
                          AND c.nation IS NOT NULL AND c.nation != ''
                        GROUP BY c.nation
                        ORDER BY max_rep DESC, n_clubs DESC
                        """,
                        (snapshot_id,),
                    ).fetchall()
                    cutoff_idx = 25 if "25" in lower_key else 30
                    if len(nations_rows) >= cutoff_idx:
                        min_nation_rep = nations_rows[cutoff_idx - 1][1]
                        top_nations = [r[0] for r in nations_rows if r[1] >= min_nation_rep]
                    else:
                        top_nations = [r[0] for r in nations_rows]
                    nat_ph = ",".join("?" for _ in top_nations)
                    l_rows = cur.execute(
                        f"""
                        SELECT cl.league_uid, cl.league_name, c.nation, COALESCE(c.reputation, 0) as rep,
                               COUNT(DISTINCT cl.club_uid) as nc
                        FROM club_leagues cl
                        JOIN comps c ON cl.snapshot_id = c.snapshot_id AND cl.league_uid = c.uid
                        WHERE cl.snapshot_id = ? AND COALESCE(c.is_female, 0) = 0
                          AND c.nation IN ({nat_ph})
                          AND COALESCE(c.reputation, 0) >= 70
                        GROUP BY cl.league_uid, cl.league_name, c.nation, c.reputation
                        HAVING nc <= 40
                        ORDER BY rep DESC
                        """,
                        [snapshot_id, *top_nations],
                    ).fetchall()
                    league_uids = [r[0] for r in l_rows]
                    print(
                        f"[*] Selected Top {len(top_nations)} nations ({', '.join(top_nations)}) -> "
                        f"{len(league_uids)} leagues across all professional/semi-pro tiers."
                    )
                elif lower_key in league_alias_map:
                    league_uids = league_alias_map[lower_key]
                elif "," in league_clean:
                    league_uids = []
                    for part in league_clean.split(","):
                        part_s = part.strip()
                        if not part_s:
                            continue
                        if part_s.isdigit():
                            league_uids.append(int(part_s))
                        elif part_s.lower() in league_alias_map:
                            league_uids.extend(league_alias_map[part_s.lower()])
                        else:
                            m_rows = cur.execute(
                                """
                                SELECT DISTINCT cl.league_uid, cl.league_name, c.reputation
                                FROM club_leagues cl
                                LEFT JOIN comps c ON cl.snapshot_id = c.snapshot_id AND cl.league_uid = c.uid
                                WHERE cl.snapshot_id = ? AND cl.league_name LIKE ?
                                ORDER BY COALESCE(c.reputation, 0) DESC
                                """,
                                (snapshot_id, f"%{part_s}%"),
                            ).fetchall()
                            if m_rows:
                                league_uids.append(m_rows[0][0])
                elif league_clean.isdigit():
                    league_uids = [int(league_clean)]
                else:
                    matched_leagues = cur.execute(
                        """
                        SELECT DISTINCT cl.league_uid, cl.league_name, c.reputation
                        FROM club_leagues cl
                        LEFT JOIN comps c ON cl.snapshot_id = c.snapshot_id AND cl.league_uid = c.uid
                        WHERE cl.snapshot_id = ? AND cl.league_name LIKE ?
                        ORDER BY COALESCE(c.reputation, 0) DESC
                        """,
                        (snapshot_id, f"%{league_clean}%"),
                    ).fetchall()
                    league_uids = [r[0] for r in matched_leagues[:1]] if matched_leagues else []

                if not league_uids:
                    print(f"[!] Could not find league matching '{league_filter}' in '{db_path.name}'.")
                    conn.close()
                    continue

                clubs = cur.execute(
                    f"""
                    SELECT club_uid, club_name, league_name
                    FROM club_leagues
                    WHERE snapshot_id = ? AND league_uid IN ({','.join('?' for _ in league_uids)})
                    ORDER BY league_name, club_name
                    """,
                    [snapshot_id, *league_uids],
                ).fetchall()
                league_names_found = sorted({c[2] for c in clubs})
                preview_leagues = ", ".join(league_names_found[:12])
                if len(league_names_found) > 12:
                    preview_leagues += f", ... (+{len(league_names_found) - 12} more)"
                print(
                    f"[*] Filtering to {len(league_names_found)} league(s): {preview_leagues} "
                    f"({len(clubs)} clubs total)"
                )

                placeholders = ",".join("?" for _ in league_uids)
                if include_all_youth:
                    # Include all players contracted to OR playing in any youth/B/reserve/U23/U21/U20/U19/U18 squad
                    # OR having a future transfer to any club in these leagues.
                    league_where = f"""
                      AND (
                          COALESCE(pr.club_uid, p.club_id) IN (
                              SELECT cl.club_uid FROM club_leagues cl
                              WHERE cl.snapshot_id = ? AND cl.league_uid IN ({placeholders})
                          )
                          OR p.team_uid IN (
                              SELECT yt.team_uid FROM youth_teams yt
                              JOIN club_leagues cl ON yt.snapshot_id = cl.snapshot_id AND yt.club_uid = cl.club_uid
                              WHERE yt.snapshot_id = ? AND cl.league_uid IN ({placeholders})
                          )
                          OR p.uid IN (
                              SELECT pt.player_uid FROM player_teams pt
                              JOIN youth_teams yt ON pt.snapshot_id = yt.snapshot_id AND pt.team_uid = yt.team_uid
                              JOIN club_leagues cl ON yt.snapshot_id = cl.snapshot_id AND yt.club_uid = cl.club_uid
                              WHERE pt.snapshot_id = ?
                                AND cl.league_uid IN ({placeholders})
                          )
                          OR p.uid IN (
                              SELECT ft.uid FROM future_transfers ft
                              JOIN club_leagues cl ON ft.snapshot_id = cl.snapshot_id AND ft.dest_club_uid = cl.club_uid
                              WHERE ft.snapshot_id = ?
                                AND cl.league_uid IN ({placeholders})
                          )
                      )
                    """
                    for _ in range(4):
                        params.append(snapshot_id)
                        params.extend(league_uids)
                else:
                    league_join = (
                        "JOIN club_leagues cl ON p.snapshot_id = cl.snapshot_id "
                        "AND COALESCE(pr.club_uid, p.club_id) = cl.club_uid"
                    )
                    league_where = f" AND cl.league_uid IN ({placeholders})"
                    params.extend(league_uids)
                    # Include First-Team Squad members plus any notable wonderkids (CA >= 100)
                    league_where += """
                      AND (
                          p.team_uid = cl.club_uid
                          OR EXISTS (
                              SELECT 1 FROM player_teams pt
                              WHERE pt.snapshot_id = p.snapshot_id
                                AND pt.player_uid = p.uid
                                AND pt.team_uid = cl.club_uid
                          )
                          OR NOT EXISTS (
                              SELECT 1 FROM player_teams pt
                              JOIN youth_teams yt ON pt.snapshot_id = yt.snapshot_id AND pt.team_uid = yt.team_uid
                              WHERE pt.snapshot_id = p.snapshot_id
                                AND pt.player_uid = p.uid
                                AND yt.team_type != 0
                          )
                          OR COALESCE(p.ca, 0) >= 100
                      )
                    """
            else:
                league_where = " AND COALESCE(p.ca, 0) >= ?"
                params.append(min_ca)

            team_to_club_name: Dict[int, str] = {}
            for t_uid, c_name in cur.execute(
                "SELECT team_uid, club_name FROM youth_teams WHERE snapshot_id = ? AND club_name IS NOT NULL",
                (snapshot_id,),
            ).fetchall():
                team_to_club_name[int(t_uid)] = str(c_name).strip()

            query = f"""
                SELECT
                    p.uid,
                    COALESCE(p.common_name, p.full_name, p.legal_name) AS display_name,
                    COALESCE(p.full_name, '') AS full_name,
                    COALESCE(p.legal_name, '') AS legal_name,
                    COALESCE(p.name_first, '') AS name_first,
                    COALESCE(p.name_last, '') AS name_last,
                    COALESCE(pr.nationality_code, pr.nationality, '') AS nat,
                    COALESCE(pr.club_name, '') AS club,
                    COALESCE(pr.birth_date, '') AS dob,
                    COALESCE(p.ca, 0) AS ca,
                    COALESCE(p.pa, 0) AS pa,
                    COALESCE(pr.is_female, 0) AS is_female,
                    p.team_uid
                FROM players p
                JOIN player_profile pr ON p.snapshot_id = pr.snapshot_id AND p.uid = pr.uid
                {league_join}
                WHERE p.snapshot_id = ?
                  AND COALESCE(pr.is_regen, 0) = 0
                  {league_where}
            """
            if gender_filter == "men":
                query += " AND COALESCE(pr.is_female, 0) = 0"
            elif gender_filter == "women":
                query += " AND COALESCE(pr.is_female, 0) = 1"

            query += " ORDER BY MAX(COALESCE(p.ca, 0), COALESCE(p.pa, 0) - 20) DESC, COALESCE(p.pa, 0) DESC, COALESCE(p.ca, 0) DESC"

            rows = cur.execute(query, params).fetchall()
            conn.close()

            players: List[PlayerRecord] = []
            seen_uids: Set[str] = set()
            for (
                uid,
                display_name,
                full_name,
                legal_name,
                name_first,
                name_last,
                nat,
                club,
                dob,
                ca,
                pa,
                is_female,
                team_uid,
            ) in rows:
                uid_str = str(uid).strip()
                name_clean = re.sub(r"\s+", " ", (display_name or full_name or legal_name or "").strip())
                if not name_clean or uid_str in seen_uids:
                    continue
                if not is_real_player(uid_str):
                    continue
                seen_uids.add(uid_str)

                club_str = str(club).strip()
                if team_uid is not None and int(team_uid) in team_to_club_name:
                    yt_club = team_to_club_name[int(team_uid)]
                    if yt_club and yt_club.lower() not in club_str.lower():
                        club_str = f"{club_str} / {yt_club}" if club_str else yt_club

                alt_cands = _build_candidate_names(
                    name_clean,
                    str(full_name).strip(),
                    str(legal_name).strip(),
                    str(name_first).strip(),
                    str(name_last).strip(),
                )
                players.append(
                    PlayerRecord(
                        uid=uid_str,
                        name=name_clean,
                        nationality=str(nat).strip(),
                        club=club_str,
                        dob=str(dob).strip(),
                        ca=int(ca or 0),
                        pa=int(pa or 0),
                        is_female=bool(is_female),
                        alt_names=tuple(alt_cands),
                    )
                )

            if players:
                print(f"[+] Loaded {len(players):,} real FM26 player(s) directly from game database.")
                return players
        except sqlite3.Error as exc:
            print(f"[!] SQLite error reading '{db_path}': {exc}")

    return []


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
                    name=re.sub(r"\s+", " ", name.strip()),
                    nationality=nat.strip(),
                    club=club.strip(),
                    dob=dob.strip(),
                )
            )

    print(f"[+] Parsed {len(players):,} real player(s) from '{export_file.name}'.")
    return players


def _normalize_name(name: str) -> str:
    """Lowercases, strips accents/diacritics, and collapses punctuation into spaces."""
    ascii_str = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", ascii_str)).strip()


def _canonical_token(tok: str) -> str:
    """Normalizes diminutive names and Cyrillic/Turkic/Slavic transliteration variants."""
    t = DIMINUTIVE_MAP.get(tok, tok)
    t = re.sub(r"iyev$", "iev", t)
    t = re.sub(r"yev$", "ev", t)
    t = re.sub(r"yov$", "ov", t)
    t = re.sub(r"(iy|ii|yy)$", "i", t)
    t = t.replace("kh", "h").replace("zh", "j").replace("ph", "f")
    return t


def _tokens_match(t1: str, t2: str) -> bool:
    if t1 == t2:
        return True
    c1, c2 = _canonical_token(t1), _canonical_token(t2)
    if c1 == c2:
        return True
    if len(c1) >= 5 and len(c2) >= 5 and difflib.SequenceMatcher(None, c1, c2).ratio() >= 0.84:
        return True
    return False


def _build_candidate_names(
    display_name: str,
    full_name: str = "",
    legal_name: str = "",
    name_first: str = "",
    name_last: str = "",
) -> List[str]:
    """
    Builds an ordered list of candidate search queries for Transfermarkt/Wikipedia:
    - Handles 1-word nicknames (e.g. 'Dro' + 'Fernández Sarmiento' -> 'Dro Fernández', 'Pedro Fernández')
    - Handles formal/diminutive Greek/Slavic first names ('Dimitris Chatsidis' -> 'Dimitrios Chatsidis')
    - Handles Cyrillic transliteration variants ('Dastan Sätpayev' -> 'Dastan Satpaev')
    - Handles multi-word Spanish/Portuguese/Brazilian legal names
    """
    cands: List[str] = []
    seen_norms: Set[str] = set()

    def add_cand(s: str) -> None:
        s_clean = re.sub(r"\s+", " ", (s or "").strip())
        if not s_clean:
            return
        n = _normalize_name(s_clean)
        if n and n not in seen_norms:
            seen_norms.add(n)
            cands.append(s_clean)

    disp_tokens = display_name.split()
    last_tokens = [
        t
        for t in re.sub(r"\s+", " ", name_last).split()
        if t.lower() not in ("de", "da", "do", "dos", "das", "del", "van", "von", "el", "al", "jr", "junior", "filho", "neto")
    ]
    first_tokens = re.sub(r"\s+", " ", name_first).split()

    # If display_name is a 1-word nickname (like 'Dro' or 'Javier') and we have a surname, prioritize 2-word combinations
    if len(disp_tokens) == 1 and last_tokens:
        add_cand(f"{display_name} {last_tokens[0]}")
        if first_tokens:
            add_cand(f"{first_tokens[0]} {last_tokens[0]}")
            add_cand(f"{name_first} {name_last}")

    add_cand(display_name)
    if full_name:
        add_cand(full_name)
    if name_first and name_last:
        add_cand(f"{name_first} {name_last}")
        if len(last_tokens) >= 2:
            add_cand(f"{first_tokens[0] if first_tokens else name_first} {last_tokens[0]}")
            add_cand(f"{first_tokens[0] if first_tokens else name_first} {last_tokens[-1]}")
    if legal_name:
        add_cand(legal_name)

    # Expand diminutive first names (e.g. 'Dimitris Chatsidis' -> 'Dimitrios Chatsidis')
    for base in list(cands):
        parts = base.split()
        if len(parts) >= 2:
            f_norm = _normalize_name(parts[0])
            if f_norm in FORMAL_NAME_EXPANSIONS:
                add_cand(f"{FORMAL_NAME_EXPANSIONS[f_norm]} {' '.join(parts[1:])}")

    # Expand Cyrillic transliteration endings (-yev -> -ev, -iyev -> -iev, -yov -> -ov)
    for base in list(cands):
        ascii_base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
        trans = re.sub(r"iyev\b", "iev", ascii_base, flags=re.IGNORECASE)
        trans = re.sub(r"yev\b", "ev", trans, flags=re.IGNORECASE)
        trans = re.sub(r"yov\b", "ov", trans, flags=re.IGNORECASE)
        if trans.lower() != ascii_base.lower():
            add_cand(trans)

    return cands


def _extract_birth_year(dob: str) -> Optional[int]:
    """Extracts a plausible footballer birth year (1975..2014) from a YYYY-MM-DD or DD/MM/YYYY date."""
    m = re.search(r"\b(19[789]\d|200\d|201[0-4])\b", dob or "")
    if m:
        return int(m.group(1))
    return None


_NON_DEFAULT_PORTRAIT_RE = re.compile(r"/portrait/(?:small|medium|header)/(?!default\.jpg)", re.IGNORECASE)
_CLUB_STOP_WORDS = frozenset(
    {
        "club",
        "clube",
        "futbol",
        "futebol",
        "football",
        "calcio",
        "sport",
        "sporting",
        "sports",
        "united",
        "city",
        "town",
        "athletic",
        "atletico",
        "real",
        "royal",
        "koninklijke",
        "voetbalvereniging",
        "ballspielverein",
        "sportverein",
        "turn",
        "und",
        "and",
        "the",
        "del",
        "deportivo",
        "associacao",
        "asociacion",
        "sociedade",
        "esporte",
        "kulubu",
        "spor",
        "fodsports",
        "forening",
        "fc",
        "sc",
        "ac",
        "as",
        "cf",
        "cd",
        "ud",
        "sd",
        "rc",
        "kv",
        "sv",
        "bv",
        "fk",
        "sk",
        "nk",
        "if",
        "bk",
        "ik",
        "de",
        "da",
        "do",
        "la",
        "el",
        "al",
        "van",
        "von",
        "u18",
        "u19",
        "u20",
        "u21",
        "u23",
        "1899",
        "1900",
        "1904",
        "1907",
        "1909",
        "1913",
    }
)


class PhotoScraper:
    """
    Scrapes real player portrait photos using:
    1. Transfermarkt quick-search across 8 regional mirrors (primary source for real football players)
    2. Wikipedia PageImages API (secondary fallback)
    3. TheSportsDB free API (tertiary fallback)
    """

    def __init__(self, delay: float = 0.05):
        self._local = threading.local()
        self.delay = delay
        self._domain_cycle = itertools.cycle(TM_DOMAINS)
        self._domain_lock = threading.Lock()

    def _next_tm_domain(self) -> str:
        with self._domain_lock:
            return next(self._domain_cycle)

    @property
    def session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            sess.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            self._local.session = sess
        return sess

    def fetch_player_photo_bytes(self, player: PlayerRecord) -> Optional[bytes]:
        img_url = None
        tm_matched_default = False
        if not player.is_female:
            img_url, tm_matched_default = self._search_transfermarkt(player)

        # Youth / low-CA men's players whose exact Transfermarkt profile has no photo (default.jpg)
        # or who have low CA (< 118) will not have Wikipedia/TheSportsDB articles.
        birth_year = _extract_birth_year(player.dob)
        is_youth_or_low_ca = (not player.is_female) and (
            tm_matched_default or (0 < player.ca < 118) or (birth_year is not None and birth_year >= 2007 and player.ca < 130)
        )

        if not img_url and not is_youth_or_low_ca:
            img_url = self._search_wikipedia(player)
        if not img_url and player.is_female:
            img_url, _ = self._search_transfermarkt(player)
        if not img_url and not is_youth_or_low_ca:
            img_url = self._search_thesportsdb(player)

        if not img_url:
            return None

        try:
            req_headers = {}
            if "wikimedia.org" in img_url or "wikipedia.org" in img_url:
                req_headers["User-Agent"] = WIKIMEDIA_USER_AGENT
            resp = self.session.get(img_url, headers=req_headers, timeout=12)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            print(f"    [!] Failed downloading image from {img_url}: {exc}")
            return None

    def _search_transfermarkt(self, player: PlayerRecord) -> Tuple[Optional[str], bool]:
        """
        Searches Transfermarkt for the player name (and any alternative/formal/transliterated names)
        and extracts the high-res portrait URL.
        Returns (high_res_url, matched_exact_player_with_default_photo).
        """
        candidate_queries = list(player.alt_names) if player.alt_names else _build_candidate_names(player.name)
        if not candidate_queries:
            candidate_queries = [player.name]

        # Precompute all known token sets for the player
        ignore_suffix = {"jr", "junior", "filho", "neto", "ii", "iii"}
        candidate_token_lists: List[List[str]] = []
        all_known_tokens: Set[str] = set()
        for cname in candidate_queries:
            toks = [t for t in _normalize_name(cname).split() if t not in ignore_suffix]
            if toks:
                candidate_token_lists.append(toks)
                all_known_tokens.update(toks)

        # Also add surname-only fallback if all full-name queries return 0 player rows
        primary_tokens = candidate_token_lists[0] if candidate_token_lists else []
        surname_fallback: Optional[str] = None
        if len(primary_tokens) >= 2 and len(primary_tokens[-1]) >= 6:
            surname_fallback = candidate_queries[0].split()[-1]

        club_norm = _normalize_name(player.club) if player.club else ""
        club_tokens = [t for t in club_norm.split() if len(t) >= 3 and t not in _CLUB_STOP_WORDS]
        birth_year = _extract_birth_year(player.dob)
        any_rows_found = False
        matched_default_any = False

        queries_to_run = list(candidate_queries)
        q_idx = 0
        while q_idx < len(queries_to_run):
            q_str = queries_to_run[q_idx]
            q_idx += 1
            query = urllib.parse.quote(q_str)
            domain = self._next_tm_domain()
            url = f"https://{domain}/schnellsuche/ergebnis/schnellsuche?query={query}"

            try:
                if self.delay > 0:
                    time.sleep(self.delay)
                resp = self.session.get(url, timeout=10)
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException:
                # Retry once on main domain if a regional mirror timed out
                try:
                    url_fb = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={query}"
                    resp = self.session.get(url_fb, timeout=10)
                    resp.raise_for_status()
                    html = resp.text
                except requests.RequestException as exc:
                    print(f"    [!] Transfermarkt request error for '{q_str}': {exc}")
                    continue

            # Fast C-level pre-check: did Transfermarkt return any player profile rows at all?
            if "/profil/spieler/" not in html:
                if q_idx == len(queries_to_run) and not any_rows_found and surname_fallback and surname_fallback not in queries_to_run:
                    queries_to_run.append(surname_fallback)
                continue

            any_rows_found = True

            # Fast C-level pre-check: if every single row on the page has default.jpg, no need to run BeautifulSoup!
            if not _NON_DEFAULT_PORTRAIT_RE.search(html):
                matched_default_any = True
                # If this was a 2+ word query and returned player rows (all with default.jpg), stop immediately
                if len(q_str.split()) >= 2 and q_idx == 1 and len(queries_to_run) == 1:
                    return None, True
                continue

            soup = BeautifulSoup(html, "html.parser")
            matched_default = False

            for tr in soup.select("table.items > tbody > tr"):
                if not tr.select_one('a[href*="/profil/spieler/"]'):
                    continue
                img = tr.select_one("img.bilderrahmen-fixed")
                if not img:
                    continue

                src = img.get("src", "")
                alt = img.get("alt", "")
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                club_or_status = (tds[3] if len(tds) > 3 else "").lower()
                club_row_norm = _normalize_name(club_or_status)
                age_str = tds[6] if len(tds) > 6 else ""

                if club_or_status in ("retired", "career break", "karriereende", "pausiert", "---"):
                    continue

                # Validate age against FM birth year when available
                age_exact_match = False
                if birth_year is not None and age_str.isdigit():
                    tm_age = int(age_str)
                    expected_age_2026 = 2026 - birth_year
                    if abs(tm_age - expected_age_2026) > 3:
                        continue
                    age_exact_match = abs(tm_age - expected_age_2026) <= 1

                alt_norm = _normalize_name(alt)
                alt_tokens = [t for t in alt_norm.split() if t not in ignore_suffix]
                if not alt_tokens:
                    continue

                club_matches = bool(club_tokens) and any(ct in club_row_norm for ct in club_tokens)

                # Check if alt_tokens matches any candidate name of the player
                name_matches = False
                for cand_toks in candidate_token_lists:
                    if all(any(_tokens_match(ct, at) for at in alt_tokens) for ct in cand_toks):
                        name_matches = True
                        break

                # Or if alt_tokens has >= 2 tokens and all of them exist in the player's known full/legal tokens
                if not name_matches and len(alt_tokens) >= 2:
                    if all(any(_tokens_match(at, kt) for kt in all_known_tokens) for at in alt_tokens):
                        name_matches = True

                # Or if first name matches AND club + birth year match on a 2+ word query
                # (e.g. 'Enzo Duarte' -> 'Enzo dos Santos' at Borussia Dortmund, age 17)
                if not name_matches and len(q_str.split()) >= 2 and primary_tokens and alt_tokens:
                    if _tokens_match(primary_tokens[0], alt_tokens[0]) and club_matches and age_exact_match:
                        name_matches = True

                if not name_matches:
                    continue

                if not src or "default.jpg" in src or "portrait" not in src:
                    matched_default = True
                    matched_default_any = True
                    # If this row matches the player's exact club and has default.jpg, stop so we don't grab a namesake
                    if club_matches:
                        return None, True
                    continue

                # If we already skipped an earlier namesake with default.jpg, require club match for subsequent rows
                if matched_default and club_tokens and not club_matches:
                    continue

                high_res_url = re.sub(r"/portrait/(small|medium|header)/", "/portrait/big/", src)
                print(f"    [+] Found Transfermarkt portrait for {player.name} ({alt}): {high_res_url}")
                return high_res_url, False

            if q_idx == len(queries_to_run) and not any_rows_found and surname_fallback and surname_fallback not in queries_to_run:
                queries_to_run.append(surname_fallback)

        return None, matched_default_any

    def _search_wikipedia(self, player: PlayerRecord) -> Optional[str]:
        """
        Searches English Wikipedia's PageImages API for the footballer's portrait.
        """
        search_term = f"{player.name} footballer"
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search_term,
            "gsrlimit": "3",
            "prop": "pageimages|description",
            "piprop": "thumbnail|original",
            "pithumbsize": "500",
        }
        url = "https://en.wikipedia.org/w/api.php"
        try:
            if self.delay > 0:
                time.sleep(self.delay * 0.5)
            resp = requests.get(
                url,
                params=params,
                headers={"User-Agent": WIKIMEDIA_USER_AGENT},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

        pages = (data.get("query") or {}).get("pages") or {}
        sorted_pages = sorted(pages.values(), key=lambda p: p.get("index", 999))
        known_norms = {_normalize_name(player.name)} | {_normalize_name(a) for a in player.alt_names}
        birth_year = _extract_birth_year(player.dob)

        for page in sorted_pages:
            raw_title = page.get("title") or ""
            stripped_title = re.sub(r"\s*\(.*?\)", "", raw_title).strip()
            title_norm = _normalize_name(stripped_title)
            desc = (page.get("description") or "").lower()

            if title_norm not in known_norms:
                continue
            if not any(
                w in desc
                for w in (
                    "football",
                    "soccer",
                    "midfielder",
                    "forward",
                    "defender",
                    "goalkeeper",
                    "striker",
                    "winger",
                )
            ):
                continue

            if birth_year is not None:
                desc_year = _extract_birth_year(desc)
                if desc_year is not None and abs(desc_year - birth_year) > 2:
                    continue

            thumb = (page.get("thumbnail") or {}).get("source")
            orig = (page.get("original") or {}).get("source")
            img_url = thumb or orig
            if img_url and not img_url.lower().endswith(".svg"):
                print(f"    [+] Found Wikipedia portrait for {player.name} ({raw_title}): {img_url}")
                return img_url

        return None

    def _search_thesportsdb(self, player: PlayerRecord) -> Optional[str]:
        """
        Fallback lookup via TheSportsDB's free v1 test API (looks for strCutout or strThumb).
        """
        query = urllib.parse.quote(player.name)
        url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={query}"

        try:
            if self.delay > 0:
                time.sleep(self.delay * 0.5)
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            if not resp.text.strip():
                return None
            data = resp.json()
        except Exception:
            return None

        known_norms = {_normalize_name(player.name)} | {_normalize_name(a) for a in player.alt_names}
        birth_year = _extract_birth_year(player.dob)
        results = data.get("player") or []
        for entry in results:
            if entry.get("strSport") != "Soccer":
                continue
            if _normalize_name(entry.get("strPlayer") or "") not in known_norms:
                continue
            if birth_year is not None:
                entry_year = _extract_birth_year(entry.get("dateBorn") or "")
                if entry_year is not None and abs(entry_year - birth_year) > 2:
                    continue
            img_url = entry.get("strCutout") or entry.get("strThumb")
            if img_url:
                print(f"    [+] Found TheSportsDB portrait for {player.name}: {img_url}")
                return img_url

        return None


def _score_alpha_mask(a) -> float:
    h, w = a.shape
    solid = a >= 180
    sr = float(solid.mean())
    fc = float(solid[int(h * 0.12) : int(h * 0.55), int(w * 0.30) : int(w * 0.70)].mean())
    tc = float(solid[int(h * 0.55) : int(h * 0.95), int(w * 0.25) : int(w * 0.75)].mean())
    if sr > 0.86:
        return fc * 0.3
    return fc * 0.65 + tc * 0.25 + min(sr, 0.55) * 0.20


def process_and_save_portrait(
    image_bytes: bytes,
    output_path: Path,
    size: int = 180,
    remove_bg: bool = True,
) -> bool:
    """
    Converts raw image bytes into a square (180x180) RGBA PNG suitable for Football Manager.
    - If the source image is ALREADY a pre-cutout transparent PNG, uses its native alpha mask directly.
    - Otherwise removes the background on the full-resolution portrait first via `rembg`
      (u2net_human_seg, with fallback to u2net on tight crops) to isolate the player,
      then scales the head-and-collar cutout into 180x180.
    """
    try:
        if remove_bg and HAS_REMBG:
            import numpy as np

            src_pil = Image.open(io.BytesIO(image_bytes))
            has_existing_cutout = False
            if src_pil.mode in ("RGBA", "LA") or "transparency" in src_pil.info:
                rgba_arr = np.array(src_pil.convert("RGBA"))
                existing_a = rgba_arr[:, :, 3]
                trans_ratio = float((existing_a < 15).mean())
                solid_ratio = float((existing_a > 200).mean())
                if trans_ratio >= 0.12 and 0.15 <= solid_ratio <= 0.85:
                    raw_arr = rgba_arr[:, :, :3]
                    best_a = existing_a
                    has_existing_cutout = True

            if not has_existing_cutout:
                raw_img = src_pil.convert("RGB")
                raw_arr = np.array(raw_img)
                session = get_rembg_session("u2net_human_seg")
                if session is None:
                    with Image.open(io.BytesIO(image_bytes)) as img:
                        img = img.convert("RGBA")
                        fitted = ImageOps.fit(img, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.2))
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        fitted.save(output_path, format="PNG")
                    return True
                cutout_img = _REMBG_REMOVE(raw_img, session=session).convert("RGBA")
                best_a = np.array(cutout_img)[:, :, 3]
                best_score = _score_alpha_mask(best_a)

                # Fallback to u2net if u2net_human_seg over-cuts a tightly zoomed portrait
                if best_score < 0.52:
                    try:
                        fb_sess = get_rembg_session("u2net")
                        if fb_sess is not None:
                            fb_a = np.array(_REMBG_REMOVE(raw_img, session=fb_sess).convert("RGBA"))[:, :, 3]
                            union_a = np.maximum(best_a, fb_a)
                            candidates = [
                                (best_a, best_score),
                                (fb_a, _score_alpha_mask(fb_a)),
                                (union_a, _score_alpha_mask(union_a)),
                            ]
                            best_a, _ = max(candidates, key=lambda item: item[1])
                    except Exception:
                        pass

            # Remove faint alpha halo noise and harden semi-transparent foreground
            a = best_a.copy()
            h, w = a.shape
            a[a < 25] = 0
            a[a >= 85] = 255

            # Fill small interior horizontal holes (<= 15% of width)
            solid = a >= 180
            max_hole = max(4, int(w * 0.15))
            for y in range(h):
                xs = np.where(solid[y])[0]
                if len(xs) >= 2:
                    span = xs[-1] - xs[0] + 1
                    hole_px = span - len(xs)
                    if 0 < hole_px <= max_hole:
                        a[y, xs[0] : xs[-1] + 1] = 255

            img = Image.fromarray(np.dstack([raw_arr, a]).astype(np.uint8), "RGBA")

            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)

            # If source image is a full-body/half-body shot (much taller than wide), keep head & shoulders
            w, h = img.size
            if h > int(w * 1.45):
                img = img.crop((0, 0, w, int(w * 1.30)))
                bbox2 = img.getbbox()
                if bbox2:
                    img = img.crop(bbox2)
                w, h = img.size

            target_h = size - 4
            scale = min(size / max(1, w), target_h / max(1, h))
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            paste_x = (size - new_w) // 2
            paste_y = size - new_h
            canvas.paste(resized, (paste_x, paste_y), resized)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path, format="PNG")
            return True

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
        "\t<!-- resource manager options -->",
        '\t<boolean id="preload" value="false"/>',
        '\t<boolean id="amap" value="false"/>',
        "",
        "\t<!-- picture mappings -->",
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


def update_fm26_facepack_index(bepinex_index: Optional[Path], output_dir: Path) -> None:
    """
    If FM26's PitchHUD facepack index cache exists (`PitchHUD.facepack-index.txt`),
    registers `output_dir` in it so FM26 loads the new facepack folder without needing a full rescan.
    """
    if not bepinex_index or not bepinex_index.is_file():
        return
    try:
        lines = bepinex_index.read_text(encoding="utf-8", errors="ignore").splitlines()
        out_str = str(output_dir)
        if not any(line.strip().lower() == out_str.lower() for line in lines):
            lines.append(out_str)
            bepinex_index.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"[+] Registered '{output_dir}' in FM26 PitchHUD index ('{bepinex_index.name}').")
    except OSError as exc:
        print(f"[!] Could not update PitchHUD index '{bepinex_index}': {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-detect FM26 files, back up existing configs, find real players missing photos, and build a cutout facepack."
    )
    parser.add_argument(
        "--export-file",
        type=Path,
        default=None,
        help="Optional path to an RTF/text player list exported from Football Manager. If omitted, reads directly from FM26 database.",
    )
    parser.add_argument(
        "--graphics-dir",
        type=Path,
        default=None,
        help="Optional path to your FM26 'Graphics' directory (auto-detected if omitted).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Folder where scraped faces & config.xml will be saved (defaults to <graphics-dir>/Faces Auto FM26).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "backups",
        help="Directory where safety backups of config.xml files are stored before execution.",
    )
    parser.add_argument(
        "--gender",
        choices=["all", "men", "women"],
        default="men",
        help="Filter players when reading from FM26 database: 'men' (default), 'women', or 'all'.",
    )
    parser.add_argument(
        "--league",
        type=str,
        default=None,
        help="Optional league name, alias ('big5', 'top25', 'top30'), or UID to filter squad players.",
    )
    parser.add_argument(
        "--all-youth",
        action="store_true",
        help="When --league is used, also include all low-CA U18/U19/U20/U21/U23/B youth academy players.",
    )
    parser.add_argument(
        "--min-ca",
        type=int,
        default=125,
        help="Minimum Current Ability (CA) when auto-loading globally from FM26 database (default: 125).",
    )
    parser.add_argument(
        "--no-remove-bg",
        action="store_true",
        help="Disable automatic background removal (by default, background is removed via rembg).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Polite delay in seconds between web requests per worker (default: 0.05s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Number of concurrent HTTP workers for scraping player photos (default: 16).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of missing players to scrape in one run (0 = no limit).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached negative (no-photo) lookup results from previous runs.",
    )

    args = parser.parse_args()
    remove_bg = not args.no_remove_bg

    print("=== FM Auto-Faces (FM26 Discovery & Backup) ===")
    env = discover_fm26_environment()

    if env.game_install_dir:
        print(f"[+] Found FM26 installation: '{env.game_install_dir}'")
    if env.official_db_dir:
        print(f"[+] Found FM26 official DB:  '{env.official_db_dir}'")
    if env.user_docs_dir:
        print(f"[+] Found FM26 user folder:  '{env.user_docs_dir}'")
    if env.saves_dir:
        saves = list(env.saves_dir.glob("*.fm"))
        print(f"[+] Found FM26 save files:   {len(saves)} save(s) in '{env.saves_dir}'")
    if env.editor_data_dir:
        fmfs = list(env.editor_data_dir.glob("*.fmf"))
        print(f"[+] Found FM26 editor data:  {len(fmfs)} .fmf file(s) in '{env.editor_data_dir}'")

    graphics_dir: Optional[Path] = args.graphics_dir or env.graphics_dir
    if graphics_dir is None:
        raise SystemExit("[!] Could not auto-detect FM26 Graphics folder. Please pass --graphics-dir.")

    output_dir: Path = args.output_dir or (graphics_dir / "Faces Auto FM26")

    # 1. Always create safety backup BEFORE scanning/modifying anything
    create_backup(
        graphics_dir=graphics_dir,
        output_dir=output_dir,
        backup_root=args.backup_dir,
        extra_files=[env.bepinex_facepack_index] if env.bepinex_facepack_index else None,
    )

    # 2. Scan existing facepacks in FM26 Graphics (plus any PNGs already in output_dir)
    existing_uids = scan_existing_facepacks(graphics_dir)
    if output_dir.exists():
        for png in output_dir.glob("*.png"):
            if png.stem.isdigit():
                existing_uids.add(png.stem)

    # Load negative-lookup cache so interrupted/repeated runs skip confirmed no-photo UIDs
    cache_file = args.backup_dir / "checked_no_photo_cache.json"
    no_photo_cache: Set[str] = set()
    if not args.no_cache and cache_file.is_file():
        try:
            no_photo_cache = set(json.loads(cache_file.read_text(encoding="utf-8")))
        except Exception:
            no_photo_cache = set()

    # 3. Load real players from export file OR directly from FM26 database
    real_players: List[PlayerRecord] = []
    if args.export_file:
        if not args.export_file.is_file():
            raise SystemExit(f"[!] Export file not found: '{args.export_file}'")
        real_players = parse_fm_export(args.export_file)
    elif env.live_sqlite_dbs:
        real_players = load_players_from_fm26_sqlite(
            env.live_sqlite_dbs,
            gender_filter=args.gender,
            min_ca=args.min_ca,
            league_filter=args.league,
            include_all_youth=args.all_youth,
        )
    else:
        sample_rtf = Path(__file__).resolve().parent / "sample_export.rtf"
        if sample_rtf.is_file():
            print(f"[!] No live FM26 database found; falling back to '{sample_rtf.name}'.")
            real_players = parse_fm_export(sample_rtf)
        else:
            raise SystemExit("[!] Could not find an FM26 player database or export file.")

    missing_players = [p for p in real_players if p.uid not in existing_uids and p.uid not in no_photo_cache]
    print(f"[*] {len(missing_players):,} real player(s) (matching filter) are missing a face in FM26.")

    if args.limit > 0 and len(missing_players) > args.limit:
        missing_players = missing_players[: args.limit]
        print(f"[*] Limiting run to top {len(missing_players)} missing player(s) by CA/PA.")

    if remove_bg and HAS_REMBG and missing_players:
        get_rembg_session()

    scraper = PhotoScraper(delay=args.delay)
    success_count = 0
    total_missing = len(missing_players)
    workers = max(1, args.workers)
    added_players: List[Dict[str, object]] = []
    added_log_path = args.backup_dir / "added_players_latest.json"
    if added_log_path.is_file():
        try:
            prev_added = json.loads(added_log_path.read_text(encoding="utf-8"))
            if isinstance(prev_added, list):
                added_players.extend(prev_added)
        except Exception:
            pass

    def _scrape_and_process(player: PlayerRecord) -> Tuple[bool, Optional[str]]:
        img_bytes = scraper.fetch_player_photo_bytes(player)
        if not img_bytes:
            return False, None
        out_png = output_dir / f"{player.uid}.png"
        ok = process_and_save_portrait(img_bytes, out_png, remove_bg=remove_bg)
        return ok, out_png.name if ok else None

    if workers == 1 or total_missing <= 1:
        for idx, player in enumerate(missing_players, start=1):
            ca_info = f", CA: {player.ca}/PA: {player.pa}" if player.ca else ""
            print(
                f"[{idx}/{total_missing}] Searching photo for {player.name} "
                f"(UID: {player.uid}, Club: {player.club or 'N/A'}{ca_info})..."
            )
            ok, png_name = _scrape_and_process(player)
            if not ok:
                no_photo_cache.add(player.uid)
                print(f"    [-] No photo found online for {player.name}.")
                continue

            print(f"    [+] Saved cutout {output_dir / png_name}")
            success_count += 1
            added_players.append(
                {
                    "uid": player.uid,
                    "name": player.name,
                    "nationality": player.nationality,
                    "club": player.club,
                    "dob": player.dob,
                    "ca": player.ca,
                    "pa": player.pa,
                }
            )
    else:
        print(f"[*] Scraping {total_missing:,} missing player(s) using {workers} concurrent workers...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_item = {
                pool.submit(_scrape_and_process, player): (idx, player)
                for idx, player in enumerate(missing_players, start=1)
            }
            completed = 0
            for future in as_completed(future_to_item):
                idx, player = future_to_item[future]
                completed += 1
                try:
                    ok, png_name = future.result()
                except Exception as exc:
                    print(f"[{completed}/{total_missing}] [!] Error fetching {player.name}: {exc}")
                    continue

                if not ok:
                    no_photo_cache.add(player.uid)
                    if completed % 200 == 0 or completed == total_missing:
                        print(
                            f"[*] Progress: {completed:,}/{total_missing:,} checked "
                            f"({success_count:,} new cutout face(s) saved so far)..."
                        )
                        try:
                            cache_file.write_text(json.dumps(sorted(no_photo_cache)), encoding="utf-8")
                        except OSError:
                            pass
                    continue

                success_count += 1
                added_players.append(
                    {
                        "uid": player.uid,
                        "name": player.name,
                        "nationality": player.nationality,
                        "club": player.club,
                        "dob": player.dob,
                        "ca": player.ca,
                        "pa": player.pa,
                    }
                )
                ca_info = f", CA: {player.ca}/PA: {player.pa}" if player.ca else ""
                print(
                    f"[{completed}/{total_missing}] [+] Saved cutout #{success_count} for {player.name} "
                    f"(UID: {player.uid}, Club: {player.club or 'N/A'}{ca_info}) -> {png_name}"
                )
                if success_count % 50 == 0:
                    generate_config_xml(output_dir)
                    try:
                        added_log_path.write_text(
                            json.dumps(added_players, indent=2, ensure_ascii=False), encoding="utf-8"
                        )
                    except OSError:
                        pass

    try:
        cache_file.write_text(json.dumps(sorted(no_photo_cache)), encoding="utf-8")
        added_log_path.write_text(json.dumps(added_players, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    generate_config_xml(output_dir)
    update_fm26_facepack_index(env.bepinex_facepack_index, output_dir)

    print(
        f"\n[Done] Added {success_count} new player photo(s) to '{output_dir}'.\n"
        "In Football Manager 26: reload your skin/cache (or press F11 in PitchHUD) to see the new faces!"
    )


if __name__ == "__main__":
    main()

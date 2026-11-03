# FM Auto-Faces (FM26 Ready)

A Python tool for **Football Manager 26** (and earlier versions) that:
1. **Automatically discovers your real FM26 files**:
   - Steam game installation & official `2620` database (`Football Manager 26/shared/data/database/db/2620`)
   - User documents folder (`Documents/Sports Interactive/Football Manager 26`), `.fm` saves, and `.fmf` editor data
   - Existing `Graphics` facepacks (`União Up Facepack`, `Faces Extras`, etc.)
   - Live/snapshot FM26 SQLite player databases (or exported `.rtf`/`.txt` player lists)
2. **Creates an automatic safety backup**:
   - Before making any changes, backs up all existing `config.xml` files, target output files, and `PitchHUD.facepack-index.txt` into `backups/backup_fm26_<timestamp>/` with SHA-256 verification (`backup_manifest.json`).
3. **Scans 540,000+ existing faces in seconds** (`config.xml` files) to find every player UID that already has a photo.
4. **Identifies real database players missing a photo** (filtering out generated Newgens/Regens via `is_regen = 0` and `r-` prefix, sorted by Current/Potential Ability).
5. **Scrapes their real portrait** from **Transfermarkt**, **Wikipedia PageImages API** (supporting both Men's and Women's football in FM26), and **TheSportsDB API**.
6. **Resizes/crops the photo** to FM26's `180x180` `.png` format (with optional AI background removal via `rembg` for cutout faces).
7. **Generates `config.xml`** and registers the facepack folder in FM26's `PitchHUD.facepack-index.txt` so FM26 loads the new faces immediately.

---

## Quick Start (FM26 Auto-Discovery)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
# Optional: if you want automatic background removal (cutout style):
pip install rembg onnxruntime
```

### 2. Run Automatically on FM26
Simply run the script with no required arguments—it will automatically create a backup, locate your FM26 installation and `Graphics` folder, read the real players from your FM26 database, and download missing portraits:
```bash
python fm_auto_faces.py
```

#### Useful Options for FM26:
```bash
# Scrape all missing squad players in a specific league (e.g. Premier League):
python fm_auto_faces.py --league "Premier League"

# Scrape all missing players across all Big Five leagues (Premier League, LaLiga, Serie A, Bundesliga, Ligue 1)
# including all Sub-20 / Sub-18 / U21 / U19 / B-Team youth squads using concurrent workers:
python fm_auto_faces.py --league big5 --all-youth --workers 12

# Scrape top 20 missing Men's football wonderkids/players globally:
python fm_auto_faces.py --gender men --limit 20

# Or run from a custom exported RTF/text file:
python fm_auto_faces.py --export-file sample_export.rtf
```

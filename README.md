# FM Auto-Faces Prototype

A Python tool for **Football Manager** that:
1. Scans your FM `graphics/` folder (`config.xml` files) to find every player UID that already has a photo.
2. Parses an exported FM player search `.rtf` / `.txt` file and filters out generated **Newgens** (`r-` prefix).
3. Diffs the two lists to identify **real database players missing a photo**.
4. Scrapes their real portrait from **Transfermarkt** (with a fallback to **TheSportsDB API**).
5. Resizes/crops the photo to FM's `180x180` `.png` format (with optional AI background removal via `rembg` for cutout faces).
6. Generates a `config.xml` file so Football Manager loads the new faces immediately upon **Reload Skin**.

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
# Optional: if you want automatic background removal (cutout style):
pip install rembg onnxruntime
```

### 2. Export Players from Football Manager
1. In Football Manager, go to **Scouting -> Players in Range** (make sure "Include players from your own club" is checked, or use the pre-game/in-game search).
2. Customize the view columns to match:
   `Unique ID` | `Name` | `Nationality` | `Club` | `Age` | `Date of Birth`
3. Scroll through or select all (`Ctrl+A`), press **`Ctrl+P`**, choose **Text File**, and save it as `players.rtf`.

### 3. Run the Script
Test with the included `sample_export.rtf`:
```bash
python3 fm_auto_faces.py \
  --export-file sample_export.rtf \
  --graphics-dir ./mock_graphics
```

Or run it against your actual Football Manager `graphics` directory:
```bash
python3 fm_auto_faces.py \
  --export-file ~/Documents/Sports\ Interactive/Football\ Manager\ 2024/players.rtf \
  --graphics-dir ~/Documents/Sports\ Interactive/Football\ Manager\ 2024/graphics \
  --remove-bg
```

### 4. Reload Skin in FM
Open Football Manager -> **Preferences** -> **Interface**:
1. Uncheck *"Use caching to decrease page loading times"*
2. Check *"Reload skin when confirming changes in Preferences"*
3. Click **Reload Skin**.

# AudiobookBuilder

Cross-platform desktop app (Windows/macOS) for building a single chaptered `.m4b` audiobook from multiple source files.

## Features
- Import `.mp3`, `.aac`, `.m4a`, `.mp4`
- Load tags/artwork from original files at import time
- View/edit common tags for one or multiple selected files
- View, replace, or remove artwork for one or multiple selected files
- Reorder files with Up/Down controls
- Select which files are included in export
- Export a single `.m4b` with chapters and embedded cover artwork
- Set custom `ffmpeg`/`ffprobe` location from the GUI when PATH is missing

## Requirements
- Python 3.10+
- FFmpeg available on PATH (`ffmpeg`, `ffprobe`)

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
python -m audiobook_builder
```

## Build

### Windows (local)

```powershell
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --onefile --name M4B-Builder audiobook_builder/__main__.py
```

Output: `dist/M4B-Builder.exe`

### macOS (recommended via GitHub Actions)
Use the workflow at `.github/workflows/build-desktop.yml`.
It builds on `macos-latest` and uploads a `M4B-Builder-macos` artifact.

## Download Executables From GitHub

1. Open the repository on GitHub: `https://github.com/alfredtp22/M4B-Builder`.
2. Click `Actions`.
3. Open the latest successful `Build Desktop App` workflow run on the `main` branch.
4. In `Artifacts`, download:
   - `M4B-Builder-windows` (contains `M4B-Builder.exe`)
   - `M4B-Builder-macos` (contains `M4B-Builder`)

## Run Executables

### Windows
1. Unzip `M4B-Builder-windows.zip`.
2. Run `M4B-Builder.exe`.
3. If SmartScreen appears, click `More info` -> `Run anyway`.
4. Make sure `ffmpeg` and `ffprobe` are installed, or use the app's `Set FFmpeg` button.

### macOS
1. Unzip `M4B-Builder-macos.zip`.
2. Open Terminal in the extracted folder.
3. Make executable and run:

```bash
chmod +x M4B-Builder
./M4B-Builder
```

4. If macOS blocks the first run, remove quarantine and retry:

```bash
xattr -d com.apple.quarantine M4B-Builder
./M4B-Builder
```

## Notes
- The exported `.m4b` uses the artwork of the first selected file.
- Chapter titles default to each file's title tag when available, otherwise filename.
- Export re-encodes audio to AAC for broad compatibility (including iOS Books).
- If FFmpeg is not on PATH, click `Set FFmpeg` and choose the `ffmpeg` executable; the app saves that path.

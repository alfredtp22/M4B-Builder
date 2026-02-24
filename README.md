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

## Notes
- The exported `.m4b` uses the artwork of the first selected file.
- Chapter titles default to each file's title tag when available, otherwise filename.
- Export re-encodes audio to AAC for broad compatibility (including iOS Books).
- If FFmpeg is not on PATH, click `Set FFmpeg` and choose the `ffmpeg` executable; the app saves that path.

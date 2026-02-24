import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from mutagen import File as MutagenFile
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


AUDIO_EXTENSIONS = (".mp3", ".aac", ".m4a", ".mp4")


@dataclass
class AudioItem:
    path: Path
    title: str
    artist: str
    album: str
    duration: float
    tags: Dict[str, str] = field(default_factory=dict)
    artwork_bytes: Optional[bytes] = None
    artwork_mime: Optional[str] = None
    selected: bool = True


class AudioMetadataService:
    def load_item(self, path: Path) -> AudioItem:
        audio = MutagenFile(path)
        if audio is None:
            raise ValueError(f"Unsupported or unreadable file: {path}")

        duration = 0.0
        if getattr(audio, "info", None) and getattr(audio.info, "length", None):
            duration = float(audio.info.length)

        title = ""
        artist = ""
        album = ""
        artwork_bytes = None
        artwork_mime = None

        if isinstance(audio, MP4):
            title = self._first(audio.tags.get("\xa9nam")) if audio.tags else ""
            artist = self._first(audio.tags.get("\xa9ART")) if audio.tags else ""
            album = self._first(audio.tags.get("\xa9alb")) if audio.tags else ""
            if audio.tags and "covr" in audio.tags and audio.tags["covr"]:
                cover = audio.tags["covr"][0]
                artwork_bytes = bytes(cover)
                artwork_mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
        elif path.suffix.lower() in (".mp3", ".aac"):
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            title = id3.get("TIT2").text[0] if id3.get("TIT2") else ""
            artist = id3.get("TPE1").text[0] if id3.get("TPE1") else ""
            album = id3.get("TALB").text[0] if id3.get("TALB") else ""
            apic = next((v for k, v in id3.items() if k.startswith("APIC")), None)
            if apic:
                artwork_bytes = apic.data
                artwork_mime = apic.mime

        generic_tags = getattr(audio, "tags", None)
        if generic_tags:
            if not title:
                title = self._first(generic_tags.get("title"))
            if not artist:
                artist = self._first(generic_tags.get("artist"))
            if not album:
                album = self._first(generic_tags.get("album"))
            if artwork_bytes is None:
                for key, value in generic_tags.items():
                    key_name = str(key).lower()
                    if key_name.startswith("apic") and getattr(value, "data", None):
                        artwork_bytes = value.data
                        artwork_mime = getattr(value, "mime", "image/jpeg")
                        break

        if not title:
            title = path.stem

        tags = {"title": title, "artist": artist, "album": album}
        return AudioItem(
            path=path,
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            tags=tags,
            artwork_bytes=artwork_bytes,
            artwork_mime=artwork_mime,
        )

    def save_tags(self, item: AudioItem) -> None:
        path = item.path
        suffix = path.suffix.lower()

        if suffix in (".mp3", ".aac"):
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.setall("TIT2", [TIT2(encoding=3, text=item.tags.get("title", ""))])
            id3.setall("TPE1", [TPE1(encoding=3, text=item.tags.get("artist", ""))])
            id3.setall("TALB", [TALB(encoding=3, text=item.tags.get("album", ""))])
            self._write_mp3_artwork(id3, item.artwork_bytes, item.artwork_mime)
            id3.save(path)
        elif suffix in (".m4a", ".mp4"):
            mp4 = MP4(path)
            mp4.tags["\xa9nam"] = [item.tags.get("title", "")]
            mp4.tags["\xa9ART"] = [item.tags.get("artist", "")]
            mp4.tags["\xa9alb"] = [item.tags.get("album", "")]
            self._write_mp4_artwork(mp4, item.artwork_bytes, item.artwork_mime)
            mp4.save()
        else:
            raise ValueError(f"Tag editing not supported for {path.suffix}")

        item.title = item.tags.get("title", "") or item.path.stem
        item.artist = item.tags.get("artist", "")
        item.album = item.tags.get("album", "")

    @staticmethod
    def _write_mp3_artwork(id3: ID3, artwork_bytes: Optional[bytes], artwork_mime: Optional[str]) -> None:
        for key in [k for k in id3.keys() if k.startswith("APIC")]:
            id3.delall(key)
        if artwork_bytes:
            mime = artwork_mime or "image/jpeg"
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=artwork_bytes))

    @staticmethod
    def _write_mp4_artwork(mp4: MP4, artwork_bytes: Optional[bytes], artwork_mime: Optional[str]) -> None:
        if artwork_bytes:
            is_png = (artwork_mime or "").lower().endswith("png")
            fmt = MP4Cover.FORMAT_PNG if is_png else MP4Cover.FORMAT_JPEG
            mp4.tags["covr"] = [MP4Cover(artwork_bytes, imageformat=fmt)]
        else:
            mp4.tags.pop("covr", None)

    @staticmethod
    def _first(value):
        if isinstance(value, list) and value:
            return str(value[0])
        return str(value) if value else ""


class ExportService:
    def __init__(self) -> None:
        self.config_dir = Path.home() / ".audiobookbuilder"
        self.config_file = self.config_dir / "config.json"
        self.ffmpeg = self._discover_binary("ffmpeg")
        self.ffprobe = self._discover_binary("ffprobe")

    def check_dependencies(self) -> Tuple[bool, str]:
        self.ffmpeg = self._discover_binary("ffmpeg")
        self.ffprobe = self._discover_binary("ffprobe")
        if not self.ffmpeg:
            return False, "ffmpeg not found. Install FFmpeg or set path via the app's Set FFmpeg button."
        if not self.ffprobe:
            return False, "ffprobe not found. Install FFmpeg or set path via the app's Set FFmpeg button."
        return True, ""

    def export_m4b(self, items: List[AudioItem], output_file: Path) -> None:
        ok, message = self.check_dependencies()
        if not ok:
            raise RuntimeError(message)

        selected = [item for item in items if item.selected]
        if not selected:
            raise ValueError("No files selected for export.")

        with tempfile.TemporaryDirectory(prefix="audiobookbuilder_") as tmp:
            tmp_path = Path(tmp)
            meta_path = tmp_path / "ffmetadata.txt"
            artwork_path = tmp_path / "cover.jpg"

            metadata_lines = [";FFMETADATA1"]
            metadata_lines.append(f"title={self._escape_meta(selected[0].album or output_file.stem)}")

            start_ms = 0
            for item in selected:
                duration_ms = int(max(item.duration, 0.0) * 1000)
                end_ms = start_ms + max(duration_ms, 1)
                metadata_lines.extend(
                    [
                        "[CHAPTER]",
                        "TIMEBASE=1/1000",
                        f"START={start_ms}",
                        f"END={end_ms}",
                        f"title={self._escape_meta(item.title or item.path.stem)}",
                    ]
                )
                start_ms = end_ms

            meta_path.write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")

            artwork_bytes = selected[0].artwork_bytes
            if artwork_bytes:
                artwork_path.write_bytes(artwork_bytes)
            else:
                # 1x1 placeholder JPEG in base64 so the output always has cover art.
                fallback = (
                    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBAQEBAPEA8PDw8PDw8PDw8QDw8PFREWFhUR"
                    b"FRUYHSggGBolGxUVITEhJSorLi4uFx8zODMsNygtLisBCgoKDg0OGhAQGzclHyUtLS0tLS0tLS0tLS0t"
                    b"LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAQMBIgACEQEDEQH/xAAXAAEBAQEA"
                    b"AAAAAAAAAAAAAAABAgME/8QAFhEBAQEAAAAAAAAAAAAAAAAAABEh/9oADAMBAAIQAxAAAAHBz//EABgQ"
                    b"AQADAQAAAAAAAAAAAAAAAAEAAhES/9oACAEBAAEFAks7j//EABYRAQEBAAAAAAAAAAAAAAAAAAABEf/a"
                    b"AAgBAwEBPwGn/8QAFhEBAQEAAAAAAAAAAAAAAAAAABEh/9oACAECAQE/AYf/xAAZEAEBAQEBAQAAAAAAAA"
                    b"AAAAABABEhMWH/2gAIAQEABj8C0S+7m//EABsQAQACAgMAAAAAAAAAAAAAAAEAESExQVFh/9oACAEBAAEI"
                    b"QHfL8EcUE0qYp//aAAwDAQACAAMAAAAQ8//EABYRAQEBAAAAAAAAAAAAAAAAAAABEf/aAAgBAwEBPxBY/"
                    b"8QAFhEBAQEAAAAAAAAAAAAAAAAAARAR/9oACAECAQE/EIh//8QAHBABAQADAAMAAAAAAAAAAAAAAREAIT"
                    b"FBYYGR/9oACAEBAAE/ELKi6R2bT+5M4ovGASRUYQ6FU//Z"
                )
                artwork_path.write_bytes(base64.b64decode(fallback))

            cmd = [self.ffmpeg, "-y"]
            for item in selected:
                cmd.extend(["-i", str(item.path)])
            cmd.extend(["-i", str(meta_path), "-i", str(artwork_path)])

            audio_refs = "".join(f"[{idx}:a]" for idx in range(len(selected)))
            concat_filter = f"{audio_refs}concat=n={len(selected)}:v=0:a=1[outa]"
            metadata_input = len(selected)
            artwork_input = len(selected) + 1

            cmd.extend(
                [
                    "-filter_complex",
                    concat_filter,
                    "-map",
                    "[outa]",
                    "-map",
                    f"{artwork_input}:v",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-c:v",
                    "mjpeg",
                    "-disposition:v",
                    "attached_pic",
                    "-map_metadata",
                    str(metadata_input),
                    "-map_chapters",
                    str(metadata_input),
                    "-movflags",
                    "+faststart",
                    str(output_file),
                ]
            )
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg failed")

    @staticmethod
    def _escape_meta(value: str) -> str:
        return value.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")

    def set_binaries(self, ffmpeg_path: Path, ffprobe_path: Path) -> None:
        if not ffmpeg_path.exists():
            raise ValueError(f"ffmpeg not found: {ffmpeg_path}")
        if not ffprobe_path.exists():
            raise ValueError(f"ffprobe not found: {ffprobe_path}")

        self.config_dir.mkdir(parents=True, exist_ok=True)
        config = {"ffmpeg": str(ffmpeg_path), "ffprobe": str(ffprobe_path)}
        self.config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

        self.ffmpeg = str(ffmpeg_path)
        self.ffprobe = str(ffprobe_path)

    def _discover_binary(self, binary_name: str) -> Optional[str]:
        env_key = f"AUDIOBOOKBUILDER_{binary_name.upper()}"
        env_value = os.getenv(env_key)
        if env_value and Path(env_value).exists():
            return env_value

        path_value = shutil.which(binary_name)
        if path_value:
            return path_value

        configured = self._from_config(binary_name)
        if configured:
            return configured

        for candidate in self._common_candidates(binary_name):
            if candidate.exists():
                return str(candidate)
        return None

    def _from_config(self, binary_name: str) -> Optional[str]:
        if not self.config_file.exists():
            return None
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = data.get(binary_name)
        if value and Path(value).exists():
            return value
        return None

    @staticmethod
    def _common_candidates(binary_name: str) -> List[Path]:
        if os.name == "nt":
            exe = f"{binary_name}.exe"
            localappdata = Path(os.getenv("LOCALAPPDATA", ""))
            return [
                Path("C:/ffmpeg/bin") / exe,
                Path("C:/Program Files/ffmpeg/bin") / exe,
                Path("C:/Program Files (x86)/ffmpeg/bin") / exe,
                localappdata / "Microsoft/WinGet/Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" / "ffmpeg-8.0-full_build/bin" / exe,
            ]

        return [
            Path("/opt/homebrew/bin") / binary_name,
            Path("/usr/local/bin") / binary_name,
            Path("/usr/bin") / binary_name,
        ]


class AudiobookBuilderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AudiobookBuilder")
        self.root.geometry("980x620")

        self.metadata_service = AudioMetadataService()
        self.export_service = ExportService()

        self.items: List[AudioItem] = []
        self.current_index: Optional[int] = None
        self.current_selection_ids: List[str] = []
        self.cover_preview_image = None

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(container)
        right = ttk.Frame(container)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(10, 0))

        button_row = ttk.Frame(left)
        button_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(button_row, text="Import Files", command=self.import_files).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(button_row, text="Move Up", command=lambda: self.move_selected(-1)).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Move Down", command=lambda: self.move_selected(1)).pack(side=tk.LEFT, padx=6)
        ttk.Button(button_row, text="Set FFmpeg", command=self.configure_ffmpeg).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(button_row, text="Export M4B", command=self.export_m4b).pack(side=tk.RIGHT)

        columns = ("selected", "name", "duration")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("selected", text="Use")
        self.tree.heading("name", text="File / Title")
        self.tree.heading("duration", text="Duration")
        self.tree.column("selected", width=50, anchor=tk.CENTER)
        self.tree.column("name", width=520)
        self.tree.column("duration", width=90, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.toggle_selected)

        details = ttk.LabelFrame(right, text="Metadata", padding=10)
        details.pack(fill=tk.X)

        ttk.Label(details, text="Title").grid(row=0, column=0, sticky="w")
        ttk.Label(details, text="Artist").grid(row=1, column=0, sticky="w")
        ttk.Label(details, text="Album").grid(row=2, column=0, sticky="w")

        self.title_var = tk.StringVar()
        self.artist_var = tk.StringVar()
        self.album_var = tk.StringVar()

        ttk.Entry(details, textvariable=self.title_var, width=35).grid(row=0, column=1, padx=(8, 0), pady=3)
        ttk.Entry(details, textvariable=self.artist_var, width=35).grid(row=1, column=1, padx=(8, 0), pady=3)
        ttk.Entry(details, textvariable=self.album_var, width=35).grid(row=2, column=1, padx=(8, 0), pady=3)

        ttk.Button(details, text="Save Tags", command=self.save_current_tags).grid(row=3, column=1, sticky="e", pady=(8, 0))

        artwork_box = ttk.LabelFrame(right, text="Artwork", padding=10)
        artwork_box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.cover_label = ttk.Label(artwork_box, text="No artwork", anchor="center")
        self.cover_label.pack(fill=tk.BOTH, expand=True)

        art_btns = ttk.Frame(artwork_box)
        art_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(art_btns, text="Replace Artwork", command=self.replace_artwork).pack(side=tk.LEFT)
        ttk.Button(art_btns, text="Remove Artwork", command=self.remove_artwork).pack(side=tk.LEFT, padx=6)

        ttk.Label(
            right,
            text="Select one or multiple rows to edit. For multiple rows, empty tag fields keep each file's current value.",
            foreground="#555",
            wraplength=320,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(6, 0))

    def import_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio files", "*.mp3 *.aac *.m4a *.mp4"), ("All files", "*.*")],
        )
        if not paths:
            return

        errors = []
        for raw in paths:
            path = Path(raw)
            if path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                item = self.metadata_service.load_item(path)
                self.items.append(item)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        self.refresh_tree()
        if errors:
            messagebox.showwarning("Import warning", "Some files failed to import:\n" + "\n".join(errors[:8]))

    def refresh_tree(self) -> None:
        selected_ids = list(self.current_selection_ids)
        self.tree.delete(*self.tree.get_children())

        for idx, item in enumerate(self.items):
            use_flag = "Yes" if item.selected else "No"
            name = f"{item.path.name}  |  {item.title}"
            duration = self._fmt_time(item.duration)
            self.tree.insert("", tk.END, iid=str(idx), values=(use_flag, name, duration))

        reusable_ids = [iid for iid in selected_ids if iid in self.tree.get_children()]
        if reusable_ids:
            self.tree.selection_set(reusable_ids)
            self.current_selection_ids = reusable_ids
        else:
            self.current_selection_ids = []

    def on_tree_select(self, _event=None) -> None:
        selection = list(self.tree.selection())
        if not selection:
            self.current_index = None
            self.current_selection_ids = []
            self._clear_editor()
            return
        self.current_selection_ids = selection
        indices = [int(iid) for iid in selection]
        self.current_index = indices[0]
        selected_items = [self.items[idx] for idx in indices]

        self.title_var.set(self._common_value(selected_items, "title"))
        self.artist_var.set(self._common_value(selected_items, "artist"))
        self.album_var.set(self._common_value(selected_items, "album"))
        self._render_artwork(self._common_artwork(selected_items))

    def save_current_tags(self) -> None:
        selected_items = self._selected_items()
        if not selected_items:
            messagebox.showinfo("Info", "Select one or more files first.")
            return

        title_input = self.title_var.get().strip()
        artist_input = self.artist_var.get().strip()
        album_input = self.album_var.get().strip()

        errors = []
        is_multi = len(selected_items) > 1
        for item in selected_items:
            if is_multi:
                if title_input:
                    item.tags["title"] = title_input
                if artist_input:
                    item.tags["artist"] = artist_input
                if album_input:
                    item.tags["album"] = album_input
            else:
                item.tags["title"] = title_input
                item.tags["artist"] = artist_input
                item.tags["album"] = album_input

            try:
                self.metadata_service.save_tags(item)
            except Exception as exc:
                errors.append(f"{item.path.name}: {exc}")

        self.refresh_tree()
        if errors:
            messagebox.showwarning("Save warning", "Some files failed to save:\n" + "\n".join(errors[:8]))
        else:
            messagebox.showinfo("Saved", f"Updated tags for {len(selected_items)} file(s)")

    def replace_artwork(self) -> None:
        selected_items = self._selected_items()
        if not selected_items:
            messagebox.showinfo("Info", "Select one or more files first.")
            return

        path = filedialog.askopenfilename(
            title="Select artwork image",
            filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
        )
        if not path:
            return

        raw = Path(path).read_bytes()
        mime = "image/png" if Path(path).suffix.lower() == ".png" else "image/jpeg"
        errors = []
        for item in selected_items:
            item.artwork_bytes = raw
            item.artwork_mime = mime
            try:
                self.metadata_service.save_tags(item)
            except Exception as exc:
                errors.append(f"{item.path.name}: {exc}")

        self._render_artwork(raw)
        if errors:
            messagebox.showwarning("Artwork warning", "Some files failed to save artwork:\n" + "\n".join(errors[:8]))
        else:
            messagebox.showinfo("Saved", f"Updated artwork for {len(selected_items)} file(s)")

    def remove_artwork(self) -> None:
        selected_items = self._selected_items()
        if not selected_items:
            messagebox.showinfo("Info", "Select one or more files first.")
            return

        errors = []
        for item in selected_items:
            item.artwork_bytes = None
            item.artwork_mime = None
            try:
                self.metadata_service.save_tags(item)
            except Exception as exc:
                errors.append(f"{item.path.name}: {exc}")

        self._render_artwork(None)
        if errors:
            messagebox.showwarning("Artwork warning", "Some files failed to remove artwork:\n" + "\n".join(errors[:8]))
        else:
            messagebox.showinfo("Saved", f"Removed artwork for {len(selected_items)} file(s)")

    def remove_selected(self) -> None:
        indices = sorted(self._selected_indices(), reverse=True)
        if not indices:
            return
        for idx in indices:
            self.items.pop(idx)
        self.current_index = None
        self.current_selection_ids = []
        self.refresh_tree()
        self._clear_editor()

    def move_selected(self, direction: int) -> None:
        indices = self._selected_indices()
        if len(indices) != 1:
            messagebox.showinfo("Info", "Select exactly one file to move.")
            return

        idx = indices[0]
        if idx is None:
            return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.items):
            return
        self.items[idx], self.items[new_idx] = self.items[new_idx], self.items[idx]
        self.refresh_tree()
        self.tree.selection_set(str(new_idx))
        self.current_selection_ids = [str(new_idx)]
        self.on_tree_select()

    def toggle_selected(self, event=None) -> None:
        row = self.tree.identify_row(event.y) if event else ""
        if not row:
            selection = self.tree.selection()
            if not selection:
                return
            row = selection[0]
        if not row:
            return
        idx = int(row)
        self.items[idx].selected = not self.items[idx].selected
        self.refresh_tree()
        self.tree.selection_set(row)
        self.current_selection_ids = [row]
        self.on_tree_select()

    def configure_ffmpeg(self) -> None:
        ffmpeg_choice = filedialog.askopenfilename(
            title="Select ffmpeg executable",
            filetypes=[("Executable", "*.exe" if os.name == "nt" else "*"), ("All files", "*.*")],
        )
        if not ffmpeg_choice:
            return

        ffmpeg_path = Path(ffmpeg_choice)
        ffprobe_path = ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if not ffprobe_path.exists():
            messagebox.showerror("Invalid FFmpeg", f"Matching ffprobe not found at:\n{ffprobe_path}")
            return

        try:
            self.export_service.set_binaries(ffmpeg_path, ffprobe_path)
        except Exception as exc:
            messagebox.showerror("FFmpeg setup failed", str(exc))
            return
        messagebox.showinfo("FFmpeg configured", f"Using:\n{ffmpeg_path}")

    def export_m4b(self) -> None:
        if not self.items:
            messagebox.showinfo("Info", "Import files first.")
            return

        output = filedialog.asksaveasfilename(
            title="Export M4B",
            defaultextension=".m4b",
            filetypes=[("M4B audiobook", "*.m4b")],
        )
        if not output:
            return

        try:
            self.export_service.export_m4b(self.items, Path(output))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return

        messagebox.showinfo("Export complete", f"Created:\n{output}")

    def _render_artwork(self, raw: Optional[bytes]) -> None:
        if not raw:
            self.cover_preview_image = None
            self.cover_label.config(text="No artwork", image="")
            return

        if Image is None or ImageTk is None:
            self.cover_label.config(text="Pillow not installed, preview unavailable", image="")
            return

        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            img.thumbnail((260, 260))
            preview = ImageTk.PhotoImage(img)
            self.cover_preview_image = preview
            self.cover_label.config(image=preview, text="")
        except Exception:
            self.cover_preview_image = None
            self.cover_label.config(text="Artwork preview unavailable", image="")

    def _current_item(self) -> Optional[AudioItem]:
        if self.current_index is None:
            return None
        if self.current_index < 0 or self.current_index >= len(self.items):
            return None
        return self.items[self.current_index]

    def _selected_indices(self) -> List[int]:
        return sorted(int(iid) for iid in self.tree.selection())

    def _selected_items(self) -> List[AudioItem]:
        return [self.items[idx] for idx in self._selected_indices()]

    @staticmethod
    def _common_value(items: List[AudioItem], key: str) -> str:
        if not items:
            return ""
        values = {item.tags.get(key, "") for item in items}
        return values.pop() if len(values) == 1 else ""

    @staticmethod
    def _common_artwork(items: List[AudioItem]) -> Optional[bytes]:
        if not items:
            return None
        first = items[0].artwork_bytes
        for item in items[1:]:
            if item.artwork_bytes != first:
                return None
        return first

    def _clear_editor(self) -> None:
        self.title_var.set("")
        self.artist_var.set("")
        self.album_var.set("")
        self._render_artwork(None)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        total = int(max(seconds, 0))
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02}:{m:02}:{s:02}"


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = AudiobookBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

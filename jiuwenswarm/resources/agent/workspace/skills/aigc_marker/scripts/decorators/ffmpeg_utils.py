"""Shared ffmpeg/ffprobe utilities for media AIGC decorators."""
import json
import os
import shutil
import struct
import subprocess

from decorators.common import parse_aigc_json


class FfmpegHelper:
    """Helper for finding ffmpeg/ffprobe and running metadata operations.

    ffmpeg/ffprobe paths are resolved lazily so that importing this module
    (or constructing the helper) does not fail when ffmpeg is not installed.
    Only actual media operations trigger the lookup.
    """

    def __init__(self):
        self._ffmpeg_path = None
        self._ffprobe_path = None

    @property
    def ffmpeg_path(self) -> str:
        if self._ffmpeg_path is None:
            self._ffmpeg_path = self._find_binary("ffmpeg")
        return self._ffmpeg_path

    @property
    def ffprobe_path(self) -> str:
        if self._ffprobe_path is None:
            self._ffprobe_path = self._find_binary("ffprobe")
        return self._ffprobe_path

    def _find_binary(self, name: str) -> str:
        """Find a binary in PATH."""
        path = shutil.which(name)
        if path:
            return path
        raise RuntimeError(
            f"{name} not found. Please install ffmpeg and ensure it's in PATH."
        )

    def has_aigc_mark(self, file_path: str) -> bool:
        """Check if media file already has AIGC metadata."""
        return self.get_aigc_data(file_path) is not None

    def get_aigc_data(self, file_path: str) -> dict | None:
        """Read and parse the AIGC metadata tag from a media file.

        Returns the parsed dict if found and valid, None otherwise.
        Handles WAV specially (top-level RIFF chunk + legacy LIST/INFO).
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".wav":
            return self._wav_get_aigc_data(file_path)
        try:
            cmd = [
                self.ffprobe_path,
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                "-v", "quiet",
                file_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            # Check format tags
            tags = data.get("format", {}).get("tags", {})
            if "AIGC" in tags:
                return parse_aigc_json(tags["AIGC"])
            # Check stream tags
            for stream in data.get("streams", []):
                stags = stream.get("tags", {})
                if "AIGC" in stags:
                    return parse_aigc_json(stags["AIGC"])
            return None
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
            return None

    def _wav_get_aigc_data(self, file_path: str) -> dict | None:
        """Read AIGC metadata from a WAV file.

        Detects both top-level 'AIGC' RIFF chunk and legacy LIST/INFO layout.
        """
        try:
            with open(file_path, "rb") as f:
                header = f.read(12)
                if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                    return None
                while True:
                    ch_header = f.read(8)
                    if len(ch_header) < 8:
                        return None
                    chunk_id = ch_header[:4]
                    chunk_size = struct.unpack("<I", ch_header[4:8])[0]
                    padded = chunk_size + (chunk_size % 2)
                    if chunk_id == b"AIGC":
                        value = f.read(chunk_size).decode("utf-8", errors="ignore").rstrip("\x00")
                        return parse_aigc_json(value)
                    if chunk_id == b"LIST":
                        body = f.read(padded)
                        if len(body) < padded:
                            return None
                        if body[:4] == b"INFO":
                            i = 4
                            while i + 8 <= len(body):
                                sub_key = body[i:i + 4]
                                sub_size = struct.unpack("<I", body[i + 4:i + 8])[0]
                                if sub_key == b"AIGC":
                                    value = body[i + 8:i + 8 + sub_size].decode("utf-8", errors="ignore").rstrip("\x00")
                                    return parse_aigc_json(value)
                                i += 8 + sub_size + (sub_size % 2)
                        continue
                    f.seek(padded, 1)
        except OSError:
            return None

    def run_ffmpeg_metadata(self, file_path: str, signature: str, extra_args: list = None) -> None:
        """Run ffmpeg with -metadata AIGC=signature and optional extra args, using temp file + atomic replace."""
        base, ext = os.path.splitext(file_path)
        tmp_path = base + ".aigc.tmp" + ext
        try:
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i", file_path,
            ]
            if extra_args:
                cmd.extend(extra_args)
            cmd.extend([
                "-metadata", f"AIGC={signature}",
                "-c", "copy",
                tmp_path,
            ])
            subprocess.run(
                cmd, capture_output=True, text=True,
                check=True, encoding="utf-8",
            )
            os.replace(tmp_path, file_path)
        except subprocess.CalledProcessError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(
                f"ffmpeg failed to add AIGC metadata: {e.stderr}"
            ) from e
        except OSError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(
                f"Failed to replace media file with AIGC-marked version: {e}"
            ) from e

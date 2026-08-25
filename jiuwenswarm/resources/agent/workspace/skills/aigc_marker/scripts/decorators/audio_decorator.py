"""Audio AIGC decorator - adds hidden AIGC metadata to audio files."""
import math
import os
import struct
import subprocess
import wave

from decorators.common import get_aigc_signature, is_aigc_complete
from decorators.ffmpeg_utils import FfmpegHelper


class AudioAigcDecorator:
    """Audio AIGC decorator - adds hidden AIGC mark to audio files (wav, mp3, ogg, flac, m4a)."""

    def __init__(self):
        self.name = "audio_aigc_decorator"
        self.ffmpeg = FfmpegHelper()

    def decorate(self, file_path: str, content: str, add_visible_mark: bool = True):
        """Add AIGC mark to audio file.

        If add_visible_mark is True, append morse code tone to the end
        and write AIGC metadata. Otherwise only write metadata.
        """
        ext = os.path.splitext(file_path)[1].lower()
        existing = self.ffmpeg.get_aigc_data(file_path)
        if existing and is_aigc_complete(existing):
            print(f"  [Audio/{ext}] AIGC mark complete, skipping")
            return
        try:
            aigc_signature = get_aigc_signature(content)
            if add_visible_mark:
                self._append_morse_and_mark(file_path, aigc_signature)
            else:
                self._add_aigc_mark(file_path, aigc_signature)
            print(f"  [Audio/{ext}] AIGC mark added successfully")
        except Exception as e:
            print(f"  [Audio/{ext}] Warning: Failed to add explicit mark: {str(e)}")
            # Fallback to implicit only
            try:
                self._add_aigc_mark(file_path, aigc_signature)
                print(f"  [Audio/{ext}] Fallback: implicit mark added")
            except Exception as e2:
                print(f"  [Audio/{ext}] Warning: Failed to add AIGC mark: {str(e2)}")

    def _generate_morse_tone_wav(self, output_path: str) -> None:
        """Generate a WAV file containing the 'AI' morse code tone.

        Rhythm: "short long  short short" (A=·−, I=··)
        Total duration ~1.8s including leading silence.
        """
        sample_rate = 44100
        frequency = 800
        amplitude = 0.2  # ~20% peak to avoid being intrusive

        dot = 0.1
        dash = 0.3
        gap = 0.1
        word_gap = 0.3
        lead = 0.5

        # A (·−), I (· ·) -> sequence of (is_tone, duration_seconds)
        segments = [
            (False, lead),
            (True, dot), (False, gap),
            (True, dash), (False, word_gap),
            (True, dot), (False, gap),
            (True, dot),
        ]

        samples = bytearray()
        for tone, duration in segments:
            n = int(sample_rate * duration)
            for i in range(n):
                if tone:
                    t = i / sample_rate
                    val = int(amplitude * 32767 * math.sin(2 * math.pi * frequency * t))
                else:
                    val = 0
                samples.extend(struct.pack("<h", val))

        # Trailing silence so detectors can finalize the last dot cleanly
        trailing = 0.3
        for _ in range(int(sample_rate * trailing)):
            samples.extend(struct.pack("<h", 0))

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(samples)

    def _append_morse_and_mark(self, file_path: str, signature: str) -> None:
        """Append morse code tone to audio end and write AIGC metadata."""
        base, ext = os.path.splitext(file_path)
        morse_path = base + ".morse.wav"
        tmp_path = base + ".aigc.tmp" + ext

        try:
            self._generate_morse_tone_wav(morse_path)

            # For WAV, ffmpeg concat re-encodes and drops custom RIFF INFO chunks,
            # so we append tone then inject metadata via the dedicated WAV path.
            if ext.lower() == ".wav":
                cmd = [
                    self.ffmpeg.ffmpeg_path,
                    "-y",
                    "-i", file_path,
                    "-i", morse_path,
                    "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
                    "-map", "[out]",
                    tmp_path,
                ]
                subprocess.run(
                    cmd, capture_output=True, text=True,
                    check=True, encoding="utf-8",
                )
                os.replace(tmp_path, file_path)
                self._add_aigc_mark_wav(file_path, signature)
                return

            extra_args = []
            if ext.lower() == ".m4a":
                extra_args = ["-movflags", "use_metadata_tags"]

            cmd = [
                self.ffmpeg.ffmpeg_path,
                "-y",
                "-i", file_path,
                "-i", morse_path,
                "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
                "-map", "[out]",
                "-metadata", f"AIGC={signature}",
            ]
            cmd.extend(extra_args)
            cmd.append(tmp_path)

            subprocess.run(
                cmd, capture_output=True, text=True,
                check=True, encoding="utf-8",
            )
            os.replace(tmp_path, file_path)
        except subprocess.CalledProcessError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(
                f"ffmpeg failed to append morse code: {e.stderr}"
            ) from e
        finally:
            if os.path.exists(morse_path):
                os.remove(morse_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _add_aigc_mark(self, file_path: str, signature: str) -> None:
        """Write AIGC metadata to audio file."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".wav":
            self._add_aigc_mark_wav(file_path, signature)
        elif ext == ".m4a":
            self.ffmpeg.run_ffmpeg_metadata(file_path, signature, extra_args=["-movflags", "use_metadata_tags"])
        else:
            self.ffmpeg.run_ffmpeg_metadata(file_path, signature)

    def _add_aigc_mark_wav(self, file_path: str, signature: str) -> None:
        """Write AIGC metadata to WAV by injecting a top-level 'AIGC' RIFF chunk.

        The AIGC chunk is placed at the top level of the RIFF container
        (sibling of fmt/data), not nested inside LIST/INFO. Legacy files that
        used the old LIST/INFO nesting are detected and stripped so re-decoration
        cleanly migrates to the new layout.
        """
        base, ext = os.path.splitext(file_path)
        tmp_path = base + ".aigc.tmp" + ext

        try:
            with open(file_path, "rb") as f:
                data = bytearray(f.read())

            if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                raise RuntimeError("Not a valid WAV file")

            key_bytes = b"AIGC"
            value_bytes = signature.encode("utf-8")
            if len(value_bytes) % 2 == 1:
                value_bytes += b"\x00"

            # Rebuild the file, stripping:
            #   - any existing top-level 'AIGC' chunk (idempotency)
            #   - any LIST/INFO chunk that contains an AIGC sub-entry (legacy)
            new_data = bytearray(data[:12])  # RIFF + size + 'WAVE'
            data_chunk_end = None
            idx = 12
            while idx < len(data):
                chunk_id = data[idx: idx + 4]
                chunk_size = struct.unpack("<I", data[idx + 4: idx + 8])[0]
                padded_size = chunk_size + (chunk_size % 2)
                chunk_end = idx + 8 + padded_size

                if chunk_id == b"data":
                    data_chunk_end = chunk_end
                    new_data.extend(data[idx:chunk_end])
                elif chunk_id == b"AIGC":
                    # Drop any existing top-level AIGC chunk; new one is appended below
                    pass
                elif chunk_id == b"LIST" and data[idx + 8: idx + 12] == b"INFO":
                    # Legacy migration: if this LIST/INFO contains AIGC, drop the
                    # whole LIST (matches the previous writer that created it
                    # solely to hold AIGC). Other LIST/INFO chunks (e.g. ISFT)
                    # are preserved as-is.
                    info_idx = idx + 12
                    info_end = chunk_end
                    has_aigc = False
                    while info_idx < info_end:
                        info_key = data[info_idx: info_idx + 4]
                        info_val_size = struct.unpack(
                            "<I", data[info_idx + 4: info_idx + 8]
                        )[0]
                        if info_key == key_bytes:
                            has_aigc = True
                            break
                        info_idx += 8 + info_val_size + (info_val_size % 2)
                    if not has_aigc:
                        new_data.extend(data[idx:chunk_end])
                else:
                    new_data.extend(data[idx:chunk_end])
                idx = chunk_end

            if data_chunk_end is None:
                raise RuntimeError("WAV file does not contain a 'data' chunk")

            # Top-level AIGC chunk: 'AIGC' + size + value
            aigc_chunk = key_bytes + struct.pack("<I", len(value_bytes)) + value_bytes

            # Insert immediately after data chunk so the AIGC chunk is the last sibling.
            # data_chunk_end was indexed into the original buffer; after stripping it
            # may no longer match new_data, so locate the data chunk in new_data.
            new_data.extend(aigc_chunk)
            new_data[4:8] = struct.pack("<I", len(new_data) - 8)

            with open(tmp_path, "wb") as f:
                f.write(new_data)
            os.replace(tmp_path, file_path)
        except OSError as e:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(
                f"Failed to write AIGC-marked WAV file: {e}"
            ) from e

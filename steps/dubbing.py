"""Step 11: Dubbing — translate and dub video into multiple languages.

Pipeline:
1. FFmpeg — extract audio from video
2. faster-whisper — transcribe Russian audio with timestamps
3. Demucs (VPS via SSH) — separate vocals from background
4. Claude — translate each segment to target language
5. ElevenLabs TTS — generate speech per segment with voice clone
6. FFmpeg — combine background + TTS segments at timestamps
7. FFmpeg — merge dubbed audio with original video
"""

import json
import logging
import subprocess
import time
import urllib.request
from pathlib import Path

from steps.base import BaseStep

logger = logging.getLogger(__name__)

# All languages use ElevenLabs multilingual_v2 with Ivan's voice clone
LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "de": "German",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
}

ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
# Cloudflare Worker proxy (bypasses Russian IP block)
ELEVENLABS_PROXY_URL = "https://elevenlabs-proxy.iv-subbotin1.workers.dev/v1"
ELEVENLABS_PROXY_SECRET = "yt-pipe-el-proxy-2026-secret"


class DubbingStep(BaseStep):
    step_name = "dubbing"

    def execute(self) -> dict:
        from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

        content_plan = self.get_previous_step_data("content_plan")
        description_data = self.get_previous_step_data("description")

        dubbing_config = self._load_dubbing_config()
        selected_langs = dubbing_config.get("languages", list(LANGUAGES.keys()))

        dubbing_dir = self.state.project_dir / "dubbing"
        dubbing_dir.mkdir(parents=True, exist_ok=True)

        # ── Find source audio ──
        # Montageur uploads audio track (mp3/wav) — much lighter than video
        source_audio = self._find_source_audio(dubbing_dir)
        logger.info(f"Dubbing source audio: {source_audio}")

        # ── Shared steps (run once, reuse for all languages) ──

        # 1. Convert to WAV 16kHz mono for Whisper (if needed)
        audio_path = dubbing_dir / "audio.wav"
        self._prepare_audio(source_audio, audio_path)

        # 2. Transcribe Russian
        transcript_path = dubbing_dir / "transcript_ru.json"
        segments = self._transcribe(audio_path, transcript_path)

        # 3. Separate vocals from background (demucs)
        background_path = dubbing_dir / "background.wav"
        self._separate_audio(Path(source_audio), background_path)

        # Get audio duration for alignment
        video_duration = self._get_audio_duration(source_audio)

        # ── Per-language steps ──
        results = []
        for lang_code in selected_langs:
            if lang_code not in LANGUAGES:
                logger.warning(f"Unknown language: {lang_code}, skipping")
                continue

            lang_name = LANGUAGES[lang_code]
            lang_dir = dubbing_dir / lang_code
            lang_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"=== Dubbing to {lang_name} ({lang_code}) ===")

            self._save_progress(dubbing_dir, lang_code, "started", {})

            try:
                # 4. Translate segments
                self._save_progress(dubbing_dir, lang_code, "translating", {})
                translated = self._translate_segments(segments, lang_code, lang_dir)

                # 5. Generate TTS per segment
                self._save_progress(dubbing_dir, lang_code, "generating_tts", {"total_segments": len(translated)})
                tts_files = self._generate_tts(
                    translated, lang_code, lang_dir,
                    api_key=ELEVENLABS_API_KEY,
                    voice_id=ELEVENLABS_VOICE_ID,
                )

                # 6. Combine background + TTS → final audio track
                self._save_progress(dubbing_dir, lang_code, "combining_audio", {})
                combined_path = lang_dir / "combined.wav"
                self._combine_audio(tts_files, translated, background_path, combined_path, video_duration)

                self._save_progress(dubbing_dir, lang_code, "translating_metadata", {})
                metadata = self._translate_metadata(
                    lang_code=lang_code,
                    title=description_data.get("title", content_plan.get("title", self.state.topic)),
                    description=description_data.get("description", ""),
                    tags=description_data.get("tags", []),
                )
                with open(lang_dir / "metadata.json", "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)

                self._save_progress(dubbing_dir, lang_code, "completed", {})
                results.append({
                    "lang_code": lang_code,
                    "lang_name": lang_name,
                    "status": "completed",
                    "output_file": str(combined_path),
                    "translated_title": metadata.get("title", ""),
                })
                logger.info(f"=== {lang_name} ({lang_code}) DONE ===")

            except Exception as e:
                logger.error(f"=== {lang_name} ({lang_code}) FAILED: {e} ===")
                self._save_progress(dubbing_dir, lang_code, "failed", {"error": str(e)})
                results.append({
                    "lang_code": lang_code,
                    "lang_name": lang_name,
                    "status": "failed",
                    "error": str(e),
                })

        completed = [r for r in results if r["status"] == "completed"]
        failed = [r for r in results if r["status"] == "failed"]

        return {
            "languages": results,
            "total": len(results),
            "completed": len(completed),
            "failed": len(failed),
            "audio_source": source_audio,
        }

    # ── Step 1: Prepare Audio ──

    def _prepare_audio(self, source_audio: str, output_path: Path):
        """Convert source audio to mono 16kHz WAV for Whisper transcription."""
        if output_path.exists():
            logger.info(f"Audio already prepared: {output_path}")
            return
        logger.info("Converting audio to 16kHz mono WAV for transcription...")
        subprocess.run(
            ["ffmpeg", "-y", "-i", source_audio, "-vn",
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             str(output_path)],
            check=True, capture_output=True, timeout=120,
        )
        logger.info(f"Audio prepared: {output_path}")

    # ── Step 2: Transcribe ──

    def _transcribe(self, audio_path: Path, output_path: Path) -> list[dict]:
        """Transcribe Russian audio using faster-whisper."""
        if output_path.exists():
            logger.info(f"Transcript already exists: {output_path}")
            with open(output_path, "r", encoding="utf-8") as f:
                return json.load(f)

        logger.info("Transcribing Russian audio with faster-whisper...")
        from faster_whisper import WhisperModel

        model = WhisperModel("medium", device="cpu", compute_type="int8")
        raw_segments, info = model.transcribe(str(audio_path), language="ru")

        segments = []
        for seg in raw_segments:
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            })

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, ensure_ascii=False, indent=2)

        logger.info(f"Transcribed: {len(segments)} segments, {info.duration:.0f}s")
        return segments

    # ── Step 3: Separate Audio (Demucs) ──

    def _separate_audio(self, audio_path: Path, output_path: Path):
        """Separate vocals from background using Demucs.

        Tries local demucs first (if running on VPS where it's installed),
        falls back to SSH if local fails, then to simple volume reduction.
        """
        if output_path.exists():
            logger.info(f"Background audio already separated: {output_path}")
            return

        logger.info("Separating vocals from background via Demucs...")

        # Demucs script that works both locally and remotely
        demucs_script = """
import torch, numpy as np, os, soundfile as sf
from demucs.pretrained import get_model
from demucs.apply import apply_model
import sys

input_path = sys.argv[1]
output_path = sys.argv[2]

model = get_model("htdemucs")
model.eval()
wav_np, sr = sf.read(input_path)
if wav_np.ndim == 1:
    wav_np = np.stack([wav_np, wav_np])
else:
    wav_np = wav_np.T
wav = torch.from_numpy(wav_np).float()
if wav.shape[0] == 1:
    wav = wav.repeat(2, 1)
ref = wav.mean(0)
wav_norm = (wav - ref.mean()) / ref.std()
with torch.no_grad():
    sources = apply_model(model, wav_norm[None], device="cpu")[0]
vocals_idx = model.sources.index("vocals")
no_vocals = sources.sum(0) - sources[vocals_idx]
no_vocals = no_vocals * ref.std() + ref.mean()
sf.write(output_path, no_vocals.numpy().T, sr)
print("DEMUCS_OK")
"""
        # Write script to temp file
        script_path = output_path.parent / "_demucs_script.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(demucs_script)

        # Try 1: Run demucs locally (works if on VPS)
        result = subprocess.run(
            ["python3", str(script_path), str(audio_path), str(output_path)],
            capture_output=True, timeout=600, text=True,
        )

        if "DEMUCS_OK" in result.stdout:
            logger.info(f"Background separated (local demucs): {output_path}")
            script_path.unlink(missing_ok=True)
            return

        logger.warning(f"Local demucs failed: {result.stderr[:200]}")

        # Try 2: Run via SSH (if running from a different machine)
        try:
            from config import VPS_SSH_HOST, VPS_SSH_USER
            remote_input = "/tmp/dubbing_input.wav"
            remote_output = "/tmp/dubbing_background.wav"

            subprocess.run(
                ["scp", str(audio_path), f"{VPS_SSH_USER}@{VPS_SSH_HOST}:{remote_input}"],
                check=True, capture_output=True, timeout=120,
            )
            ssh_result = subprocess.run(
                ["ssh", f"{VPS_SSH_USER}@{VPS_SSH_HOST}",
                 f"python3 {script_path} {remote_input} {remote_output}"],
                capture_output=True, timeout=600, text=True,
            )
            if "DEMUCS_OK" in ssh_result.stdout:
                subprocess.run(
                    ["scp", f"{VPS_SSH_USER}@{VPS_SSH_HOST}:{remote_output}", str(output_path)],
                    check=True, capture_output=True, timeout=120,
                )
                logger.info(f"Background separated (SSH demucs): {output_path}")
                script_path.unlink(missing_ok=True)
                return
        except Exception as e:
            logger.warning(f"SSH demucs failed: {e}")

        # Fallback: use original audio with reduced volume (no vocal removal)
        logger.warning("Demucs unavailable. Using original audio with reduced volume as background.")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-filter:a", "volume=0.15",
             str(output_path)],
            check=True, capture_output=True, timeout=30,
        )
        script_path.unlink(missing_ok=True)
        logger.info(f"Background (fallback, reduced volume): {output_path}")

    # ── Step 4: Translate Segments ──

    def _translate_segments(self, segments: list[dict], lang_code: str, output_dir: Path) -> list[dict]:
        """Translate timestamped segments to target language via Claude."""
        transcript_path = output_dir / "transcript.json"
        if transcript_path.exists():
            logger.info(f"Translation already exists: {transcript_path}")
            with open(transcript_path, "r", encoding="utf-8") as f:
                return json.load(f)

        lang_name = LANGUAGES[lang_code]
        logger.info(f"Translating {len(segments)} segments to {lang_name}...")

        segments_text = "\n".join(
            f'{i+1}. [{s["start"]:.1f}-{s["end"]:.1f}] {s["text"]}'
            for i, s in enumerate(segments)
        )

        system = f"You are a professional translator specializing in {lang_name}. Translate naturally for YouTube audience."
        prompt = f"""Translate these Russian speech segments to {lang_name}.
Keep translations concise — they will be spoken aloud and must roughly fit the original timing.
Return ONLY a valid JSON array with the same structure.

Segments:
{segments_text}

Return JSON: [{{"start": 0.0, "end": 4.8, "text": "{lang_name} text"}}]"""

        response = self.ask_claude(system=system, prompt=prompt)

        try:
            start = response.index("[")
            end = response.rindex("]") + 1
            translated = json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Failed to parse translation: {e}")

        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(translated, f, ensure_ascii=False, indent=2)

        logger.info(f"Translated: {len(translated)} segments")
        return translated

    # ── Step 5: Generate TTS ──

    def _generate_tts(
        self,
        segments: list[dict],
        lang_code: str,
        output_dir: Path,
        api_key: str,
        voice_id: str,
    ) -> list[Path]:
        """Generate TTS audio for each segment using ElevenLabs voice clone."""
        logger.info(f"Generating TTS for {len(segments)} segments...")
        tts_files = []

        for i, seg in enumerate(segments):
            out_file = output_dir / f"tts_{i}.mp3"

            # Resume support: skip if already generated
            if out_file.exists() and out_file.stat().st_size > 0:
                tts_files.append(out_file)
                continue

            text = seg["text"]
            logger.info(f"  TTS [{i+1}/{len(segments)}]: {text[:50]}...")

            tts_data = json.dumps({
                "text": text,
                "model_id": ELEVENLABS_MODEL,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.0,
                    "use_speaker_boost": True,
                },
            }).encode("utf-8")

            # Try proxy first (bypasses Russian IP block), then direct
            tts_urls = [
                (ELEVENLABS_PROXY_URL, {"xi-api-key": api_key, "x-proxy-secret": ELEVENLABS_PROXY_SECRET, "Content-Type": "application/json", "Accept": "audio/mpeg", "User-Agent": "YTPipeline/1.0"}),
                (ELEVENLABS_BASE_URL, {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg", "User-Agent": "YTPipeline/1.0"}),
            ]

            success = False
            for attempt in range(3):
                if success:
                    break
                for base_url, hdrs in tts_urls:
                    try:
                        req = urllib.request.Request(
                            f"{base_url}/text-to-speech/{voice_id}",
                            data=tts_data,
                            headers=hdrs,
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=60) as resp:
                            with open(out_file, "wb") as f:
                                while True:
                                    chunk = resp.read(8192)
                                    if not chunk:
                                        break
                                    f.write(chunk)
                        success = True
                        break  # got it, skip other URLs
                    except urllib.error.HTTPError as e:
                        if e.code == 429 and attempt < 2:
                            wait = (attempt + 1) * 10
                            logger.warning(f"  Rate limited, waiting {wait}s...")
                            time.sleep(wait)
                            break  # retry same URL set
                        # Try next URL
                        continue
                    except urllib.error.URLError:
                        continue  # try next URL
            if not success:
                raise RuntimeError(f"TTS failed for segment {i} after all retries")

            tts_files.append(out_file)

        logger.info(f"TTS generated: {len(tts_files)} files")
        return tts_files

    # ── Step 6: Combine Audio ──

    def _combine_audio(
        self,
        tts_files: list[Path],
        segments: list[dict],
        background_path: Path,
        output_path: Path,
        video_duration: float,
    ):
        """Combine background audio + TTS segments placed at correct timestamps."""
        if output_path.exists():
            logger.info(f"Combined audio already exists: {output_path}")
            return

        logger.info("Combining background + TTS segments...")

        # Build FFmpeg filter: place each TTS segment at its timestamp
        inputs = ["-i", str(background_path)]
        filter_parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[bg]"]

        for i, (tts_file, seg) in enumerate(zip(tts_files, segments)):
            inputs.extend(["-i", str(tts_file)])
            delay_ms = int(seg["start"] * 1000)
            idx = i + 1  # input index (0 is background)
            filter_parts.append(
                f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms}[tts{i}]"
            )

        # Mix: background (80%) + all TTS segments (130%)
        tts_inputs = "".join(f"[tts{i}]" for i in range(len(tts_files)))
        filter_parts.append(
            f"{tts_inputs}amix=inputs={len(tts_files)}:normalize=0,volume=1.3[voice]"
        )
        filter_parts.append(
            "[bg]volume=0.8[bg_vol]"
        )
        filter_parts.append(
            "[bg_vol][voice]amix=inputs=2:duration=longest:normalize=0[out]"
        )

        filter_str = ";".join(filter_parts)

        cmd = (
            ["ffmpeg", "-y"] + inputs +
            ["-filter_complex", filter_str, "-map", "[out]",
             "-t", str(video_duration), str(output_path)]
        )

        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg combine failed: {result.stderr.decode('utf-8', errors='replace')[:500]}")

        logger.info(f"Combined audio: {output_path}")

    # ── Translate Metadata ──

    def _translate_metadata(
        self,
        lang_code: str,
        title: str,
        description: str,
        tags: list[str],
    ) -> dict:
        """Translate video title, description and tags via Claude."""
        lang_name = LANGUAGES.get(lang_code, lang_code)

        prompt = f"""Translate the following YouTube video metadata from Russian to {lang_name}.

RULES:
- Keep the tone engaging and YouTube-friendly
- Adapt cultural references for the target audience
- DO NOT translate channel names, brand names, or URLs
- Keep emojis as-is

TITLE:
{title}

DESCRIPTION:
{description}

TAGS:
{json.dumps(tags, ensure_ascii=False)}

Respond ONLY with valid JSON:
{{
  "title": "translated title",
  "description": "translated description",
  "tags": ["tag1", "tag2", ...]
}}"""

        system = f"You are a professional YouTube translator specializing in {lang_name}. Translate naturally, not literally."
        response = self.ask_claude(system=system, prompt=prompt)

        try:
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse metadata translation: {e}")
            return {"title": title, "description": description, "tags": tags}

    # ── Utilities ──

    def _find_source_audio(self, dubbing_dir: Path) -> str:
        """Locate the source audio file uploaded by montageur."""
        # Check dubbing directory first (uploaded via UI)
        # Case-insensitive glob for Linux compatibility
        for pattern in ("source_audio.*", "SOURCE_AUDIO.*", "Source_Audio.*"):
            candidates = list(dubbing_dir.glob(pattern))
            audio_candidates = [c for c in candidates if c.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")]
            if audio_candidates:
                return str(audio_candidates[0])

        # Any audio file in dubbing dir
        for f in dubbing_dir.iterdir() if dubbing_dir.exists() else []:
            if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                return str(f)

        # Check project directory
        for f in sorted(self.state.project_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                return str(f)

        # Fallback: try video file and extract audio
        for f in sorted(self.state.project_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                return str(f)

        raise FileNotFoundError(
            f"Audio file not found in {dubbing_dir} or {self.state.project_dir}. "
            "Upload an audio track (MP3/WAV) via the dashboard before running dubbing."
        )

    def _load_dubbing_config(self) -> dict:
        """Load dubbing configuration (selected languages)."""
        config_file = self.state.project_dir / "dubbing_config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)

        channel_id = self.state.channel_id
        if channel_id:
            from config import CHANNELS_DIR
            channel_config = CHANNELS_DIR / channel_id / "dubbing_config.json"
            if channel_config.exists():
                with open(channel_config, "r", encoding="utf-8") as f:
                    return json.load(f)

        return {"languages": list(LANGUAGES.keys())}

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds via ffprobe."""
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())

    def _save_progress(self, dubbing_dir: Path, lang_code: str, stage: str, data: dict):
        """Save dubbing progress for UI polling."""
        state_file = dubbing_dir / "dubbing_state.json"
        state = {}
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        state[lang_code] = {"stage": stage, "updated_at": time.time(), **data}
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


# ── Standalone functions for API use ──

def get_available_languages() -> dict:
    """Return available dubbing languages."""
    from config import ELEVENLABS_API_KEY
    has_key = bool(ELEVENLABS_API_KEY)
    return {
        code: {"name": name, "provider": "ElevenLabs TTS", "available": has_key}
        for code, name in LANGUAGES.items()
    }

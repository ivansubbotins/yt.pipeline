"""Step 11: Dubbing — translate and dub video into multiple languages.

Hybrid approach:
- ElevenLabs API (voice clone) for EN + ES — premium quality
- KrillinAI / Edge TTS for PT, DE, KO, JA — free, good quality
"""

import json
import logging
import time
import urllib.request
import urllib.parse
from pathlib import Path

from steps.base import BaseStep

logger = logging.getLogger(__name__)

# Language config: code -> (name_ru, tts_provider, edge_voice)
LANGUAGES = {
    "en": ("Английский", "elevenlabs", "en-US-GuyNeural"),
    "es": ("Испанский", "elevenlabs", "es-ES-AlvaroNeural"),
    "pt": ("Португальский", "edge", "pt-BR-AntonioNeural"),
    "de": ("Немецкий", "edge", "de-DE-ConradNeural"),
    "ko": ("Корейский", "edge", "ko-KR-InJoonNeural"),
    "ja": ("Японский", "edge", "ja-JP-KeitaNeural"),
}


class DubbingStep(BaseStep):
    step_name = "dubbing"

    def execute(self) -> dict:
        from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, KRILLIN_BASE_URL

        content_plan = self.get_previous_step_data("content_plan")
        description_data = self.get_previous_step_data("description")

        # Get video file
        video_file = self._find_video_file()
        logger.info(f"Dubbing source video: {video_file}")

        # Get selected languages from dubbing config (or all)
        dubbing_config = self._load_dubbing_config()
        selected_langs = dubbing_config.get("languages", list(LANGUAGES.keys()))

        results = []
        for lang_code in selected_langs:
            if lang_code not in LANGUAGES:
                logger.warning(f"Unknown language: {lang_code}, skipping")
                continue

            lang_name, tts_provider, edge_voice = LANGUAGES[lang_code]
            logger.info(f"Dubbing to {lang_name} ({lang_code}) via {tts_provider}")

            try:
                result = self._dub_single_language(
                    video_file=video_file,
                    lang_code=lang_code,
                    lang_name=lang_name,
                    tts_provider=tts_provider,
                    edge_voice=edge_voice,
                    krillin_url=KRILLIN_BASE_URL,
                    elevenlabs_key=ELEVENLABS_API_KEY,
                    elevenlabs_voice=ELEVENLABS_VOICE_ID,
                )

                # Translate title + description
                translated = self._translate_metadata(
                    lang_code=lang_code,
                    title=description_data.get("title", content_plan.get("title", self.state.topic)),
                    description=description_data.get("description", ""),
                    tags=description_data.get("tags", []),
                )
                result["translated_title"] = translated["title"]
                result["translated_description"] = translated["description"]
                result["translated_tags"] = translated["tags"]

                # Save translated metadata to files
                lang_dir = self.state.project_dir / "dubbing" / lang_code
                lang_dir.mkdir(parents=True, exist_ok=True)
                meta_file = lang_dir / "metadata.json"
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(translated, f, ensure_ascii=False, indent=2)

                result["status"] = "completed"
                results.append(result)
                logger.info(f"✓ {lang_name} ({lang_code}) — done")

            except Exception as e:
                logger.error(f"✗ {lang_name} ({lang_code}) — failed: {e}")
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
            "video_source": video_file,
        }

    def _dub_single_language(
        self,
        video_file: str,
        lang_code: str,
        lang_name: str,
        tts_provider: str,
        edge_voice: str,
        krillin_url: str,
        elevenlabs_key: str,
        elevenlabs_voice: str,
    ) -> dict:
        """Dub video into a single language using the appropriate provider."""
        lang_dir = self.state.project_dir / "dubbing" / lang_code
        lang_dir.mkdir(parents=True, exist_ok=True)

        if tts_provider == "elevenlabs" and elevenlabs_key:
            return self._dub_via_elevenlabs(
                video_file=video_file,
                lang_code=lang_code,
                lang_name=lang_name,
                output_dir=lang_dir,
                api_key=elevenlabs_key,
                voice_id=elevenlabs_voice,
            )
        else:
            # Use KrillinAI with Edge TTS (free)
            return self._dub_via_krillin(
                video_file=video_file,
                lang_code=lang_code,
                lang_name=lang_name,
                edge_voice=edge_voice,
                output_dir=lang_dir,
                krillin_url=krillin_url,
            )

    # ── ElevenLabs Dubbing API ──

    def _dub_via_elevenlabs(
        self,
        video_file: str,
        lang_code: str,
        lang_name: str,
        output_dir: Path,
        api_key: str,
        voice_id: str,
    ) -> dict:
        """Dub using ElevenLabs Dubbing API (high quality voice clone)."""
        import io
        import mimetypes

        base_url = "https://api.elevenlabs.io/v1"

        # ElevenLabs language codes
        el_lang_map = {"en": "en", "es": "es", "pt": "pt", "de": "de", "ko": "ko", "ja": "ja"}
        target_lang = el_lang_map.get(lang_code, lang_code)

        # Step 1: Create dubbing project
        logger.info(f"  ElevenLabs: creating dubbing project for {lang_code}...")
        boundary = f"----PipelineBoundary{int(time.time())}"
        body = self._build_multipart_body(
            boundary=boundary,
            fields={
                "source_lang": "ru",
                "target_lang": target_lang,
                "num_speakers": "1",
                "watermark": "false",
            },
            file_field="file",
            file_path=video_file,
        )

        req = urllib.request.Request(
            f"{base_url}/dubbing",
            data=body,
            headers={
                "xi-api-key": api_key,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        dubbing_id = result["dubbing_id"]
        expected_duration = result.get("expected_duration_sec", 0)
        logger.info(f"  ElevenLabs: dubbing_id={dubbing_id}, expected ~{expected_duration}s")

        # Step 2: Poll for completion
        dubbed_file = self._poll_elevenlabs_dubbing(
            base_url=base_url,
            api_key=api_key,
            dubbing_id=dubbing_id,
            target_lang=target_lang,
            output_dir=output_dir,
            timeout=1800,  # 30 min max
        )

        return {
            "lang_code": lang_code,
            "lang_name": lang_name,
            "provider": "elevenlabs",
            "dubbing_id": dubbing_id,
            "output_file": str(dubbed_file),
        }

    def _poll_elevenlabs_dubbing(
        self,
        base_url: str,
        api_key: str,
        dubbing_id: str,
        target_lang: str,
        output_dir: Path,
        timeout: int = 1800,
    ) -> Path:
        """Poll ElevenLabs dubbing status until complete, then download."""
        start = time.time()
        poll_interval = 15  # seconds

        while time.time() - start < timeout:
            req = urllib.request.Request(
                f"{base_url}/dubbing/{dubbing_id}",
                headers={"xi-api-key": api_key},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                status_data = json.loads(resp.read().decode("utf-8"))

            status = status_data.get("status", "unknown")
            logger.info(f"  ElevenLabs: status={status}")

            if status == "dubbed":
                # Download the dubbed audio/video
                output_file = output_dir / f"dubbed_{target_lang}.mp4"
                dl_req = urllib.request.Request(
                    f"{base_url}/dubbing/{dubbing_id}/audio/{target_lang}",
                    headers={"xi-api-key": api_key},
                )
                with urllib.request.urlopen(dl_req, timeout=300) as dl_resp:
                    with open(output_file, "wb") as f:
                        while True:
                            chunk = dl_resp.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                logger.info(f"  ElevenLabs: downloaded {output_file}")
                return output_file

            elif status in ("failed", "error"):
                error_msg = status_data.get("error", "Unknown error")
                raise RuntimeError(f"ElevenLabs dubbing failed: {error_msg}")

            time.sleep(poll_interval)

        raise TimeoutError(f"ElevenLabs dubbing timed out after {timeout}s")

    # ── KrillinAI (Edge TTS) Dubbing ──

    def _dub_via_krillin(
        self,
        video_file: str,
        lang_code: str,
        lang_name: str,
        edge_voice: str,
        output_dir: Path,
        krillin_url: str,
    ) -> dict:
        """Dub using KrillinAI self-hosted server (Edge TTS — free).

        KrillinAI API:
          POST /api/file              — upload video file -> {"data": {"file_path": ["local:./uploads/..."]}}
          POST /api/capability/subtitleTask — start task (JSON body) -> {"data": {"task_id": "..."}}
          GET  /api/capability/subtitleTask?taskId=... — poll status -> {"data": {"process_percent": N, "speech_download_url": "...", "subtitle_info": [...]}}
          GET  /api/file/<path>        — download result file
        """
        # Step 1: Upload video file
        logger.info(f"  KrillinAI: uploading video for {lang_code}...")
        boundary = f"----KrillinBoundary{int(time.time())}"
        body = self._build_multipart_body(
            boundary=boundary,
            fields={},
            file_field="file",
            file_path=video_file,
        )

        req = urllib.request.Request(
            f"{krillin_url}/api/file",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            upload_result = json.loads(resp.read().decode("utf-8"))

        if upload_result.get("error", 0) != 0:
            raise RuntimeError(f"KrillinAI upload failed: {upload_result.get('msg', 'unknown')}")

        file_paths = upload_result.get("data", {}).get("file_path", [])
        uploaded_path = file_paths[0] if file_paths else ""
        logger.info(f"  KrillinAI: uploaded as {uploaded_path}")

        # Step 2: Create subtitle/dubbing task
        logger.info(f"  KrillinAI: creating task for {lang_code}...")
        task_body = json.dumps({
            "url": uploaded_path,
            "origin_lang": "ru",
            "target_lang": lang_code,
            "tts": 1,  # 1 = enable TTS dubbing, 2 = subtitles only
            "tts_voice_code": edge_voice,
            "bilingual": 2,  # 1 = bilingual subs, 2 = target language only
            "translation_subtitle_pos": 2,  # 2 = bottom
            "modal_filter": 2,  # 2 = keep filler words
            "embed_subtitle_video_type": "none",  # none/horizontal/vertical
            "origin_language_word_one_line": 12,
            "replace": [],
            "language": "en",  # UI language
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{krillin_url}/api/capability/subtitleTask",
            data=task_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            task_result = json.loads(resp.read().decode("utf-8"))

        if task_result.get("error", 0) != 0:
            raise RuntimeError(f"KrillinAI task creation failed: {task_result.get('msg', 'unknown')}")

        task_id = task_result.get("data", {}).get("task_id", "")
        logger.info(f"  KrillinAI: task_id={task_id}")

        # Step 3: Poll for completion
        task_data = self._poll_krillin_task(
            krillin_url=krillin_url,
            task_id=task_id,
            timeout=3600,  # 1 hour max
        )

        # Step 4: Download dubbed audio (tts_final_audio.wav)
        speech_url = task_data.get("speech_download_url", "")
        audio_file = output_dir / f"dubbed_{lang_code}.wav"
        dubbed_file = output_dir / f"dubbed_{lang_code}.mp4"
        if speech_url:
            dl_url = speech_url if speech_url.startswith("http") else f"{krillin_url}{speech_url}"
            req = urllib.request.Request(dl_url)
            with urllib.request.urlopen(req, timeout=300) as dl_resp:
                with open(audio_file, "wb") as f:
                    while True:
                        chunk = dl_resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
            logger.info(f"  KrillinAI: downloaded dubbed audio to {audio_file}")

            # Merge dubbed audio with original video using FFmpeg
            import subprocess
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_file, "-i", str(audio_file),
                     "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                     "-shortest", str(dubbed_file)],
                    check=True, capture_output=True, timeout=600,
                )
                logger.info(f"  KrillinAI: merged video+audio to {dubbed_file}")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning(f"  FFmpeg merge failed: {e}. Audio-only file available at {audio_file}")
                # Fallback: keep just the audio file
                dubbed_file = audio_file

        # Step 5: Download subtitles
        srt_file = None
        subtitle_info = task_data.get("subtitle_info", [])
        for sub in subtitle_info:
            sub_url = sub.get("download_url", "")
            if sub_url:
                srt_file = output_dir / f"subtitles_{lang_code}.srt"
                dl_url = sub_url if sub_url.startswith("http") else f"{krillin_url}{sub_url}"
                req = urllib.request.Request(dl_url)
                with urllib.request.urlopen(req, timeout=30) as dl_resp:
                    with open(srt_file, "wb") as f:
                        f.write(dl_resp.read())
                logger.info(f"  KrillinAI: subtitles saved to {srt_file}")
                break  # take first subtitle file

        # Video info (translated title/description from KrillinAI)
        video_info = task_data.get("video_info", {})

        return {
            "lang_code": lang_code,
            "lang_name": lang_name,
            "provider": "krillin",
            "task_id": task_id,
            "output_file": str(dubbed_file),
            "srt_file": str(srt_file) if srt_file else None,
            "krillin_translated_title": video_info.get("translated_title", ""),
            "krillin_translated_description": video_info.get("translated_description", ""),
        }

    def _poll_krillin_task(
        self,
        krillin_url: str,
        task_id: str,
        timeout: int = 3600,
    ) -> dict:
        """Poll KrillinAI task status until complete. Returns task data dict."""
        start = time.time()
        poll_interval = 10

        while time.time() - start < timeout:
            req = urllib.request.Request(
                f"{krillin_url}/api/capability/subtitleTask?taskId={task_id}",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if result.get("error", 0) != 0:
                raise RuntimeError(f"KrillinAI status check failed: {result.get('msg', 'unknown')}")

            data = result.get("data", {})
            progress = data.get("process_percent", 0)
            logger.info(f"  KrillinAI: progress={progress}%")

            if progress >= 100:
                return data

            time.sleep(poll_interval)

        raise TimeoutError(f"KrillinAI task timed out after {timeout}s")

    # ── Translate Metadata ──

    def _translate_metadata(
        self,
        lang_code: str,
        title: str,
        description: str,
        tags: list[str],
    ) -> dict:
        """Translate video title, description and tags via Claude."""
        lang_name = LANGUAGES.get(lang_code, (lang_code,))[0]

        prompt = f"""Translate the following YouTube video metadata from Russian to {lang_name} ({lang_code}).

RULES:
- Keep the tone engaging and YouTube-friendly
- Adapt cultural references for the target audience
- Keep hashtags in the target language
- Tags should be relevant keywords in the target language
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

        system = f"You are a professional YouTube video translator specializing in {lang_name}. Translate naturally, not literally."
        response = self.ask_claude(system=system, prompt=prompt)

        # Parse JSON from response
        try:
            # Find JSON in response
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse translation JSON: {e}")
            return {"title": title, "description": description, "tags": tags}

    # ── Utilities ──

    def _find_video_file(self) -> str:
        """Locate the source video file (same logic as publish step)."""
        # Check editing step
        video_file = self.state.get_step("editing").get("data", {}).get("video_file")
        if video_file and Path(video_file).exists():
            return video_file

        # Check publish step (might have the uploaded video path)
        publish_data = self.state.get_step("publish").get("data", {})
        video_file = publish_data.get("video_file")
        if video_file and Path(video_file).exists():
            return video_file

        # Search project directory
        for ext in ("mp4", "mov", "mkv", "avi", "webm"):
            candidates = list(self.state.project_dir.glob(f"*.{ext}"))
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return str(candidates[0])

        raise FileNotFoundError(
            f"Video file not found in {self.state.project_dir}. "
            "Place the video file in the project directory before running dubbing."
        )

    def _load_dubbing_config(self) -> dict:
        """Load dubbing configuration (selected languages, voices, etc.)."""
        config_file = self.state.project_dir / "dubbing_config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                return json.load(f)

        # Check channel-level config
        channel_id = self.state.channel_id
        if channel_id:
            from config import CHANNELS_DIR
            channel_config = CHANNELS_DIR / channel_id / "dubbing_config.json"
            if channel_config.exists():
                with open(channel_config, "r", encoding="utf-8") as f:
                    return json.load(f)

        # Default: all languages
        return {"languages": list(LANGUAGES.keys()), "auto_publish": False}

    def _build_multipart_body(
        self,
        boundary: str,
        fields: dict[str, str],
        file_field: str,
        file_path: str,
    ) -> bytes:
        """Build multipart/form-data body for file upload."""
        import mimetypes

        lines = []
        for key, value in fields.items():
            lines.append(f"--{boundary}".encode())
            lines.append(f'Content-Disposition: form-data; name="{key}"'.encode())
            lines.append(b"")
            lines.append(value.encode())

        # File part
        filename = Path(file_path).name
        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode()
        )
        lines.append(f"Content-Type: {content_type}".encode())
        lines.append(b"")

        with open(file_path, "rb") as f:
            file_data = f.read()

        lines.append(file_data)
        lines.append(f"--{boundary}--".encode())

        # Join with CRLF
        body = b"\r\n".join(lines)
        return body


# ── Standalone dubbing functions for API use ──

def dub_single_video(
    video_path: str,
    lang_code: str,
    output_dir: str,
    provider: str = "auto",
) -> dict:
    """Dub a single video file without pipeline context.

    Used by API endpoints for on-demand dubbing.
    """
    from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, KRILLIN_BASE_URL

    if lang_code not in LANGUAGES:
        raise ValueError(f"Unsupported language: {lang_code}. Supported: {list(LANGUAGES.keys())}")

    lang_name, default_provider, edge_voice = LANGUAGES[lang_code]

    if provider == "auto":
        provider = default_provider

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Use a dummy step instance for the dubbing methods
    # (This is a convenience wrapper — real pipeline uses DubbingStep)
    step = DubbingStep.__new__(DubbingStep)

    if provider == "elevenlabs" and ELEVENLABS_API_KEY:
        return step._dub_via_elevenlabs(
            video_file=video_path,
            lang_code=lang_code,
            lang_name=lang_name,
            output_dir=output_path,
            api_key=ELEVENLABS_API_KEY,
            voice_id=ELEVENLABS_VOICE_ID,
        )
    else:
        return step._dub_via_krillin(
            video_file=video_path,
            lang_code=lang_code,
            lang_name=lang_name,
            edge_voice=edge_voice,
            output_dir=output_path,
            krillin_url=KRILLIN_BASE_URL,
        )


def get_available_languages() -> dict:
    """Return available dubbing languages with their config."""
    from config import ELEVENLABS_API_KEY
    result = {}
    for code, (name, provider, voice) in LANGUAGES.items():
        result[code] = {
            "name": name,
            "provider": provider,
            "edge_voice": voice,
            "available": True if provider == "edge" else bool(ELEVENLABS_API_KEY),
        }
    return result

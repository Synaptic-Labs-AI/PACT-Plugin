"""
Location: pact-plugin/telegram/voice.py
Summary: Voice note download and transcription via OpenAI Whisper API.
Used by: tools.py (telegram_ask handler) when a voice note reply is received.

Handles the full pipeline:
1. Download voice note file from Telegram (getFile API -> file download)
2. Send OGG audio directly to OpenAI Whisper API (no ffmpeg conversion needed)
3. Return transcribed text
4. Clean up temporary files in a finally block (security requirement)

Design decisions:
- Telegram voice notes are OGG/Opus format, which Whisper accepts directly
- Uses httpx for async HTTP (consistent with telegram_client.py)
- Temp files are always cleaned up, even on error (security control)
- File size is capped at 5MB to prevent API cost abuse
- OpenAI API key is optional; graceful degradation if not configured

Security controls:
- Temp files deleted in finally block (plan security checklist item)
- File size limit enforced before download completes (plan risk mitigation)
- No credentials in logs or error messages
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger("pact-telegram.voice")

# Maximum voice file size (5MB) to prevent API cost abuse
MAX_VOICE_FILE_SIZE = 5 * 1024 * 1024

# Telegram file download base URL
TELEGRAM_FILE_URL = "https://api.telegram.org/file/bot{token}/{file_path}"

# OpenAI Whisper API endpoint
WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"

# Supported audio formats for Whisper API
SUPPORTED_FORMATS = {"ogg", "oga", "mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"}


class VoiceTranscriptionError(Exception):
    """Raised when voice transcription fails at any stage."""


class VoiceTranscriber:
    """
    Downloads Telegram voice notes and transcribes them via OpenAI Whisper.

    Requires:
    - A Telegram bot token (for downloading voice files)
    - An OpenAI API key (for Whisper transcription)

    Both are read from the config at construction time. If the OpenAI API key
    is not configured, is_available() returns False and callers should skip
    voice transcription gracefully.
    """

    def __init__(
        self,
        bot_token: str,
        openai_api_key: str | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Initialize the voice transcriber.

        Args:
            bot_token: Telegram bot token for file downloads.
            openai_api_key: OpenAI API key for Whisper. None disables transcription.
            http_client: Optional shared httpx client. If None, creates one internally.
        """
        self._bot_token = bot_token
        self._openai_api_key = openai_api_key
        self._client = http_client
        self._owns_client = http_client is None

    def is_available(self) -> bool:
        """Check if voice transcription is configured and available."""
        return bool(self._openai_api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True
        return self._client

    async def get_file_path(self, file_id: str) -> str:
        """
        Get the file path from Telegram using getFile API.

        Args:
            file_id: Telegram file_id from the voice message.

        Returns:
            The file_path string for downloading the file.

        Raises:
            VoiceTranscriptionError: If the API call fails or returns unexpected data.
        """
        client = await self._get_client()
        url = f"https://api.telegram.org/bot{self._bot_token}/getFile"

        try:
            response = await client.post(url, json={"file_id": file_id})
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise VoiceTranscriptionError(
                f"Telegram getFile failed with status {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise VoiceTranscriptionError(
                f"Telegram getFile request failed: {type(e).__name__}"
            ) from e

        data = response.json()
        if not data.get("ok"):
            description = data.get("description", "Unknown error")
            raise VoiceTranscriptionError(
                f"Telegram getFile returned error: {description}"
            )

        result = data.get("result", {})
        file_path = result.get("file_path")
        if not file_path:
            raise VoiceTranscriptionError("Telegram getFile returned no file_path")

        # Check file size before downloading
        file_size = result.get("file_size", 0)
        if file_size > MAX_VOICE_FILE_SIZE:
            raise VoiceTranscriptionError(
                f"Voice file too large ({file_size} bytes, max {MAX_VOICE_FILE_SIZE}). "
                f"Skipping transcription."
            )

        return file_path

    async def download_file(self, file_path: str) -> bytes:
        """
        Download a file from Telegram servers.

        Args:
            file_path: The file_path from getFile API response.

        Returns:
            Raw file bytes.

        Raises:
            VoiceTranscriptionError: If download fails or file exceeds size limit.
        """
        # Validate file_path to prevent path traversal or injection in URL
        if not re.match(r'^[a-zA-Z0-9/_.\-]+$', file_path) or '..' in file_path:
            raise VoiceTranscriptionError(
                "Invalid file_path from Telegram (unexpected characters)"
            )

        client = await self._get_client()
        url = TELEGRAM_FILE_URL.format(token=self._bot_token, file_path=file_path)

        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise VoiceTranscriptionError(
                f"File download failed with status {e.response.status_code}"
            ) from e
        except httpx.RequestError as e:
            raise VoiceTranscriptionError(
                f"File download request failed: {type(e).__name__}"
            ) from e

        content = response.content
        if len(content) > MAX_VOICE_FILE_SIZE:
            raise VoiceTranscriptionError(
                f"Downloaded file too large ({len(content)} bytes, "
                f"max {MAX_VOICE_FILE_SIZE}). Discarding."
            )

        return content

    async def transcribe(self, audio_data: bytes, filename: str = "voice.ogg") -> str:
        """
        Transcribe audio data using OpenAI Whisper API.

        Writes audio to a temporary file, sends it to Whisper, and cleans up
        the temp file in a finally block regardless of success or failure.

        Args:
            audio_data: Raw audio bytes (OGG/Opus from Telegram).
            filename: Filename hint for Whisper (helps with format detection).

        Returns:
            Transcribed text string.

        Raises:
            VoiceTranscriptionError: If transcription fails or API key is missing.
        """
        if not self._openai_api_key:
            raise VoiceTranscriptionError(
                "OpenAI API key not configured. Voice transcription unavailable."
            )

        tmp_path: Path | None = None
        try:
            # Write to temp file (Whisper API requires file upload)
            suffix = Path(filename).suffix or ".ogg"
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False, prefix="pact-tg-voice-"
            ) as tmp:
                tmp.write(audio_data)
                tmp_path = Path(tmp.name)

            # Read file bytes outside of async context to avoid blocking
            audio_bytes = await asyncio.to_thread(tmp_path.read_bytes)

            # Send to Whisper API
            client = await self._get_client()
            response = await client.post(
                WHISPER_API_URL,
                headers={"Authorization": f"Bearer {self._openai_api_key}"},
                files={"file": (filename, audio_bytes, "audio/ogg")},
                data={"model": "whisper-1"},
                timeout=60.0,
            )

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Parse error message without exposing API key
                error_body = e.response.text[:200]
                raise VoiceTranscriptionError(
                    f"Whisper API returned status {e.response.status_code}: {error_body}"
                ) from e

            result = response.json()
            text = result.get("text", "").strip()

            if not text:
                raise VoiceTranscriptionError("Whisper returned empty transcription")

            logger.info(
                "Voice note transcribed",
                extra={"text_length": len(text)},
            )
            return text

        except VoiceTranscriptionError:
            raise
        except httpx.RequestError as e:
            raise VoiceTranscriptionError(
                f"Whisper API request failed: {type(e).__name__}"
            ) from e
        except Exception as e:
            raise VoiceTranscriptionError(
                f"Unexpected transcription error: {type(e).__name__}: {e}"
            ) from e
        finally:
            # Security control: always clean up temp files
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Failed to delete temp voice file",
                        extra={"path": str(tmp_path)},
                    )

    async def transcribe_voice_message(self, file_id: str) -> str:
        """
        Full pipeline: download voice note from Telegram and transcribe it.

        This is the primary entry point for voice transcription. Handles the
        complete flow from Telegram file_id to transcribed text.

        Args:
            file_id: Telegram file_id from the voice message update.

        Returns:
            Transcribed text string.

        Raises:
            VoiceTranscriptionError: If any step in the pipeline fails.
        """
        if not self.is_available():
            raise VoiceTranscriptionError(
                "Voice transcription not available (OpenAI API key not configured)"
            )

        logger.info("Starting voice transcription", extra={"file_id": file_id[:8]})

        # Step 1: Get file path from Telegram
        file_path = await self.get_file_path(file_id)

        # Step 2: Download the file
        audio_data = await self.download_file(file_path)
        logger.debug(
            "Voice file downloaded",
            extra={"size_bytes": len(audio_data)},
        )

        # Step 3: Determine filename from file_path for format hint
        filename = Path(file_path).name if file_path else "voice.ogg"

        # Step 4: Transcribe
        text = await self.transcribe(audio_data, filename=filename)

        logger.info(
            "Voice transcription complete",
            extra={"text_length": len(text), "file_id": file_id[:8]},
        )
        return text

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

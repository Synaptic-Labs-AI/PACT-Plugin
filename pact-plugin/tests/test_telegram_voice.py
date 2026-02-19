"""
Tests for pact-plugin/telegram/voice.py

Tests cover:
1. VoiceTranscriber construction and is_available check
2. get_file_path: API call, error handling, file size limit
3. download_file: file download, size enforcement
4. transcribe: Whisper API call, temp file cleanup (security), error handling
5. transcribe_voice_message: full pipeline integration
6. Security: temp file always deleted, no credentials in errors, size limits
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram.voice import (
    VoiceTranscriber,
    VoiceTranscriptionError,
    MAX_VOICE_FILE_SIZE,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def transcriber(mock_http_client):
    """Create a VoiceTranscriber with mock HTTP client."""
    return VoiceTranscriber(
        bot_token="123:TEST",
        openai_api_key="sk-test-key",
        http_client=mock_http_client,
    )


@pytest.fixture
def transcriber_no_key(mock_http_client):
    """Create a VoiceTranscriber without OpenAI key."""
    return VoiceTranscriber(
        bot_token="123:TEST",
        openai_api_key=None,
        http_client=mock_http_client,
    )


# =============================================================================
# Construction and is_available Tests
# =============================================================================

class TestVoiceTranscriberInit:
    """Tests for VoiceTranscriber construction."""

    def test_is_available_with_api_key(self, transcriber):
        """Should be available when OpenAI API key is configured."""
        assert transcriber.is_available() is True

    def test_not_available_without_api_key(self, transcriber_no_key):
        """Should not be available when OpenAI API key is None."""
        assert transcriber_no_key.is_available() is False

    def test_not_available_with_empty_key(self, mock_http_client):
        """Should not be available when OpenAI API key is empty string."""
        t = VoiceTranscriber(bot_token="x", openai_api_key="", http_client=mock_http_client)
        assert t.is_available() is False

    @pytest.mark.asyncio
    async def test_close_closes_owned_client(self, mock_http_client):
        """Should close HTTP client when we own it."""
        t = VoiceTranscriber(bot_token="x", openai_api_key="sk-x", http_client=mock_http_client)
        t._owns_client = True  # Override to pretend we own it
        await t.close()
        mock_http_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_skips_external_client(self, mock_http_client):
        """Should not close externally provided client."""
        t = VoiceTranscriber(
            bot_token="x", openai_api_key="sk-x", http_client=mock_http_client
        )
        await t.close()
        mock_http_client.aclose.assert_not_awaited()


# =============================================================================
# get_file_path Tests
# =============================================================================

class TestGetFilePath:
    """Tests for get_file_path -- Telegram getFile API call."""

    @pytest.mark.asyncio
    async def test_returns_file_path_on_success(self, transcriber, mock_http_client):
        """Should return file_path from successful getFile response."""
        response = MagicMock()
        response.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/file_0.ogg", "file_size": 1000},
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        path = await transcriber.get_file_path("file_abc")

        assert path == "voice/file_0.ogg"

    @pytest.mark.asyncio
    async def test_raises_on_file_too_large(self, transcriber, mock_http_client):
        """Should raise when file exceeds MAX_VOICE_FILE_SIZE."""
        response = MagicMock()
        response.json.return_value = {
            "ok": True,
            "result": {
                "file_path": "voice/big.ogg",
                "file_size": MAX_VOICE_FILE_SIZE + 1,
            },
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="too large"):
            await transcriber.get_file_path("file_big")

    @pytest.mark.asyncio
    async def test_raises_on_api_error(self, transcriber, mock_http_client):
        """Should raise on Telegram API error."""
        response = MagicMock()
        response.json.return_value = {
            "ok": False,
            "description": "Invalid file_id",
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="Invalid file_id"):
            await transcriber.get_file_path("bad_id")

    @pytest.mark.asyncio
    async def test_raises_on_missing_file_path(self, transcriber, mock_http_client):
        """Should raise when API returns no file_path."""
        response = MagicMock()
        response.json.return_value = {
            "ok": True,
            "result": {"file_size": 100},  # no file_path
        }
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="no file_path"):
            await transcriber.get_file_path("file_123")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, transcriber, mock_http_client):
        """Should raise on HTTP error from Telegram."""
        response = MagicMock()
        response.status_code = 500
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=response
        )
        mock_http_client.post.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="status 500"):
            await transcriber.get_file_path("file_123")

    @pytest.mark.asyncio
    async def test_raises_on_network_error(self, transcriber, mock_http_client):
        """Should raise on network-level errors."""
        mock_http_client.post.side_effect = httpx.ConnectError("Network down")

        with pytest.raises(VoiceTranscriptionError, match="ConnectError"):
            await transcriber.get_file_path("file_123")


# =============================================================================
# download_file Tests
# =============================================================================

class TestDownloadFile:
    """Tests for download_file -- downloading voice files from Telegram."""

    @pytest.mark.asyncio
    async def test_downloads_file_successfully(self, transcriber, mock_http_client):
        """Should return file bytes on success."""
        response = MagicMock()
        response.content = b"audio data"
        response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = response

        data = await transcriber.download_file("voice/file.ogg")

        assert data == b"audio data"

    @pytest.mark.asyncio
    async def test_raises_on_oversized_download(self, transcriber, mock_http_client):
        """Should raise when downloaded content exceeds size limit."""
        response = MagicMock()
        response.content = b"x" * (MAX_VOICE_FILE_SIZE + 1)
        response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="too large"):
            await transcriber.download_file("voice/big.ogg")

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, transcriber, mock_http_client):
        """Should raise on HTTP error during download."""
        response = MagicMock()
        response.status_code = 404
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=response
        )
        mock_http_client.get.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="status 404"):
            await transcriber.download_file("voice/missing.ogg")

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_in_file_path(self, transcriber, mock_http_client):
        """Security: should reject file paths with path traversal characters."""
        with pytest.raises(VoiceTranscriptionError, match="unexpected characters"):
            await transcriber.download_file("../../../etc/passwd")

    @pytest.mark.asyncio
    async def test_rejects_special_chars_in_file_path(self, transcriber, mock_http_client):
        """Security: should reject file paths with shell injection chars."""
        with pytest.raises(VoiceTranscriptionError, match="unexpected characters"):
            await transcriber.download_file("voice/file.ogg; rm -rf /")

    @pytest.mark.asyncio
    async def test_accepts_valid_file_path(self, transcriber, mock_http_client):
        """Should accept valid Telegram file paths."""
        response = MagicMock()
        response.content = b"audio data"
        response.raise_for_status = MagicMock()
        mock_http_client.get.return_value = response

        # Valid paths should not raise the validation error
        data = await transcriber.download_file("voice/file_0.oga")
        assert data == b"audio data"


# =============================================================================
# transcribe Tests
# =============================================================================

class TestTranscribe:
    """Tests for transcribe -- OpenAI Whisper API transcription."""

    @pytest.mark.asyncio
    async def test_transcribes_audio_successfully(self, transcriber, mock_http_client):
        """Should return transcribed text from Whisper API."""
        response = MagicMock()
        response.json.return_value = {"text": "Hello world"}
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        result = await transcriber.transcribe(b"audio data", "voice.ogg")

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_raises_on_no_api_key(self, transcriber_no_key):
        """Should raise when OpenAI API key is not configured."""
        with pytest.raises(VoiceTranscriptionError, match="not configured"):
            await transcriber_no_key.transcribe(b"audio", "voice.ogg")

    @pytest.mark.asyncio
    async def test_raises_on_empty_transcription(self, transcriber, mock_http_client):
        """Should raise when Whisper returns empty text."""
        response = MagicMock()
        response.json.return_value = {"text": ""}
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        with pytest.raises(VoiceTranscriptionError, match="empty transcription"):
            await transcriber.transcribe(b"audio", "voice.ogg")

    @pytest.mark.asyncio
    async def test_cleans_up_temp_file_on_success(self, transcriber, mock_http_client):
        """Security: temp file should be deleted after successful transcription."""
        response = MagicMock()
        response.json.return_value = {"text": "Hello"}
        response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = response

        # Track temp files created
        temp_files = []
        original_named_temp = tempfile.NamedTemporaryFile

        def tracking_temp(**kwargs):
            f = original_named_temp(**kwargs)
            temp_files.append(Path(f.name))
            return f

        with patch("telegram.voice.tempfile.NamedTemporaryFile", side_effect=tracking_temp):
            await transcriber.transcribe(b"audio", "voice.ogg")

        for tf in temp_files:
            assert not tf.exists(), f"Temp file not cleaned up: {tf}"

    @pytest.mark.asyncio
    async def test_cleans_up_temp_file_on_error(self, transcriber, mock_http_client):
        """Security: temp file should be deleted even on transcription error."""
        response = MagicMock()
        response.status_code = 500
        response.text = "Internal Server Error"
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Error", request=MagicMock(), response=response
        )
        mock_http_client.post.return_value = response

        temp_files = []
        original_named_temp = tempfile.NamedTemporaryFile

        def tracking_temp(**kwargs):
            f = original_named_temp(**kwargs)
            temp_files.append(Path(f.name))
            return f

        with patch("telegram.voice.tempfile.NamedTemporaryFile", side_effect=tracking_temp):
            with pytest.raises(VoiceTranscriptionError):
                await transcriber.transcribe(b"audio", "voice.ogg")

        for tf in temp_files:
            assert not tf.exists(), f"Temp file not cleaned up on error: {tf}"


# =============================================================================
# transcribe_voice_message Tests
# =============================================================================

class TestTranscribeVoiceMessage:
    """Tests for transcribe_voice_message -- full pipeline integration."""

    @pytest.mark.asyncio
    async def test_raises_when_not_available(self, transcriber_no_key):
        """Should raise when voice transcription is not available."""
        with pytest.raises(VoiceTranscriptionError, match="not available"):
            await transcriber_no_key.transcribe_voice_message("file_123")

    @pytest.mark.asyncio
    async def test_full_pipeline(self, transcriber, mock_http_client):
        """Should execute getFile -> download -> transcribe pipeline."""
        # Mock getFile response
        get_file_response = MagicMock()
        get_file_response.json.return_value = {
            "ok": True,
            "result": {"file_path": "voice/test.ogg", "file_size": 1000},
        }
        get_file_response.raise_for_status = MagicMock()

        # Mock download response
        download_response = MagicMock()
        download_response.content = b"fake audio data"
        download_response.raise_for_status = MagicMock()

        # Mock Whisper response
        whisper_response = MagicMock()
        whisper_response.json.return_value = {"text": "Transcribed text"}
        whisper_response.raise_for_status = MagicMock()

        # First post = getFile, second post = Whisper
        mock_http_client.post.side_effect = [get_file_response, whisper_response]
        mock_http_client.get.return_value = download_response

        result = await transcriber.transcribe_voice_message("file_abc")

        assert result == "Transcribed text"
        assert mock_http_client.post.call_count == 2  # getFile + Whisper
        assert mock_http_client.get.call_count == 1   # download

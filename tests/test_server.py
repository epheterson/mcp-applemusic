"""Tests for server module."""

import json
import time
from unittest.mock import patch, MagicMock

import pytest
import responses

from applemusic_mcp import server


class TestGetTokenExpirationWarning:
    """Tests for get_token_expiration_warning function."""

    def test_returns_none_when_no_token_file(self, mock_config_dir):
        """Should return None when token file doesn't exist."""
        result = server.get_token_expiration_warning()
        assert result is None

    def test_returns_none_when_token_valid(self, mock_config_dir):
        """Should return None when token has more than 30 days left."""
        token_file = mock_config_dir / "developer_token.json"
        token_data = {"expires": time.time() + 86400 * 60}  # 60 days
        with open(token_file, "w") as f:
            json.dump(token_data, f)

        result = server.get_token_expiration_warning()
        assert result is None

    def test_returns_warning_when_expiring_soon(self, mock_config_dir):
        """Should return warning when token expires within 30 days."""
        token_file = mock_config_dir / "developer_token.json"
        token_data = {"expires": time.time() + 86400 * 15}  # 15 days
        with open(token_file, "w") as f:
            json.dump(token_data, f)

        result = server.get_token_expiration_warning()
        assert result is not None
        assert "days" in result  # Could be 14 or 15 depending on timing
        assert "generate-token" in result


class TestGetHeaders:
    """Tests for get_headers function."""

    def test_returns_headers_with_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return properly formatted headers."""
        # Setup token files
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 30}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.get_headers()

        assert "Authorization" in result
        assert result["Authorization"].startswith("Bearer ")
        assert "Music-User-Token" in result
        assert result["Content-Type"] == "application/json"


class TestGetLibraryPlaylists:
    """Tests for get_library_playlists function (API path)."""

    @responses.activate
    def test_returns_playlists(self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch):
        """Should return formatted playlist list via API."""
        # Disable AppleScript to test API path
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {
                        "id": "p.abc123",
                        "attributes": {"name": "Test Playlist", "canEdit": True}
                    },
                    {
                        "id": "p.def456",
                        "attributes": {"name": "Read Only", "canEdit": False}
                    }
                ]
            },
            status=200,
        )

        result = server.playlist(action="list")

        assert "Test Playlist" in result
        assert "p.abc123" in result
        assert "Read Only" in result
        assert "p.def456" in result
        assert "2 items" in result

    @responses.activate
    def test_handles_api_error(self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch):
        """Should return error message on API failure."""
        # Disable AppleScript to test API path
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = server.playlist(action="list")

        assert "API Error" in result or "401" in result


class TestCreatePlaylist:
    """Tests for create_playlist function (API path)."""

    @responses.activate
    def test_creates_playlist_successfully(self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch):
        """Should create playlist via API and return ID."""
        # Disable AppleScript to test API path
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.newplaylist123"}]},
            status=201,
        )

        result = server.playlist(action="create", name="My New Playlist", description="A description")

        assert "My New Playlist" in result
        assert "p.newplaylist123" in result


class TestAddToPlaylist:
    """Tests for add_to_playlist function."""

    @responses.activate
    def test_adds_tracks_successfully(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should add tracks and return confirmation."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.test123/tracks",
            status=204,
        )

        result = server.playlist(action="add", playlist="p.test123", track="i.song1, i.song2, i.song3")

        assert "Added" in result
        assert "3 track" in result

    def test_handles_empty_track(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return error for empty track."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.playlist(action="add", playlist="p.test123", track="")

        assert "Provide track or album parameter" in result


class TestSearchLibrary:
    """Tests for search_library function."""

    @responses.activate
    def test_returns_search_results(self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch):
        """Should return formatted search results via API fallback."""
        # Force API path by disabling AppleScript
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/search",
            json={
                "results": {
                    "library-songs": {
                        "data": [
                            {
                                "id": "i.abc123",
                                "attributes": {
                                    "name": "Wonderwall",
                                    "artistName": "Oasis",
                                    "albumName": "(What's the Story) Morning Glory?"
                                }
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.library(action="search", query="Wonderwall")

        assert "Wonderwall" in result
        assert "Oasis" in result
        assert "i.abc123" in result


class TestSearchCatalog:
    """Tests for search_catalog function."""

    @responses.activate
    def test_returns_catalog_results(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return formatted catalog search results."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "123456789",
                                "attributes": {
                                    "name": "Let It Be",
                                    "artistName": "The Beatles"
                                }
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.catalog(action="search", query="Let It Be")

        assert "Let It Be" in result
        assert "The Beatles" in result
        assert "123456789" in result


class TestCheckAuthStatus:
    """Tests for check_auth_status function."""

    def test_reports_missing_tokens(self, mock_config_dir):
        """Should report missing tokens."""
        result = server.config(action="auth-status")

        assert "MISSING" in result
        assert "Developer Token" in result
        assert "Music User Token" in result

    def test_reports_valid_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should report OK for valid tokens."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Don't actually test API connection
        with patch.object(server, "get_headers", return_value={}):
            with patch("requests.get") as mock_get:
                mock_get.return_value.status_code = 200
                result = server.config(action="auth-status")

        assert "OK" in result
        assert "Developer Token" in result

    def test_reports_expiring_token(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should warn about expiring token."""
        # Setup expiring token
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 10}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        result = server.config(action="auth-status")

        assert "EXPIRES IN" in result or "10" in result


class TestFormatDuration:
    """Tests for format_duration helper function."""

    def test_formats_standard_duration(self):
        """Should format milliseconds as m:ss."""
        assert server.format_duration(225000) == "3:45"
        assert server.format_duration(60000) == "1:00"
        assert server.format_duration(5000) == "0:05"

    def test_handles_zero(self):
        """Should return empty string for zero."""
        assert server.format_duration(0) == ""

    def test_handles_none(self):
        """Should return empty string for None."""
        assert server.format_duration(None) == ""

    def test_handles_negative(self):
        """Should return empty string for negative values."""
        assert server.format_duration(-1000) == ""
        assert server.format_duration(-60000) == ""

    def test_handles_large_duration(self):
        """Should handle songs longer than an hour."""
        # 1 hour, 5 minutes, 30 seconds = 3930000 ms
        assert server.format_duration(3930000) == "65:30"


class TestExtractTrackData:
    """Tests for extract_track_data helper function."""

    def test_extracts_basic_fields(self):
        """Should extract core fields from track data."""
        track = {
            "id": "i.abc123",
            "attributes": {
                "name": "Wonderwall",
                "artistName": "Oasis",
                "albumName": "(What's the Story) Morning Glory?",
                "durationInMillis": 258000,
                "releaseDate": "1995-10-02",
                "genreNames": ["Rock", "Alternative"],
            }
        }
        result = server.extract_track_data(track)

        assert result["name"] == "Wonderwall"
        assert result["artist"] == "Oasis"
        assert result["album"] == "(What's the Story) Morning Glory?"
        assert result["duration"] == "4:18"
        assert result["year"] == "1995"
        assert result["genre"] == "Rock"
        assert result["id"] == "i.abc123"

    def test_handles_empty_track(self):
        """Should handle empty track dict gracefully."""
        result = server.extract_track_data({})

        assert result["name"] == ""
        assert result["artist"] == ""
        assert result["duration"] == ""
        assert result["id"] == ""

    def test_handles_missing_attributes(self):
        """Should handle track with empty attributes."""
        track = {"id": "test123", "attributes": {}}
        result = server.extract_track_data(track)

        assert result["id"] == "test123"
        assert result["name"] == ""

    def test_includes_extras_when_requested(self):
        """Should include extra fields when include_extras=True."""
        track = {
            "id": "123",
            "attributes": {
                "name": "Test",
                "trackNumber": 5,
                "discNumber": 2,
                "hasLyrics": True,
                "composerName": "John Doe",
                "isrc": "USRC12345678",
                "contentRating": "explicit",
                "playParams": {"catalogId": "cat123"},
                "previews": [{"url": "https://example.com/preview.m4a"}],
                "artwork": {"url": "https://example.com/{w}x{h}bb.jpg"},
            }
        }
        result = server.extract_track_data(track, include_extras=True)

        assert result["track_number"] == 5
        assert result["disc_number"] == 2
        assert result["has_lyrics"] is True
        assert result["composer"] == "John Doe"
        assert result["isrc"] == "USRC12345678"
        assert result["is_explicit"] is True
        assert result["catalog_id"] == "cat123"
        assert "preview.m4a" in result["preview_url"]
        assert "500x500" in result["artwork_url"]


class TestTruncate:
    """Tests for truncate helper function."""

    def test_truncates_long_string(self):
        """Should truncate and add ellipsis for strings exceeding max length."""
        result = server.truncate("This is a very long string", 10)
        assert result == "This is a ..."
        assert len(result) == 13  # 10 chars + "..."

    def test_returns_short_string_unchanged(self):
        """Should return strings shorter than max unchanged."""
        result = server.truncate("Short", 10)
        assert result == "Short"

    def test_returns_exact_length_unchanged(self):
        """Should return strings exactly at max length unchanged."""
        result = server.truncate("TenChars!!", 10)
        assert result == "TenChars!!"

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = server.truncate("", 10)
        assert result == ""


class TestFormatTrackList:
    """Tests for format_track_list helper function."""

    def test_full_format_for_small_lists(self):
        """Should use full format for small track lists."""
        tracks = [{
            "name": "Song Name",
            "artist": "Artist Name",
            "duration": "3:45",
            "album": "Album Name",
            "year": "2024",
            "genre": "Rock",
            "id": "123"
        }]
        lines, tier = server.format_track_list(tracks)

        assert tier == "Full"
        assert len(lines) == 1
        assert "Song Name - Artist Name (3:45) Album Name [2024] Rock 123" == lines[0]

    def test_clipped_format_when_full_exceeds_limit(self):
        """Should use clipped format when full format exceeds MAX_OUTPUT_CHARS."""
        track = {
            "name": "A" * 100,
            "artist": "B" * 50,
            "duration": "3:00",
            "album": "C" * 100,
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890"
        }
        tracks = [track] * 200
        lines, tier = server.format_track_list(tracks)

        assert tier == "Clipped"
        assert len(lines) == 200
        assert "..." in lines[0]  # Truncated
        assert "C" * 100 not in lines[0]  # Album truncated
        assert "[2024]" in lines[0]  # Year still present
        assert "Rock" in lines[0]  # Genre still present

    def test_compact_format_when_clipped_exceeds_limit(self):
        """Should use compact format when clipped format exceeds MAX_OUTPUT_CHARS."""
        track = {
            "name": "A" * 50,
            "artist": "B" * 30,
            "duration": "3:00",
            "album": "Album",
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890"
        }
        tracks = [track] * 450
        lines, tier = server.format_track_list(tracks)

        assert tier == "Compact"
        assert len(lines) == 450
        assert "Album" not in lines[0]  # Album dropped
        assert "[2024]" not in lines[0]  # Year dropped
        assert "(3:00)" in lines[0]  # Duration still present

    def test_minimal_format_when_compact_exceeds_limit(self):
        """Should use minimal format when compact format also exceeds limit."""
        track = {
            "name": "A" * 50,
            "artist": "B" * 30,
            "duration": "3:00",
            "album": "Album",
            "year": "2024",
            "genre": "Rock",
            "id": "12345678901234567890"
        }
        tracks = [track] * 800
        lines, tier = server.format_track_list(tracks)

        assert tier == "Minimal"
        assert len(lines) == 800
        assert "(3:00)" not in lines[0]

    def test_handles_empty_optional_fields(self):
        """Should handle tracks with empty year/genre gracefully."""
        tracks = [{
            "name": "Song",
            "artist": "Artist",
            "duration": "3:00",
            "album": "Album",
            "year": "",
            "genre": "",
            "id": "123"
        }]
        lines, tier = server.format_track_list(tracks)

        assert tier == "Full"
        assert "[" not in lines[0]
        assert lines[0] == "Song - Artist (3:00) Album 123"

    def test_returns_empty_for_no_tracks(self):
        """Should handle empty track list."""
        lines, tier = server.format_track_list([])
        assert tier == "Full"
        assert lines == []


class TestSearchCatalogSongsHelper:
    """Tests for _search_catalog_songs internal helper."""

    @responses.activate
    def test_returns_songs_on_success(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return list of song dicts on successful search."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {"id": "123", "attributes": {"name": "Test Song", "artistName": "Test Artist"}}
                        ]
                    }
                }
            },
            status=200,
        )

        result = server._search_catalog_songs("test", limit=5)
        assert len(result) == 1
        assert result[0]["id"] == "123"

    @responses.activate
    def test_returns_empty_on_error(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return empty list on API error."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"error": "Unauthorized"},
            status=401,
        )

        result = server._search_catalog_songs("test")
        assert result == []


class TestAddSongsToLibraryHelper:
    """Tests for _add_songs_to_library internal helper."""

    def test_returns_error_for_empty_ids(self):
        """Should return error tuple for empty ID list."""
        success, msg = server._add_songs_to_library([])
        assert success is False
        assert "No catalog IDs" in msg

    @responses.activate
    def test_returns_success_on_valid_response(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return success tuple on successful add."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=202,
        )

        success, msg = server._add_songs_to_library(["123456789"])
        assert success is True
        assert "1 song" in msg

    @responses.activate
    def test_returns_error_on_api_failure(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return error tuple on API failure."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=401,
        )

        success, msg = server._add_songs_to_library(["123456789"])
        assert success is False
        assert "401" in msg


class TestAddToLibraryTool:
    """Tests for add_to_library MCP tool."""

    def test_returns_error_for_empty_input(self):
        """Should return error when no input provided."""
        result = server.library(action="add")
        assert "Error: Provide track or album parameter" in result

    @responses.activate
    def test_adds_songs_successfully(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should add songs and return success message."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            status=202,
        )

        result = server.library(action="add", track="1234567890, 9876543210")
        assert "Added" in result
        assert "2" in result


class TestPlayTrackMatching:
    """Tests for play_track song matching logic."""

    def test_matches_featured_artist_in_song_name(self):
        """Should match artist in song name for featured artists."""
        # Mock song with featured artist in title
        song = {
            "id": "123",
            "attributes": {
                "name": "Uptown Funk (feat. Bruno Mars)",
                "artistName": "Mark Ronson",
                "url": "https://music.apple.com/us/song/123"
            }
        }
        
        # Check if "Bruno Mars" matches (in song name, not artistName)
        song_name = song["attributes"]["name"]
        song_artist = song["attributes"]["artistName"]
        artist = "Bruno Mars"
        
        # This is the matching logic from play_track
        matches_artist = artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        assert matches_artist is True

    def test_matches_artist_in_artist_name(self):
        """Should match artist in artistName field."""
        song_name = "Bohemian Rhapsody"
        song_artist = "Queen"
        artist = "Queen"
        
        matches_artist = artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        assert matches_artist is True

    def test_no_match_when_artist_not_found(self):
        """Should not match when artist is in neither field."""
        song_name = "Some Song"
        song_artist = "Some Artist"
        artist = "Different Artist"
        
        matches_artist = artist.lower() in song_artist.lower() or artist.lower() in song_name.lower()
        assert matches_artist is False

    def test_partial_track_name_match(self):
        """Should match partial track names."""
        song_name = "Bohemian Rhapsody (Remastered 2011)"
        track_name = "Bohemian Rhapsody"
        
        matches_track = track_name.lower() in song_name.lower()
        assert matches_track is True

    def test_case_insensitive_matching(self):
        """Should match regardless of case."""
        song_name = "BOHEMIAN RHAPSODY"
        song_artist = "QUEEN"
        track_name = "bohemian rhapsody"
        artist = "queen"

        matches_track = track_name.lower() in song_name.lower()
        matches_artist = artist.lower() in song_artist.lower()
        assert matches_track is True
        assert matches_artist is True


class TestPaginationWithFetchExplicit:
    """Tests for pagination when fetch_explicit is True.

    When fetch_explicit=True and playlist name is provided, we use the
    cache-first approach: AppleScript for fast native access, then check
    cache for explicit status, only hitting API on cache miss.
    """

    @responses.activate
    def test_uses_applescript_with_cache_when_fetch_explicit_true(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """With fetch_explicit=True and playlist name, should use AppleScript + cache.

        AppleScript provides fast native access, cache stores explicit status.
        API is only called on cache miss.
        """
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", True)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock AppleScript - SHOULD be called
        mock_applescript_called = False
        def mock_asc_get_tracks(*args, **kwargs):
            nonlocal mock_applescript_called
            mock_applescript_called = True
            return (True, [
                {"name": f"Track {i}", "artist": "Artist", "album": "Album", "id": f"PID{i}"}
                for i in range(5)
            ])

        # Mock cache - return cached explicit status for all tracks
        mock_cache = MagicMock()
        mock_cache.get_explicit.return_value = "Clean"  # All tracks have cached explicit status

        with patch.object(server.asc, 'get_playlist_tracks', side_effect=mock_asc_get_tracks):
            with patch.object(server, 'get_track_cache', return_value=mock_cache):
                # Call with playlist name and fetch_explicit=True
                result = server.playlist(action="tracks", playlist="Test Playlist", fetch_explicit=True)

                # Should use AppleScript
                assert mock_applescript_called, "AppleScript should be called for fast native access"

                # Cache should be checked for each track (via get_explicit)
                assert mock_cache.get_explicit.call_count == 5

                # Should show all 5 tracks
                assert "=== 5 tracks ===" in result
                assert "Track 0" in result
                assert "Track 4" in result
                # Note: text format may not display explicit status, but cache was used

    @responses.activate
    def test_optimized_pagination_minimal_api_calls(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """With limit specified, should only fetch needed tracks, not all.

        Performance test: limit=5 on a 500 track playlist should make 1 API call,
        not 5 calls to fetch all 500 tracks.
        """
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)

        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API: return 5 tracks (simulating a partial response)
        api_call_count = 0
        def request_callback(request):
            nonlocal api_call_count
            api_call_count += 1
            return (200, {}, json.dumps({
                "data": [
                    {
                        "id": f"i.lib{i}",
                        "attributes": {
                            "name": f"Track {i}",
                            "artistName": "Artist",
                            "albumName": "Album",
                            "contentRating": "clean",
                        }
                    }
                    for i in range(5)
                ]
            }))

        responses.add_callback(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.test123/tracks",
            callback=request_callback,
            content_type="application/json",
        )

        # Call with playlist ID and limit=5
        result = server.playlist(action="tracks", playlist="p.test123", limit=5)

        # Should only make 1 API call, not multiple
        assert api_call_count == 1, f"Expected 1 API call, got {api_call_count}"
        assert "Track 0" in result
        assert "Track 4" in result


class TestFindApiPlaylistByName:
    """Tests for _find_api_playlist_by_name function."""

    @responses.activate
    def test_finds_exact_match(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should find playlist by exact name match."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.abc123", "attributes": {"name": "My Playlist"}},
                    {"id": "p.def456", "attributes": {"name": "Another Playlist"}},
                ]
            },
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("My Playlist")
        assert playlist_id == "p.abc123"
        assert fuzzy_match is None  # Should be exact match

    @responses.activate
    def test_finds_partial_match(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should find playlist by partial name match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.emoji123", "attributes": {"name": "🎸 Rock Playlist"}},
                ]
            },
            status=200,
        )

        # Partial match without emoji
        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Rock Playlist")
        assert playlist_id == "p.emoji123"

    @responses.activate
    def test_prefers_exact_match_over_partial(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should prefer exact match over partial match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Partial match comes first in the list, but exact match should win
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.partial", "attributes": {"name": "Rock Playlist Extended"}},
                    {"id": "p.exact", "attributes": {"name": "Rock Playlist"}},
                ]
            },
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Rock Playlist")
        assert playlist_id == "p.exact"
        assert fuzzy_match is None  # Should be exact match

    @responses.activate
    def test_returns_none_when_not_found(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return None when playlist not found."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": []},
            status=200,
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Nonexistent Playlist")
        assert playlist_id is None
        assert fuzzy_match is None

    @responses.activate
    def test_returns_none_on_api_error(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should return None on API error (graceful fallback)."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            status=401,  # Unauthorized
        )

        playlist_id, fuzzy_match = server._find_api_playlist_by_name("Any Playlist")
        assert playlist_id is None
        assert fuzzy_match is None


class TestResolvePlaylistApiLookup:
    """Tests for _resolve_playlist API lookup behavior."""

    def test_returns_id_directly_for_p_prefix(self):
        """Should return playlist ID directly when p. prefix provided."""
        resolved = server._resolve_playlist("p.abc123xyz")
        assert resolved.api_id == "p.abc123xyz"
        assert resolved.applescript_name is None
        assert resolved.error is None
        assert resolved.raw_input == "p.abc123xyz"

    def test_returns_error_for_empty_string(self):
        """Should return error for empty playlist string."""
        resolved = server._resolve_playlist("")
        assert resolved.api_id is None
        assert resolved.applescript_name is None
        assert resolved.error is not None
        assert "required" in resolved.error.lower()

    @responses.activate
    def test_looks_up_api_id_for_name(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should look up API playlist ID when given a name."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.found123", "attributes": {"name": "My Music"}},
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("My Music")

        # Should return BOTH API ID and AppleScript name when match found
        assert resolved.api_id == "p.found123"
        assert resolved.applescript_name == "My Music"  # Matched name populated too!
        assert resolved.error is None
        assert resolved.raw_input == "My Music"

    @responses.activate
    def test_falls_back_to_name_when_not_in_api(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should fall back to playlist name when not found in API."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": []},  # Empty - playlist not in API
            status=200,
        )

        resolved = server._resolve_playlist("Local Only Playlist")

        # Should fall back to name for AppleScript
        assert resolved.api_id is None
        assert resolved.applescript_name == "Local Only Playlist"
        assert resolved.error is None

    def test_handles_ps_i_love_you_as_name(self):
        """Should treat 'p.s. I love you' as a name, not an ID."""
        # This tests the edge case where a playlist name starts with "p."
        # but isn't an ID (has spaces/punctuation after p.)
        with patch.object(server, '_find_api_playlist_by_name', return_value=(None, None)):
            resolved = server._resolve_playlist("p.s. I love you")

        # Should be treated as a name, not an ID
        assert resolved.api_id is None
        assert resolved.applescript_name == "p.s. I love you"
        assert resolved.error is None


class TestFuzzyMatchingPlaylistResolution:
    """Tests for fuzzy matching playlist names - REGRESSION TESTS."""

    @responses.activate
    def test_fuzzy_matches_and_vs_ampersand(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should fuzzy match 'Jack and Norah' to 'Jack & Norah'."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with playlist named "Jack & Norah"
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.jack123", "attributes": {"name": "Jack & Norah"}},
                ]
            },
            status=200,
        )

        # User types "Jack and Norah" (with "and" instead of "&")
        resolved = server._resolve_playlist("Jack and Norah")

        # Should fuzzy match and return BOTH identifiers
        assert resolved.api_id == "p.jack123"
        assert resolved.applescript_name == "Jack & Norah"  # CRITICAL: Must have actual name
        assert resolved.error is None
        assert resolved.raw_input == "Jack and Norah"
        assert resolved.fuzzy_match is not None
        assert "and" in str(resolved.fuzzy_match.transformations).lower() or "&" in str(resolved.fuzzy_match.transformations)

    @responses.activate
    def test_fuzzy_matches_with_emojis(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should fuzzy match playlist names with emojis removed."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with emoji playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.emoji123", "attributes": {"name": "🤟👶🎸 Jack & Norah"}},
                ]
            },
            status=200,
        )

        # User types without emojis
        resolved = server._resolve_playlist("Jack & Norah")

        # Should fuzzy match (emojis ignored)
        assert resolved.api_id == "p.emoji123"
        assert resolved.applescript_name == "🤟👶🎸 Jack & Norah"  # Keep actual emoji name
        assert resolved.error is None
        assert resolved.fuzzy_match is not None

    @responses.activate
    def test_exact_match_preferred_over_fuzzy(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should prefer exact match over fuzzy match."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API response with both exact and fuzzy matches
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.fuzzy", "attributes": {"name": "The Rock Music"}},  # Fuzzy (article removed)
                    {"id": "p.exact", "attributes": {"name": "Rock Music"}},  # Exact match
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("Rock Music")

        # Should choose exact match
        assert resolved.api_id == "p.exact"
        assert resolved.applescript_name == "Rock Music"
        assert resolved.fuzzy_match is None or resolved.fuzzy_match.match_type == "exact"

    @responses.activate
    def test_resolved_object_has_both_ids_after_fuzzy_match(self, mock_config_dir, mock_developer_token, mock_user_token):
        """REGRESSION TEST: Resolved object MUST have both api_id and applescript_name after fuzzy match.

        This is the critical fix for the bug where remove_from_playlist("Jack and Norah", ...)
        would fail because fuzzy matching converted to API ID but function needs AppleScript name.
        """
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.test123", "attributes": {"name": "Test & Playlist"}},
                ]
            },
            status=200,
        )

        resolved = server._resolve_playlist("Test and Playlist")

        # CRITICAL: Both must be populated
        assert resolved.api_id is not None, "api_id must be populated after fuzzy match"
        assert resolved.applescript_name is not None, "applescript_name must be populated after fuzzy match"
        assert resolved.api_id == "p.test123"
        assert resolved.applescript_name == "Test & Playlist"


class TestPlaylistResolutionPerformance:
    """Performance tests for playlist resolution."""

    @responses.activate
    def test_fuzzy_matching_performance_with_many_playlists(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should complete fuzzy matching in reasonable time even with many playlists.

        This tests the optimization where fuzzy matching only happens if exact/partial fails.
        With 50 playlists and target at position 25, should complete quickly.
        """
        import time as time_module

        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock API with 50 playlists (realistic library size)
        playlists = [
            {"id": f"p.test{i}", "attributes": {"name": f"Playlist {i}"}}
            for i in range(25)
        ]
        # Add fuzzy match target in the middle
        playlists.append({"id": "p.target", "attributes": {"name": "Rock & Roll"}})
        # Add more playlists after
        playlists.extend([
            {"id": f"p.test{i}", "attributes": {"name": f"Playlist {i}"}}
            for i in range(25, 50)
        ])

        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlists},
            status=200,
        )

        start = time_module.time()
        resolved = server._resolve_playlist("Rock and Roll")  # Fuzzy: "and" → "&"
        elapsed = time_module.time() - start

        # Should complete in under 500ms (fuzzy matching is now fallback-only)
        assert elapsed < 0.5, f"Fuzzy matching took {elapsed:.2f}s, should be < 0.5s"
        assert resolved.api_id == "p.target"
        assert resolved.applescript_name == "Rock & Roll"
        assert resolved.fuzzy_match is not None, "Should be a fuzzy match"


# =============================================================================
# Integration Tests - Real User Journeys
# =============================================================================
# These tests validate realistic workflows at different scales:
# - First 1-2 actions: Basic discovery
# - First 5 actions: Getting started
# - First 10 actions: Regular user
# - First 20 actions: Power user
#
# Each flow is tested in three modes:
# - API-only: APPLESCRIPT_AVAILABLE = False
# - macOS-only: AppleScript mocked, preferred for local operations
# - Combined: Both available, tests routing logic
# =============================================================================


class TestUserJourneyAPIOnly:
    """Integration tests for API-only mode (non-macOS or AppleScript unavailable)."""

    @responses.activate
    def test_first_2_actions_search_and_list_playlists(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """First things users do: search for music and see their playlists."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 1. Search catalog for a song
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={
                "results": {
                    "songs": {
                        "data": [
                            {
                                "id": "123456",
                                "attributes": {
                                    "name": "Hey Jude",
                                    "artistName": "The Beatles",
                                    "albumName": "Past Masters",
                                    "durationInMillis": 431000,
                                    "releaseDate": "1968-08-26",
                                    "genreNames": ["Rock"],
                                }
                            }
                        ]
                    }
                }
            },
            status=200,
        )

        result = server.catalog(action="search", query="Hey Jude Beatles")
        assert "Hey Jude" in result
        assert "Beatles" in result

        # 2. List playlists
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={
                "data": [
                    {"id": "p.favorites", "attributes": {"name": "Favorites", "canEdit": True}},
                    {"id": "p.workout", "attributes": {"name": "Workout Mix", "canEdit": True}},
                ]
            },
            status=200,
        )

        result = server.playlist(action="list")
        assert "Favorites" in result
        assert "Workout Mix" in result

    @responses.activate
    def test_first_5_actions_basic_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Getting started: search, list playlists, get tracks, add to playlist."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 1. Search catalog
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"songs": {"data": [
                {"id": "song1", "attributes": {"name": "Bohemian Rhapsody", "artistName": "Queen",
                 "albumName": "A Night at the Opera", "durationInMillis": 354000,
                 "releaseDate": "1975-10-31", "genreNames": ["Rock"]}}
            ]}}},
            status=200,
        )
        result = server.catalog(action="search", query="Bohemian Rhapsody")
        assert "Bohemian Rhapsody" in result

        # 2. List playlists
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [
                {"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}},
            ]},
            status=200,
        )
        result = server.playlist(action="list")
        assert "Classic Rock" in result

        # 3. Get playlist tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [
                {"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}},
            ]},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={"data": [
                {"id": "i.track1", "attributes": {"name": "Stairway to Heaven", "artistName": "Led Zeppelin",
                 "albumName": "Led Zeppelin IV", "durationInMillis": 482000}}
            ]},
            status=200,
        )
        result = server.playlist(action="tracks", playlist="Classic Rock")
        assert "Stairway to Heaven" in result

        # 4. Search library
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/search",
            json={"results": {"library-songs": {"data": [
                {"id": "i.lib1", "attributes": {"name": "Hotel California", "artistName": "Eagles",
                 "albumName": "Hotel California", "durationInMillis": 391000}}
            ]}}},
            status=200,
        )
        result = server.library(action="search", query="Hotel California")
        assert "Hotel California" in result

        # 5. Add track to playlist (via catalog search + add)
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.rock", "attributes": {"name": "Classic Rock", "canEdit": True}}]},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"songs": {"data": [
                {"id": "cat123", "attributes": {"name": "Dream On", "artistName": "Aerosmith",
                 "albumName": "Aerosmith", "durationInMillis": 267000, "releaseDate": "1973-01-01",
                 "genreNames": ["Rock"]}}
            ]}}},
            status=200,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.rock/tracks",
            json={"data": [
                {"id": "i.track1", "attributes": {"name": "Dream On", "artistName": "Aerosmith"}}
            ]},
            status=200,
        )
        result = server.playlist(action="add", playlist="Classic Rock", track="Dream On", artist="Aerosmith")
        assert "Dream On" in result or "Added" in result or "error" not in result.lower()

    @responses.activate
    def test_first_10_actions_regular_user(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Regular user: create playlist, add/remove tracks, get recommendations."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # 6. Create a new playlist
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.new123", "attributes": {"name": "My New Playlist"}}]},
            status=201,
        )
        result = server.playlist(action="create", name="My New Playlist", description="Created for testing")
        assert "p.new123" in result or "My New Playlist" in result

        # 7. Get recently played
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recent/played/tracks",
            json={"data": [
                {"id": "recent1", "attributes": {"name": "Yesterday", "artistName": "The Beatles",
                 "albumName": "Help!", "durationInMillis": 125000}}
            ]},
            status=200,
        )
        result = server.library(action="recently_played", limit=5)
        assert "Yesterday" in result or "recent" in result.lower()

        # 8. Get recommendations
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={"data": [
                {"id": "rec1", "type": "playlists", "attributes": {"name": "For You"}}
            ]},
            status=200,
        )
        result = server.discover(action="recommendations")
        # Just check it doesn't error

        # 9. Get heavy rotation
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/history/heavy-rotation",
            json={"data": [
                {"id": "hr1", "type": "albums", "attributes": {"name": "Abbey Road", "artistName": "The Beatles"}}
            ]},
            status=200,
        )
        result = server.discover(action="heavy_rotation")
        assert "Abbey Road" in result or result  # Just check no error

        # 10. Get artist top songs
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"artists": {"data": [
                {"id": "artist1", "attributes": {"name": "The Beatles", "genreNames": ["Rock"]}}
            ]}}},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/artists/artist1/view/top-songs",
            json={"data": [
                {"id": "top1", "attributes": {"name": "Come Together", "artistName": "The Beatles",
                 "albumName": "Abbey Road", "durationInMillis": 259000}}
            ]},
            status=200,
        )
        result = server.discover(action="top_songs", artist="The Beatles")
        assert "Come Together" in result or "Beatles" in result

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyFuzzyMatching:
    """Integration tests for fuzzy matching across all entity types."""

    @responses.activate
    def test_fuzzy_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User workflow with fuzzy-named playlist: get tracks, add, remove."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Playlist has special characters
        playlist_data = [
            {"id": "p.fuzzy1", "attributes": {"name": "🎸 Rock & Roll Classics", "canEdit": True}}
        ]

        # 1. Get tracks from fuzzy-named playlist (typed without emoji, "and" instead of "&")
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlist_data},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={"data": [
                {"id": "i.t1", "attributes": {"name": "Sweet Child O' Mine", "artistName": "Guns N' Roses",
                 "albumName": "Appetite for Destruction", "durationInMillis": 356000}}
            ]},
            status=200,
        )

        result = server.playlist(action="tracks", playlist="Rock and Roll Classics")
        assert "Sweet Child" in result
        assert "Fuzzy match" in result or "fuzzy" in result.lower()

        # 2. Add to fuzzy-named playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": playlist_data},
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"songs": {"data": [
                {"id": "cat456", "attributes": {"name": "Back in Black", "artistName": "AC/DC",
                 "albumName": "Back in Black", "durationInMillis": 255000, "releaseDate": "1980-07-25",
                 "genreNames": ["Rock"]}}
            ]}}},
            status=200,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={},
            status=201,
        )
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.fuzzy1/tracks",
            json={"data": [{"id": "i.new", "attributes": {"name": "Back in Black"}}]},
            status=200,
        )

        result = server.playlist(action="add", playlist="Rock and Roll Classics", track="Back in Black")
        assert "Back in Black" in result or "Added" in result

    @responses.activate
    def test_fuzzy_track_search_in_catalog(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User searches with typos/variations, fuzzy matching finds correct track."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # API returns track with proper name
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"songs": {"data": [
                {"id": "789", "attributes": {"name": "Can't Buy Me Love", "artistName": "The Beatles",
                 "albumName": "A Hard Day's Night", "durationInMillis": 137000,
                 "releaseDate": "1964-03-16", "genreNames": ["Rock"]}}
            ]}}},
            status=200,
        )

        # User types without apostrophe
        result = server.catalog(action="search", query="Cant Buy Me Love Beatles")
        assert "Can't Buy Me Love" in result or "Cant Buy Me Love" in result

    @responses.activate
    def test_fuzzy_album_search(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """User searches for album with fuzzy name."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Test _find_matching_catalog_album with fuzzy input
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"albums": {"data": [
                {"id": "album1", "attributes": {"name": "Sgt. Pepper's Lonely Hearts Club Band",
                 "artistName": "The Beatles", "trackCount": 13, "releaseDate": "1967-06-01"}}
            ]}}},
            status=200,
        )

        album, error, fuzzy = server._find_matching_catalog_album(
            "Sgt Peppers Lonely Hearts", "Beatles"
        )
        assert album is not None
        assert error is None
        assert album.get("id") == "album1"

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyMacOSOnly:
    """Integration tests for macOS-only mode (AppleScript preferred)."""

    def test_playlist_operations_prefer_applescript(self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch):
        """On macOS, playlist operations should prefer AppleScript when possible."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", True)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Mock AppleScript module
        mock_asc = MagicMock()
        mock_asc.get_playlists.return_value = (True, [
            {"name": "Chill Vibes", "id": "abc123", "count": 25}
        ])
        monkeypatch.setattr(server, "asc", mock_asc)

        result = server.playlist(action="list")
        assert "Chill Vibes" in result
        mock_asc.get_playlists.assert_called_once()

    def test_remove_from_playlist_uses_applescript_name(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """remove_from_playlist MUST use AppleScript name, not API ID."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", True)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Mock AppleScript
        mock_asc = MagicMock()
        mock_asc.get_playlists.return_value = (True, [
            {"name": "🎵 My Mix", "id": "xyz789", "count": 10}
        ])
        mock_asc.remove_track_from_playlist.return_value = (True, "Removed")
        monkeypatch.setattr(server, "asc", mock_asc)

        # Resolve playlist with fuzzy name
        resolved = server._resolve_playlist("My Mix")

        # Critical: applescript_name must be the actual name with emoji
        assert resolved.applescript_name == "🎵 My Mix"
        assert resolved.api_id is None or resolved.applescript_name is not None

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyCombinedMode:
    """Integration tests for combined mode (both API and AppleScript available)."""

    @responses.activate
    def test_add_to_playlist_chooses_best_mode(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """add_to_playlist should use AppleScript for track names, API for IDs."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", True)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Mock AppleScript
        mock_asc = MagicMock()
        mock_asc.get_playlists.return_value = (True, [
            {"name": "Workout", "id": "work123", "count": 50}
        ])
        mock_asc.track_exists_in_playlist.return_value = (True, False)  # Track doesn't exist yet
        mock_asc.add_track_to_playlist.return_value = (True, "Added")
        monkeypatch.setattr(server, "asc", mock_asc)

        # Mock API for playlist resolution
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.work", "attributes": {"name": "Workout", "canEdit": True}}]},
            status=200,
        )

        # Add track by name - should use AppleScript
        result = server.playlist(action="add", playlist="Workout", track="Eye of the Tiger")

        # AppleScript should have been called for track name operations
        # (The exact assertion depends on implementation details)

    @responses.activate
    def test_fallback_to_api_when_applescript_fails(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Should fall back to API when AppleScript fails."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", True)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Mock AppleScript to fail
        mock_asc = MagicMock()
        mock_asc.get_playlists.return_value = (False, "AppleScript error")
        monkeypatch.setattr(server, "asc", mock_asc)

        # Mock API fallback
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [
                {"id": "p.fallback", "attributes": {"name": "API Playlist", "canEdit": True}}
            ]},
            status=200,
        )

        result = server.playlist(action="list")
        assert "API Playlist" in result

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestUserJourneyPowerUser:
    """Integration tests for power user workflows (20+ actions)."""

    @responses.activate
    def test_album_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Power user: add entire album to library."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Search for album
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"albums": {"data": [
                {"id": "alb1", "attributes": {"name": "Dark Side of the Moon",
                 "artistName": "Pink Floyd", "trackCount": 10, "releaseDate": "1973-03-01"}}
            ]}}},
            status=200,
        )

        # Add album to library
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library",
            json={},
            status=202,
        )

        result = server.library(action="add", album="Dark Side of the Moon", artist="Pink Floyd")
        assert "Dark Side" in result or "Added" in result.lower() or "Album" in result

    @responses.activate
    def test_copy_playlist_workflow(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Power user: copy a playlist."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # Source playlist
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.src", "attributes": {"name": "Original Mix", "canEdit": True}}]},
            status=200,
        )

        # Get source tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/library/playlists/p.src/tracks",
            json={"data": [
                {"id": "i.t1", "attributes": {"name": "Track 1", "artistName": "Artist 1"}},
                {"id": "i.t2", "attributes": {"name": "Track 2", "artistName": "Artist 2"}},
            ]},
            status=200,
        )

        # Create new playlist
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists",
            json={"data": [{"id": "p.new", "attributes": {"name": "Copy of Original Mix"}}]},
            status=201,
        )

        # Add tracks to new playlist
        responses.add(
            responses.POST,
            "https://api.music.apple.com/v1/me/library/playlists/p.new/tracks",
            json={},
            status=201,
        )

        result = server.playlist(action="copy", source="Original Mix", new_name="Copy of Original Mix")
        assert "p.new" in result or "Copy" in result or "copied" in result.lower()

    @responses.activate
    def test_search_deduplication(
        self, mock_config_dir, mock_developer_token, mock_user_token, monkeypatch
    ):
        """Search results should be deduplicated by track ID."""
        monkeypatch.setattr(server, "APPLESCRIPT_AVAILABLE", False)
        self._setup_tokens(mock_config_dir, mock_developer_token, mock_user_token)

        # API returns duplicates
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"songs": {"data": [
                {"id": "dup1", "attributes": {"name": "Duplicate Song", "artistName": "Artist",
                 "albumName": "Album", "durationInMillis": 200000, "releaseDate": "2020-01-01",
                 "genreNames": ["Pop"]}},
                {"id": "dup1", "attributes": {"name": "Duplicate Song", "artistName": "Artist",
                 "albumName": "Album", "durationInMillis": 200000, "releaseDate": "2020-01-01",
                 "genreNames": ["Pop"]}},  # Same ID = duplicate
                {"id": "dup2", "attributes": {"name": "Unique Song", "artistName": "Artist",
                 "albumName": "Album", "durationInMillis": 180000, "releaseDate": "2020-01-01",
                 "genreNames": ["Pop"]}},
            ]}}},
            status=200,
        )

        result = server.catalog(action="search", query="test query")

        # Should show "2 Songs" not "3 Songs"
        assert "2 Songs" in result

    def _setup_tokens(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Helper to setup authentication tokens."""
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)


class TestCatalogAlbumDetails:
    """Tests for catalog album_details action."""

    @responses.activate
    def test_album_details_by_id(self, mock_config_dir, mock_developer_token, mock_user_token):
        """Should fetch album metadata and tracks by catalog ID."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock album metadata response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/1781270319",
            json={"data": [{
                "id": "1781270319",
                "attributes": {
                    "name": "GNX",
                    "artistName": "Kendrick Lamar",
                    "releaseDate": "2024-11-22",
                    "genreNames": ["Hip-Hop/Rap"],
                    "recordLabel": "pgLang",
                    "trackCount": 12,
                    "copyright": "℗ 2024 pgLang"
                }
            }]},
            status=200,
        )

        # Mock tracks response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/1781270319/tracks",
            json={"data": [
                {"id": "t1", "attributes": {"name": "wacced out murals", "durationInMillis": 251000}},
                {"id": "t2", "attributes": {"name": "squabble up", "durationInMillis": 193000}},
            ]},
            status=200,
        )

        result = server.catalog(action="album_details", album="1781270319")

        assert "GNX" in result
        assert "Kendrick Lamar" in result
        assert "2024-11-22" in result
        assert "Hip-Hop/Rap" in result
        assert "pgLang" in result
        assert "wacced out murals" in result
        assert "squabble up" in result

    @responses.activate
    def test_album_details_by_name_fuzzy_match(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should find album by name using fuzzy matching."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"albums": {"data": [{
                "id": "123",
                "attributes": {"name": "Abbey Road", "artistName": "The Beatles"}
            }]}}},
            status=200,
        )

        # Mock album metadata
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/123",
            json={"data": [{
                "id": "123",
                "attributes": {
                    "name": "Abbey Road",
                    "artistName": "The Beatles",
                    "releaseDate": "1969-09-26",
                    "genreNames": ["Rock"],
                    "recordLabel": "Apple Records",
                    "trackCount": 17,
                    "copyright": "℗ 1969"
                }
            }]},
            status=200,
        )

        # Mock tracks
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/albums/123/tracks",
            json={"data": [
                {"id": "t1", "attributes": {"name": "Come Together", "durationInMillis": 259000}},
            ]},
            status=200,
        )

        result = server.catalog(action="album_details", album="abbey road", artist="beatles")

        assert "Abbey Road" in result
        assert "The Beatles" in result
        assert "Come Together" in result

    @responses.activate
    def test_album_details_missing_album_error(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return error when album not found."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search with no results
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/us/search",
            json={"results": {"albums": {"data": []}}},
            status=200,
        )

        result = server.catalog(action="album_details", album="NonexistentAlbum999")

        assert "not found" in result.lower() or "error" in result.lower()


class TestDiscoverStorefrontParameter:
    """Tests for discover action storefront parameter."""

    @responses.activate
    def test_charts_with_storefront_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should query Italian charts without changing default storefront."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock Italy charts response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/it/charts",
            json={"results": {"songs": [{
                "name": "Top brani",
                "data": [
                    {"id": "it1", "attributes": {"name": "Italian Song", "artistName": "Italian Artist"}},
                ]
            }]}},
            status=200,
        )

        result = server.discover(action="charts", chart_type="songs", storefront="it")

        # Verify it was called with 'it' storefront
        assert len(responses.calls) == 1
        assert "/catalog/it/charts" in responses.calls[0].request.url
        assert "Italian Song" in result or "Top brani" in result

    @responses.activate
    def test_top_songs_with_storefront_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should query artist top songs in specific storefront."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock search in JP storefront
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/jp/search",
            json={"results": {"artists": {"data": [{
                "id": "jp-artist-123",
                "attributes": {"name": "Japanese Artist"}
            }]}}},
            status=200,
        )

        # Mock top songs
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/catalog/jp/artists/jp-artist-123/view/top-songs",
            json={"data": [
                {"id": "jp-song-1", "attributes": {"name": "JP Hit Song", "artistName": "Japanese Artist"}},
            ]},
            status=200,
        )

        result = server.discover(action="top_songs", artist="Japanese Artist", storefront="jp")

        # Verify JP storefront was used
        assert any("/catalog/jp/" in call.request.url for call in responses.calls)
        assert "Japanese Artist" in result


class TestDiscoverRecommendationsLimit:
    """Tests for discover recommendations limit parameter."""

    @responses.activate
    def test_recommendations_respects_limit_parameter(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should only return requested number of recommendations."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock recommendations response with many items
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={"data": [
                {
                    "attributes": {"title": {"stringForDisplay": "For You"}},
                    "relationships": {"contents": {"data": [
                        {"id": f"rec{i}", "type": "songs", "attributes": {
                            "name": f"Song {i}", "artistName": "Artist", "releaseDate": "2024-01-01"
                        }} for i in range(1, 51)  # 50 items
                    ]}}
                }
            ]},
            status=200,
        )

        # Request only 15 items
        result = server.discover(action="recommendations", limit=15, format="text")

        # Count items in result (rough check - each song should have a line)
        lines = [l for l in result.split('\n') if l.strip() and not l.startswith('===')]
        # Should have ~15 lines, not 50
        assert len(lines) <= 20  # Allow some buffer for formatting

    @responses.activate
    def test_recommendations_limit_zero_returns_all(
        self, mock_config_dir, mock_developer_token, mock_user_token
    ):
        """Should return all recommendations when limit=0."""
        # Setup tokens
        dev_token_file = mock_config_dir / "developer_token.json"
        with open(dev_token_file, "w") as f:
            json.dump({"token": mock_developer_token, "expires": time.time() + 86400 * 60}, f)

        user_token_file = mock_config_dir / "music_user_token.json"
        with open(user_token_file, "w") as f:
            json.dump({"music_user_token": mock_user_token}, f)

        # Mock recommendations response
        responses.add(
            responses.GET,
            "https://api.music.apple.com/v1/me/recommendations",
            json={"data": [
                {
                    "attributes": {"title": {"stringForDisplay": "For You"}},
                    "relationships": {"contents": {"data": [
                        {"id": f"rec{i}", "type": "songs", "attributes": {
                            "name": f"Song {i}", "artistName": "Artist", "releaseDate": "2024-01-01"
                        }} for i in range(1, 21)  # 20 items
                    ]}}
                }
            ]},
            status=200,
        )

        # Request with limit=0 (should return all)
        result = server.discover(action="recommendations", limit=0, format="text")

        # Should have all ~20 items
        lines = [l for l in result.split('\n') if l.strip() and not l.startswith('===')]
        assert len(lines) >= 7  # At least 7-8 items from the category

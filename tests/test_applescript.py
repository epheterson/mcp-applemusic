"""Tests for AppleScript integration (applescript.py)."""

import time
import pytest
from src.applemusic_mcp import applescript as asc


class TestAvailability:
    """Test that AppleScript is available on macOS."""

    def test_is_available_on_macos(self):
        """Should return True on macOS with osascript."""
        assert asc.is_available() is True

    def test_run_applescript_simple(self):
        """Should execute simple AppleScript and return output."""
        success, output = asc.run_applescript('return "hello"')
        assert success is True
        assert output == "hello"

    def test_run_applescript_math(self):
        """Should handle AppleScript math operations."""
        success, output = asc.run_applescript("return 2 + 2")
        assert success is True
        assert output.strip() == "4"

    def test_run_applescript_error(self):
        """Should return error for invalid AppleScript."""
        success, output = asc.run_applescript("this is not valid applescript")
        assert success is False
        assert len(output) > 0  # Should have error message


class TestPlaybackControl:
    """Test playback control functions."""

    def test_get_player_state(self):
        """Should return a valid player state."""
        success, state = asc.get_player_state()
        assert success is True
        assert state in ["playing", "paused", "stopped", "fast forwarding", "rewinding"]

    def test_get_volume(self):
        """Should get volume level."""
        success, volume = asc.get_volume()
        assert success is True
        assert isinstance(volume, int)
        assert 0 <= volume <= 100

    def test_get_shuffle(self):
        """Should get shuffle state."""
        success, shuffle = asc.get_shuffle()
        assert success is True
        assert isinstance(shuffle, bool)

    def test_get_repeat(self):
        """Should return a valid repeat mode."""
        success, mode = asc.get_repeat()
        assert success is True
        assert mode.strip() in ["off", "one", "all"]

    def test_get_current_track_when_stopped(self):
        """Should handle case when nothing is playing."""
        # This test may or may not pass depending on Music state
        # Just verify it returns a tuple without throwing
        result = asc.get_current_track()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)


class TestPlaylistOperations:
    """Test playlist operations."""

    def test_get_playlists(self):
        """Should return a list of playlist names."""
        success, playlists = asc.get_playlists()
        assert success is True
        assert isinstance(playlists, list)
        # Should have at least one playlist
        assert len(playlists) > 0
        # Each item should have a 'name' key
        for p in playlists:
            assert "name" in p

    def test_get_playlist_tracks(self):
        """Should return tracks for a known playlist."""
        # First get playlist names
        success, playlists = asc.get_playlists()
        assert success is True
        assert len(playlists) > 0

        # Try to get tracks from the first playlist
        playlist_name = playlists[0]["name"]
        success, tracks = asc.get_playlist_tracks(playlist_name)
        assert success is True
        assert isinstance(tracks, list)
        # Tracks might be empty but should be a list
        if tracks:
            # Each track should have basic info
            for track in tracks:
                assert "name" in track

    def test_get_playlist_tracks_not_found(self):
        """Should handle non-existent playlist gracefully."""
        success, result = asc.get_playlist_tracks("NonExistent_Test_Playlist_12345")
        assert success is False

    def test_create_and_delete_playlist(self):
        """Should create and delete a playlist."""
        test_name = "🧪 Test Playlist (delete me)"

        # Create
        success, msg = asc.create_playlist(test_name)
        assert success is True

        # Verify it exists
        success, playlists = asc.get_playlists()
        assert success is True
        names = [p["name"] for p in playlists]
        assert test_name in names

        # Delete
        success, msg = asc.delete_playlist(test_name)
        assert success is True

        # Verify deleted
        success, playlists = asc.get_playlists()
        assert success is True
        names = [p["name"] for p in playlists]
        assert test_name not in names


class TestLibrarySearch:
    """Test library search functions."""

    def test_search_library_all(self):
        """Should search across all types."""
        success, results = asc.search_library("the", "all")
        assert success is True
        assert isinstance(results, list)

    def test_search_library_artists(self):
        """Should search for artists."""
        success, results = asc.search_library("the", "artists")
        assert success is True
        assert isinstance(results, list)

    def test_search_library_structure(self):
        """Should return properly structured results."""
        success, results = asc.search_library("the", "all")
        assert success is True
        if results:
            for item in results:
                assert isinstance(item, dict)
                assert "name" in item


class TestLibraryStats:
    """Test library statistics."""

    def test_get_library_stats(self):
        """Should return library statistics."""
        success, stats = asc.get_library_stats()
        assert success is True
        assert isinstance(stats, dict)
        # Should have basic stat keys
        assert "total_tracks" in stats or "tracks" in stats or len(stats) > 0


class TestAirPlay:
    """Test AirPlay device listing."""

    def test_get_airplay_devices(self):
        """Should return AirPlay devices list."""
        success, devices = asc.get_airplay_devices()
        assert success is True
        assert isinstance(devices, list)
        # Should have at least the built-in speaker
        assert len(devices) > 0


class TestRepeatMode:
    """Test repeat mode setting."""

    def test_set_repeat_invalid(self):
        """Should reject invalid repeat modes."""
        success, result = asc.set_repeat("invalid_mode")
        assert success is False


class TestSpecialCharacters:
    """Test handling of special characters in input."""

    def test_quote_escaping_in_playlist_name(self):
        """Should handle quotes in playlist names safely."""
        # This tests the _escape_for_applescript function indirectly
        test_name = '🧪 Test "Quoted" Playlist'

        success, msg = asc.create_playlist(test_name)
        assert success is True

        # Verify and cleanup
        success, playlists = asc.get_playlists()
        names = [p["name"] for p in playlists]
        assert test_name in names

        success, msg = asc.delete_playlist(test_name)
        assert success is True

    def test_special_characters_in_search(self):
        """Should handle special characters in search queries."""
        # Should not crash, even with special chars
        for query in ["test's", 'test"s', "test\\s", "tëst"]:
            success, results = asc.search_library(query, "songs")
            assert isinstance(results, (list, str))


class TestRemoveTrack:
    """Test track removal edge cases."""

    def test_remove_nonexistent_track(self):
        """Should handle removing a track that doesn't exist."""
        success, result = asc.remove_track_from_playlist("Library", "NonExistent_Track_12345")
        assert success is False

    def test_remove_from_library_returns_tuple(self):
        """Should return a tuple for library removal."""
        success, result = asc.remove_from_library("NonExistent_Track_12345")
        assert isinstance(success, bool)
        assert isinstance(result, str)


class TestOpenCatalogSong:
    """Test open_catalog_song function."""

    def test_open_catalog_song_returns_tuple(self, monkeypatch):
        """Should return (success, message) tuple for valid Apple Music URL."""
        # Mock subprocess to avoid launching Music
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

        success, result = asc.open_catalog_song("https://music.apple.com/us/song/1234567890")
        assert isinstance(success, bool)
        assert isinstance(result, str)

    def test_open_catalog_song_rejects_empty_url(self):
        """Should reject empty URL."""
        success, result = asc.open_catalog_song("")
        assert success is False
        assert "empty" in result.lower() or "invalid" in result.lower()

    def test_open_catalog_song_rejects_non_apple_url(self):
        """Should reject URLs that aren't from Apple Music."""
        success, result = asc.open_catalog_song("https://spotify.com/track/123")
        assert success is False
        assert "not an apple music url" in result.lower()

    def test_open_catalog_song_rejects_invalid_format(self):
        """Should reject strings that aren't valid URLs."""
        success, result = asc.open_catalog_song("just-a-random-string")
        assert success is False
        assert "invalid url format" in result.lower()

    def test_open_catalog_song_accepts_music_scheme(self, monkeypatch):
        """Should accept music:// scheme URLs."""
        # Mock subprocess to avoid launching Music
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

        success, result = asc.open_catalog_song("music://music.apple.com/us/song/1234567890")
        assert isinstance(success, bool)
        assert isinstance(result, str)
        # If it fails, should not be a format rejection


class TestOpenCatalogAndPlay:
    """Test open_catalog_and_play function."""

    def _mock_subprocess(self, monkeypatch):
        """Helper to mock subprocess.run for open_catalog_song."""
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())

    def test_open_catalog_and_play_returns_tuple(self, monkeypatch):
        """Should return (success, message) tuple for album URL."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert isinstance(success, bool)
        assert isinstance(result, str)

    def test_open_catalog_and_play_rejects_empty_url(self):
        """Should reject empty URL (delegates to open_catalog_song validation)."""
        success, result = asc.open_catalog_and_play("")
        assert success is False

    def test_open_catalog_and_play_rejects_non_apple_url(self):
        """Should reject non-Apple Music URLs."""
        success, result = asc.open_catalog_and_play("https://spotify.com/track/123")
        assert success is False

    def test_open_catalog_and_play_rejects_song_url(self):
        """Should reject /song/ URLs with helpful message."""
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/song/track-name/1234567890")
        assert success is False
        assert "not supported" in result.lower()
        assert "?i=" in result

    def test_open_catalog_and_play_accepts_music_scheme(self, monkeypatch):
        """Should accept music:// scheme URLs."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))

        success, result = asc.open_catalog_and_play("music://music.apple.com/us/album/1234567890")
        assert isinstance(success, bool)

    def test_open_catalog_and_play_skips_click_if_already_playing(self, monkeypatch):
        """Should skip UI click if Music already started playing."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert success is True
        assert "auto-started" in result.lower()

    def test_open_catalog_and_play_click_play_button(self, monkeypatch):
        """Should find and click Play button when auto-play doesn't start."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)

        call_count = [0]
        def mock_run_applescript(script):
            call_count[0] += 1
            # _check_playing (attempt 1) -> stopped
            if call_count[0] == 1:
                return (True, "stopped")
            # _click_play_or_shuffle tries path 1 -> ok
            if call_count[0] == 2:
                return (True, "")
            # _check_playing after click -> playing
            return (True, "playing")

        monkeypatch.setattr(asc, "run_applescript", mock_run_applescript)

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert success is True
        assert "playing" in result.lower()

    def test_open_catalog_and_play_shuffle(self, monkeypatch):
        """Should click Shuffle button when shuffle=True."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)

        scripts_called = []
        call_count = [0]
        def mock_run_applescript(script):
            scripts_called.append(script)
            call_count[0] += 1
            if call_count[0] == 1:
                return (True, "stopped")
            if call_count[0] == 2:
                return (True, "")
            return (True, "playing")

        monkeypatch.setattr(asc, "run_applescript", mock_run_applescript)

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890", shuffle=True)
        assert success is True
        assert any("Shuffle" in s for s in scripts_called)
        assert "shuffling" in result.lower()

    def test_open_catalog_and_play_retry_exhaustion(self, monkeypatch):
        """Should return graceful message after retries exhausted."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "stopped"))

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890", timeout=0.1)
        assert success is True
        assert "could not confirm" in result.lower()

    def test_open_catalog_and_play_song_with_i_param(self, monkeypatch):
        """Should attempt track-specific playback for ?i= URLs."""
        self._mock_subprocess(monkeypatch)
        monkeypatch.setattr(time, "sleep", lambda _: None)

        call_count = [0]
        def mock_run_applescript(script):
            call_count[0] += 1
            if "player state" in script and call_count[0] <= 2:
                return (True, "stopped")
            if "activate" in script:
                return (True, "")
            if "Favorite" in script and "position" in script:
                return (True, "500.0,400.0,Test Track")
            if "size of window" in script:
                return (True, "1000")
            if "click checkbox" in script:
                return (True, "Test Track")
            if "player state" in script:
                return (True, "playing")
            return (True, "")

        monkeypatch.setattr(asc, "run_applescript", mock_run_applescript)
        monkeypatch.setattr(asc, "_jxa_mouse_move", lambda x, y: True)

        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/name/123?i=456")
        assert success is True
        assert "test track" in result.lower()


class TestAddTrackDisambiguation:
    """Test artist exact match and album disambiguation in add_track_to_playlist.

    These tests require specific tracks in the library:
    - Hot Potato by The Wiggles (Ready, Steady, Wiggle!)
    - Hot Potato by Dorothy the Dinosaur & The Wiggles (Dorothy The Dinosaur's Travelling Show)
    """

    TEST_PLAYLIST = "🧪 Integration Test Playlist"

    def test_artist_exact_match_preferred_over_contains(self):
        """Should prefer exact artist match over partial contains match.

        'artist is "The Wiggles"' should match solo Wiggles,
        not 'Dorothy the Dinosaur & The Wiggles'.
        """
        success, result = asc.add_track_to_playlist(
            self.TEST_PLAYLIST, "Hot Potato", "The Wiggles"
        )
        assert success is True
        # Should be the solo Wiggles version, not Dorothy collab
        assert "Dorothy" not in result
        assert "Ready, Steady, Wiggle!" in result

        # Cleanup
        asc.remove_track_from_playlist(self.TEST_PLAYLIST, "Hot Potato")

    def test_album_disambiguation_selects_correct_version(self):
        """Should use album param to disambiguate between track versions."""
        success, result = asc.add_track_to_playlist(
            self.TEST_PLAYLIST, "Hot Potato", "The Wiggles", "Ready, Steady"
        )
        assert success is True
        assert "Ready, Steady, Wiggle!" in result

        # Cleanup
        asc.remove_track_from_playlist(self.TEST_PLAYLIST, "Hot Potato")

    def test_album_param_accepted_without_artist(self):
        """Should accept album param even without artist for disambiguation."""
        success, result = asc.add_track_to_playlist(
            self.TEST_PLAYLIST, "Hot Potato", album="Ready, Steady"
        )
        assert success is True
        assert "Ready, Steady, Wiggle!" in result

        # Cleanup
        asc.remove_track_from_playlist(self.TEST_PLAYLIST, "Hot Potato")

    def test_fallback_to_contains_when_exact_fails(self):
        """Should fall back to contains if exact artist match finds nothing."""
        # "Dorothy the Dinosaur & The Wiggles" - only matches via contains
        success, result = asc.add_track_to_playlist(
            self.TEST_PLAYLIST, "Hot Potato", "Dorothy the Dinosaur"
        )
        assert success is True
        assert "Dorothy" in result

        # Cleanup
        asc.remove_track_from_playlist(self.TEST_PLAYLIST, "Hot Potato")

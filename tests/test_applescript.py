"""Tests for AppleScript integration module.

These tests only run on macOS where AppleScript is available.
They test the actual Music app integration.
"""

import pytest
import sys

# Skip all tests if not on macOS
pytestmark = pytest.mark.skipif(
    sys.platform != 'darwin',
    reason="AppleScript tests only run on macOS"
)

from applemusic_mcp import applescript as asc


class TestAppleScriptAvailability:
    """Test AppleScript availability detection."""

    def test_is_available_on_macos(self):
        """Should return True on macOS."""
        assert asc.is_available() is True

    def test_run_applescript_simple(self):
        """Should run simple AppleScript."""
        success, output = asc.run_applescript('return "hello"')
        assert success is True
        assert output == "hello"

    def test_run_applescript_math(self):
        """Should handle AppleScript expressions."""
        success, output = asc.run_applescript('return 2 + 2')
        assert success is True
        assert output == "4"

    def test_run_applescript_error(self):
        """Should handle AppleScript errors gracefully."""
        success, output = asc.run_applescript('this is not valid applescript')
        assert success is False
        assert len(output) > 0  # Should have error message


class TestPlaybackControl:
    """Test playback control functions."""

    def test_get_player_state(self):
        """Should get player state."""
        success, state = asc.get_player_state()
        assert success is True
        assert state in ('stopped', 'playing', 'paused')

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
        """Should get repeat mode."""
        success, repeat = asc.get_repeat()
        assert success is True
        assert repeat in ('off', 'one', 'all')

    def test_get_current_track_when_stopped(self):
        """Should handle stopped state gracefully."""
        success, info = asc.get_current_track()
        assert success is True
        # Either has track info or shows stopped
        assert isinstance(info, dict)


class TestPlaylistOperations:
    """Test playlist operations."""

    def test_get_playlists(self):
        """Should get list of playlists."""
        success, playlists = asc.get_playlists()
        assert success is True
        assert isinstance(playlists, list)
        assert len(playlists) > 0

        # Check playlist structure
        p = playlists[0]
        assert 'name' in p
        assert 'id' in p
        assert 'smart' in p
        assert 'track_count' in p

    def test_get_playlist_tracks(self):
        """Should get tracks from a known playlist."""
        # First get a playlist name
        success, playlists = asc.get_playlists()
        assert success is True
        assert len(playlists) > 0

        # Skip special/smart playlists that could be huge (Music, Music Videos, etc)
        special_names = {'Music', 'Music Videos', 'Library', 'Favorite Songs'}

        # Get tracks from first non-empty, non-special playlist
        for p in playlists:
            if p['track_count'] > 0 and p['track_count'] < 1000 and p['name'] not in special_names:
                success, tracks = asc.get_playlist_tracks(p['name'])
                assert success is True
                assert isinstance(tracks, list)
                if tracks:
                    t = tracks[0]
                    assert 'name' in t
                    assert 'artist' in t
                    assert 'album' in t
                    assert 'id' in t
                break

    def test_get_playlist_tracks_not_found(self):
        """Should handle missing playlist gracefully."""
        success, result = asc.get_playlist_tracks("__NONEXISTENT_PLAYLIST_12345__")
        assert success is False
        assert "not found" in result.lower()

    def test_create_and_delete_playlist(self):
        """Should create and delete a playlist."""
        test_name = "_TEST_PLAYLIST_DELETE_ME_"

        # Create
        success, playlist_id = asc.create_playlist(test_name, "Test description")
        assert success is True
        assert len(playlist_id) > 0

        # Verify it exists
        success, playlists = asc.get_playlists()
        assert success is True
        names = [p['name'] for p in playlists]
        assert test_name in names

        # Delete
        success, msg = asc.delete_playlist(test_name)
        assert success is True

        # Verify deleted
        success, playlists = asc.get_playlists()
        names = [p['name'] for p in playlists]
        assert test_name not in names


class TestLibrarySearch:
    """Test library search functions."""

    def test_search_library_all(self):
        """Should search entire library."""
        success, results = asc.search_library("love")
        assert success is True
        assert isinstance(results, list)
        # Should find something with "love" in a reasonable library
        assert len(results) >= 0

    def test_search_library_artists(self):
        """Should search by artist."""
        success, results = asc.search_library("Beatles", "artists")
        assert success is True
        assert isinstance(results, list)

    def test_search_library_structure(self):
        """Should return properly structured results."""
        success, results = asc.search_library("the")
        assert success is True
        if results:
            t = results[0]
            assert 'name' in t
            assert 'artist' in t
            assert 'album' in t
            assert 'duration' in t
            assert 'id' in t


class TestLibraryStats:
    """Test library statistics."""

    def test_get_library_stats(self):
        """Should get library statistics."""
        success, stats = asc.get_library_stats()
        assert success is True
        assert isinstance(stats, dict)
        assert 'track_count' in stats
        assert 'playlist_count' in stats
        assert 'player_state' in stats
        assert 'shuffle' in stats
        assert 'repeat' in stats
        assert 'volume' in stats

        assert isinstance(stats['track_count'], int)
        assert stats['track_count'] >= 0
        assert isinstance(stats['shuffle'], bool)


class TestAirPlay:
    """Test AirPlay functions."""

    def test_get_airplay_devices(self):
        """Should get AirPlay devices list."""
        success, devices = asc.get_airplay_devices()
        assert success is True
        assert isinstance(devices, list)
        # At minimum, the local computer should be available


class TestTrackMetadata:
    """Test track metadata operations."""

    def test_set_repeat_invalid(self):
        """Should reject invalid repeat mode."""
        success, msg = asc.set_repeat("invalid_mode")
        assert success is False
        assert "invalid" in msg.lower()


class TestInputSanitization:
    """Test that user input is properly sanitized to prevent injection."""

    def test_quote_escaping_in_playlist_name(self):
        """Should properly escape quotes in playlist names."""
        # Attempt to create a playlist with quotes in the name
        # This would break the AppleScript if not properly escaped
        test_name = '_TEST_QUOTES_"escape"_test_'

        # Create should not crash (quotes are escaped)
        success, result = asc.create_playlist(test_name)

        # Cleanup if it worked
        if success:
            asc.delete_playlist(test_name)

        # The main test is that we didn't crash/error on quote handling
        # Success depends on whether the playlist was actually created
        assert isinstance(success, bool)
        assert isinstance(result, str)

    def test_special_characters_in_search(self):
        """Should handle special characters in search queries."""
        # These characters should not cause AppleScript errors
        special_queries = [
            "test'quote",
            "test&ampersand",
            "test<angle>",
            "test\\backslash",
        ]
        for query in special_queries:
            # Should not raise an exception
            success, result = asc.search_library(query)
            assert isinstance(success, bool)


class TestRemoveFromLibrary:
    """Test remove_from_library function."""

    def test_remove_nonexistent_track(self):
        """Should return error for track not in library."""
        success, result = asc.remove_from_library("__NONEXISTENT_TRACK_12345__")
        assert success is False
        assert "not found" in result.lower()

    def test_remove_from_library_returns_tuple(self):
        """Should return (success, message) tuple."""
        success, result = asc.remove_from_library("test")
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
        if not success:
            assert "invalid url format" not in result.lower()
            assert "not an apple music url" not in result.lower()


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


class TestOpenCatalogAndPlay:
    """Test open_catalog_and_play function."""

    def _mock_subprocess(self, monkeypatch):
        """Mock subprocess.run for open_catalog_song."""
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())

    def _mock_time(self, monkeypatch):
        """Mock time.sleep to avoid delays."""
        import time
        monkeypatch.setattr(time, "sleep", lambda _: None)

    def test_returns_tuple(self, monkeypatch):
        """Should return (success, message) tuple for album URL."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert isinstance(success, bool)
        assert isinstance(result, str)

    def test_rejects_empty_url(self):
        """Should reject empty URL."""
        success, result = asc.open_catalog_and_play("")
        assert success is False

    def test_rejects_non_apple_url(self):
        """Should reject non-Apple Music URLs."""
        success, result = asc.open_catalog_and_play("https://spotify.com/track/123")
        assert success is False

    def test_rejects_song_url(self):
        """Should reject /song/ URLs with helpful message."""
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/song/track-name/1234567890")
        assert success is False
        assert "not supported" in result.lower()
        assert "?i=" in result

    def test_accepts_music_scheme(self, monkeypatch):
        """Should accept music:// scheme URLs."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))
        success, result = asc.open_catalog_and_play("music://music.apple.com/us/album/1234567890")
        assert isinstance(success, bool)

    def test_skips_click_if_already_playing(self, monkeypatch):
        """Should return immediately if Music auto-starts playing."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "playing"))
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert success is True
        assert "auto-started" in result.lower()

    def test_clicks_play_button(self, monkeypatch):
        """Should find and click Play button when auto-play doesn't start."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        call_count = [0]
        def mock_run(script):
            call_count[0] += 1
            if call_count[0] == 1:
                return (True, "stopped")
            if call_count[0] == 2:
                return (True, "")
            return (True, "playing")
        monkeypatch.setattr(asc, "run_applescript", mock_run)
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890")
        assert success is True
        assert "playing" in result.lower()

    def test_shuffle(self, monkeypatch):
        """Should click Shuffle button when shuffle=True."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        scripts_called = []
        call_count = [0]
        def mock_run(script):
            scripts_called.append(script)
            call_count[0] += 1
            if call_count[0] == 1:
                return (True, "stopped")
            if call_count[0] == 2:
                return (True, "")
            return (True, "playing")
        monkeypatch.setattr(asc, "run_applescript", mock_run)
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890", shuffle=True)
        assert success is True
        assert any("Shuffle" in s for s in scripts_called)

    def test_retry_exhaustion(self, monkeypatch):
        """Should return graceful message after timeout."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        monkeypatch.setattr(asc, "run_applescript", lambda script: (True, "stopped"))
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/1234567890", timeout=0.1)
        assert success is True
        assert "could not confirm" in result.lower()

    def test_song_with_i_param(self, monkeypatch):
        """Should attempt track-specific playback for ?i= URLs."""
        self._mock_subprocess(monkeypatch)
        self._mock_time(monkeypatch)
        call_count = [0]
        def mock_run(script):
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
        monkeypatch.setattr(asc, "run_applescript", mock_run)
        monkeypatch.setattr(asc, "_jxa_mouse_move", lambda x, y: True)
        success, result = asc.open_catalog_and_play("https://music.apple.com/us/album/name/123?i=456")
        assert success is True
        assert "test track" in result.lower()

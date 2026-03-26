"""AppleScript integration for Music.app on macOS.

This module provides direct control of the Music app via AppleScript,
enabling capabilities not available through the REST API like playback
control, deleting tracks from playlists, and deleting playlists.

Only available on macOS with the Music app installed.

Security Notes:
    - All user input (track names, playlist names, etc.) is escaped via
      _escape_for_applescript() which escapes backslashes first, then quotes,
      before embedding in AppleScript strings. This prevents injection attacks.
    - Scripts are executed via subprocess.run() with capture_output=True
      and a 30-second timeout to prevent hangs.
    - The osascript binary location is verified via shutil.which() before use.
"""

import subprocess
import sys
import shutil
import time
from typing import Optional


def is_available() -> bool:
    """Check if AppleScript is available (macOS with osascript)."""
    return sys.platform == 'darwin' and shutil.which('osascript') is not None


def _escape_for_applescript(s: str) -> str:
    """Escape a string for safe use in AppleScript.

    Backslashes must be escaped first, then quotes, to prevent
    injection attacks and handle edge cases like 'Playlist\\Test'.
    """
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _find_playlist_applescript(safe_name: str) -> str:
    """Generate AppleScript code to find a playlist by name.

    Tries exact match first, then falls back to partial match (contains).

    Args:
        safe_name: Already-escaped playlist name

    Returns:
        AppleScript code snippet that sets targetPlaylist variable
    """
    return f'''
        try
            -- Try exact match first
            set targetPlaylist to first user playlist whose name is "{safe_name}"
        on error
            try
                -- Fall back to partial match
                set targetPlaylist to first user playlist whose name contains "{safe_name}"
            on error
                return "ERROR:Playlist not found"
            end try
        end try'''


def run_applescript(script: str) -> tuple[bool, str]:
    """Execute AppleScript and return (success, output/error).

    Args:
        script: AppleScript code to execute

    Returns:
        Tuple of (success: bool, output: str)
        On success, output is the script's return value.
        On failure, output is the error message.
    """
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "AppleScript timed out after 30 seconds"
    except Exception as e:
        return False, str(e)


# =============================================================================
# Playback Control
# =============================================================================

def play() -> tuple[bool, str]:
    """Start or resume playback."""
    return run_applescript('tell application "Music" to play')


def pause() -> tuple[bool, str]:
    """Pause playback."""
    return run_applescript('tell application "Music" to pause')


def playpause() -> tuple[bool, str]:
    """Toggle play/pause."""
    return run_applescript('tell application "Music" to playpause')


def stop() -> tuple[bool, str]:
    """Stop playback."""
    return run_applescript('tell application "Music" to stop')


def next_track() -> tuple[bool, str]:
    """Skip to next track."""
    return run_applescript('tell application "Music" to next track')


def previous_track() -> tuple[bool, str]:
    """Go to previous track."""
    return run_applescript('tell application "Music" to previous track')


def get_player_state() -> tuple[bool, str]:
    """Get current player state (playing, paused, stopped)."""
    return run_applescript('tell application "Music" to get player state as string')


def get_current_track() -> tuple[bool, dict]:
    """Get info about currently playing track.

    Returns:
        Tuple of (success, track_info_dict or error_string)
    """
    script = '''
    tell application "Music"
        if player state is stopped then
            return "STOPPED"
        end if
        set t to current track
        set output to ""
        set output to output & "name:" & (name of t) & "\\n"
        set output to output & "artist:" & (artist of t) & "\\n"
        set output to output & "album:" & (album of t) & "\\n"
        set output to output & "duration:" & (duration of t) & "\\n"
        set output to output & "position:" & (player position) & "\\n"
        try
            set output to output & "genre:" & (genre of t) & "\\n"
        end try
        try
            set output to output & "year:" & (year of t) & "\\n"
        end try
        return output
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output
    if output == "STOPPED":
        return True, {"state": "stopped"}

    # Parse key:value pairs
    track_info = {"state": "playing"}
    for line in output.split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            track_info[key.strip()] = value.strip()
    return True, track_info


def get_volume() -> tuple[bool, int | str]:
    """Get current volume (0-100).

    Returns:
        Tuple of (success, volume 0-100 or error message string)
    """
    success, output = run_applescript('tell application "Music" to get sound volume')
    if success:
        try:
            return True, int(output)
        except ValueError:
            return False, f"Invalid volume value: {output}"
    return False, output


def set_volume(volume: int) -> tuple[bool, str]:
    """Set volume (0-100)."""
    volume = max(0, min(100, volume))
    return run_applescript(f'tell application "Music" to set sound volume to {volume}')


def get_shuffle() -> tuple[bool, bool | str]:
    """Get shuffle state.

    Returns:
        Tuple of (success, shuffle enabled bool or error message string)
    """
    success, output = run_applescript('tell application "Music" to get shuffle enabled')
    if success:
        return True, output.lower() == 'true'
    return False, output


def set_shuffle(enabled: bool) -> tuple[bool, str]:
    """Set shuffle on/off."""
    value = 'true' if enabled else 'false'
    return run_applescript(f'tell application "Music" to set shuffle enabled to {value}')


def get_repeat() -> tuple[bool, str]:
    """Get repeat mode (off, one, all)."""
    return run_applescript('tell application "Music" to get song repeat as string')


def set_repeat(mode: str) -> tuple[bool, str]:
    """Set repeat mode (off, one, all)."""
    if mode not in ('off', 'one', 'all'):
        return False, f"Invalid repeat mode: {mode}. Use 'off', 'one', or 'all'"
    return run_applescript(f'tell application "Music" to set song repeat to {mode}')


def seek(position: float) -> tuple[bool, str]:
    """Seek to position in seconds."""
    return run_applescript(f'tell application "Music" to set player position to {position}')


# =============================================================================
# Playlist Operations
# =============================================================================

def get_playlists() -> tuple[bool, list[dict]]:
    """Get all user playlists with details.

    Returns:
        Tuple of (success, list of playlist dicts or error string)
    """
    script = '''
    tell application "Music"
        set output to ""
        repeat with p in user playlists
            set pName to name of p
            set pId to persistent ID of p
            set pSmart to smart of p
            set pCount to count of tracks of p
            try
                set pTime to time of p
            on error
                set pTime to "0:00"
            end try
            set output to output & pName & "|||" & pId & "|||" & pSmart & "|||" & pCount & "|||" & pTime & "\\n"
        end repeat
        return output
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output

    playlists = []
    for line in output.split('\n'):
        if '|||' in line:
            parts = line.split('|||')
            if len(parts) >= 5:
                playlists.append({
                    'name': parts[0],
                    'id': parts[1],
                    'smart': parts[2].lower() == 'true',
                    'track_count': int(parts[3]) if parts[3].isdigit() else 0,
                    'duration': parts[4]
                })
    return True, playlists


def _get_playlist_tracks_bulk(safe_name: str, limit: int) -> tuple[bool, str]:
    """Try bulk property fetch for playlist tracks (fast path).

    Returns (success, output) where output is raw AppleScript output or error.
    """
    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_name)}

        set allTracks to tracks of targetPlaylist
        set trackCount to count of allTracks
        if trackCount is 0 then return ""

        -- Bulk fetch all properties at once (much faster than per-track)
        set allNames to name of allTracks
        set allArtists to artist of allTracks
        set allAlbums to album of allTracks
        set allDurations to duration of allTracks
        set allGenres to genre of allTracks
        set allYears to year of allTracks
        set allIds to persistent ID of allTracks

        -- Combine into output
        set output to ""
        set maxTracks to {limit}
        if trackCount < maxTracks then set maxTracks to trackCount
        repeat with i from 1 to maxTracks
            set tName to item i of allNames
            set tArtist to item i of allArtists
            set tAlbum to item i of allAlbums
            set tDuration to item i of allDurations
            set tGenre to item i of allGenres
            set tYear to item i of allYears
            set tId to item i of allIds
            set output to output & tName & "|||" & tArtist & "|||" & tAlbum & "|||" & tDuration & "|||" & tGenre & "|||" & tYear & "|||" & tId & "\\n"
        end repeat
        return output
    end tell
    '''
    return run_applescript(script)


def _get_playlist_tracks_slow(safe_name: str, limit: int) -> tuple[bool, str]:
    """Per-track iteration fallback for playlists with shared tracks (slow path).

    Optimized for shared tracks: skips genre/year (saves ~33% time).
    Returns (success, output) where output is raw AppleScript output or error.
    """
    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_name)}

        set allTracks to tracks of targetPlaylist
        set trackCount to count of allTracks
        if trackCount is 0 then return ""

        -- Per-track iteration (slower but handles shared tracks)
        -- Optimized: skip genre/year to reduce try/catch overhead
        set output to ""
        set maxTracks to {limit}
        if trackCount < maxTracks then set maxTracks to trackCount
        repeat with i from 1 to maxTracks
            set t to item i of allTracks
            try
                set tName to name of t
                set tArtist to artist of t
                set tAlbum to album of t
                set tDuration to duration of t
                set tId to persistent ID of t
                -- Skip genre/year for speed (shared tracks typically have them but try/catch is expensive)
                set output to output & tName & "|||" & tArtist & "|||" & tAlbum & "|||" & tDuration & "|||||||||" & tId & "\\n"
            on error
                -- Skip tracks that can't be read (extremely rare)
            end try
        end repeat
        return output
    end tell
    '''
    return run_applescript(script)


def get_playlist_tracks(playlist_name: str, limit: int = 500) -> tuple[bool, list[dict]]:
    """Get tracks in a playlist by name.

    Uses fast bulk fetch when possible, falls back to per-track iteration
    for playlists containing shared tracks (Apple Music subscription tracks).

    Args:
        playlist_name: Name of the playlist
        limit: Maximum number of tracks to return (default 500)

    Returns:
        Tuple of (success, list of track dicts or error string)
    """
    safe_name = _escape_for_applescript(playlist_name)

    # Try bulk fetch first (150x faster)
    success, output = _get_playlist_tracks_bulk(safe_name, limit)

    # If bulk fetch fails (e.g., shared tracks), fall back to per-track
    # Note: AppleScript uses straight apostrophe in "Can't get"
    if not success and "Can" in output and "get" in output:
        success, output = _get_playlist_tracks_slow(safe_name, limit)

    if not success:
        return False, output
    if output.startswith("ERROR:"):
        return False, output[6:]

    tracks = []
    for line in output.split('\n'):
        if '|||' in line:
            parts = line.split('|||')
            if len(parts) >= 7:
                # Format duration
                try:
                    dur_sec = float(parts[3])
                    minutes = int(dur_sec) // 60
                    seconds = int(dur_sec) % 60
                    duration = f"{minutes}:{seconds:02d}"
                except (ValueError, TypeError):
                    duration = ""

                tracks.append({
                    'name': parts[0],
                    'artist': parts[1],
                    'album': parts[2],
                    'duration': duration,
                    'genre': parts[4],
                    'year': parts[5],
                    'id': parts[6],
                })
    return True, tracks


def create_playlist(name: str, description: str = "") -> tuple[bool, str]:
    """Create a new playlist.

    Args:
        name: Playlist name
        description: Optional description

    Returns:
        Tuple of (success, playlist_id or error)
    """
    safe_name = _escape_for_applescript(name)
    safe_desc = _escape_for_applescript(description)

    if description:
        script = f'''
        tell application "Music"
            set newPlaylist to make new user playlist with properties {{name:"{safe_name}", description:"{safe_desc}"}}
            return persistent ID of newPlaylist
        end tell
        '''
    else:
        script = f'''
        tell application "Music"
            set newPlaylist to make new user playlist with properties {{name:"{safe_name}"}}
            return persistent ID of newPlaylist
        end tell
        '''
    return run_applescript(script)


def delete_playlist(playlist_name: str) -> tuple[bool, str]:
    """Delete a playlist by name.

    Args:
        playlist_name: Name of the playlist to delete

    Returns:
        Tuple of (success, message or error)
    """
    safe_name = _escape_for_applescript(playlist_name)
    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_name)}
        set playlistName to name of targetPlaylist
        delete targetPlaylist
        return "Deleted playlist: " & playlistName
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def rename_playlist(playlist_name: str, new_name: str) -> tuple[bool, str]:
    """Rename a playlist.

    Args:
        playlist_name: Current name of the playlist
        new_name: New name for the playlist

    Returns:
        Tuple of (success, message or error)
    """
    safe_old = _escape_for_applescript(playlist_name)
    safe_new = _escape_for_applescript(new_name)
    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_old)}
        set oldName to name of targetPlaylist
        set name of targetPlaylist to "{safe_new}"
        return "Renamed: " & oldName & " → {safe_new}"
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def track_exists_in_playlist(playlist_name: str, track_name: str, artist: Optional[str] = None) -> tuple[bool, bool | str]:
    """Quick check if a track exists in a playlist.

    Args:
        playlist_name: Playlist to check
        track_name: Track name to look for
        artist: Optional artist to match

    Returns:
        Tuple of (success, exists: bool | error: str)
        On success, second element is True/False for exists.
        On failure, second element is error message.
    """
    safe_playlist = _escape_for_applescript(playlist_name)
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_filter = f'whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_filter = f'whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_playlist)}
        set matchingTracks to (every track of targetPlaylist {track_filter})
        if (count of matchingTracks) > 0 then
            return "FOUND:" & name of (item 1 of matchingTracks) & " - " & artist of (item 1 of matchingTracks)
        else
            return "NOT_FOUND"
        end if
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output
    if output.startswith("ERROR:"):
        return False, output[6:]
    if output.startswith("FOUND:"):
        return True, output[6:]  # Return the matched track info
    return True, False  # NOT_FOUND


def add_track_to_playlist(playlist_name: str, track_name: str, artist: Optional[str] = None, album: Optional[str] = None) -> tuple[bool, str]:
    """Add a track from library to a playlist.

    Args:
        playlist_name: Target playlist name
        track_name: Name of the track to add (partial match supported)
        artist: Optional artist name to disambiguate (prefers exact match, falls back to contains)
        album: Optional album name to disambiguate (partial match supported)

    Returns:
        Tuple of (success, message or error)
    """
    safe_playlist = _escape_for_applescript(playlist_name)
    safe_track = _escape_for_applescript(track_name)

    # Build filter conditions
    conditions = [f'name contains "{safe_track}"']
    if artist:
        safe_artist = _escape_for_applescript(artist)
        conditions.append(f'artist is "{safe_artist}"')
    if album:
        safe_album = _escape_for_applescript(album)
        conditions.append(f'album contains "{safe_album}"')

    track_query = f'first track of library playlist 1 whose {" and ".join(conditions)}'

    # If artist provided, try exact match first, then fall back to contains
    if artist and not album:
        fallback_conditions = [f'name contains "{safe_track}"', f'artist contains "{safe_artist}"']
        fallback_query = f'first track of library playlist 1 whose {" and ".join(fallback_conditions)}'
    elif artist and album:
        fallback_conditions = [f'name contains "{safe_track}"', f'artist contains "{safe_artist}"', f'album contains "{safe_album}"']
        fallback_query = f'first track of library playlist 1 whose {" and ".join(fallback_conditions)}'
    else:
        fallback_query = None

    if fallback_query:
        script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_playlist)}
        try
            set targetTrack to {track_query}
        on error
            try
                set targetTrack to {fallback_query}
            on error
                return "ERROR:Track not found: {safe_track}"
            end try
        end try
        duplicate targetTrack to targetPlaylist
        return "Added " & name of targetTrack & " (" & album of targetTrack & ") by " & artist of targetTrack & " to " & name of targetPlaylist
    end tell
    '''
    else:
        script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_playlist)}
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        duplicate targetTrack to targetPlaylist
        return "Added " & name of targetTrack & " (" & album of targetTrack & ") by " & artist of targetTrack & " to " & name of targetPlaylist
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def remove_track_from_playlist(
    playlist_name: str,
    track_name: str = "",
    artist: Optional[str] = None,
    track_id: Optional[str] = None
) -> tuple[bool, str]:
    """Remove a track from a playlist (not from library).

    Args:
        playlist_name: Playlist to remove from
        track_name: Name of the track to remove (partial match supported)
        artist: Optional artist name to disambiguate (partial match)
        track_id: Optional persistent ID (exact match, overrides name/artist)

    Returns:
        Tuple of (success, message or error)
    """
    safe_playlist = _escape_for_applescript(playlist_name)

    # Build track filter
    if track_id:
        # Remove by ID (exact match)
        track_filter = f'whose persistent ID is "{track_id}"'
    elif track_name:
        # Remove by name (partial match)
        safe_track = _escape_for_applescript(track_name)
        if artist:
            safe_artist = _escape_for_applescript(artist)
            track_filter = f'whose name contains "{safe_track}" and artist contains "{safe_artist}"'
        else:
            track_filter = f'whose name contains "{safe_track}"'
    else:
        return False, "Must provide track_name or track_id"

    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_playlist)}
        try
            set targetTrack to (first track of targetPlaylist {track_filter})
        on error
            return "ERROR:Track not found in playlist"
        end try
        set trackName to name of targetTrack
        set trackArtist to artist of targetTrack
        delete targetTrack
        return "Removed " & trackName & " by " & trackArtist & " from {safe_playlist}"
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def remove_from_library(
    track_name: str = "",
    artist: Optional[str] = None,
    track_id: Optional[str] = None
) -> tuple[bool, str]:
    """Remove a track from the library entirely.

    Args:
        track_name: Name of the track to remove (partial match)
        artist: Optional artist name to disambiguate (partial match)
        track_id: Optional persistent ID (exact match, overrides name/artist)

    Returns:
        Tuple of (success, message or error)
    """
    # Build track filter
    if track_id:
        # Remove by ID (exact match)
        track_filter = f'whose persistent ID is "{track_id}"'
    elif track_name:
        # Remove by name (partial match)
        safe_track = _escape_for_applescript(track_name)
        if artist:
            safe_artist = _escape_for_applescript(artist)
            track_filter = f'whose name contains "{safe_track}" and artist contains "{safe_artist}"'
        else:
            track_filter = f'whose name contains "{safe_track}"'
    else:
        return False, "Must provide track_name or track_id"

    script = f'''
    tell application "Music"
        try
            set targetTrack to (first track of library playlist 1 {track_filter})
        on error
            return "ERROR:Track not found in library"
        end try
        set trackName to name of targetTrack
        set trackArtist to artist of targetTrack
        delete targetTrack
        return "Removed from library: " & trackName & " by " & trackArtist
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def search_playlist(playlist_name: str, query: str) -> tuple[bool, list[dict]]:
    """Search for tracks in a playlist using native AppleScript search.

    Uses Music app's native search (same as typing in search field).
    Much faster than manually iterating through all tracks.

    Args:
        playlist_name: Name of the playlist to search
        query: Search term (matches name, artist, album, etc.)

    Returns:
        Tuple of (success, list of matching tracks or error message)
    """
    safe_name = _escape_for_applescript(playlist_name)
    safe_query = _escape_for_applescript(query)

    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_name)}
        set foundTracks to search targetPlaylist for "{safe_query}"
        set output to ""
        repeat with t in foundTracks
            set trackName to name of t
            set trackArtist to artist of t
            set trackAlbum to album of t
            set trackId to persistent ID of t
            try
                set trackExplicit to explicit of t
            on error
                set trackExplicit to false
            end try
            set output to output & trackName & "|||" & trackArtist & "|||" & trackAlbum & "|||" & trackId & "|||" & trackExplicit & "\\n"
        end repeat
        return output
    end tell
    '''
    success, output = run_applescript(script)

    if not success:
        return False, output

    if output.startswith("ERROR:"):
        return False, output[6:]

    # Parse results
    tracks = []
    for line in output.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|||")
        if len(parts) >= 4:
            # Parse explicit field (added in 5th position)
            explicit = "Unknown"
            if len(parts) >= 5:
                explicit = "Yes" if parts[4].lower() == "true" else "No"

            tracks.append({
                "name": parts[0],
                "artist": parts[1],
                "album": parts[2],
                "id": parts[3],
                "explicit": explicit,
            })

    return True, tracks


def download_tracks(track_ids: str = "", playlist_name: str = "") -> tuple[bool, str]:
    """Download cloud tracks or playlist for offline playback.

    Args:
        track_ids: Comma-separated persistent IDs to download
        playlist_name: Name of playlist to download all tracks from

    Returns:
        Tuple of (success, message or error)
    """
    if track_ids and playlist_name:
        return False, "Error: Provide either track_ids or playlist_name, not both"
    if not track_ids and not playlist_name:
        return False, "Error: Provide track_ids or playlist_name"

    if playlist_name:
        # Download entire playlist
        safe_name = _escape_for_applescript(playlist_name)
        script = f'''
        tell application "Music"
{_find_playlist_applescript(safe_name)}
            download targetPlaylist
            return "Downloading playlist: " & name of targetPlaylist
        end tell
        '''
    else:
        # Download individual tracks by ID
        ids = [tid.strip() for tid in track_ids.split(",") if tid.strip()]
        if not ids:
            return False, "Error: No valid track IDs provided"

        # Build AppleScript to download each track
        download_cmds = []
        for track_id in ids:
            safe_id = _escape_for_applescript(track_id)
            download_cmds.append(f'download (first track of library playlist 1 whose persistent ID is "{safe_id}")')

        script = f'''
        tell application "Music"
            {chr(10).join(f"            {cmd}" for cmd in download_cmds)}
            return "Downloading {len(ids)} track(s)"
        end tell
        '''

    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def play_playlist(playlist_name: str, shuffle: bool = False) -> tuple[bool, str]:
    """Start playing a playlist.

    Args:
        playlist_name: Name of the playlist to play
        shuffle: Whether to shuffle the playlist

    Returns:
        Tuple of (success, message or error)
    """
    safe_name = _escape_for_applescript(playlist_name)
    shuffle_cmd = "set shuffle enabled to true" if shuffle else "set shuffle enabled to false"

    script = f'''
    tell application "Music"
{_find_playlist_applescript(safe_name)}
        {shuffle_cmd}
        play targetPlaylist
        return "Now playing: " & name of targetPlaylist
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def play_track(track_name: str, artist: Optional[str] = None) -> tuple[bool, str]:
    """Play a specific track from library.

    Args:
        track_name: Name of the track to play
        artist: Optional artist name to disambiguate

    Returns:
        Tuple of (success, message or error)
    """
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        play targetTrack
        return "Now playing: " & name of targetTrack & " by " & artist of targetTrack
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def open_catalog_song(song_url: str) -> tuple[bool, str]:
    """Open a catalog song in the Music app (user must click play).

    Note: macOS cannot programmatically play catalog songs not in library.
    This function reveals the song in Music for manual playback.

    Args:
        song_url: The song URL from Apple Music API (https://music.apple.com/...)

    Returns:
        Tuple of (success, message or error)
    """
    import subprocess

    # Validate URL format - must be an Apple Music URL
    if not song_url or not isinstance(song_url, str):
        return False, "Invalid URL: empty or not a string"

    # Normalize the URL - handle both https:// and music:// schemes
    if song_url.startswith("music://"):
        music_url = song_url
        https_url = song_url.replace("music://", "https://")
    elif song_url.startswith("https://music.apple.com"):
        https_url = song_url
        music_url = song_url.replace("https://", "music://")
    elif song_url.startswith("https://"):
        # Non-Apple Music https URL - reject it
        return False, f"Not an Apple Music URL: {song_url}"
    else:
        # Assume it might be a bare URL without scheme
        return False, f"Invalid URL format: {song_url}"

    # Try music:// scheme first - opens directly in Music app
    try:
        subprocess.run(["open", music_url], check=True, capture_output=True)
        return True, "Opened in Music"
    except subprocess.CalledProcessError:
        pass

    # Fallback: https:// opens in browser which redirects to Music
    try:
        subprocess.run(["open", https_url], check=True, capture_output=True)
        return True, "Opened via browser"
    except subprocess.CalledProcessError:
        return False, f"Failed to open: {song_url}"


# =============================================================================
# UI Path Constants
# =============================================================================
# Centralized UI element paths for Music.app System Events automation.
# These paths are used by multiple functions to interact with the Music UI.

_SCROLL_AREA = 'scroll area 2 of splitter group 1 of window "Music"'

_SEARCH_FIELD = (
    'text field 1 of UI element 1 of row 1 of outline 1'
    ' of scroll area 1 of splitter group 1 of window "Music"'
)


def _check_playing() -> bool:
    """Check if Music is currently playing."""
    ok, state = run_applescript('tell application "Music" to get player state')
    return ok and state.strip() == "playing"


def _click_play_or_shuffle(shuffle: bool = False) -> tuple[bool, str]:
    """Find and click the Play or Shuffle button across different Music.app page layouts.

    Tries multiple known UI paths since albums, editorial playlists, and personal
    playlists each have different accessibility hierarchies.

    Args:
        shuffle: If True, click Shuffle instead of Play

    Returns:
        Tuple of (success, message or error)
    """
    button_name = "Shuffle" if shuffle else "Play"
    base = 'tell application "System Events" to tell process "Music"'
    sa = _SCROLL_AREA

    # Path 1: Album / editorial playlist layout (nested lists)
    script1 = f'{base} to click button "{button_name}" of UI element 1 of list 1 of list 1 of {sa}'
    # Path 2: Personal playlist layout (playlist header group)
    script2 = f'{base} to click button "{button_name}" of group 1 of {sa}'

    for script in [script1, script2]:
        ok, _ = run_applescript(script)
        if ok:
            time.sleep(1)
            if _check_playing():
                mode = "shuffling" if shuffle else "playing"
                return True, f"Playing ({mode} via UI click)"

    return False, f"Could not find {button_name} button"


def _ensure_music_frontmost() -> None:
    """Bring Music.app to the foreground with a visible window.

    Music.app can be running without a window (e.g. after closing the window
    or via background playback). This ensures the main window is open via
    the Window menu if no window is found.
    """
    run_applescript('''
tell application "Music" to activate
delay 0.5
tell application "System Events"
    tell process "Music"
        set frontmost to true
        delay 0.3
        if (count of windows) is 0 then
            try
                click menu item "Music" of menu "Window" of menu bar 1
            end try
            delay 1
            if (count of windows) is 0 then
                keystroke "1" using command down
                delay 1
            end if
        end if
    end tell
end tell''')


def _jxa_mouse_move(x: float, y: float) -> bool:
    """Move the mouse cursor via CoreGraphics (JXA). Triggers hover effects.

    Uses osascript -l JavaScript to call CoreGraphics CGEventCreateMouseEvent,
    which generates real mouse events that trigger hover-dependent UI elements
    in Music.app (like the per-track play checkbox).

    Returns True if the command succeeded.
    """
    script = f'''ObjC.import("CoreGraphics");
var p = $.CGPointMake({x}, {y});
var e = $.CGEventCreateMouseEvent($(), $.kCGEventMouseMoved, p, 0);
$.CGEventPost($.kCGHIDEventTap, e);
delay(0.5);
"ok"'''
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def _jxa_scroll_down(x: float, y: float, amount: int = 10) -> bool:
    """Scroll down at coordinates via CoreGraphics (JXA).

    Moves mouse to position first, then sends scroll wheel events.
    Used to bring off-screen track rows into view.

    Returns True if the command succeeded.
    """
    script = f'''ObjC.import("CoreGraphics");
var p = $.CGPointMake({x}, {y});
var m = $.CGEventCreateMouseEvent($(), $.kCGEventMouseMoved, p, 0);
$.CGEventPost($.kCGHIDEventTap, m);
delay(0.3);
for (var i = 0; i < {amount}; i++) {{
    var s = $.CGEventCreateScrollWheelEvent($(), 0, 1, -3);
    $.CGEventPost($.kCGHIDEventTap, s);
    delay(0.1);
}}
delay(0.5);
"ok"'''
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def _find_highlighted_track_position() -> Optional[tuple[float, float, str]]:
    """Find the track row highlighted by ?i= parameter (has Favorite button).

    Searches all track groups across disc sections for a group containing
    a button with description "Favorite" — Music.app adds this to the
    track highlighted by the ?i= URL parameter.

    Returns (center_x, center_y, track_name) or None if not found.
    """
    ok, result = run_applescript('''
tell application "System Events"
    tell process "Music"
        set sg to splitter group 1 of window "Music"
        set sa to scroll area 2 of sg
        repeat with subList in (every list of list 1 of sa)
            repeat with g in (every group of subList)
                try
                    repeat with b in (every button of g)
                        if description of b is "Favorite" then
                            set {x, y} to position of g
                            set {w, h} to size of g
                            set cx to (x + w / 2)
                            set cy to (y + h / 2)
                            return (cx as text) & "," & (cy as text) & "," & description of g
                        end if
                    end repeat
                end try
            end repeat
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    if not ok or not result or result.strip() == "NOT_FOUND":
        return None
    parts = result.strip().split(",", 2)
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), parts[2]
    except ValueError:
        return None


def _get_window_bottom() -> Optional[float]:
    """Get the bottom y-coordinate of the Music window."""
    ok, result = run_applescript('''
tell application "System Events"
    tell process "Music"
        set {wx, wy} to position of window "Music"
        set {ww, wh} to size of window "Music"
        return ((wy + wh) as text)
    end tell
end tell''')
    if ok and result:
        try:
            return float(result.strip())
        except ValueError:
            pass
    return None


def _play_specific_track() -> tuple[bool, str]:
    """Play the track highlighted by ?i= URL parameter via hover + click.

    Finds the highlighted track row (marked with Favorite button), scrolls
    it into view if off-screen, hovers via CoreGraphics to reveal the
    per-track play checkbox, and clicks it.

    Requires Accessibility permissions for System Events.

    Returns:
        Tuple of (success, message or error)
    """
    # Ensure Music is frontmost before any CoreGraphics interaction
    _ensure_music_frontmost()

    # Find the highlighted track
    pos = _find_highlighted_track_position()
    if pos is None:
        return False, "Could not find highlighted track row"
    cx, cy, track_name = pos

    # Scroll into view if off-screen
    win_bottom = _get_window_bottom()
    if win_bottom and cy > win_bottom - 30:
        _ensure_music_frontmost()
        _jxa_scroll_down(cx, win_bottom - 200, amount=10)
        # Re-find position after scroll
        pos = _find_highlighted_track_position()
        if pos is None:
            return False, "Lost track row after scrolling"
        cx, cy, track_name = pos

    # Ensure Music is still frontmost (user may have clicked away during scroll)
    _ensure_music_frontmost()

    # Hover to reveal the play checkbox
    if not _jxa_mouse_move(cx, cy):
        return False, "Failed to move mouse for hover"
    time.sleep(0.5)

    # Click the play checkbox that appears on hover
    ok, result = run_applescript('''
tell application "System Events"
    tell process "Music"
        set sg to splitter group 1 of window "Music"
        set sa to scroll area 2 of sg
        repeat with subList in (every list of list 1 of sa)
            repeat with g in (every group of subList)
                try
                    repeat with b in (every button of g)
                        if description of b is "Favorite" then
                            click checkbox 1 of g
                            return description of g
                        end if
                    end repeat
                end try
            end repeat
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    if ok and result and result.strip() != "NOT_FOUND":
        time.sleep(1)
        if _check_playing():
            return True, f"Playing: {result.strip()}"

    return False, f"Hover+click attempted on {track_name} but playback did not start"


def open_catalog_and_play(url: str, shuffle: bool = False, timeout: float = 15.0) -> tuple[bool, str]:
    """Open an Apple Music URL and attempt to start playback via UI scripting.

    Supports albums, playlists (editorial and personal), and specific tracks
    via ?i= parameter. Uses multiple UI automation strategies depending on
    the URL type and page layout.

    For albums/playlists: clicks the Play or Shuffle button.
    For ?i= song URLs: hovers over the highlighted track row via CoreGraphics
    to reveal the per-track play checkbox, then clicks it.

    Uses adaptive polling — checks every second and attempts playback as soon
    as the page loads, rather than waiting fixed delays. Fast networks get
    fast response, slow networks get up to `timeout` seconds.

    Requires Accessibility permissions for System Events.
    Song URLs (/song/name/id) are not supported via deep link — the server
    layer converts them to album URLs with ?i= via API when available.

    Args:
        url: Apple Music URL (https://music.apple.com/... or music://...)
        shuffle: If True, click Shuffle instead of Play (albums/playlists only)
        timeout: Maximum seconds to wait for content to load (default 15)

    Returns:
        Tuple of (success, message or error)
    """
    # Reject /song/ URLs — they show "Something went wrong" in Music.app
    url_stripped = url.strip()
    if "/song/" in url_stripped and "?i=" not in url_stripped:
        return False, (
            "Song URLs (/song/id) are not supported by Music.app via deep link. "
            "Use an album URL with ?i= parameter instead: "
            "/album/name/albumId?i=songId"
        )

    # Detect if this is a specific track request
    has_track_param = "?i=" in url_stripped

    # Reuse existing URL opening logic
    open_ok, open_msg = open_catalog_song(url)
    if not open_ok:
        return False, open_msg

    # Adaptive polling: check frequently, attempt playback as soon as page loads
    # Initial wait for Music.app to start loading content
    time.sleep(2)
    deadline = time.time() + timeout
    attempt = 0

    while time.time() < deadline:
        # Check if Music already started playing on its own
        if _check_playing():
            return True, "Playing (auto-started after opening URL)"

        if has_track_param:
            ok, msg = _play_specific_track()
            if ok:
                return True, msg
        else:
            ok, msg = _click_play_or_shuffle(shuffle)
            if ok:
                return True, msg

        # Wait before retrying — longer gaps on early attempts (content loading),
        # shorter gaps on later attempts (UI may just need another try)
        attempt += 1
        wait = 2.0 if attempt <= 2 else 1.0
        time.sleep(wait)

    if has_track_param:
        return True, "Opened URL in Music. Could not auto-play the specific track — try clicking it manually."
    return True, "Opened URL in Music. Auto-play attempted but could not confirm playback started — may need Accessibility permissions for System Events."


# =============================================================================
# Library Search
# =============================================================================

def get_library_songs(limit: int = 100) -> tuple[bool, list[dict]]:
    """Get songs from the library (no search query required).

    Args:
        limit: Maximum number of songs to return (default 100, 0 for all)

    Returns:
        Tuple of (success, list of track dicts or error)

    Note: Large libraries (10,000+ tracks) with limit=0 may timeout (30s).
    """
    if limit < 0:
        return False, "limit must be >= 0 (use 0 for all songs)"
    limit_clause = f"if resultCount >= {limit} then exit repeat" if limit > 0 else ""

    script = f'''
    tell application "Music"
        set output to ""
        set resultCount to 0
        repeat with t in tracks of library playlist 1
            {limit_clause}
            set tName to name of t
            set tArtist to artist of t
            set tAlbum to album of t
            set tDuration to duration of t
            set tId to persistent ID of t
            try
                set tGenre to genre of t
            on error
                set tGenre to ""
            end try
            try
                set tYear to year of t as string
            on error
                set tYear to ""
            end try
            try
                set tExplicit to explicit of t
            on error
                set tExplicit to false
            end try
            set output to output & tName & "|||" & tArtist & "|||" & tAlbum & "|||" & tDuration & "|||" & tGenre & "|||" & tYear & "|||" & tId & "|||" & tExplicit & "\\n"
            set resultCount to resultCount + 1
        end repeat
        return output
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output

    tracks = []
    for line in output.split('\n'):
        if '|||' in line:
            parts = line.split('|||')
            if len(parts) >= 7:
                try:
                    dur_sec = float(parts[3])
                    minutes = int(dur_sec) // 60
                    seconds = int(dur_sec) % 60
                    duration = f"{minutes}:{seconds:02d}"
                except (ValueError, TypeError):
                    duration = ""

                # Parse explicit field (added in 8th position)
                explicit = "Unknown"
                if len(parts) >= 8:
                    explicit = "Yes" if parts[7].lower() == "true" else "No"

                tracks.append({
                    'name': parts[0],
                    'artist': parts[1],
                    'album': parts[2],
                    'duration': duration,
                    'genre': parts[4],
                    'year': parts[5],
                    'id': parts[6],
                    'explicit': explicit,
                })
    return True, tracks


def search_library(query: str, types: str = "all") -> tuple[bool, list[dict]]:
    """Search the local library.

    Args:
        query: Search query
        types: Type of search - "all", "artists", "albums", "songs"

    Returns:
        Tuple of (success, list of track dicts or error)
    """
    safe_query = _escape_for_applescript(query)

    # Map search types to AppleScript search kinds
    search_map = {
        "all": "",
        "artists": "only artists",
        "albums": "only albums",
        "songs": "only songs"
    }
    search_modifier = search_map.get(types, "")

    script = f'''
    tell application "Music"
        set searchResults to search library playlist 1 for "{safe_query}" {search_modifier}
        set output to ""
        set maxResults to 100
        set resultCount to 0
        repeat with t in searchResults
            if resultCount >= maxResults then exit repeat
            set tName to name of t
            set tArtist to artist of t
            set tAlbum to album of t
            set tDuration to duration of t
            set tId to persistent ID of t
            try
                set tGenre to genre of t
            on error
                set tGenre to ""
            end try
            try
                set tYear to year of t as string
            on error
                set tYear to ""
            end try
            try
                set tExplicit to explicit of t
            on error
                set tExplicit to false
            end try
            set output to output & tName & "|||" & tArtist & "|||" & tAlbum & "|||" & tDuration & "|||" & tGenre & "|||" & tYear & "|||" & tId & "|||" & tExplicit & "\\n"
            set resultCount to resultCount + 1
        end repeat
        return output
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output

    tracks = []
    for line in output.split('\n'):
        if '|||' in line:
            parts = line.split('|||')
            if len(parts) >= 7:
                try:
                    dur_sec = float(parts[3])
                    minutes = int(dur_sec) // 60
                    seconds = int(dur_sec) % 60
                    duration = f"{minutes}:{seconds:02d}"
                except (ValueError, TypeError):
                    duration = ""

                # Parse explicit field (added in 8th position)
                explicit = "Unknown"
                if len(parts) >= 8:
                    explicit = "Yes" if parts[7].lower() == "true" else "No"

                tracks.append({
                    'name': parts[0],
                    'artist': parts[1],
                    'album': parts[2],
                    'duration': duration,
                    'genre': parts[4],
                    'year': parts[5],
                    'id': parts[6],
                    'explicit': explicit,
                })
    return True, tracks


# =============================================================================
# Track Metadata
# =============================================================================

def love_track(track_name: str, artist: Optional[str] = None) -> tuple[bool, str]:
    """Mark a track as loved.

    Args:
        track_name: Name of the track (partial match supported)
        artist: Optional artist name to disambiguate (partial match supported)

    Returns:
        Tuple of (success, message or error)
    """
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        set loved of targetTrack to true
        set disliked of targetTrack to false
        return "Loved: " & name of targetTrack
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def dislike_track(track_name: str, artist: Optional[str] = None) -> tuple[bool, str]:
    """Mark a track as disliked.

    Args:
        track_name: Name of the track (partial match supported)
        artist: Optional artist name to disambiguate (partial match supported)

    Returns:
        Tuple of (success, message or error)
    """
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        set disliked of targetTrack to true
        set loved of targetTrack to false
        return "Disliked: " & name of targetTrack
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def get_rating(track_name: str, artist: Optional[str] = None) -> tuple[bool, int | str]:
    """Get track rating (0-100, where 20=1 star, 40=2 stars, etc).

    Args:
        track_name: Name of the track (partial match supported)
        artist: Optional artist name to disambiguate (partial match supported)

    Returns:
        Tuple of (success, rating 0-100 or error message string)
    """
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        return rating of targetTrack as integer
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    try:
        return True, int(output)
    except (ValueError, TypeError):
        return False, f"Invalid rating value: {output}"


def set_rating(track_name: str, rating: int, artist: Optional[str] = None) -> tuple[bool, str]:
    """Set track rating (0-100, where 20=1 star, 40=2 stars, etc).

    Args:
        track_name: Name of the track (partial match supported)
        rating: Rating value 0-100
        artist: Optional artist name to disambiguate (partial match supported)

    Returns:
        Tuple of (success, message or error)
    """
    safe_track = _escape_for_applescript(track_name)
    rating = max(0, min(100, rating))

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        set rating of targetTrack to {rating}
        return "Set rating to {rating} for: " & name of targetTrack
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


# =============================================================================
# AirPlay
# =============================================================================

def get_airplay_devices() -> tuple[bool, list[str]]:
    """Get list of available AirPlay devices."""
    script = '''
    tell application "Music"
        set deviceNames to name of every AirPlay device
        set output to ""
        repeat with d in deviceNames
            set output to output & d & "\\n"
        end repeat
        return output
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output

    devices = [d.strip() for d in output.split('\n') if d.strip()]
    return True, devices


def set_airplay_device(device_name: str) -> tuple[bool, str]:
    """Switch audio output to a specific AirPlay device.

    Args:
        device_name: Name of the AirPlay device (or partial match)

    Returns:
        Tuple of (success, message or error)
    """
    safe_name = _escape_for_applescript(device_name)

    script = f'''
    tell application "Music"
        try
            set targetDevice to first AirPlay device whose name contains "{safe_name}"
        on error
            return "ERROR:Device not found: {safe_name}"
        end try
        set current AirPlay devices to {{targetDevice}}
        return "Switched to: " & name of targetDevice
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


# =============================================================================
# Utilities
# =============================================================================

def reveal_track(track_name: str, artist: Optional[str] = None) -> tuple[bool, str]:
    """Reveal a track in the Music app window.

    Args:
        track_name: Name of the track
        artist: Optional artist name to disambiguate

    Returns:
        Tuple of (success, message or error)
    """
    safe_track = _escape_for_applescript(track_name)

    if artist:
        safe_artist = _escape_for_applescript(artist)
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}" and artist contains "{safe_artist}"'
    else:
        track_query = f'first track of library playlist 1 whose name contains "{safe_track}"'

    script = f'''
    tell application "Music"
        try
            set targetTrack to {track_query}
        on error
            return "ERROR:Track not found: {safe_track}"
        end try
        reveal targetTrack
        activate
        return "Revealed: " & name of targetTrack
    end tell
    '''
    success, output = run_applescript(script)
    if output.startswith("ERROR:"):
        return False, output[6:]
    return success, output


def get_library_stats() -> tuple[bool, dict]:
    """Get library statistics."""
    script = '''
    tell application "Music"
        set trackCount to count of tracks of library playlist 1
        set playlistCount to count of user playlists
        set playerState to player state as string
        set shuffleState to shuffle enabled
        set repeatState to song repeat as string
        set vol to sound volume

        return trackCount & "|||" & playlistCount & "|||" & playerState & "|||" & shuffleState & "|||" & repeatState & "|||" & vol
    end tell
    '''
    success, output = run_applescript(script)
    if not success:
        return False, output

    parts = output.split('|||')
    if len(parts) >= 6:
        return True, {
            'track_count': int(parts[0]) if parts[0].isdigit() else 0,
            'playlist_count': int(parts[1]) if parts[1].isdigit() else 0,
            'player_state': parts[2],
            'shuffle': parts[3].lower() == 'true',
            'repeat': parts[4],
            'volume': int(parts[5]) if parts[5].isdigit() else 0
        }
    return False, "Failed to parse library stats"


# =============================================================================
# UI Catalog Automation (no API required)
# =============================================================================
# These functions control Music.app through its UI (System Events + CoreGraphics)
# to provide catalog search, add-to-library, and play functionality without
# needing an Apple Developer account or API token.
#
# Requirements: macOS, Accessibility permissions for System Events,
# Music.app visible (not minimized), display attached (not headless).


def ui_search_catalog(query: str) -> tuple[bool, list[dict]]:
    """Search the Apple Music catalog via Music.app's search field.

    Types the query into the search field, submits it, and parses
    the "Top Results" section from the results page.

    Args:
        query: Search query (e.g. "Radiohead Creep", "Taylor Swift")

    Returns:
        Tuple of (success, list of result dicts).
        Each result dict has: name, type ("Song", "Album", "Artist", etc.),
        artist (if applicable), and index (position in results).
    """
    if not query or not query.strip():
        return False, []

    # Focus and populate the search field
    # Ensure Music has a visible window before interacting with search
    _ensure_music_frontmost()

    ok, _ = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set searchField to {_SEARCH_FIELD}
        set focused of searchField to true
        delay 0.3
        set value of searchField to "{_escape_for_applescript(query)}"
        delay 0.5
        key code 36
    end tell
end tell''')
    if not ok:
        return False, []

    # Wait for results to load
    time.sleep(4)

    # Parse the Top Results section
    ok, raw = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set sa to {_SCROLL_AREA}
        set resultList to list 1 of sa
        try
            set topResults to list 1 of resultList
        on error
            return "NO_RESULTS"
        end try
        set ec to every UI element of topResults
        set r to ""
        set idx to 0
        repeat with e in ec
            try
                set c to class of e as text
                if c is "UI element" then
                    set d to description of e
                    if d is not "Top Results" and d is not "group" then
                        set idx to idx + 1
                        -- Get the type line (second static text, e.g. "Song · Radiohead")
                        set typeLine to ""
                        set stTexts to every static text of e
                        if (count of stTexts) > 1 then
                            set typeLine to name of item 2 of stTexts
                        end if
                        set r to r & idx & "|||" & d & "|||" & typeLine & return
                    end if
                end if
            end try
        end repeat
        return r
    end tell
end tell''')
    if not ok or not raw or raw.strip() == "NO_RESULTS":
        return False, []

    results = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or "|||" not in line:
            continue
        parts = line.split("|||")
        if len(parts) >= 3:
            name = parts[1].strip()
            type_line = parts[2].strip()
            # Parse "Song · Radiohead" or "Album · Radiohead" etc.
            # Apple uses U+2004 (three-per-em space) + U+00B7 (middle dot) + U+2004
            result_type = ""
            artist = ""
            for sep in ["\u2004\u00b7\u2004", " \u00b7 ", " · "]:
                if sep in type_line:
                    result_type, artist = type_line.split(sep, 1)
                    break
            else:
                result_type = type_line
            results.append({
                "name": name,
                "type": result_type.strip(),
                "artist": artist.strip(),
                "index": int(parts[0]),
            })

    return True, results


def ui_clear_search() -> None:
    """Clear the Music.app search field and dismiss search."""
    run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set searchField to {_SEARCH_FIELD}
        set focused of searchField to true
        delay 0.2
        set value of searchField to ""
        delay 0.2
        key code 53
    end tell
end tell''')


def ui_add_to_library(result_name: str) -> tuple[bool, str]:
    """Add a catalog item to library via Music.app UI.

    Must be called after ui_search_catalog() with results visible.
    Hovers over the result to reveal the "Add to Library" button and clicks it.

    Args:
        result_name: Exact name of the result to add (as returned by ui_search_catalog)

    Returns:
        Tuple of (success, message)
    """
    safe_name = _escape_for_applescript(result_name)

    # Find the result's position
    ok, pos_str = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set sa to {_SCROLL_AREA}
        set resultList to list 1 of sa
        try
            set topResults to list 1 of resultList
        on error
            return "NO_RESULTS"
        end try
        repeat with e in (every UI element of topResults)
            try
                if description of e is "{safe_name}" then
                    set {{x, y}} to position of e
                    set {{w, h}} to size of e
                    return ((x + w / 2) as text) & "," & ((y + h / 2) as text)
                end if
            end try
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    if not ok or not pos_str or pos_str.strip() in ("NOT_FOUND", "NO_RESULTS"):
        return False, f"Could not find '{result_name}' in search results"

    try:
        cx, cy = [float(v) for v in pos_str.strip().split(",")]
    except ValueError:
        return False, f"Invalid position: {pos_str}"

    # Hover to reveal the Add to Library button
    _ensure_music_frontmost()
    if not _jxa_mouse_move(cx, cy):
        return False, "Failed to move mouse for hover"
    time.sleep(1)

    # Click the Add to Library button
    ok, click_result = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set sa to {_SCROLL_AREA}
        set resultList to list 1 of sa
        try
            set topResults to list 1 of resultList
        on error
            return "NO_RESULTS"
        end try
        repeat with e in (every UI element of topResults)
            try
                if description of e is "{safe_name}" then
                    -- Look for Add to Library button
                    repeat with btn in (every button of e)
                        if description of btn is "Add to Library" then
                            click btn
                            return "ADDED"
                        end if
                    end repeat
                    return "NO_ADD_BUTTON"
                end if
            end try
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    if ok and click_result and click_result.strip() == "ADDED":
        return True, f"Added '{result_name}' to library"

    if click_result and "NO_ADD_BUTTON" in click_result:
        return False, f"No 'Add to Library' button found — may already be in library, or hover didn't reveal it"

    return False, f"Failed to add: {click_result}"


def ui_play_result(result_name: str) -> tuple[bool, str]:
    """Play a catalog item from search results via Music.app UI.

    Must be called after ui_search_catalog() with results visible.
    Hovers over the result to reveal the play checkbox and clicks it.

    Args:
        result_name: Exact name of the result to play (as returned by ui_search_catalog)

    Returns:
        Tuple of (success, message)
    """
    safe_name = _escape_for_applescript(result_name)

    # Find position
    ok, pos_str = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set sa to {_SCROLL_AREA}
        set resultList to list 1 of sa
        try
            set topResults to list 1 of resultList
        on error
            return "NOT_FOUND"
        end try
        repeat with e in (every UI element of topResults)
            try
                if description of e is "{safe_name}" then
                    set {{x, y}} to position of e
                    set {{w, h}} to size of e
                    return ((x + w / 2) as text) & "," & ((y + h / 2) as text)
                end if
            end try
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    if not ok or not pos_str or pos_str.strip() == "NOT_FOUND":
        return False, f"Could not find '{result_name}' in search results"

    try:
        cx, cy = [float(v) for v in pos_str.strip().split(",")]
    except ValueError:
        return False, f"Invalid position: {pos_str}"

    # Hover to reveal play checkbox
    _ensure_music_frontmost()
    if not _jxa_mouse_move(cx, cy):
        return False, "Failed to hover"
    time.sleep(1)

    # Click the play checkbox
    ok, _ = run_applescript(f'''
tell application "System Events"
    tell process "Music"
        set sa to {_SCROLL_AREA}
        set resultList to list 1 of sa
        try
            set topResults to list 1 of resultList
        on error
            return "NOT_FOUND"
        end try
        repeat with e in (every UI element of topResults)
            try
                if description of e is "{safe_name}" then
                    click checkbox 1 of e
                    return "CLICKED"
                end if
            end try
        end repeat
        return "NOT_FOUND"
    end tell
end tell''')
    time.sleep(2)
    if _check_playing():
        return True, f"Playing: {result_name}"
    return False, f"Clicked play on '{result_name}' but playback didn't start"


def ui_play_result_by_query(query: str) -> tuple[bool, str]:
    """Search catalog via UI and play the first song result.

    Convenience function that combines ui_search_catalog + ui_play_result.

    Args:
        query: Search query (e.g. "Radiohead Creep")

    Returns:
        Tuple of (success, message)
    """
    ok, results = ui_search_catalog(query)
    if not ok or not results:
        ui_clear_search()
        return False, f"No results found for '{query}'"

    # Find first song result
    target = None
    for r in results:
        if r["type"] == "Song":
            target = r
            break
    if target is None:
        target = results[0]

    ok, msg = ui_play_result(target["name"])
    ui_clear_search()
    return ok, msg


def ui_add_to_playlist(playlist_name: str, query: str, artist: str = "") -> tuple[bool, str]:
    """Add a catalog track to a playlist via UI automation (no API required).

    Composite flow:
    1. Search catalog via Music.app UI
    2. Add the best matching song to library via hover+click
    3. Wait for iCloud sync
    4. Add to playlist via existing AppleScript backend

    Args:
        playlist_name: Target playlist name
        query: Search query (e.g. "Artist Song")
        artist: Optional artist filter for result matching

    Returns:
        Tuple of (success, message)
    """
    # Search
    ok, results = ui_search_catalog(query)
    if not ok or not results:
        ui_clear_search()
        return False, f"No results found for '{query}'"

    # Find best song result
    target = None
    for r in results:
        if r["type"] == "Song":
            if artist and artist.lower() not in r.get("artist", "").lower():
                continue
            target = r
            break

    if target is None:
        # Fall back to first result if no Song type match
        target = results[0]

    # Add to library
    ok, msg = ui_add_to_library(target["name"])
    if not ok:
        ui_clear_search()
        return False, f"Failed to add to library: {msg}"

    ui_clear_search()

    # Wait for iCloud sync
    track_name = target["name"]
    track_artist = target.get("artist", artist)
    time.sleep(8)

    # Verify it's in library
    for attempt in range(3):
        ok, lib_results = search_library(track_name.replace("\u0301", ""), "songs")
        if ok and lib_results:
            break
        time.sleep(3)
    else:
        return False, f"Added to library but sync not confirmed for '{track_name}'"

    # Add to playlist via existing backend
    ok, result = add_track_to_playlist(playlist_name, track_name, track_artist)
    if ok:
        return True, f"Added {track_name} by {track_artist} to {playlist_name}"
    return False, f"Added to library but failed to add to playlist: {result}"


# =============================================================================
# Library Snapshot & Diff
# =============================================================================

def library_snapshot() -> tuple[bool, dict]:
    """Capture a full snapshot of the Music library for integrity checking.

    Returns a dict with:
        - track_count: total library tracks
        - playback: dict with player state, volume, shuffle, repeat, current track
        - playlists: dict mapping playlist name -> list of {name, artist, album}

    This is intentionally thorough (captures full track lists) so diffs can
    detect any accidental additions, removals, or reorders.
    """
    # Get total track count
    ok, count_str = run_applescript(
        'tell application "Music" to return (count of tracks of library playlist 1) as text'
    )
    if not ok:
        return False, {"error": f"Failed to count tracks: {count_str}"}
    try:
        track_count = int(count_str.strip())
    except ValueError:
        return False, {"error": f"Invalid track count: {count_str}"}

    # Get playback state
    ok, pb_str = run_applescript('''
tell application "Music"
    set ps to player state as text
    set v to (sound volume) as text
    set sh to (shuffle enabled) as text
    set rp to song repeat as text
    set ct to ""
    set ca to ""
    set calb to ""
    try
        set ct to name of current track
        set ca to artist of current track
        set calb to album of current track
    end try
    return ps & return & v & return & sh & return & rp & return & ct & return & ca & return & calb
end tell''')
    playback_state = {}
    if ok and pb_str:
        lines = pb_str.strip().split("\n")
        playback_state = {
            "player_state": lines[0] if len(lines) > 0 else "unknown",
            "volume": int(lines[1]) if len(lines) > 1 and lines[1].strip().isdigit() else 0,
            "shuffle": lines[2].strip() == "true" if len(lines) > 2 else False,
            "repeat": lines[3].strip() if len(lines) > 3 else "unknown",
            "current_track": lines[4].strip() if len(lines) > 4 and lines[4].strip() else None,
            "current_artist": lines[5].strip() if len(lines) > 5 and lines[5].strip() else None,
            "current_album": lines[6].strip() if len(lines) > 6 and lines[6].strip() else None,
        }

    # Get all user playlists and their contents
    ok, playlist_data = run_applescript('''
tell application "Music"
    set r to ""
    repeat with p in user playlists
        set pName to name of p
        set pKind to smart of p
        if pKind is false and pName is not "Music" and pName is not "Music Videos" then
            set r to r & "PLAYLIST:" & pName & return
            try
                repeat with t in tracks of p
                    set r to r & name of t & "|||" & artist of t & "|||" & album of t & return
                end repeat
            end try
        end if
    end repeat
    return r
end tell''')
    if not ok:
        return False, {"error": f"Failed to get playlists: {playlist_data}"}

    playlists = {}
    current_playlist = None
    for line in playlist_data.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("PLAYLIST:"):
            current_playlist = line[9:]
            playlists[current_playlist] = []
        elif current_playlist is not None and "|||" in line:
            parts = line.split("|||")
            if len(parts) >= 3:
                playlists[current_playlist].append({
                    "name": parts[0],
                    "artist": parts[1],
                    "album": parts[2],
                })

    return True, {
        "track_count": track_count,
        "playback": playback_state,
        "playlists": playlists,
    }


def library_diff(before: dict, after: dict) -> dict:
    """Compare two library snapshots and return differences.

    Args:
        before: snapshot dict from library_snapshot()
        after: snapshot dict from library_snapshot()

    Returns:
        Dict with:
            - track_count_change: int (positive = added, negative = removed)
            - playback_changes: dict of changed playback settings
            - playlists_added: list of playlist names
            - playlists_removed: list of playlist names
            - playlists_changed: dict of {name: {added: [...], removed: [...]}}
            - is_clean: True if no library changes detected (playback state changes are tracked separately)
    """
    # Compare playback state
    before_pb = before.get("playback", {})
    after_pb = after.get("playback", {})
    playback_changes = {}
    for key in ["player_state", "volume", "shuffle", "repeat", "current_track", "current_artist"]:
        if before_pb.get(key) != after_pb.get(key):
            playback_changes[key] = {"before": before_pb.get(key), "after": after_pb.get(key)}

    result = {
        "track_count_change": after.get("track_count", 0) - before.get("track_count", 0),
        "playback_changes": playback_changes,
        "playlists_added": [],
        "playlists_removed": [],
        "playlists_changed": {},
        "is_clean": True,
    }

    before_pl = before.get("playlists", {})
    after_pl = after.get("playlists", {})

    # Find added/removed playlists
    for name in after_pl:
        if name not in before_pl:
            result["playlists_added"].append(name)
    for name in before_pl:
        if name not in after_pl:
            result["playlists_removed"].append(name)

    # Compare track lists for playlists that exist in both
    for name in before_pl:
        if name in after_pl:
            before_tracks = {f"{t['name']}|{t['artist']}" for t in before_pl[name]}
            after_tracks = {f"{t['name']}|{t['artist']}" for t in after_pl[name]}
            added = after_tracks - before_tracks
            removed = before_tracks - after_tracks
            if added or removed:
                result["playlists_changed"][name] = {
                    "added": list(added),
                    "removed": list(removed),
                }

    # Determine if clean (library changes only — playback state changes don't count)
    if (result["track_count_change"] != 0
            or result["playlists_added"]
            or result["playlists_removed"]
            or result["playlists_changed"]):
        result["is_clean"] = False

    return result

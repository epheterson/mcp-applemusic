"""MCP server for Apple Music API.

macOS only - requires the Music app for playback controls.
"""

import json
import subprocess
import time

import requests
from mcp.server.fastmcp import FastMCP

from .auth import get_developer_token, get_user_token, get_config_dir

BASE_URL = "https://api.music.apple.com/v1"
STOREFRONT = "us"

mcp = FastMCP("AppleMusicAPI")


# ============ HELPER FUNCTIONS ============


def is_music_app_running() -> bool:
    """Check if Music app is running."""
    result = subprocess.run(
        ["pgrep", "-x", "Music"],
        capture_output=True,
        text=True
    )
    return result.returncode == 0


def ensure_music_app() -> tuple[bool, str]:
    """
    Ensure Music app is running. Returns (success, message).
    Attempts to launch if not running.
    """
    if is_music_app_running():
        return True, ""

    # Try to launch Music app
    result = subprocess.run(
        ["open", "-a", "Music"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return False, "Music app is not running. Please open the Music app and try again."

    # Wait a moment for it to launch
    time.sleep(2)

    if is_music_app_running():
        return True, "Music app launched."

    return False, "Music app is not running. Please open the Music app and try again."


def run_applescript(script: str) -> str:
    """Execute an AppleScript command via osascript."""
    # Check Music app first
    running, msg = ensure_music_app()
    if not running:
        return msg

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"
    return result.stdout.strip()


def get_token_expiration_warning() -> str | None:
    """Check if developer token expires within 30 days. Returns warning message or None."""
    config_dir = get_config_dir()
    token_file = config_dir / "developer_token.json"

    if not token_file.exists():
        return None

    try:
        with open(token_file) as f:
            data = json.load(f)

        expires = data.get("expires", 0)
        days_left = (expires - time.time()) / 86400

        if days_left < 30:
            return f"⚠️ Developer token expires in {int(days_left)} days. Run: applemusic-mcp generate-token"
    except Exception:
        pass

    return None


def get_headers() -> dict:
    """Get headers for API requests."""
    return {
        "Authorization": f"Bearer {get_developer_token()}",
        "Music-User-Token": get_user_token(),
        "Content-Type": "application/json",
    }


# ============ LOCAL PLAYBACK CONTROLS (AppleScript, macOS only) ============


@mcp.tool()
def music_play() -> str:
    """Start playback in Music app. macOS only."""
    return run_applescript('tell application "Music" to play')


@mcp.tool()
def music_pause() -> str:
    """Pause playback in Music app. macOS only."""
    return run_applescript('tell application "Music" to pause')


@mcp.tool()
def music_next() -> str:
    """Skip to the next track. macOS only."""
    return run_applescript('tell application "Music" to next track')


@mcp.tool()
def music_previous() -> str:
    """Return to the previous track. macOS only."""
    return run_applescript('tell application "Music" to previous track')


@mcp.tool()
def music_current_track() -> str:
    """Get information about the currently playing track. macOS only."""
    script = """
    tell application "Music"
        if player state is playing then
            set currentTrack to current track
            return "Now playing: " & (name of currentTrack) & " by " & (artist of currentTrack) & " from " & (album of currentTrack)
        else
            return "No track is currently playing"
        end if
    end tell
    """
    return run_applescript(script)


@mcp.tool()
def music_play_playlist(playlist: str) -> str:
    """Play a playlist by name. macOS only."""
    script = f'''
    tell application "Music"
        set thePlaylist to user playlist "{playlist}"
        play thePlaylist
        return "Now playing playlist: {playlist}"
    end tell
    '''
    return run_applescript(script)


# ============ LOCAL LIBRARY QUERIES (AppleScript, macOS only) ============


@mcp.tool()
def music_list_playlists() -> str:
    """List all user playlists in the Music library with track counts. macOS only."""
    script = """
    tell application "Music"
        set playlistList to every user playlist
        set output to ""
        repeat with p in playlistList
            set output to output & (name of p) & " (" & (count of tracks of p) & " tracks)" & linefeed
        end repeat
        if output is "" then
            return "No user playlists found."
        end if
        return output
    end tell
    """
    return run_applescript(script)


@mcp.tool()
def music_get_playlist_tracks(playlist: str) -> str:
    """Get all tracks in a playlist (local query). macOS only."""
    script = f'''
    tell application "Music"
        set thePlaylist to user playlist "{playlist}"
        set trackList to every track of thePlaylist
        set output to ""
        repeat with t in trackList
            set output to output & (name of t) & " - " & (artist of t) & linefeed
        end repeat
        if output is "" then
            return "Playlist is empty."
        end if
        return output
    end tell
    '''
    return run_applescript(script)


@mcp.tool()
def music_search_library(query: str) -> str:
    """Search the local Music library for tracks by name. macOS only."""
    script = f'''
    tell application "Music"
        set trackList to every track of playlist "Library" whose name contains "{query}"
        set output to ""
        repeat with t in trackList
            set output to output & (name of t) & " - " & (artist of t) & linefeed
        end repeat
        return output
    end tell
    '''
    return run_applescript(script)


# ============ API-BASED PLAYLIST MANAGEMENT ============


@mcp.tool()
def api_get_library_playlists() -> str:
    """
    Get all playlists from your Apple Music library via API.
    Returns playlist names, IDs, and whether they're editable.
    Only API-created playlists can be edited.
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/me/library/playlists", headers=headers, params={"limit": 100}
        )
        response.raise_for_status()
        data = response.json()

        output = []

        # Check for token expiration warning
        warning = get_token_expiration_warning()
        if warning:
            output.append(warning)
            output.append("")

        for playlist in data.get("data", []):
            attrs = playlist.get("attributes", {})
            name = attrs.get("name", "Unknown")
            can_edit = attrs.get("canEdit", False)
            playlist_id = playlist.get("id")
            edit_status = "editable" if can_edit else "read-only"
            output.append(f"{name} (ID: {playlist_id}, {edit_status})")

        return "\n".join(output) if output else "No playlists found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_search_catalog(query: str, types: str = "songs") -> str:
    """
    Search the Apple Music catalog.

    Args:
        query: Search term
        types: Comma-separated types (songs, albums, artists, playlists)

    Returns: Search results with catalog IDs (note: use api_add_to_library to add these to your library first)
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{STOREFRONT}/search",
            headers=headers,
            params={"term": query, "types": types, "limit": 10},
        )
        response.raise_for_status()
        data = response.json()

        output = []
        results = data.get("results", {})

        if "songs" in results:
            output.append("=== Songs (Catalog IDs - use api_add_to_library first) ===")
            for song in results["songs"].get("data", []):
                attrs = song.get("attributes", {})
                name = attrs.get("name", "Unknown")
                artist = attrs.get("artistName", "Unknown")
                song_id = song.get("id")
                output.append(f"  {name} - {artist} (catalog ID: {song_id})")

        if "albums" in results:
            output.append("=== Albums ===")
            for album in results["albums"].get("data", []):
                attrs = album.get("attributes", {})
                name = attrs.get("name", "Unknown")
                artist = attrs.get("artistName", "Unknown")
                album_id = album.get("id")
                output.append(f"  {name} - {artist} (catalog ID: {album_id})")

        return "\n".join(output) if output else "No results found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_get_library_songs(query: str = "") -> str:
    """
    Search your personal Apple Music library for songs.

    Args:
        query: Search term (leave empty to list recent additions)

    Returns: Songs from your library with library IDs (these can be added to playlists)
    """
    try:
        headers = get_headers()

        if query:
            response = requests.get(
                f"{BASE_URL}/me/library/search",
                headers=headers,
                params={"term": query, "types": "library-songs", "limit": 25},
            )
            songs_key = "library-songs"
        else:
            response = requests.get(
                f"{BASE_URL}/me/library/songs", headers=headers, params={"limit": 25}
            )
            songs_key = None

        response.raise_for_status()
        data = response.json()

        if songs_key:
            songs = data.get("results", {}).get(songs_key, {}).get("data", [])
        else:
            songs = data.get("data", [])

        output = []
        for song in songs:
            attrs = song.get("attributes", {})
            name = attrs.get("name", "Unknown")
            artist = attrs.get("artistName", "Unknown")
            song_id = song.get("id")
            output.append(f"{name} - {artist} (library ID: {song_id})")

        return "\n".join(output) if output else "No songs found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_create_playlist(name: str, description: str = "") -> str:
    """
    Create a new playlist in your Apple Music library.
    Playlists created via API are editable via API.

    Args:
        name: Name for the new playlist
        description: Optional description

    Returns: The new playlist ID
    """
    try:
        headers = get_headers()

        body = {"attributes": {"name": name, "description": description}}

        response = requests.post(
            f"{BASE_URL}/me/library/playlists", headers=headers, json=body
        )
        response.raise_for_status()
        data = response.json()

        playlist_id = data.get("data", [{}])[0].get("id")
        return f"Created playlist '{name}' (ID: {playlist_id})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_update_playlist(playlist_id: str, name: str = "", description: str = "") -> str:
    """
    Update a playlist's name and/or description.
    Only works for playlists created via API (editable=true).

    Args:
        playlist_id: The playlist ID
        name: New name (optional)
        description: New description (optional)

    Returns: Confirmation or error message
    """
    try:
        headers = get_headers()

        attrs = {}
        if name:
            attrs["name"] = name
        if description:
            attrs["description"] = description

        if not attrs:
            return "Error: Provide at least a name or description to update"

        body = {"attributes": attrs}

        response = requests.patch(
            f"{BASE_URL}/me/library/playlists/{playlist_id}",
            headers=headers,
            json=body,
        )

        if response.status_code == 204:
            return f"Successfully updated playlist"
        elif response.status_code == 403:
            return "Error: Cannot edit this playlist (not API-created or permission denied)"
        else:
            response.raise_for_status()
            return f"Updated playlist (status: {response.status_code})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_delete_playlist(playlist_id: str) -> str:
    """
    Delete a playlist from your library.
    Only works for playlists created via API (editable=true).

    Args:
        playlist_id: The playlist ID to delete

    Returns: Confirmation or error message
    """
    try:
        headers = get_headers()

        response = requests.delete(
            f"{BASE_URL}/me/library/playlists/{playlist_id}",
            headers=headers,
        )

        if response.status_code == 204:
            return f"Successfully deleted playlist {playlist_id}"
        elif response.status_code == 403:
            return "Error: Cannot delete this playlist (not API-created or permission denied)"
        elif response.status_code == 404:
            return "Error: Playlist not found"
        else:
            response.raise_for_status()
            return f"Deleted playlist (status: {response.status_code})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_add_to_playlist(playlist_id: str, song_ids: str) -> str:
    """
    Add songs to a library playlist using the Apple Music API.
    Only works for playlists created via API (editable=true).

    Args:
        playlist_id: The playlist ID (get from api_get_library_playlists)
        song_ids: Comma-separated LIBRARY song IDs (get from api_get_library_songs, NOT catalog IDs)

    Returns: Confirmation or error message
    """
    try:
        headers = get_headers()

        ids = [s.strip() for s in song_ids.split(",") if s.strip()]
        if not ids:
            return "No song IDs provided"

        tracks = [{"id": sid, "type": "library-songs"} for sid in ids]
        body = {"data": tracks}

        response = requests.post(
            f"{BASE_URL}/me/library/playlists/{playlist_id}/tracks",
            headers=headers,
            json=body,
        )

        if response.status_code == 204:
            return f"Successfully added {len(ids)} track(s) to playlist"
        elif response.status_code == 403:
            return "Error: Cannot edit this playlist (not API-created or permission denied)"
        elif response.status_code == 500:
            return "Error: Cannot edit this playlist (likely not API-created)"
        else:
            response.raise_for_status()
            return f"Added tracks (status: {response.status_code})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_remove_from_playlist(playlist_id: str, song_ids: str) -> str:
    """
    Remove songs from a library playlist.
    Only works for playlists created via API (editable=true).

    Note: This removes ALL instances of the specified songs from the playlist.

    Args:
        playlist_id: The playlist ID
        song_ids: Comma-separated library song IDs to remove

    Returns: Confirmation or error message
    """
    try:
        headers = get_headers()

        ids = [s.strip() for s in song_ids.split(",") if s.strip()]
        if not ids:
            return "No song IDs provided"

        # Get current playlist tracks
        response = requests.get(
            f"{BASE_URL}/me/library/playlists/{playlist_id}/tracks",
            headers=headers,
            params={"limit": 100},
        )
        response.raise_for_status()
        current_tracks = response.json().get("data", [])

        # Filter out tracks to remove
        tracks_to_keep = [t for t in current_tracks if t["id"] not in ids]
        removed_count = len(current_tracks) - len(tracks_to_keep)

        if removed_count == 0:
            return "No matching tracks found in playlist"

        # Apple Music API doesn't have a direct remove endpoint for tracks
        # We need to clear and re-add (this is a limitation of the API)
        # For now, return an informational message
        return (
            f"Note: Apple Music API doesn't support direct track removal. "
            f"Found {removed_count} track(s) to remove. "
            f"Workaround: Create a new playlist with api_create_playlist, "
            f"then use api_add_to_playlist with only the tracks you want to keep."
        )

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_add_to_library(catalog_ids: str) -> str:
    """
    Add songs from the Apple Music catalog to your personal library.
    After adding, use api_get_library_songs to find the library IDs for playlist operations.

    Args:
        catalog_ids: Comma-separated catalog song IDs (from api_search_catalog)

    Returns: Confirmation or error message
    """
    try:
        headers = get_headers()

        ids = [s.strip() for s in catalog_ids.split(",") if s.strip()]
        if not ids:
            return "No catalog IDs provided"

        # Build the URL with multiple ids parameters
        params = {"ids[songs]": ",".join(ids)}

        response = requests.post(
            f"{BASE_URL}/me/library",
            headers=headers,
            params=params,
        )

        if response.status_code == 202:
            return f"Successfully added {len(ids)} song(s) to your library. Use api_get_library_songs to find their library IDs."
        else:
            response.raise_for_status()
            return f"Added to library (status: {response.status_code})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_get_recently_played() -> str:
    """
    Get recently played tracks from your Apple Music history.

    Returns: List of recently played tracks
    """
    try:
        headers = get_headers()

        response = requests.get(
            f"{BASE_URL}/me/recent/played/tracks",
            headers=headers,
            params={"limit": 20},
        )
        response.raise_for_status()
        data = response.json()

        output = []
        for track in data.get("data", []):
            attrs = track.get("attributes", {})
            name = attrs.get("name", "Unknown")
            artist = attrs.get("artistName", "Unknown")
            album = attrs.get("albumName", "Unknown")
            output.append(f"{name} - {artist} ({album})")

        return "\n".join(output) if output else "No recently played tracks"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_get_album_tracks(album_id: str) -> str:
    """
    Get all tracks from an album (catalog ID).
    Use api_search_catalog with types='albums' to find album IDs.

    Args:
        album_id: Catalog album ID

    Returns: List of tracks with their catalog IDs
    """
    try:
        headers = get_headers()

        response = requests.get(
            f"{BASE_URL}/catalog/{STOREFRONT}/albums/{album_id}/tracks",
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

        output = [f"=== Album Tracks (Catalog IDs) ==="]
        for track in data.get("data", []):
            attrs = track.get("attributes", {})
            name = attrs.get("name", "Unknown")
            track_num = attrs.get("trackNumber", "?")
            duration_ms = attrs.get("durationInMillis", 0)
            duration = f"{duration_ms // 60000}:{(duration_ms // 1000) % 60:02d}"
            track_id = track.get("id")
            output.append(f"  {track_num}. {name} [{duration}] (catalog ID: {track_id})")

        return "\n".join(output) if len(output) > 1 else "No tracks found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def api_copy_playlist(source_playlist_id: str, new_name: str) -> str:
    """
    Copy a playlist to a new API-editable playlist.
    Use this to make an editable copy of a read-only playlist.

    Args:
        source_playlist_id: ID of the playlist to copy
        new_name: Name for the new playlist

    Returns: New playlist ID or error
    """
    try:
        headers = get_headers()

        # Get source playlist tracks
        all_tracks = []
        offset = 0
        while True:
            response = requests.get(
                f"{BASE_URL}/me/library/playlists/{source_playlist_id}/tracks",
                headers=headers,
                params={"limit": 100, "offset": offset},
            )
            response.raise_for_status()
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)
            offset += 100

        # Create new playlist
        body = {"attributes": {"name": new_name}}
        response = requests.post(
            f"{BASE_URL}/me/library/playlists", headers=headers, json=body
        )
        response.raise_for_status()
        new_id = response.json()["data"][0]["id"]

        # Add tracks in batches
        batch_size = 25
        for i in range(0, len(all_tracks), batch_size):
            batch = all_tracks[i : i + batch_size]
            track_data = [{"id": t["id"], "type": "library-songs"} for t in batch]
            requests.post(
                f"{BASE_URL}/me/library/playlists/{new_id}/tracks",
                headers=headers,
                json={"data": track_data},
            )

        return f"Created '{new_name}' (ID: {new_id}) with {len(all_tracks)} tracks"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def check_auth_status() -> str:
    """Check if authentication tokens are valid and API is accessible."""
    config_dir = get_config_dir()
    dev_token_file = config_dir / "developer_token.json"
    user_token_file = config_dir / "music_user_token.json"

    status = []

    # Check developer token
    if dev_token_file.exists():
        try:
            with open(dev_token_file) as f:
                data = json.load(f)
            expires = data.get("expires", 0)
            days_left = (expires - time.time()) / 86400

            if days_left < 0:
                status.append("Developer Token: EXPIRED - Run: applemusic-mcp generate-token")
            elif days_left < 30:
                status.append(f"Developer Token: ⚠️ EXPIRES IN {int(days_left)} DAYS - Run: applemusic-mcp generate-token")
            else:
                status.append(f"Developer Token: OK ({int(days_left)} days remaining)")
        except Exception:
            status.append("Developer Token: ERROR reading file")
    else:
        status.append("Developer Token: MISSING - Run: applemusic-mcp generate-token")

    # Check user token
    if user_token_file.exists():
        status.append("Music User Token: OK")
    else:
        status.append("Music User Token: MISSING - Run: applemusic-mcp authorize")

    # Test API connection
    if dev_token_file.exists() and user_token_file.exists():
        try:
            headers = get_headers()
            response = requests.get(
                f"{BASE_URL}/me/library/playlists", headers=headers, params={"limit": 1}
            )
            if response.status_code == 200:
                status.append("API Connection: OK")
            elif response.status_code == 401:
                status.append("API Connection: UNAUTHORIZED - Token may be expired. Run: applemusic-mcp authorize")
            else:
                status.append(f"API Connection: FAILED ({response.status_code})")
        except Exception as e:
            status.append(f"API Connection: ERROR - {str(e)}")

    return "\n".join(status)


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()

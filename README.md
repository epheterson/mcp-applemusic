# mcp-applemusic-api

An MCP (Model Context Protocol) server for managing Apple Music playlists via the official Apple Music API.

**Unlike AppleScript-based solutions, this actually works for adding/removing tracks from playlists.**

> **macOS only** - Playback controls require the Music app. API features work on any platform with Python.

## Features

- **Playlist Management**: Create, update, delete, and copy playlists
- **Track Management**: Add tracks to playlists, add songs to library
- **Search**: Search your library and the Apple Music catalog
- **Playback Controls**: Play, pause, next, previous (macOS only)
- **Smart Warnings**: Alerts when tokens are expiring within 30 days
- **Auto Music App Launch**: Automatically opens Music app when needed

## Why This Exists

AppleScript-based Apple Music automation is broken for playlist modification in modern macOS - commands execute but silently fail to add tracks. The Apple Music REST API actually works, but requires proper authentication.

## Prerequisites

1. **Apple Developer Account** (free or paid)
2. **macOS** with Apple Music app (for playback features)
3. **Python 3.10+**
4. **Active Apple Music subscription** (for user token)

## Setup

### 1. Create MusicKit Credentials

1. Go to [Apple Developer Portal](https://developer.apple.com/account/resources/authkeys/list)
2. Create a new **Key** with **MusicKit** enabled
3. Download the `.p8` private key file

> **⚠️ IMPORTANT: You can only download the .p8 key file ONCE!**
> Back it up immediately to a secure location (e.g., iCloud Drive, password manager).
> If you lose it, you'll need to create a new key.

4. Note your **Key ID** (shown after creation)
5. Note your **Team ID** (from Membership page)

### 2. Install the Package

```bash
# Clone the repo
git clone https://github.com/epheterson/mcp-applemusic-api.git
cd mcp-applemusic-api

# Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

> **Note:** This is an MCP "server" in name only. You don't run it manually—Claude Code launches it automatically when needed.

### 3. Configure Credentials

```bash
# Create config directory
mkdir -p ~/.config/applemusic-mcp

# Copy your .p8 key (back this up first!)
cp /path/to/AuthKey_XXXXXXXX.p8 ~/.config/applemusic-mcp/

# Create config file
cat > ~/.config/applemusic-mcp/config.json << EOF
{
  "team_id": "YOUR_TEAM_ID",
  "key_id": "YOUR_KEY_ID",
  "private_key_path": "~/.config/applemusic-mcp/AuthKey_XXXXXXXX.p8"
}
EOF
```

### 4. Generate Developer Token

```bash
applemusic-mcp generate-token
```

This creates a JWT valid for 180 days. The server will warn you when it's within 30 days of expiring.

### 5. Authorize User Access

```bash
applemusic-mcp authorize
```

This opens a browser for Apple ID login. After authorizing, the Music User Token is saved automatically—no copy/paste needed.

### 6. Configure Claude Code

Add to your Claude Code MCP settings (`~/.claude.json` or project settings):

```json
{
  "mcpServers": {
    "applemusic": {
      "command": "/path/to/mcp-applemusic-api/venv/bin/python",
      "args": ["-m", "applemusic_mcp"]
    }
  }
}
```

### 7. Check Status

```bash
applemusic-mcp status
```

## Usage

Once configured, Claude can:

```
"List my Apple Music playlists"
"Create a new playlist called 'Road Trip 2024'"
"Add 'Wonderwall' by Oasis to my workout playlist"
"Search my library for songs by The Beatles"
"What's currently playing?"
"What have I listened to recently?"
```

## Important Limitations

### Playlist Editability

The Apple Music API can only edit playlists that were **created via the API**. Playlists created in iTunes/Music app are read-only via API.

**Workaround:** Use `api_copy_playlist` to create an API-editable copy of any playlist.

### Track Removal

The Apple Music API doesn't support direct track removal from playlists. To remove tracks, create a new playlist with only the tracks you want to keep.

### Library IDs vs Catalog IDs

- **Catalog IDs**: From `api_search_catalog` - these are global Apple Music IDs
- **Library IDs**: From `api_get_library_songs` - these are your personal library IDs

To add a song from the catalog to a playlist:
1. First add it to your library with `api_add_to_library`
2. Find its library ID with `api_get_library_songs`
3. Then add to playlist with `api_add_to_playlist`

### Token Expiration

- **Developer Token:** Valid 180 days. You'll see warnings starting 30 days before expiration.
- **Music User Token:** Expires periodically. Re-authorize with `applemusic-mcp authorize` if API returns 401.

### macOS Only (Playback Controls)

The `music_*` tools (play, pause, next, etc.) use AppleScript and only work on macOS. The `api_*` tools work on any platform.

## Tools Available

### Playback Controls (macOS only)

| Tool | Description |
|------|-------------|
| `music_play` | Start playback |
| `music_pause` | Pause playback |
| `music_next` | Next track |
| `music_previous` | Previous track |
| `music_current_track` | Get now playing info |
| `music_play_playlist` | Play a playlist by name |

### Local Library (macOS only)

| Tool | Description |
|------|-------------|
| `music_list_playlists` | List all playlists with track counts |
| `music_get_playlist_tracks` | Get tracks in a playlist |
| `music_search_library` | Search local library |

### API - Playlists

| Tool | Description |
|------|-------------|
| `api_get_library_playlists` | List playlists with IDs and editability |
| `api_create_playlist` | Create a new playlist |
| `api_update_playlist` | Rename or update description |
| `api_delete_playlist` | Delete an API-created playlist |
| `api_copy_playlist` | Copy playlist to an editable version |
| `api_add_to_playlist` | Add tracks to a playlist |
| `api_remove_from_playlist` | Info on removing tracks (see limitations) |

### API - Library & Search

| Tool | Description |
|------|-------------|
| `api_get_library_songs` | Search your library for songs |
| `api_add_to_library` | Add catalog songs to your library |
| `api_search_catalog` | Search Apple Music catalog |
| `api_get_album_tracks` | Get all tracks from an album |
| `api_get_recently_played` | Get recently played tracks |

### Utilities

| Tool | Description |
|------|-------------|
| `check_auth_status` | Verify tokens and API connection |

## Troubleshooting

### "Music app is not running"
The Music app needs to be open for playback controls. The server will try to launch it automatically, but if that fails, open it manually.

### "Unauthorized" error
Your Music User Token may have expired. Run `applemusic-mcp authorize` to get a new one.

### "Cannot edit this playlist"
The playlist wasn't created via API. Use `api_copy_playlist` to create an editable copy.

### Developer token expiring
Run `applemusic-mcp generate-token` to generate a new 180-day token.

### Lost your .p8 key?
You'll need to create a new MusicKit key in the Apple Developer Portal. Update your config.json with the new key_id and key file.

## Setting Up on Another Machine

1. Clone the repo and install (steps 2 above)
2. Copy your backed-up `.p8` key file to `~/.config/applemusic-mcp/`
3. Create config.json with the same team_id and key_id
4. Run `applemusic-mcp generate-token`
5. Run `applemusic-mcp authorize` (you'll need to sign in again)

## License

MIT

## Credits

- [FastMCP](https://github.com/jlowin/fastmcp) - The MCP server framework
- [Apple MusicKit](https://developer.apple.com/documentation/applemusicapi) - REST API documentation
- [Model Context Protocol](https://modelcontextprotocol.io/) - The protocol specification by Anthropic

Built to solve the "AppleScript playlist editing is broken" problem after discovering existing solutions don't actually work for playlist modification.

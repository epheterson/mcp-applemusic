# mcp-applemusic

[MCP](https://modelcontextprotocol.io/) server for Apple Music - lets Claude manage playlists, control playback, and browse your library.

## Features

| Feature | macOS | API |
|---------|:-----:|:---:|
| List playlists | ✓ | ✓ |
| Browse library songs | ✓ | ✓ |
| Create playlists | ✓ | ✓ |
| Search library | ✓ | ✓ |
| Love/dislike tracks | ✓ | ✓ |
| CSV/JSON export | ✓ | ✓ |
| Add tracks to playlists | ✓ | API-created |
| Search catalog |   | ✓ |
| Add songs to library |   | ✓ |
| Recommendations, charts, radio |   | ✓ |
| Play tracks | ✓ |   |
| Playback control (pause/skip/seek) | ✓ |   |
| Volume, shuffle, repeat | ✓ |   |
| Star ratings (1-5) | ✓ |   |
| Remove tracks from playlists | ✓ |   |
| Delete playlists | ✓ |   |

**macOS** uses AppleScript for full local control. **API** mode enables catalog features and works cross-platform.

---

## Quick Start (macOS)

**Requirements:** Python 3.10+, Apple Music app with subscription.

**No Apple Developer account needed!** Most features work instantly via AppleScript.

```bash
git clone https://github.com/epheterson/mcp-applemusic.git
cd mcp-applemusic
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

Add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "Apple Music": {
      "command": "/full/path/to/mcp-applemusic/venv/bin/python",
      "args": ["-m", "applemusic_mcp"]
    }
  }
}
```

**That's it!** Restart Claude and try: "List my Apple Music playlists" or "Play my favorites playlist"

> **Windows/Linux users:** Skip to [API Setup](#api-setup-optional-on-macos-required-on-windowslinux) - AppleScript features require macOS, but API mode works cross-platform.

---

## API Setup (Optional on macOS, Required on Windows/Linux)

Want catalog search, recommendations, or adding songs from Apple Music? Set up API access:

### 1. Get MusicKit Key

1. [Apple Developer Portal → Keys](https://developer.apple.com/account/resources/authkeys/list) → Click **+**
2. Name it anything, check **MusicKit**, click Continue → Register
3. **Download the .p8 file** (one-time download!)
4. Note your **Key ID** (10 chars) and **Team ID** (from [Membership](https://developer.apple.com/account/#!/membership))

### 2. Configure

```bash
mkdir -p ~/.config/applemusic-mcp
cp ~/Downloads/AuthKey_XXXXXXXXXX.p8 ~/.config/applemusic-mcp/
```

Create `~/.config/applemusic-mcp/config.json`:
```json
{
  "team_id": "YOUR_TEAM_ID",
  "key_id": "YOUR_KEY_ID",
  "private_key_path": "~/.config/applemusic-mcp/AuthKey_XXXXXXXXXX.p8"
}
```

### 3. Generate Tokens

```bash
applemusic-mcp generate-token   # Creates developer token (180 days)
applemusic-mcp authorize        # Opens browser for Apple Music auth
applemusic-mcp status           # Verify everything works
```

### 4. Add to Claude (Windows/Linux)

Add to your Claude Desktop config:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "Apple Music": {
      "command": "/full/path/to/mcp-applemusic/venv/bin/python",
      "args": ["-m", "applemusic_mcp"]
    }
  }
}
```

### Optional Preferences

Add to config.json:
```json
{
  "preferences": {
    "auto_search": true,
    "clean_only": false,
    "fetch_explicit": false,
    "reveal_on_library_miss": false
  }
}
```

- `auto_search`: Auto-find catalog tracks not in library, for `add_to_playlist` (default: false)
- `clean_only`: Filter explicit content, for `search_catalog`, `search_library`, `browse_library` (default: false)
- `fetch_explicit`: Fetch explicit status (cached), for `get_playlist_tracks`, `search_library`, `browse_library` (default: false)
- `reveal_on_library_miss`: Open catalog tracks in Music app, for `play` (default: false)

---

## Usage Examples

**Playlist management:**
- "List my Apple Music playlists"
- "Create a playlist called 'Road Trip' and add some upbeat songs"
- "Add Hey Jude by The Beatles to my Road Trip playlist"
- "Remove the last 3 tracks from my workout playlist"
- "Export my library to CSV"

**Discovery & playback (macOS):**
- "What have I been listening to recently?"
- "Play my workout playlist on shuffle"
- "Skip to the next track"
- "What's playing right now?"

**With API enabled:**
- "Search Apple Music for 90s alternative rock"
- "Find songs similar to Bohemian Rhapsody and add them to my library"
- "What are the top charts right now?"
- "Get me personalized recommendations"

---

## Tools

**v0.5.0 Update:** All tools consolidated into 5 action-based dispatchers for reduced MCP overhead. See [CHANGELOG](CHANGELOG.md#050---2026-01-06) for migration guide.

### `playlist(action=...)`
Playlist operations - list, manage tracks, create, copy, remove (macOS), delete (macOS)

| Action | Parameters | Description | Platform |
|--------|-----------|-------------|----------|
| `list` | `format`, `export`, `full` | List all playlists | All |
| `tracks` | `playlist`, `filter`, `limit`, `offset`, `format`, `export`, `full`, `fetch_explicit` | Get playlist tracks with filter/pagination | All (by-name: macOS) |
| `search` | `query`, `playlist` | Search tracks in playlist | All |
| `create` | `name`, `description` | Create new playlist | All |
| `add` | `playlist`, `track`, `album`, `artist`, `allow_duplicates`, `verify`, `auto_search` | Smart add: auto-search catalog, skip duplicates | All (by-name: macOS) |
| `copy` | `source`, `new_name` | Copy playlist to editable version | All (by-name: macOS) |
| `remove` | `playlist`, `track`, `artist` | Remove track(s) from playlist | macOS |
| `delete` | `name` or `playlist` | Delete playlist | macOS |

**Examples:**
```python
playlist(action="list")
playlist(action="create", name="Road Trip", description="Summer vibes")
playlist(action="add", playlist="Road Trip", track="Hey Jude", artist="Beatles")
playlist(action="tracks", playlist="p.abc123", limit=50)
```

**Unified `track` parameter** auto-detects: names, IDs (catalog/library/persistent), CSV, or JSON arrays. Add entire albums with `album` parameter.

### `library(action=...)`
Library management - search, add, browse, rate, recently played/added, remove (macOS)

| Action | Parameters | Description | Platform |
|--------|-----------|-------------|----------|
| `search` | `query`, `types`, `limit`, `format`, `export`, `full`, `fetch_explicit`, `clean_only` | Search your library (fast local on macOS) | All |
| `add` | `track`, `album`, `artist` | Add tracks/albums from catalog | All |
| `browse` | `item_type`, `limit`, `offset`, `format`, `export`, `full`, `fetch_explicit`, `clean_only` | List songs/albums/artists/videos | All |
| `recently_played` | `limit`, `format`, `export`, `full` | Recent listening history | All |
| `recently_added` | `limit`, `format`, `export`, `full` | Recently added content | All |
| `rate` | `rate_action`, `track`, `artist`, `stars` | Love/dislike/get/set ratings | All (stars: macOS) |
| `remove` | `track`, `artist` | Remove track(s) from library | macOS |

**Examples:**
```python
library(action="search", query="Beatles", types="songs", limit=25)
library(action="add", album="Abbey Road", artist="Beatles")
library(action="recently_played", limit=30)
library(action="rate", rate_action="love", track="Hey Jude")
```

### `catalog(action=...)`
Catalog search and details - search, albums, songs, artists, genres, stations

| Action | Parameters | Description | Platform |
|--------|-----------|-------------|----------|
| `search` | `query`, `types`, `limit`, `format`, `export`, `full`, `clean_only` | Search Apple Music catalog | All |
| `album_tracks` | `album`, `artist`, `limit`, `offset`, `format`, `export`, `full` | Get album tracks (by name or ID) | All |
| `album_details` | `album`, `artist`, `format`, `export`, `full` | Full album metadata + track listing | All |
| `song_details` | `song_id` | Full song metadata | All |
| `artist_details` | `artist` | Artist info and discography | All |
| `song_station` | `song_id` | Get radio station for song | All |
| `genres` | - | List all available genres | All |

**Examples:**
```python
catalog(action="search", query="90s alternative", types="songs", limit=50)
catalog(action="album_tracks", album="Abbey Road", artist="Beatles")
catalog(action="album_details", album="GNX", artist="Kendrick Lamar")
catalog(action="artist_details", artist="The Beatles")
```

### `discover(action=...)`
Discovery and recommendations - personalized stations, charts, top songs, similar artists

| Action | Parameters | Description | Platform |
|--------|-----------|-------------|----------|
| `recommendations` | `format`, `export`, `full` | Personalized recommendations | All |
| `heavy_rotation` | `format`, `export`, `full` | Your frequently played | All |
| `charts` | `chart_type`, `format`, `export`, `full` | Apple Music charts | All |
| `top_songs` | `artist` | Artist's popular songs | All |
| `similar_artists` | `artist` | Find similar artists | All |
| `search_suggestions` | `term` | Autocomplete suggestions | All |
| `personal_station` | - | Your personal radio station | All |

**Optional:** All catalog-based discover actions (`charts`, `top_songs`, `similar_artists`, `song_station`) accept an optional `storefront` parameter to query other regions without changing your default storefront.

**Examples:**
```python
discover(action="recommendations")
discover(action="charts", chart_type="songs", storefront="it")  # Italy charts
discover(action="top_songs", artist="The Beatles")
```

### Playback (macOS only)
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `play` | Play track, playlist, or album (with shuffle option) | API + AS | macOS |
| `playback_control` | Play, pause, stop, next, previous, seek | AppleScript | macOS |
| `get_now_playing` | Current track info and player state | AppleScript | macOS |
| `playback_settings` | Get/set volume, shuffle, repeat | AppleScript | macOS |

`play` accepts ONE of: `track`, `playlist`, or `album`. Use `shuffle=True` for shuffled playback. Response shows source: `[Library]`, `[Catalog]`, or `[Catalog→Library]`. Catalog items can be added first (`add_to_library=True`) or opened in Music (`reveal=True`).

### Utilities

| Tool | Description | Platform |
|------|-------------|----------|
| `config(action=...)` | Preferences, storefronts, cache, audit log | All |
| `check_auth_status()` | Verify tokens and API connection | All |
| `airplay(device_name=...)` | List or switch AirPlay devices | macOS |
| `reveal_in_music(track, artist)` | Show track in Music app | macOS |

**Config actions:** `info`, `set-pref`, `list-storefronts`, `audit-log`, `clear-tracks`, `clear-exports`, `clear-audit-log`

### Output Format

Most list tools support these output options:

| Parameter | Values | Description |
|-----------|--------|-------------|
| `format` | `"text"` (default), `"json"`, `"csv"`, `"none"` | Response format |
| `export` | `"none"` (default), `"csv"`, `"json"` | Write file to disk |
| `full` | `False` (default), `True` | Include all metadata |

**Text format** auto-selects the best tier that fits:
- **Full**: Name - Artist (duration) Album [Year] Genre id
- **Compact**: Name - Artist (duration) id
- **Minimal**: Name - Artist id

**Examples:**
```
library(action="search", query="beatles", format="json")                      # JSON response
library(action="browse", item_type="songs", export="csv")                     # Text + CSV file
library(action="browse", item_type="songs", format="none", export="csv")      # CSV only (saves tokens)
playlist(action="tracks", playlist="p.123", export="json", full=True)         # JSON file with all metadata
```

### MCP Resources

Exported files are accessible via MCP resources (Claude Desktop can read these):

| Resource | Description |
|----------|-------------|
| `exports://list` | List all exported files |
| `exports://{filename}` | Read a specific export file |

---

## Limitations

### Windows/Linux
| Limitation | Workaround |
|------------|------------|
| Only API-created playlists editable | `copy_playlist` makes editable copy |
| Can't delete playlists or remove tracks | Create new playlist instead |
| No playback control | Use Music app directly |

### Both Platforms
- **Tokens expire:** Developer token lasts 180 days. You'll see warnings starting 30 days before expiration. Run `applemusic-mcp generate-token` to renew.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 401 Unauthorized | `applemusic-mcp authorize` |
| "Cannot edit playlist" | Use `copy_playlist` for editable copy |
| Token expiring | `applemusic-mcp generate-token` |
| Check everything | `applemusic-mcp status` |

---

## CLI Reference

```bash
applemusic-mcp status          # Check tokens and connection
applemusic-mcp generate-token  # New developer token (180 days)
applemusic-mcp authorize       # Browser auth for user token
applemusic-mcp serve           # Run MCP server (auto-launched by Claude)
```

**Config:** `~/.config/applemusic-mcp/` (config.json, .p8 key, tokens)

---

## License

MIT · *Unofficial community project, not affiliated with Apple.*

## Credits

[FastMCP](https://github.com/jlowin/fastmcp) · [Apple MusicKit](https://developer.apple.com/documentation/applemusicapi) · [Model Context Protocol](https://modelcontextprotocol.io/)

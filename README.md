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

## Tools (37 total)

### Playlists
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `get_library_playlists` | List all playlists | API | All |
| `get_playlist_tracks` | Get tracks with filter/limit, optional explicit status | API or AS | All (by-name: macOS) |
| `create_playlist` | Create new playlist | API | All |
| `add_to_playlist` | Smart add: auto-search catalog, auto-add to library, skip duplicates | API or AS | All (by-name: macOS) |
| `copy_playlist` | Copy playlist to editable version (by ID or name) | API or AS | All (by-name: macOS) |
| `remove_from_playlist` | Remove track(s): single, array, or by ID | AppleScript | macOS |
| `delete_playlist` | Delete playlist | AppleScript | macOS |
| `search_playlist` | Search playlist tracks (native AS search, fast) | API or AS | All |

`add_to_playlist` uses a **unified `track` parameter** that auto-detects format: names (`track="Hey Jude"`), IDs (`track="1440783617"`), CSV (`track="Hey Jude, Let It Be"`), or JSON (`track='[{"name":"Hey Jude","artist":"Beatles"}]'`). You can also add entire albums with the `album` parameter. With `auto_search=True`, tracks not in your library are automatically found in the catalog, added to your library, then added to the playlist—all in one call.

`remove_from_playlist` and `remove_from_library` use the same unified `track` parameter format.

### Library
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `search_library` | Search your library by types (fast local on macOS) | AS + API | All |
| `browse_library` | List songs/albums/artists/videos by type | API | All |
| `get_album_tracks` | Get tracks from album (by name or ID) | API | All |
| `get_recently_played` | Recent listening history | API | All |
| `get_recently_added` | Recently added content | API | All |
| `add_to_library` | Add tracks or albums from catalog | API | All |
| `remove_from_library` | Remove track(s) from library | AppleScript | macOS |
| `rating` | Love/dislike/get/set star ratings | API + AS | All (stars: macOS) |

`add_to_library` supports both `track` and `album` parameters. `get_album_tracks` accepts album names (`album="Abbey Road"`) or IDs. Artist tools accept names or catalog IDs.

### Catalog & Discovery
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `search_catalog` | Search Apple Music (songs, albums, artists, playlists, music-videos) | API | All |
| `get_song_details` | Full song details | API | All |
| `get_artist_details` | Artist info and discography | API | All |
| `get_artist_top_songs` | Artist's popular songs | API | All |
| `get_similar_artists` | Find similar artists | API | All |
| `get_recommendations` | Personalized recommendations | API | All |
| `get_heavy_rotation` | Your frequently played | API | All |
| `get_personal_station` | Your personal radio station | API | All |
| `get_song_station` | Radio station from a song | API | All |
| `get_charts` | Top songs, albums, playlists | API | All |
| `get_genres` | List all genres | API | All |
| `get_search_suggestions` | Autocomplete suggestions | API | All |

`search_catalog` supports `types="music-videos"` for video search. Leave query empty for featured videos.

### Playback
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `play` | Play track, playlist, or album (with shuffle option) | API + AS | macOS |
| `playback_control` | Play, pause, stop, next, previous, seek | AppleScript | macOS |
| `get_now_playing` | Current track info and player state | AppleScript | macOS |
| `playback_settings` | Get/set volume, shuffle, repeat | AppleScript | macOS |

`play` accepts ONE of: `track`, `playlist`, or `album`. Use `shuffle=True` for shuffled playback. Response shows source: `[Library]`, `[Catalog]`, or `[Catalog→Library]`. Catalog items can be added first (`add_to_library=True`) or opened in Music (`reveal=True`).

### Utilities
| Tool | Description | Method | Platform |
|------|-------------|--------|----------|
| `check_auth_status` | Verify tokens and API connection | API | All |
| `config` | Preferences, storefronts, cache, audit log | Local + API | All |
| `airplay` | List or switch AirPlay devices | AppleScript | macOS |
| `reveal_in_music` | Show track in Music app | AppleScript | macOS |

`config(action="list-storefronts")` shows available Apple Music regions.

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
search_library("beatles", format="json")                      # JSON response
browse_library("songs", export="csv")                         # Text + CSV file
browse_library("songs", format="none", export="csv")          # CSV only (saves tokens)
get_playlist_tracks("p.123", export="json", full=True)        # JSON file with all metadata
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

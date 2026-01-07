# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-01-06

### Breaking Changes

**MCP Tool Consolidation** - Reduced from 37 to 5 action-based dispatchers to minimize MCP context footprint (80%+ reduction):

#### Playlist Operations → `playlist(action=...)`

| Old Tool | New Call |
|----------|----------|
| `get_library_playlists()` | `playlist(action="list")` |
| `get_playlist_tracks(playlist, ...)` | `playlist(action="tracks", playlist=playlist, ...)` |
| `search_playlist(query, playlist)` | `playlist(action="search", query=query, playlist=playlist)` |
| `create_playlist(name, description)` | `playlist(action="create", name=name, description=description)` |
| `add_to_playlist(playlist, ...)` | `playlist(action="add", playlist=playlist, ...)` |
| `copy_playlist(source, new_name)` | `playlist(action="copy", source=source, new_name=new_name)` |
| `remove_from_playlist(...)` | `playlist(action="remove", ...)` _(macOS only)_ |
| `delete_playlist(name)` | `playlist(action="delete", name=name)` _(macOS only)_ |

#### Library Operations → `library(action=...)`

| Old Tool | New Call |
|----------|----------|
| `search_library(query, ...)` | `library(action="search", query=query, ...)` |
| `add_to_library(track, album, ...)` | `library(action="add", track=track, album=album, ...)` |
| `get_recently_played(...)` | `library(action="recently_played", ...)` |
| `get_recently_added(...)` | `library(action="recently_added", ...)` |
| `browse_library(item_type, ...)` | `library(action="browse", item_type=item_type, ...)` |
| `rating(rate_action, ...)` | `library(action="rate", rate_action=rate_action, ...)` |
| `remove_from_library(...)` | `library(action="remove", ...)` _(macOS only)_ |

#### Catalog Operations → `catalog(action=...)`

| Old Tool | New Call |
|----------|----------|
| `search_catalog(query, ...)` | `catalog(action="search", query=query, ...)` |
| `get_album_tracks(album, ...)` | `catalog(action="album_tracks", album=album, ...)` |
| `get_song_details(song_id)` | `catalog(action="song_details", song_id=song_id)` |
| `get_artist_details(artist)` | `catalog(action="artist_details", artist=artist)` |
| `get_song_station(song_id)` | `catalog(action="song_station", song_id=song_id)` |
| `get_genres()` | `catalog(action="genres")` |

#### Discovery → `discover(action=...)`

| Old Tool | New Call |
|----------|----------|
| `get_recommendations(...)` | `discover(action="recommendations", ...)` |
| `get_heavy_rotation(...)` | `discover(action="heavy_rotation", ...)` |
| `get_charts(chart_type)` | `discover(action="charts", chart_type=chart_type)` |
| `get_artist_top_songs(artist)` | `discover(action="artist_top_songs", artist=artist)` |
| `get_similar_artists(artist)` | `discover(action="similar_artists", artist=artist)` |
| `get_search_suggestions(term)` | `discover(action="search_suggestions", term=term)` |
| `get_personal_station()` | `discover(action="personal_station")` |

#### Configuration → `config(action=...)` _(unchanged)_

Playback tools (`play`, `playback_control`, `playback_settings`, `get_now_playing`) remain unchanged.

**Rationale:** Reduces MCP context overhead from 37 tools to 5 dispatchers, enabling more efficient token usage for Claude and other LLM clients. Each dispatcher accepts a superset of parameters and routes to internal implementation functions.

**Migration:** All functionality preserved. Update MCP client code to use action-based API.

### Changed

- **Trimmed tool docstrings** - Reduced verbose multi-line docstrings to concise 1-2 line summaries to further reduce MCP context footprint (commit 0984338)
- **Action normalization** - All dispatchers normalize action names: `action.lower().strip().replace("-", "_")` allows both "recently-played" and "recently_played"
- **Consistent error messages** - All dispatchers validate required parameters and provide helpful error messages with valid action lists

## [0.4.3] - 2026-01-05

### Added

- **Fuzzy matching for all entity types** - Unified fuzzy matching across playlists, tracks, and albums:
  - 3-pass algorithm: exact match → partial substring → fuzzy (normalized)
  - Transformations: lowercase, diacritics removal, "and" ↔ "&", emoji stripping, apostrophe normalization
  - Partial-after-normalization support: "Sgt Peppers" matches "Sgt. Pepper's Lonely Hearts Club Band"
  - AppleScript fuzzy resolution: emoji playlists resolve correctly ("My Mix" → "🎵 My Mix")
  - Fuzzy match info shown in output when non-exact match used

- **ResolvedPlaylist dataclass** - All playlist operations now use structured resolution:
  - `api_id` - p.XXX ID for API calls (fast, cross-platform)
  - `applescript_name` - Actual playlist name for AppleScript (required for remove operations)
  - `fuzzy_match` - Details about any fuzzy matching performed
  - Eliminates tuple unpacking bugs and provides type safety

- **Search result deduplication** - `search_catalog` and `search_library` now deduplicate by track ID

- **Comprehensive integration tests** - 15 new tests covering:
  - API-only mode (first 2, 5, 10 user actions)
  - macOS-only mode (AppleScript-preferred operations)
  - Combined mode (routing logic, fallback behavior)
  - Fuzzy matching workflows (playlists, tracks, albums)
  - Power user workflows (album operations, copy playlist, deduplication)

### Fixed

- **API/AppleScript routing** - Functions now correctly prefer API when `api_id` is available:
  - `get_playlist_tracks`, `search_playlist`, `copy_playlist`, `add_to_playlist`
  - Previously incorrectly preferred AppleScript even when API ID was available

- **Fallback logic in fuzzy matching** - Fixed condition that checked if filtered list was empty instead of whether a match was found

- **Variable shadowing in `remove_from_playlist`** - Renamed internal variable to avoid shadowing the resolved playlist

### Changed

- **DRY fuzzy matching** - Extracted `_fuzzy_match_entity()` generic function used by:
  - Playlist resolution (`_find_api_playlist_by_name`)
  - Track matching (`_find_matching_catalog_song`)
  - Album matching (`_find_matching_catalog_album`)

- **Performance optimization** - Fuzzy matching uses 3-pass approach:
  - Pass 1: Exact match (O(n), no normalization) - fastest
  - Pass 2: Partial match (O(n), substring only) - fast
  - Pass 3: Fuzzy match (normalization, only if needed) - slower but thorough

## [0.4.2] - 2026-01-02

### Fixed

- **add_to_playlist regression** - v0.4.1's API-first playlist resolution broke library track lookup. "Four Tops" couldn't find "The Four Tops" because AppleScript mode (which does partial matching on library) was skipped in favor of API mode (which only searched catalog).

### Changed

- **add_to_playlist prefers AppleScript on macOS** - When user provides track names, now uses AppleScript mode (searches library directly with partial matching) instead of forcing API mode. API mode only used for explicit playlist IDs or track IDs.

### Added

- **`_find_track_id()` helper** - Canonical way to find a track: searches library first, then catalog. Used as fallback when API mode is needed.
- **`_search_library_songs()` helper** - Matches `_search_catalog_songs()` for consistency.

## [0.4.1] - 2026-01-01

### Added

- **Pagination with `offset` parameter** - Skip first N items in `get_playlist_tracks`, `get_album_tracks`, `browse_library`:
  ```python
  get_playlist_tracks(playlist="Mix", limit=50, offset=100)  # Get tracks 101-150
  ```
- **`_find_api_playlist_by_name()`** - New helper to look up `p.XXX` playlist IDs from names via API (case-insensitive, exact match prioritized)
- **`_apply_pagination()` helper** - DRY pagination logic for all listing tools
- **Catalog/library ID support for removal** - `remove_from_playlist` and `remove_from_library` now accept any ID type via cache lookup

### Changed

- **20x faster playlist queries for shared tracks** - Playlists with Apple Music subscription content now resolve via API instead of slow AppleScript per-track iteration (~29s → ~1.6s for 432-track playlist)
- **API-first playlist name resolution** - `_resolve_playlist()` now looks up API playlist ID by name before falling back to AppleScript
- **Performance stats in output** - `get_playlist_tracks` API path now shows timing and API call count
- **Improved pagination display** - Shows "X-Y of Z tracks" when paginating, simple count otherwise
- **Cache stores track metadata** - Name, artist, album now cached alongside explicit status for ID-to-name lookups
- **Slimmed tool docstrings** - Removed verbose format detection examples, kept essential info

### Fixed

- **Offset shadowing bug** - When `fetch_explicit=True` on AppleScript path, internal API pagination loop overwrote the function's offset parameter, causing wrong headers like "401-432 of 432" when no pagination was requested

## [0.4.0] - 2025-12-30

### Breaking Changes

**Tool consolidation** - Reduced from 40 to 37 tools:

| Removed | Replacement |
|---------|-------------|
| `play_track` | `play(track="...")` |
| `play_playlist` | `play(playlist="...")` |
| `get_music_videos` | `search_catalog(types="music-videos")` |
| `get_storefronts` | `config(action="list-storefronts")` |
| `seek_to_position` | `playback_control(action="seek", seconds=...)` |

### Added

- **Unified `play` tool** - Play tracks, playlists, or albums with one tool:
  ```python
  play(track="Hey Jude")                    # play a track
  play(playlist="Road Trip", shuffle=True)  # shuffle a playlist
  play(album="Abbey Road", artist="Beatles") # play an album
  ```

- **Album playback** - New `album` parameter in `play` tool

- **Music video search in catalog** - `search_catalog(types="music-videos")` or leave query empty for featured videos

### Changed

- **Enhanced `get_now_playing`** - Now includes player state (playing/paused/stopped)

- **Expanded preference scope**:
  - `clean_only` now works in `search_library` and `browse_library` (was only `search_catalog`)
  - `fetch_explicit` now works in `search_library` and `browse_library` (was only `get_playlist_tracks`)

### Fixed

- **Documentation** - Removed ghost tool `get_player_state` that never existed in code

## [0.3.0] - 2025-12-29

### Breaking Changes

This release introduces a **unified parameter architecture** where entity parameters (track, album, artist) accept any format with automatic detection. Old parameter names are replaced:

| Tool | Old Parameters | New Parameters |
|------|----------------|----------------|
| `add_to_playlist` | `ids`, `track_name`, `tracks` | `track`, `album` |
| `add_to_library` | `ids`, `track_name`, `tracks` | `track`, `album` |
| `remove_from_playlist` | `ids`, `track_name`, `tracks` | `track` |
| `remove_from_library` | `ids`, `track_name`, `tracks` | `track` |
| `rating` | `song_id`, `track_name` | `track` |
| `play_track` | `track_name` | `track` |
| `get_album_tracks` | `album_id` | `album` |
| `get_artist_details` | `artist_name` | `artist` |
| `get_artist_top_songs` | `artist_name` | `artist` |
| `get_similar_artists` | `artist_name` | `artist` |

### Added

- **Universal input detection** - All entity parameters auto-detect format:
  - JSON array: `track='[{"name":"Hey Jude","artist":"Beatles"}]'`
  - Prefixed IDs: `track="i.ABC123"` (library), `playlist="p.XYZ789"`
  - CSV names: `track="Hey Jude, Let It Be"`
  - Catalog IDs: `track="1440783617"` (10+ digits)
  - Persistent IDs: `track="ABC123DEF456"` (12+ hex chars)
  - Names: `track="Hey Jude"` (triggers search)

- **Album support for playlists** - Add entire albums to playlists:
  ```python
  add_to_playlist(playlist="Road Trip", album="Abbey Road", artist="Beatles")
  add_to_playlist(playlist="Mix", album="1440783617")  # by catalog ID
  ```

- **Album by name lookup** - `get_album_tracks` now accepts album names:
  ```python
  get_album_tracks(album="Abbey Road", artist="Beatles")  # search by name
  get_album_tracks(album="1440783617")  # catalog ID still works
  get_album_tracks(album="l.ABC123")    # library ID still works
  ```

- **Artist by ID** - `get_artist_details`, `get_artist_top_songs`, `get_similar_artists` now accept catalog IDs:
  ```python
  get_artist_details(artist="136975")       # by catalog ID
  get_artist_details(artist="The Beatles")  # by name still works
  ```

- **Extended cache** - Cache now stores albums and name index:
  - Cache file renamed from `track_cache.json` to `cache.json`
  - Stores album metadata (name, artist, track count, year)
  - Name index for reverse lookups (name+artist → ID)
  - Automatic migration from legacy format

### Changed

- **Simplified API surface** - Each tool now has 1-2 main parameters instead of 3-5 mutually exclusive ones
- **Consistent naming** - All tools use `track`, `album`, `artist` parameter names consistently
- **Detection order priority**: JSON → prefixed ID → CSV → catalog ID → persistent ID → name

### Migration Guide

```python
# Before (0.2.x)
add_to_playlist(playlist_name="Mix", track_name="Hey Jude", artist="Beatles")
add_to_playlist(playlist_name="Mix", ids="1440783617")
add_to_playlist(playlist_name="Mix", tracks='[{"name":"Hey Jude","artist":"Beatles"}]')

# After (0.3.0) - all equivalent, auto-detected
add_to_playlist(playlist="Mix", track="Hey Jude", artist="Beatles")
add_to_playlist(playlist="Mix", track="1440783617")
add_to_playlist(playlist="Mix", track='[{"name":"Hey Jude","artist":"Beatles"}]')

# New: add albums to playlists
add_to_playlist(playlist="Mix", album="Abbey Road", artist="Beatles")

# New: get album tracks by name
get_album_tracks(album="Abbey Road", artist="Beatles")
```

### Documentation

- **Restructured README** - Quick Start (macOS) section now comes first with zero-config setup
- **Clearer platform guidance** - Windows/Linux users directed to API Setup section
- **Better usage examples** - Organized by category (playlist management, discovery, API features)
- **MCP link added** - Links to modelcontextprotocol.io for newcomers
- **Token expiration clarity** - Notes that warnings appear 30 days before expiration

## [0.2.10] - 2025-12-23

### Fixed

- **`auto_search` now works with batch add** - JSON tracks mode (`tracks='[...]'`) now falls back to auto_search when tracks aren't found in library
- **DRY refactoring** - Consolidated auto_search logic into `_auto_search_and_add_to_playlist()` helper (~100 lines reduced to ~15)

### Changed

- **Unified playlist parameters** - All playlist-related tools now accept a single `playlist` parameter:
  - Starts with `p.` → playlist ID (API mode, cross-platform)
  - Otherwise → playlist name (AppleScript, macOS only)
  - Affects: `get_playlist_tracks`, `add_to_playlist`, `remove_from_playlist`, `search_playlist`
  - `copy_playlist` uses `source` parameter with same auto-detection
- **Unified ID parameters** - Simplified ID parameters across tools:
  - `add_to_library`: `catalog_ids` → `ids` (auto-detects catalog/library IDs), added `type` param for albums
  - `add_to_playlist`: `track_ids` → `ids` (auto-detects catalog/library IDs)
  - `remove_from_playlist`: `track_ids` → `ids`
  - `remove_from_library`: `track_ids` → `ids`
- **ID auto-detection** - New `_detect_id_type()` helper identifies ID types:
  - All digits → catalog ID
  - Starts with `i.` → library ID
  - Starts with `p.` → playlist ID
  - Otherwise → persistent ID (hex)
- **README features table sorted** - Double-checkmark features (both macOS and API) now listed first

## [0.2.9] - 2025-12-23

### Added

- **Audit logging for destructive operations** - All library/playlist modifications now logged:
  - Logs to `~/.cache/applemusic-mcp/audit_log.jsonl`
  - Operations: add_to_library, remove_from_library, add_to_playlist, remove_from_playlist, create_playlist, delete_playlist, copy_playlist, rating
  - View via `config(action="audit-log")`, clear via `config(action="clear-audit-log")`
  - Includes undo hints for recovery guidance
- **JSON `tracks` parameter for `add_to_playlist`** - Consistent with `add_to_library`:
  - `add_to_playlist(playlist_name="Mix", tracks='[{"name":"Song","artist":"Artist"},...]')`
  - Supports multiple tracks with different artists in a single call

### Changed

- **Renamed `system` tool to `config`** - Better reflects purpose (configuration, preferences, cache management)
- **Improved limit parameter docs** - Now says "default: all" instead of "0 = all" to discourage explicit 0
- **DRY refactoring of track operations** - Extracted common patterns into reusable helpers:
  - `_split_csv()` - Consistent comma-separated value parsing
  - `_parse_tracks_json()` - Standardized JSON tracks array parsing
  - `_validate_track_object()` - Unified track object validation
  - `_find_matching_catalog_song()` - Shared catalog search with partial matching
  - `_build_track_results()` - Consistent success/error message formatting
  - Reduced code duplication across `add_to_library`, `add_to_playlist`, `remove_from_library`, `remove_from_playlist`

## [0.2.8] - 2025-12-23

### Added

- **Configurable storefront via system tool** - Set your Apple Music region:
  - `system(action="set-pref", preference="storefront", string_value="gb")` - Set UK region
  - `system(action="list-storefronts")` - List all available regions
  - `system()` - Now shows current storefront in preferences
  - Supports all Apple Music storefronts (175+ countries)
  - Enables non-US users to get localized catalog results
- **`auto_search` preference now displayed** in `system()` info output

### Changed

- **Thread-safe track cache** - Singleton initialization now uses double-check locking pattern
- **Named constants for play_track retry loop** - Magic numbers extracted to descriptive constants:
  - `PLAY_TRACK_INITIAL_DELAY`, `PLAY_TRACK_RETRY_DELAY`, `PLAY_TRACK_MAX_ATTEMPTS`, `PLAY_TRACK_READD_AT_ATTEMPT`
- **Consolidated storefront functionality** - `get_storefronts()` tool still available, but `system(action="list-storefronts")` preferred

### Fixed

- **Request timeouts** - All 51 API calls now have 30-second timeout (prevents indefinite hangs)
- **Cache error logging** - Track cache load/save errors now logged instead of silently swallowed

## [0.2.7] - 2025-12-22

### Changed

- **`check_playlist` → `search_playlist`** - Renamed for clarity and enhanced:
  - Uses native AppleScript search on macOS (fast, same as Music app search field)
  - API path manually filters tracks (cross-platform support maintained)
  - Now searches album field in addition to name/artist
  - Better name reflects actual functionality

### Fixed

- **Album search** - API path now searches album field (was missing)

## [0.2.6] - 2025-12-22

### Added

- **Auto-search feature** - Automatically find and add tracks from catalog when not in library (opt-in):
  - New `auto_search` parameter for `add_to_playlist` (uses preference if not specified, default: false)
  - When track not in library: searches catalog → adds to library → adds to playlist (one operation!)
  - Uses optimized API flow: `/catalog/{catalog_id}/library` to get library ID instantly (no retry loop)
  - Includes API verification to confirm track added to playlist
  - Reduces 7-step manual process to 1 call
  - Set via `system(action="set-pref", preference="auto_search", value=True)` to enable by default
- **New `auto_search` preference** - Control automatic catalog search behavior (default: false, respects user choice)

### Changed

- **Partial matching everywhere** - ALL track operations now support partial name matching:
  - `add_track_to_playlist` - Changed from `is` to `contains` (CRITICAL FIX)
  - `love_track` - Now supports partial matching
  - `dislike_track` - Now supports partial matching
  - `get_rating` - Now supports partial matching
  - `set_rating` - Now supports partial matching
  - No more frustration with exact titles like "Song (Live at Venue, Date)"
- **Optimized auto_search flow** - Minimal API calls:
  1. Search catalog → get catalog_id
  2. Add to library via API
  3. Get library ID from `/catalog/{catalog_id}/library` (instant!)
  4. Get playlist ID from name (AppleScript, local)
  5. Add to playlist via API
  6. Verify via API

### Fixed

- **Critical:** `add_to_playlist` with track names required EXACT match (now uses `contains`)
  - Example: "Give Up the Funk" now finds "Give up the Funk (Tear the Roof Off the Sucker)"
  - Fixes the user's exact scenario where 7 attempts were needed to add one song

## [0.2.5] - 2025-12-22

### Added

- **Track metadata caching system** - Intelligent caching for stable track metadata:
  - Dedicated `track_cache.py` module with clean interface
  - Multi-ID indexing: caches by persistent IDs (AppleScript), library IDs (API), and catalog IDs (universal)
  - Stores stable fields only: explicit status and ISRC
  - Eliminates redundant API calls (90% reduction for repeated checks)
  - Extensible design for adding more stable fields
  - 10-20x speedup for subsequent playlist explicit status checks
  - Cache persisted to `~/.cache/applemusic-mcp/track_cache.json`
- **Explicit content tracking** - Comprehensive explicit status throughout:
  - `[Explicit]` marker in all track output formats (text, JSON, CSV)
  - `fetch_explicit=True` parameter for `get_playlist_tracks()` to fetch explicit status via API
  - `clean_only=True` parameter for `search_catalog()` to filter explicit content
  - AppleScript mode shows "Unknown" by default (contentRating not exposed)
  - API mode shows accurate "Yes"/"No" explicit status
- **User preferences system** - Set defaults for common parameters:
  - `fetch_explicit` - always fetch explicit status (default: false)
  - `reveal_on_library_miss` - auto-reveal catalog tracks in Music app (default: false)
  - `clean_only` - filter explicit content in catalog searches (default: false)
  - Set via `system(action="set-pref", preference="...", value=True/False)`
  - View current preferences via `system()` info display
  - Stored in `~/.config/applemusic-mcp/config.json`
  - See `config.example.json` for format
- **New `system` tool** - Comprehensive system configuration and cache management:
  - `system()` - show preferences, track cache stats, and export files
  - `system(action="set-pref", ...)` - update preferences
  - `system(action="clear-tracks")` - clear track metadata cache separately
  - `system(action="clear-exports")` - clear CSV/JSON export files separately
  - Shows cache sizes, entry counts, file ages
  - Replaces old `cache` tool with more intuitive naming
- **Partial playlist matching** - Smart playlist name matching with exact-match priority:
  - "Jack & Norah" now finds "🤟👶🎸 Jack & Norah"
  - Exact matches always prioritized over partial matches
  - Applied to all playlist operations via `_find_playlist_applescript()` helper
- **Comprehensive documentation**:
  - `CACHING.md` - Multi-ID caching architecture, E2E flow, performance analysis
  - `COMPOSITE_KEYS.md` - Why we use composite keys for AppleScript ↔ API bridging
  - `config.example.json` - Example configuration with preferences
- **Test suite expansion** - 30 new tests (120 total: 26 track cache, 4 preferences)

### Changed

- **Error messages cleaned up** - Removed redundant playlist names from error responses
- **Helpful guidance** - Error messages suggest `search_catalog` + `add_to_library` workflow when tracks not found
- **Tool parameters** - `fetch_explicit`, `clean_only`, `reveal` now use `Optional[bool]` to support user preferences
- **Asymmetry fixes** - Systematic review and fixes for add/remove inconsistencies:
  - **`remove_from_playlist` enhanced**:
    - **Partial matching fixed** - Now uses `contains` instead of `is` (no more exact match requirement!)
    - **Array support** - Remove multiple tracks at once (comma-separated names, IDs, or JSON array)
    - **ID-based removal** - Remove by persistent IDs via `track_ids` parameter
    - **Better output** - Shows removed count, lists successes and failures separately
  - **`remove_from_library` enhanced** - Now matches `add_to_library` capabilities:
    - **Array support** - Remove multiple tracks: `track_name="Song1,Song2"` or `track_ids="ID1,ID2"`
    - **ID-based removal** - Remove by persistent IDs via `track_ids` parameter
    - **JSON array support** - Different artists: `tracks='[{"name":"Hey Jude","artist":"Beatles"}]'`
    - **Flexible formats** - Same 5 modes as `remove_from_playlist`
  - **`search_library` parameter standardized** - Renamed `search_type` → `types` to match `search_catalog`
  - **`copy_playlist` name support** - Added `source_playlist_name` parameter for macOS users (matches other playlist operations)

## [0.2.4] - 2025-12-21

### Added

- **No-credentials mode on macOS** - Many features now work without API setup:
  - `get_library_playlists` - Lists playlists via AppleScript first
  - `create_playlist` - Creates playlists via AppleScript first
  - `browse_library(songs)` - Lists library songs via AppleScript first
  - New `get_library_songs()` AppleScript helper function
- **Test cleanup** - Automatically removes test playlists after test runs

### Changed

- **AppleScript-first approach** - macOS tools try AppleScript before falling back to API
- **README** - Documents no-credentials mode, simplified requirements

## [0.2.3] - 2025-12-21

### Changed

- **format=csv** - Inline CSV output in response (in addition to text/json/none)
- **export=none** - Consistent "none" default instead of empty string
- **play_track response prefixes** - Shows `[Library]`, `[Catalog]`, or `[Catalog→Library]` to indicate source
- **Featured artist matching** - `play_track` matches "Bruno Mars" in "Uptown Funk (feat. Bruno Mars)"
- **Catalog song reveal** - `reveal=True` opens song in Music app via `music://` URL (user clicks play)
- **Add-to-library retry** - Retries add at 5s mark in case first attempt silently failed
- **URL validation** - `open_catalog_song` validates Apple Music URLs before opening

## [0.2.2] - 2025-12-20

### Added

- **MCP Resources for exports** - Claude Desktop can now read exported files:
  - `exports://list` - List all exported files
  - `exports://{filename}` - Read a specific export file

### Changed

- **Tool consolidation (55 → 42 tools)** - The answer to life, the universe, and everything:
  - `browse_library(type=songs|albums|artists|videos)` - merged 4 library listing tools
  - `rating(action=love|dislike|get|set)` - merged 5 rating tools into one
  - `playback_settings(volume, shuffle, repeat)` - merged 4 settings tools
  - `search_library` - now uses AppleScript on macOS (faster), API fallback elsewhere
  - `airplay` - list or switch devices (merged 2 tools)
  - `cache` - view or clear cache (merged 2 tools)
- **Unified output format** - List tools now support:
  - `format="text"` (default), `"json"`, `"csv"`, or `"none"` (export only)
  - `export="none"` (default), `"csv"`, or `"json"` to write files
  - `full=True` to include all metadata
- **Extended iCloud sync wait** - `play_track` now waits ~10s for add-to-library sync (was ~7s)

## [0.2.1] - 2025-12-20

### Added

- **`remove_from_library`** - Remove tracks from library via AppleScript (macOS only)
- **`check_playlist`** - Quick check if song/artist is in a playlist (cross-platform)
- **`set_airplay_device`** - Switch audio output to AirPlay device (macOS)
- **`_rate_song_api`** - Internal helper for rating songs via API

### Changed

- **`love_track` / `dislike_track` now cross-platform** - Uses AppleScript on macOS, falls back to API elsewhere

- **play_track enhanced** - Now properly handles catalog tracks not in library:
  - `add_to_library=True`: Adds song to library first, then plays
  - `reveal=True`: Opens song in Music app for manual play
  - Clear messaging about AppleScript's inability to auto-play non-library catalog tracks
- **Code refactoring** - Extracted `_search_catalog_songs()` and `_add_songs_to_library()` internal helpers to reduce duplication

### Fixed

- Fixed `play_track` calling non-existent `reveal_in_music` (now correctly calls `reveal_track`)
- Replaced misleading `play_catalog_track` AppleScript function with honest `open_catalog_song`

## [0.2.0] - 2024-12-20

### Added

- **AppleScript integration for macOS** - 16 new tools providing capabilities not available via REST API:
  - Playback control: `play_track`, `play_playlist`, `playback_control`, `get_now_playing`, `seek_to_position`
  - Volume/settings: `set_volume`, `get_volume_and_playback`, `set_shuffle`, `set_repeat`
  - Playlist management: `remove_from_playlist`, `delete_playlist`
  - Track ratings: `love_track`, `dislike_track`
  - Other: `reveal_in_music`, `get_airplay_devices`, `local_search_library`
- **Clipped output tier** - New tier between Full and Compact that truncates long names while preserving all metadata fields (album, year, genre)
- **Platform Capabilities table** in README showing feature availability across macOS and Windows/Linux
- **Cross-platform OS classifiers** in pyproject.toml (Windows, Linux in addition to macOS)
- **Security documentation** for AppleScript input escaping

### Changed

- Renamed package from `mcp-applemusic-api` to `mcp-applemusic` (repo rename pending)
- Updated README with comprehensive macOS-only tools documentation
- Improved input sanitization: backslash escaping added to prevent edge cases in AppleScript strings
- Test count increased from 48 to 71 tests

### Fixed

- Exception handling in AppleScript module: replaced bare `except:` with specific exception types

## [0.1.0] - 2024-12-15

### Added

- Initial release with REST API integration
- 33 cross-platform MCP tools for Apple Music
- Playlist management (create, add tracks, copy)
- Library browsing and search
- Catalog search and recommendations
- Tiered output formatting (Full, Compact, Minimal)
- CSV export for large track listings
- Developer token generation and user authorization
- Comprehensive test suite (48 tests)

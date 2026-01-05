# Apple Music MCP - Development Plan

Living document tracking current work, known issues, and future improvements.

---

## 🔥 Current Work: Fuzzy Matching & Resolution Refactor

**Status**: In Progress
**Started**: 2026-01-04
**Priority**: High (blocking user workflows)

### Problem Statement

Multiple critical bugs discovered during fuzzy matching implementation:

1. **Resolution returns wrong data structure** - tuple instead of object breaks when functions need multiple identifiers
2. **Missing playlist identifiers** - need API ID, AppleScript name, AND persistent ID
3. **Fuzzy matching incomplete** - only works for playlists, should work for tracks/artists/albums
4. **Duplicate results** - search returns duplicates (e.g., "2 Songs" shows 3 tracks)
5. **Reporting not visible** - fuzzy match transformations not appearing in output

### Design Decision: Resolved Objects

**ALL entity resolution should return objects, not tuples.**

```python
@dataclass
class ResolvedPlaylist:
    api_id: str | None              # p.XXX for API calls
    applescript_name: str | None    # "Jack & Norah" for AppleScript
    persistent_id: str | None       # 583528883966122E from AppleScript
    raw_input: str                  # What user typed
    error: str | None
    fuzzy_match: FuzzyMatchResult | None
```

**Why**: Functions have different needs. `get_playlist_tracks()` prefers API ID for performance, `remove_from_playlist()` REQUIRES AppleScript name. Resolution should provide ALL available identifiers.

### Implementation Phases

#### Phase 1: Fix Immediate Breakage ✅
- [x] Create `ResolvedPlaylist` dataclass with all 3 ID types
- [x] Refactor `_resolve_playlist()` to return object
- [x] Update ALL callers (get_playlist_tracks, add_to_playlist, remove_from_playlist, copy_playlist, search_playlist)
  - [x] get_playlist_tracks
  - [x] search_playlist
  - [x] remove_from_playlist
  - [x] add_to_playlist
  - [x] copy_playlist
- [x] Add type hints to all `resolved: ResolvedPlaylist` variables
- [x] Fix CRITICAL variable shadowing bug in `remove_from_playlist` (line 5076)
- [x] Fix CRITICAL type annotation bug in `_normalize_with_tracking` (line 149)
- [DEFERRED] Add persistent ID resolution from AppleScript (not blocking)
- [x] Fix: `add_to_playlist` bypassed fuzzy matching when AppleScript mode preferred
- [x] Test: `add_to_playlist("Jack and Norah", ...)` works ✅
- [x] Test: `remove_from_playlist("Jack and Norah", ...)` works ✅

#### Phase 2: Deduplication
- [ ] Add deduplication to `search_catalog()` by track ID
- [ ] Add deduplication to `search_library()` by track ID
- [ ] Test: `search_catalog("Für Elise Barenboim")` shows correct count

#### Phase 3: DRY Fuzzy Matching
- [ ] Extract `fuzzy_match_entities(query, candidates, name_field)` generic function
- [ ] Apply to playlists (replace inline code)
- [ ] Create `ResolvedTrack`, `ResolvedArtist`, `ResolvedAlbum` dataclasses
- [ ] Apply fuzzy matching to track resolution
- [ ] Apply fuzzy matching to artist resolution
- [ ] Apply fuzzy matching to album resolution

#### Phase 4: Reporting & Polish
- [ ] Debug why `_format_fuzzy_match()` output not appearing
- [ ] Ensure all tools show fuzzy transformations
- [ ] Update CHANGELOG.md
- [ ] Add tests for fuzzy matching edge cases

### Progress Log

**2026-01-04 PM**:
- ✅ Implemented `_normalize_with_tracking()` with transformations
- ✅ Added fuzzy matching to `_find_api_playlist_by_name()`
- ✅ Created `FuzzyMatchResult` dataclass
- ✅ Added `_format_fuzzy_match()` formatter
- ✅ Updated playlist tools to accept fuzzy match (tuple pattern)
- ❌ DISCOVERED: Tuple return breaks remove_from_playlist
- ❌ DISCOVERED: Missing persistent ID in resolution
- ❌ DISCOVERED: Search result duplicates
- ❌ DISCOVERED: Fuzzy reporting not showing
- 📝 Created this PLAN.md
- ✅ Created `ResolvedPlaylist` dataclass with api_id, applescript_name, persistent_id fields
- ✅ Refactored `_resolve_playlist()` to return `ResolvedPlaylist` object
- ✅ Updated `get_playlist_tracks()` to use resolved object
- ✅ Updated `search_playlist()` to use resolved object
- ✅ Updated `remove_from_playlist()` to use resolved object (fixes fuzzy name bug!)
- ✅ Updated `add_to_playlist()` to use resolved object
- ✅ Updated `copy_playlist()` to use resolved object
- ✅ **PHASE 1 IMPLEMENTATION COMPLETE**: All 5 callers updated to use ResolvedPlaylist pattern
- ✅ Code review via superpowers:code-reviewer agent
- ✅ Fixed CRITICAL: Variable shadowing in `remove_from_playlist` (renamed to `track_resolved`)
- ✅ Fixed CRITICAL: Type annotation in `_normalize_with_tracking` (returns list, not str)
- ✅ Added type hints to all `resolved: ResolvedPlaylist` variables
- ✅ Updated PLAN.md to mark `persistent_id` as deferred (not blocking)
- ✅ Added comprehensive integration tests (fuzzy matching, regression tests, performance)
- ✅ Fixed fuzzy matching priority bug (exact match now preferred over fuzzy)
- ✅ Updated all existing tests for new ResolvedPlaylist API
- ✅ **PERFORMANCE OPTIMIZATION**: Refactored fuzzy matching to 3-pass algorithm:
  - Pass 1: Exact match (O(n), no normalization)
  - Pass 2: Partial match (O(n), substring only)
  - Pass 3: Fuzzy match (expensive normalization, only if Pass 1 & 2 fail)
- ✅ **ALL 62 TESTS PASSING** (including performance test)
- ✅ Fixed CRITICAL: `add_to_playlist` bypassed `_resolve_playlist()` when AppleScript mode preferred
  - "Jack and Norah" was passed raw to AppleScript, never fuzzy-matched to "🤟👶🎸 Jack & Norah"
  - Now always resolves first, then decides API vs AppleScript mode

---

## 🐛 Known Issues

### High Priority
- [ ] Search results show duplicates (wrong count in header)
- [ ] Fuzzy match transformations not visible in output (might be working - needs verification)
- [ ] Fuzzy matching only works for playlists

### Medium Priority
- [ ] No fuzzy matching for tracks/artists/albums
- [DEFERRED] `_resolve_playlist()` doesn't fetch persistent ID (not blocking - can add later if needed)

### Fixed (Ready for Testing)
- [x] `remove_from_playlist()` crash when using fuzzy-matched playlist names - **FIXED** (variable shadowing bug)
- [x] `add_to_playlist` bypassed `_resolve_playlist()` for AppleScript mode - **FIXED** (now always resolves first)

### Low Priority
- [ ] (None currently)

---

## 📋 Backlog

Ideas and improvements for future consideration:

### Features
- [ ] Batch operations (add multiple tracks to multiple playlists)
- [ ] Playlist merge/diff operations
- [ ] Smart playlist suggestions based on listening history
- [ ] Export playlists to other formats (Spotify, CSV, etc.)

### Performance
- [ ] Cache playlist → ID mappings
- [ ] Parallel API requests where possible
- [ ] Rate limiting / request batching

### Developer Experience
- [ ] Better error messages with suggested fixes
- [ ] Retry logic for transient API failures
- [ ] Progress indicators for long operations

### Testing
- [ ] Integration tests for fuzzy matching
- [ ] Test coverage for AppleScript fallbacks
- [ ] Performance benchmarks

---

## 📝 Design Principles

### Resolution Pattern
**All `_resolve_*()` functions should return objects with ALL available identifiers.**

Benefits:
- Functions pick what they need
- Easier to extend (add new ID types)
- Better type safety
- Clearer intent

### Fuzzy Matching
**Apply uniformly across all entity types using shared implementation.**

Transformations (in order):
1. Lowercase & strip
2. Remove diacritics (café → cafe)
3. Strip leading articles (The Beatles → Beatles)
4. Normalize "and" ↔ "&"
5. Normalize music abbreviations (feat. → ft, w/ → with)
6. Remove quotes/apostrophes
7. Hyphens → spaces
8. Remove emojis/special chars
9. Collapse whitespace

### Error Handling
**Errors should be actionable and include suggestions when possible.**

Good: `"Track not found. Try searching with search_library() first."`
Bad: `"Error: Not found"`

---

## 🎯 Current Session TODO

(Synced with TodoWrite)

1. [in_progress] Fix duplicate track display bug in search results
2. [pending] Apply fuzzy matching to tracks/artists/albums (not just playlists)
3. [pending] Refactor fuzzy matching to be DRY and reusable
4. [pending] Show fuzzy match reporting in output
5. [pending] Update CHANGELOG.md

---

## 📚 Reference

### File Structure
- `src/applemusic_mcp/server.py` - Main MCP server, all tools
- `src/applemusic_mcp/applescript.py` - AppleScript integration (macOS only)
- `src/applemusic_mcp/track_cache.py` - Persistent cache for explicit status
- `src/applemusic_mcp/audit_log.py` - Action logging
- `tests/` - Test suite

### Key Functions
- `_resolve_playlist()` - Playlist resolution (needs refactor)
- `_resolve_track()` - Track resolution (needs fuzzy matching)
- `_resolve_album()` - Album resolution (needs fuzzy matching)
- `_normalize_with_tracking()` - Fuzzy normalization with transform tracking
- `_find_api_playlist_by_name()` - Playlist fuzzy matching (API)

### Data Flow: Playlist Operations

```
User input → _resolve_playlist() → ResolvedPlaylist object
                                    ↓
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
            get_playlist_tracks  add_to_playlist  remove_from_playlist
            (uses .api_id)       (uses both)     (uses .applescript_name)
```

---

**Last Updated**: 2026-01-04
**Next Review**: After Phase 1 completion

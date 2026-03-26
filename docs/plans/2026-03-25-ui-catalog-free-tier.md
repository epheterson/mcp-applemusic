# UI Catalog Free Tier + Library Safety System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete safety system (snapshot, diff, audit) and a free-tier catalog experience (search, add to library, add to playlist) that works entirely through Music.app UI scripting — no API key needed.

**Architecture:** Three layers: (1) Library safety system with snapshots, diffs, and enhanced audit logging for all operations. (2) UI automation primitives for search/hover/click in `applescript.py`. (3) Server-layer integration that exposes these as MCP tools with automatic safety checks.

**Tech Stack:** AppleScript, System Events UI scripting, CoreGraphics JXA (for hover/mouse), existing `run_applescript()` infrastructure.

**Safety protocol:** Every task that modifies library state must: take a snapshot before, verify after, and undo on unexpected changes. All testing uses a dedicated test playlist (`_TEST_UI_AUTOMATION_DELETE_ME_`) that is created at the start and deleted at the end.

---

### Task 1: Fix and validate library_snapshot() and library_diff()

The snapshot/diff code exists in applescript.py but hasn't been tested. Fix the AppleScript return format issues and validate it works.

**Files:**
- Modify: `src/applemusic_mcp/applescript.py` (snapshot/diff functions, ~line 1243)
- Test: `tests/test_applescript.py`

**Step 1: Run the snapshot function and fix any issues**

Run: `./venv/bin/python -c "from src.applemusic_mcp import applescript as asc; ok, snap = asc.library_snapshot(); print(f'OK: {ok}, tracks: {snap.get(\"track_count\", \"?\")}'); print(f'playlists: {len(snap.get(\"playlists\", {}))}'); print(f'playback: {snap.get(\"playback\", {})}')"`

Expected: OK: True, tracks: ~12294, playlists: ~30+, playback state dict

If it fails, debug the AppleScript return format and fix.

**Step 2: Test the diff function**

Run two snapshots back-to-back (no changes between them), diff should be clean:
```python
ok1, snap1 = asc.library_snapshot()
ok2, snap2 = asc.library_snapshot()
diff = asc.library_diff(snap1, snap2)
assert diff["is_clean"] == True
```

**Step 3: Test diff detects changes**

Create a test playlist, take snapshot, diff with original — should detect the new playlist:
```python
ok1, snap1 = asc.library_snapshot()
asc.create_playlist("_TEST_SNAPSHOT_DELETE_ME_")
ok2, snap2 = asc.library_snapshot()
diff = asc.library_diff(snap1, snap2)
assert "_TEST_SNAPSHOT_DELETE_ME_" in diff["playlists_added"]
asc.delete_playlist("_TEST_SNAPSHOT_DELETE_ME_")
```

**Step 4: Write unit tests**

Add to `tests/test_applescript.py`:
```python
class TestLibrarySnapshot:
    def test_snapshot_returns_valid_structure(self):
        ok, snap = asc.library_snapshot()
        assert ok is True
        assert isinstance(snap["track_count"], int)
        assert snap["track_count"] > 0
        assert isinstance(snap["playlists"], dict)
        assert len(snap["playlists"]) > 0
        assert isinstance(snap["playback"], dict)
        assert "player_state" in snap["playback"]

    def test_diff_clean_when_no_changes(self):
        ok1, snap1 = asc.library_snapshot()
        ok2, snap2 = asc.library_snapshot()
        diff = asc.library_diff(snap1, snap2)
        assert diff["is_clean"] is True
        assert diff["track_count_change"] == 0
        assert diff["playlists_added"] == []
        assert diff["playlists_removed"] == []

    def test_diff_detects_playlist_creation(self):
        test_name = "_TEST_SNAPSHOT_DIFF_"
        ok1, snap1 = asc.library_snapshot()
        asc.create_playlist(test_name)
        ok2, snap2 = asc.library_snapshot()
        diff = asc.library_diff(snap1, snap2)
        assert test_name in diff["playlists_added"]
        assert diff["is_clean"] is False
        # Cleanup
        asc.delete_playlist(test_name)
```

**Step 5: Run tests**

Run: `./venv/bin/python -m pytest tests/test_applescript.py::TestLibrarySnapshot -v`
Expected: 3 passed

**Step 6: Commit**

```bash
git add src/applemusic_mcp/applescript.py tests/test_applescript.py
git commit -m "feat: library snapshot and diff for integrity checking"
```

---

### Task 2: Enhance audit_log for playback operations

Currently only `play_url` is logged. Add logging for all playback actions: play (track/playlist/album), control (pause/skip/etc), settings (volume/shuffle/repeat), airplay.

**Files:**
- Modify: `src/applemusic_mcp/server.py` (playback tool handlers)

**Step 1: Add logging to _playback_play for non-URL paths**

After successful play of track/playlist/album, log it:
- Track: `audit_log.log_action("play_track", {"track": name, "artist": artist, "source": source})`
- Playlist: `audit_log.log_action("play_playlist", {"playlist": name, "shuffle": shuffle})`
- Album: `audit_log.log_action("play_album", {"album": name, "artist": artist})`

Find each successful return path in `_playback_play` and add logging before the return.

**Step 2: Add logging to _playback_control**

After successful control action (pause, play, stop, next, previous, seek):
`audit_log.log_action("playback_control", {"action": control, "seconds": seconds})`

**Step 3: Add logging to _playback_settings**

After successful settings change:
`audit_log.log_action("playback_settings", {"volume": volume, "shuffle": shuffle_mode, "repeat": repeat})`

Only log the fields that were actually changed (non-default values).

**Step 4: Add logging to _playback_airplay**

After successful airplay switch:
`audit_log.log_action("airplay", {"device": device_name})`

**Step 5: Run full test suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: All tests pass (no behavior change, just added logging)

**Step 6: Commit**

```bash
git add src/applemusic_mcp/server.py
git commit -m "feat: audit log all playback operations (control, settings, airplay)"
```

---

### Task 3: Wire snapshot/diff into MCP as a user-facing tool

Add snapshot/diff as actions on the existing `config` tool, or as a new lightweight tool.

**Files:**
- Modify: `src/applemusic_mcp/server.py` (config tool or new tool)

**Step 1: Add `snapshot` and `diff` actions to the config tool**

- `config(action="snapshot")` — takes and stores a snapshot, returns summary
- `config(action="diff")` — compares current state to last snapshot, returns diff
- `config(action="snapshot-history")` — shows list of saved snapshots

Store snapshots in `~/.cache/applemusic-mcp/snapshots/` as timestamped JSON files.

**Step 2: Implement auto-snapshot on first tool call**

On server startup or first tool invocation, automatically take a baseline snapshot. Store it as `snapshots/baseline-{timestamp}.json`.

**Step 3: Test manually**

Run snapshot, make a change, run diff, verify it shows the change.

**Step 4: Commit**

```bash
git add src/applemusic_mcp/server.py
git commit -m "feat: user-facing library snapshot and diff via config tool"
```

---

### Task 4: UI Search — type in Music.app search field and read results

Build the UI search primitive in applescript.py.

**Files:**
- Modify: `src/applemusic_mcp/applescript.py`
- Test: `tests/test_applescript.py`

**Step 1: Implement `ui_search_catalog(query)` in applescript.py**

```python
def ui_search_catalog(query: str) -> tuple[bool, list[dict]]:
    """Search the Apple Music catalog via Music.app's search field.

    Types the query into the search field, waits for results, then
    parses track groups from the results area.

    Returns list of {name, artist, duration} dicts.
    """
```

Implementation approach:
1. Click search field: `text field 1 of UI element 1 of row 1 of outline 1 of scroll area 1`
2. Clear + type query via keystroke
3. Wait for results to load (poll for content in scroll area 2)
4. Parse groups from results: each group has description = track name, static texts for details
5. Clear search after reading results

**Step 2: Test with known queries**

Test searches: "Radiohead", "Taylor Swift", "Beethoven Symphony" — verify we get results back with track names and artists.

**Step 3: Write unit test (mocked)**

```python
class TestUISearchCatalog:
    def test_ui_search_returns_results(self):
        # This is a live integration test
        ok, results = asc.ui_search_catalog("Radiohead")
        assert ok is True
        assert len(results) > 0
        assert "name" in results[0]
```

**Step 4: Commit**

```bash
git add src/applemusic_mcp/applescript.py tests/test_applescript.py
git commit -m "feat: UI catalog search via Music.app search field"
```

---

### Task 5: UI Add to Library — hover to reveal button and click it

Build the "Add to Library" primitive that hovers over a track row to reveal the Add to Library button.

**Files:**
- Modify: `src/applemusic_mcp/applescript.py`

**Step 1: Implement `ui_add_to_library(track_name)` in applescript.py**

```python
def ui_add_to_library(track_name: str) -> tuple[bool, str]:
    """Add a track to library via Music.app UI.

    Finds the track in current search results, hovers via CoreGraphics
    to reveal the 'Add to Library' button, and clicks it.

    Must be called after ui_search_catalog() with results visible.
    """
```

Implementation:
1. Find the group whose description contains `track_name`
2. Get its position
3. Ensure Music is frontmost
4. CoreGraphics hover (same `_jxa_mouse_move` pattern as play_url)
5. After hover, find `button desc=Add to Library` in the group
6. Click it
7. Verify button changed (becomes "Added" or disappears)

**Step 2: Test with snapshot verification**

```python
# Take snapshot before
ok1, snap1 = asc.library_snapshot()
# Search and add
asc.ui_search_catalog("some obscure track")
asc.ui_add_to_library("Track Name")
# Wait for sync
time.sleep(5)
# Take snapshot after
ok2, snap2 = asc.library_snapshot()
diff = asc.library_diff(snap1, snap2)
# Verify only the expected change happened
assert diff["track_count_change"] >= 1
# UNDO: remove the track we just added
asc.remove_from_library("Track Name")
```

**Step 3: Commit**

```bash
git add src/applemusic_mcp/applescript.py
git commit -m "feat: UI add-to-library via hover and click"
```

---

### Task 6: UI Add to Playlist — search, add to library, then use backend

The composite operation: search catalog via UI → add to library via UI → wait for sync → add to playlist via existing AppleScript backend.

**Files:**
- Modify: `src/applemusic_mcp/server.py` (or new function in applescript.py)

**Step 1: Implement the composite flow**

This lives in server.py's playback/playlist logic as a fallback when API isn't available:

```python
def _ui_add_to_playlist(playlist_name, track_name, artist=""):
    """Add a catalog track to a playlist via UI automation (no API needed).

    1. Search via UI
    2. Add to library via UI hover+click
    3. Wait for iCloud sync
    4. Add to playlist via existing AppleScript backend
    """
```

**Step 2: Test end-to-end with snapshot verification**

Use the test playlist, add a track, verify via snapshot, remove it.

**Step 3: Commit**

```bash
git add src/applemusic_mcp/server.py
git commit -m "feat: UI-based add to playlist (search, add to library, backend add)"
```

---

### Task 7: Wire UI features into MCP tools

Expose the UI catalog features as MCP tool options that work without API.

**Files:**
- Modify: `src/applemusic_mcp/server.py`

**Step 1: Add UI fallback to search_catalog tool**

When API is not available, fall back to `ui_search_catalog()`.

**Step 2: Add UI fallback to add_to_library**

When API is not available, fall back to `ui_add_to_library()`.

**Step 3: Add UI fallback to add_to_playlist**

When API is not available and track is not in library, fall back to the composite UI flow.

**Step 4: Update README**

Add note about free-tier UI automation capabilities.

**Step 5: Run full test suite + manual validation**

Run: `./venv/bin/python -m pytest tests/ -q`
Manual: test each UI flow with snapshot verification.

**Step 6: Final snapshot comparison**

Take a final snapshot, diff with the baseline from Task 1. Should be completely clean (all test artifacts removed).

**Step 7: Commit**

```bash
git add src/applemusic_mcp/server.py README.md
git commit -m "feat: wire UI catalog features into MCP tools as API-free fallback"
```

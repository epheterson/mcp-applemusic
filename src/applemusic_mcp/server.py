"""MCP server for Apple Music - Cross-platform playlist and library management.

On macOS, additional AppleScript-powered tools are available for playback control,
deleting tracks from playlists, and other operations not supported by the REST API.
"""

import csv
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import requests
from mcp.server.fastmcp import FastMCP

from .auth import get_developer_token, get_user_token, get_config_dir, get_user_preferences
from . import applescript as asc
from .track_cache import get_track_cache, get_cache_dir
from . import audit_log

# Check if AppleScript is available (macOS only)
APPLESCRIPT_AVAILABLE = asc.is_available()

# Max characters for track listing output
MAX_OUTPUT_CHARS = 50000

# Minimum digits for a string to be considered a catalog ID (Apple IDs are 10 digits)
MIN_CATALOG_ID_LENGTH = 9


class EntityType(Enum):
    """Types of Apple Music entities."""
    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"
    GENRE = "genre"


class InputType(Enum):
    """How the input was interpreted."""
    CATALOG_ID = "catalog_id"      # All digits, 9+ chars: "1440783617"
    LIBRARY_ID = "library_id"      # Starts with "i.": "i.ABC123"
    PLAYLIST_ID = "playlist_id"    # Starts with "p.": "p.ABC123"
    ALBUM_ID = "album_id"          # Starts with "l.": "l.ABC123"
    PERSISTENT_ID = "persistent_id"  # 12+ hex chars: "ABC123DEF456"
    NAME = "name"                  # Plain name to search for
    JSON_OBJECT = "json_object"    # From JSON array


@dataclass
class ResolvedInput:
    """Result of resolving a user input to an entity reference."""
    input_type: InputType
    value: str                     # The ID or name
    artist: str = ""               # Artist hint for disambiguation
    raw: str = ""                  # Original input string
    error: str | None = None       # Error message if resolution failed


@dataclass
class FuzzyMatchResult:
    """Result of a fuzzy match operation."""
    matched_name: str              # The actual name that was matched
    query: str                     # The original query
    normalized_query: str          # The normalized query used for matching
    normalized_match: str          # The normalized matched name
    transformations: list[str]     # List of transformations applied
    match_type: str                # "exact", "fuzzy", or "partial"


@dataclass
class ResolvedPlaylist:
    """Result of resolving a playlist parameter.

    Contains all available identifiers for a playlist. Different functions
    need different identifiers:
    - API operations prefer api_id for performance
    - AppleScript operations require applescript_name
    - Some operations need persistent_id

    Resolution should populate as many as possible so callers can choose.
    """
    api_id: str | None = None              # API playlist ID (p.XXX) for REST calls
    applescript_name: str | None = None    # Playlist name for AppleScript operations
    persistent_id: str | None = None       # Hex ID from AppleScript (e.g., 583528883966122E)
    raw_input: str = ""                    # Original input from user
    error: str | None = None               # Error message if resolution failed
    fuzzy_match: FuzzyMatchResult | None = None  # Fuzzy match details if applicable


def truncate(s: str, max_len: int) -> str:
    """Truncate string with ellipsis if longer than max_len."""
    return s[:max_len] + "..." if len(s) > max_len else s


def _deduplicate_by_id(items: list[dict], id_key: str = "id", keep_no_id: bool = False) -> list[dict]:
    """Remove duplicate items based on ID field.

    Args:
        items: List of dicts to deduplicate
        id_key: Key to use for ID lookup (default "id")
        keep_no_id: If True, keep items without an ID (default False)

    Returns:
        List with duplicates removed, preserving order
    """
    seen_ids: set[str] = set()
    unique = []
    for item in items:
        item_id = item.get(id_key, "")
        if item_id:
            if item_id not in seen_ids:
                seen_ids.add(item_id)
                unique.append(item)
        elif keep_no_id:
            unique.append(item)
    return unique


def _format_fuzzy_match(fuzzy: FuzzyMatchResult | None) -> str:
    """Format fuzzy match information for display.

    Args:
        fuzzy: Fuzzy match result or None

    Returns:
        Formatted string describing the fuzzy match, or empty string if None
    """
    if not fuzzy:
        return ""

    parts = [f"\n🔍 Fuzzy match: '{fuzzy.query}' → '{fuzzy.matched_name}'"]

    if fuzzy.match_type == "exact":
        return ""  # Don't show anything for exact matches

    if fuzzy.match_type == "partial":
        parts.append(f"   Match type: Partial substring match")
    elif fuzzy.match_type == "fuzzy":
        parts.append(f"   Match type: Fuzzy match")
        if fuzzy.transformations:
            trans_str = ", ".join(fuzzy.transformations)
            parts.append(f"   Transformations: {trans_str}")
    elif fuzzy.match_type == "fuzzy_partial":
        parts.append(f"   Match type: Fuzzy partial match")
        if fuzzy.transformations:
            trans_str = ", ".join(fuzzy.transformations)
            parts.append(f"   Transformations: {trans_str}")

    return "\n".join(parts)


def _normalize_for_match(s: str) -> str:
    """Normalize string for fuzzy matching.

    Lowercases, removes special chars, collapses whitespace.
    Used for matching AppleScript track names to API track names.
    """
    # Lowercase and strip
    s = s.lower().strip()
    # Remove common variations
    s = s.replace("'", "").replace("'", "").replace("`", "")
    s = s.replace('"', "").replace('"', "").replace('"', "")
    s = s.replace("&", "and")
    # Keep only alphanumeric and spaces
    s = re.sub(r"[^a-z0-9\s]", "", s)
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _normalize_with_tracking(name: str) -> tuple[list[str], list[str]]:
    """Normalize a name for fuzzy matching and track transformations applied.

    Applies various normalization rules and returns normalized variations
    plus a list of which transformations were applied.

    Args:
        name: The name to normalize

    Returns:
        Tuple of (normalized_variations, transformations_applied)
    """
    transformations = []

    # Step 1: Lowercase and strip
    name = name.lower().strip()

    # Step 2: Remove diacritics (café → cafe)
    if any(unicodedata.category(c).startswith('M') for c in unicodedata.normalize('NFD', name)):
        name = ''.join(c for c in unicodedata.normalize('NFD', name)
                      if not unicodedata.category(c).startswith('M'))
        transformations.append("removed diacritics")

    # Step 3: Strip leading articles (The Beatles → Beatles)
    for article in [r'\bthe\s+', r'\ban\s+', r'\ba\s+']:
        if re.match(article, name):
            name = re.sub(f'^{article}', '', name)
            transformations.append(f"removed article '{article.replace(r'\\b', '').replace(r'\\s+', '').strip()}'")
            break

    # Step 4: Normalize "and" / "&"
    if ' and ' in name:
        variations = [name, name.replace(' and ', ' & ')]
        transformations.append("'and' ↔ '&'")
    elif ' & ' in name:
        variations = [name, name.replace(' & ', ' and ')]
        transformations.append("'and' ↔ '&'")
    else:
        variations = [name]

    # Step 5: Normalize music-specific abbreviations
    abbrev_map = {
        r'\bfeat\.?\s': 'ft ',
        r'\bfeaturing\s': 'ft ',
        r'\bft\.?\s': 'ft ',
        r'\bw/\s': 'with ',
    }
    for pattern, replacement in abbrev_map.items():
        if re.search(pattern, name):
            name = re.sub(pattern, replacement, name)
            transformations.append(f"normalized '{pattern}' to '{replacement.strip()}'")

    # Step 6: Normalize apostrophes and quotes
    if any(char in name for char in ["'", "'", "`", '"', '"', '"']):
        name = name.replace("'", "").replace("'", "").replace("`", "")
        name = name.replace('"', "").replace('"', "").replace('"', "")
        transformations.append("removed quotes/apostrophes")

    # Step 7: Normalize hyphens to spaces
    if '-' in name:
        name = name.replace('-', ' ')
        transformations.append("hyphens → spaces")

    # Step 8: Remove emojis and special characters (keep only alphanumeric and spaces)
    cleaned = re.sub(r"[^a-z0-9\s]", "", name)
    if cleaned != name:
        transformations.append("removed special characters/emojis")
        name = cleaned

    # Step 9: Collapse multiple spaces
    if re.search(r'\s{2,}', name):
        name = re.sub(r"\s+", " ", name).strip()
        transformations.append("normalized whitespace")

    # Also generate variations for "and" / "&" substitution
    all_variations = []
    for variant in variations:
        # Apply all transformations to each variant
        v = variant
        v = ''.join(c for c in unicodedata.normalize('NFD', v)
                   if not unicodedata.category(c).startswith('M'))
        for article in [r'\bthe\s+', r'\ban\s+', r'\ba\s+']:
            v = re.sub(f'^{article}', '', v)
        for pattern, replacement in abbrev_map.items():
            v = re.sub(pattern, replacement, v)
        v = v.replace("'", "").replace("'", "").replace("`", "")
        v = v.replace('"', "").replace('"', "").replace('"', "")
        v = v.replace('-', ' ')
        v = re.sub(r"[^a-z0-9\s]", "", v)
        v = re.sub(r"\s+", " ", v).strip()
        all_variations.append(v)

    return all_variations, transformations


def _fuzzy_match_entity(
    query: str,
    candidates: list[dict],
    name_extractor: Callable[[dict], str],
) -> tuple[dict | None, FuzzyMatchResult | None]:
    """Generic 3-pass fuzzy matching for any entity type.

    Matching priority:
    1. Exact match (case-insensitive) - fastest, no normalization
    2. Partial match (query contained in name) - fast, just substring
    3. Fuzzy match (normalized with transformations) - slowest, only if needed

    Args:
        query: The search query from the user
        candidates: List of candidate entities to match against
        name_extractor: Function to extract name string from a candidate dict

    Returns:
        Tuple of (matched_entity, fuzzy_match_result)
        - matched_entity: The dict from candidates that matched, or None
        - fuzzy_match_result: Details about the match if fuzzy/partial, None if exact
    """
    if not candidates:
        return None, None

    query_lower = query.lower()

    # PASS 1: Exact match (fastest - no normalization)
    for candidate in candidates:
        candidate_name = name_extractor(candidate)
        if query_lower == candidate_name.lower():
            return candidate, None  # Exact match, no fuzzy result

    # PASS 2: Partial match (fast - just substring check)
    partial_match = None
    partial_match_name = None
    for candidate in candidates:
        candidate_name = name_extractor(candidate)
        if query_lower in candidate_name.lower():
            partial_match = candidate
            partial_match_name = candidate_name
            break  # Take first partial match

    # PASS 3: Fuzzy match (slowest - only if no exact/partial)
    if partial_match is None:
        normalized_variations, transformations = _normalize_with_tracking(query)

        for candidate in candidates:
            candidate_name = name_extractor(candidate)
            candidate_variations, _ = _normalize_with_tracking(candidate_name)

            for query_variant in normalized_variations:
                for candidate_variant in candidate_variations:
                    # Check exact match after normalization
                    if query_variant == candidate_variant:
                        fuzzy_result = FuzzyMatchResult(
                            matched_name=candidate_name,
                            query=query,
                            normalized_query=query_variant,
                            normalized_match=candidate_variant,
                            transformations=transformations,
                            match_type="fuzzy"
                        )
                        return candidate, fuzzy_result
                    # Check partial match after normalization (query contained in candidate)
                    if query_variant in candidate_variant:
                        fuzzy_result = FuzzyMatchResult(
                            matched_name=candidate_name,
                            query=query,
                            normalized_query=query_variant,
                            normalized_match=candidate_variant,
                            transformations=transformations + ["partial normalized match"],
                            match_type="fuzzy_partial"
                        )
                        return candidate, fuzzy_result

    # Return partial match if found (after checking fuzzy didn't find better)
    if partial_match:
        fuzzy_result = FuzzyMatchResult(
            matched_name=partial_match_name,
            query=query,
            normalized_query=query_lower,
            normalized_match=partial_match_name.lower(),
            transformations=["partial substring match"],
            match_type="partial"
        )
        return partial_match, fuzzy_result

    return None, None


def get_timestamp() -> str:
    """Get timestamp for unique filenames (YYYYMMDD_HHMMSS)."""
    return time.strftime("%Y%m%d_%H%M%S")


def format_duration(ms: int | None) -> str:
    """Format milliseconds as m:ss (e.g., 3:45).

    Args:
        ms: Duration in milliseconds. Returns empty string for None, 0, or negative values.

    Returns:
        Formatted duration string like "3:45" or empty string for invalid input.
    """
    if not ms or ms <= 0:
        return ""
    total_seconds = ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}:{seconds:02d}"


def extract_track_data(track: dict, include_extras: bool = False) -> dict:
    """Extract track data from API response into standardized dict.

    Args:
        track: Raw track dict from Apple Music API response.
        include_extras: If True, include additional metadata (track_number, artwork, etc.)

    Returns:
        Dict with standardized keys: name, duration, artist, album, year, genre, id.
        If include_extras=True, also includes: track_number, disc_number, has_lyrics,
        catalog_id, composer, isrc, is_explicit, preview_url, artwork_url.
    """
    attrs = track.get("attributes", {})
    play_params = attrs.get("playParams", {})
    genres = attrs.get("genreNames", [])
    release_date = attrs.get("releaseDate", "") or ""

    track_id = track.get("id", "")
    name = attrs.get("name", "")
    artist = attrs.get("artistName", "")
    album = attrs.get("albumName", "")
    explicit = "Yes" if attrs.get("contentRating") == "explicit" else "No"
    isrc = attrs.get("isrc", "")

    data = {
        "name": name,
        "duration": format_duration(attrs.get("durationInMillis", 0)),
        "artist": artist,
        "album": album,
        "year": release_date[:4] if release_date else "",
        "genre": genres[0] if genres else "",
        "explicit": explicit,
        "id": track_id,
    }

    if include_extras:
        previews = attrs.get("previews", [])
        data.update({
            "track_number": attrs.get("trackNumber", ""),
            "disc_number": attrs.get("discNumber", ""),
            "has_lyrics": attrs.get("hasLyrics", False),
            "catalog_id": play_params.get("catalogId", ""),
            "composer": attrs.get("composerName", ""),
            "isrc": isrc,
            "is_explicit": attrs.get("contentRating") == "explicit",
            "preview_url": previews[0].get("url", "") if previews else "",
            "artwork_url": attrs.get("artwork", {}).get("url", "").replace("{w}x{h}", "500x500"),
        })

    # Cache track metadata for later ID lookups (e.g., removal by catalog ID)
    if track_id and name:
        cache = get_track_cache()
        # Determine if this is a catalog or library ID
        catalog_id = track_id if track_id.isdigit() else play_params.get("catalogId", "")
        library_id = track_id if track_id.startswith("i.") else None
        cache.set_track_metadata(
            explicit=explicit,
            catalog_id=catalog_id or None,
            library_id=library_id,
            isrc=isrc or None,
            name=name,
            artist=artist,
            album=album,
        )

    return data


def write_tracks_csv(track_data: list[dict], csv_path: Path, include_extras: bool = False) -> None:
    """Write track data to CSV file.

    Args:
        track_data: List of track dicts from extract_track_data().
        csv_path: Path to write CSV file.
        include_extras: If True, include additional metadata columns.
    """
    csv_fields = ["name", "duration", "artist", "album", "year", "genre", "explicit", "id"]
    if include_extras:
        csv_fields += ["track_number", "disc_number", "has_lyrics", "catalog_id",
                       "composer", "isrc", "is_explicit", "preview_url", "artwork_url"]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(track_data)


def _format_full(t: dict) -> str:
    """Full format: Name - Artist (duration) Album [Year] Genre [Explicit] id"""
    year_str = f" [{t['year']}]" if t["year"] else ""
    genre_str = f" {t['genre']}" if t["genre"] else ""
    explicit_str = " [Explicit]" if t.get("explicit") == "Yes" else ""
    return f"{t['name']} - {t['artist']} ({t['duration']}) {t['album']}{year_str}{genre_str}{explicit_str} {t['id']}"


def _format_clipped(t: dict) -> str:
    """Clipped format: Truncated Name - Artist (duration) Album [Year] Genre [Explicit] id"""
    year_str = f" [{t['year']}]" if t["year"] else ""
    genre_str = f" {t['genre']}" if t["genre"] else ""
    explicit_str = " [Explicit]" if t.get("explicit") == "Yes" else ""
    return f"{truncate(t['name'], 35)} - {truncate(t['artist'], 22)} ({t['duration']}) {truncate(t['album'], 30)}{year_str}{genre_str}{explicit_str} {t['id']}"


def _format_compact(t: dict) -> str:
    """Compact format: Name - Artist (duration) id"""
    return f"{truncate(t['name'], 40)} - {truncate(t['artist'], 25)} ({t['duration']}) {t['id']}"


def _format_minimal(t: dict) -> str:
    """Minimal format: Name - Artist id"""
    return f"{truncate(t['name'], 30)} - {truncate(t['artist'], 20)} {t['id']}"


def format_track_list(track_data: list[dict]) -> tuple[list[str], str]:
    """Format track list with tiered display based on output size.

    Automatically selects the most detailed format that fits within MAX_OUTPUT_CHARS:
    - Full: Name - Artist (duration) Album [Year] Genre id
    - Clipped: Same as Full but with truncated Name/Artist/Album
    - Compact: Truncated Name - Artist (duration) id
    - Minimal: Truncated Name - Artist id

    Args:
        track_data: List of track dicts from extract_track_data().

    Returns:
        Tuple of (list of formatted strings, tier_name) where tier_name is
        "Full", "Clipped", "Compact", or "Minimal".
    """
    if not track_data:
        return [], "Full"

    def char_count(lines: list[str]) -> int:
        return sum(len(line) for line in lines) + max(0, len(lines) - 1)

    # Try full format first
    full_output = [_format_full(t) for t in track_data]
    if char_count(full_output) <= MAX_OUTPUT_CHARS:
        return full_output, "Full"

    # Try clipped (truncated but keeps all fields)
    clipped_output = [_format_clipped(t) for t in track_data]
    if char_count(clipped_output) <= MAX_OUTPUT_CHARS:
        return clipped_output, "Clipped"

    # Fall back to compact (drops album/year/genre)
    compact_output = [_format_compact(t) for t in track_data]
    if char_count(compact_output) <= MAX_OUTPUT_CHARS:
        return compact_output, "Compact"

    # Fall back to minimal
    return [_format_minimal(t) for t in track_data], "Minimal"


def format_output(
    items: list[dict],
    format: str = "text",
    export: str = "none",
    full: bool = False,
    file_prefix: str = "export",
    total_count: int = 0,
    offset: int = 0,
) -> str:
    """Format output with optional file export.

    Args:
        items: List of item dicts (tracks, albums, etc.)
        format: "text" for human-readable, "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports (extras like artwork, track numbers)
        file_prefix: Prefix for export filename
        total_count: Total items before pagination (0 = no pagination info)
        offset: Starting offset for pagination display

    Returns:
        Formatted string (text or JSON) with optional file path info
    """
    if not items:
        return "No results" if format != "json" else "[]"

    result_parts = []

    # Build response content (skip if format="none")
    if format == "json":
        # JSON response - include standard fields, optionally extras
        if full:
            result_parts.append(json.dumps(items, indent=2))
        else:
            # Filter to standard fields only
            standard_keys = {"name", "duration", "artist", "album", "year", "genre", "id",
                           "track_count", "release_date"}
            filtered = [{k: v for k, v in item.items() if k in standard_keys} for item in items]
            result_parts.append(json.dumps(filtered, indent=2))
    elif format == "csv":
        # CSV response inline
        output = io.StringIO()
        if items and "duration" in items[0]:
            csv_fields = ["name", "duration", "artist", "album", "year", "genre", "id"]
            if full:
                csv_fields += ["track_number", "disc_number", "has_lyrics", "catalog_id",
                               "composer", "isrc", "is_explicit", "preview_url", "artwork_url"]
        else:
            csv_fields = list(items[0].keys()) if items else []
        writer = csv.DictWriter(output, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
        result_parts.append(output.getvalue())
    elif format == "text":
        # Text response - use tiered formatting for tracks
        if items and "duration" in items[0]:
            # Track data - use tiered format
            formatted_lines, _tier = format_track_list(items)
            # Build header with pagination info if provided
            if total_count > 0 and total_count > len(items):
                start = offset + 1
                end = offset + len(items)
                result_parts.append(f"=== {start}-{end} of {total_count} tracks ===\n")
            else:
                result_parts.append(f"=== {len(items)} tracks ===\n")
            result_parts.append("\n".join(formatted_lines))
        else:
            # Non-track data (albums, artists) - simple format
            result_parts.append(f"=== {len(items)} items ===\n")
            for item in items[:200]:
                if "artist" in item and "name" in item:
                    result_parts.append(f"{item['name']} - {item.get('artist', '')} {item.get('id', '')}")
                elif "name" in item:
                    result_parts.append(f"{item['name']} {item.get('id', '')}")
    # format="none" - skip response body, only show export info

    # Handle file export
    if export in ("csv", "json"):
        cache_dir = get_cache_dir()
        timestamp = get_timestamp()

        if export == "csv":
            file_path = cache_dir / f"{file_prefix}_{timestamp}.csv"
            # Determine fields based on full flag
            if items and "duration" in items[0]:
                csv_fields = ["name", "duration", "artist", "album", "year", "genre", "id"]
                if full:
                    csv_fields += ["track_number", "disc_number", "has_lyrics", "catalog_id",
                                   "composer", "isrc", "is_explicit", "preview_url", "artwork_url"]
            else:
                csv_fields = list(items[0].keys()) if items else []

            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(items)
        else:  # json
            file_path = cache_dir / f"{file_prefix}_{timestamp}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(items if full else [{k: v for k, v in item.items()
                         if k in {"name", "duration", "artist", "album", "year", "genre", "id",
                                  "track_count", "release_date"}} for item in items], f, indent=2)

        result_parts.append(f"Exported {len(items)} items: {file_path}")
        result_parts.append(f"Resource: exports://{file_path.name}")

    if not result_parts:
        return f"{len(items)} items (use export='csv' or 'json' to save)"

    return "\n".join(result_parts)


BASE_URL = "https://api.music.apple.com/v1"
DEFAULT_STOREFRONT = "us"
REQUEST_TIMEOUT = 30  # seconds

# play_track retry constants for iCloud sync
PLAY_TRACK_INITIAL_DELAY = 1.0  # seconds before first retry
PLAY_TRACK_RETRY_DELAY = 0.2  # seconds between retries
PLAY_TRACK_MAX_ATTEMPTS = 45  # total retry attempts (~10 seconds)


def get_storefront() -> str:
    """Get storefront from preferences, defaulting to 'us'."""
    prefs = get_user_preferences()
    return prefs.get("storefront", DEFAULT_STOREFRONT)

mcp = FastMCP("AppleMusicAPI")


# ============ MCP RESOURCES ============


@mcp.resource("exports://list")
def list_exports() -> str:
    """List all exported files in the cache directory."""
    cache_dir = get_cache_dir()
    files = sorted(cache_dir.glob("*.*"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "No exports found"
    return "\n".join(f"{f.name} ({f.stat().st_size} bytes)" for f in files[:50])


@mcp.resource("exports://{filename}")
def read_export(filename: str) -> str:
    """Read an exported file from the cache directory."""
    cache_dir = get_cache_dir()
    file_path = cache_dir / filename
    if not file_path.exists():
        return f"File not found: {filename}"
    if not file_path.is_relative_to(cache_dir):
        return "Invalid path"
    return file_path.read_text(encoding="utf-8")


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


# ============ INTERNAL HELPERS ============


def _apply_pagination(
    items: list,
    limit: int = 0,
    offset: int = 0,
) -> tuple[list, int, str | None]:
    """Apply offset/limit pagination to a list.

    Args:
        items: List of items to paginate
        limit: Max items to return (0 = all)
        offset: Skip first N items

    Returns:
        Tuple of (paginated_items, total_count, error_message)
        - On success: (items, total, None)
        - On error: ([], total, error message)
    """
    total_count = len(items)

    if offset >= total_count and total_count > 0:
        return [], total_count, f"Offset {offset} exceeds {total_count} items"

    if offset > 0:
        items = items[offset:]
    if limit > 0:
        items = items[:limit]

    return items, total_count, None


def _split_csv(value: str) -> list[str]:
    """Split comma-separated string into list of trimmed non-empty values.

    Args:
        value: Comma-separated string (e.g., "a, b, c")

    Returns:
        List of trimmed values, excluding empty strings
    """
    return [s.strip() for s in value.split(",") if s.strip()]


def _parse_tracks_json(tracks: str) -> tuple[list[dict], str | None]:
    """Parse JSON tracks array parameter.

    Args:
        tracks: JSON string like '[{"name":"Song","artist":"Artist"},...]'

    Returns:
        Tuple of (track_list, error_message)
        - On success: (list of track dicts, None)
        - On error: ([], error message string)
    """
    try:
        track_list = json.loads(tracks)
        if not isinstance(track_list, list):
            return [], "Error: tracks must be a JSON array"
        return track_list, None
    except json.JSONDecodeError as e:
        return [], f"Error: Invalid JSON - {e}"


def _validate_track_object(track_obj: dict) -> tuple[str, str, str | None]:
    """Validate and extract name/artist from a track object.

    Args:
        track_obj: Dict with 'name' and optional 'artist' fields

    Returns:
        Tuple of (name, artist, error_message)
        - On success: (name, artist, None)
        - On error: ("", "", error message)
    """
    if not isinstance(track_obj, dict):
        return "", "", "Invalid track object (must be dict)"
    name = track_obj.get("name", "")
    if not name:
        return "", "", "Track missing 'name' field"
    artist = track_obj.get("artist", "")
    return name, artist, None


def _detect_id_type(id_str: str) -> str:
    """Detect the type of an Apple Music ID.

    ID patterns:
    - Catalog: 9+ digits (e.g., "1440783617")
    - Library: starts with "i." (e.g., "i.ABC123XYZ")
    - Playlist: starts with "p." (e.g., "p.XYZ789ABC")
    - Persistent: 12+ hex chars (e.g., "ABC123DEF456")

    Args:
        id_str: The ID string to classify

    Returns:
        One of: "catalog", "library", "playlist", "persistent", "unknown"
    """
    id_str = id_str.strip()
    if id_str.startswith("i."):
        return "library"
    elif id_str.startswith("p."):
        return "playlist"
    elif id_str.isdigit() and len(id_str) >= MIN_CATALOG_ID_LENGTH:
        return "catalog"
    elif len(id_str) >= 12 and re.match(r'^[A-Fa-f0-9]+$', id_str) and re.search(r'[A-Fa-f]', id_str):
        return "persistent"
    else:
        return "unknown"


def _find_api_playlist_by_name(name: str) -> tuple[str | None, FuzzyMatchResult | None]:
    """Find API playlist ID by name with fuzzy matching.

    Uses generic _fuzzy_match_entity for 3-pass matching:
    1. Exact match (case-insensitive)
    2. Partial match (query contained in playlist name)
    3. Fuzzy match (normalized with transformations)

    Args:
        name: Playlist name to search for

    Returns:
        Tuple of (playlist_id, fuzzy_match_result)
        - playlist_id: API playlist ID (p.XXX) if found, None otherwise
        - fuzzy_match_result: Details about the match if fuzzy/partial, None if exact
    """
    try:
        headers = get_headers()
        api_offset = 0

        # Collect all playlists first (for multi-pass matching)
        all_playlists = []
        while True:
            response = requests.get(
                f"{BASE_URL}/me/library/playlists",
                headers=headers,
                params={"limit": 100, "offset": api_offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                break

            playlists = response.json().get("data", [])
            if not playlists:
                break

            all_playlists.extend(playlists)

            if len(playlists) < 100:
                break
            api_offset += 100

        # Use generic fuzzy matching
        def playlist_name_extractor(pl: dict) -> str:
            return pl.get("attributes", {}).get("name", "")

        matched, fuzzy_result = _fuzzy_match_entity(name, all_playlists, playlist_name_extractor)

        if matched:
            return matched.get("id"), fuzzy_result

    except Exception:
        pass  # Fall back to AppleScript

    return None, None


def _resolve_playlist(playlist: str) -> ResolvedPlaylist:
    """Resolve a playlist parameter to all available identifiers.

    Populates as many identifiers as possible (API ID, name, persistent ID) so
    callers can use what they need. Different operations require different IDs:
    - API operations prefer api_id for performance
    - AppleScript operations require applescript_name

    Auto-detects based on pattern:
    - Matches "p." + alphanumeric only → playlist ID (e.g., p.ABC123xyz)
    - Otherwise → playlist name, tries to find API ID first for better performance

    Args:
        playlist: Either a playlist ID (p.XXX) or name

    Returns:
        ResolvedPlaylist with populated fields
    """
    playlist = playlist.strip()

    if not playlist:
        return ResolvedPlaylist(
            raw_input=playlist,
            error="Error: playlist parameter required"
        )

    # Real playlist IDs are "p." followed by alphanumeric chars only (no spaces/punctuation)
    # This correctly treats "p.s. I love you" as a name, not an ID
    if playlist.startswith("p.") and len(playlist) > 2 and playlist[2:].isalnum():
        # User provided explicit ID
        # TODO: Could look up the name from API for completeness
        return ResolvedPlaylist(
            raw_input=playlist,
            api_id=playlist,
            applescript_name=None  # Not available without lookup
        )

    # User provided a name - try to find API ID first (faster than AppleScript)
    api_id, fuzzy_match = _find_api_playlist_by_name(playlist)

    if api_id:
        # Found via API - we have both ID and name
        # The matched name comes from fuzzy_match if it was fuzzy, otherwise it's exact
        matched_name = fuzzy_match.matched_name if fuzzy_match else playlist
        return ResolvedPlaylist(
            raw_input=playlist,
            api_id=api_id,
            applescript_name=matched_name,  # Use the actual matched name
            fuzzy_match=fuzzy_match
        )

    # Not found via API - try AppleScript-based fuzzy matching if available
    if APPLESCRIPT_AVAILABLE:
        success, playlists = asc.get_playlists()
        if success and playlists:
            # Use fuzzy matching on AppleScript playlist names
            def playlist_name_extractor(pl: dict) -> str:
                return pl.get("name", "")

            matched, fuzzy_result = _fuzzy_match_entity(playlist, playlists, playlist_name_extractor)
            if matched:
                matched_name = matched.get("name", playlist)
                return ResolvedPlaylist(
                    raw_input=playlist,
                    api_id=None,
                    applescript_name=matched_name,  # Use actual matched name
                    fuzzy_match=fuzzy_result
                )

    # Fall back to raw input for AppleScript
    return ResolvedPlaylist(
        raw_input=playlist,
        api_id=None,
        applescript_name=playlist  # Use as-is for AppleScript
    )


def _detect_input_type(value: str) -> InputType:
    """Detect what type of input a string represents.

    Detection order:
    1. Prefixed IDs (i., p., l.) - explicit type markers
    2. All digits AND length >= 9 - catalog ID
    3. 12+ hex chars, no spaces - persistent ID (AppleScript)
    4. Everything else - name

    Args:
        value: The input string to classify

    Returns:
        InputType enum value
    """
    value = value.strip()

    # Check prefix-based IDs first
    if value.startswith("i."):
        return InputType.LIBRARY_ID
    if value.startswith("p.") and len(value) > 2 and value[2:].isalnum():
        return InputType.PLAYLIST_ID
    if value.startswith("l."):
        return InputType.ALBUM_ID

    # Catalog IDs are 9+ digits (Apple uses 10-digit IDs)
    if value.isdigit() and len(value) >= MIN_CATALOG_ID_LENGTH:
        return InputType.CATALOG_ID

    # Persistent IDs from AppleScript are 12+ hex chars with no spaces, must contain at least one letter
    if len(value) >= 12 and " " not in value and re.match(r'^[A-Fa-f0-9]+$', value) and re.search(r'[A-Fa-f]', value):
        return InputType.PERSISTENT_ID

    # Default to name
    return InputType.NAME


def _resolve_input(
    value: str,
    entity_type: EntityType,
    artist: str = "",
) -> list[ResolvedInput]:
    """Universal input resolution for any entity type.

    Accepts multiple formats and returns a list of resolved inputs:
    - JSON array: '[{"name":"Hey Jude","artist":"Beatles"}]'
    - CSV names: "Hey Jude, Let It Be"
    - Single ID: "1440783617" or "i.ABC123"
    - Single name: "Hey Jude"

    Detection order for single values:
    1. Starts with '[' → JSON array of objects
    2. Contains comma → CSV of names
    3. Otherwise → single value (ID or name auto-detected)

    Args:
        value: Raw input - ID, name, CSV, or JSON
        entity_type: What kind of entity we're resolving (for context)
        artist: Artist name for disambiguation (used with names)

    Returns:
        List of ResolvedInput objects (single item for ID/name, multiple for CSV/JSON)
    """
    value = value.strip()
    if not value:
        return [ResolvedInput(
            input_type=InputType.NAME,
            value="",
            raw=value,
            error="Empty input"
        )]

    results = []

    # 1. JSON array detection
    if value.startswith("["):
        try:
            items = json.loads(value)
            if not isinstance(items, list):
                return [ResolvedInput(
                    input_type=InputType.NAME,
                    value=value,
                    raw=value,
                    error="JSON must be an array"
                )]

            for item in items:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    item_artist = item.get("artist", "") or artist
                    if not name:
                        results.append(ResolvedInput(
                            input_type=InputType.JSON_OBJECT,
                            value="",
                            artist=item_artist,
                            raw=str(item),
                            error="Object missing 'name' field"
                        ))
                    else:
                        results.append(ResolvedInput(
                            input_type=InputType.JSON_OBJECT,
                            value=name,
                            artist=item_artist,
                            raw=str(item)
                        ))
                elif isinstance(item, str):
                    # JSON array of strings treated as names
                    input_type = _detect_input_type(item)
                    results.append(ResolvedInput(
                        input_type=input_type,
                        value=item.strip(),
                        artist=artist,
                        raw=item
                    ))
                else:
                    results.append(ResolvedInput(
                        input_type=InputType.NAME,
                        value=str(item),
                        raw=str(item),
                        error="Invalid item type in array"
                    ))

            return results if results else [ResolvedInput(
                input_type=InputType.NAME,
                value="",
                raw=value,
                error="Empty JSON array"
            )]

        except json.JSONDecodeError as e:
            return [ResolvedInput(
                input_type=InputType.NAME,
                value=value,
                raw=value,
                error=f"Invalid JSON: {e}"
            )]

    # 2. CSV detection (contains comma, not JSON)
    if "," in value:
        for item in value.split(","):
            item = item.strip()
            if item:
                input_type = _detect_input_type(item)
                results.append(ResolvedInput(
                    input_type=input_type,
                    value=item,
                    artist=artist,
                    raw=item
                ))
        return results if results else [ResolvedInput(
            input_type=InputType.NAME,
            value="",
            raw=value,
            error="Empty CSV"
        )]

    # 3. Single value - detect type
    input_type = _detect_input_type(value)
    return [ResolvedInput(
        input_type=input_type,
        value=value,
        artist=artist,
        raw=value
    )]


def _resolve_track(track: str, artist: str = "") -> list[ResolvedInput]:
    """Convenience wrapper for track resolution."""
    return _resolve_input(track, EntityType.TRACK, artist)


def _resolve_album(album: str, artist: str = "") -> list[ResolvedInput]:
    """Convenience wrapper for album resolution."""
    return _resolve_input(album, EntityType.ALBUM, artist)


def _resolve_artist(artist: str) -> list[ResolvedInput]:
    """Convenience wrapper for artist resolution."""
    return _resolve_input(artist, EntityType.ARTIST, "")


def _build_track_results(
    results: list[str],
    errors: list[str],
    success_prefix: str = "✓",
    error_prefix: str = "✗",
    success_verb: str = "processed",
    error_verb: str = "failed",
) -> str:
    """Build formatted results message from success/error lists.

    Args:
        results: List of success messages
        errors: List of error messages
        success_prefix: Prefix for success section (default: ✓)
        error_prefix: Prefix for error section (default: ✗)
        success_verb: Verb for success count (default: processed)
        error_verb: Verb for error count (default: failed)

    Returns:
        Formatted multi-line message, or "No tracks were processed" if empty
    """
    output = []

    if results:
        output.append(f"{success_prefix} {success_verb.capitalize()} {len(results)} track(s):")
        for r in results:
            output.append(f"  {r}")

    if errors:
        if output:
            output.append("")  # Blank line between sections
        output.append(f"{error_prefix} {error_verb.capitalize()} {len(errors)} track(s):")
        for e in errors:
            output.append(f"  {e}")

    if not output:
        return f"No tracks were {success_verb}"

    return "\n".join(output)


def _find_matching_catalog_song(
    name: str, artist: str = ""
) -> tuple[dict | None, str | None, FuzzyMatchResult | None]:
    """Search catalog and find a song matching name and optional artist.

    Matching priority:
    1. Exact match (case-insensitive) on name, with artist filter
    2. Partial match (name in song_name), with artist filter
    3. Fuzzy match on name only (relaxes artist constraint)

    Args:
        name: Track name to search for
        artist: Artist name (optional, for filtering)

    Returns:
        Tuple of (song_dict, error_message, fuzzy_match_result)
        - On success: (song dict, None, fuzzy_result or None)
        - On not found: (None, "Not found in catalog", None)
    """
    search_term = f"{name} {artist}".strip() if artist else name
    songs = _search_catalog_songs(search_term, limit=5)  # Get more results for fuzzy

    if not songs:
        return None, "Not found in catalog", None

    # Filter by artist first if provided
    def artist_matches(song: dict) -> bool:
        if not artist:
            return True
        song_artist = song.get("attributes", {}).get("artistName", "")
        return artist.lower() in song_artist.lower()

    # Candidates that match artist filter
    artist_filtered = [s for s in songs if artist_matches(s)]

    # Use generic fuzzy matching on name
    def song_name_extractor(song: dict) -> str:
        return song.get("attributes", {}).get("name", "")

    # Try fuzzy match on artist-filtered songs first
    matched = None
    fuzzy_result = None
    if artist_filtered:
        matched, fuzzy_result = _fuzzy_match_entity(name, artist_filtered, song_name_extractor)
        if matched:
            _cache_song_metadata(matched)
            return matched, None, fuzzy_result

    # If no match with artist filter, try all songs (relaxed matching)
    if artist and not matched:
        matched, fuzzy_result = _fuzzy_match_entity(name, songs, song_name_extractor)
        if matched:
            _cache_song_metadata(matched)
            return matched, None, fuzzy_result

    return None, "Not found in catalog", None


def _cache_song_metadata(song: dict) -> None:
    """Cache song metadata for later ID lookups."""
    attrs = song.get("attributes", {})
    catalog_id = song.get("id", "")
    song_name = attrs.get("name", "")

    if catalog_id and song_name:
        cache = get_track_cache()
        cache.set_track_metadata(
            explicit="Yes" if attrs.get("contentRating") == "explicit" else "No",
            catalog_id=catalog_id,
            isrc=attrs.get("isrc") or None,
            name=song_name,
            artist=attrs.get("artistName", ""),
            album=attrs.get("albumName", ""),
        )


def _search_catalog_songs(query: str, limit: int = 5) -> list[dict]:
    """Search catalog for songs and return raw song data.

    Args:
        query: Search term
        limit: Max results (default 5)

    Returns:
        List of song dicts with 'id', 'attributes' (name, artistName, etc.)
        Empty list on error.
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/search",
            headers=headers,
            params={"term": query, "types": "songs", "limit": min(limit, 25)},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("results", {}).get("songs", {}).get("data", [])
    except Exception:
        pass
    return []


def _search_catalog_albums(query: str, limit: int = 5) -> list[dict]:
    """Search catalog for albums and return raw album data.

    Args:
        query: Search term
        limit: Max results (default 5)

    Returns:
        List of album dicts with 'id', 'attributes' (name, artistName, etc.)
        Empty list on error.
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/search",
            headers=headers,
            params={"term": query, "types": "albums", "limit": min(limit, 25)},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("results", {}).get("albums", {}).get("data", [])
    except Exception:
        pass
    return []


def _find_matching_catalog_album(
    name: str, artist: str = ""
) -> tuple[dict | None, str | None, FuzzyMatchResult | None]:
    """Search catalog and find an album matching name and optional artist.

    Uses _fuzzy_match_entity for 3-pass matching:
    1. Exact match (case-insensitive) on name, with artist filter
    2. Partial match (name in album_name), with artist filter
    3. Fuzzy match on name only (relaxes artist constraint)

    Args:
        name: Album name to search for
        artist: Artist name (optional, for filtering)

    Returns:
        Tuple of (album_dict, error_message, fuzzy_match_result)
        - On success: (album dict, None, fuzzy_result or None)
        - On not found: (None, "Not found in catalog", None)
    """
    search_term = f"{name} {artist}".strip() if artist else name
    albums = _search_catalog_albums(search_term, limit=5)

    if not albums:
        return None, "Not found in catalog", None

    # Filter by artist first if provided
    def artist_matches(album: dict) -> bool:
        if not artist:
            return True
        album_artist = album.get("attributes", {}).get("artistName", "")
        return artist.lower() in album_artist.lower()

    # Candidates that match artist filter
    artist_filtered = [a for a in albums if artist_matches(a)]

    # Use generic fuzzy matching on name
    def album_name_extractor(album: dict) -> str:
        return album.get("attributes", {}).get("name", "")

    # Try fuzzy match on artist-filtered albums first
    matched = None
    fuzzy_result = None
    if artist_filtered:
        matched, fuzzy_result = _fuzzy_match_entity(name, artist_filtered, album_name_extractor)
        if matched:
            return matched, None, fuzzy_result

    # If no match with artist filter, try all albums (relaxed matching)
    if artist and not matched:
        matched, fuzzy_result = _fuzzy_match_entity(name, albums, album_name_extractor)
        if matched:
            return matched, None, fuzzy_result

    return None, "Not found in catalog", None


def _search_library_songs(query: str, limit: int = 5) -> list[dict]:
    """Search library for songs and return raw song data.

    Args:
        query: Search term
        limit: Max results (default 5)

    Returns:
        List of song dicts with 'id', 'attributes' (name, artistName, etc.)
        Empty list on error.
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/me/library/search",
            headers=headers,
            params={"term": query, "types": "library-songs", "limit": min(limit, 25)},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("results", {}).get("library-songs", {}).get("data", [])
    except Exception:
        pass
    return []


def _find_track_id(
    name: str, artist: str = ""
) -> tuple[str | None, str | None, str]:
    """Find a track by name, searching library first then catalog.

    This is the canonical way to find a track - always prefers library
    (track is already added) over catalog (would need to add first).

    Args:
        name: Track name to search for (partial match)
        artist: Artist name (optional, improves matching)

    Returns:
        Tuple of (library_id, catalog_id, display_name)
        - If in library: (library_id, None, "Name - Artist")
        - If in catalog only: (None, catalog_id, "Name - Artist")
        - If not found: (None, None, "")
    """
    search_term = f"{name} {artist}".strip() if artist else name
    name_lower = name.lower()
    artist_lower = artist.lower() if artist else ""

    # 1. Search library first
    library_songs = _search_library_songs(search_term, limit=5)
    for song in library_songs:
        attrs = song.get("attributes", {})
        song_name = attrs.get("name", "")
        song_artist = attrs.get("artistName", "")

        # Partial match on name
        if name_lower not in song_name.lower():
            continue
        # Partial match on artist if provided
        if artist_lower and artist_lower not in song_artist.lower():
            continue

        library_id = song.get("id", "")
        display = f"{song_name} - {song_artist}"
        return library_id, None, display

    # 2. Fall back to catalog
    catalog_songs = _search_catalog_songs(search_term, limit=5)
    for song in catalog_songs:
        attrs = song.get("attributes", {})
        song_name = attrs.get("name", "")
        song_artist = attrs.get("artistName", "")

        # Partial match on name
        if name_lower not in song_name.lower():
            continue
        # Partial match on artist if provided
        if artist_lower and artist_lower not in song_artist.lower():
            continue

        catalog_id = song.get("id", "")
        display = f"{song_name} - {song_artist}"

        # Cache for later lookups
        cache = get_track_cache()
        cache.set_track_metadata(
            explicit="Yes" if attrs.get("contentRating") == "explicit" else "No",
            catalog_id=catalog_id,
            isrc=attrs.get("isrc") or None,
            name=song_name,
            artist=song_artist,
            album=attrs.get("albumName", ""),
        )

        return None, catalog_id, display

    return None, None, ""


def _add_to_library_api(
    catalog_ids: list[str], content_type: str = "songs"
) -> tuple[bool, str]:
    """Add content to library by catalog ID.

    Args:
        catalog_ids: List of catalog IDs
        content_type: Type of content - "songs" (default) or "albums"

    Returns:
        Tuple of (success, message)
    """
    if not catalog_ids:
        return False, "No catalog IDs provided"

    # Map type to API parameter
    type_param = {
        "songs": "ids[songs]",
        "albums": "ids[albums]",
    }.get(content_type, "ids[songs]")

    type_label = "song" if content_type == "songs" else "album"

    try:
        headers = get_headers()
        response = requests.post(
            f"{BASE_URL}/me/library",
            headers=headers,
            params={type_param: ",".join(catalog_ids)},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (200, 201, 202, 204):
            return True, f"Added {len(catalog_ids)} {type_label}(s) to library"
        return False, f"API returned status {response.status_code}"
    except Exception as e:
        return False, str(e)


def _add_songs_to_library(catalog_ids: list[str]) -> tuple[bool, str]:
    """Add songs to library by catalog ID. (Legacy wrapper)"""
    return _add_to_library_api(catalog_ids, "songs")


def _add_album_to_library(album_id: str) -> tuple[bool, str]:
    """Add album to library by catalog ID."""
    return _add_to_library_api([album_id], "albums")


def _auto_search_and_add_to_playlist(
    track_name: str,
    artist: str,
    playlist_name: str,
    playlist_id: str | None = None,
) -> tuple[bool, str, list[str]]:
    """Search catalog for track, add to library, add to playlist.

    Args:
        track_name: Track name to search for
        artist: Artist name (optional but helps matching)
        playlist_name: Playlist name for messaging
        playlist_id: Playlist ID for API add (optional, will look up if not provided)

    Returns:
        Tuple of (success, result_message, steps_log)
    """
    steps = []
    catalog_search = f"{track_name} {artist}" if artist else track_name

    try:
        headers = get_headers()

        # Search catalog
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/search",
            headers=headers,
            params={"term": catalog_search, "types": "songs", "limit": 3},
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return False, f"Catalog search failed (status {response.status_code})", steps

        data = response.json()
        songs = data.get("results", {}).get("songs", {}).get("data", [])

        if not songs:
            return False, f"Not found in library or catalog", steps

        # Take the first match
        song = songs[0]
        catalog_id = song["id"]
        attrs = song.get("attributes", {})
        found_name = attrs.get("name", "")
        found_artist = attrs.get("artistName", "")

        steps.append(f"Found in catalog: {found_name} - {found_artist}")

        # Add to library via API
        add_response = requests.post(
            f"{BASE_URL}/me/library",
            headers=headers,
            params={"ids[songs]": catalog_id},
            timeout=REQUEST_TIMEOUT,
        )

        if add_response.status_code not in (200, 202):
            return False, f"Failed to add to library (status {add_response.status_code})", steps

        # Get library ID from catalog song's library relationship
        lib_response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/songs/{catalog_id}/library",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        library_id = None
        if lib_response.status_code == 200:
            lib_data = lib_response.json()
            lib_songs = lib_data.get("data", [])
            if lib_songs:
                library_id = lib_songs[0]["id"]

        if not library_id:
            return False, "Added to library but could not get library ID", steps

        # Get playlist ID if not provided
        if not playlist_id:
            pl_success, playlists = asc.get_playlists()
            if pl_success:
                for pl in playlists:
                    if playlist_name.lower() in pl.get("name", "").lower():
                        playlist_id = pl.get("id")
                        break

        if not playlist_id:
            return False, f"Could not find playlist ID for '{playlist_name}'", steps

        # Add to playlist via API
        pl_add_response = requests.post(
            f"{BASE_URL}/me/library/playlists/{playlist_id}/tracks",
            headers=headers,
            json={"data": [{"id": library_id, "type": "library-songs"}]},
            timeout=REQUEST_TIMEOUT,
        )

        if pl_add_response.status_code in (200, 201, 204):
            return True, f"{found_name} - {found_artist}", steps
        else:
            return False, f"Failed to add to playlist (status {pl_add_response.status_code})", steps

    except Exception as e:
        return False, f"Error: {str(e)}", steps


def _rate_song_api(song_id: str, rating: str) -> tuple[bool, str]:
    """Rate a song via API.

    Args:
        song_id: Catalog song ID
        rating: 'love' or 'dislike'

    Returns:
        Tuple of (success, message)
    """
    rating_value = {"love": 1, "dislike": -1}.get(rating.lower())
    if rating_value is None:
        return False, "rating must be 'love' or 'dislike'"

    try:
        headers = get_headers()
        body = {"type": "rating", "attributes": {"value": rating_value}}
        response = requests.put(
            f"{BASE_URL}/me/ratings/songs/{song_id}",
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code in (200, 201, 204):
            return True, f"Marked as {rating}"
        return False, f"API returned status {response.status_code}"
    except Exception as e:
        return False, str(e)


# ============ PLAYLIST MANAGEMENT ============


@mcp.tool()
def get_library_playlists(
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get all playlists from your Apple Music library.

    Args:
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Playlist listing in requested format
    """
    playlist_data = []

    # Try AppleScript first (local, instant, no auth required)
    if APPLESCRIPT_AVAILABLE:
        success, as_playlists = asc.get_playlists()
        if success:
            if not as_playlists:
                return "No playlists in library"
            for p in as_playlists:
                playlist_data.append({
                    "id": p.get("id", ""),
                    "name": p.get("name", "Unknown"),
                    "track_count": p.get("track_count", 0),
                    "smart": p.get("smart", False),
                    "can_edit": True,  # AS can edit any playlist
                })
            return format_output(playlist_data, format, export, full, "playlists")
        # AppleScript failed - fall through to API

    # Fall back to API
    try:
        headers = get_headers()
        all_playlists = []
        offset = 0

        # Paginate to get all playlists
        while True:
            response = requests.get(
                f"{BASE_URL}/me/library/playlists",
                headers=headers,
                params={"limit": 100, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            playlists = response.json().get("data", [])
            if not playlists:
                break
            all_playlists.extend(playlists)
            if len(playlists) < 100:
                break
            offset += 100

        # Extract playlist data
        for playlist in all_playlists:
            attrs = playlist.get("attributes", {})
            desc = attrs.get("description", {})

            playlist_data.append({
                "id": playlist.get("id", ""),
                "name": attrs.get("name", "Unknown"),
                "can_edit": attrs.get("canEdit", False),
                "is_public": attrs.get("isPublic", False),
                "date_added": attrs.get("dateAdded", ""),
                "last_modified": attrs.get("lastModifiedDate", ""),
                "description": desc.get("standard", "") if isinstance(desc, dict) else str(desc),
                "has_catalog": attrs.get("hasCatalog", False),
            })

        # Add token warning if text format
        warning = get_token_expiration_warning()
        prefix = f"{warning}\n\n" if warning and format == "text" else ""

        return prefix + format_output(playlist_data, format, export, full, "playlists")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_playlist_tracks(
    playlist: str = "",
    filter: str = "",
    limit: int = 0,
    offset: int = 0,
    format: str = "text",
    export: str = "none",
    full: bool = False,
    fetch_explicit: Optional[bool] = None,
) -> str:
    """
    Get tracks in a playlist.

    Args:
        playlist: Playlist ID (p.XXX) or name (auto-detected)
        filter: Filter by name/artist (case-insensitive)
        limit: Max tracks (default: all)
        offset: Skip first N tracks (for pagination)
        format: "text", "json", "csv", or "none"
        export: "none", "csv", or "json" to write file
        full: Include all metadata in exports
        fetch_explicit: Fetch explicit status via API (default: user preference)

    Returns: Track listing in requested format
    """
    start_time = time.time()
    query_stats = {"cache_hits": 0, "cache_misses": 0, "api_calls": 0}

    # Resolve playlist parameter
    resolved: ResolvedPlaylist = _resolve_playlist(playlist)
    if resolved.error:
        return resolved.error

    # Apply user preferences
    if fetch_explicit is None:
        prefs = get_user_preferences()
        fetch_explicit = prefs["fetch_explicit"]

    use_api = bool(resolved.api_id)
    use_applescript = bool(resolved.applescript_name)

    # Use AppleScript with name (only if we don't have API ID)
    if use_applescript and not use_api:
        if not APPLESCRIPT_AVAILABLE:
            return "Error: AppleScript (playlist_name) requires macOS"
        success, result = asc.get_playlist_tracks(resolved.applescript_name)
        if not success:
            return f"Error: {result}"
        if not result:
            return "Playlist is empty"

        # Format AppleScript results
        track_data = []
        for t in result:
            track_data.append({
                "name": t.get("name", "Unknown"),
                "artist": t.get("artist", "Unknown"),
                "album": t.get("album", ""),
                "duration": t.get("duration", "0:00"),
                "genre": t.get("genre", ""),
                "year": t.get("year", ""),
                "explicit": "Unknown",  # Will be enriched below if fetch_explicit=True
                "id": t.get("id", ""),
            })

        # Enrich with explicit status via API if requested
        # Uses TrackCache for ID-based caching (persistent, library, catalog IDs)
        if fetch_explicit and track_data:
            try:
                cache = get_track_cache()

                # First pass: fill in what we know from cache (ID-based lookup only)
                unknown_tracks = []
                for track in track_data:
                    track_id = track.get("id", "")
                    if track_id:
                        cached_explicit = cache.get_explicit(track_id)
                        if cached_explicit:
                            track["explicit"] = cached_explicit
                            query_stats["cache_hits"] += 1
                            continue
                    query_stats["cache_misses"] += 1
                    unknown_tracks.append(track)

                # If we have unknown tracks, fetch from API
                if unknown_tracks:
                    headers = get_headers()

                    # Find the playlist in the API library by matching name
                    query_stats["api_calls"] += 1
                    response = requests.get(
                        f"{BASE_URL}/me/library/playlists",
                        headers=headers,
                        params={"limit": 100},
                        timeout=REQUEST_TIMEOUT,
                    )

                    if response.status_code == 200:
                        playlists = response.json().get("data", [])
                        api_playlist_id = None

                        # Find matching playlist by name
                        for pl in playlists:
                            pl_name = pl.get("attributes", {}).get("name", "")
                            if pl_name.lower() == resolved.applescript_name.lower() or resolved.applescript_name.lower() in pl_name.lower():
                                api_playlist_id = pl.get("id")
                                break

                        # If found, fetch all tracks from API with explicit info
                        if api_playlist_id:
                            all_api_tracks = []
                            api_offset = 0

                            while True:
                                query_stats["api_calls"] += 1
                                track_response = requests.get(
                                    f"{BASE_URL}/me/library/playlists/{api_playlist_id}/tracks",
                                    headers=headers,
                                    params={"limit": 100, "offset": api_offset},
                                    timeout=REQUEST_TIMEOUT,
                                )
                                if track_response.status_code != 200:
                                    break

                                tracks = track_response.json().get("data", [])
                                if not tracks:
                                    break

                                all_api_tracks.extend(tracks)
                                if len(tracks) < 100:
                                    break
                                api_offset += 100

                            # Build temporary maps for matching (normalized keys -> API data)
                            # Multiple keys for fallback matching: name+artist+album, name+artist, name
                            api_track_map_full = {}  # name+artist+album
                            api_track_map_partial = {}  # name+artist (for fallback)
                            api_track_map_name = {}  # name only (for last resort, only if unique)
                            api_track_name_counts = {}  # count occurrences of each name

                            for api_track in all_api_tracks:
                                attrs = api_track.get("attributes", {})
                                play_params = attrs.get("playParams", {})
                                library_id = api_track.get("id", "")
                                catalog_id = play_params.get("catalogId", "")
                                isrc = attrs.get("isrc", "")
                                track_name = _normalize_for_match(attrs.get("name", ""))
                                track_artist = _normalize_for_match(attrs.get("artistName", ""))
                                track_album = _normalize_for_match(attrs.get("albumName", ""))
                                explicit = "Yes" if attrs.get("contentRating") == "explicit" else "No"

                                api_data = {
                                    "library_id": library_id,
                                    "catalog_id": catalog_id,
                                    "isrc": isrc,
                                    "explicit": explicit,
                                }

                                # Full match key (name+artist+album)
                                full_key = f"{track_name}|||{track_artist}|||{track_album}"
                                api_track_map_full[full_key] = api_data

                                # Partial match key (name+artist) for fallback
                                partial_key = f"{track_name}|||{track_artist}"
                                if partial_key not in api_track_map_partial:
                                    api_track_map_partial[partial_key] = api_data

                                # Name-only map (only use if name is unique)
                                api_track_name_counts[track_name] = api_track_name_counts.get(track_name, 0) + 1
                                api_track_map_name[track_name] = api_data

                            # Match AppleScript tracks to API tracks and cache
                            for track in track_data:
                                if track["explicit"] != "Unknown":
                                    continue

                                persistent_id = track.get("id", "")
                                norm_name = _normalize_for_match(track["name"])
                                norm_artist = _normalize_for_match(track["artist"])
                                norm_album = _normalize_for_match(track["album"])

                                # Try full match first
                                full_key = f"{norm_name}|||{norm_artist}|||{norm_album}"
                                api_data = api_track_map_full.get(full_key)

                                # Fallback to partial match (name+artist)
                                if not api_data:
                                    partial_key = f"{norm_name}|||{norm_artist}"
                                    api_data = api_track_map_partial.get(partial_key)

                                # Last resort: name only (if unique in playlist)
                                if not api_data and api_track_name_counts.get(norm_name, 0) == 1:
                                    api_data = api_track_map_name.get(norm_name)

                                if api_data:
                                    track["explicit"] = api_data["explicit"]

                                    # Cache by all IDs for this track
                                    cache.set_track_metadata(
                                        explicit=api_data["explicit"],
                                        persistent_id=persistent_id,
                                        library_id=api_data["library_id"],
                                        catalog_id=api_data["catalog_id"],
                                        isrc=api_data["isrc"] or None,
                                        name=track["name"],
                                        artist=track["artist"],
                                        album=track.get("album", ""),
                                    )
                                else:
                                    # Cache unmatched track as Unknown to avoid re-fetching
                                    if persistent_id:
                                        cache.set_track_metadata(
                                            explicit="Unknown",
                                            persistent_id=persistent_id,
                                        )

            except Exception:
                pass  # API not available - explicit stays "Unknown"

        # Apply filter
        if filter:
            filter_lower = filter.lower()
            track_data = [
                t for t in track_data
                if filter_lower in t["name"].lower() or filter_lower in t["artist"].lower()
            ]

        # Apply pagination
        track_data, total_count, error = _apply_pagination(track_data, limit, offset)
        if error:
            return error

        safe_name = "".join(c if c.isalnum() else "_" for c in resolved.applescript_name)
        result = format_output(track_data, format, export, full, f"playlist_{safe_name}",
                           total_count=total_count, offset=offset)

        # Add timing and stats
        elapsed = time.time() - start_time
        stats_line = f"\n\n⏱️ {elapsed:.2f}s | Cache: {query_stats['cache_hits']} hits, {query_stats['cache_misses']} misses | API calls: {query_stats['api_calls']}"

        # Log to audit
        if fetch_explicit:
            audit_log.log_action(
                "playlist_query",
                {
                    "playlist": resolved.applescript_name,
                    "track_count": total_count,
                    "duration_sec": round(elapsed, 2),
                    "cache_hits": query_stats["cache_hits"],
                    "cache_misses": query_stats["cache_misses"],
                    "api_calls": query_stats["api_calls"],
                }
            )

        fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
        return result + fuzzy_info + stats_line

    # Use API with ID
    try:
        headers = get_headers()
        all_tracks = []

        # Optimization: if no filter and limit specified, only fetch what we need
        # Use playlist_track_count for total if available
        can_optimize = not filter and limit > 0
        if can_optimize:
            # Fetch only offset+limit tracks
            needed = offset + limit
            api_offset = 0
            while len(all_tracks) < needed:
                batch_limit = min(100, needed - len(all_tracks))
                query_stats["api_calls"] += 1
                response = requests.get(
                    f"{BASE_URL}/me/library/playlists/{resolved.api_id}/tracks",
                    headers=headers,
                    params={"limit": batch_limit, "offset": api_offset},
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code == 404:
                    break
                response.raise_for_status()
                tracks = response.json().get("data", [])
                if not tracks:
                    break
                all_tracks.extend(tracks)
                if len(tracks) < batch_limit:
                    break
                api_offset += batch_limit

            if not all_tracks:
                return "Playlist is empty"

            track_data = [extract_track_data(t, full) for t in all_tracks]

            # Apply pagination locally (skip offset, take limit)
            if offset > 0:
                track_data = track_data[offset:]
            track_data = track_data[:limit]

            # In optimized path, we don't know total count - use fetched count
            total_count = len(all_tracks)

            safe_id = resolved.api_id.replace('.', '_')
            result = format_output(track_data, format, export, full, f"playlist_{safe_id}",
                               total_count=total_count, offset=offset)

            # Add stats line
            elapsed = time.time() - start_time
            stats_line = f"\n\n⏱️ {elapsed:.2f}s | API calls: {query_stats['api_calls']}"
            return result + stats_line

        # Full fetch path (filter specified or no limit)
        api_offset = 0
        while True:
            query_stats["api_calls"] += 1
            response = requests.get(
                f"{BASE_URL}/me/library/playlists/{resolved.api_id}/tracks",
                headers=headers,
                params={"limit": 100, "offset": api_offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)
            if len(tracks) < 100:
                break
            api_offset += 100

        if not all_tracks:
            return "Playlist is empty"

        track_data = [extract_track_data(t, full) for t in all_tracks]

        # Apply filter
        if filter:
            filter_lower = filter.lower()
            track_data = [
                t for t in track_data
                if filter_lower in t["name"].lower() or filter_lower in t["artist"].lower()
            ]

        # Apply pagination
        track_data, total_count, error = _apply_pagination(track_data, limit, offset)
        if error:
            return error

        safe_id = resolved.api_id.replace('.', '_')
        result = format_output(track_data, format, export, full, f"playlist_{safe_id}",
                           total_count=total_count, offset=offset)

        # Add stats line
        elapsed = time.time() - start_time
        stats_line = f"\n\n⏱️ {elapsed:.2f}s | API calls: {query_stats['api_calls']}"
        fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
        return result + fuzzy_info + stats_line

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def search_playlist(
    query: str,
    playlist: str = "",
) -> str:
    """
    Search for tracks in a playlist by name, artist, or album.

    Args:
        query: Search term (matches name, artist, album, etc.)
        playlist: Playlist ID (p.XXX) or name

    Returns: List of matching tracks or "No matches"
    """
    # Resolve playlist parameter
    resolved: ResolvedPlaylist = _resolve_playlist(playlist)
    if resolved.error:
        return resolved.error

    use_api = bool(resolved.api_id)
    use_applescript = bool(resolved.applescript_name)

    matches = []

    # Use AppleScript (only if we don't have API ID)
    if use_applescript and not use_api:
        if not APPLESCRIPT_AVAILABLE:
            return "Error: playlist_name requires macOS"
        # Use native AppleScript search (fast, same as Music app search field)
        success, result = asc.search_playlist(resolved.applescript_name, query)
        if not success:
            return f"Error: {result}"
        for t in result:
            track_id = t.get("id", "")
            matches.append({"name": t["name"], "artist": t["artist"], "id": track_id})
    else:
        # API path: manually filter tracks (cross-platform)
        query_lower = query.lower()
        success, tracks = _get_playlist_track_names(resolved.api_id)
        if not success:
            return f"Error: {tracks}"
        for t in tracks:
            name = t.get("name", "")
            artist = t.get("artist", "")
            album = t.get("album", "")
            track_id = t.get("id", "")
            if (query_lower in name.lower() or
                query_lower in artist.lower() or
                query_lower in album.lower()):
                matches.append({"name": name, "artist": artist, "id": track_id})

    fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)

    if not matches:
        return f"No matches for '{query}'{fuzzy_info}"

    def format_match(m: dict) -> str:
        return f"{m['name']} by {m['artist']} {m['id']}"

    if len(matches) == 1:
        return f"Found: {format_match(matches[0])}{fuzzy_info}"

    output = f"Found {len(matches)} matches:\n"
    output += "\n".join(f"  - {format_match(m)}" for m in matches[:10])
    if len(matches) > 10:
        output += f"\n  ...and {len(matches) - 10} more"
    return output + fuzzy_info


def _is_catalog_id(track_id: str) -> bool:
    """Check if an ID is a catalog ID (numeric) vs library ID (prefixed or hex).

    Catalog IDs are purely numeric (e.g., "1440783617").
    Library IDs are either prefixed (i.XXX, l.XXX, p.XXX) or hexadecimal strings.

    Uses _detect_id_type() internally for consistent ID classification.
    """
    return _detect_id_type(track_id) == "catalog"


def _get_playlist_track_names(playlist_id: str) -> tuple[bool, list[dict] | str]:
    """Get track names from a playlist for duplicate checking."""
    try:
        headers = get_headers()
        all_tracks = []
        offset = 0

        while True:
            response = requests.get(
                f"{BASE_URL}/me/library/playlists/{playlist_id}/tracks",
                headers=headers,
                params={"limit": 100, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)
            if len(tracks) < 100:
                break
            offset += 100

        return True, [
            {
                "id": t.get("id", ""),
                "name": t.get("attributes", {}).get("name", ""),
                "artist": t.get("attributes", {}).get("artistName", ""),
            }
            for t in all_tracks
        ]
    except Exception as e:
        return False, str(e)


def _find_track_in_list(
    tracks: list[dict], track_name: str, artist: str = ""
) -> list[str]:
    """Find matching tracks in a list by name/artist."""
    track_lower = track_name.lower()
    artist_lower = artist.lower() if artist else ""
    matches = []

    for t in tracks:
        if track_lower in t["name"].lower():
            if artist_lower:
                if artist_lower in t["artist"].lower():
                    matches.append(f"{t['name']} - {t['artist']}")
            else:
                matches.append(f"{t['name']} - {t['artist']}")

    return matches


@mcp.tool()
def create_playlist(name: str, description: str = "") -> str:
    """
    Create a new playlist in your Apple Music library.

    Args:
        name: Name for the new playlist
        description: Optional description

    Returns: The new playlist ID
    """
    # Try AppleScript first (local, instant, no auth required)
    if APPLESCRIPT_AVAILABLE:
        success, result = asc.create_playlist(name, description)
        if success:
            audit_log.log_action(
                "create_playlist",
                {"name": name, "playlist_id": result, "method": "applescript"},
                undo_info={"playlist_name": name, "playlist_id": result}
            )
            return f"Created playlist '{name}' (ID: {result})"

    # Fall back to API
    try:
        headers = get_headers()

        body = {"attributes": {"name": name, "description": description}}

        response = requests.post(
            f"{BASE_URL}/me/library/playlists", headers=headers, json=body, timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        playlist_id = data.get("data", [{}])[0].get("id")
        audit_log.log_action(
            "create_playlist",
            {"name": name, "playlist_id": playlist_id, "method": "api"},
            undo_info={"playlist_name": name, "playlist_id": playlist_id}
        )
        return f"Created playlist '{name}' (ID: {playlist_id})"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def add_to_playlist(
    playlist: str = "",
    track: str = "",
    album: str = "",
    artist: str = "",
    allow_duplicates: bool = False,
    verify: bool = True,
    auto_search: Optional[bool] = None,
) -> str:
    """
    Add songs or entire albums to a playlist.

    Handles catalog songs (adds to library first), checks duplicates, verifies success.

    Args:
        playlist: Playlist ID (p.XXX) or name
        track: Catalog ID, library ID (i.XXX), name, CSV, or JSON array (auto-detected)
        album: Album catalog ID, name, or JSON array - adds all tracks
        artist: Artist name (improves matching accuracy)
        allow_duplicates: Skip tracks already in playlist (default: False)
        verify: Confirm track was added (default: True)
        auto_search: Search catalog if not in library (default: user preference)

    Returns: Result with success/failure details
    """
    steps = []  # Track what we did for verbose output

    if not playlist.strip():
        return "Error: playlist parameter required"

    if not track and not album:
        return "Error: Provide track or album parameter"

    # Convert resolved inputs to internal format
    ids_list = []
    names_list = []

    # Resolve track input - handles ID, name, CSV, JSON
    if track:
        resolved_tracks = _resolve_track(track, artist)
        for r in resolved_tracks:
            if r.error:
                return f"Error parsing track input: {r.error}"
            if r.input_type in (InputType.CATALOG_ID, InputType.LIBRARY_ID, InputType.PERSISTENT_ID):
                ids_list.append(r.value)
            elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                names_list.append({"name": r.value, "artist": r.artist})

    # Resolve playlist with fuzzy matching
    # Always resolve first to get fuzzy-matched name, then decide API vs AppleScript mode
    playlist_str = playlist.strip()
    resolved: ResolvedPlaylist
    if playlist_str.startswith("p.") and len(playlist_str) > 2 and playlist_str[2:].isalnum():
        # Explicit playlist ID - use API mode only (no fuzzy matching needed)
        resolved = ResolvedPlaylist(
            raw_input=playlist_str,
            api_id=playlist_str,
            applescript_name=None  # Not available for ID-only input
        )
    else:
        # Resolve playlist with fuzzy matching
        resolved = _resolve_playlist(playlist_str)
        if resolved.error:
            return resolved.error

        # If we have track names and AppleScript is available, prefer AppleScript mode
        # But use the fuzzy-matched applescript_name, not the raw input!
        if APPLESCRIPT_AVAILABLE and names_list and resolved.applescript_name:
            # Clear api_id to force AppleScript mode (searches library directly)
            resolved = ResolvedPlaylist(
                raw_input=resolved.raw_input,
                api_id=None,
                applescript_name=resolved.applescript_name,
                fuzzy_match=resolved.fuzzy_match
            )

    # Resolve album input - get all tracks from album(s)
    if album:
        resolved_albums = _resolve_album(album, artist)
        for r in resolved_albums:
            if r.error:
                steps.append(f"Album error: {r.error}")
                continue

            album_tracks = []
            try:
                headers = get_headers()
                if r.input_type == InputType.CATALOG_ID:
                    # Direct album ID - fetch tracks
                    response = requests.get(
                        f"{BASE_URL}/catalog/{get_storefront()}/albums/{r.value}/tracks",
                        headers=headers,
                        params={"limit": 100},
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code == 200:
                        album_tracks = response.json().get("data", [])
                        steps.append(f"Album {r.value}: found {len(album_tracks)} tracks")
                    else:
                        steps.append(f"Album {r.value}: API error {response.status_code}")
                elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                    # Search for album by name
                    query = f"{r.value} {r.artist}" if r.artist else r.value
                    response = requests.get(
                        f"{BASE_URL}/catalog/{get_storefront()}/search",
                        headers=headers,
                        params={"term": query, "types": "albums", "limit": 5},
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code == 200:
                        albums = response.json().get("results", {}).get("albums", {}).get("data", [])
                        # Find best match
                        found_album = None
                        for alb in albums:
                            attrs = alb.get("attributes", {})
                            if r.value.lower() in attrs.get("name", "").lower():
                                if r.artist:
                                    if r.artist.lower() in attrs.get("artistName", "").lower():
                                        found_album = alb
                                        break
                                else:
                                    found_album = alb
                                    break
                        if not found_album and albums:
                            found_album = albums[0]

                        if found_album:
                            album_id = found_album.get("id")
                            album_name = found_album.get("attributes", {}).get("name", r.value)
                            # Fetch tracks
                            track_response = requests.get(
                                f"{BASE_URL}/catalog/{get_storefront()}/albums/{album_id}/tracks",
                                headers=headers,
                                params={"limit": 100},
                                timeout=REQUEST_TIMEOUT,
                            )
                            if track_response.status_code == 200:
                                album_tracks = track_response.json().get("data", [])
                                steps.append(f"Album '{album_name}': found {len(album_tracks)} tracks")
                        else:
                            steps.append(f"Album '{r.value}': not found in catalog")
                    else:
                        steps.append(f"Album '{r.value}': API error {response.status_code}")

                # Add album tracks to ids_list
                for t in album_tracks:
                    catalog_id = t.get("id")
                    if catalog_id:
                        ids_list.append(catalog_id)

            except Exception as e:
                steps.append(f"Album '{r.value}': {e}")

    # === AppleScript mode (playlist by name, only if no API ID) ===
    if resolved.applescript_name and not resolved.api_id:
        if not APPLESCRIPT_AVAILABLE:
            return "Error: Playlist name requires macOS (use playlist ID like 'p.XXX' for cross-platform)"

        # Apply auto_search preference once
        if auto_search is None:
            prefs = get_user_preferences()
            auto_search = prefs["auto_search"]

        added = []
        errors = []

        # Process names first (from track names or JSON objects)
        for track_obj in names_list:
            name = track_obj["name"]
            track_artist = track_obj["artist"]

            # Check for duplicates
            if not allow_duplicates:
                success, exists = asc.track_exists_in_playlist(
                    resolved.applescript_name, name, track_artist or None
                )
                if success and exists:
                    steps.append(f"Skipped duplicate: {name}")
                    continue

            # Add track
            success, result = asc.add_track_to_playlist(
                resolved.applescript_name, name, track_artist or None
            )
            if success:
                added.append(f"{name} - {track_artist}" if track_artist else name)
            elif "Track not found" in result and auto_search:
                # Auto-search fallback: search catalog, add to library, add to playlist
                search_success, search_result, _ = _auto_search_and_add_to_playlist(
                    name, track_artist or "", resolved.applescript_name
                )
                if search_success:
                    added.append(search_result)
                else:
                    errors.append(f"{name}: {search_result}")
            else:
                errors.append(f"{name}: {result}")

        # Process IDs (catalog or library IDs)
        if ids_list:
            headers = get_headers()

            for track_id in ids_list:
                # Get track info from catalog or library
                if _is_catalog_id(track_id):
                    # Add to library first
                    steps.append(f"Adding catalog ID {track_id} to library...")
                    params = {"ids[songs]": track_id}
                    requests.post(f"{BASE_URL}/me/library", headers=headers, params=params, timeout=REQUEST_TIMEOUT)

                    # Get catalog info
                    response = requests.get(
                        f"{BASE_URL}/catalog/{get_storefront()}/songs/{track_id}", headers=headers, timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code != 200:
                        errors.append(f"Could not get info for {track_id}")
                        continue
                    data = response.json().get("data", [])
                    if not data:
                        continue
                    attrs = data[0].get("attributes", {})
                    name = attrs.get("name", "")
                    artist_name = attrs.get("artistName", "")
                else:
                    # Library ID - look up info
                    response = requests.get(
                        f"{BASE_URL}/me/library/songs/{track_id}", headers=headers, timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code != 200:
                        errors.append(f"Could not get info for {track_id}")
                        continue
                    data = response.json().get("data", [])
                    if not data:
                        continue
                    attrs = data[0].get("attributes", {})
                    name = attrs.get("name", "")
                    artist_name = attrs.get("artistName", "")

                if not name:
                    errors.append(f"No name found for {track_id}")
                    continue

                # Wait a moment for library sync if it was a catalog ID
                if _is_catalog_id(track_id):
                    time.sleep(0.5)

                # Check duplicates for IDs
                if not allow_duplicates:
                    success, exists = asc.track_exists_in_playlist(
                        resolved.applescript_name, name, artist_name or None
                    )
                    if success and exists:
                        steps.append(f"Skipped duplicate: {name}")
                        continue

                # Add via AppleScript
                success, result = asc.add_track_to_playlist(
                    resolved.applescript_name, name, artist_name if artist_name else None
                )
                if success:
                    added.append(f"{name} - {artist_name}" if artist_name else name)
                else:
                    errors.append(f"{name}: {result}")

        # Log successful adds
        if added:
            audit_log.log_action(
                "add_to_playlist",
                {"playlist": resolved.applescript_name, "tracks": added, "method": "applescript"},
                undo_info={"playlist_name": resolved.applescript_name, "tracks": added}
            )

        # Build result
        fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
        if added and not errors:
            return f"Added {len(added)} track(s) to '{resolved.applescript_name}':\n" + "\n".join(f"  + {t}" for t in added) + fuzzy_info
        elif added and errors:
            msg = f"Added {len(added)} track(s), {len(errors)} failed:\n"
            msg += "\n".join(f"  + {t}" for t in added)
            msg += "\nErrors:\n" + "\n".join(f"  - {e}" for e in errors)
            if steps:
                msg += "\n\n" + "\n".join(steps)
            return msg + fuzzy_info
        elif errors:
            msg = "Errors:\n" + "\n".join(f"  - {e}" for e in errors)
            if auto_search is False or (auto_search is None and not get_user_preferences().get("auto_search")):
                msg += "\n\n💡 Tip: Enable auto_search to automatically find and add tracks from catalog"
            return msg + fuzzy_info
        else:
            if steps:
                return "\n".join(steps) + fuzzy_info
            return "No tracks added" + fuzzy_info

    # === API mode (playlist by ID) ===
    try:
        headers = get_headers()
        if not ids_list:
            # In API mode with names, search library first, then catalog
            for track_obj in names_list:
                name = track_obj["name"]
                track_artist = track_obj["artist"]
                library_id, catalog_id, display = _find_track_id(name, track_artist)
                if library_id:
                    ids_list.append(library_id)
                    steps.append(f"Found in library: {display}")
                elif catalog_id:
                    ids_list.append(catalog_id)
                    steps.append(f"Found in catalog: {display}")
                else:
                    steps.append(f"Could not find '{name}' in library or catalog")

        if not ids_list:
            return "Error: No tracks to add\n" + "\n".join(steps)

        library_ids = []
        track_info = {}  # For verbose output

        # Process each ID - add to library if catalog ID
        for track_id in ids_list:
            if _is_catalog_id(track_id):
                # It's a catalog ID - need to add to library first
                steps.append(f"Adding catalog ID {track_id} to library...")

                # Add to library
                params = {"ids[songs]": track_id}
                response = requests.post(
                    f"{BASE_URL}/me/library", headers=headers, params=params, timeout=REQUEST_TIMEOUT,
                )
                if response.status_code not in (200, 202):
                    steps.append(f"  Warning: library add returned {response.status_code}")

                # Get catalog info for the track name
                cat_response = requests.get(
                    f"{BASE_URL}/catalog/{get_storefront()}/songs/{track_id}",
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                if cat_response.status_code == 200:
                    cat_data = cat_response.json().get("data", [])
                    if cat_data:
                        attrs = cat_data[0].get("attributes", {})
                        name = attrs.get("name", "")
                        artist_name = attrs.get("artistName", "")
                        track_info[track_id] = f"{name} - {artist_name}"

                        # Poll library until track appears (up to 1s)
                        found_id = None
                        for attempt in range(10):
                            if attempt > 0:
                                time.sleep(0.1)
                            lib_response = requests.get(
                                f"{BASE_URL}/me/library/search",
                                headers=headers,
                                params={"term": name, "types": "library-songs", "limit": 25},
                                timeout=REQUEST_TIMEOUT,
                            )
                            if lib_response.status_code == 200:
                                lib_data = lib_response.json()
                                songs = lib_data.get("results", {}).get("library-songs", {}).get("data", [])
                                for song in songs:
                                    song_attrs = song.get("attributes", {})
                                    if (song_attrs.get("name", "").lower() == name.lower() and
                                        artist_name.lower() in song_attrs.get("artistName", "").lower()):
                                        found_id = song["id"]
                                        break
                                if found_id:
                                    break
                        if found_id:
                            library_ids.append(found_id)
                            steps.append(f"  Found in library: {name} (ID: {found_id})")
                        else:
                            steps.append(f"  Warning: could not find '{name}' in library after adding")
                else:
                    steps.append(f"  Warning: could not get catalog info for {track_id}")
            else:
                # Already a library ID
                library_ids.append(track_id)

        if not library_ids:
            return "Error: No valid library IDs to add\n" + "\n".join(steps)

        # Check for duplicates
        if not allow_duplicates:
            success, existing = _get_playlist_track_names(resolved.api_id)
            if success and existing:
                filtered_ids = []
                for lib_id in library_ids:
                    # Get track name for this library ID
                    response = requests.get(
                        f"{BASE_URL}/me/library/songs/{lib_id}",
                        headers=headers,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code == 200:
                        data = response.json().get("data", [])
                        if data:
                            attrs = data[0].get("attributes", {})
                            name = attrs.get("name", "")
                            artist_name = attrs.get("artistName", "")
                            matches = _find_track_in_list(existing, name, artist_name)
                            if matches:
                                steps.append(f"Skipped duplicate: {name} - {artist_name}")
                                continue
                    filtered_ids.append(lib_id)
                library_ids = filtered_ids

        if not library_ids:
            steps.append("All tracks already in playlist")
            return "\n".join(steps)

        # Add to playlist
        track_data = [{"id": lid, "type": "library-songs"} for lid in library_ids]
        body = {"data": track_data}

        response = requests.post(
            f"{BASE_URL}/me/library/playlists/{resolved.api_id}/tracks",
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 204:
            steps.append(f"Added {len(library_ids)} track(s) to playlist")
        elif response.status_code == 403:
            return "Error: Cannot edit this playlist (not API-created). Use playlist_name on macOS.\n" + "\n".join(steps)
        elif response.status_code == 500:
            return "Error: Cannot edit this playlist (not API-created). Use playlist_name on macOS.\n" + "\n".join(steps)
        else:
            response.raise_for_status()

        # Verify
        success, updated = _get_playlist_track_names(resolved.api_id)
        if success:
            steps.append(f"Verified: playlist now has {len(updated)} tracks")

        # Log successful add (API mode)
        added_tracks = [track_info.get(tid, tid) for tid in library_ids]
        audit_log.log_action(
            "add_to_playlist",
            {"playlist": resolved.api_id, "tracks": added_tracks, "method": "api"},
            undo_info={"playlist_id": resolved.api_id, "library_ids": library_ids}
        )
        return "\n".join(steps)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}\n" + "\n".join(steps)
    except (FileNotFoundError, ValueError) as e:
        return f"Error: {str(e)}\n" + "\n".join(steps)


@mcp.tool()
def copy_playlist(
    source: str = "",
    new_name: str = ""
) -> str:
    """
    Copy a playlist to a new API-editable playlist.
    Use this to make an editable copy of a read-only playlist.

    Args:
        source: Source playlist ID (p.XXX) or name
        new_name: Name for the new playlist

    Returns: New playlist ID or error
    """
    # Validate inputs
    if not new_name:
        return "Error: new_name is required"

    # Resolve source playlist parameter
    resolved = _resolve_playlist(source)
    if resolved.error:
        return resolved.error

    has_id = bool(resolved.api_id)
    has_name = bool(resolved.applescript_name)

    try:
        headers = get_headers()

        # === AppleScript mode (by name, only if we don't have API ID) ===
        if has_name and not has_id:
            if not APPLESCRIPT_AVAILABLE:
                return "Error: Playlist name requires macOS (use playlist ID like 'p.XXX' for cross-platform)"

            # Get tracks from source playlist via AppleScript
            success, source_tracks = asc.get_playlist_tracks(resolved.applescript_name)
            if not success:
                return f"Error: {source_tracks}"
            if not source_tracks:
                return f"Error: Playlist '{resolved.applescript_name}' is empty"

            # Create new playlist via AppleScript
            success, new_playlist_id = asc.create_playlist(new_name, "")
            if not success:
                return f"Error creating playlist: {new_playlist_id}"

            # Add tracks to new playlist via AppleScript
            added = 0
            failed = []
            for track in source_tracks:
                track_name = track.get("name", "")
                artist = track.get("artist", "")
                if track_name:
                    success, _ = asc.add_track_to_playlist(new_name, track_name, artist if artist else None)
                    if success:
                        added += 1
                    else:
                        failed.append(track_name)

            if failed:
                failed_list = ", ".join(failed[:5])
                if len(failed) > 5:
                    failed_list += f", ... (+{len(failed) - 5} more)"
                audit_log.log_action(
                    "copy_playlist",
                    {"source": resolved.applescript_name, "destination": new_name, "track_count": added, "failed_count": len(failed), "method": "applescript"},
                    undo_info={"playlist_name": new_name, "playlist_id": new_playlist_id}
                )
                fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
                return f"Created '{new_name}' (ID: {new_playlist_id}) with {added}/{len(source_tracks)} tracks. Failed: {failed_list}{fuzzy_info}"
            audit_log.log_action(
                "copy_playlist",
                {"source": resolved.applescript_name, "destination": new_name, "track_count": added, "method": "applescript"},
                undo_info={"playlist_name": new_name, "playlist_id": new_playlist_id}
            )
            fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
            return f"Created '{new_name}' (ID: {new_playlist_id}) with {added} tracks (macOS){fuzzy_info}"

        # === API mode (by ID) ===
        # Get source playlist tracks
        all_tracks = []
        offset = 0
        while True:
            response = requests.get(
                f"{BASE_URL}/me/library/playlists/{resolved.api_id}/tracks",
                headers=headers,
                params={"limit": 100, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break  # End of pagination or empty
            response.raise_for_status()
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)
            if len(tracks) < 100:
                break  # Last page
            offset += 100

        # Create new playlist
        body = {"attributes": {"name": new_name}}
        response = requests.post(
            f"{BASE_URL}/me/library/playlists", headers=headers, json=body, timeout=REQUEST_TIMEOUT,
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
                timeout=REQUEST_TIMEOUT,
            )

        audit_log.log_action(
            "copy_playlist",
            {"source": resolved.api_id, "destination": new_name, "track_count": len(all_tracks), "method": "api"},
            undo_info={"playlist_name": new_name, "playlist_id": new_id}
        )
        fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
        return f"Created '{new_name}' (ID: {new_id}) with {len(all_tracks)} tracks{fuzzy_info}"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ LIBRARY MANAGEMENT ============


@mcp.tool()
def search_library(
    query: str,
    types: str = "songs",
    limit: int = 25,
    format: str = "text",
    export: str = "none",
    full: bool = False,
    fetch_explicit: Optional[bool] = None,
    clean_only: Optional[bool] = None,
) -> str:
    """
    Search your personal Apple Music library.

    On macOS, uses AppleScript for fast local search. Falls back to API elsewhere.

    Args:
        query: Search term
        types: Type of search - songs, artists, albums, or all (macOS only)
        limit: Max results (default 25, up to 100 on macOS)
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports (artwork, track numbers, etc.)
        fetch_explicit: If True, fetch explicit status via API (uses cache for speed).
                       Uses user preference from config if not specified.
        clean_only: If True, filter out explicit content.
                   Uses user preference from config if not specified.

    Returns: Library items with IDs (library IDs can be added to playlists)
    """
    # Apply user preferences
    prefs = get_user_preferences()
    if fetch_explicit is None:
        fetch_explicit = prefs["fetch_explicit"]
    if clean_only is None:
        clean_only = prefs["clean_only"]

    # Try AppleScript on macOS (faster for local searches)
    if APPLESCRIPT_AVAILABLE:
        success, results = asc.search_library(query, types)
        if success and results:
            # Enrich with explicit status if requested
            if fetch_explicit or clean_only:
                cache = get_track_cache()
                for track in results:
                    track_id = track.get("id", "")
                    if track_id:
                        cached_explicit = cache.get_explicit(track_id)
                        if cached_explicit:
                            track["explicit"] = cached_explicit
                        else:
                            track["explicit"] = "Unknown"
                    else:
                        track["explicit"] = "Unknown"

            # Deduplicate by track ID (AppleScript can return duplicates)
            results = _deduplicate_by_id(results, keep_no_id=True)

            # Filter explicit content if clean_only
            if clean_only:
                results = [t for t in results if t.get("explicit") != "Yes"]

            return format_output(results, format, export, full, f"search_{query[:20]}")
        # AppleScript found nothing or failed - fall through to API

    # API fallback (or primary on non-macOS)
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/me/library/search",
            headers=headers,
            params={"term": query, "types": "library-songs", "limit": min(limit, 25)},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        songs = data.get("results", {}).get("library-songs", {}).get("data", [])
        if not songs:
            return "No songs found"

        song_data = [extract_track_data(s, full) for s in songs]

        # Deduplicate by track ID (API can return duplicates)
        song_data = _deduplicate_by_id(song_data)

        # Filter explicit content if clean_only
        if clean_only:
            song_data = [s for s in song_data if s.get("explicit") != "Yes"]

        return format_output(song_data, format, export, full, f"search_{query[:20]}")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def add_to_library(
    track: str = "",
    album: str = "",
    artist: str = "",
) -> str:
    """
    Add content from the Apple Music catalog to your library.

    Args:
        track: Catalog ID, name, CSV, or JSON array (auto-detected)
        album: Album catalog ID, name, or JSON array
        artist: Artist name (improves matching accuracy)

    Returns: Confirmation with what was added
    """
    added = []
    errors = []

    if not track and not album:
        return "Error: Provide track or album parameter"

    # Helper to add a song by catalog search
    def _add_track_by_search(name: str, search_artist: str) -> None:
        song, error, fuzzy_result = _find_matching_catalog_song(name, search_artist)
        if error:
            errors.append(f"{name}: {error}")
            return
        attrs = song.get("attributes", {})
        catalog_id = song.get("id")
        success, msg = _add_to_library_api([catalog_id], "songs")
        if success:
            result_name = attrs.get('name', name)
            result_artist = attrs.get('artistName', 'Unknown')
            added_msg = f"{result_name} by {result_artist}"
            if fuzzy_result:
                added_msg += f" (fuzzy: '{fuzzy_result.query}' → '{fuzzy_result.matched_name}')"
            added.append(added_msg)
        else:
            errors.append(f"{name}: {msg}")

    # Helper to add an album by catalog search
    def _add_album_by_search(name: str, search_artist: str) -> None:
        album, error, fuzzy_result = _find_matching_catalog_album(name, search_artist)
        if error:
            errors.append(f"Album '{name}': {error}")
            return
        attrs = album.get("attributes", {})
        catalog_id = album.get("id")
        success, msg = _add_to_library_api([catalog_id], "albums")
        if success:
            result_name = attrs.get('name', name)
            result_artist = attrs.get('artistName', 'Unknown')
            added_msg = f"Album: {result_name} by {result_artist}"
            if fuzzy_result:
                added_msg += f" (fuzzy: '{fuzzy_result.query}' → '{fuzzy_result.matched_name}')"
            added.append(added_msg)
        else:
            errors.append(f"Album '{name}': {msg}")

    # Process tracks
    if track:
        resolved_tracks = _resolve_track(track, artist)
        for r in resolved_tracks:
            if r.error:
                errors.append(f"Track parse error: {r.error}")
                continue

            if r.input_type == InputType.CATALOG_ID:
                # Direct catalog ID
                success, msg = _add_to_library_api([r.value], "songs")
                if success:
                    added.append(f"Track ID {r.value}")
                else:
                    errors.append(f"Track {r.value}: {msg}")
            elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                # Search by name
                _add_track_by_search(r.value, r.artist)
            else:
                # Library ID or persistent ID - already in library
                errors.append(f"Track {r.value}: Already a library ID, not a catalog ID")

    # Process albums
    if album:
        resolved_albums = _resolve_album(album, artist)
        for r in resolved_albums:
            if r.error:
                errors.append(f"Album parse error: {r.error}")
                continue

            if r.input_type == InputType.CATALOG_ID:
                # Direct catalog ID
                success, msg = _add_to_library_api([r.value], "albums")
                if success:
                    added.append(f"Album ID {r.value}")
                else:
                    errors.append(f"Album {r.value}: {msg}")
            elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                # Search by name
                _add_album_by_search(r.value, r.artist)
            else:
                # Library ID - already in library
                errors.append(f"Album {r.value}: Already a library ID")

    # Log successful additions
    if added:
        audit_log.log_action(
            "add_to_library",
            {"items": added, "mode": "unified"},
        )

    # Build result message
    if added and not errors:
        return f"Added {len(added)} item(s) to library:\n" + "\n".join(f"  + {a}" for a in added)
    elif added and errors:
        msg = f"Added {len(added)} item(s), {len(errors)} failed:\n"
        msg += "\n".join(f"  + {a}" for a in added)
        msg += "\nErrors:\n" + "\n".join(f"  - {e}" for e in errors)
        return msg
    elif errors:
        return "Errors:\n" + "\n".join(f"  - {e}" for e in errors)
    else:
        return "No items added"


@mcp.tool()
def get_recently_played(
    limit: int = 30,
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get recently played tracks from your Apple Music history.

    Args:
        limit: Number of tracks to return (default 30, max 50)
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Recently played tracks in requested format
    """
    try:
        headers = get_headers()
        all_tracks = []
        max_limit = min(limit, 50)

        # API limits to 10 per request, paginate up to max
        for offset in range(0, max_limit, 10):
            batch_limit = min(10, max_limit - offset)
            response = requests.get(
                f"{BASE_URL}/me/recent/played/tracks",
                headers=headers,
                params={"limit": batch_limit, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                break
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)

        if not all_tracks:
            return "No recently played tracks"

        track_data = [extract_track_data(t, full) for t in all_tracks]
        return format_output(track_data, format, export, full, "recently_played")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ CATALOG SEARCH ============


@mcp.tool()
def search_catalog(
    query: str = "",
    types: str = "songs",
    limit: int = 15,
    format: str = "text",
    export: str = "none",
    full: bool = False,
    clean_only: Optional[bool] = None,
) -> str:
    """
    Search the Apple Music catalog.

    Args:
        query: Search term (leave empty with types="music-videos" for featured videos)
        types: Comma-separated types (songs, albums, artists, playlists, music-videos)
        limit: Max results per type (default 15)
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file (songs only)
        full: Include all metadata in exports
        clean_only: If True, filter out explicit content (songs only).
                   Uses user preference from config if not specified.

    To set default: config(action="set-pref", preference="clean_only", value=True)

    Returns: Search results with catalog IDs (use add_to_library to add songs)
    """
    # Apply user preferences
    if clean_only is None:
        prefs = get_user_preferences()
        clean_only = prefs["clean_only"]

    # Require query for non-music-videos types
    if not query and types != "music-videos":
        return "Error: query required (except for types='music-videos' which shows featured)"

    try:
        headers = get_headers()

        # Handle music-videos with empty query (get featured/charts)
        if types == "music-videos" and not query:
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/charts",
                headers=headers,
                params={"types": "music-videos", "limit": min(limit, 25)},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            charts = data.get("results", {}).get("music-videos", [])
            videos = charts[0].get("data", []) if charts else []
            results = {"music-videos": {"data": videos}}
        else:
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/search",
                headers=headers,
                params={"term": query, "types": types, "limit": min(limit, 25)},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", {})

        # Collect all data for JSON format
        all_data = {"songs": [], "albums": [], "artists": [], "playlists": [], "music-videos": []}

        if "songs" in results:
            all_data["songs"] = [extract_track_data(s, full) for s in results["songs"].get("data", [])]
            # Deduplicate by track ID (API can return duplicates)
            all_data["songs"] = _deduplicate_by_id(all_data["songs"])
            # Filter out explicit content if clean_only is True
            if clean_only:
                all_data["songs"] = [s for s in all_data["songs"] if s.get("explicit") == "No"]

        if "albums" in results:
            for album in results["albums"].get("data", []):
                attrs = album.get("attributes", {})
                all_data["albums"].append({
                    "id": album.get("id"), "name": attrs.get("name"),
                    "artist": attrs.get("artistName"), "track_count": attrs.get("trackCount", 0),
                    "year": attrs.get("releaseDate", "")[:4],
                })

        if "artists" in results:
            for artist in results["artists"].get("data", []):
                attrs = artist.get("attributes", {})
                all_data["artists"].append({
                    "id": artist.get("id"), "name": attrs.get("name"),
                    "genres": attrs.get("genreNames", []),
                })

        if "playlists" in results:
            for pl in results["playlists"].get("data", []):
                attrs = pl.get("attributes", {})
                all_data["playlists"].append({
                    "id": pl.get("id"), "name": attrs.get("name"),
                    "curator": attrs.get("curatorName", ""),
                })

        if "music-videos" in results:
            for video in results["music-videos"].get("data", []):
                attrs = video.get("attributes", {})
                all_data["music-videos"].append({
                    "id": video.get("id"),
                    "name": attrs.get("name", ""),
                    "artist": attrs.get("artistName", ""),
                    "duration": format_duration(attrs.get("durationInMillis", 0)),
                })

        # Handle export (songs only)
        export_msg = ""
        if export not in ("", "none") and all_data["songs"]:
            export_msg = "\n" + format_output(all_data["songs"], "text", export, full, f"catalog_{query[:20]}").split("\n")[-1]

        # JSON format - return all data
        if format == "json":
            return json.dumps(all_data, indent=2) + export_msg

        # Text format
        output = []
        if all_data["songs"]:
            output.append(f"=== {len(all_data['songs'])} Songs ===")
            for s in all_data["songs"]:
                explicit_marker = " [Explicit]" if s.get("explicit") == "Yes" else ""
                output.append(f"{s['name']} - {s['artist']} ({s['duration']}) {s['album']} [{s['year']}]{explicit_marker} {s['id']}")

        if all_data["albums"]:
            output.append(f"\n=== {len(all_data['albums'])} Albums ===")
            for a in all_data["albums"]:
                output.append(f"  {a['name']} - {a['artist']} ({a['track_count']} tracks) [{a['year']}] {a['id']}")

        if all_data["artists"]:
            output.append(f"\n=== {len(all_data['artists'])} Artists ===")
            for a in all_data["artists"]:
                output.append(f"  {a['name']} {a['id']}")

        if all_data["playlists"]:
            output.append(f"\n=== {len(all_data['playlists'])} Playlists ===")
            for p in all_data["playlists"]:
                output.append(f"  {p['name']} {p['id']}")

        if all_data["music-videos"]:
            output.append(f"\n=== {len(all_data['music-videos'])} Music Videos ===")
            for v in all_data["music-videos"]:
                output.append(f"  {v['name']} - {v['artist']} ({v['duration']}) {v['id']}")

        return ("\n".join(output) + export_msg) if output else "No results found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_album_tracks(
    album: str = "",
    artist: str = "",
    limit: int = 0,
    offset: int = 0,
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get all tracks from an album.

    Args:
        album: Catalog ID, library ID (l.XXX), or name (auto-detected)
        artist: Artist name (improves matching for name lookups)
        limit: Max tracks (default: all)
        offset: Skip first N tracks (for pagination)
        format: "text", "json", "csv", or "none"
        export: "none", "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Track listing in requested format
    """
    if not album:
        return "Error: Provide album parameter"

    # Resolve album input
    resolved = _resolve_album(album, artist)
    if not resolved:
        return "Error: Could not resolve album"

    r = resolved[0]  # Only use first resolved album
    if r.error:
        return f"Error: {r.error}"

    album_id = None

    if r.input_type == InputType.CATALOG_ID:
        album_id = r.value
    elif r.input_type == InputType.ALBUM_ID:
        album_id = r.value
    elif r.input_type == InputType.NAME:
        # Search for album by name
        try:
            headers = get_headers()
            search_term = f"{r.value} {r.artist}".strip() if r.artist else r.value
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/search",
                headers=headers,
                params={"term": search_term, "types": "albums", "limit": 5},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                albums = response.json().get("results", {}).get("albums", {}).get("data", [])
                for a in albums:
                    attrs = a.get("attributes", {})
                    album_name = attrs.get("name", "")
                    album_artist = attrs.get("artistName", "")
                    if r.value.lower() in album_name.lower():
                        if not r.artist or r.artist.lower() in album_artist.lower():
                            album_id = a.get("id")
                            break
        except Exception:
            pass
        if not album_id:
            return f"Album not found: {r.value}"
    else:
        return f"Unsupported input type for album lookup"

    try:
        headers = get_headers()

        # Detect if it's a library or catalog ID
        if album_id.startswith("l."):
            base_url = f"{BASE_URL}/me/library/albums/{album_id}/tracks"
        else:
            base_url = f"{BASE_URL}/catalog/{get_storefront()}/albums/{album_id}/tracks"

        # Paginate to handle box sets / compilations with 100+ tracks
        all_tracks = []
        api_offset = 0

        while True:
            response = requests.get(
                base_url,
                headers=headers,
                params={"limit": 100, "offset": api_offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            tracks = response.json().get("data", [])
            if not tracks:
                break
            all_tracks.extend(tracks)
            if len(tracks) < 100:
                break
            api_offset += 100

        if not all_tracks:
            return "No tracks found"

        # Extract track data with extras for numbered display
        track_data = [extract_track_data(t, include_extras=True) for t in all_tracks]

        # Apply pagination
        track_data, total_count, error = _apply_pagination(track_data, limit, offset)
        if error:
            return error

        return format_output(track_data, format, export, full, f"album_{album_id.replace('.', '_')}",
                           total_count=total_count, offset=offset)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ LIBRARY BROWSING ============


@mcp.tool()
def browse_library(
    item_type: str = "songs",
    limit: int = 100,
    offset: int = 0,
    format: str = "text",
    export: str = "none",
    full: bool = False,
    fetch_explicit: Optional[bool] = None,
    clean_only: Optional[bool] = None,
) -> str:
    """
    Browse your Apple Music library by type.

    Args:
        item_type: What to browse - songs, albums, artists, or videos
        limit: Max items (default 100). Omit or set higher to retrieve more.
        offset: Skip first N items (for pagination)
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports
        fetch_explicit: If True, fetch explicit status via API (uses cache for speed).
                       Uses user preference from config if not specified. Songs only.
        clean_only: If True, filter out explicit content.
                   Uses user preference from config if not specified. Songs only.

    Returns: Item listing in requested format
    """
    item_type = item_type.lower().strip()

    # Apply user preferences (only relevant for songs)
    prefs = get_user_preferences()
    if fetch_explicit is None:
        fetch_explicit = prefs["fetch_explicit"]
    if clean_only is None:
        clean_only = prefs["clean_only"]

    # Try AppleScript first for songs (local, instant, no auth required)
    if APPLESCRIPT_AVAILABLE and item_type == "songs":
        success, as_songs = asc.get_library_songs(limit)
        if success:
            if not as_songs:
                return f"No {item_type} in library"
            data = []
            for s in as_songs:
                data.append({
                    "name": s.get("name", ""),
                    "artist": s.get("artist", ""),
                    "album": s.get("album", ""),
                    "duration": s.get("duration", ""),
                    "genre": s.get("genre", ""),
                    "year": s.get("year", ""),
                    "id": s.get("id", ""),
                    "explicit": "Unknown",
                })

            # Enrich with explicit status if requested
            if fetch_explicit or clean_only:
                cache = get_track_cache()
                for track in data:
                    track_id = track.get("id", "")
                    if track_id:
                        cached_explicit = cache.get_explicit(track_id)
                        if cached_explicit:
                            track["explicit"] = cached_explicit

            # Filter explicit content if clean_only
            if clean_only:
                data = [t for t in data if t.get("explicit") != "Yes"]

            # Apply pagination
            data, total_count, error = _apply_pagination(data, limit, offset)
            if error:
                return error

            return format_output(data, format, export, full, "songs",
                               total_count=total_count, offset=offset)
        # AppleScript failed - fall through to API

    # Fall back to API
    try:
        headers = get_headers()

        # Map type to API endpoint
        type_map = {
            "songs": "library-songs",
            "albums": "library/albums",
            "artists": "library/artists",
            "videos": "library/music-videos",
        }
        if item_type not in type_map:
            return f"Invalid type: {item_type}. Use: songs, albums, artists, or videos"

        endpoint = type_map[item_type]
        all_items = []
        api_offset = 0
        fetch_all = limit == 0
        # Need to fetch enough for both offset and limit
        max_to_fetch = (offset + limit) if not fetch_all else float('inf')

        # Paginate
        while len(all_items) < max_to_fetch:
            batch_limit = 100 if fetch_all else min(100, int(max_to_fetch - len(all_items)))
            url = f"{BASE_URL}/me/{endpoint}" if "/" in endpoint else f"{BASE_URL}/me/library/songs"
            response = requests.get(
                url,
                headers=headers,
                params={"limit": batch_limit, "offset": api_offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            items = response.json().get("data", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 100:
                break
            api_offset += 100

        if not all_items:
            return f"No {item_type} in library"

        # Extract data based on type
        if item_type == "songs":
            data = [extract_track_data(s, full) for s in all_items]
        elif item_type == "albums":
            data = []
            for album in all_items:
                attrs = album.get("attributes", {})
                genres = attrs.get("genreNames", [])
                data.append({
                    "id": album.get("id", ""),
                    "name": attrs.get("name", ""),
                    "artist": attrs.get("artistName", ""),
                    "track_count": attrs.get("trackCount", 0),
                    "genre": genres[0] if genres else "",
                    "release_date": attrs.get("releaseDate", ""),
                })
        elif item_type == "artists":
            data = [{"id": a.get("id", ""), "name": a.get("attributes", {}).get("name", "")} for a in all_items]
        else:  # videos
            data = [{"id": v.get("id", ""), "name": v.get("attributes", {}).get("name", ""),
                     "artist": v.get("attributes", {}).get("artistName", "")} for v in all_items]

        # Filter explicit content if clean_only (songs only, API already has explicit status)
        if item_type == "songs" and clean_only:
            data = [t for t in data if t.get("explicit") != "Yes"]

        # Apply pagination
        data, total_count, error = _apply_pagination(data, limit, offset)
        if error:
            return error

        return format_output(data, format, export, full, f"library_{item_type}",
                           total_count=total_count, offset=offset)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ DISCOVERY & PERSONALIZATION ============


@mcp.tool()
def get_recommendations(
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get personalized music recommendations based on your listening history.

    Args:
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Recommendations in requested format
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/me/recommendations",
            headers=headers,
            params={"limit": 10},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        all_items = []
        for rec in data.get("data", []):
            attrs = rec.get("attributes", {})
            title = attrs.get("title", {}).get("stringForDisplay", "Recommendation")
            relationships = rec.get("relationships", {})
            contents = relationships.get("contents", {}).get("data", [])

            for item in contents[:8]:
                item_attrs = item.get("attributes", {})
                all_items.append({
                    "category": title,
                    "name": item_attrs.get("name", "Unknown"),
                    "artist": item_attrs.get("artistName", ""),
                    "type": item.get("type", "").replace("library-", ""),
                    "id": item.get("id"),
                    "year": item_attrs.get("releaseDate", "")[:4],
                })

        return format_output(all_items, format, export, full, "recommendations")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_heavy_rotation(
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get your heavy rotation - content you've been playing frequently.

    Args:
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Heavy rotation items in requested format
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/me/history/heavy-rotation",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        items = data.get("data", [])
        if not items:
            return "No heavy rotation data"

        # Extract item data
        item_data = []
        for item in items:
            attrs = item.get("attributes", {})
            genres = attrs.get("genreNames", [])

            item_data.append({
                "id": item.get("id", ""),
                "name": attrs.get("name", ""),
                "artist": attrs.get("artistName", ""),
                "type": item.get("type", "").replace("library-", "").replace("-", " "),
                "track_count": attrs.get("trackCount", ""),
                "genre": genres[0] if genres else "",
                "release_date": attrs.get("releaseDate", ""),
                "date_added": attrs.get("dateAdded", ""),
                "artwork_url": attrs.get("artwork", {}).get("url", "").replace("{w}x{h}", "500x500"),
            })

        return format_output(item_data, format, export, full, "heavy_rotation")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_recently_added(
    limit: int = 50,
    format: str = "text",
    export: str = "none",
    full: bool = False,
) -> str:
    """
    Get content recently added to your library.

    Args:
        limit: Number of items to return (default 50)
        format: "text" (default), "json", "csv", or "none" (export only)
        export: "none" (default), "csv", or "json" to write file
        full: Include all metadata in exports

    Returns: Recently added items in requested format
    """
    try:
        headers = get_headers()
        all_items = []
        offset = 0
        max_to_fetch = min(limit, 100)

        # Paginate
        while len(all_items) < max_to_fetch:
            batch_limit = min(25, max_to_fetch - len(all_items))
            response = requests.get(
                f"{BASE_URL}/me/library/recently-added",
                headers=headers,
                params={"limit": batch_limit, "offset": offset},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                break
            response.raise_for_status()
            items = response.json().get("data", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < batch_limit:
                break
            offset += 25  # Recently-added uses fixed 25-item pages

        if not all_items:
            return "No recently added content"

        # Extract item data
        item_data = []
        for item in all_items:
            attrs = item.get("attributes", {})
            genres = attrs.get("genreNames", [])

            item_data.append({
                "id": item.get("id", ""),
                "name": attrs.get("name", ""),
                "artist": attrs.get("artistName", ""),
                "type": item.get("type", "").replace("library-", ""),
                "track_count": attrs.get("trackCount", ""),
                "genre": genres[0] if genres else "",
                "release_date": attrs.get("releaseDate", ""),
                "date_added": attrs.get("dateAdded", ""),
                "artwork_url": attrs.get("artwork", {}).get("url", "").replace("{w}x{h}", "500x500"),
            })

        return format_output(item_data, format, export, full, "recently_added")

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_artist_top_songs(artist: str) -> str:
    """
    Get an artist's top/most popular songs.

    Args:
        artist: Artist name or catalog ID

    Returns: Artist's top songs with catalog IDs
    """
    if not artist:
        return "Error: Provide artist parameter"

    try:
        headers = get_headers()

        # Check if it's a catalog ID (all digits)
        if artist.isdigit():
            artist_id = artist
            # Look up artist name
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json().get("data", [])
                artist_actual_name = data[0].get("attributes", {}).get("name", artist) if data else artist
            else:
                artist_actual_name = artist
        else:
            # Search for artist by name
            search_response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/search",
                headers=headers,
                params={"term": artist, "types": "artists", "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            search_response.raise_for_status()
            artists = search_response.json().get("results", {}).get("artists", {}).get("data", [])

            if not artists:
                return f"No artist found matching '{artist}'"

            artist_data = artists[0]
            artist_id = artist_data.get("id")
            artist_actual_name = artist_data.get("attributes", {}).get("name", artist)

        # Get top songs
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}/view/top-songs",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        songs = response.json().get("data", [])

        output = [f"=== Top Songs by {artist_actual_name} ==="]
        for i, song in enumerate(songs, 1):
            attrs = song.get("attributes", {})
            name = attrs.get("name", "Unknown")
            album = attrs.get("albumName", "")
            song_id = song.get("id")
            output.append(f"{i}. {name}" + (f" ({album})" if album else "") + f" [catalog ID: {song_id}]")

        return "\n".join(output) if len(output) > 1 else "No top songs found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_similar_artists(artist: str) -> str:
    """
    Get artists similar to a given artist.

    Args:
        artist: Artist name or catalog ID

    Returns: List of similar artists
    """
    if not artist:
        return "Error: Provide artist parameter"

    try:
        headers = get_headers()

        # Check if it's a catalog ID (all digits)
        if artist.isdigit():
            artist_id = artist
            # Look up artist name
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json().get("data", [])
                artist_actual_name = data[0].get("attributes", {}).get("name", artist) if data else artist
            else:
                artist_actual_name = artist
        else:
            # Search for artist by name
            search_response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/search",
                headers=headers,
                params={"term": artist, "types": "artists", "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            search_response.raise_for_status()
            artists = search_response.json().get("results", {}).get("artists", {}).get("data", [])

            if not artists:
                return f"No artist found matching '{artist}'"

            artist_data = artists[0]
            artist_id = artist_data.get("id")
            artist_actual_name = artist_data.get("attributes", {}).get("name", artist)

        # Get similar artists
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}/view/similar-artists",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        similar = response.json().get("data", [])

        output = [f"=== Artists Similar to {artist_actual_name} ==="]
        for artist in similar:
            attrs = artist.get("attributes", {})
            name = attrs.get("name", "Unknown")
            genres = ", ".join(attrs.get("genreNames", [])[:2])
            artist_id = artist.get("id")
            output.append(f"{name} ({genres}) [artist ID: {artist_id}]")

        return "\n".join(output) if len(output) > 1 else "No similar artists found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_song_station(song_id: str) -> str:
    """
    Get the radio station based on a song. Great for discovering similar music.

    Args:
        song_id: Catalog song ID (from search_catalog)

    Returns: Station info that you can reference when asking for similar music
    """
    try:
        headers = get_headers()

        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/songs/{song_id}/station",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        stations = data.get("data", [])
        if not stations:
            return "No station found for this song"

        station = stations[0]
        attrs = station.get("attributes", {})
        name = attrs.get("name", "Unknown Station")
        station_id = station.get("id")

        return f"Station: {name}\nStation ID: {station_id}\n\nUse this station to discover music similar to this song."

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ RATINGS ============


@mcp.tool()
def rating(
    action: str,
    track: str = "",
    artist: str = "",
    stars: int = 0,
) -> str:
    """
    Rate tracks: love, dislike, get stars, or set stars.

    Args:
        action: "love", "dislike", "get", or "set"
        track: Catalog ID or name (auto-detected)
        artist: Artist name (improves matching for name lookups)
        stars: 0-5 stars (for "set" action only)

    Note: get/set require macOS. love/dislike work cross-platform.

    Returns: Rating info or confirmation
    """
    action = action.lower().strip()

    if not track:
        return "Error: Provide track parameter"

    if action not in ("love", "dislike", "get", "set"):
        return f"Invalid action: {action}. Use: love, dislike, get, set"

    # Resolve track input (only single track supported for rating)
    resolved = _resolve_track(track, artist)
    if not resolved:
        return "Error: Could not resolve track"

    r = resolved[0]  # Only use first resolved track
    if r.error:
        return f"Error: {r.error}"

    track_name = ""
    track_artist = r.artist or artist

    # Handle based on input type
    if r.input_type == InputType.CATALOG_ID:
        catalog_id = r.value

        # Direct API rating for love/dislike
        if action in ("love", "dislike"):
            success, msg = _rate_song_api(catalog_id, action)
            if success:
                audit_log.log_action(
                    "rating",
                    {"track": f"catalog_id:{catalog_id}", "type": action, "method": "api"},
                )
                return f"Set '{action}' for song {catalog_id}"
            return f"Error: {msg}"

        # For get/set, need to look up track name for AppleScript
        if not APPLESCRIPT_AVAILABLE:
            return "Error: Star ratings require macOS"
        try:
            headers = get_headers()
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/songs/{catalog_id}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    attrs = data[0].get("attributes", {})
                    track_name = attrs.get("name", "")
                    track_artist = attrs.get("artistName", "")
        except Exception:
            pass
        if not track_name:
            return f"Error: Could not find track info for catalog ID {catalog_id}"

    elif r.input_type == InputType.PERSISTENT_ID:
        # Persistent IDs can't be used for rating - need track name or catalog ID
        return f"Error: Persistent ID {r.value} not supported for rating - use track name or catalog ID"

    elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
        track_name = r.value
        track_artist = r.artist or artist

    elif r.input_type == InputType.LIBRARY_ID:
        return f"Error: Library ID {r.value} not supported for rating - use track name or catalog ID"

    # Now we have track_name, handle each action
    if action == "get":
        if not APPLESCRIPT_AVAILABLE:
            return "Error: Star ratings require macOS"
        success, rating_val = asc.get_rating(track_name, track_artist if track_artist else None)
        if success:
            s = rating_val // 20
            return f"{track_name}: {'★' * s}{'☆' * (5 - s)} ({rating_val}/100)"
        return f"Error: {rating_val}"

    if action == "set":
        if not APPLESCRIPT_AVAILABLE:
            return "Error: Star ratings require macOS"
        rating_val = max(0, min(5, stars)) * 20
        success, result = asc.set_rating(track_name, rating_val, track_artist if track_artist else None)
        if success:
            track_desc = f"{track_name} - {track_artist}" if track_artist else track_name
            audit_log.log_action(
                "rating",
                {"track": track_desc, "type": "set_stars", "value": stars, "method": "applescript"},
            )
            return f"Set {track_name} to {'★' * stars}{'☆' * (5 - stars)}"
        return f"Error: {result}"

    # Love/dislike by name - try AppleScript first
    if APPLESCRIPT_AVAILABLE:
        func = asc.love_track if action == "love" else asc.dislike_track
        success, result = func(track_name, track_artist if track_artist else None)
        if success:
            track_desc = f"{track_name} - {track_artist}" if track_artist else track_name
            audit_log.log_action(
                "rating",
                {"track": track_desc, "type": action, "method": "applescript"},
            )
            return result

    # API fallback for love/dislike
    search_term = f"{track_name} {track_artist}".strip() if track_artist else track_name
    songs = _search_catalog_songs(search_term, limit=5)

    for song in songs:
        attrs = song.get("attributes", {})
        song_name = attrs.get("name", "")
        song_artist = attrs.get("artistName", "")
        if track_name.lower() in song_name.lower():
            if not track_artist or track_artist.lower() in song_artist.lower():
                success, msg = _rate_song_api(song.get("id"), action)
                if success:
                    audit_log.log_action(
                        "rating",
                        {"track": f"{song_name} by {song_artist}", "type": action, "method": "api_fallback"},
                    )
                    return f"{action.capitalize()}d: {song_name} by {song_artist}"
                return f"Error: {msg}"

    return f"Track not found: {track_name}"


# ============ CATALOG DETAILS ============


@mcp.tool()
def get_song_details(song_id: str) -> str:
    """
    Get detailed information about a song from the catalog.

    Args:
        song_id: Catalog song ID

    Returns: Song details including album, duration, genre, etc.
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/songs/{song_id}",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        songs = data.get("data", [])
        if not songs:
            return "Song not found"

        attrs = songs[0].get("attributes", {})
        duration = format_duration(attrs.get("durationInMillis", 0)) or "Unknown"
        output = [
            f"Title: {attrs.get('name', 'Unknown')}",
            f"Artist: {attrs.get('artistName', 'Unknown')}",
            f"Album: {attrs.get('albumName', 'Unknown')}",
            f"Genre: {', '.join(attrs.get('genreNames', ['Unknown']))}",
            f"Duration: {duration}",
            f"Release Date: {attrs.get('releaseDate', 'Unknown')}",
            f"Explicit: {'Yes' if attrs.get('contentRating') == 'explicit' else 'No'}",
            f"ISRC: {attrs.get('isrc', 'N/A')}",
        ]

        return "\n".join(output)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_artist_details(artist: str) -> str:
    """
    Get detailed information about an artist.

    Args:
        artist: Artist name or catalog ID

    Returns: Artist details including genres and albums
    """
    if not artist:
        return "Error: Provide artist parameter"

    try:
        headers = get_headers()

        # Check if it's a catalog ID (all digits)
        if artist.isdigit():
            artist_id = artist
            # Look up artist details directly
            response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}",
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return f"Artist with ID {artist_id} not found"
            data = response.json().get("data", [])
            if not data:
                return f"Artist with ID {artist_id} not found"
            artist_data = data[0]
            attrs = artist_data.get("attributes", {})
        else:
            # Search for the artist by name
            search_response = requests.get(
                f"{BASE_URL}/catalog/{get_storefront()}/search",
                headers=headers,
                params={"term": artist, "types": "artists", "limit": 1},
                timeout=REQUEST_TIMEOUT,
            )
            search_response.raise_for_status()
            search_data = search_response.json()

            artists = search_data.get("results", {}).get("artists", {}).get("data", [])
            if not artists:
                return f"No artist found matching '{artist}'"

            artist_data = artists[0]
            artist_id = artist_data.get("id")
            attrs = artist_data.get("attributes", {})

        output = [
            f"Artist: {attrs.get('name', 'Unknown')}",
            f"Artist ID: {artist_id}",
            f"Genres: {', '.join(attrs.get('genreNames', ['Unknown']))}",
        ]

        # Get artist's albums
        albums_response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/artists/{artist_id}/albums",
            headers=headers,
            params={"limit": 10},
            timeout=REQUEST_TIMEOUT,
        )
        if albums_response.status_code == 200:
            albums_data = albums_response.json()
            albums = albums_data.get("data", [])
            if albums:
                output.append("\nRecent Albums:")
                for album in albums[:10]:
                    album_attrs = album.get("attributes", {})
                    name = album_attrs.get("name", "Unknown")
                    year = album_attrs.get("releaseDate", "")[:4]
                    album_id = album.get("id")
                    output.append(f"  - {name} ({year}) [catalog ID: {album_id}]")

        return "\n".join(output)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_charts(chart_type: str = "songs") -> str:
    """
    Get Apple Music charts (top songs, albums, etc.).

    Args:
        chart_type: Type of chart - 'songs', 'albums', 'music-videos', or 'playlists'

    Returns: Current chart listings
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/charts",
            headers=headers,
            params={"types": chart_type, "limit": 20},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        output = []
        results = data.get("results", {})

        for chart_name, chart_data in results.items():
            for chart in chart_data:
                chart_title = chart.get("name", chart_name)
                output.append(f"=== {chart_title} ===")

                for i, item in enumerate(chart.get("data", [])[:20], 1):
                    attrs = item.get("attributes", {})
                    name = attrs.get("name", "Unknown")
                    artist = attrs.get("artistName", "")
                    if artist:
                        output.append(f"  {i}. {name} - {artist}")
                    else:
                        output.append(f"  {i}. {name}")

        return "\n".join(output) if output else "No chart data available"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_genres() -> str:
    """
    Get all available music genres in the Apple Music catalog.

    Returns: List of genres with their IDs
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/genres",
            headers=headers,
            params={"limit": 50},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        output = []
        for genre in data.get("data", []):
            attrs = genre.get("attributes", {})
            name = attrs.get("name", "Unknown")
            genre_id = genre.get("id")
            output.append(f"{name} (ID: {genre_id})")

        return "\n".join(output) if output else "No genres found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_search_suggestions(term: str) -> str:
    """
    Get autocomplete/typeahead suggestions for a search term.
    Useful for building search interfaces or getting quick results.

    Args:
        term: Partial search term (e.g., "tay" for Taylor Swift)

    Returns: List of suggested search terms
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/search/suggestions",
            headers=headers,
            params={"term": term, "kinds": "terms", "limit": 10},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        suggestions = data.get("results", {}).get("suggestions", [])
        output = ["=== Search Suggestions ==="]
        for suggestion in suggestions:
            if suggestion.get("kind") == "terms":
                search_term = suggestion.get("searchTerm", "")
                display = suggestion.get("displayTerm", search_term)
                output.append(f"  {display}")

        return "\n".join(output) if len(output) > 1 else "No suggestions found"

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


@mcp.tool()
def get_personal_station() -> str:
    """
    Get your personal Apple Music radio station.
    This is a station curated based on your listening history.

    Returns: Personal station info
    """
    try:
        headers = get_headers()
        response = requests.get(
            f"{BASE_URL}/catalog/{get_storefront()}/stations",
            headers=headers,
            params={"filter[identity]": "personal"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        stations = data.get("data", [])
        if not stations:
            return "No personal station found (may require more listening history)"

        station = stations[0]
        attrs = station.get("attributes", {})
        name = attrs.get("name", "Your Personal Station")
        station_id = station.get("id")
        is_live = attrs.get("isLive", False)

        output = [
            f"=== {name} ===",
            f"Station ID: {station_id}",
            f"Type: {'Live' if is_live else 'On-demand'}",
            "",
            "This station plays music based on your listening history and preferences.",
        ]
        return "\n".join(output)

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ SYSTEM MANAGEMENT ============


@mcp.tool()
def config(
    action: str = "info",
    days_old: int = 0,
    preference: str = "",
    value: Optional[bool] = None,
    string_value: str = "",
    limit: int = 20,
) -> str:
    """
    Configuration, preferences, cache management, and audit log.

    Args:
        action: One of: info, set-pref, list-storefronts, audit-log, clear-tracks, clear-exports, clear-audit-log
        days_old: When clearing exports, only delete files older than this (0 = all)
        preference: For set-pref: fetch_explicit, reveal_on_library_miss, clean_only, auto_search, or storefront
        value: For set-pref (bool prefs): true or false
        string_value: For set-pref (string prefs like storefront): e.g., "us", "gb", "de"
        limit: For audit-log: max entries to show (default 20)

    Returns: Config info, preference update, or cache deletion summary
    """
    try:
        action = action.lower()

        # === SET PREFERENCE ===
        if action == "set-pref":
            bool_prefs = ["fetch_explicit", "reveal_on_library_miss", "clean_only", "auto_search"]
            string_prefs = ["storefront"]
            all_prefs = bool_prefs + string_prefs

            if not preference:
                return f"Error: set-pref requires 'preference' parameter. Valid: {', '.join(all_prefs)}"

            if preference not in all_prefs:
                return f"Error: preference must be one of: {', '.join(all_prefs)}"

            # Determine the value to set
            if preference in string_prefs:
                if not string_value:
                    return f"Error: '{preference}' requires 'string_value' parameter (e.g., string_value='gb')"
                pref_value = string_value.lower()
            else:
                if value is None:
                    return f"Error: '{preference}' requires 'value' parameter (true or false)"
                pref_value = value

            # Load current config
            from .auth import load_config, get_config_dir as get_auth_config_dir
            try:
                config = load_config()
            except FileNotFoundError:
                return "Error: config.json not found. Create it first with your API credentials."

            # Update preferences
            if "preferences" not in config:
                config["preferences"] = {}
            config["preferences"][preference] = pref_value

            # Save back
            config_file = get_auth_config_dir() / "config.json"
            with open(config_file, "w") as f:
                json.dump(config, f, indent=2)

            return f"✓ Updated: {preference} = {pref_value}\n\nUse config() to see current preferences."

        # === LIST STOREFRONTS ===
        if action == "list-storefronts":
            try:
                headers = get_headers()
                response = requests.get(
                    f"{BASE_URL}/storefronts",
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()

                output = ["=== Available Storefronts ===", ""]
                for storefront in data.get("data", []):
                    sf_id = storefront.get("id", "")
                    attrs = storefront.get("attributes", {})
                    name = attrs.get("name", "Unknown")
                    output.append(f"  {sf_id}: {name}")

                output.append("")
                output.append(f"Current: {get_storefront()}")
                output.append("Set via: config(action='set-pref', preference='storefront', string_value='xx')")
                return "\n".join(output)
            except Exception as e:
                return f"Error listing storefronts: {e}"

        # === CLEAR TRACK CACHE ===
        if action == "clear-tracks":
            track_cache = get_track_cache()
            num_entries = len(track_cache._cache)
            track_cache.clear()
            return f"✓ Cleared track metadata cache ({num_entries} entries removed)"

        # === CLEAR EXPORT FILES ===
        if action == "clear-exports":
            cache_dir = get_cache_dir()
            if not cache_dir.exists():
                return "Cache directory doesn't exist"

            export_files = list(cache_dir.glob("*.csv")) + list(cache_dir.glob("*.json"))
            # Don't delete track_cache.json
            export_files = [f for f in export_files if f.name != "track_cache.json"]

            if not export_files:
                return "No export files in cache"

            now = time.time()
            cutoff = now - (days_old * 86400) if days_old > 0 else now + 1
            deleted = []
            kept = []
            total_size = 0

            for f in export_files:
                file_size = f.stat().st_size
                if days_old == 0 or f.stat().st_mtime < cutoff:
                    deleted.append(f.name)
                    total_size += file_size
                    f.unlink()
                else:
                    kept.append(f.name)

            if total_size < 1024:
                size_str = f"{total_size} bytes"
            elif total_size < 1024 * 1024:
                size_str = f"{total_size / 1024:.1f} KB"
            else:
                size_str = f"{total_size / (1024 * 1024):.1f} MB"

            output = [f"✓ Deleted: {len(deleted)} export files ({size_str})"]
            if kept:
                output.append(f"Kept: {len(kept)} files (newer than {days_old} days)")
            return "\n".join(output)

        # === AUDIT LOG ===
        if action == "audit-log":
            entries = audit_log.get_recent_entries(limit=limit)
            return audit_log.format_entries_for_display(entries, limit=limit)

        # === CLEAR AUDIT LOG ===
        if action == "clear-audit-log":
            entries = audit_log.get_recent_entries(limit=1000)
            if audit_log.clear_audit_log():
                return f"✓ Cleared audit log ({len(entries)} entries removed)"
            return "Error: Failed to clear audit log"

        # === INFO (DEFAULT) ===
        if action == "info":
            output = ["=== System Info ===", ""]

            # User Preferences
            prefs = get_user_preferences()
            output.append("Preferences (set via config(action='set-pref', ...)):")
            output.append(f"  storefront: {prefs['storefront']} (list: config(action='list-storefronts'))")
            output.append(f"  fetch_explicit: {prefs['fetch_explicit']}")
            output.append(f"  reveal_on_library_miss: {prefs['reveal_on_library_miss']}")
            output.append(f"  clean_only: {prefs['clean_only']}")
            output.append(f"  auto_search: {prefs['auto_search']}")
            output.append("")

            # Track Metadata Cache
            track_cache = get_track_cache()
            num_tracks = len(track_cache._cache)
            if track_cache.cache_file.exists():
                cache_size = track_cache.cache_file.stat().st_size
                if cache_size < 1024:
                    size_str = f"{cache_size}B"
                elif cache_size < 1024 * 1024:
                    size_str = f"{cache_size / 1024:.0f}KB"
                else:
                    size_str = f"{cache_size / (1024 * 1024):.1f}MB"
                output.append(f"Track Metadata Cache: {num_tracks} entries, {size_str}")
            else:
                output.append(f"Track Metadata Cache: {num_tracks} entries (not yet saved)")
            output.append(f"  Location: {track_cache.cache_file}")
            output.append(f"  Clear: config(action='clear-tracks')")
            output.append("")

            # Export Files
            cache_dir = get_cache_dir()
            if cache_dir.exists():
                export_files = list(cache_dir.glob("*.csv")) + list(cache_dir.glob("*.json"))
                # Don't count track_cache.json
                export_files = [f for f in export_files if f.name != "track_cache.json"]

                if export_files:
                    export_files = sorted(export_files, key=lambda f: f.stat().st_mtime, reverse=True)
                    total_size = sum(f.stat().st_size for f in export_files)
                    total_str = f"{total_size / 1024:.0f}KB" if total_size < 1024 * 1024 else f"{total_size / (1024 * 1024):.1f}MB"
                    output.append(f"Export Files: {len(export_files)} files, {total_str}")

                    now = time.time()
                    for f in export_files[:10]:  # Show most recent 10
                        file_size = f.stat().st_size
                        age_days = (now - f.stat().st_mtime) / 86400

                        if file_size < 1024:
                            size_str = f"{file_size}B"
                        elif file_size < 1024 * 1024:
                            size_str = f"{file_size / 1024:.0f}KB"
                        else:
                            size_str = f"{file_size / (1024 * 1024):.1f}MB"

                        age_str = f"{age_days * 24:.0f}h ago" if age_days < 1 else f"{age_days:.0f}d ago"
                        output.append(f"  {f.name} ({size_str}, {age_str})")

                    if len(export_files) > 10:
                        output.append(f"  ... and {len(export_files) - 10} more")
                    output.append(f"  Clear: config(action='clear-exports')")
                else:
                    output.append("Export Files: None")
            else:
                output.append("Export Files: Cache directory doesn't exist yet")

            output.append("")

            # Audit Log
            log_path = audit_log.get_audit_log_path()
            if log_path.exists():
                log_size = log_path.stat().st_size
                if log_size < 1024:
                    log_size_str = f"{log_size}B"
                elif log_size < 1024 * 1024:
                    log_size_str = f"{log_size / 1024:.0f}KB"
                else:
                    log_size_str = f"{log_size / (1024 * 1024):.1f}MB"
                entries = audit_log.get_recent_entries(limit=5)
                output.append(f"Audit Log: {len(entries)}+ entries, {log_size_str}")
            else:
                output.append("Audit Log: Empty (no operations logged yet)")
            output.append(f"  Location: {log_path}")
            output.append(f"  View: config(action='audit-log')")
            output.append(f"  Clear: config(action='clear-audit-log')")

            return "\n".join(output)

        # === UNKNOWN ACTION ===
        valid_actions = "info, set-pref, list-storefronts, audit-log, clear-tracks, clear-exports, clear-audit-log"
        return f"Error: Unknown action '{action}'. Valid: {valid_actions}"

    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def test_output_size(target_chars: int = 50000) -> str:
    """
    Diagnostic tool to test MCP response size limits using real library data.

    Fetches tracks from multiple playlists to build unique (non-repeating) content
    until hitting the target character count. Use this to find where output gets
    truncated by checking if the END marker is visible.

    Args:
        target_chars: Target character count to test (default 50000)

    Returns: Real track data from your library, with markers to detect truncation
    """
    try:
        headers = get_headers()

        # Fetch all playlists
        response = requests.get(
            f"{BASE_URL}/me/library/playlists",
            headers=headers,
            params={"limit": 100},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        playlists = response.json().get("data", [])

        if not playlists:
            return "No playlists found in library"

        # Build output by fetching tracks from different playlists
        header = f"=== SIZE TEST: {target_chars:,} chars target ===\n"
        header += f"=== Found {len(playlists)} playlists to draw from ===\n\n"

        content = header
        playlists_used = 0
        total_tracks = 0

        for playlist in playlists:
            if len(content) >= target_chars:
                break

            playlist_id = playlist.get("id", "")
            playlist_name = playlist.get("attributes", {}).get("name", "Unknown")

            # Fetch this playlist's tracks
            try:
                track_response = requests.get(
                    f"{BASE_URL}/me/library/playlists/{playlist_id}/tracks",
                    headers=headers,
                    params={"limit": 100},
                    timeout=REQUEST_TIMEOUT,
                )
                track_response.raise_for_status()
                tracks = track_response.json().get("data", [])
            except requests.exceptions.RequestException:
                continue  # Skip playlists that fail

            if not tracks:
                continue

            # Format and add this playlist's tracks
            playlist_header = f"--- {playlist_name} ({len(tracks)} tracks, char {len(content):,}) ---\n"
            content += playlist_header

            for t in tracks:
                if len(content) >= target_chars:
                    break
                data = extract_track_data(t, include_extras=False)
                line = _format_full(data)
                content += line + "\n"
                total_tracks += 1

            content += "\n"
            playlists_used += 1

        # If we still haven't hit target, note it
        if len(content) < target_chars:
            content += f"\n(Exhausted all {len(playlists)} playlists at {len(content):,} chars)\n"

        footer = f"\n\n=== END: {len(content):,} chars, {playlists_used} playlists, {total_tracks} tracks ==="

        return content + footer

    except requests.exceptions.RequestException as e:
        return f"API Error: {str(e)}"
    except (FileNotFoundError, ValueError) as e:
        return str(e)


# ============ STATUS ============


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
                f"{BASE_URL}/me/library/playlists", headers=headers, params={"limit": 1}, timeout=REQUEST_TIMEOUT,
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


# =============================================================================
# AppleScript-powered tools (macOS only)
# =============================================================================
# These tools provide capabilities not available through the REST API:
# - Playback control (play, pause, skip)
# - Delete tracks from playlists
# - Delete playlists
# - Volume and shuffle control
# - Get currently playing track

if APPLESCRIPT_AVAILABLE:

    @mcp.tool()
    def play(
        track: str = "",
        playlist: str = "",
        album: str = "",
        artist: str = "",
        shuffle: bool = False,
        reveal: Optional[bool] = None,
        add_to_library: bool = False,
    ) -> str:
        """Play a track, playlist, or album (macOS only).

        Provide ONE of: track, playlist, or album.

        Args:
            track: Track name or catalog ID (auto-detected)
            playlist: Playlist name to play
            album: Album name (starts playing first track; Music app continues with album)
            artist: Artist name to disambiguate tracks/albums
            shuffle: Shuffle playlist or album (default False)
            reveal: Open in Music app when not in library (you click play).
                   Uses user preference from config if not specified.
            add_to_library: Add catalog item to library first, then auto-play

        Response shows source: [Library], [Catalog], or [Catalog→Library].

        Returns: Status message
        """
        # Count how many targets provided
        targets = sum(1 for t in [track, playlist, album] if t)
        if targets == 0:
            return "Error: Provide track, playlist, or album parameter"
        if targets > 1:
            return "Error: Provide only ONE of track, playlist, or album"

        # === PLAYLIST ===
        if playlist:
            success, result = asc.play_playlist(playlist, shuffle)
            if success:
                return result
            return f"Error: {result}"

        # === ALBUM ===
        if album:
            # Apply user preferences for reveal when library miss
            if reveal is None:
                prefs = get_user_preferences()
                reveal = prefs["reveal_on_library_miss"]

            # Search library for tracks from this album
            search_ok, lib_results = asc.search_library(album, "albums")
            if search_ok and lib_results:
                # Filter by artist if provided
                for lib_track in lib_results:
                    lib_album = lib_track.get("album", "")
                    lib_artist = lib_track.get("artist", "")
                    if album.lower() not in lib_album.lower():
                        continue
                    if artist and artist.lower() not in lib_artist.lower():
                        continue
                    # Found match - play first track (Music continues with album)
                    if shuffle:
                        asc.set_shuffle(True)
                    success, result = asc.play_track(lib_track.get("name", ""), lib_artist)
                    if success:
                        shuffle_note = " (shuffled)" if shuffle else ""
                        return f"[Library] Playing: {lib_album} by {lib_artist}{shuffle_note}"
                    break

            # Not in library - search catalog
            try:
                headers = get_headers()
                response = requests.get(
                    f"{BASE_URL}/catalog/{get_storefront()}/search",
                    headers=headers,
                    params={"term": f"{album} {artist}".strip(), "types": "albums", "limit": 5},
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code == 200:
                    data = response.json()
                    albums_data = data.get("results", {}).get("albums", {}).get("data", [])
                    for cat_album in albums_data:
                        attrs = cat_album.get("attributes", {})
                        album_name = attrs.get("name", "")
                        album_artist = attrs.get("artistName", "")
                        album_id = cat_album.get("id", "")
                        if album.lower() not in album_name.lower():
                            continue
                        if artist and artist.lower() not in album_artist.lower():
                            continue
                        album_url = attrs.get("url", "")

                        # Option 1: Add album to library and play
                        if add_to_library and album_id:
                            add_ok, add_msg = _add_album_to_library(album_id)
                            if add_ok:
                                time.sleep(PLAY_TRACK_INITIAL_DELAY)
                                # Re-search library for the album
                                for attempt in range(PLAY_TRACK_MAX_ATTEMPTS):
                                    if attempt > 0:
                                        time.sleep(PLAY_TRACK_RETRY_DELAY)
                                    search_ok2, lib_results2 = asc.search_library(album_name, "albums")
                                    if search_ok2 and lib_results2:
                                        for lib_track2 in lib_results2:
                                            if album_name.lower() in lib_track2.get("album", "").lower():
                                                if shuffle:
                                                    asc.set_shuffle(True)
                                                success, result = asc.play_track(lib_track2.get("name", ""), lib_track2.get("artist", ""))
                                                if success:
                                                    shuffle_note = " (shuffled)" if shuffle else ""
                                                    return f"[Catalog→Library] Playing: {album_name} by {album_artist}{shuffle_note}"
                                                break
                                return f"[Catalog→Library] Added but sync pending: {album_name} by {album_artist}"
                            return f"[Catalog] Failed to add: {add_msg}"

                        # Option 2: Open in Music app (user must click play)
                        if reveal and album_url:
                            success, msg = asc.open_catalog_song(album_url)
                            if success:
                                return f"[Catalog] Opened: {album_name} by {album_artist} (click play)"
                            return f"[Catalog] {msg}"

                        # Neither flag set - explain options
                        return (
                            f"[Catalog] Found: {album_name} by {album_artist}. "
                            f"Use reveal=True to open in Music, or add_to_library=True to save & play."
                        )
            except requests.exceptions.RequestException as e:
                return f"API Error searching catalog: {str(e)}"
            except (FileNotFoundError, ValueError) as e:
                return f"Error: {str(e)}"
            return f"Album not found: {album}"

        # === TRACK ===
        # Apply user preferences for reveal when library miss
        if reveal is None:
            prefs = get_user_preferences()
            reveal = prefs["reveal_on_library_miss"]

        # Resolve track input
        resolved = _resolve_track(track, artist)
        if not resolved:
            return "Error: Could not resolve track"

        r = resolved[0]  # Only first track
        if r.error:
            return f"Error: {r.error}"

        track_name = ""
        track_artist = r.artist or artist

        # If catalog ID, look up track info and play directly
        if r.input_type == InputType.CATALOG_ID:
            catalog_id = r.value
            try:
                headers = get_headers()
                response = requests.get(
                    f"{BASE_URL}/catalog/{get_storefront()}/songs/{catalog_id}",
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code == 200:
                    data = response.json().get("data", [])
                    if data:
                        attrs = data[0].get("attributes", {})
                        track_name = attrs.get("name", "")
                        track_artist = attrs.get("artistName", "")
                        song_url = attrs.get("url", "")

                        # For catalog ID, try to add to library and play
                        if add_to_library:
                            add_ok, add_msg = _add_songs_to_library([catalog_id])
                            if add_ok:
                                time.sleep(PLAY_TRACK_INITIAL_DELAY)
                                for attempt in range(PLAY_TRACK_MAX_ATTEMPTS):
                                    if attempt > 0:
                                        time.sleep(PLAY_TRACK_RETRY_DELAY)
                                    success, result = asc.play_track(track_name, track_artist)
                                    if success:
                                        if reveal:
                                            asc.reveal_track(track_name, track_artist)
                                        return f"[Catalog→Library] Playing: {track_name} by {track_artist}"
                                return f"[Catalog→Library] Added but sync pending: {track_name} by {track_artist}"
                            return f"[Catalog] Failed to add: {add_msg}"

                        if reveal:
                            if song_url:
                                success, msg = asc.open_catalog_song(song_url)
                                if success:
                                    return f"[Catalog] Opened: {track_name} by {track_artist} (click play)"
                                return f"[Catalog] {msg}"
                            return f"[Catalog] No URL available for: {track_name}"

                        return (
                            f"[Catalog] Found: {track_name} by {track_artist}. "
                            f"Use reveal=True to open in Music, or add_to_library=True to save & play."
                        )
            except Exception:
                pass
            return f"Track not found for catalog ID: {catalog_id}"

        # Name-based lookup
        track_name = r.value
        track_artist = r.artist or artist

        # Search library first (doesn't foreground Music)
        search_ok, lib_results = asc.search_library(track_name, "songs")
        if search_ok and lib_results:
            # Filter for matching artist if provided
            for lib_track in lib_results:
                lib_name = lib_track.get("name", "")
                lib_artist = lib_track.get("artist", "")
                if track_name.lower() not in lib_name.lower():
                    continue
                if track_artist and track_artist.lower() not in lib_artist.lower():
                    continue
                # Found match - now play it (will foreground Music)
                success, result = asc.play_track(lib_name, lib_artist)
                if success:
                    if reveal:
                        asc.reveal_track(lib_name, lib_artist)
                    return f"[Library] {result}"
                break

        # Track not in library - search catalog
        search_term = f"{track_name} {track_artist}".strip() if track_artist else track_name
        songs = _search_catalog_songs(search_term, limit=5)

        # Find best match
        for song in songs:
            attrs = song.get("attributes", {})
            song_name = attrs.get("name", "")
            song_artist = attrs.get("artistName", "")

            # Check if it's a reasonable match
            if track_name.lower() not in song_name.lower():
                continue
            # Check artist in artistName OR song name (for "feat. X" cases)
            if track_artist and track_artist.lower() not in song_artist.lower() and track_artist.lower() not in song_name.lower():
                continue

            catalog_id = song.get("id")
            song_url = attrs.get("url", "")

            # Option 1: Add to library first, then play
            if add_to_library:
                add_ok, add_msg = _add_songs_to_library([catalog_id])
                if add_ok:
                    # Wait for iCloud sync, then play
                    time.sleep(PLAY_TRACK_INITIAL_DELAY)
                    for attempt in range(PLAY_TRACK_MAX_ATTEMPTS):
                        if attempt > 0:
                            time.sleep(PLAY_TRACK_RETRY_DELAY)
                        success, result = asc.play_track(song_name, song_artist)
                        if success:
                            if reveal:
                                asc.reveal_track(song_name, song_artist)
                            return f"[Catalog→Library] Playing: {song_name} by {song_artist}"
                    return f"[Catalog→Library] Added but sync pending: {song_name} by {song_artist}"
                return f"[Catalog] Failed to add: {add_msg}"

            # Option 2: Open in Music app (user must click play)
            if reveal:
                if song_url:
                    success, msg = asc.open_catalog_song(song_url)
                    if success:
                        return f"[Catalog] Opened: {song_name} by {song_artist} (click play)"
                    return f"[Catalog] {msg}"
                return f"[Catalog] No URL available for: {song_name}"

            # Neither flag set - explain options
            return (
                f"[Catalog] Found: {song_name} by {song_artist}. "
                f"Use reveal=True to open in Music, or add_to_library=True to save & play."
            )

        return f"Track not found in library or catalog: {track_name}"

    @mcp.tool()
    def playback_control(action: str, seconds: float = 0) -> str:
        """Control playback: play, pause, stop, next, previous, seek (macOS only).

        Args:
            action: One of: play, pause, playpause, stop, next, previous, seek
            seconds: For seek action, position in seconds from track start

        Returns: Confirmation message or error
        """
        action = action.lower().strip()

        # Handle seek separately since it takes a parameter
        if action == "seek":
            success, result = asc.seek(seconds)
            if success:
                return f"Seeked to {int(seconds // 60)}:{int(seconds % 60):02d}"
            return f"Error: {result}"

        action_map = {
            "play": asc.play,
            "pause": asc.pause,
            "playpause": asc.playpause,
            "stop": asc.stop,
            "next": asc.next_track,
            "previous": asc.previous_track,
        }
        if action not in action_map:
            return f"Invalid action: {action}. Use: play, pause, playpause, stop, next, previous, seek"

        success, result = action_map[action]()
        if success:
            return f"Playback: {action}"
        return f"Error: {result}"

    @mcp.tool()
    def get_now_playing() -> str:
        """Get info about the currently playing track and player state (macOS only).

        Returns: Player state and track info (name, artist, album, position)
        """
        success, info = asc.get_current_track()
        if not success:
            return f"Error: {info}"

        if info.get("state") == "stopped":
            return "State: stopped\nNot currently playing"

        parts = []
        # Add player state first
        state = info.get("state", "unknown")
        parts.append(f"State: {state}")

        if "name" in info:
            parts.append(f"Track: {info['name']}")
        if "artist" in info:
            parts.append(f"Artist: {info['artist']}")
        if "album" in info:
            parts.append(f"Album: {info['album']}")
        if "position" in info and "duration" in info:
            try:
                pos = float(info["position"])
                dur = float(info["duration"])
                pos_min, pos_sec = int(pos) // 60, int(pos) % 60
                dur_min, dur_sec = int(dur) // 60, int(dur) % 60
                parts.append(f"Position: {pos_min}:{pos_sec:02d} / {dur_min}:{dur_sec:02d}")
            except (ValueError, TypeError):
                pass

        return "\n".join(parts) if parts else "Playing (no track info available)"

    @mcp.tool()
    def playback_settings(
        volume: int = -1,
        shuffle: str = "",
        repeat: str = "",
    ) -> str:
        """Get or set playback settings (macOS only).

        With no args, returns current settings. Provide any arg to change it.

        Args:
            volume: 0-100 (-1 to leave unchanged)
            shuffle: "on" or "off" (empty to leave unchanged)
            repeat: "off", "one", or "all" (empty to leave unchanged)

        Returns: Current/updated settings or confirmation
        """
        changes = []

        # Apply any changes
        if volume >= 0:
            v = max(0, min(100, volume))
            success, result = asc.set_volume(v)
            if not success:
                return f"Error setting volume: {result}"
            changes.append(f"Volume: {v}")

        if shuffle:
            enabled = shuffle.lower() in ("on", "true", "1", "yes")
            success, result = asc.set_shuffle(enabled)
            if not success:
                return f"Error setting shuffle: {result}"
            changes.append(f"Shuffle: {'on' if enabled else 'off'}")

        if repeat:
            success, result = asc.set_repeat(repeat.lower())
            if not success:
                return f"Error setting repeat: {result}"
            changes.append(f"Repeat: {repeat}")

        # If changes were made, return confirmation
        if changes:
            return "Updated: " + ", ".join(changes)

        # Otherwise return current settings
        success, stats = asc.get_library_stats()
        if not success:
            return f"Error: {stats}"

        return (
            f"Player: {stats['player_state']}\n"
            f"Volume: {stats['volume']}\n"
            f"Shuffle: {'on' if stats['shuffle'] else 'off'}\n"
            f"Repeat: {stats['repeat']}"
        )

    @mcp.tool()
    def remove_from_playlist(
        playlist: str = "",
        track: str = "",
        artist: str = "",
    ) -> str:
        """Remove track(s) from a playlist (macOS only).

        Args:
            playlist: Playlist name (playlist IDs not supported)
            track: Track(s) - name, ID, CSV, or JSON array
            artist: Artist name (helps with matching)

        Note: Removes from playlist only, not from library. Catalog/library IDs
        work if the track was previously cached (e.g., from search results).

        Returns: Confirmation message or error
        """
        # Resolve playlist (name-based only for removal)
        resolved = _resolve_playlist(playlist)
        if resolved.error:
            return resolved.error

        # This function requires AppleScript name (macOS only)
        if not resolved.applescript_name:
            return "Error: Playlist not found or requires explicit playlist name (not just ID)"

        if not track:
            return "Error: Provide track parameter"

        results = []
        errors = []

        # Resolve track input
        track_resolved = _resolve_track(track, artist)

        for r in track_resolved:
            if r.error:
                errors.append(r.error)
                continue

            if r.input_type == InputType.PERSISTENT_ID:
                # Remove by persistent ID
                success, result = asc.remove_track_from_playlist(
                    resolved.applescript_name,
                    track_id=r.value
                )
                if success:
                    results.append(result)
                else:
                    errors.append(f"ID {r.value}: {result}")

            elif r.input_type == InputType.CATALOG_ID:
                # Try cache lookup to get track name
                cache = get_track_cache()
                info = cache.get_track_info(r.value)
                if info and info.get("name"):
                    success, result = asc.remove_track_from_playlist(
                        resolved.applescript_name,
                        track_name=info["name"],
                        artist=info.get("artist") or None
                    )
                    if success:
                        results.append(result)
                    else:
                        errors.append(f"{info['name']}: {result}")
                else:
                    errors.append(f"Catalog ID {r.value}: Not in cache - use track name instead")

            elif r.input_type == InputType.LIBRARY_ID:
                # Try cache lookup to get track name
                cache = get_track_cache()
                info = cache.get_track_info(r.value)
                if info and info.get("name"):
                    success, result = asc.remove_track_from_playlist(
                        resolved.applescript_name,
                        track_name=info["name"],
                        artist=info.get("artist") or None
                    )
                    if success:
                        results.append(result)
                    else:
                        errors.append(f"{info['name']}: {result}")
                else:
                    errors.append(f"Library ID {r.value}: Not in cache - use track name instead")

            elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                # Remove by name
                success, result = asc.remove_track_from_playlist(
                    resolved.applescript_name,
                    track_name=r.value,
                    artist=r.artist or None
                )
                if success:
                    results.append(result)
                else:
                    errors.append(f"{r.value}: {result}")

        # Log successful removes
        if results:
            audit_log.log_action(
                "remove_from_playlist",
                {"playlist": resolved.applescript_name, "tracks": results},
                undo_info={"playlist_name": resolved.applescript_name, "tracks": results}
            )

        result = _build_track_results(
            results, errors,
            success_verb="removed",
            error_verb="failed to remove"
        )
        fuzzy_info = _format_fuzzy_match(resolved.fuzzy_match)
        return result + fuzzy_info

    @mcp.tool()
    def remove_from_library(
        track: str = "",
        artist: str = "",
    ) -> str:
        """Remove track(s) from your library entirely (macOS only).

        Args:
            track: Track(s) - name, ID, CSV, or JSON array
            artist: Artist name (helps with matching)

        Warning: PERMANENTLY deletes tracks. Catalog/library IDs work if cached.

        Returns: Confirmation message or error
        """
        if not track:
            return "Error: Provide track parameter"

        results = []
        errors = []

        # Resolve track input
        resolved = _resolve_track(track, artist)

        for r in resolved:
            if r.error:
                errors.append(r.error)
                continue

            if r.input_type == InputType.PERSISTENT_ID:
                # Remove by persistent ID
                success, result = asc.remove_from_library(track_id=r.value)
                if success:
                    results.append(result)
                else:
                    errors.append(f"ID {r.value}: {result}")

            elif r.input_type == InputType.CATALOG_ID:
                # Try cache lookup to get track name
                cache = get_track_cache()
                info = cache.get_track_info(r.value)
                if info and info.get("name"):
                    success, result = asc.remove_from_library(
                        track_name=info["name"],
                        artist=info.get("artist") or None
                    )
                    if success:
                        results.append(result)
                    else:
                        errors.append(f"{info['name']}: {result}")
                else:
                    errors.append(f"Catalog ID {r.value}: Not in cache - use track name instead")

            elif r.input_type == InputType.LIBRARY_ID:
                # Try cache lookup to get track name
                cache = get_track_cache()
                info = cache.get_track_info(r.value)
                if info and info.get("name"):
                    success, result = asc.remove_from_library(
                        track_name=info["name"],
                        artist=info.get("artist") or None
                    )
                    if success:
                        results.append(result)
                    else:
                        errors.append(f"{info['name']}: {result}")
                else:
                    errors.append(f"Library ID {r.value}: Not in cache - use track name instead")

            elif r.input_type in (InputType.NAME, InputType.JSON_OBJECT):
                # Remove by name
                success, result = asc.remove_from_library(
                    track_name=r.value,
                    artist=r.artist or None
                )
                if success:
                    results.append(result)
                else:
                    errors.append(f"{r.value}: {result}")

        # Log successful removes - this is destructive, important for audit
        if results:
            audit_log.log_action(
                "remove_from_library",
                {"tracks": results},
                undo_info={"tracks": results, "note": "Re-add via search_catalog and add_to_library"}
            )

        return _build_track_results(
            results, errors,
            success_verb="removed from library",
            error_verb="failed to remove"
        )

    @mcp.tool()
    def delete_playlist(playlist_name: str) -> str:
        """Delete a playlist entirely (macOS only).

        Warning: This permanently deletes the playlist. It cannot be undone.

        Args:
            playlist_name: Name of the playlist to delete

        Returns: Confirmation message or error
        """
        # Get track count before deletion for audit log
        track_count = 0
        track_names = []
        tracks_success, tracks = asc.get_playlist_tracks(playlist_name)
        if tracks_success and isinstance(tracks, list):
            track_count = len(tracks)
            track_names = [f"{t.get('name', '')} - {t.get('artist', '')}" for t in tracks[:20]]

        success, result = asc.delete_playlist(playlist_name)
        if success:
            # Log deletion with undo info
            audit_log.log_action(
                "delete_playlist",
                {"name": playlist_name, "track_count": track_count},
                undo_info={"playlist_name": playlist_name, "tracks": track_names, "note": "Recreate playlist and re-add tracks"}
            )
            return result
        return f"Error: {result}"

    @mcp.tool()
    def reveal_in_music(track_name: str, artist: str = "") -> str:
        """Reveal a track in the Music app window (macOS only).

        Opens Music app and navigates to show the track.

        Args:
            track_name: Name of the track (can be partial match)
            artist: Optional artist name to disambiguate

        Returns: Confirmation message or error
        """
        success, result = asc.reveal_track(track_name, artist if artist else None)
        if success:
            return result
        return f"Error: {result}"

    @mcp.tool()
    def airplay(device_name: str = "") -> str:
        """List or switch AirPlay devices (macOS only).

        Args:
            device_name: Device to switch to (partial match OK). Omit to list devices.

        Returns: Device list or switch confirmation
        """
        if device_name:
            success, result = asc.set_airplay_device(device_name)
            if success:
                return result
            return f"Error: {result}"
        else:
            success, devices = asc.get_airplay_devices()
            if not success:
                return f"Error: {devices}"
            if not devices:
                return "No AirPlay devices found"
            return f"AirPlay devices ({len(devices)}):\n" + "\n".join(f"  - {d}" for d in devices)



def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()

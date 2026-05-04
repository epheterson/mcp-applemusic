"""Shared test fixtures."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from applemusic_mcp import applescript as asc
from applemusic_mcp import audit_log


# Mock audit log for all tests to avoid polluting real audit log
@pytest.fixture(autouse=True)
def mock_audit_log_for_all_tests(tmp_path):
    """Ensure all tests use a temp audit log, not the real one."""
    audit_dir = tmp_path / ".cache" / "applemusic-mcp"
    audit_dir.mkdir(parents=True)
    log_path = audit_dir / "audit_log.jsonl"
    with patch.object(audit_log, "get_audit_log_path", return_value=log_path):
        yield log_path


# Clean up test playlists after all tests
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_playlists():
    """Remove test debris from the user's Music library at session end.

    Sweeps known test markers used across test classes:
    - Single names (legacy): __TEST_PLAYLIST__, 🧪 Integration Test Playlist
    - Prefixed: _TEST_*, _VERIFY*, _UI_TEST_, _SNAPSHOT_TEST_

    Each test ideally cleans up its own debris (per-test setUp/tearDown),
    but interrupted runs and intermittent iCloud sync hiccups leave behind
    playlists/folders that pollute the user's library. This is the safety
    net.
    """
    yield  # Run tests first

    if not asc.is_available():
        return

    # Single-name targets to delete unconditionally if present.
    for name in ("__TEST_PLAYLIST__", "🧪 Integration Test Playlist"):
        try:
            asc.delete_playlist(name)
        except Exception:
            pass

    # Prefix-match targets: enumerate user playlists + folders and delete
    # any whose name starts with a known test marker. Wrapped in a single
    # AppleScript for efficiency (one shell-out instead of N).
    asc.run_applescript("""
tell application "Music"
    repeat with p in (every user playlist)
        set pn to name of p
        if (pn starts with "_TEST_") or (pn starts with "_VERIFY") or (pn starts with "_UI_TEST_") or (pn starts with "_SNAPSHOT_TEST_") then
            try
                delete p
            end try
        end if
    end repeat
end tell""")


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / ".config" / "applemusic-mcp"
    config_dir.mkdir(parents=True)
    return config_dir


@pytest.fixture
def mock_config_dir(temp_config_dir, monkeypatch):
    """Patch get_config_dir to use temp directory."""
    from applemusic_mcp import auth
    monkeypatch.setattr(auth, "DEFAULT_CONFIG_DIR", temp_config_dir)
    return temp_config_dir


@pytest.fixture
def sample_config():
    """Sample configuration data."""
    return {
        "team_id": "TEST_TEAM_ID",
        "key_id": "TEST_KEY_ID",
        "private_key_path": "~/.config/applemusic-mcp/AuthKey_TEST.p8"
    }


@pytest.fixture
def sample_private_key():
    """Sample EC private key for testing (not a real key)."""
    # This is a test-only key, generated for testing purposes
    return """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgtest1234567890
abcdefghijklmnopqrstuvwxyzABCDEFGHhRANCAARtest1234567890abcdefgh
ijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghi
-----END PRIVATE KEY-----"""


@pytest.fixture
def configured_config_dir(mock_config_dir, sample_config, sample_private_key):
    """Config directory with config.json and private key."""
    # Write config
    config_file = mock_config_dir / "config.json"
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    # Write fake private key
    key_file = mock_config_dir / "AuthKey_TEST.p8"
    with open(key_file, "w") as f:
        f.write(sample_private_key)

    # Update config to use actual path
    sample_config["private_key_path"] = str(key_file)
    with open(config_file, "w") as f:
        json.dump(sample_config, f)

    return mock_config_dir


@pytest.fixture
def mock_developer_token():
    """A mock developer token."""
    return "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6IlRFU1RfS0VZX0lEIn0.eyJpc3MiOiJURVNUX1RFQU1fSUQiLCJpYXQiOjE3MDAwMDAwMDAsImV4cCI6MTcxNTAwMDAwMH0.test_signature"


@pytest.fixture
def mock_user_token():
    """A mock music user token."""
    return "Atest1234567890abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def mock_api_headers(mock_developer_token, mock_user_token):
    """Mock API headers."""
    return {
        "Authorization": f"Bearer {mock_developer_token}",
        "Music-User-Token": mock_user_token,
        "Content-Type": "application/json",
    }

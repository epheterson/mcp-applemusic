"""Authentication and token management for Apple Music API."""

import json
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional

import jwt

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "applemusic-mcp"


def get_config_dir() -> Path:
    """Get or create the config directory."""
    config_dir = DEFAULT_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def load_config() -> dict:
    """Load configuration from config.json."""
    config_file = get_config_dir() / "config.json"
    if not config_file.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_file}\n"
            "Create it with your Apple Developer credentials."
        )
    with open(config_file) as f:
        return json.load(f)


def get_private_key_path(config: dict) -> Path:
    """Resolve the private key path from config."""
    path = Path(config["private_key_path"]).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Private key not found: {path}")
    return path


def generate_developer_token(expiry_days: int = 180) -> str:
    """Generate a developer token (JWT) valid for up to 180 days."""
    config = load_config()
    key_path = get_private_key_path(config)

    with open(key_path) as f:
        private_key = f.read()

    now = int(time.time())
    exp = now + (expiry_days * 24 * 60 * 60)

    headers = {"alg": "ES256", "kid": config["key_id"]}
    payload = {"iss": config["team_id"], "iat": now, "exp": exp}

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

    # Save token
    token_file = get_config_dir() / "developer_token.json"
    token_data = {
        "token": token,
        "created": now,
        "expires": exp,
        "team_id": config["team_id"],
        "key_id": config["key_id"],
    }
    with open(token_file, "w") as f:
        json.dump(token_data, f, indent=2)

    return token


def get_developer_token() -> str:
    """Get existing developer token or raise if not found/expired."""
    token_file = get_config_dir() / "developer_token.json"
    if not token_file.exists():
        raise FileNotFoundError(
            "Developer token not found. Run: applemusic-mcp generate-token"
        )

    with open(token_file) as f:
        data = json.load(f)

    # Check if expired (with 1 day buffer)
    if data["expires"] < time.time() + 86400:
        raise ValueError(
            "Developer token expired or expiring soon. Run: applemusic-mcp generate-token"
        )

    return data["token"]


def get_user_token() -> str:
    """Get the music user token or raise if not found."""
    token_file = get_config_dir() / "music_user_token.json"
    if not token_file.exists():
        raise FileNotFoundError(
            "Music user token not found. Run: applemusic-mcp authorize"
        )

    with open(token_file) as f:
        data = json.load(f)

    return data["music_user_token"]


def save_user_token(token: str) -> None:
    """Save the music user token."""
    token_file = get_config_dir() / "music_user_token.json"
    data = {
        "music_user_token": token,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(token_file, "w") as f:
        json.dump(data, f, indent=2)


def create_auth_html(developer_token: str) -> str:
    """Generate the HTML for browser-based authorization."""
    return f'''<!DOCTYPE html>
<html>
<head>
    <title>Apple Music Authorization</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            max-width: 600px;
            margin: 50px auto;
            padding: 20px;
            background: #1a1a1a;
            color: #fff;
        }}
        h1 {{ color: #fa586a; }}
        button {{
            background: #fa586a;
            color: white;
            border: none;
            padding: 15px 30px;
            font-size: 18px;
            border-radius: 8px;
            cursor: pointer;
            margin: 10px 0;
        }}
        button:hover {{ background: #ff6b7a; }}
        button:disabled {{ background: #666; cursor: not-allowed; }}
        .token-box {{
            background: #2a2a2a;
            padding: 15px;
            border-radius: 8px;
            margin: 20px 0;
            word-break: break-all;
            font-family: monospace;
            font-size: 12px;
        }}
        .success {{ color: #4ade80; }}
        .error {{ color: #f87171; }}
        #status {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>Apple Music Authorization</h1>
    <p>Click the button below to authorize access to your Apple Music library.</p>
    <button id="authButton" onclick="authorize()">Authorize with Apple Music</button>
    <div id="status"></div>
    <div id="tokenDisplay" style="display:none;">
        <h3>Music User Token:</h3>
        <div class="token-box" id="userToken"></div>
        <button onclick="copyToken()">Copy Token</button>
        <p id="copyStatus"></p>
        <p>Copy this token and paste it when prompted in the terminal.</p>
    </div>
    <script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js" data-web-components async></script>
    <script>
        const developerToken = "{developer_token}";
        let musicUserToken = null;

        document.addEventListener('musickitloaded', async () => {{
            try {{
                await MusicKit.configure({{
                    developerToken: developerToken,
                    app: {{ name: 'Apple Music MCP Server', build: '1.0.0' }}
                }});
                document.getElementById('status').innerHTML = '<p class="success">MusicKit loaded!</p>';
            }} catch (err) {{
                document.getElementById('status').innerHTML = '<p class="error">Error: ' + err.message + '</p>';
            }}
        }});

        async function authorize() {{
            const button = document.getElementById('authButton');
            const status = document.getElementById('status');
            button.disabled = true;
            status.innerHTML = '<p>Authorizing...</p>';
            try {{
                const music = MusicKit.getInstance();
                musicUserToken = await music.authorize();
                status.innerHTML = '<p class="success">Authorization successful!</p>';
                document.getElementById('userToken').textContent = musicUserToken;
                document.getElementById('tokenDisplay').style.display = 'block';
            }} catch (err) {{
                status.innerHTML = '<p class="error">Failed: ' + err.message + '</p>';
                button.disabled = false;
            }}
        }}

        function copyToken() {{
            navigator.clipboard.writeText(musicUserToken).then(() => {{
                document.getElementById('copyStatus').innerHTML = '<span class="success">Copied!</span>';
            }});
        }}
    </script>
</body>
</html>'''


def run_auth_server(port: int = 8765) -> Optional[str]:
    """Run a local server for browser-based authorization."""
    config_dir = get_config_dir()
    developer_token = get_developer_token()

    # Write auth HTML
    auth_html = create_auth_html(developer_token)
    auth_file = config_dir / "auth.html"
    with open(auth_file, "w") as f:
        f.write(auth_html)

    print(f"Starting authorization server on http://localhost:{port}")
    print("Opening browser for Apple Music authorization...")
    print()

    # Start server in background thread
    import threading

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(config_dir), **kwargs)

        def log_message(self, format, *args):
            pass  # Suppress logs

    server = HTTPServer(("localhost", port), QuietHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    # Open browser
    webbrowser.open(f"http://localhost:{port}/auth.html")

    print("After authorizing in the browser, copy the token and paste it here.")
    print("(The token is a long string of characters)")
    print()

    try:
        token = input("Paste Music User Token: ").strip()
        if token:
            save_user_token(token)
            print()
            print("Token saved successfully!")
            return token
        else:
            print("No token provided.")
            return None
    finally:
        server.shutdown()

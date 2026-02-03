"""User authentication, settings persistence, and session management."""

import hashlib
import json
import os
import random
import string
import threading
from functools import wraps

import requests
from flask import redirect, session, url_for

import config

SETTINGS_FILE = os.getenv("TRACKDROP_USER_SETTINGS_PATH", "/app/data/user_settings.json")

DEFAULT_SETTINGS = {
    "listenbrainz_enabled": False,
    "listenbrainz_username": "",
    "listenbrainz_token": "",
    "lastfm_enabled": False,
    "lastfm_username": "",
    "lastfm_password": "",
    "lastfm_api_key": "",
    "lastfm_api_secret": "",
    "lastfm_session_key": "",
    "cron_minute": 0,
    "cron_hour": 0,
    "cron_day": 1,
    "cron_timezone": "US/Eastern",
    "cron_enabled": True,
    "playlist_sources": ["listenbrainz", "lastfm"],
    "first_time_setup_done": False,
    "api_key": "",  # For iOS Shortcuts / external API access
}


def authenticate_navidrome(username, password):
    """Validate credentials against Navidrome's Subsonic API using MD5+salt auth.

    Returns (success: bool, error_reason: str or None).
    error_reason is None on success, 'offline' if Navidrome is unreachable,
    or 'invalid' if the credentials are wrong.
    """
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    token = hashlib.md5((password + salt).encode()).hexdigest()
    try:
        resp = requests.get(
            f"{config.ROOT_ND}/rest/ping.view",
            params={
                "u": username,
                "t": token,
                "s": salt,
                "v": "1.16.1",
                "c": "trackdrop",
                "f": "json",
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("subsonic-response", {}).get("status") == "ok":
            return True, None
        return False, "invalid"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return False, "offline"
    except Exception:
        return False, "offline"


class UserManager:
    """Thread-safe per-user settings stored in a JSON file."""

    def __init__(self, settings_file=SETTINGS_FILE):
        self._file = settings_file
        self._lock = threading.Lock()

    def _load(self):
        if not os.path.exists(self._file):
            return {}
        with open(self._file, "r") as f:
            return json.load(f)

    def _save(self, data):
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, "w") as f:
            json.dump(data, f, indent=2)

    def authenticate(self, username, password):
        """Validate credentials against Navidrome.

        Returns (success, error_reason) where error_reason is None on success,
        'offline' if Navidrome is unreachable, or 'invalid' for bad credentials.
        """
        return authenticate_navidrome(username, password)

    def get_user_settings(self, username):
        """Return settings for a user, filling in defaults for missing keys."""
        with self._lock:
            data = self._load()
        user = data.get(username, {})
        merged = dict(DEFAULT_SETTINGS)
        merged.update(user)
        return merged

    def update_user_settings(self, username, settings_dict):
        """Merge updates into a user's settings. Returns True on success."""
        with self._lock:
            data = self._load()
            current = data.get(username, {})
            current.update(settings_dict)
            data[username] = current
            self._save(data)
        return True

    def is_first_time(self, username):
        """Check whether the user has completed first-time setup."""
        return not self.get_user_settings(username).get("first_time_setup_done", False)

    def mark_setup_done(self, username):
        """Mark first-time setup as complete for a user."""
        self.update_user_settings(username, {"first_time_setup_done": True})

    def get_all_users(self):
        """Return a list of all usernames with stored settings."""
        with self._lock:
            data = self._load()
        return list(data.keys())

    def generate_api_key(self, username):
        """Generate a new API key for the user and save it."""
        api_key = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        self.update_user_settings(username, {"api_key": api_key})
        return api_key

    def get_user_by_api_key(self, api_key):
        """Look up username by API key. Returns None if not found."""
        if not api_key:
            return None
        with self._lock:
            data = self._load()
        for username, settings in data.items():
            if settings.get("api_key") == api_key:
                return username
        return None


# ---------------------------------------------------------------------------
# Flask session helpers
# ---------------------------------------------------------------------------

def get_current_user():
    """Return the currently logged-in username, or None."""
    return session.get("username")


def login_required(f):
    """Decorator that redirects to /login when no session is active."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

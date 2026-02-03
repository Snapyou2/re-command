
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session
import os
import subprocess
import re
import asyncio
import sys
import traceback
import time
import threading
import json
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *
from apis.lastfm_api import LastFmAPI
from utils import initialize_streamrip_db, get_user_history_path
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from apis.deezer_api import DeezerAPI
from apis.llm_api import LlmAPI
from downloaders.track_downloader import TrackDownloader
from downloaders.link_downloader import LinkDownloader
from downloaders.playlist_downloader import is_playlist_url, download_playlist, extract_playlist_tracks
from playlist_monitor import (
    get_monitored_playlists, add_monitored_playlist, update_monitored_playlist,
    remove_monitored_playlist, start_scheduler, mark_synced,
)
from utils import Tagger
from web_ui.user_manager import UserManager, login_required, get_current_user
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get('TRACKDROP_SECRET_KEY') or os.urandom(24)

# User manager for per-user settings
user_manager = UserManager()

# Common timezones for the UI dropdown
TIMEZONE_LIST = [
    "UTC",
    "US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii",
    "Canada/Atlantic", "Canada/Eastern", "Canada/Central", "Canada/Mountain", "Canada/Pacific",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Amsterdam", "Europe/Brussels",
    "Europe/Madrid", "Europe/Rome", "Europe/Zurich", "Europe/Vienna", "Europe/Stockholm",
    "Europe/Oslo", "Europe/Copenhagen", "Europe/Helsinki", "Europe/Warsaw", "Europe/Prague",
    "Europe/Budapest", "Europe/Bucharest", "Europe/Athens", "Europe/Istanbul", "Europe/Moscow",
    "Asia/Dubai", "Asia/Kolkata", "Asia/Bangkok", "Asia/Singapore", "Asia/Shanghai",
    "Asia/Hong_Kong", "Asia/Tokyo", "Asia/Seoul",
    "Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Perth",
    "Pacific/Auckland", "Pacific/Fiji",
    "America/Sao_Paulo", "America/Argentina/Buenos_Aires", "America/Mexico_City",
    "Africa/Cairo", "Africa/Johannesburg", "Africa/Lagos",
]


def convert_local_to_utc(minute, hour, day_of_week, timezone_str):
    """Convert a local time + day-of-week to UTC for cron scheduling.

    Returns (utc_minute, utc_hour, utc_day_of_week).
    Uses a reference date to compute the offset so DST is handled for the
    *current* period (cron doesn't shift with DST automatically, but this
    gives the user a correct result at save-time).
    """
    from datetime import datetime, timedelta
    try:
        local_tz = ZoneInfo(timezone_str)
    except Exception:
        # Unknown timezone — treat as UTC
        return minute, hour, day_of_week

    # Pick a reference date that falls on the chosen day_of_week
    # Start from today and find the next occurrence of that weekday
    today = datetime.now()
    # days until target weekday (0=Mon in Python, but cron uses 0=Sun)
    # Convert cron day (0=Sun,1=Mon,...6=Sat) to Python weekday (0=Mon,...6=Sun)
    python_weekday = (day_of_week - 1) % 7  # cron 0(Sun)->6, 1(Mon)->0, etc.
    days_ahead = (python_weekday - today.weekday()) % 7
    ref_date = today + timedelta(days=days_ahead)

    # Create a naive datetime in the user's local timezone
    from datetime import datetime as dt
    local_dt = dt(ref_date.year, ref_date.month, ref_date.day, hour, minute, tzinfo=local_tz)
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))

    utc_minute = utc_dt.minute
    utc_hour = utc_dt.hour

    # Compute day shift
    day_diff = (utc_dt.date() - local_dt.replace(tzinfo=None).date()).days
    utc_day = (day_of_week + day_diff) % 7

    return utc_minute, utc_hour, utc_day


# Global dictionary to store download queue status
# Key: download_id (UUID), Value: { 'artist', 'title', 'status', 'start_time', 'message' }
downloads_queue = {}

# Initialize streamrip database at the very start
initialize_streamrip_db()

# Initialize global instances for downloaders and APIs
tagger_global = Tagger(ALBUM_RECOMMENDATION_COMMENT)
# Correctly initialize NavidromeAPI with required arguments from config.py
navidrome_api_global = NavidromeAPI(
    root_nd=ROOT_ND,
    user_nd=USER_ND,
    password_nd=PASSWORD_ND,
    music_library_path=MUSIC_LIBRARY_PATH,
    target_comment=TARGET_COMMENT,
    lastfm_target_comment=LASTFM_TARGET_COMMENT,
    album_recommendation_comment=ALBUM_RECOMMENDATION_COMMENT,
    listenbrainz_enabled=LISTENBRAINZ_ENABLED,
    lastfm_enabled=LASTFM_ENABLED,
    llm_target_comment=LLM_TARGET_COMMENT,
    llm_enabled=LLM_ENABLED,
    admin_user=globals().get('ADMIN_USER', ''),
    admin_password=globals().get('ADMIN_PASSWORD', ''),
    navidrome_db_path=globals().get('NAVIDROME_DB_PATH', '')
)
deezer_api_global = DeezerAPI()
link_downloader_global = LinkDownloader(tagger_global, navidrome_api_global, deezer_api_global)

# --- Helper Functions ---
def validate_deemix_arl(arl_to_validate):
    """
    Attempts to validate an ARL by running a deemix command in a subprocess.
    Returns True if deemix seems to accept the ARL, False otherwise.
    """
    try:
        # Creating a temporary .arl file for deemix to use in portable mode
        deemix_config_dir = os.path.join(app.root_path, '.config', 'deemix')
        os.makedirs(deemix_config_dir, exist_ok=True)
        arl_file_path = os.path.join(deemix_config_dir, '.arl')
        
        with open(arl_file_path, 'w', encoding="utf-8") as f:
            f.write(arl_to_validate)

        deemix_command = [
            "deemix",
            "--portable",
            "-p", "/dev/null",
            "https://www.deezer.com/track/1"
        ]
        
        # Setting HOME environment variable for the subprocess to ensure deemix finds its config
        env = os.environ.copy()
        env['HOME'] = app.root_path 
        
        # Deemix w/ a short timeout, as it hangs if ARL is bad
        result = subprocess.run(deemix_command, capture_output=True, text=True, env=env, timeout=10)

        if "Paste here your arl:" in result.stdout or "Aborted!" in result.stderr:
            print(f"Deemix ARL validation failed: {result.stdout} {result.stderr}")
            return False

        return True
    except subprocess.TimeoutExpired:
        print("Deemix ARL validation timed out.")
        return False
    except Exception as e:
        print(f"Error during deemix ARL validation: {e}")
        return False
    finally:
        # Clean up the temporary .arl file
        if 'arl_file_path' in locals() and os.path.exists(arl_file_path):
            os.remove(arl_file_path)

def get_current_cron_schedule():
    try:
        # Read the crontab file
        with open('/etc/cron.d/trackdrop-cron', 'r') as f:
            cron_line = f.read().strip()
        # Extract the schedule part (e.g., "0 0 * * 2")
        match = re.match(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+.*", cron_line)
        if match:
            return match.group(1)
    except FileNotFoundError:
        return "0 0 * * 2"
    return "0 0 * * 2"

def rebuild_cron_from_settings():
    """Rebuild /etc/cron.d/trackdrop-cron from all users' persisted settings.
    Each enabled user gets their own cron line with --user <username>."""
    try:
        all_users = user_manager.get_all_users()
        cron_lines = []
        for username in all_users:
            settings = user_manager.get_user_settings(username)
            if not settings.get('cron_enabled', True):
                continue
            minute = settings.get('cron_minute', 0)
            hour = settings.get('cron_hour', 0)
            day = settings.get('cron_day', 2)
            timezone = settings.get('cron_timezone', 'UTC')
            # Convert user's local time to UTC for the container's cron
            utc_minute, utc_hour, utc_day = convert_local_to_utc(minute, hour, day, timezone)
            cron_lines.append(
                f"{utc_minute} {utc_hour} * * {utc_day} root /usr/local/bin/python3 /app/trackdrop.py --user {username} >> /proc/1/fd/1 2>&1"
            )

        cron_file = '/etc/cron.d/trackdrop-cron'
        if cron_lines:
            with open(cron_file, 'w') as f:
                f.write('\n'.join(cron_lines) + '\n')
            os.chmod(cron_file, 0o644)
        else:
            # No enabled users — remove cron file
            if os.path.exists(cron_file):
                os.remove(cron_file)

        # cron daemon automatically picks up changes to /etc/cron.d/ files
        return True
    except Exception as e:
        import traceback
        print(f"Error rebuilding cron from settings: {e}", flush=True)
        traceback.print_exc()
        return False

def update_cron_schedule(new_schedule):
    """Legacy helper — kept for backwards compatibility but prefer rebuild_cron_from_settings."""
    try:
        with open('/etc/cron.d/trackdrop-cron', 'r') as f:
            cron_line = f.read().strip()

        command_match = re.match(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(.*)", cron_line)
        if command_match:
            command_part = command_match.group(1)
            new_cron_line = f"{new_schedule} {command_part}"
            with open('/etc/cron.d/trackdrop-cron', 'w') as f:
                f.write(new_cron_line + '\n')
            return True
    except Exception as e:
        print(f"Error updating cron schedule: {e}")
        return False
    return False

# --- Helper to update download status (used by background tasks, or simulated) ---
def update_download_status(download_id, status, message=None, title=None, current_track_count=None, total_track_count=None, **kwargs):
    if download_id in downloads_queue:
        item = downloads_queue[download_id]
        item['status'] = status
        if message is not None:
            item['message'] = message
        if title is not None:
            item['title'] = title
        if current_track_count is not None:
            item['current_track_count'] = current_track_count
        if total_track_count is not None:
            item['total_track_count'] = total_track_count
        # Pass through extra fields
        for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type'):
            if key in kwargs and kwargs[key] is not None:
                item[key] = kwargs[key]
    else:
        print(f"Download ID {download_id} not found in queue.")
        print(f"Download ID {download_id} not in memory queue. Creating new entry from status file.")
        new_item = {
            'id': download_id,
            'username': '',
            'artist': 'Playlist Download',
            'title': title or f'Download {download_id[:8]}...',
            'status': status,
            'start_time': datetime.now().isoformat(),
            'message': message,
            'current_track_count': current_track_count,
            'total_track_count': total_track_count
        }
        for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type'):
            if key in kwargs and kwargs[key] is not None:
                new_item[key] = kwargs[key]
        downloads_queue[download_id] = new_item

DOWNLOAD_STATUS_DIR = "/tmp/trackdrop_download_status"
DOWNLOAD_QUEUE_CLEANUP_INTERVAL_SECONDS = 300 # 5 minutes

def poll_download_statuses():
    print("Starting background thread for polling download statuses...")
    while True:
        try:
            if os.path.exists(DOWNLOAD_STATUS_DIR):
                for filename in os.listdir(DOWNLOAD_STATUS_DIR):
                    if filename.endswith(".json"):
                        download_id = filename.split(".")[0]
                        filepath = os.path.join(DOWNLOAD_STATUS_DIR, filename)
                        
                        try:
                            with open(filepath, 'r') as f:
                                status_data = json.load(f)
                            
                            status = status_data.get('status')
                            message = status_data.get('message')
                            title = status_data.get('title')
                            current_track_count = status_data.get('current_track_count')
                            total_track_count = status_data.get('total_track_count')
                            timestamp = datetime.fromisoformat(status_data.get('timestamp'))

                            # Check if an update to the in-memory queue is needed
                            needs_update = False
                            if download_id not in downloads_queue:
                                needs_update = True
                            else:
                                current_item = downloads_queue[download_id]
                                if current_item['status'] != status or \
                                   (title and current_item.get('title') != title) or \
                                   (message and current_item.get('message') != message) or \
                                   (current_track_count is not None and current_item.get('current_track_count') != current_track_count) or \
                                   (total_track_count is not None and current_item.get('total_track_count') != total_track_count):
                                    needs_update = True

                            if needs_update:
                                print(f"Polling: Found update for {download_id}. New status: {status}, New title: {title}")
                                extra = {}
                                for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type'):
                                    if key in status_data:
                                        extra[key] = status_data[key]
                                update_download_status(download_id, status, message, title, current_track_count, total_track_count, **extra)

                            # Cleanup completed/failed entries and their files after an interval
                            if status in ['completed', 'failed']:
                                # Convert start_time to datetime object for comparison
                                item_start_time = datetime.fromisoformat(downloads_queue[download_id]['start_time'])
                                if (datetime.now() - item_start_time).total_seconds() > DOWNLOAD_QUEUE_CLEANUP_INTERVAL_SECONDS:
                                    print(f"Cleaning up old download entry {download_id} (status: {status}).")
                                    del downloads_queue[download_id]
                                    os.remove(filepath)
                                    print(f"Removed status file {filepath}.")

                        except json.JSONDecodeError:
                            print(f"Error decoding JSON from status file: {filepath}")
                        except Exception as e:
                            print(f"Error processing status file {filepath}: {e}")
            
            # Remove any entries from downloads_queue that don't have a corresponding file
            # This handles cases where a file might have been manually deleted or an error occurred
            current_status_files = {f.split(".")[0] for f in os.listdir(DOWNLOAD_STATUS_DIR) if f.endswith(".json")} if os.path.exists(DOWNLOAD_STATUS_DIR) else set()
            ids_to_remove = [
                dl_id for dl_id in downloads_queue 
                if dl_id not in current_status_files and downloads_queue[dl_id]['status'] not in ['completed', 'failed']
            ]
            for dl_id in ids_to_remove:
                print(f"Removing download ID {dl_id} from queue: no corresponding status file found.")
                update_download_status(dl_id, 'failed', 'Status file disappeared unexpectedly.')
                # Mark as failed before removing if not already completed/failed
                # This ensures the UI reflects a failure if the file vanishes mid-download
                if downloads_queue[dl_id]['status'] not in ['completed', 'failed']:
                     downloads_queue[dl_id]['status'] = 'failed'
                     downloads_queue[dl_id]['message'] = 'Status file disappeared unexpectedly.'

                # Still clean up if it's been in a terminal state for long enough
                item_start_time = datetime.fromisoformat(downloads_queue[dl_id]['start_time'])
                if (datetime.now() - item_start_time).total_seconds() > DOWNLOAD_QUEUE_CLEANUP_INTERVAL_SECONDS:
                     del downloads_queue[dl_id]


        except Exception as e:
            print(f"Error in poll_download_statuses thread: {e}")
        time.sleep(5) # Poll every 5 seconds

# --- Routes ---

@app.route('/login', methods=['GET'])
def login():
    if 'username' in session:
        next_url = request.args.get('next', '/')
        return redirect(next_url)
    return render_template('login.html', next_url=request.args.get('next', '/'))

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required"}), 400
    success, error_reason = user_manager.authenticate(username, password)
    if success:
        session['username'] = username
        return jsonify({"status": "success", "message": "Login successful"})
    if error_reason == "offline":
        return jsonify({"status": "error", "message": "Could not connect to Navidrome. Check that the server is running and accessible."}), 503
    return jsonify({"status": "error", "message": "Invalid username or password."}), 401

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/user/settings', methods=['GET'])
@login_required
def get_user_settings():
    username = get_current_user()
    settings = user_manager.get_user_settings(username)
    # Mask sensitive fields
    masked = dict(settings)
    for key in ['listenbrainz_token', 'lastfm_password', 'lastfm_api_key', 'lastfm_api_secret', 'lastfm_session_key']:
        if masked.get(key):
            masked[key] = '••••••••'
    return jsonify({"status": "success", "settings": masked, "first_time": user_manager.is_first_time(username)})

@app.route('/api/user/settings', methods=['POST'])
@login_required
def update_user_settings():
    username = get_current_user()
    data = request.get_json()
    current = user_manager.get_user_settings(username)
    # Don't overwrite sensitive fields if masked
    for key in ['listenbrainz_token', 'lastfm_password', 'lastfm_api_key', 'lastfm_api_secret', 'lastfm_session_key']:
        if data.get(key) == '••••••••':
            data.pop(key, None)
    user_manager.update_user_settings(username, data)
    return jsonify({"status": "success", "message": "Settings saved successfully"})

@app.route('/api/user/setup_done', methods=['POST'])
@login_required
def mark_setup_done():
    user_manager.mark_setup_done(get_current_user())
    return jsonify({"status": "success"})

# --- PWA Routes ---
@app.route('/manifest.json')
def pwa_manifest():
    manifest = {
        "name": "TrackDrop",
        "short_name": "TrackDrop",
        "description": "Music recommendation & download manager",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#1a1a2e",
        "theme_color": "#4142e0",
        "icons": [
            {"src": "/assets/logo.svg", "sizes": "any", "type": "image/svg+xml"},
            {"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/assets/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
        ],
        "share_target": {
            "action": "/share",
            "method": "POST",
            "enctype": "application/x-www-form-urlencoded",
            "params": {
                "title": "title",
                "text": "text",
                "url": "url"
            }
        }
    }
    return jsonify(manifest)

@app.route('/share', methods=['GET', 'POST'])
def share_target():
    """PWA share target: receives a shared URL and queues it for download."""
    # Extract the shared link from POST form data or GET query params
    if request.method == 'POST':
        shared_url = request.form.get('url') or request.form.get('text') or ''
    else:
        shared_url = request.args.get('url') or request.args.get('text') or ''

    # Android often sends "Title\nhttps://..." in the text field — extract the URL
    import re as _re
    url_match = _re.search(r'https?://\S+', shared_url)
    link = url_match.group(0) if url_match else shared_url.strip()

    if link:
        session['pending_share_url'] = link

    # If not logged in, redirect to login which will bounce back to index
    if 'username' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('index'))


@app.route('/api/quick_download', methods=['POST', 'GET'])
def quick_download():
    """API endpoint for iOS Shortcuts - accepts API key auth via header or query param."""
    import re as _re

    # Get API key from header or query param
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    if not api_key:
        return jsonify({"status": "error", "message": "API key required. Use X-API-Key header or api_key query param."}), 401

    # Look up user by API key
    username = user_manager.get_user_by_api_key(api_key)
    if not username:
        return jsonify({"status": "error", "message": "Invalid API key."}), 401

    # Get URL from various sources (JSON body, form data, query param)
    if request.is_json:
        data = request.get_json()
        url = data.get('url') or data.get('link') or data.get('text') or ''
    else:
        url = request.form.get('url') or request.form.get('link') or request.form.get('text') or \
              request.args.get('url') or request.args.get('link') or request.args.get('text') or ''

    # Extract URL if text contains other content
    url_match = _re.search(r'https?://\S+', url)
    link = url_match.group(0) if url_match else url.strip()

    if not link:
        return jsonify({"status": "error", "message": "No URL provided."}), 400

    # Queue the download
    download_id = str(uuid.uuid4())
    downloads_queue[download_id] = {
        'id': download_id,
        'username': username,
        'artist': 'Link Download',
        'title': link,
        'status': 'in_progress',
        'start_time': datetime.now().isoformat(),
        'message': 'Download initiated via API.'
    }

    # Run download in background thread
    def _run_download():
        try:
            result = asyncio.run(link_downloader_global.download_from_url(link, download_id=download_id))
            if result:
                current_title = downloads_queue.get(download_id, {}).get('title', link)
                update_download_status(download_id, 'completed', f"Downloaded {len(result)} files.", title=current_title)
            else:
                current_title = downloads_queue.get(download_id, {}).get('title', link)
                update_download_status(download_id, 'failed', f"No files downloaded.", title=current_title)
        except Exception as e:
            update_download_status(download_id, 'failed', f"Error: {str(e)}")

    threading.Thread(target=_run_download, daemon=True).start()

    return jsonify({
        "status": "success",
        "message": f"Download queued for {link}",
        "download_id": download_id
    })


@app.route('/api/generate_api_key', methods=['POST'])
@login_required
def generate_api_key():
    """Generate a new API key for the current user."""
    username = get_current_user()
    api_key = user_manager.generate_api_key(username)
    return jsonify({"status": "success", "api_key": api_key})


@app.route('/api/get_api_key', methods=['GET'])
@login_required
def get_api_key():
    """Get the current user's API key."""
    username = get_current_user()
    settings = user_manager.get_user_settings(username)
    api_key = settings.get("api_key", "")
    return jsonify({"api_key": api_key})


@app.route('/ios-shortcut')
@login_required
def ios_shortcut_setup():
    """Page with iOS Shortcut setup instructions."""
    username = get_current_user()
    settings = user_manager.get_user_settings(username)
    api_key = settings.get("api_key", "")
    server_url = request.host_url.rstrip('/')

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>iOS Shortcut Setup</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #eee; }}
        h1 {{ color: #fff; }}
        .card {{ background: #16213e; border-radius: 12px; padding: 20px; margin: 20px 0; }}
        .api-key {{ font-family: monospace; background: #0f0f23; padding: 12px; border-radius: 8px; word-break: break-all; margin: 10px 0; }}
        button {{ background: #e94560; color: white; border: none; padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 16px; margin: 5px; }}
        button:hover {{ background: #ff6b6b; }}
        .step {{ margin: 15px 0; padding-left: 20px; border-left: 3px solid #e94560; }}
        code {{ background: #0f0f23; padding: 2px 6px; border-radius: 4px; }}
        a {{ color: #e94560; }}
        .back-link {{ display: inline-block; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <a href="/" class="back-link">&larr; Back to Home</a>
    <h1>iOS Shortcut Setup</h1>

    <div class="card">
        <h2>Your API Key</h2>
        <div class="api-key" id="apiKeyDisplay">{api_key if api_key else '(No API key generated yet)'}</div>
        <button onclick="generateKey()">Generate New Key</button>
        <button onclick="copyKey()">Copy Key</button>
    </div>

    <div class="card">
        <h2>Setup Instructions</h2>
        <div class="step">
            <strong>1.</strong> Open the <strong>Shortcuts</strong> app on your iPhone/iPad
        </div>
        <div class="step">
            <strong>2.</strong> Tap <strong>+</strong> to create a new shortcut
        </div>
        <div class="step">
            <strong>3.</strong> Add action: <strong>"Receive input from Share Sheet"</strong><br>
            <small>Set input type to "URLs" and "Text"</small>
        </div>
        <div class="step">
            <strong>4.</strong> Add action: <strong>"Get URLs from Input"</strong>
        </div>
        <div class="step">
            <strong>5.</strong> Add action: <strong>"Get Contents of URL"</strong> with these settings:<br>
            <small>
            URL: <code>{server_url}/api/quick_download?api_key={api_key if api_key else 'YOUR_API_KEY'}&url=[URLs]</code><br>
            Method: <code>GET</code>
            </small>
        </div>
        <div class="step">
            <strong>6.</strong> Add action: <strong>"Show Notification"</strong> (optional)<br>
            <small>Title: "Download Queued", Body: "Shortcut Input"</small>
        </div>
        <div class="step">
            <strong>7.</strong> Name the shortcut (e.g., "Download Music") and tap <strong>Done</strong>
        </div>
    </div>

    <div class="card">
        <h2>Quick Setup URL</h2>
        <p>Copy this URL and paste it in the "Get Contents of URL" action:</p>
        <div class="api-key" id="quickUrl">{server_url}/api/quick_download?api_key={api_key if api_key else 'YOUR_API_KEY'}&url=</div>
        <button onclick="copyUrl()">Copy URL</button>
        <p><small>The shortcut will append the shared URL automatically.</small></p>
    </div>

    <div class="card">
        <h2>Usage</h2>
        <p>Once set up, you can share any music link (Spotify, Apple Music, YouTube, etc.) and select your shortcut from the share sheet. The download will be queued automatically!</p>
    </div>

    <script>
        async function generateKey() {{
            const resp = await fetch('/api/generate_api_key', {{ method: 'POST' }});
            const data = await resp.json();
            if (data.api_key) {{
                document.getElementById('apiKeyDisplay').textContent = data.api_key;
                // Update quick URL too
                const baseUrl = '{server_url}/api/quick_download?api_key=';
                document.getElementById('quickUrl').textContent = baseUrl + data.api_key + '&url=';
                alert('New API key generated!');
            }}
        }}
        function copyKey() {{
            const key = document.getElementById('apiKeyDisplay').textContent;
            if (key && !key.includes('No API key')) {{
                navigator.clipboard.writeText(key);
                alert('API key copied!');
            }} else {{
                alert('Generate an API key first.');
            }}
        }}
        function copyUrl() {{
            const url = document.getElementById('quickUrl').textContent;
            if (url && !url.includes('YOUR_API_KEY')) {{
                navigator.clipboard.writeText(url);
                alert('URL copied!');
            }} else {{
                alert('Generate an API key first.');
            }}
        }}
    </script>
</body>
</html>'''


@app.route('/sw.js')
def service_worker():
    sw_content = """
self.addEventListener('install', e => e.waitUntil(self.skipWaiting()));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
    const url = new URL(e.request.url);
    // Handle PWA share target POST — extract the shared URL and redirect as GET
    if (url.pathname === '/share' && e.request.method === 'POST') {
        e.respondWith((async () => {
            const formData = await e.request.formData();
            const text = formData.get('text') || formData.get('url') || formData.get('title') || '';
            return Response.redirect('/share?text=' + encodeURIComponent(text), 303);
        })());
        return;
    }
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""
    from flask import Response
    return Response(sw_content.strip(), mimetype='application/javascript')

@app.route('/api/download_queue', methods=['GET'])
@login_required
def get_download_queue():
    # Update the queue from status files to ensure latest data
    if os.path.exists(DOWNLOAD_STATUS_DIR):
        for filename in os.listdir(DOWNLOAD_STATUS_DIR):
            if filename.endswith(".json"):
                download_id = filename.split(".")[0]
                filepath = os.path.join(DOWNLOAD_STATUS_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        status_data = json.load(f)
                    status = status_data.get('status')
                    message = status_data.get('message')
                    title = status_data.get('title')
                    current_track_count = status_data.get('current_track_count')
                    total_track_count = status_data.get('total_track_count')
                    extra = {}
                    for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type'):
                        if key in status_data:
                            extra[key] = status_data[key]
                    update_download_status(download_id, status, message, title, current_track_count, total_track_count, **extra)
                except Exception as e:
                    print(f"Error processing status file {filepath} in /api/download_queue: {e}")

    # Filter to only show downloads belonging to the current user
    username = get_current_user()
    queue_list = [item for item in downloads_queue.values() if item.get('username') == username]
    # Most recent first
    queue_list.sort(key=lambda x: x.get('start_time', ''), reverse=True)
    return jsonify({"status": "success", "queue": queue_list})

@app.route('/')
@login_required
def index():
    username = get_current_user()
    user_settings = user_manager.get_user_settings(username)
    first_time = user_manager.is_first_time(username)

    current_cron = get_current_cron_schedule()

    # Parse cron schedule to extract hour and day (use per-user settings if available)
    cron_minute = user_settings.get('cron_minute', 0)
    cron_hour = user_settings.get('cron_hour', 0)
    cron_day = user_settings.get('cron_day', 2)
    cron_enabled = user_settings.get('cron_enabled', True)
    cron_timezone = user_settings.get('cron_timezone', 'UTC')

    pending_share = session.pop('pending_share_url', None)

    return render_template('index.html',
        cron_schedule=current_cron,
        cron_minute=cron_minute,
        cron_hour=cron_hour,
        cron_day=cron_day,
        cron_enabled=cron_enabled,
        cron_timezone=cron_timezone,
        timezones=TIMEZONE_LIST,
        username=username,
        first_time=first_time,
        user_settings=json.dumps(user_settings),
        llm_enabled=LLM_ENABLED,
        hide_fresh_releases=True,
        pending_share_url=pending_share
    )

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'assets'), 'favicon.ico', mimetype='image/x-icon')

@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(app.root_path, 'assets'), filename)

@app.route('/api/config', methods=['GET'])
@login_required
def get_config():
    return jsonify({
        "ROOT_ND": "••••••••" if ROOT_ND else "",
        "USER_ND": USER_ND,
        "PASSWORD_ND": "••••••••" if PASSWORD_ND else "",
        "LISTENBRAINZ_ENABLED": LISTENBRAINZ_ENABLED,
        "TOKEN_LB": "••••••••" if TOKEN_LB else "",
        "USER_LB": USER_LB,
        "LASTFM_ENABLED": LASTFM_ENABLED,
        "LASTFM_API_KEY": "••••••••" if LASTFM_API_KEY else "",
        "LASTFM_API_SECRET": "••••••••" if LASTFM_API_SECRET else "",
        "LASTFM_USERNAME": LASTFM_USERNAME,
        "LASTFM_SESSION_KEY": "••••••••" if LASTFM_SESSION_KEY else "",
        "DEEZER_ARL": "••••••••" if DEEZER_ARL else "",
        "DOWNLOAD_METHOD": DOWNLOAD_METHOD,
        "ALBUM_RECOMMENDATION_ENABLED": ALBUM_RECOMMENDATION_ENABLED,
        "HIDE_DOWNLOAD_FROM_LINK": HIDE_DOWNLOAD_FROM_LINK,
        "HIDE_FRESH_RELEASES": HIDE_FRESH_RELEASES,
        "LLM_ENABLED": LLM_ENABLED,
        "LLM_PROVIDER": LLM_PROVIDER,
        "LLM_API_KEY": "••••••••" if LLM_API_KEY else "",
        "LLM_MODEL_NAME": globals().get("LLM_MODEL_NAME", ""),
        "LLM_BASE_URL": globals().get("LLM_BASE_URL", ""),
        "CRON_SCHEDULE": get_current_cron_schedule(),
        "PLAYLIST_MODE": globals().get("PLAYLIST_MODE", "tags")
    })

@app.route('/api/update_arl', methods=['POST'])
@login_required
def update_arl():
    data = request.get_json()
    new_arl = data.get('arl')
    if not new_arl:
        return jsonify({"status": "error", "message": "ARL is required"}), 400
    
    # Update streamrip_config.toml directly
    streamrip_config_path = "/root/.config/streamrip/config.toml"
    try:
        with open(streamrip_config_path, 'r') as f:
            content = f.read()
        content = re.sub(r'arl = ".*"', f'arl = "{new_arl}"', content)
        with open(streamrip_config_path, 'w') as f:
            f.write(content)
        
        # Validate the new ARL
        if not validate_deemix_arl(new_arl):
            return jsonify({"status": "warning", "message": "ARL updated, but it appears to be invalid or stale. Please check your ARL and restart the container."}), 200
            
        return jsonify({"status": "success", "message": "ARL updated successfully (in-memory and streamrip config). Restart container for full persistence."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to update streamrip config: {e}"}), 500

@app.route('/api/update_cron', methods=['POST'])
@login_required
def update_cron():
    data = request.get_json()
    new_schedule = data.get('schedule')
    if not new_schedule:
        return jsonify({"status": "error", "message": "Cron schedule is required"}), 400

    # Parse minute, hour and day from cron schedule "MINUTE HOUR * * DAY"
    parts = new_schedule.split()
    if len(parts) >= 5:
        try:
            cron_minute = int(parts[0])
            cron_hour = int(parts[1])
            cron_day = int(parts[4])
        except ValueError:
            return jsonify({"status": "error", "message": "Invalid cron schedule format"}), 400
    else:
        return jsonify({"status": "error", "message": "Invalid cron schedule format"}), 400

    # Persist to user settings (store the user's LOCAL time + timezone)
    timezone = data.get('timezone', 'UTC')
    username = get_current_user()
    user_manager.update_user_settings(username, {
        "cron_minute": cron_minute,
        "cron_hour": cron_hour,
        "cron_day": cron_day,
        "cron_timezone": timezone,
    })

    # Rebuild system cron from all users' settings
    if rebuild_cron_from_settings():
        return jsonify({"status": "success", "message": "Cron schedule updated successfully."})
    else:
        return jsonify({"status": "error", "message": "Failed to update cron schedule."}), 500

@app.route('/api/update_config', methods=['POST'])
@login_required
def update_config():
    data = request.get_json()
    try:
        # Read current config.py content
        with open('config.py', 'r') as f:
            current_config_content = f.read()

        # Define sensitive fields that should not be overwritten if masked
        sensitive_fields = {'ROOT_ND', 'PASSWORD_ND', 'TOKEN_LB', 'LASTFM_API_KEY', 'LASTFM_API_SECRET', 'LASTFM_SESSION_KEY', 'DEEZER_ARL', 'LLM_API_KEY'}

        # Prepare a list to hold the updated lines
        updated_lines = current_config_content.splitlines()
        
        # Keep track of updated keys to avoid redundant processing
        updated_keys_in_memory = {}

        # Process updates only for keys present in the incoming data
        for key, value in data.items():
            # Skip updating masked sensitive fields
            if key in sensitive_fields and value == '••••••••':
                # For masked sensitive fields, retrieve current value from globals
                if key in globals():
                    updated_keys_in_memory[key] = globals()[key]
                continue

            # Determine the string representation for writing to config.py
            if key in {'LISTENBRAINZ_ENABLED', 'LASTFM_ENABLED', 'ALBUM_RECOMMENDATION_ENABLED', 'HIDE_DOWNLOAD_FROM_LINK', 'HIDE_FRESH_RELEASES', 'LLM_ENABLED'}:
                # Ensure boolean values are written as True/False (Python literal)
                new_value_str_for_file = str(value) 
            elif key in ('DOWNLOAD_METHOD', 'LLM_PROVIDER', 'PLAYLIST_MODE'):
                new_value_str_for_file = f'"{value}"'
            else:
                # For other string values, ensure they are quoted
                new_value_str_for_file = f'"{value}"' if isinstance(value, str) else str(value)
            
            # Update global variables in memory
            globals()[key] = value
            updated_keys_in_memory[key] = value

            # Update the corresponding line in config.py content
            pattern = re.compile(rf'^{key}\s*=\s*.*$', re.MULTILINE)
            if pattern.search(current_config_content): # Only modify if the key exists 
                current_config_content = pattern.sub(f'{key} = {new_value_str_for_file}', current_config_content)

        # Write updated config.py file for persistence
        with open('config.py', 'w') as f:
            f.write(current_config_content)

        # Reinitialize global API instances with updated config
        global navidrome_api_global, link_downloader_global
        navidrome_api_global = NavidromeAPI(
            root_nd=globals().get('ROOT_ND', ''),
            user_nd=globals().get('USER_ND', ''),
            password_nd=globals().get('PASSWORD_ND', ''),
            music_library_path=globals().get('MUSIC_LIBRARY_PATH', ''),
            target_comment=globals().get('TARGET_COMMENT', ''),
            lastfm_target_comment=globals().get('LASTFM_TARGET_COMMENT', ''),
            album_recommendation_comment=globals().get('ALBUM_RECOMMENDATION_COMMENT', ''),
            listenbrainz_enabled=globals().get('LISTENBRAINZ_ENABLED', False),
            lastfm_enabled=globals().get('LASTFM_ENABLED', False),
            llm_target_comment=globals().get('LLM_TARGET_COMMENT', ''),
            llm_enabled=globals().get('LLM_ENABLED', False),
            admin_user=globals().get('ADMIN_USER', ''),
            admin_password=globals().get('ADMIN_PASSWORD', ''),
            navidrome_db_path=globals().get('NAVIDROME_DB_PATH', '')
        )
        link_downloader_global = LinkDownloader(tagger_global, navidrome_api_global, deezer_api_global)

        # Update streamrip config if ARL changed and it's not the obfuscated value
        if 'DEEZER_ARL' in data and data['DEEZER_ARL'] and data['DEEZER_ARL'] != '••••••••':
            streamrip_config_path = "/root/.config/streamrip/config.toml"
            try:
                os.makedirs(os.path.dirname(streamrip_config_path), exist_ok=True)
                with open(streamrip_config_path, 'r') as f:
                    streamrip_content = f.read()
                streamrip_content = re.sub(r'arl = ".*"', f'arl = "{data["DEEZER_ARL"]}"', streamrip_content)
                with open(streamrip_config_path, 'w') as f:
                    f.write(streamrip_content)
                
                # Also update deemix ARL file
                deemix_config_dir = '/root/.config/deemix'
                os.makedirs(deemix_config_dir, exist_ok=True)
                with open(os.path.join(deemix_config_dir, '.arl'), 'w') as f:
                    f.write(data["DEEZER_ARL"])
            except Exception as e:
                print(f"Warning: Could not update streamrip/deemix config files: {e}")

        return jsonify({"status": "success", "message": "Configuration updated successfully. Settings are now active."})
    except Exception as e:
        # Debug traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Failed to update configuration: {e}"}), 500

@app.route('/api/get_listenbrainz_playlist', methods=['GET'])
@login_required
def get_listenbrainz_playlist():
    print("Attempting to get ListenBrainz playlist...")

    # Check if ListenBrainz credentials are configured
    if not USER_LB or not TOKEN_LB:
        return jsonify({"status": "error", "message": "ListenBrainz credentials not configured. Please set USER_LB and TOKEN_LB in the config menu."}), 400

    try:
        print("Creating ListenBrainzAPI instance with current config...")
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        print("Running async get_listenbrainz_recommendations...")
        lb_recs = asyncio.run(listenbrainz_api.get_listenbrainz_recommendations())
        print(f"ListenBrainz recommendations found: {len(lb_recs)}")
        if lb_recs:
            return jsonify({"status": "success", "recommendations": lb_recs})
        else:
            return jsonify({"status": "info", "message": "No new ListenBrainz recommendations found."})
    except Exception as e:
        print(f"Error getting ListenBrainz playlist: {e}")
        print("Traceback:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error getting ListenBrainz playlist: {e}"}), 500

@app.route('/api/trigger_listenbrainz_download', methods=['POST'])
@login_required
def trigger_listenbrainz_download():
    print("Attempting to trigger ListenBrainz download via background script...")
    try:
        # Check if there are recommendations first
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        recs = asyncio.run(listenbrainz_api.get_listenbrainz_recommendations())
        if not recs:
            return jsonify({"status": "error", "message": "No ListenBrainz recommendations found. Please check your credentials and try again."}), 400
        
        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': get_current_user(),
            'artist': 'ListenBrainz Playlist',
            'title': 'Multiple Tracks',
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.',
            'current_track_count': 0,
            'total_track_count': None,
            'download_type': 'playlist',
            'tracks': [],
            'downloaded_count': 0,
            'failed_count': 0,
        }

        # Execute trackdrop.py in a separate process for non-blocking download, bypassing playlist check
        subprocess.Popen([
            sys.executable, '/app/trackdrop.py',
            '--source', 'listenbrainz',
            '--bypass-playlist-check',
            '--download-id', download_id,
            '--user', get_current_user()
        ])
        return jsonify({"status": "info", "message": "ListenBrainz download initiated in the background."})
    except Exception as e:
        print(f"Error triggering ListenBrainz download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering ListenBrainz download: {e}"}), 500

@app.route('/api/get_lastfm_playlist', methods=['GET'])
@login_required
def get_lastfm_playlist():
    print("Attempting to get Last.fm playlist...")

    # Check if Last.fm credentials are configured
    if not LASTFM_USERNAME or not LASTFM_API_KEY or not LASTFM_API_SECRET:
        return jsonify({"status": "error", "message": "Last.fm credentials not configured. Please set LASTFM_USERNAME, LASTFM_API_KEY, and LASTFM_API_SECRET in the config menu."}), 400

    try:
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)
        lf_recs = asyncio.run(lastfm_api.get_lastfm_recommendations())
        print(f"Last.fm recommendations found: {len(lf_recs)}")
        if lf_recs:
            return jsonify({"status": "success", "recommendations": lf_recs})
        else:
            return jsonify({"status": "info", "message": "No new Last.fm recommendations found."})
    except Exception as e:
        print(f"Error getting Last.fm playlist: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error getting Last.fm playlist: {e}"}), 500

@app.route('/api/trigger_lastfm_download', methods=['POST'])
@login_required
def trigger_lastfm_download():
    print("Attempting to trigger Last.fm download via background script...")
    try:
        # Check if there are recommendations first
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)
        recs = asyncio.run(lastfm_api.get_lastfm_recommendations())
        if not recs:
            return jsonify({"status": "error", "message": "No Last.fm recommendations found. Please check your credentials and try again."}), 400
        
        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': get_current_user(),
            'artist': 'Last.fm Playlist',
            'title': 'Multiple Tracks',
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.',
            'current_track_count': 0,
            'total_track_count': None,
            'download_type': 'playlist',
            'tracks': [],
            'downloaded_count': 0,
            'failed_count': 0,
        }

        # Execute trackdrop.py in a separate process for non-blocking download
        subprocess.Popen([
            sys.executable, '/app/trackdrop.py',
            '--source', 'lastfm',
            '--download-id', download_id,
            '--user', get_current_user()
        ])
        return jsonify({"status": "info", "message": "Last.fm download initiated in the background."})
    except Exception as e:
        print(f"Error triggering Last.fm download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering Last.fm download: {e}"}), 500

@app.route('/api/trigger_navidrome_cleanup', methods=['POST'])
@login_required
def trigger_navidrome_cleanup():
    print("Attempting to trigger Navidrome cleanup...")
    try:
        # Initialize API instances for cleanup
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)

        import asyncio
        playlist_mode = globals().get('PLAYLIST_MODE', 'tags')
        if playlist_mode == 'api':
            download_history_path = get_user_history_path(get_current_user())
            asyncio.run(navidrome_api_global.process_api_cleanup(
                history_path=download_history_path,
                listenbrainz_api=listenbrainz_api,
                lastfm_api=lastfm_api
            ))
        else:
            asyncio.run(navidrome_api_global.process_navidrome_library(listenbrainz_api=listenbrainz_api, lastfm_api=lastfm_api))
        return jsonify({"status": "success", "message": "Navidrome cleanup completed successfully."})
    except Exception as e:
        print(f"Error triggering Navidrome cleanup: {e}")
        return jsonify({"status": "error", "message": f"Error during Navidrome cleanup: {e}"}), 500

@app.route('/api/trigger_debug_cleanup', methods=['POST'])
@login_required
def trigger_debug_cleanup():
    """Debug: clear playlists and remove all songs not rated 4-5 stars."""
    import sys
    username = get_current_user()
    print(f"[DEBUG CLEANUP] Triggered by user: {username}", flush=True)
    sys.stdout.flush()
    try:
        download_history_path = get_user_history_path(username)
        print(f"[DEBUG CLEANUP] Using history path: {download_history_path}", flush=True)
        sys.stdout.flush()
        summary = asyncio.run(navidrome_api_global.process_debug_cleanup(
            history_path=download_history_path
        ))
        msg_parts = []
        if summary['deleted']:
            msg_parts.append(f"Deleted {len(summary['deleted'])} songs")
        if summary['kept']:
            msg_parts.append(f"Kept {len(summary['kept'])} songs (protected)")
        if summary.get('failed'):
            msg_parts.append(f"Failed/skipped {len(summary['failed'])} songs")
        if summary['playlists_cleared']:
            msg_parts.append(f"Cleared playlists: {', '.join(summary['playlists_cleared'])}")
        message = '. '.join(msg_parts) if msg_parts else 'Nothing to clean up (empty history).'
        return jsonify({"status": "success", "message": message, "summary": summary})
    except Exception as e:
        print(f"Error triggering debug cleanup: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error during debug cleanup: {e}"}), 500

@app.route('/api/get_fresh_releases', methods=['GET'])
@login_required
async def get_fresh_releases():
    overall_start_time = time.perf_counter()
    print("Attempting to get ListenBrainz fresh releases...")

    # Check if ListenBrainz credentials are configured
    if not USER_LB or not TOKEN_LB:
        print("Error: ListenBrainz credentials not configured.", file=sys.stderr)
        return jsonify({"status": "error", "message": "ListenBrainz credentials not configured. Please set USER_LB and TOKEN_LB in the config menu."}), 400

    server_timing_metrics = []

    try:
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        
        lb_fetch_start_time = time.perf_counter()
        data = await listenbrainz_api.get_fresh_releases()
        lb_fetch_end_time = time.perf_counter()
        lb_fetch_duration = (lb_fetch_end_time - lb_fetch_start_time) * 1000
        server_timing_metrics.append(f"lb_fetch;dur={lb_fetch_duration:.2f};desc=\"ListenBrainz Fetch\"")
        print(f"ListenBrainz API fetch time: {lb_fetch_duration:.2f}ms")

        releases = data.get('payload', {}).get('releases', [])

        if not releases:
            print("No fresh ListenBrainz releases found.")
            response = jsonify({"status": "info", "message": "No fresh ListenBrainz releases found."})
            response.headers['Server-Timing'] = ", ".join(server_timing_metrics)
            return response

        # Parallelize Deezer availability checks
        deezer_checks_start_time = time.perf_counter()
        deezer_tasks = []
        for release in releases:
            artist = release['artist_credit_name']
            album = release['release_name']
            deezer_tasks.append(deezer_api_global.check_album_download_availability(artist, album))
        
        is_available_on_deezer_results = await asyncio.gather(*deezer_tasks)
        deezer_checks_end_time = time.perf_counter()
        deezer_checks_duration = (deezer_checks_end_time - deezer_checks_start_time) * 1000
        server_timing_metrics.append(f"deezer_checks;dur={deezer_checks_duration:.2f};desc=\"Deezer Availability Checks\"")
        print(f"Deezer availability checks (parallelized) time: {deezer_checks_duration:.2f}ms for {len(releases)} releases")

        processed_releases = []
        for i, release in enumerate(releases):
            release['is_available_on_deezer'] = is_available_on_deezer_results[i]
            processed_releases.append(release)

        print(f"ListenBrainz fresh releases found: {len(processed_releases)}")
        
        overall_end_time = time.perf_counter()
        overall_duration = (overall_end_time - overall_start_time) * 1000
        server_timing_metrics.append(f"total;dur={overall_duration:.2f};desc=\"Total API Latency\"")
        print(f"Total /api/get_fresh_releases endpoint time: {overall_duration:.2f}ms")

        response = jsonify({"status": "success", "releases": processed_releases})
        response.headers['Server-Timing'] = ", ".join(server_timing_metrics)
        return response

    except Exception as e:
        print(f"Error getting ListenBrainz fresh releases: {e}", file=sys.stderr)
        print("Traceback:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        response = jsonify({"status": "error", "message": f"Error getting ListenBrainz fresh releases: {e}"}), 500
        response[0].headers['Server-Timing'] = ", ".join(server_timing_metrics)
        return response

@app.route('/api/run_now', methods=['POST'])
@login_required
def run_now():
    """Run the full recommendation pipeline immediately (same as cron)."""
    try:
        username = get_current_user()
        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': username,
            'artist': 'All Sources',
            'title': 'Weekly Recommendations',
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Fetching recommendations from all sources...',
            'current_track_count': 0,
            'total_track_count': None,
        }
        subprocess.Popen([
            sys.executable, '/app/trackdrop.py',
            '--source', 'all',
            '--bypass-playlist-check',
            '--download-id', download_id,
            '--user', username,
        ])
        return jsonify({"status": "success", "message": "Fetching recommendations from all enabled sources in the background."})
    except Exception as e:
        print(f"Error running now: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error: {e}"}), 500

@app.route('/api/toggle_cron', methods=['POST'])
@login_required
def toggle_cron():
    data = request.get_json()
    disabled = data.get('disabled', False)
    try:
        # Persist to user settings
        username = get_current_user()
        user_manager.update_user_settings(username, {"cron_enabled": not disabled})

        # Rebuild system cron from all users' settings
        rebuild_cron_from_settings()

        if disabled:
            return jsonify({"status": "success", "message": "Automatic downloads disabled."})
        else:
            return jsonify({"status": "success", "message": "Automatic downloads enabled."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error toggling cron: {e}"}), 500

@app.route('/api/submit_listenbrainz_feedback', methods=['POST'])
@login_required
def submit_listenbrainz_feedback():
    print("Attempting to submit ListenBrainz feedback...")
    try:
        data = request.get_json()
        print(f"Received data: {data}")
        recording_mbid = data.get('recording_mbid')
        score = data.get('score')
        print(f"recording_mbid: {recording_mbid}, score: {score}")

        if not recording_mbid or score not in [1, -1]:
            print(f"Invalid data: recording_mbid={recording_mbid}, score={score}")
            return jsonify({"status": "error", "message": "Valid recording_mbid and score (1 or -1) are required"}), 400

        # Check if ListenBrainz is configured
        if not TOKEN_LB or not USER_LB:
            print(f"ListenBrainz not configured: TOKEN_LB={TOKEN_LB}, USER_LB={USER_LB}")
            return jsonify({"status": "error", "message": "ListenBrainz credentials not configured"}), 400

        print(f"Creating ListenBrainzAPI with ROOT_LB={ROOT_LB}, TOKEN_LB={'*' * len(TOKEN_LB) if TOKEN_LB else None}, USER_LB={USER_LB}")
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        print("Calling submit_feedback...")
        asyncio.run(listenbrainz_api.submit_feedback(recording_mbid, score))
        print("Feedback submitted successfully")

        feedback_type = "positive" if score == 1 else "negative"
        return jsonify({"status": "success", "message": f"{feedback_type.capitalize()} feedback submitted successfully."})

    except Exception as e:
        print(f"Error submitting ListenBrainz feedback: {e}")
        print("Traceback:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error submitting feedback: {e}"}), 500

@app.route('/api/submit_lastfm_feedback', methods=['POST'])
@login_required
def submit_lastfm_feedback():
    print("Attempting to submit Last.fm feedback...")
    try:
        data = request.get_json()
        print(f"Received data: {data}")
        track = data.get('track')
        artist = data.get('artist')
        print(f"track: {track}, artist: {artist}")

        if not track or not artist:
            print(f"Invalid data: track={track}, artist={artist}")
            return jsonify({"status": "error", "message": "Track and artist are required"}), 400

        # Check if Last.fm is configured
        if not LASTFM_API_KEY or not LASTFM_API_SECRET or not LASTFM_SESSION_KEY:
            print(f"Last.fm not configured: API_KEY={LASTFM_API_KEY}, API_SECRET={'*' * len(LASTFM_API_SECRET) if LASTFM_API_SECRET else None}, SESSION_KEY={'*' * len(LASTFM_SESSION_KEY) if LASTFM_SESSION_KEY else None}")
            return jsonify({"status": "error", "message": "Last.fm credentials not configured"}), 400

        print(f"Creating LastFmAPI with API_KEY={LASTFM_API_KEY}, API_SECRET={'*' * len(LASTFM_API_SECRET) if LASTFM_API_SECRET else None}, USERNAME={LASTFM_USERNAME}, SESSION_KEY={'*' * len(LASTFM_SESSION_KEY) if LASTFM_SESSION_KEY else None}")
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)
        print("Calling love_track...")
        lastfm_api.love_track(track, artist)
        print("Feedback submitted successfully")

        return jsonify({"status": "success", "message": "Track loved successfully."})

    except Exception as e:
        print(f"Error submitting Last.fm feedback: {e}")
        print("Traceback:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error submitting feedback: {e}"}), 500

@app.route('/api/get_llm_playlist', methods=['GET'])
@login_required
async def get_llm_playlist():
    if not LLM_ENABLED:
        return jsonify({"status": "error", "message": "LLM suggestions are not enabled in the configuration."}), 400
    if not LLM_API_KEY and LLM_PROVIDER != 'llama':
        return jsonify({"status": "error", "message": "LLM API key is not configured."}), 400
    if LLM_PROVIDER == 'llama' and not LLM_BASE_URL:
        return jsonify({"status": "error", "message": "Base URL is required for Llama.cpp."}), 400

    try:
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        scrobbles = await listenbrainz_api.get_weekly_scrobbles()

        if not scrobbles:
            return jsonify({"status": "info", "message": "Could not fetch recent scrobbles from ListenBrainz to generate recommendations."})

        llm_api = LlmAPI(
            provider=LLM_PROVIDER,
            gemini_api_key=LLM_API_KEY if LLM_PROVIDER == 'gemini' else None,
            openrouter_api_key=LLM_API_KEY if LLM_PROVIDER == 'openrouter' else None,
            llama_api_key=LLM_API_KEY if LLM_PROVIDER == 'llama' else None,
            model_name=globals().get('LLM_MODEL_NAME'),
            base_url=globals().get('LLM_BASE_URL') if LLM_PROVIDER == 'llama' else None
        )
        recommendations = llm_api.get_recommendations(scrobbles)

        if recommendations:
            # Check Deezer availability for each recommendation and filter out unavailable tracks
            available_recommendations = []
            for rec in recommendations:
                try:
                    # Check if track is available on Deezer
                    deezer_link = await deezer_api_global.get_deezer_track_link(rec['artist'], rec['title'])
                    if deezer_link:
                        available_recommendations.append(rec)
                    else:
                        print(f"LLM recommendation not available on Deezer: {rec['artist']} - {rec['title']}")
                except Exception as e:
                    print(f"Error checking Deezer availability for {rec['artist']} - {rec['title']}: {e}")
                    # If checking availability is impossible, include it anyway to avoid losing recommendations due to API errors
                    available_recommendations.append(rec)

            print(f"LLM generated {len(recommendations)} recommendations, {len(available_recommendations)} available on Deezer")

            # Fetch recording_mbid and release_mbid for each available recommendation to enable feedback and album art
            processed_recommendations = []
            for rec in available_recommendations:
                # Respect MusicBrainz rate limit (1 req/sec)
                await asyncio.sleep(1)
                mbid = await listenbrainz_api.get_recording_mbid_from_track(rec['artist'], rec['title'])
                
                rec['recording_mbid'] = mbid
                rec['caa_release_mbid'] = None
                rec['caa_id'] = None # Not available through this flow, but good to have for consistency

                if mbid:
                    await asyncio.sleep(1) # Another request, another sleep
                    # get_track_info returns: artist, title, album, release_date, release_mbid
                    _, _, fetched_album, _, release_mbid = await listenbrainz_api.get_track_info(mbid)
                    if release_mbid:
                        rec['caa_release_mbid'] = release_mbid
                    # Use the more accurate album title from MusicBrainz
                    if fetched_album and fetched_album != "Unknown Album":
                        rec['album'] = fetched_album
                
                processed_recommendations.append(rec)

            return jsonify({"status": "success", "recommendations": processed_recommendations})
        else:
            return jsonify({"status": "error", "message": "LLM failed to generate recommendations."})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"An error occurred: {e}"}), 500

@app.route('/api/trigger_llm_download', methods=['POST'])
@login_required
def trigger_llm_download():
    # This endpoint will fetch recommendations and then trigger downloads.
    # For simplicity, it wil be re-fetched. A better implementation might cache the result from get_llm_playlist.
    if not LLM_ENABLED or (not LLM_API_KEY and LLM_PROVIDER != 'llama'):
        return jsonify({"status": "error", "message": "LLM suggestions are not enabled or configured."}), 400
    if LLM_PROVIDER == 'llama' and not LLM_BASE_URL:
        return jsonify({"status": "error", "message": "Base URL is required for Llama.cpp."}), 400

    listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
    scrobbles = asyncio.run(listenbrainz_api.get_weekly_scrobbles())
    if not scrobbles:
        return jsonify({"status": "info", "message": "No scrobbles to generate recommendations from."})

    llm_api = LlmAPI(
        provider=LLM_PROVIDER,
        gemini_api_key=LLM_API_KEY if LLM_PROVIDER == 'gemini' else None,
        openrouter_api_key=LLM_API_KEY if LLM_PROVIDER == 'openrouter' else None,
        llama_api_key=LLM_API_KEY if LLM_PROVIDER == 'llama' else None,
        model_name=globals().get('LLM_MODEL_NAME'),
        base_url=globals().get('LLM_BASE_URL') if LLM_PROVIDER == 'llama' else None
    )
    recommendations = llm_api.get_recommendations(scrobbles)

    if not recommendations:
        return jsonify({"status": "error", "message": "LLM failed to generate recommendations for download."})

    download_id = str(uuid.uuid4())
    downloads_queue[download_id] = {
        'id': download_id,
        'username': get_current_user(),
        'artist': 'LLM Playlist',
        'title': f'{len(recommendations)} Tracks',
        'status': 'in_progress',
        'start_time': datetime.now().isoformat(),
        'message': 'Download initiated.',
        'current_track_count': 0,
        'total_track_count': len(recommendations),
        'download_type': 'playlist',
        'tracks': [
            {"artist": s.get("artist", "Unknown"), "title": s.get("title", "Unknown"), "status": "pending", "message": ""}
            for s in recommendations
        ],
        'downloaded_count': 0,
        'failed_count': 0,
    }

    # Execute downloads in a background thread
    threading.Thread(target=lambda: asyncio.run(download_llm_recommendations_background(recommendations, download_id))).start()

    return jsonify({"status": "info", "message": f"Started download of {len(recommendations)} tracks from LLM recommendations in the background."})

@app.route('/api/trigger_fresh_release_download', methods=['POST'])
@login_required
def trigger_fresh_release_download():
    print("Attempting to trigger fresh release album download...")
    artist = None
    try:
        data = request.get_json()
        artist = data.get('artist')
        album = data.get('album')
        release_date = data.get('release_date')
        # Global setting for album recommendations
        is_album_recommendation = ALBUM_RECOMMENDATION_ENABLED

        if not artist or not album:
            return jsonify({"status": "error", "message": "Artist and album are required"}), 400

        from downloaders.album_downloader import AlbumDownloader
        from utils import Tagger

        tagger = Tagger(ALBUM_RECOMMENDATION_COMMENT)
        # Initialize AlbumDownloader with the album recommendation comment
        album_downloader = AlbumDownloader(tagger, ALBUM_RECOMMENDATION_COMMENT)

        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': get_current_user(),
            'artist': artist,
            'title': album, # Using album as title for fresh releases
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.'
        }

        album_info = {
            'artist': artist,
            'album': album,
            'release_date': release_date,
            'album_art': None,
            'download_id': download_id # Pass download_id to the downloader
        }

        import asyncio
        print(f"Fresh Release Download Triggered for: Artist={artist}, Album={album}, Release Date={release_date}, Download ID={download_id}")
        print(f"Album Info sent to downloader: {album_info}")

        result = asyncio.run(album_downloader.download_album(album_info, is_album_recommendation=is_album_recommendation))
        # Update the global queue with the final status after download completes
        if result["status"] == "success":
            update_download_status(download_id, 'completed', f"Downloaded {len(result.get('files', []))} tracks.")
        else:
            update_download_status(download_id, 'failed', result.get('message', 'Download failed.'))

        response_message = result["message"] if "message" in result else "Operation completed."
        debug_output = {
            "album_info_sent": album_info,
            "download_result": result,
            "error_traceback": None
        }

        if result["status"] == "success":
            # Organize the downloaded files -> music library
            navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)
            return jsonify({
                "status": "success",
                "message": f"Successfully downloaded and organized album {artist} - {album} with {len(result.get('files', []))} tracks.",
                "debug_info": debug_output
            })
        else:
            return jsonify({
                "status": "error",
                "message": response_message,
                "debug_info": debug_output
            })

    except Exception as e:
        error_trace = traceback.format_exc()
        print(f"Error triggering fresh release download: {e}")
        print(error_trace) # Debugging traceback

        debug_output = {
            "album_info_sent": {'artist': artist, 'album': album, 'release_date': release_date, 'album_art': None},
            "download_result": {"status": "error", "message": str(e)},
            "error_traceback": error_trace
        }
        return jsonify({
            "status": "error",
            "message": f"Error triggering download: {e}",
            "debug_info": debug_output
        }), 500

@app.route('/api/get_track_preview', methods=['GET'])
@login_required
async def get_track_preview():
    artist = request.args.get('artist')
    title = request.args.get('title')
    if not artist or not title:
        return jsonify({"status": "error", "message": "Artist and title are required"}), 400

    try:
        deezer_api = DeezerAPI()
        preview_url = await deezer_api.get_deezer_track_preview(artist, title)
        if preview_url:
            return jsonify({"status": "success", "preview_url": preview_url})
        else:
            return jsonify({"status": "error", "message": "Preview not found for this track"}), 404
    except Exception as e:
        print(f"Error getting track preview for {artist} - {title}: {e}")
        return jsonify({"status": "error", "message": f"Error getting track preview: {e}"}), 500

@app.route('/api/trigger_track_download', methods=['POST'])
@login_required
def trigger_track_download():
    print("Attempting to trigger individual track download...")
    try:
        data = request.get_json()
        artist = data.get('artist')
        title = data.get('title')
        lb_recommendation = data.get('lb_recommendation', False)  # Get the lb_recommendation flag
        source = data.get('source', 'Manual') # Get the source

        if not artist or not title:
            return jsonify({"status": "error", "message": "Artist and title are required"}), 400

        # Use TrackDownloader
        tagger = Tagger(ALBUM_RECOMMENDATION_COMMENT)
        track_downloader = TrackDownloader(tagger)

        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': get_current_user(),
            'artist': artist,
            'title': title,
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.'
        }

        track_info = {
            'artist': artist,
            'title': title,
            'album': '',
            'release_date': '', # Will be fetched later
            'recording_mbid': '',
            'source': source,
            'download_id': download_id # Pass download_id to the downloader
        }

        downloaded_path = asyncio.run(track_downloader.download_track(track_info, lb_recommendation=lb_recommendation, navidrome_api=navidrome_api_global))
        
        if downloaded_path:
            update_download_status(download_id, 'completed', "Download completed.")
            # Organize the downloaded files -> music library
            navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)
            return jsonify({"status": "success", "message": f"Successfully downloaded and organized track: {artist} - {title}."})
        elif track_info.get('_duplicate'):
            update_download_status(download_id, 'completed', f"Already in library: {artist} - {title}")
            return jsonify({"status": "info", "message": f"Track already in library: {artist} - {title}."})
        else:
            update_download_status(download_id, 'failed', "Download failed. See logs for details.")
            return jsonify({"status": "error", "message": f"Failed to download track: {artist} - {title}."})

    except Exception as e:
        print(f"Error triggering track download: {e}")
        if 'download_id' in locals():
            update_download_status(download_id, 'failed', f"An error occurred: {e}")
        return jsonify({"status": "error", "message": f"Error triggering download: {e}"}), 500

@app.route('/api/download_from_link', methods=['POST'])
@login_required
def download_from_link():
    print("Attempting to download from link...")
    try:
        data = request.get_json()
        link = data.get('link')
        lb_recommendation = data.get('lb_recommendation', False) # Get the checkbox value, default to False

        # Auto-detect ListenBrainz playlist URLs and set lb_recommendation=True
        if 'listenbrainz.org/playlist' in link.lower():
            lb_recommendation = True
            print(f"Detected ListenBrainz playlist URL, automatically setting lb_recommendation=True")

        if not link:
            return jsonify({"status": "error", "message": "Link is required"}), 400

        # Check if it's a playlist URL — handle in background thread
        if is_playlist_url(link):
            download_id = str(uuid.uuid4())
            username = get_current_user()
            monitor = data.get('monitor', False)
            poll_interval_hours = data.get('poll_interval_hours', 24)
            playlist_name_override = data.get('playlist_name', None)

            downloads_queue[download_id] = {
                'id': download_id,
                'username': get_current_user(),
                'artist': 'Playlist Download',
                'title': link,
                'status': 'in_progress',
                'start_time': datetime.now().isoformat(),
                'message': 'Extracting playlist tracks...',
                'current_track_count': 0,
                'total_track_count': None,
                'download_type': 'playlist',
                'tracks': [],
                'downloaded_count': 0,
                'skipped_count': 0,
                'failed_count': 0,
            }

            def _run_playlist_download():
                asyncio.run(
                    download_playlist(
                        url=link,
                        username=username,
                        navidrome_api=navidrome_api_global,
                        download_id=download_id,
                        update_status_fn=update_download_status,
                        playlist_name_override=playlist_name_override,
                    )
                )
                # If user chose to monitor, add after first successful download
                if monitor:
                    from downloaders.playlist_downloader import extract_playlist_tracks as _extract
                    platform, name, tracks = _extract(link)
                    name = playlist_name_override or name
                    if not name or name.startswith("Unknown"):
                        name = link
                    entry = add_monitored_playlist(
                        url=link, name=name, platform=platform,
                        username=username, poll_interval_hours=poll_interval_hours,
                    )
                    # Update with track count and sync time from just-completed download
                    mark_synced(entry["id"], len(tracks) if tracks else None)

            threading.Thread(target=_run_playlist_download, daemon=True).start()
            msg = "Playlist download started."
            if monitor:
                msg += " Playlist will be monitored for new tracks."
            return jsonify({"status": "success", "message": msg})

        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'username': get_current_user(),
            'artist': 'Link Download',
            'title': link,
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.'
        }

        # Use globally initialized link_downloader
        result = asyncio.run(link_downloader_global.download_from_url(link, lb_recommendation=lb_recommendation, download_id=download_id))

        if result:
            # Preserve the resolved title from the status file if available
            current_title = downloads_queue.get(download_id, {}).get('title', link)
            update_download_status(download_id, 'completed', f"Downloaded {len(result)} files.", title=current_title)
            return jsonify({"status": "success", "message": f"Successfully downloaded and organized {len(result)} files from {link}."})
        else:
            current_title = downloads_queue.get(download_id, {}).get('title', link)
            update_download_status(download_id, 'failed', f"No files downloaded. The track may not be available on Deezer.", title=current_title)
            return jsonify({"status": "info", "message": f"No files downloaded from {link}. The track may not be available on Deezer."})

    except Exception as e:
        print(f"Error downloading from link: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"status": "error", "message": f"Error initiating download from link: {e}"}), 500

@app.route('/api/playlist_preflight', methods=['POST'])
@login_required
def playlist_preflight():
    """Check a playlist URL: extract name/platform, check if name already exists in Navidrome."""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        if not url or not is_playlist_url(url):
            return jsonify({"status": "error", "message": "Invalid playlist URL"}), 400

        platform, name, tracks = extract_playlist_tracks(url)
        if not tracks:
            return jsonify({"status": "error", "message": f"Could not extract tracks from playlist. Platform: {platform}"}), 400

        # Check if a playlist with this name already exists in Navidrome
        salt, token = navidrome_api_global._get_navidrome_auth_params()
        existing = navidrome_api_global._find_playlist_by_name(name, salt, token)

        return jsonify({
            "status": "success",
            "name": name,
            "platform": platform,
            "track_count": len(tracks),
            "exists": existing is not None,
        })
    except Exception as e:
        print(f"Error in playlist preflight: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": str(e)}), 500


# ---------------------------------------------------------------------------
# Monitored Playlists API
# ---------------------------------------------------------------------------

@app.route('/api/monitored_playlists', methods=['GET'])
@login_required
def api_get_monitored_playlists():
    username = get_current_user()
    playlists = [p for p in get_monitored_playlists() if p.get("username") == username]
    return jsonify({"status": "success", "playlists": playlists})


@app.route('/api/monitored_playlists', methods=['POST'])
@login_required
def api_add_monitored_playlist():
    data = request.get_json()
    url = data.get("url", "").strip()
    poll_interval_hours = data.get("poll_interval_hours", 24)
    username = get_current_user()

    if not url or not is_playlist_url(url):
        return jsonify({"status": "error", "message": "Invalid playlist URL"}), 400

    # Extract playlist name and platform
    platform, name, tracks = extract_playlist_tracks(url)
    if not name or name.startswith("Unknown"):
        name = url

    entry = add_monitored_playlist(
        url=url, name=name, platform=platform,
        username=username, poll_interval_hours=poll_interval_hours,
    )
    entry["last_track_count"] = len(tracks)
    from playlist_monitor import _save_playlists, _load_playlists
    all_pl = _load_playlists()
    for p in all_pl:
        if p["id"] == entry["id"]:
            p["last_track_count"] = len(tracks)
    _save_playlists(all_pl)

    return jsonify({"status": "success", "playlist": entry})


@app.route('/api/monitored_playlists/<playlist_id>', methods=['PUT'])
@login_required
def api_update_monitored_playlist(playlist_id):
    data = request.get_json()
    updated = update_monitored_playlist(playlist_id, data)
    if updated:
        return jsonify({"status": "success", "playlist": updated})
    return jsonify({"status": "error", "message": "Playlist not found"}), 404


@app.route('/api/monitored_playlists/<playlist_id>', methods=['DELETE'])
@login_required
def api_remove_monitored_playlist(playlist_id):
    if remove_monitored_playlist(playlist_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Playlist not found"}), 404


@app.route('/api/monitored_playlists/<playlist_id>/sync', methods=['POST'])
@login_required
def api_sync_monitored_playlist(playlist_id):
    playlists = get_monitored_playlists()
    entry = next((p for p in playlists if p["id"] == playlist_id), None)
    if not entry:
        return jsonify({"status": "error", "message": "Playlist not found"}), 404

    download_id = str(uuid.uuid4())
    downloads_queue[download_id] = {
        'id': download_id,
        'username': get_current_user(),
        'artist': 'Playlist Sync',
        'title': entry['name'],
        'status': 'in_progress',
        'start_time': datetime.now().isoformat(),
        'message': 'Syncing monitored playlist...',
        'current_track_count': 0,
        'total_track_count': None,
        'download_type': 'playlist',
        'tracks': [],
        'downloaded_count': 0,
        'skipped_count': 0,
        'failed_count': 0,
    }
    def _run_sync():
        from downloaders.playlist_downloader import extract_playlist_tracks as _ext
        track_count = None
        try:
            _, _, tracks = _ext(entry['url'])
            if tracks:
                track_count = len(tracks)
        except Exception:
            pass
        try:
            asyncio.run(
                download_playlist(
                    url=entry['url'],
                    username=entry['username'],
                    navidrome_api=navidrome_api_global,
                    download_id=download_id,
                    update_status_fn=update_download_status,
                )
            )
        except Exception as e:
            print(f"Error in manual sync for {entry['name']}: {e}", file=sys.stderr)
        mark_synced(entry['id'], track_count)

    threading.Thread(target=_run_sync, daemon=True).start()
    return jsonify({"status": "success", "message": f"Sync started for {entry['name']}"})


@app.route('/api/get_deezer_album_art', methods=['GET'])
@login_required
async def get_deezer_album_art():
    artist = request.args.get('artist')
    album_title = request.args.get('album_title')

    if not artist or not album_title:
        return jsonify({"status": "error", "message": "Artist and album_title are required"}), 400

    try:
        deezer_api = DeezerAPI()
        album_details = await deezer_api.get_deezer_album_art(artist, album_title)
        if album_details and album_details.get('album_art'):
            return jsonify({"status": "success", "album_art_url": album_details['album_art']})
        else:
            return jsonify({"status": "info", "message": "Deezer album art not found"}), 404
    except Exception as e:
        print(f"Error getting Deezer album art for {artist} - {album_title}: {e}")
        return jsonify({"status": "error", "message": f"Error getting Deeezer album art: {e}"}), 500

@app.route('/api/create_smart_playlists', methods=['POST'])
@login_required
def create_smart_playlists():
    """
    Create Navidrome Smart Playlist (.nsp) files for enabled recommendation types.
    These files will be automatically detected by Navidrome and appear as playlists.
    Only creates playlists for services that are enabled in the configuration.
    """
    try:
        # Get the music library path from config
        music_library_path = MUSIC_LIBRARY_PATH

        # Check if music library path is configured
        if not music_library_path or music_library_path == "/path/to/music":
            return jsonify({
                "status": "error",
                "message": "Music library path is not properly configured. Please set MUSIC_LIBRARY_PATH in config.py."
            }), 400

        # Ensure the music library directory exists
        if not os.path.exists(music_library_path):
            return jsonify({
                "status": "error",
                "message": f"Music library path does not exist: {music_library_path}"
            }), 400

        # Define the smart playlist templates based on comment strings from config
        # Only include playlists for enabled services
        playlist_templates = []

        # Add ListenBrainz playlist if enabled
        if LISTENBRAINZ_ENABLED:
            playlist_templates.append({
                "filename": "lb.nsp",
                "name": "ListenBrainz Weekly",
                "comment": "Tracks where comment is lb_recommendation",
                "comment_value": TARGET_COMMENT,
                "source": "ListenBrainz"
            })

        # Add Last.fm playlist if enabled
        if LASTFM_ENABLED:
            playlist_templates.append({
                "filename": "lastfm.nsp",
                "name": "Last.fm Weekly",
                "comment": "Tracks where comment is lastfm_recommendation",
                "comment_value": LASTFM_TARGET_COMMENT,
                "source": "Last.fm"
            })

        # Add LLM playlist if enabled
        if LLM_ENABLED:
            playlist_templates.append({
                "filename": "llm.nsp",
                "name": "LLM Weekly",
                "comment": "Tracks where comment is llm_recommendation",
                "comment_value": LLM_TARGET_COMMENT,
                "source": "LLM"
            })

        # Add Album Recommendations playlist if album recommendations are enabled
        if ALBUM_RECOMMENDATION_ENABLED:
            playlist_templates.append({
                "filename": "album.nsp",
                "name": "Album Weekly",
                "comment": "Tracks where comment is album_recommendation",
                "comment_value": ALBUM_RECOMMENDATION_COMMENT,
                "source": "Album Recommendations"
            })

        # Check if any playlists are configured to be created
        if not playlist_templates:
            return jsonify({
                "status": "info",
                "message": "No recommendation sources are enabled in the configuration. Please enable ListenBrainz, Last.fm, LLM, or Album Recommendations in the settings to create smart playlists."
            })

        created_files = []
        failed_files = []

        for template in playlist_templates:
            try:
                # Create the NSP file content
                nsp_content = {
                    "name": template["name"],
                    "comment": template["comment"],
                    "all": [
                        {
                            "is": {
                                "comment": template["comment_value"]
                            }
                        }
                    ],
                    "sort": "title",
                    "order": "asc",
                    "limit": 10000
                }

                # Write the NSP file to the music library
                file_path = os.path.join(music_library_path, template["filename"])

                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(nsp_content, f, indent=2)

                created_files.append(template["filename"])
                print(f"Created smart playlist file: {file_path}")

            except Exception as e:
                failed_files.append({
                    "filename": template["filename"],
                    "error": str(e),
                    "source": template["source"]
                })
                print(f"Failed to create smart playlist file {template['filename']}: {e}")

        if created_files:
            message = f"Successfully created {len(created_files)} smart playlist files: {', '.join(created_files)}"
            if failed_files:
                message += f" | Failed to create {len(failed_files)} files: {', '.join([f['filename'] for f in failed_files])}"
            return jsonify({
                "status": "success",
                "message": message,
                "created_files": created_files,
                "failed_files": failed_files
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to create any smart playlist files",
                "failed_files": failed_files
            }), 500

    except Exception as e:
        print(f"Error creating smart playlists: {e}")
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": f"An unexpected error occurred while creating smart playlists: {e}"
        }), 500

# --- Global Error Handler ---
@app.errorhandler(Exception)
def handle_exception(e):
    print(f"Unhandled exception: {e}", file=sys.stderr)
    return jsonify({"status": "error", "message": "An unexpected error occurred.", "details": str(e)}), 500

async def download_llm_recommendations_background(recommendations, download_id):
    """Helper function to download tracks from LLM recommendations in the background."""
    tagger = Tagger(album_recommendation_comment=ALBUM_RECOMMENDATION_COMMENT)
    track_downloader = TrackDownloader(tagger)

    total_tracks = len(recommendations)
    downloaded_count = 0
    failed_count = 0
    skipped_count = 0
    track_statuses = [
        {"artist": s.get("artist", "Unknown"), "title": s.get("title", "Unknown"), "status": "pending", "message": ""}
        for s in recommendations
    ]

    def _update_llm(status, message):
        update_download_status(
            download_id, status, message,
            current_track_count=downloaded_count,
            total_track_count=total_tracks,
            tracks=track_statuses,
            downloaded_count=downloaded_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            download_type="playlist",
        )

    _update_llm("in_progress", f"Starting download of {total_tracks} tracks.")

    for i, song in enumerate(recommendations):
        label = f"{song.get('artist', 'Unknown')} - {song.get('title', 'Unknown')}"
        track_statuses[i]["status"] = "in_progress"
        track_statuses[i]["message"] = "Searching Deezer..."
        _update_llm("in_progress", f"Downloading {i+1}/{total_tracks}: {label}")

        song['source'] = 'LLM'
        song['recording_mbid'] = '' # Not available from LLM
        song['release_date'] = '' # Not available from LLM

        downloaded_path = await track_downloader.download_track(song, navidrome_api=navidrome_api_global)

        if downloaded_path:
            downloaded_count += 1
            track_statuses[i]["status"] = "completed"
            track_statuses[i]["message"] = "Downloaded"
            _update_llm("in_progress", f"Downloaded {i+1}/{total_tracks}: {label}")
        elif song.get('_duplicate'):
            skipped_count += 1
            track_statuses[i]["status"] = "skipped"
            track_statuses[i]["message"] = "Already in library"
            _update_llm("in_progress", f"Skipped {i+1}/{total_tracks}: {label} (already in library)")
        else:
            failed_count += 1
            track_statuses[i]["status"] = "failed"
            track_statuses[i]["message"] = "Not found on Deezer"
            print(f"Failed to download LLM recommendation: {label}")

    # Organize files after all downloads are attempted
    navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)

    parts = [f"{downloaded_count} downloaded"]
    if skipped_count:
        parts.append(f"{skipped_count} already in library")
    if failed_count:
        parts.append(f"{failed_count} failed")
    _update_llm("completed", ", ".join(parts) + ".")

if __name__ == '__main__':
    download_poller_thread = threading.Thread(target=poll_download_statuses, daemon=True)
    download_poller_thread.start()

    # Start playlist monitoring scheduler
    start_scheduler(navidrome_api_global, update_download_status, downloads_queue)

    app.run(host='0.0.0.0', port=5000, debug=True)

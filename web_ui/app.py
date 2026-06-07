
from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import subprocess
import re
import asyncio
import sys
import traceback
import time
import threading
import json
import queue as queue_module
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *
from apis.lastfm_api import LastFmAPI
from utils import initialize_streamrip_db, DeezerAuthError
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from apis.deezer_api import DeezerAPI
from apis.llm_api import LlmAPI
from downloaders.track_downloader import TrackDownloader
from downloaders.link_downloader import LinkDownloader
from utils import Tagger
import uuid

app = Flask(__name__)

CRON_FILE = '/etc/cron.d/re-command-cron'
DEFAULT_CRON_SCHEDULE = "0 0 * * 2"
CRON_COMMAND = (
    "root cd /app && ( /usr/local/bin/python3 /app/re-command.py --cleanup "
    "&& /usr/local/bin/python3 /app/re-command.py ) >> /proc/1/fd/1 2>&1"
)

# Global dictionary to store download queue status
# Key: download_id (UUID), Value: { 'artist', 'title', 'status', 'start_time', 'message' }
downloads_queue = {}
BATCH_DOWNLOAD_LOCK = "/tmp/recommand_batch_download.lock"

# Queue manager state
_download_manager = None
_current_download_id = None
_cancel_event = threading.Event()

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
    llm_enabled=LLM_ENABLED
)
deezer_api_global = DeezerAPI()
link_downloader_global = LinkDownloader(tagger_global, navidrome_api_global, deezer_api_global)
soulseek_queue = None

# --- Helper Functions ---
def write_cron_file(schedule):
    with open(CRON_FILE, 'w', newline='\n') as f:
        f.write(f"{schedule} {CRON_COMMAND}\n")
    os.chmod(CRON_FILE, 0o644)

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
        with open(CRON_FILE, 'r') as f:
            cron_line = f.read().strip()
        # Extract the schedule part (e.g., "0 0 * * 2")
        match = re.match(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+.*", cron_line)
        if match:
            return match.group(1)
    except FileNotFoundError:
        return DEFAULT_CRON_SCHEDULE
    return DEFAULT_CRON_SCHEDULE

def update_cron_schedule(new_schedule):
    try:
        write_cron_file(new_schedule)
        return True
    except Exception as e:
        print(f"Error updating cron schedule: {e}")
        return False
    return False

# --- Helper to update download status (used by background tasks, or simulated) ---
def update_download_status(download_id, status, message=None, title=None, current_track=None, current_track_count=None, total_track_count=None, parent_download_id=None):
    if download_id in downloads_queue:
        item = downloads_queue[download_id]
        item['status'] = status
        if message is not None:
            item['message'] = message
        if title is not None:
            item['title'] = title
        if current_track is not None:
            item['current_track'] = current_track
        if current_track_count is not None:
            item['current_track_count'] = current_track_count
        if total_track_count is not None:
            item['total_track_count'] = total_track_count
        if parent_download_id is not None:
            item['parent_download_id'] = parent_download_id
    else:
        print(f"Download ID {download_id} not found in queue.")
        print(f"Download ID {download_id} not in memory queue. Creating new entry from status file.")
        downloads_queue[download_id] = {
            'id': download_id,
            'artist': 'Playlist Download',
            'title': title or f'Download {download_id[:8]}...',
            'status': status,
            'start_time': datetime.now().isoformat(),
            'message': message,
            'current_track': current_track or '',
            'current_track_count': current_track_count,
            'total_track_count': total_track_count,
            'parent_download_id': parent_download_id
        }

DOWNLOAD_STATUS_DIR = "/tmp/recommand_download_status"
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
                            current_track = status_data.get('current_track')
                            current_track_count = status_data.get('current_track_count')
                            total_track_count = status_data.get('total_track_count')
                            parent_download_id = status_data.get('parent_download_id')
                            timestamp = datetime.fromisoformat(status_data.get('timestamp'))

                            # Check if an update to the in-memory queue is needed
                            needs_update = False
                            if download_id not in downloads_queue:
                                needs_update = True
                            else:
                                current_item = downloads_queue[download_id]
                                if current_item['status'] != status or \
                                   (title and current_item.get('title') != title) or \
                                   (current_track and current_item.get('current_track') != current_track) or \
                                   (message and current_item.get('message') != message) or \
                                   (current_track_count is not None and current_item.get('current_track_count') != current_track_count) or \
                                   (total_track_count is not None and current_item.get('total_track_count') != total_track_count):
                                    needs_update = True

                            if needs_update:
                                print(f"Polling: Found update for {download_id}. New status: {status}, New title: {title}")
                                update_download_status(download_id, status, message, title, current_track, current_track_count, total_track_count, parent_download_id)

                            # Cleanup completed/failed entries and their files after an interval
                            if status in ['completed', 'failed']:
                                # Convert start_time to datetime object for comparison
                                item_start_time = datetime.fromisoformat(downloads_queue[download_id]['start_time'])
                                if (datetime.now() - item_start_time).total_seconds() > DOWNLOAD_QUEUE_CLEANUP_INTERVAL_SECONDS:
                                    print(f"Cleaning up old download entry {download_id} (status: {status}).")
                                    del downloads_queue[download_id]
                                    os.remove(filepath)
                                    print(f"Removed status file {filepath}.")

                            # Check for .cancel flag
                            cancel_file = os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.cancel")
                            if os.path.exists(cancel_file) and status == 'in_progress':
                                status_data['status'] = 'failed'
                                status_data['message'] = 'Cancelled by user'
                                with open(filepath, 'w') as f:
                                    json.dump(status_data, f)
                                print(f"Polling: Detected cancel for {download_id}")
                                needs_update = True

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

# Start poller thread at module level (works under gunicorn)
_download_poller = threading.Thread(target=poll_download_statuses, daemon=True)
_download_poller.start()

# --- Routes ---
@app.route('/api/download_queue', methods=['GET'])
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
                    current_track = status_data.get('current_track')
                    current_track_count = status_data.get('current_track_count')
                    total_track_count = status_data.get('total_track_count')
                    parent_download_id = status_data.get('parent_download_id')
                    update_download_status(download_id, status, message, title, current_track, current_track_count, total_track_count, parent_download_id)
                except Exception as e:
                    print(f"Error processing status file {filepath} in /api/download_queue: {e}")

    # Filter out children (items with parent_download_id)
    queue_list = [v for v in downloads_queue.values() if not v.get('parent_download_id')]
    active_download_count = sum(1 for v in queue_list if v.get('status') == 'in_progress')
    return jsonify({"status": "success", "queue": queue_list, "active_download_count": active_download_count})

@app.route('/')
def index():
    current_arl = DEEZER_ARL
    current_cron = get_current_cron_schedule()

    # Parse cron schedule to extract hour and day
    cron_parts = current_cron.split()
    if len(cron_parts) >= 5:
        try:
            cron_hour = int(cron_parts[1])
            cron_day = int(cron_parts[4])
        except (ValueError, IndexError):
            cron_hour = 0
            cron_day = 2
    else:
        cron_hour = 0
        cron_day = 2

    return render_template('index.html', arl=current_arl, cron_schedule=current_cron, cron_hour=cron_hour, cron_day=cron_day)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'assets'), 'favicon.png', mimetype='image/png')

@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(app.root_path, 'assets'), filename)

@app.route('/api/config', methods=['GET'])
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
        "SOULSEEK_USERNAME": SOULSEEK_USERNAME,
        "SOULSEEK_PASSWORD": "••••••••" if SOULSEEK_PASSWORD else "",
        "SOULSEEK_SEARCH_TIMEOUT": SOULSEEK_SEARCH_TIMEOUT,
        "SOULSEEK_KEEP_ALIVE": SOULSEEK_KEEP_ALIVE,
        "SOULSEEK_MIN_QUALITY": SOULSEEK_MIN_QUALITY,
        "SOULSEEK_SHARE_MUSIC": SOULSEEK_SHARE_MUSIC,
        "ALBUM_RECOMMENDATION_ENABLED": ALBUM_RECOMMENDATION_ENABLED,
        "HIDE_DOWNLOAD_FROM_LINK": HIDE_DOWNLOAD_FROM_LINK,
        "HIDE_FRESH_RELEASES": HIDE_FRESH_RELEASES,
        "LLM_ENABLED": LLM_ENABLED,
        "LLM_PROVIDER": LLM_PROVIDER,
        "LLM_API_KEY": "••••••••" if LLM_API_KEY else "",
        "LLM_MODEL_NAME": globals().get("LLM_MODEL_NAME", ""),
        "LLM_BASE_URL": globals().get("LLM_BASE_URL", ""),
        "CRON_SCHEDULE": get_current_cron_schedule()
    })

@app.route('/api/update_arl', methods=['POST'])
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
def update_cron():
    data = request.get_json()
    new_schedule = data.get('schedule')
    if not new_schedule:
        return jsonify({"status": "error", "message": "Cron schedule is required"}), 400

    if update_cron_schedule(new_schedule):
        return jsonify({"status": "success", "message": "Cron schedule updated successfully."})
    else:
        return jsonify({"status": "error", "message": "Failed to update cron schedule."}), 500

@app.route('/api/update_config', methods=['POST'])
def update_config():
    data = request.get_json()
    try:
        # Read current config.py content
        with open('config.py', 'r') as f:
            current_config_content = f.read()

        # Define sensitive fields that should not be overwritten if masked
        sensitive_fields = {'ROOT_ND', 'PASSWORD_ND', 'TOKEN_LB', 'LASTFM_API_KEY', 'LASTFM_API_SECRET', 'LASTFM_SESSION_KEY', 'DEEZER_ARL', 'LLM_API_KEY', 'SOULSEEK_PASSWORD'}

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
            if key in {'LISTENBRAINZ_ENABLED', 'LASTFM_ENABLED', 'ALBUM_RECOMMENDATION_ENABLED', 'HIDE_DOWNLOAD_FROM_LINK', 'HIDE_FRESH_RELEASES', 'LLM_ENABLED', 'SOULSEEK_KEEP_ALIVE', 'SOULSEEK_SHARE_MUSIC'}:
                # Ensure boolean values are written as True/False (Python literal)
                new_value_str_for_file = str(value) 
            elif key in ('DOWNLOAD_METHOD', 'LLM_PROVIDER'):
                new_value_str_for_file = f'"{value}"'
            elif key in {'SOULSEEK_SEARCH_TIMEOUT', 'SOULSEEK_MIN_QUALITY', 'FRESH_RELEASES_DAYS', 'FRESH_RELEASES_CACHE_DURATION', 'DEEZER_MAX_CONCURRENT_REQUESTS'}:
                new_value_str_for_file = str(int(value))
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
            llm_enabled=globals().get('LLM_ENABLED', False)
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
def trigger_listenbrainz_download():
    print("Attempting to trigger ListenBrainz download...")

    try:
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        recs = asyncio.run(listenbrainz_api.get_listenbrainz_recommendations())
        if not recs:
            return jsonify({"status": "error", "message": "No ListenBrainz recommendations found. Please check your credentials and try again."}), 400

        download_id = str(uuid.uuid4())
        total = len(recs)
        is_soulseek = DOWNLOAD_METHOD == "soulseek" and SOULSEEK_USERNAME
        artist_label = 'ListenBrainz Playlist'
        title_label = f'{total} Tracks'

        downloads_queue[download_id] = {
            'id': download_id,
            'artist': artist_label,
            'title': title_label,
            'status': 'queued',
            'start_time': datetime.now().isoformat(),
            'message': f'Waiting to download {total} tracks...',
            'current_track': '',
            'current_track_count': 0,
            'total_track_count': total,
            '_type': 'playlist_soulseek' if is_soulseek else 'playlist_deezer',
            'source': 'listenbrainz',
        }

        if is_soulseek:
            downloads_queue[download_id]['_tracks'] = [
                {'artist': r['artist'], 'title': r['title'],
                 'album': r.get('album', ''), 'lb_recommendation': True}
                for r in recs
            ]
            os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
            with open(os.path.join(DOWNLOAD_STATUS_DIR, f'{download_id}.json'), 'w') as f:
                json.dump(downloads_queue[download_id], f)

        _get_manager().enqueue(download_id)

        return jsonify({"status": "success", "message": f"Queued {total} tracks for download."})
    except Exception as e:
        print(f"Error triggering ListenBrainz download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering ListenBrainz download: {e}"}), 500

@app.route('/api/get_lastfm_playlist', methods=['GET'])
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
def trigger_lastfm_download():
    print("Attempting to trigger Last.fm download...")

    try:
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)
        recs = asyncio.run(lastfm_api.get_lastfm_recommendations())
        if not recs:
            return jsonify({"status": "error", "message": "No Last.fm recommendations found. Please check your credentials and try again."}), 400

        download_id = str(uuid.uuid4())
        total = len(recs)
        is_soulseek = DOWNLOAD_METHOD == "soulseek" and SOULSEEK_USERNAME
        artist_label = 'Last.fm Playlist'
        title_label = f'{total} Tracks'

        downloads_queue[download_id] = {
            'id': download_id,
            'artist': artist_label,
            'title': title_label,
            'status': 'queued',
            'start_time': datetime.now().isoformat(),
            'message': f'Waiting to download {total} tracks...',
            'current_track': '',
            'current_track_count': 0,
            'total_track_count': total,
            '_type': 'playlist_soulseek' if is_soulseek else 'playlist_deezer',
            'source': 'lastfm',
        }

        if is_soulseek:
            downloads_queue[download_id]['_tracks'] = [
                {'artist': r['artist'], 'title': r['title'],
                 'album': r.get('album', ''), 'lb_recommendation': False}
                for r in recs
            ]
            os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
            with open(os.path.join(DOWNLOAD_STATUS_DIR, f'{download_id}.json'), 'w') as f:
                json.dump(downloads_queue[download_id], f)

        _get_manager().enqueue(download_id)

        return jsonify({"status": "success", "message": f"Queued {total} tracks for download."})
    except Exception as e:
        print(f"Error triggering Last.fm download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering Last.fm download: {e}"}), 500

@app.route('/api/trigger_navidrome_cleanup', methods=['POST'])
def trigger_navidrome_cleanup():
    print("Attempting to trigger Navidrome cleanup...")
    try:
        # Initialize API instances for cleanup
        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        lastfm_api = LastFmAPI(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD, LASTFM_SESSION_KEY, LASTFM_ENABLED)

        import asyncio
        # Use the global navidrome_api_global instance
        asyncio.run(navidrome_api_global.process_navidrome_library(listenbrainz_api=listenbrainz_api, lastfm_api=lastfm_api))
        return jsonify({"status": "success", "message": "Navidrome cleanup completed successfully."})
    except Exception as e:
        print(f"Error triggering Navidrome cleanup: {e}")
        return jsonify({"status": "error", "message": f"Error during Navidrome cleanup: {e}"}), 500

@app.route('/api/get_fresh_releases', methods=['GET'])
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
        data = await listenbrainz_api.get_fresh_releases(days=FRESH_RELEASES_DAYS)
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

@app.route('/api/toggle_cron', methods=['POST'])
def toggle_cron():
    data = request.get_json()
    disabled = data.get('disabled', False)
    try:
        if disabled:
            if os.path.exists(CRON_FILE):
                os.remove(CRON_FILE)
            return jsonify({"status": "success", "message": "Automatic downloads disabled."})
        else:
            # If cron is being enabled
            if not os.path.exists(CRON_FILE):
                # Create with default schedule if it doesn't exist
                write_cron_file(DEFAULT_CRON_SCHEDULE)
                return jsonify({"status": "success", "message": "Automatic downloads re-enabled with default schedule."})
            else:
                # If file already exists, cron is already considered enabled, just return success
                return jsonify({"status": "success", "message": "Automatic downloads already enabled."})
    except Exception as e:
        traceback.print_exc() # Debugging traceback
        return jsonify({"status": "error", "message": f"Error toggling cron: {e}"}), 500

@app.route('/api/submit_listenbrainz_feedback', methods=['POST'])
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
def trigger_llm_download():
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
    total = len(recommendations)
    is_soulseek = DOWNLOAD_METHOD == "soulseek" and SOULSEEK_USERNAME

    downloads_queue[download_id] = {
        'id': download_id,
        'artist': 'LLM Playlist',
        'title': f'{total} Tracks',
        'status': 'queued',
        'start_time': datetime.now().isoformat(),
        'message': f'Waiting to download {total} tracks...',
        'current_track': '',
        'current_track_count': 0,
        'total_track_count': total,
        '_type': 'playlist_soulseek' if is_soulseek else 'playlist_deezer',
        'source': 'llm',
    }

    if is_soulseek:
        downloads_queue[download_id]['_tracks'] = [
            {'artist': r['artist'], 'title': r['title'],
             'album': r.get('album', ''), 'lb_recommendation': False}
            for r in recommendations
        ]
        os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
        with open(os.path.join(DOWNLOAD_STATUS_DIR, f'{download_id}.json'), 'w') as f:
            json.dump(downloads_queue[download_id], f)

    _get_manager().enqueue(download_id)

    return jsonify({"status": "success", "message": f"Queued {total} tracks for download."})

@app.route('/api/trigger_fresh_release_download', methods=['POST'])
def trigger_fresh_release_download():
    print("Attempting to trigger fresh release album download...")
    try:
        data = request.get_json()
        artist = data.get('artist')
        album = data.get('album')
        release_date = data.get('release_date')

        if not artist or not album:
            return jsonify({"status": "error", "message": "Artist and album are required"}), 400

        download_id = str(uuid.uuid4())
        is_soulseek = DOWNLOAD_METHOD == "soulseek" and SOULSEEK_USERNAME

        if is_soulseek:
            deezer_api = DeezerAPI()
            deezer_link, deezer_data = asyncio.run(deezer_api.get_deezer_album_link(artist, album))
            if not deezer_link:
                return jsonify({"status": "error", "message": "Could not find album on Deezer."}), 400
            album_id = deezer_link.split('/')[-1]
            deezer_tracks = asyncio.run(deezer_api.get_deezer_album_tracks(album_id))
            if not deezer_tracks:
                return jsonify({"status": "error", "message": "Could not fetch track list for album."}), 400
            total = len(deezer_tracks)
            downloads_queue[download_id] = {
                'id': download_id, 'artist': artist, 'title': album,
                'status': 'queued', 'start_time': datetime.now().isoformat(),
                'message': f'Waiting to download {total} tracks...',
                'current_track': '', 'current_track_count': 0, 'total_track_count': total,
                '_type': 'playlist_soulseek', 'source': 'Fresh Releases',
                '_tracks': [
                    {'artist': artist, 'title': t.get('title', 'Unknown'),
                     'album': album, 'lb_recommendation': False,
                     'release_date': release_date or ''}
                    for t in deezer_tracks
                ],
            }
            os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
            with open(os.path.join(DOWNLOAD_STATUS_DIR, f'{download_id}.json'), 'w') as f:
                json.dump(downloads_queue[download_id], f)
        else:
            downloads_queue[download_id] = {
                'id': download_id, 'artist': artist, 'title': album,
                'status': 'queued', 'start_time': datetime.now().isoformat(),
                'message': 'Waiting in queue...',
                'current_track': '', 'current_track_count': 0, 'total_track_count': 0,
                '_type': 'album_deezer', 'source': 'Fresh Releases',
                '_album_info': {
                    'artist': artist, 'album': album,
                    'release_date': release_date or '',
                },
            }

        _get_manager().enqueue(download_id)
        return jsonify({"status": "success", "message": f"Queued album {artist} - {album} for download."})
    except Exception as e:
        print(f"Error triggering fresh release download: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error triggering download: {e}"}), 500

@app.route('/api/get_track_preview', methods=['GET'])
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

def _get_soulseek_queue():
    global soulseek_queue
    if soulseek_queue is None:
        from downloaders.soulseek_queue import SoulseekQueueManager
        os.makedirs(TEMP_DOWNLOAD_FOLDER, exist_ok=True)
        soulseek_queue = SoulseekQueueManager(
            username=SOULSEEK_USERNAME,
            password=SOULSEEK_PASSWORD,
            download_dir=TEMP_DOWNLOAD_FOLDER,
            music_library_path=MUSIC_LIBRARY_PATH,
            search_timeout=SOULSEEK_SEARCH_TIMEOUT,
            keep_alive=SOULSEEK_KEEP_ALIVE,
            min_quality=SOULSEEK_MIN_QUALITY,
            share_music=SOULSEEK_SHARE_MUSIC,
        )
        soulseek_queue.start()
    return soulseek_queue

# --- Queue Manager (Background Worker) ---
class QueueManager:
    def __init__(self):
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        print("QueueManager worker started.")

    def stop(self):
        self._running = False

    def enqueue(self, download_id):
        """Mark an existing downloads_queue entry as queued and notify the worker."""
        if download_id in downloads_queue:
            downloads_queue[download_id]['status'] = 'queued'

    def cancel(self, download_id):
        """Cancel a download by ID. Aborts active transfers and cleans partial files."""
        global _current_download_id, _cancel_event
        if download_id == _current_download_id:
            _cancel_event.set()
        cancel_file = os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.cancel")
        os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
        with open(cancel_file, 'w') as f:
            f.write("cancelled")
        status_file = os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.json")
        if os.path.exists(status_file):
            try:
                with open(status_file) as f:
                    data = json.load(f)
                data['status'] = 'failed'
                data['message'] = 'Cancelled by user'
                with open(status_file, 'w') as f:
                    json.dump(data, f)
            except Exception:
                pass
        update_download_status(download_id, "failed", "Cancelled by user")
        return True

    def _worker_loop(self):
        while self._running:
            try:
                download_id = self._dequeue_next()
                if download_id is None:
                    time.sleep(2)
                    continue
                global _current_download_id, _cancel_event
                _current_download_id = download_id
                _cancel_event.clear()

                item = downloads_queue.get(download_id)
                if not item:
                    _current_download_id = None
                    continue

                if _cancel_event.is_set():
                    update_download_status(download_id, 'failed', 'Cancelled')
                    _current_download_id = None
                    continue

                download_type = item.get('_type', 'track_deezer')
                try:
                    if download_type == 'track_deezer':
                        self._process_track_deezer(download_id, item)
                    elif download_type == 'track_soulseek':
                        self._process_track_soulseek(download_id, item)
                    elif download_type == 'playlist_soulseek':
                        self._process_playlist_soulseek(download_id, item)
                    elif download_type == 'playlist_deezer':
                        self._process_playlist_deezer(download_id, item)
                    elif download_type == 'album_deezer':
                        self._process_album_deezer(download_id, item)
                    elif download_type == 'link_download':
                        self._process_link(download_id, item)
                except Exception as e:
                    if not _cancel_event.is_set():
                        update_download_status(download_id, 'failed', str(e))
                        print(f"QueueManager: Error processing {download_id}: {e}")
                        traceback.print_exc()

                _current_download_id = None
            except Exception as e:
                print(f"QueueManager worker error: {e}")
                time.sleep(2)

    def _dequeue_next(self):
        """Find the oldest queued item and set it to in_progress."""
        queued = []
        for k, v in downloads_queue.items():
            if v.get('status') == 'queued':
                try:
                    start_time = datetime.fromisoformat(v['start_time'])
                except Exception:
                    start_time = datetime.min
                queued.append((start_time, k))
        if not queued:
            return None
        queued.sort(key=lambda x: x[0])
        download_id = queued[0][1]
        downloads_queue[download_id]['status'] = 'in_progress'
        self._write_status_file(download_id, 'in_progress', 
                                downloads_queue[download_id].get('message', 'Processing...'))
        return download_id

    def _poll_status_file(self, download_id, interval=2):
        """Poll a status file until it reaches a terminal state or cancel is set."""
        while self._running and not _cancel_event.is_set():
            status_file = os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.json")
            if os.path.exists(status_file):
                try:
                    with open(status_file) as f:
                        data = json.load(f)
                    status = data.get('status')
                    if status in ('completed', 'failed'):
                        update_download_status(
                            download_id, status, data.get('message'),
                            current_track=data.get('current_track'),
                            current_track_count=data.get('current_track_count'),
                            total_track_count=data.get('total_track_count'),
                        )
                        return status == 'completed'
                except Exception:
                    pass
            time.sleep(interval)
        return False

    def _process_track_deezer(self, download_id, item):
        if _cancel_event.is_set():
            return
        update_download_status(download_id, 'in_progress', 'Download initiated.')
        self._spawn_deezer_worker({
            'mode': 'track',
            'download_id': download_id,
            'artist': item['artist'],
            'title': item['title'],
            'album': '',
            'release_date': '',
            'recording_mbid': '',
            'source': item.get('source', 'Manual'),
            'lb_recommendation': item.get('lb_recommendation', False),
            'album_recommendation_comment': ALBUM_RECOMMENDATION_COMMENT,
            'status_dir': DOWNLOAD_STATUS_DIR,
            'music_library_path': MUSIC_LIBRARY_PATH,
            'temp_download_folder': TEMP_DOWNLOAD_FOLDER,
        }, download_id)

    def _spawn_deezer_worker(self, request_data, download_id):
        """Run a Deezer track/album download in a subprocess (killable on cancel)."""
        req_file = os.path.join(TEMP_DOWNLOAD_FOLDER, f'_deezreq_{download_id}.json')
        os.makedirs(TEMP_DOWNLOAD_FOLDER, exist_ok=True)
        worker_script = os.path.join(os.path.dirname(__file__), 'deezer_worker.py')
        try:
            with open(req_file, 'w') as f:
                json.dump(request_data, f)

            proc = subprocess.Popen([sys.executable, worker_script, req_file])

            while self._running and not _cancel_event.is_set():
                ret = proc.poll()
                if ret is not None:
                    break
                time.sleep(0.5)

            if _cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()

            # Read result from status file
            status_file = os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.json")
            if os.path.exists(status_file):
                try:
                    with open(status_file) as f:
                        data = json.load(f)
                    s = data.get('status')
                    if s == 'completed':
                        update_download_status(download_id, 'completed',
                                               data.get('message', 'Done'))
                    elif s == 'failed':
                        update_download_status(download_id, 'failed',
                                               data.get('message', 'Failed'))
                except Exception:
                    pass
        finally:
            try:
                if os.path.exists(req_file):
                    os.remove(req_file)
            except Exception:
                pass

    def _write_status_file(self, download_id, status, message, **kw):
        os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
        data = {"status": status, "message": message, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        data.update(kw)
        with open(os.path.join(DOWNLOAD_STATUS_DIR, f"{download_id}.json"), 'w') as f:
            json.dump(data, f)

    def _process_track_soulseek(self, download_id, item):
        if _cancel_event.is_set():
            return
        queue = _get_soulseek_queue()
        self._write_status_file(download_id, 'in_progress', 'Queued to Soulseek...',
                                title=f"{item['artist']} - {item['title']}")
        update_download_status(download_id, 'in_progress', 'Queued to Soulseek...')
        queue.enqueue(
            download_id=download_id,
            artist=item['artist'],
            title=item['title'],
            source=item.get('source', 'Manual'),
            lb_recommendation=item.get('lb_recommendation', False),
        )
        self._poll_status_file(download_id)

    def _process_playlist_soulseek(self, download_id, item):
        if _cancel_event.is_set():
            return
        tracks = item.get('_tracks', [])
        total = len(tracks)
        source = item.get('source', 'Manual')
        update_download_status(
            download_id, 'in_progress',
            f'Starting download of {total} tracks...',
            current_track_count=0, total_track_count=total,
        )
        queue = _get_soulseek_queue()
        for track in tracks:
            if _cancel_event.is_set():
                return
            track_id = str(uuid.uuid4())
            queue.enqueue(
                download_id=track_id,
                artist=track['artist'],
                title=track['title'],
                source=source,
                lb_recommendation=track.get('lb_recommendation', False),
                album=track.get('album', ''),
                release_date=track.get('release_date', ''),
                parent_download_id=download_id,
                parent_total=total,
            )
        self._poll_status_file(download_id)

    def _process_playlist_deezer(self, download_id, item):
        if _cancel_event.is_set():
            return
        source = item.get('source', 'listenbrainz')

        if source == 'llm':
            self._process_llm_deezer(download_id, item)
            return

        update_download_status(download_id, 'in_progress', 'Starting subprocess...')
        bypass = ['--bypass-playlist-check'] if source == 'listenbrainz' else []
        cmd = [sys.executable, '/app/re-command.py', '--source', source] + bypass + ['--download-id', download_id]
        proc = subprocess.Popen(cmd)
        while self._running and not _cancel_event.is_set():
            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(1)
        if _cancel_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def _process_llm_deezer(self, download_id, item):
        """Download LLM recommendations inline via Deezer."""
        update_download_status(download_id, 'in_progress', 'Starting LLM download...')
        from downloaders.track_downloader import TrackDownloader
        from utils import Tagger

        tagger = Tagger(ALBUM_RECOMMENDATION_COMMENT)
        track_downloader = TrackDownloader(tagger)

        total_tracks = item.get('total_track_count', 0)
        downloaded_count = 0

        listenbrainz_api = ListenBrainzAPI(ROOT_LB, TOKEN_LB, USER_LB, LISTENBRAINZ_ENABLED)
        scrobbles = asyncio.run(listenbrainz_api.get_weekly_scrobbles())
        if not scrobbles:
            update_download_status(download_id, 'failed', 'No scrobbles found.')
            return

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
            update_download_status(download_id, 'failed', 'LLM returned no recommendations.')
            return

        total = len(recommendations)
        update_download_status(
            download_id, 'in_progress',
            f'Downloading 0/{total} tracks...',
            current_track_count=0, total_track_count=total,
        )

        for i, song in enumerate(recommendations):
            if _cancel_event.is_set():
                update_download_status(download_id, 'failed', 'Cancelled')
                return

            song['source'] = 'LLM'
            song['recording_mbid'] = ''
            song['release_date'] = ''
            song['download_id'] = download_id

            update_download_status(
                download_id, 'in_progress',
                f'Downloading track {i+1}/{total}: {song["artist"]} - {song["title"]}',
                current_track=song['artist'] + ' - ' + song['title'],
                current_track_count=downloaded_count,
                total_track_count=total,
            )

            try:
                downloaded_path = asyncio.run(track_downloader.download_track(song))
            except Exception as e:
                if 'DeezerAuthError' in str(type(e)):
                    update_download_status(download_id, 'failed', str(e))
                    return
                print(f"LLM Deezer: failed to download {song['artist']} - {song['title']}: {e}")
                continue

            if downloaded_path:
                downloaded_count += 1
                update_download_status(
                    download_id, 'in_progress',
                    f'Downloaded track {i+1}/{total}',
                    current_track_count=downloaded_count,
                )

        if not _cancel_event.is_set():
            navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)
            update_download_status(
                download_id, 'completed',
                f'Download complete. Processed {downloaded_count}/{total} tracks.',
                current_track_count=downloaded_count,
            )

    def _process_link(self, download_id, item):
        if _cancel_event.is_set():
            return
        update_download_status(download_id, 'in_progress', 'Processing link...')
        link = item.get('_link', '')
        lb_recommendation = item.get('lb_recommendation', False)
        try:
            result = asyncio.run(link_downloader_global.download_from_url(
                link, lb_recommendation=lb_recommendation, download_id=download_id))
        except Exception as e:
            if 'DeezerAuthError' in str(type(e)):
                update_download_status(download_id, 'failed', str(e))
            else:
                raise
            return
        if result and not _cancel_event.is_set():
            update_download_status(download_id, 'completed',
                                   f"Downloaded {len(result)} files.")
        elif not _cancel_event.is_set():
            update_download_status(download_id, 'failed',
                                   "No files downloaded. Track may not be available.")

    def _process_album_deezer(self, download_id, item):
        if _cancel_event.is_set():
            return
        album_info = item.get('_album_info', {})
        update_download_status(download_id, 'in_progress', 'Downloading album...')
        self._spawn_deezer_worker({
            'mode': 'album',
            'download_id': download_id,
            'artist': album_info.get('artist', ''),
            'album': album_info.get('album', ''),
            'release_date': album_info.get('release_date', ''),
            'is_album_recommendation': ALBUM_RECOMMENDATION_ENABLED,
            'album_recommendation_comment': ALBUM_RECOMMENDATION_COMMENT,
            'status_dir': DOWNLOAD_STATUS_DIR,
            'music_library_path': MUSIC_LIBRARY_PATH,
            'temp_download_folder': TEMP_DOWNLOAD_FOLDER,
        }, download_id)


def _get_manager():
    global _download_manager
    if _download_manager is None:
        _download_manager = QueueManager()
        _download_manager.start()
    return _download_manager


@app.route('/api/trigger_track_download', methods=['POST'])
def trigger_track_download():
    print("Attempting to trigger individual track download...")
    try:
        data = request.get_json()
        artist = data.get('artist')
        title = data.get('title')
        lb_recommendation = data.get('lb_recommendation', False)
        source = data.get('source', 'Manual')

        if not artist or not title:
            return jsonify({"status": "error", "message": "Artist and title are required"}), 400

        if DOWNLOAD_METHOD == "soulseek" and not SOULSEEK_USERNAME:
            return jsonify({"status": "error", "message": "Soulseek username not configured. Set it in Settings."}), 400

        download_id = str(uuid.uuid4())
        is_soulseek = DOWNLOAD_METHOD == "soulseek" and SOULSEEK_USERNAME

        downloads_queue[download_id] = {
            'id': download_id,
            'artist': artist,
            'title': title,
            'status': 'queued',
            'start_time': datetime.now().isoformat(),
            'message': 'Waiting in queue...',
            '_type': 'track_soulseek' if is_soulseek else 'track_deezer',
            'source': source,
            'lb_recommendation': lb_recommendation,
        }

        if is_soulseek:
            os.makedirs(DOWNLOAD_STATUS_DIR, exist_ok=True)
            with open(os.path.join(DOWNLOAD_STATUS_DIR, f'{download_id}.json'), 'w') as f:
                json.dump(downloads_queue[download_id], f)

        _get_manager().enqueue(download_id)

        return jsonify({
            "status": "success",
            "message": f"Track queued for download: {artist} - {title}.",
            "download_id": download_id
        })
    except Exception as e:
        print(f"Error triggering track download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering download: {e}"}), 500

@app.route('/api/download_from_link', methods=['POST'])
def download_from_link():
    print("Attempting to download from link...")
    try:
        data = request.get_json()
        link = data.get('link')
        lb_recommendation = data.get('lb_recommendation', False)

        if 'listenbrainz.org/playlist' in link.lower():
            lb_recommendation = True
            print(f"Detected ListenBrainz playlist URL, automatically setting lb_recommendation=True")

        if not link:
            return jsonify({"status": "error", "message": "Link is required"}), 400

        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'artist': 'Link Download',
            'title': link[:80] + ('...' if len(link) > 80 else ''),
            'status': 'queued',
            'start_time': datetime.now().isoformat(),
            'message': 'Waiting in queue...',
            '_type': 'link_download',
            '_link': link,
            'lb_recommendation': lb_recommendation,
        }

        _get_manager().enqueue(download_id)

        return jsonify({"status": "success", "message": f"Link queued for download.", "download_id": download_id})
    except Exception as e:
        print(f"Error downloading from link: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"status": "error", "message": f"Error queuing download: {e}"}), 500

@app.route('/api/get_deezer_album_art', methods=['GET'])
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
                "name": "ListenBrainz Recommendations",
                "comment": "Tracks where comment is lb_recommendation",
                "comment_value": TARGET_COMMENT,
                "source": "ListenBrainz"
            })

        # Add Last.fm playlist if enabled
        if LASTFM_ENABLED:
            playlist_templates.append({
                "filename": "lastfm.nsp",
                "name": "Last.fm Recommendations",
                "comment": "Tracks where comment is lastfm_recommendation",
                "comment_value": LASTFM_TARGET_COMMENT,
                "source": "Last.fm"
            })

        # Add LLM playlist if enabled
        if LLM_ENABLED:
            playlist_templates.append({
                "filename": "llm.nsp",
                "name": "LLM Recommendations",
                "comment": "Tracks where comment is llm_recommendation",
                "comment_value": LLM_TARGET_COMMENT,
                "source": "LLM"
            })

        # Add Album Recommendations playlist if album recommendations are enabled
        if ALBUM_RECOMMENDATION_ENABLED:
            playlist_templates.append({
                "filename": "album.nsp",
                "name": "Album Recommendations",
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

@app.route('/api/cancel_download', methods=['POST'])
def cancel_download():
    data = request.get_json()
    download_id = data.get('download_id')
    if not download_id:
        return jsonify({"status": "error", "message": "download_id is required"}), 400

    try:
        manager = _get_manager()
        manager.cancel(download_id)

        # Also cancel in soulseek queue manager if active
        global soulseek_queue
        if soulseek_queue:
            soulseek_queue.cancel(download_id)

        # Remove all files for this download and its children
        ids_to_clean = {download_id}
        if os.path.exists(DOWNLOAD_STATUS_DIR):
            for fname in os.listdir(DOWNLOAD_STATUS_DIR):
                if fname.endswith('.json'):
                    try:
                        with open(os.path.join(DOWNLOAD_STATUS_DIR, fname)) as f:
                            data = json.load(f)
                        if data.get('parent_download_id') == download_id:
                            ids_to_clean.add(fname[:-5])
                    except Exception:
                        pass

        for dl_id in ids_to_clean:
            for suffix in ['.json', '.cancel']:
                fpath = os.path.join(DOWNLOAD_STATUS_DIR, f"{dl_id}{suffix}")
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
            if dl_id in downloads_queue:
                del downloads_queue[dl_id]

        return jsonify({"status": "success", "message": "Download cancelled."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/flush_queue', methods=['POST'])
def flush_queue():
    return jsonify({"status": "success", "message": "OK"})


@app.route('/api/cleanup_queue', methods=['POST'])
def cleanup_queue():
    """Remove completed/failed/cancelled entries from the queue."""
    try:
        removed = 0
        terminal_ids = []
        for dl_id, item in list(downloads_queue.items()):
            if item.get('status') in ('completed', 'failed'):
                terminal_ids.append(dl_id)

        for dl_id in terminal_ids:
            if dl_id in downloads_queue:
                del downloads_queue[dl_id]
            for suffix in ['.json', '.cancel']:
                fpath = os.path.join(DOWNLOAD_STATUS_DIR, f"{dl_id}{suffix}")
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
            removed += 1

        return jsonify({"status": "success", "message": f"Cleaned up {removed} entries.", "removed": removed})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    download_poller_thread = threading.Thread(target=poll_download_statuses, daemon=True)
    download_poller_thread.start()

    app.run(host='0.0.0.0', port=5000, debug=True)

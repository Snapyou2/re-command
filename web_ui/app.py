
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
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *
from apis.lastfm_api import LastFmAPI
from utils import initialize_streamrip_db
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from apis.deezer_api import DeezerAPI
from apis.llm_api import LlmAPI
from downloaders.track_downloader import TrackDownloader
from downloaders.link_downloader import LinkDownloader
from utils import Tagger
import uuid

app = Flask(__name__)

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
    llm_enabled=LLM_ENABLED
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
        with open('/etc/cron.d/re-command-cron', 'r') as f:
            cron_line = f.read().strip()
        # Extract the schedule part (e.g., "0 0 * * 2")
        match = re.match(r"^(\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+.*", cron_line)
        if match:
            return match.group(1)
    except FileNotFoundError:
        return "0 0 * * 2"
    return "0 0 * * 2"

def update_cron_schedule(new_schedule):
    try:
        with open('/etc/cron.d/re-command-cron', 'r') as f:
            cron_line = f.read().strip()

        command_match = re.match(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(.*)", cron_line)
        if command_match:
            command_part = command_match.group(1)
            new_cron_line = f"{new_schedule} {command_part}"
            with open('/etc/cron.d/re-command-cron', 'w') as f:
                f.write(new_cron_line + '\n')

            subprocess.run(["crontab", "/etc/cron.d/re-command-cron"], check=True)
            return True
    except Exception as e:
        print(f"Error updating cron schedule: {e}")
        return False
    return False

# --- Helper to update download status (used by background tasks, or simulated) ---
def update_download_status(download_id, status, message=None, title=None, current_track_count=None, total_track_count=None):
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
    else:
        print(f"Download ID {download_id} not found in queue.")
        # This can happen if app.py restarts and finds an old status file
        print(f"Download ID {download_id} not in memory queue. Creating new entry from status file.")
        downloads_queue[download_id] = {
            'id': download_id,
            'artist': 'Playlist Download', # Generic placeholder
            'title': title or f'Download {download_id[:8]}...',
            'status': status,
            'start_time': datetime.now().isoformat(),
            'message': message,
            'current_track_count': current_track_count,
            'total_track_count': total_track_count
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
                                update_download_status(download_id, status, message, title, current_track_count, total_track_count)

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
                    current_track_count = status_data.get('current_track_count')
                    total_track_count = status_data.get('total_track_count')
                    update_download_status(download_id, status, message, title, current_track_count, total_track_count)
                except Exception as e:
                    print(f"Error processing status file {filepath} in /api/download_queue: {e}")

    # Filter out older completed/failed tasks to keep the queue clean
    # For now, let's keep everything, a cleanup mechanism can be added later
    queue_list = list(downloads_queue.values())
    return jsonify({"status": "success", "queue": queue_list})

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
            'artist': 'ListenBrainz Playlist',
            'title': 'Multiple Tracks',
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.',
            'current_track_count': 0,
            'total_track_count': None  # Will be updated when recommendations are fetched
        }
        
        # Execute re-command.py in a separate process for non-blocking download, bypassing playlist check
        subprocess.Popen([
            sys.executable, '/app/re-command.py', 
            '--source', 'listenbrainz', 
            '--bypass-playlist-check',
            '--download-id', download_id # Pass the download ID
        ])
        return jsonify({"status": "info", "message": "ListenBrainz download initiated in the background."})
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
            'artist': 'Last.fm Playlist',
            'title': 'Multiple Tracks',
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.',
            'current_track_count': 0,
            'total_track_count': None  # Will be updated when recommendations are fetched
        }
        
        # Execute re-command.py in a separate process for non-blocking download
        subprocess.Popen([
            sys.executable, '/app/re-command.py', 
            '--source', 'lastfm',
            '--download-id', download_id # Pass the download ID
        ])
        return jsonify({"status": "info", "message": "Last.fm download initiated in the background."})
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
        playlist_mode = globals().get('PLAYLIST_MODE', 'tags')
        if playlist_mode == 'api':
            download_history_path = globals().get('DOWNLOAD_HISTORY_PATH', '/app/download_history.json')
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

@app.route('/api/toggle_cron', methods=['POST'])
def toggle_cron():
    data = request.get_json()
    disabled = data.get('disabled', False)
    cron_file = '/etc/cron.d/re-command-cron'
    try:
        if disabled:
            if os.path.exists(cron_file):
                os.remove(cron_file)
                subprocess.run(["crontab", "/etc/cron.d/re-command-cron"], check=False)
            return jsonify({"status": "success", "message": "Automatic downloads disabled."})
        else:
            # If cron is being enabled
            if not os.path.exists(cron_file):
                # Create with default schedule if it doesn't exist
                default_schedule = "0 0 * * 2"
                default_command = "/usr/bin/python3 /app/re-command.py >> /var/log/re-command.log 2>&1"
                with open(cron_file, 'w') as f:
                    f.write(f"{default_schedule} {default_command}\n")
                os.chmod(cron_file, 0o644) # Set permissions
                subprocess.run(["crontab", cron_file], check=True)
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
        'artist': 'LLM Playlist',
        'title': f'{len(recommendations)} Tracks',
        'status': 'in_progress',
        'start_time': datetime.now().isoformat(),
        'message': 'Download initiated.',
        'current_track_count': 0,
        'total_track_count': len(recommendations)
    }

    # Execute downloads in a background thread
    threading.Thread(target=lambda: asyncio.run(download_llm_recommendations_background(recommendations, download_id))).start()

    return jsonify({"status": "info", "message": f"Started download of {len(recommendations)} tracks from LLM recommendations in the background."})

@app.route('/api/trigger_fresh_release_download', methods=['POST'])
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

        downloaded_path = asyncio.run(track_downloader.download_track(track_info, lb_recommendation=lb_recommendation))
        
        if downloaded_path:
            update_download_status(download_id, 'completed', "Download completed.")
            # Organize the downloaded files -> music library
            navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)
            return jsonify({"status": "success", "message": f"Successfully downloaded and organized track: {artist} - {title}."})
        else:
            update_download_status(download_id, 'failed', "Download failed. See logs for details.")
            return jsonify({"status": "error", "message": f"Failed to download track: {artist} - {title}."})

    except Exception as e:
        print(f"Error triggering track download: {e}")
        if 'download_id' in locals():
            update_download_status(download_id, 'failed', f"An error occurred: {e}")
        return jsonify({"status": "error", "message": f"Error triggering download: {e}"}), 500

@app.route('/api/download_from_link', methods=['POST'])
async def download_from_link():
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
        download_id = str(uuid.uuid4())
        downloads_queue[download_id] = {
            'id': download_id,
            'artist': 'Link Download',
            'title': link,
            'status': 'in_progress',
            'start_time': datetime.now().isoformat(),
            'message': 'Download initiated.'
        }
        
        # Use globally initialized link_downloader
        result = await link_downloader_global.download_from_url(link, lb_recommendation=lb_recommendation, download_id=download_id)

        if result:
            update_download_status(download_id, 'completed', f"Downloaded {len(result)} files.")
            return jsonify({"status": "success", "message": f"Successfully downloaded and organized {len(result)} files from {link}."})
        else:
            update_download_status(download_id, 'failed', f"No files downloaded from {link}. The track may not be available on Deezer.")
            return jsonify({"status": "info", "message": f"No files downloaded from {link}. The track may not be available on Deezer."})

    except Exception as e:
        print(f"Error downloading from link: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return jsonify({"status": "error", "message": f"Error initiating download from link: {e}"}), 500

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

async def download_llm_recommendations_background(recommendations, download_id):
    """Helper function to download tracks from LLM recommendations in the background."""
    tagger = Tagger(album_recommendation_comment=ALBUM_RECOMMENDATION_COMMENT)
    track_downloader = TrackDownloader(tagger)
    
    total_tracks = len(recommendations)
    downloaded_count = 0
    for i, song in enumerate(recommendations):
        update_download_status(
            download_id, 
            'in_progress', 
            f"Downloading track {i+1}/{total_tracks}: {song['artist']} - {song['title']}",
            current_track_count=downloaded_count,
            total_track_count=total_tracks
        )
        
        song['source'] = 'LLM'
        song['recording_mbid'] = '' # Not available from LLM
        song['release_date'] = '' # Not available from LLM
        
        downloaded_path = await track_downloader.download_track(song)
        
        if downloaded_path:
            downloaded_count += 1
            update_download_status(
                download_id,
                'in_progress',
                f"Downloaded track {i+1}/{total_tracks}",
                current_track_count=downloaded_count
            )
        else:
            print(f"Failed to download LLM recommendation: {song['artist']} - {song['title']}")

    # Organize files after all downloads are attempted
    navidrome_api_global.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)

    # Set final status
    update_download_status(
        download_id, 
        'completed', 
        f"Download complete. Processed {downloaded_count}/{total_tracks} tracks.",
        current_track_count=downloaded_count
    )

if __name__ == '__main__':
    download_poller_thread = threading.Thread(target=poll_download_statuses, daemon=True)
    download_poller_thread.start()

    app.run(host='0.0.0.0', port=5000, debug=True)

from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import subprocess
import re
import asyncio
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *
from apis.lastfm_api import LastFmAPI
from apis.listenbrainz_api import ListenBrainzAPI
from apis.navidrome_api import NavidromeAPI
from downloaders.track_downloader import TrackDownloader
from utils import Tagger

app = Flask(__name__)

# --- Helper Functions ---
def validate_deemix_arl(arl_to_validate):
    """
    Attempts to validate an ARL by running a deemix command in a subprocess.
    Returns True if deemix seems to accept the ARL, False otherwise.
    """
    try:
        # Create a temporary .arl file for deemix to use in portable mode
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
        
        # Set HOME environment variable for the subprocess to ensure deemix finds its config
        env = os.environ.copy()
        env['HOME'] = app.root_path 
        
        # Run deemix with a short timeout, as it hangs if ARL is bad
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
        return "0 0 * * 2" # Default: Tuesday at 00:00 if file not found
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

# --- Routes ---
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
            # Default to Tuesday 00:00 if parsing fails
            cron_hour = 0
            cron_day = 2
    else:
        cron_hour = 0
        cron_day = 2

    return render_template('index.html', arl=current_arl, cron_schedule=current_cron, cron_hour=cron_hour, cron_day=cron_day)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path), 'favicon.png', mimetype='image/vnd.microsoft.icon')

@app.route('/assets/<path:filename>')
def assets(filename):
    return send_from_directory(os.path.join(app.root_path, 'assets'), filename)

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        "DEEZER_ARL": DEEZER_ARL,
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

@app.route('/api/get_listenbrainz_playlist', methods=['GET'])
def get_listenbrainz_playlist():
    print("Attempting to get ListenBrainz playlist...")
    try:
        print("Creating ListenBrainzAPI instance...")
        listenbrainz_api = ListenBrainzAPI()
        print("Running async get_listenbrainz_recommendations...")
        lb_recs = asyncio.run(listenbrainz_api.get_listenbrainz_recommendations())
        print(f"ListenBrainz recommendations found: {len(lb_recs)}")
        if lb_recs:
            return jsonify({"status": "success", "recommendations": lb_recs})
        else:
            return jsonify({"status": "info", "message": "No new ListenBrainz recommendations found."})
    except Exception as e:
        import traceback
        print(f"Error getting ListenBrainz playlist: {e}")
        print("Traceback:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error getting ListenBrainz playlist: {e}"}), 500

@app.route('/api/get_album_art', methods=['GET'])
async def get_album_art():
    artist = request.args.get('artist')
    title = request.args.get('title')
    mbid = request.args.get('mbid')
    if not artist or not title:
        return jsonify({"status": "error", "message": "Artist and title are required"}), 400

    try:
        listenbrainz_api = ListenBrainzAPI()
        album_art_url = await listenbrainz_api._fetch_album_art_for_track(artist, title, mbid)
        return jsonify({"status": "success", "album_art_url": album_art_url})
    except Exception as e:
        print(f"Error fetching album art for {artist} - {title}: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": f"Error fetching album art: {e}"}), 500

@app.route('/api/trigger_listenbrainz_download', methods=['POST'])
def trigger_listenbrainz_download():
    print("Attempting to trigger ListenBrainz download via background script...")
    try:
        # Check if there are recommendations first
        listenbrainz_api = ListenBrainzAPI()
        recs = asyncio.run(listenbrainz_api.get_listenbrainz_recommendations())
        if not recs:
            return jsonify({"status": "error", "message": "No ListenBrainz recommendations found. Please check your credentials and try again."}), 400
        # Execute re-command.py in a separate process for non-blocking download, bypassing playlist check
        subprocess.Popen([sys.executable, '/app/re-command.py', '--source', 'listenbrainz', '--bypass-playlist-check'])
        return jsonify({"status": "info", "message": "ListenBrainz download initiated in the background."})
    except Exception as e:
        print(f"Error triggering ListenBrainz download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering ListenBrainz download: {e}"}), 500

@app.route('/api/get_lastfm_playlist', methods=['GET'])
def get_lastfm_playlist():
    print("Attempting to get Last.fm playlist...")
    try:
        lastfm_api = LastFmAPI()
        # Synchronous call
        lf_recs = lastfm_api.get_lastfm_recommendations()
        print(f"Last.fm recommendations found: {len(lf_recs)}")
        if lf_recs:
            return jsonify({"status": "success", "recommendations": lf_recs})
        else:
            return jsonify({"status": "info", "message": "No new Last.fm recommendations found."})
    except Exception as e:
        print(f"Error getting Last.fm playlist: {e}")
        return jsonify({"status": "error", "message": f"Error getting Last.fm playlist: {e}"}), 500

@app.route('/api/trigger_lastfm_download', methods=['POST'])
def trigger_lastfm_download():
    print("Attempting to trigger Last.fm download via background script...")
    try:
        # Check if there are recommendations first
        lastfm_api = LastFmAPI()
        recs = lastfm_api.get_lastfm_recommendations()
        if not recs:
            return jsonify({"status": "error", "message": "No Last.fm recommendations found. Please check your credentials and try again."}), 400
        # Execute re-command.py in a separate process for non-blocking download
        subprocess.Popen([sys.executable, '/app/re-command.py', '--source', 'lastfm'])
        return jsonify({"status": "info", "message": "Last.fm download initiated in the background."})
    except Exception as e:
        print(f"Error triggering Last.fm download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering Last.fm download: {e}"}), 500

@app.route('/api/trigger_navidrome_cleanup', methods=['POST'])
def trigger_navidrome_cleanup():
    print("Attempting to trigger Navidrome cleanup...")
    try:
        navidrome_api = NavidromeAPI()
        # Run the cleanup process (this might take some time)
        navidrome_api.process_navidrome_library()
        return jsonify({"status": "success", "message": "Navidrome cleanup completed successfully."})
    except Exception as e:
        print(f"Error triggering Navidrome cleanup: {e}")
        return jsonify({"status": "error", "message": f"Error during Navidrome cleanup: {e}"}), 500

@app.route('/api/get_fresh_releases', methods=['GET'])
async def get_fresh_releases():
    print("Attempting to get ListenBrainz fresh releases...")
    try:
        listenbrainz_api = ListenBrainzAPI()
        data = await listenbrainz_api.get_fresh_releases()
        releases = data.get('payload', {}).get('releases', [])

        print(f"ListenBrainz fresh releases found: {len(releases)}")
        if releases:
            return jsonify({"status": "success", "releases": releases})
        else:
            return jsonify({"status": "info", "message": "No fresh ListenBrainz releases found."})
    except Exception as e:
        import traceback
        print(f"Error getting ListenBrainz fresh releases: {e}")
        print("Traceback:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Error getting ListenBrainz fresh releases: {e}"}), 500

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
            return jsonify({"status": "info", "message": "Please update the cron schedule to re-enable automatic downloads."})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error toggling cron: {e}"}), 500

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

        from downloaders.album_downloader import AlbumDownloader
        from utils import Tagger

        tagger = Tagger()
        album_downloader = AlbumDownloader(tagger)

        album_info = {
            'artist': artist,
            'album': album,
            'release_date': release_date,
            'album_art': None
        }

        import asyncio
        result = asyncio.run(album_downloader.download_album(album_info))

        if result["status"] == "success":
            # Organize the downloaded files -> music library
            navidrome_api = NavidromeAPI()
            navidrome_api.organize_music_files(TEMP_DOWNLOAD_FOLDER, MUSIC_LIBRARY_PATH)
            return jsonify({"status": "success", "message": f"Successfully downloaded and organized album {artist} - {album} with {len(result['files'])} tracks."})
        else:
            return jsonify({"status": "error", "message": result["message"]})

    except Exception as e:
        print(f"Error triggering fresh release download: {e}")
        return jsonify({"status": "error", "message": f"Error triggering download: {e}"}), 500

# --- Global Error Handler ---
@app.errorhandler(Exception)
def handle_exception(e):
    # Log the error for debugging purposes
    print(f"Unhandled exception: {e}", file=sys.stderr)
    # Return a JSON response for all errors
    return jsonify({"status": "error", "message": "An unexpected error occurred.", "details": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

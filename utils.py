import os
import re
import subprocess
import requests
from mutagen.id3 import ID3, COMM, error

def get_last_playlist_name(playlist_history_file):
    """Retrieves the last playlist name from the history file."""
    try:
        with open(playlist_history_file, "r") as f:
            return f.readline().strip()
    except FileNotFoundError:
        return None

def save_playlist_name(playlist_history_file, playlist_name):
    """Saves the playlist name to the history file."""
    try:
        with open(playlist_history_file, "w") as f:
            f.write(playlist_name)
    except OSError as e:
        print(f"Error saving playlist name to file: {e}")

def sanitize_filename(filename):
    """Replaces problematic characters in filenames with underscores."""
    return re.sub(r'[\\/:*?"<>|]', '_', filename)

def remove_empty_folders(path):
    """Removes empty folders from a given path."""
    for root, dirs, files in os.walk(path, topdown=False):
        for dir in dirs:
            full_path = os.path.join(root, dir)
            if not os.listdir(full_path):
                try:
                    os.rmdir(full_path)
                except OSError as e:
                    print(f"Error removing folder: {full_path}. Error: {e}")

class Tagger:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.target_comment = self.config_manager.get("TARGET_COMMENT")
        self.lastfm_target_comment = self.config_manager.get("LASTFM_TARGET_COMMENT")

    def add_comment_to_file(self, mp3_file, comment):
        """Add a comment to a specific MP3 file."""
        try:
            id3v2_3 = ID3(mp3_file, translate=False, load_v1=False)
        except error.ID3NoHeaderError:
            id3v2_3 = ID3()

        id3v2_3.add(COMM(encoding=3, lang='eng', desc='', text=comment))
        id3v2_3.save(mp3_file, v2_version=3, v1=2)

    def tag_track(self, file_path, artist, title, album, release_date, recording_mbid, source):
        """Tags a track with metadata using kid3-cli."""
        comment = self.target_comment if source == "ListenBrainz" else self.lastfm_target_comment
        try:
            commands = [
                "-c", f"set artist \"{artist}\"",
                "-c", f"set title \"{title}\"",
                "-c", f"set album \"{album}\"",
                "-c", f"set date \"{release_date}\"",
                "-c", f"set comment \"{comment}\""
            ]
            if recording_mbid:
                commands.extend(["-c", f"set musicBrainzId \"{recording_mbid}\""])
            commands.append(file_path)

            subprocess.run(["kid3-cli"] + commands, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error tagging {file_path}: {e}")
        except FileNotFoundError:
            print(f"kid3-cli not found. Is it installed and in your PATH?")

    def get_album_art(self, album_id, salt, token):
        """Fetches album art from Navidrome."""
        root_nd = self.config_manager.get("ROOT_ND")
        user_nd = self.config_manager.get("USER_ND")
        url = f"{root_nd}/rest/getCoverArt.view"
        params = {
            'u': user_nd,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'python-script',
            'id': album_id,
            'size': 1200
        }
        try:
            response = requests.get(url, params=params, stream=True)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            print(f"Error fetching album art: {e}")
            return None

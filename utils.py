import os
import re
import subprocess
import requests
from mutagen.id3 import ID3, COMM, APIC, error
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.m4a import M4A
import imghdr
from config import *

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
    def __init__(self):
        self.target_comment = TARGET_COMMENT
        self.lastfm_target_comment = LASTFM_TARGET_COMMENT

    def add_comment_to_file(self, file_path, comment):
        """Add a comment to a specific audio file."""
        try:
            audio = ID3(file_path)
        except error.ID3NoHeaderError:
            audio = ID3()

        audio.add(COMM(encoding=3, lang='eng', desc='', text=comment))
        audio.save(file_path, v2_version=3, v1=2)

    def _embed_album_art(self, file_path, album_art_url):
        """Downloads and embeds album art into the audio file."""
        if not album_art_url:
            print(f"No album art URL provided for {file_path}.")
            return

        try:
            response = requests.get(album_art_url, stream=True)
            response.raise_for_status()
            image_data = response.content

            image_type = imghdr.what(None, h=image_data)
            if not image_type:
                print(f"Could not determine image type for {album_art_url}. Skipping embedding.")
                return

            mime_type = f"image/{image_type}"

            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime=mime_type,
                        type=3,
                        desc='Cover',
                        data=image_data
                    )
                )
                audio.save()
                print(f"Embedded album art into MP3: {file_path}")
            elif file_path.lower().endswith('.flac'):
                audio = FLAC(file_path)
                image = FLAC.Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                audio.add_picture(image)
                audio.save()
                print(f"Embedded album art into FLAC: {file_path}")
            elif file_path.lower().endswith(('.ogg', '.oga')):
                audio = OggVorbis(file_path)
                image = OggVorbis.Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                audio.add_picture(image)
                audio.save()
                print(f"Embedded album art into OggVorbis: {file_path}")
            elif file_path.lower().endswith('.m4a'):
                audio = M4A(file_path)
                image = M4A.Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                audio.tags['covr'] = [image]
                audio.save()
                print(f"Embedded album art into M4A: {file_path}")
            else:
                print(f"Unsupported file type for album art embedding: {file_path}")

        except requests.exceptions.RequestException as e:
            print(f"Error downloading album art from {album_art_url}: {e}")
        except Exception as e:
            print(f"Error embedding album art into {file_path}: {e}")

    def tag_track(self, file_path, artist, title, album, release_date, recording_mbid, source, album_art_url=None):
        """Tags a track with metadata using kid3-cli and embeds album art."""
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
            
            if album_art_url:
                self._embed_album_art(file_path, album_art_url)

        except subprocess.CalledProcessError as e:
            print(f"Error tagging {file_path}: {e}")
        except FileNotFoundError:
            print(f"kid3-cli not found. Is it installed and in your PATH?")

    def get_album_art(self, album_id, salt, token):
        """Fetches album art from Navidrome."""
        url = f"{ROOT_ND}/rest/getCoverArt.view"
        params = {
            'u': USER_ND,
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

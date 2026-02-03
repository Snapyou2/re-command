from datetime import datetime
import json
import os
import re
import requests
import imghdr
from mutagen.id3 import ID3, COMM, APIC, TPE1, TALB, TIT2, TDRC, TXXX, UFID, error as ID3Error
from mutagen import File, MutagenError
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4, MP4Cover
from streamrip.db import Database, Downloads, Failed
from config import *

def get_user_history_path(username):
    """Get per-user download history path."""
    base_dir = os.path.dirname(globals().get('DOWNLOAD_HISTORY_PATH', '/app/data/download_history.json'))
    os.makedirs(base_dir, exist_ok=True)
    safe_user = username.replace('/', '_').replace('\\', '_')
    return os.path.join(base_dir, f'download_history_{safe_user}.json')

def initialize_streamrip_db():
    """Initializes the streamrip database, ensuring tables exist."""
    db_path = "/app/temp_downloads/downloads.db"
    failed_db_path = "/app/temp_downloads/failed_downloads.db"
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    print(f"Initializing streamrip database at {db_path}...")
    try:
        # Instantiating Downloads and Failed should create their tables if they don't exist
        # based on streamrip's design. This ensures the schema is in place.
        downloads_db = Downloads(db_path)
        failed_downloads_db = Failed(failed_db_path)
        Database(downloads=downloads_db, failed=failed_downloads_db)
        print("Streamrip database initialization complete.")
    except Exception as e:
        print(f"Error initializing streamrip database: {e}")
        # Re-raise the exception to make sure the program doesn't continue with a broken DB
        raise

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
    def __init__(self, album_recommendation_comment=None):
        self.target_comment = TARGET_COMMENT
        self.lastfm_target_comment = LASTFM_TARGET_COMMENT
        self.album_recommendation_comment = album_recommendation_comment

    def add_comment_to_file(self, file_path, comment):
        """Add a comment to a specific audio file."""
        # Debug logging
        debug_info = {
            'file_path': file_path,
            'comment': comment,
            'timestamp': __import__('datetime').datetime.now().isoformat()
        }
        with open('/app/debug.log', 'a') as f:
            f.write(f"ADD_COMMENT_START: {debug_info}\n")

        try:
            if file_path.lower().endswith('.mp3'):
                # For MP3s, use ID3 tags
                try:
                    audio = ID3(file_path)
                except error.ID3NoHeaderError:
                    audio = ID3()

                audio.add(COMM(encoding=3, lang='eng', desc='', text=comment))
                audio.save(file_path, v2_version=3, v1=2)
                print(f"Added ID3 comment to MP3 file: {file_path}")

            elif file_path.lower().endswith('.flac'):
                # For FLAC, use Vorbis comments
                audio = FLAC(file_path)
                audio['comment'] = comment
                audio.save()
                print(f"Added Vorbis comment to FLAC file: {file_path}")

            elif file_path.lower().endswith(('.ogg', '.oga')):
                # For OggVorbis, use Vorbis comments
                audio = OggVorbis(file_path)
                audio['comment'] = comment
                audio.save()
                print(f"Added Vorbis comment to OggVorbis file: {file_path}")

            elif file_path.lower().endswith('.m4a'):
                # For M4A, use iTunes-style atoms
                audio = MP4(file_path)
                audio['\xa9cmt'] = [comment]
                audio.save()
                print(f"Added iTunes comment to M4A file: {file_path}")

            else:
                print(f"Unsupported file type for adding comment: {file_path}")
                return

        except Exception as e:
            print(f"Error adding comment to {file_path}: {e}")
            import traceback
            traceback.print_exc()

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
                image = Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                audio.clear_pictures()
                audio.add_picture(image)
                audio.save()
                print(f"Embedded album art into FLAC: {file_path}")
            elif file_path.lower().endswith(('.ogg', '.oga')):
                audio = OggVorbis(file_path)
                image = Picture()
                image.data = image_data
                image.type = 3
                image.mime = mime_type
                import base64
                encoded = base64.b64encode(image.write()).decode('ascii')
                audio['metadata_block_picture'] = [encoded]
                audio.save()
                print(f"Embedded album art into OggVorbis: {file_path}")
            elif file_path.lower().endswith('.m4a'):
                audio = MP4(file_path)
                img_format = MP4Cover.FORMAT_JPEG if image_type == 'jpeg' else MP4Cover.FORMAT_PNG
                audio.tags['covr'] = [MP4Cover(image_data, imageformat=img_format)]
                audio.save()
                print(f"Embedded album art into M4A: {file_path}")
            else:
                print(f"Unsupported file type for album art embedding: {file_path}")

        except requests.exceptions.RequestException as e:
            print(f"Error downloading album art from {album_art_url}: {e}")
        except Exception as e:
            print(f"Error embedding album art into {file_path}: {e}")

    def tag_track(self, file_path, artist, title, album, release_date, recording_mbid, source, album_art_url=None, is_album_recommendation=False, album_artist=None, artists=None, artist_mbids=None):
        """Tags a track with metadata using Mutagen and embeds album art."""
        
        # If title is not provided, try to extract it from the filename
        if not title:
            base_filename = os.path.splitext(os.path.basename(file_path))[0]
            extracted_title = base_filename

            # Remove artist name if present at the beginning (case-insensitive)
            if artist:
                artist_pattern = re.compile(f"^{re.escape(artist)}\s*-\s*", re.IGNORECASE)
                extracted_title = artist_pattern.sub("", extracted_title, 1)

            # Remove common track number patterns like "01 - ", "01. "
            extracted_title = re.sub(r"^\d+\s*-\s*", "", extracted_title) # "01 - Title"
            extracted_title = re.sub(r"^\d+\.\s*", "", extracted_title)   # "01. Title"
            extracted_title = re.sub(r"^\(\d+\)\s*", "", extracted_title)  # "(01) Title"

            # Remove any trailing " - " or leading/trailing whitespace
            extracted_title = extracted_title.strip(' -')
            extracted_title = extracted_title.strip() # Final trim
            
            # If after all removals, the title is empty, use the original base filename
            if not extracted_title:
                extracted_title = base_filename

            title = extracted_title # Ensure title is set
            
        if is_album_recommendation and self.album_recommendation_comment:
            comment = self.album_recommendation_comment
        else:
            comment = self.target_comment if source == "ListenBrainz" else self.lastfm_target_comment
        
        try:
            audio = File(file_path)
            if audio is None:
                print(f"Could not open audio file with Mutagen: {file_path}")
                return

            if file_path.lower().endswith('.mp3'):
                # For MP3s, use ID3 tags
                if audio.tags is None:
                    audio.tags = ID3()

                from mutagen.id3 import TPE2
                if artists and len(artists) > 1:
                    # Multi-artist: use plural ARTISTS tag only, skip singular ARTIST
                    audio.tags.add(TXXX(encoding=3, desc='ARTISTS', text=artists))
                elif artist:
                    audio.tags.add(TPE1(encoding=3, text=[artist]))
                if album_artist:
                    audio.tags.add(TPE2(encoding=3, text=[album_artist]))
                audio.tags.add(TIT2(encoding=3, text=[title]))
                audio.tags.add(TALB(encoding=3, text=[album]))
                audio.tags.add(TDRC(encoding=3, text=[release_date]))
                audio.tags.add(COMM(encoding=3, lang='eng', desc='', text=[comment]))

                if recording_mbid:
                    audio.tags.add(TXXX(encoding=3, desc='MUSICBRAINZ_RECORDINGID', text=[recording_mbid]))
                    audio.tags.add(UFID(owner='http://musicbrainz.org', data=f'http://musicbrainz.org/recording/{recording_mbid}'.encode('utf-8')))
                if artist_mbids:
                    audio.tags.add(TXXX(encoding=3, desc='MUSICBRAINZ_ARTISTID', text=artist_mbids))

            elif file_path.lower().endswith('.flac'):
                # For FLAC, use Vorbis comments
                if artists and len(artists) > 1:
                    # Multi-artist: use plural ARTISTS tag, skip singular
                    audio['artists'] = artists
                    if 'artist' in audio:
                        del audio['artist']
                elif artist:
                    audio['artist'] = [artist]
                if album_artist:
                    audio['albumartist'] = [album_artist]
                audio['title'] = [title]
                audio['album'] = [album]
                audio['date'] = [release_date]
                audio['comment'] = [comment]
                if recording_mbid:
                    audio['musicbrainz_recordingid'] = recording_mbid
                if artist_mbids:
                    audio['musicbrainz_artistid'] = artist_mbids

            elif file_path.lower().endswith(('.ogg', '.oga')):
                # For OggVorbis, use Vorbis comments
                if artists and len(artists) > 1:
                    audio['artists'] = artists
                    if 'artist' in audio:
                        del audio['artist']
                elif artist:
                    audio['artist'] = [artist]
                if album_artist:
                    audio['albumartist'] = [album_artist]
                audio['title'] = [title]
                audio['album'] = [album]
                audio['date'] = [release_date]
                audio['comment'] = [comment]
                if recording_mbid:
                    audio['musicbrainz_recordingid'] = recording_mbid
                if artist_mbids:
                    audio['musicbrainz_artistid'] = artist_mbids

            elif file_path.lower().endswith('.m4a'):
                # For M4A, use iTunes-style atoms (no standard plural tag — use separator)
                if artists and len(artists) > 1:
                    audio['\xa9ART'] = ["; ".join(artists)]
                elif artist:
                    audio['\xa9ART'] = [artist]
                if album_artist:
                    audio['aART'] = [album_artist]
                audio['\xa9nam'] = [title]
                audio['\xa9alb'] = [album]
                audio['\xa9day'] = [release_date]
                audio['\xa9cmt'] = [comment]
                if recording_mbid:
                    # M4A does not have a standard tag for MusicBrainz ID, use a custom one
                    audio['----:com.apple.iTunes:MusicBrainz Recording Id'] = [recording_mbid.encode('utf-8')]
                if artist_mbids:
                    audio['----:com.apple.iTunes:MusicBrainz Artist Id'] = [mbid.encode('utf-8') for mbid in artist_mbids]

            else:
                print(f"Unsupported file type for tagging: {file_path}")
                return

            audio.save()
            print(f"Successfully tagged {file_path} with Mutagen.")

            if album_art_url:
                self._embed_album_art(file_path, album_art_url)

        except MutagenError as e:
            print(f"Error tagging {file_path} with Mutagen: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while tagging {file_path}: {e}")

    def get_album_art(self, album_id, salt, token):
        """Fetches album art from Navidrome."""
        url = f"{ROOT_ND}/rest/getCoverArt.view"
        params = {
            'u': USER_ND,
            't': token,
            's': salt,
            'v': '1.16.1',
            'c': 'trackdrop',
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

def update_status_file(download_id, status, message=None, title=None, current_track_count=None, total_track_count=None, **kwargs):
    if not download_id:
        return

    status_dir = "/tmp/trackdrop_download_status"
    os.makedirs(status_dir, exist_ok=True)
    status_file_path = os.path.join(status_dir, f"{download_id}.json")

    status_data = {
        "status": status,
        "timestamp": datetime.now().isoformat()
    }
    if message:
        status_data["message"] = message
    if title:
        status_data["title"] = title
    else:
        # Default title based on status
        if status == "completed":
            status_data["title"] = "Download completed"
        elif status == "failed":
            status_data["title"] = "Download failed"
        elif status == "in_progress":
            status_data["title"] = "Download in progress"
    if current_track_count is not None:
        status_data["current_track_count"] = current_track_count
    if total_track_count is not None:
        status_data["total_track_count"] = total_track_count
    # Pass through extra fields (e.g. tracks, skipped_count, failed_count)
    for key in ('tracks', 'skipped_count', 'failed_count', 'downloaded_count', 'download_type'):
        if key in kwargs and kwargs[key] is not None:
            status_data[key] = kwargs[key]

    with open(status_file_path, 'w') as f:
        json.dump(status_data, f)
    print(f"Updated status file for {download_id}: {status}")

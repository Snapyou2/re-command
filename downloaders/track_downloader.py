import os
import subprocess
import asyncio
from streamrip.client import DeezerClient
from streamrip.media import Track, PendingSingle
from streamrip.config import Config
from streamrip.db import Database, Downloads, Failed
from mutagen.id3 import ID3, COMM, error
from tqdm import tqdm
import sys
import importlib
import config

class TrackDownloader:
    def __init__(self, tagger):
        self.tagger = tagger
        # Initial load, will be reloaded dynamically
        self.temp_download_folder = config.TEMP_DOWNLOAD_FOLDER
        self.deezer_arl = config.DEEZER_ARL

    async def download_track(self, song_info, lb_recommendation=None):
        """Downloads a track using the configured method."""
        # Reload config to get the latest DOWNLOAD_METHOD
        importlib.reload(config)
        current_download_method = config.DOWNLOAD_METHOD
        temp_download_folder = config.TEMP_DOWNLOAD_FOLDER
        deezer_arl = config.DEEZER_ARL

        # Determine the correct comment based on source and lb_recommendation flag
        # Special handling: if source is 'Manual' but lb_recommendation is set, prioritize it
        if lb_recommendation is not None and lb_recommendation:
            comment = config.TARGET_COMMENT
        elif song_info.get('source', '').lower() == 'llm':
            comment = config.LLM_TARGET_COMMENT
        elif song_info.get('source', '').lower() == 'listenbrainz':
            comment = config.TARGET_COMMENT
        else:
            comment = config.LASTFM_TARGET_COMMENT

        # Debug logging
        debug_info = {
            'song_info': song_info,
            'lb_recommendation': lb_recommendation,
            'determined_comment': comment,
            'timestamp': __import__('datetime').datetime.now().isoformat()
        }
        with open('/app/debug.log', 'a') as f:
            f.write(f"TRACK_DOWNLOADER_START: {debug_info}\n")

        deezer_link = await self._get_deezer_link_and_details(song_info)
        if not deezer_link:
            print(f"  ❌ No Deezer link found for {song_info['artist']} - {song_info['title']}")
            return None

        downloaded_file_path = None
        if current_download_method == "deemix":
            downloaded_file_path = self._download_track_deemix(deezer_link, song_info, temp_download_folder)
        elif current_download_method == "streamrip":
            downloaded_file_path = await self._download_track_streamrip(deezer_link, song_info, temp_download_folder)
        else:
            print(f"  ❌ Unknown DOWNLOAD_METHOD: {current_download_method}")
            return None

        if downloaded_file_path:
            self.tagger.tag_track(
                downloaded_file_path,
                song_info['artist'],
                song_info['title'],
                song_info['album'],
                song_info['release_date'],
                song_info['recording_mbid'],
                song_info['source'],
                song_info.get('album_art')
            )
            self.tagger.add_comment_to_file(
                downloaded_file_path,
                comment
            )
            return downloaded_file_path
        else:
            print(f"  ❌ Failed to download: {song_info['artist']} - {song_info['title']}")
            return None

    async def _get_deezer_link_and_details(self, song_info):
        """Fetches Deezer link and updates song_info with album details."""
        from apis.deezer_api import DeezerAPI
        deezer_api = DeezerAPI()
        deezer_link = await deezer_api.get_deezer_track_link(song_info['artist'], song_info['title'])
        if deezer_link:
            track_id = deezer_link.split('/')[-1]
            deezer_details = await deezer_api.get_deezer_track_details(track_id)
            if deezer_details:
                song_info['album'] = deezer_details.get('album', song_info['album'])
                song_info['release_date'] = deezer_details.get('release_date', song_info['release_date'])
                song_info['album_art'] = deezer_details.get('album_art', song_info.get('album_art'))
        return deezer_link

    def _download_track_deemix(self, deezer_link, song_info, temp_download_folder):
        """Downloads a track using deemix."""
        try:
            output_dir = temp_download_folder
            deemix_command = [
                "deemix",
                "-p", output_dir,
                deezer_link
            ]
            env = os.environ.copy()
            env['XDG_CONFIG_HOME'] = '/root/.config'
            env['HOME'] = '/root'

            result = subprocess.run(deemix_command, capture_output=True, text=True, env=env)

            downloaded_file = None
            for line in result.stdout.splitlines():
                if "Completed download of" in line:
                    relative_path = line.split("Completed download of ")[1].strip()
                    if relative_path.startswith('/'):
                        relative_path = relative_path[1:]
                    downloaded_file = os.path.join(output_dir, relative_path)
                    break

            if not downloaded_file or not os.path.exists(downloaded_file):
                print(f"Could not determine downloaded file path from deemix output for {song_info['artist']} - {song_info['title']}.")
                print(f"deemix stdout: {result.stdout}")
                print(f"deemix stderr: {result.stderr}")
                # Fallback: search for the file using improved logic
                downloaded_file = self._find_downloaded_file_deemix(song_info, output_dir)

            if downloaded_file:
                # Fix permissions
                dir_path = os.path.dirname(downloaded_file)
                os.system(f'chown -R 1000:1000 "{dir_path}"')

            return downloaded_file
        except Exception as e:
            print(f"Error downloading track {song_info['artist']} - {song_info['title']} ({deezer_link}) with deemix: {e}")
            return None

    def _find_downloaded_file_deemix(self, song_info, temp_download_folder):
        """Finds the downloaded file for deemix using improved search logic."""
        from utils import sanitize_filename
        import time

        sanitized_artist = sanitize_filename(song_info['artist']).lower()
        sanitized_title = sanitize_filename(song_info['title']).lower()

        # Get all audio files with their modification times
        audio_files = []
        for root, _, files in os.walk(temp_download_folder):
            for filename in files:
                if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    filepath = os.path.join(root, filename)
                    mtime = os.path.getmtime(filepath)
                    audio_files.append((filepath, mtime, filename))

        # Sort by modification time (newest first)
        audio_files.sort(key=lambda x: x[1], reverse=True)

        # First, try strict matching (both artist and title)
        for filepath, mtime, filename in audio_files:
            sanitized_filename = sanitize_filename(filename).lower()
            if sanitized_artist in sanitized_filename and sanitized_title in sanitized_filename:
                return filepath

        # Fallback: try title-only matching for recently modified files (last 60 seconds)
        current_time = time.time()
        for filepath, mtime, filename in audio_files:
            if current_time - mtime > 60:  # Skip files older than 1 minute
                continue
            sanitized_filename = sanitize_filename(filename).lower()
            if sanitized_title in sanitized_filename:
                return filepath

        # Last resort: return the most recently modified audio file if it's very recent
        if audio_files and current_time - audio_files[0][1] < 30:  # Within last 30 seconds
            filepath, mtime, filename = audio_files[0]
            return filepath

        return None

    async def _download_track_streamrip(self, deezer_link: str, song_info, temp_download_folder):
        """Downloads a track using streamrip."""
        try:
            # Streamrip Config object, path -> streamrip config file
            streamrip_config = Config("/root/.config/streamrip/config.toml")

            # Initialize DeezerClient with the config object
            client = DeezerClient(config=streamrip_config)

            await client.login()
            track_id = deezer_link.split('/')[-1]

            # Creating a database for streamrip
            rip_db = Database(downloads=Downloads("/app/temp_downloads/downloads.db"), failed=Failed("/app/temp_downloads/failed_downloads.db"))

            # Get the PendingSingle object
            pending = PendingSingle(id=track_id, client=client, config=streamrip_config, db=rip_db)

            # Resolve the PendingSingle to get the actual Media (Track) object
            my_track = await pending.resolve()

            if my_track is None:
                print(f"Skipping download for {song_info['artist']} - {song_info['title']} (Error resolving media or already downloaded).", file=sys.stderr)
                print(f"Debug: Deezer link: {deezer_link}, track_id: {track_id}", file=sys.stderr)
                return None

            await my_track.rip()

            # Try to get the path directly from the track object first
            downloaded_file_path = None
            if hasattr(my_track, 'path') and my_track.path and os.path.exists(my_track.path):
                downloaded_file_path = my_track.path
            else:
                downloaded_file_path = await self._find_downloaded_file_streamrip(song_info, temp_download_folder)

            if downloaded_file_path and os.path.exists(downloaded_file_path):
                # Fix permissions
                dir_path = os.path.dirname(downloaded_file_path)
                os.system(f'chown -R 1000:1000 "{dir_path}"')
                return downloaded_file_path
            else:
                print(f"  ❌ Could not find downloaded file for {song_info['artist']} - {song_info['title']}", file=sys.stderr)
                return None

        except Exception as e:
            print(f"Error downloading {song_info['artist']} - {song_info['title']} with streamrip: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return None
        finally:
            try:
                await client.session.close()
            except Exception as e:
                print(f"Error closing streamrip client session: {e}", file=sys.stderr)

    async def _find_downloaded_file_streamrip(self, song_info, temp_download_folder):
        """Finds the downloaded file using improved search logic with retry and better matching."""
        from utils import sanitize_filename
        import time

        # Wait a bit for the file to be fully written
        await asyncio.sleep(2)

        sanitized_artist = sanitize_filename(song_info['artist']).lower()
        sanitized_title = sanitize_filename(song_info['title']).lower()

        # Get all audio files with their modification times
        audio_files = []
        for root, _, files in os.walk(temp_download_folder):
            for filename in files:
                if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    filepath = os.path.join(root, filename)
                    mtime = os.path.getmtime(filepath)
                    audio_files.append((filepath, mtime, filename))

        # Sort by modification time (newest first)
        audio_files.sort(key=lambda x: x[1], reverse=True)

        # First, try strict matching (both artist and title)
        for filepath, mtime, filename in audio_files:
            sanitized_filename = sanitize_filename(filename).lower()
            if sanitized_artist in sanitized_filename and sanitized_title in sanitized_filename:
                return filepath

        # Fallback: try title-only matching for recently modified files (last 60 seconds)
        current_time = time.time()
        for filepath, mtime, filename in audio_files:
            if current_time - mtime > 60:  # Skip files older than 1 minute
                continue
            sanitized_filename = sanitize_filename(filename).lower()
            if sanitized_title in sanitized_filename:
                return filepath

        # Last resort: return the most recently modified audio file if it's very recent
        if audio_files and current_time - audio_files[0][1] < 30:  # Within last 30 seconds
            filepath, mtime, filename = audio_files[0]
            return filepath

        return None

    def _debug_list_files(self, directory):
        """Lists all files in the directory for debugging purposes."""
        print(f"Debug: Listing files in {directory}")
        try:
            for root, dirs, files in os.walk(directory):
                level = root.replace(directory, '').count(os.sep)
                indent = ' ' * 2 * level
                print(f"{indent}{os.path.basename(root)}/")
                subindent = ' ' * 2 * (level + 1)
                for file in files:
                    print(f"{subindent}{file}")
        except Exception as e:
            print(f"Error listing files: {e}")

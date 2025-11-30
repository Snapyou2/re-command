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

    async def download_track(self, song_info):
        """Downloads a track using the configured method."""
        # Reload config to get the latest DOWNLOAD_METHOD
        importlib.reload(config)
        current_download_method = config.DOWNLOAD_METHOD
        temp_download_folder = config.TEMP_DOWNLOAD_FOLDER
        deezer_arl = config.DEEZER_ARL
        comment = config.TARGET_COMMENT if song_info['source'] == 'ListenBrainz' else config.LASTFM_TARGET_COMMENT

        print(f"Starting download for {song_info['artist']} - {song_info['title']}")
        deezer_link = await self._get_deezer_link_and_details(song_info)
        if not deezer_link:
            error_msg = f"No Deezer link found for {song_info['artist']} - {song_info['title']}."
            print(f"❌ {error_msg}")
            return {"status": "error", "message": error_msg}

        print(f"✅ Found Deezer link: {deezer_link}")
        downloaded_file_path = None
        if current_download_method == "deemix":
            print(f"📥 Downloading using deemix method...")
            downloaded_file_path = self._download_track_deemix(deezer_link, song_info, temp_download_folder)
        elif current_download_method == "streamrip":
            print(f"📥 Downloading using streamrip method...")
            downloaded_file_path = await self._download_track_streamrip(deezer_link, song_info, temp_download_folder)
        else:
            error_msg = f"Unknown DOWNLOAD_METHOD: {current_download_method}. Skipping download for {song_info['artist']} - {song_info['title']}."
            print(f"❌ {error_msg}")
            return {"status": "error", "message": error_msg}

        if downloaded_file_path:
            print(f"🏷️  Tagging downloaded file: {downloaded_file_path}")
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
            print(f"💬 Adding comment to file: {comment}")
            self.tagger.add_comment_to_file(
                downloaded_file_path,
                comment
            )
            print(f"✅ Successfully downloaded and processed: {song_info['artist']} - {song_info['title']}")
            return {"status": "success", "file": downloaded_file_path}
        else:
            error_msg = f"Failed to download: {song_info['artist']} - {song_info['title']}"
            print(f"❌ {error_msg}")
            return {"status": "error", "message": error_msg}

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

            if not downloaded_file:
                print(f"deemix stdout: {result.stdout}")
                print(f"deemix stderr: {result.stderr}")
                print(f"Could not determine downloaded file path from deemix output for {song_info['artist']} - {song_info['title']}.")
                for filename in os.listdir(output_dir):
                    if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                        from utils import sanitize_filename
                        if sanitize_filename(song_info['artist']).lower() in filename.lower() and sanitize_filename(song_info['title']).lower() in filename.lower():
                            downloaded_file = os.path.join(output_dir, filename)
                            break
            return downloaded_file
        except Exception as e:
            print(f"Error downloading track {song_info['artist']} - {song_info['title']} ({deezer_link}) with deemix: {e}")
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
                return None

            await my_track.rip()
            
            # After ripping, find the downloaded file in the temp_download_folder
            downloaded_file_path = None
            output_dir = temp_download_folder
            
            # Find a file matching artist and title in the output directory
            from utils import sanitize_filename
            sanitized_artist = sanitize_filename(song_info['artist']).lower()
            sanitized_title = sanitize_filename(song_info['title']).lower()

            for root, _, files in os.walk(output_dir):
                for filename in files:
                    if sanitized_artist in sanitize_filename(filename).lower() and \
                       sanitized_title in sanitize_filename(filename).lower() and \
                       filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                        downloaded_file_path = os.path.join(root, filename)
                        break
                if downloaded_file_path:
                    break

            if downloaded_file_path:
                print(f"Successfully downloaded {song_info['artist']} - {song_info['title']} using streamrip to {downloaded_file_path}")
                return downloaded_file_path
            else:
                print(f"Successfully called rip() for {song_info['artist']} - {song_info['title']}, but could not find the downloaded file in {output_dir}.", file=sys.stderr)
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

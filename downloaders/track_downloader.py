import os
import subprocess
import asyncio
from streamrip.client import DeezerClient
from streamrip.media import Track
from mutagen.id3 import ID3, COMM, error
from tqdm import tqdm
import sys

class TrackDownloader:
    def __init__(self, config_manager, tagger):
        self.config_manager = config_manager
        self.tagger = tagger
        self.temp_download_folder = self.config_manager.get("TEMP_DOWNLOAD_FOLDER")
        self.deezer_arl = self.config_manager.get("DEEZER_ARL")
        self.download_method = self.config_manager.get("DOWNLOAD_METHOD")

    async def download_track(self, song_info):
        """Downloads a track using the configured method."""
        deezer_link = self._get_deezer_link_and_details(song_info)
        if not deezer_link:
            print(f"Skipping download for {song_info['artist']} - {song_info['title']} (no Deezer link found).")
            return None

        downloaded_file_path = None
        if self.download_method == "deemix":
            downloaded_file_path = self._download_track_deemix(deezer_link, song_info)
        elif self.download_method == "streamrip":
            downloaded_file_path = await self._download_track_streamrip(deezer_link, song_info)
        else:
            print(f"Unknown DOWNLOAD_METHOD: {self.download_method}. Skipping download for {song_info['artist']} - {song_info['title']}.")
            return None

        if downloaded_file_path:
            self.tagger.tag_track(
                downloaded_file_path,
                song_info['artist'],
                song_info['title'],
                song_info['album'],
                song_info['release_date'],
                song_info['recording_mbid'],
                song_info['source']
            )
            self.tagger.add_comment_to_file(
                downloaded_file_path,
                self.config_manager.get("TARGET_COMMENT") if song_info['source'] == 'ListenBrainz' else self.config_manager.get("LASTFM_TARGET_COMMENT")
            )
            return downloaded_file_path
        return None

    def _get_deezer_link_and_details(self, song_info):
        """Fetches Deezer link and updates song_info with album details."""
        from apis.deezer_api import DeezerAPI
        deezer_api = DeezerAPI()
        deezer_link = deezer_api.get_deezer_track_link(song_info['artist'], song_info['title'])
        if deezer_link:
            track_id = deezer_link.split('/')[-1]
            deezer_details = deezer_api.get_deezer_track_details(track_id)
            if deezer_details:
                song_info['album'] = deezer_details.get('album', song_info['album'])
                song_info['release_date'] = deezer_details.get('release_date', song_info['release_date'])
        return deezer_link

    def _download_track_deemix(self, deezer_link, song_info):
        """Downloads a track using deemix."""
        try:
            output_dir = self.temp_download_folder
            deemix_command = [
                "deemix",
                "-p", output_dir,
                deezer_link
            ]
            result = subprocess.run(deemix_command, capture_output=True, text=True)

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

    async def _download_track_streamrip(self, deezer_link: str, song_info):
        """Downloads a track using streamrip v2 API."""
        client = None
        try:
            from streamrip.config import Config
            from streamrip.media import PendingSingle
            from streamrip.db import Database, Dummy
            import os

            # Get the streamrip config file path
            config_path = os.path.expanduser("~/.config/streamrip/config.toml")

            # Create config object and pass it to DeezerClient
            config = Config(config_path)
            client = DeezerClient(config)

            # Update the downloads folder to use our temp directory
            config.session.downloads.folder = self.temp_download_folder

            await client.login()

            track_id = deezer_link.split('/')[-1]

            # Use the new Database API that requires both downloads and failed arguments
            # Using Dummy() for both as we don't need database tracking for individual tracks
            db = Database(downloads=Dummy(), failed=Dummy())
            pending_track = PendingSingle(track_id, client, config, db)

            # Resolve the pending track to get metadata and create the actual Track object
            resolved_track = await pending_track.resolve()

            if resolved_track is None:
                print(f"Failed to resolve track {song_info['artist']} - {song_info['title']}")
                return None

            # Download the track
            await resolved_track.rip()

            # Get the downloaded file path from the track object
            downloaded_file_path = resolved_track.download_path

            print(f"Successfully downloaded {song_info['artist']} - {song_info['title']} using streamrip to {downloaded_file_path}")
            return downloaded_file_path

        except asyncio.CancelledError:
            print(f"Download cancelled for {song_info['artist']} - {song_info['title']}")
            return None
        except KeyboardInterrupt:
            print(f"Download interrupted for {song_info['artist']} - {song_info['title']}")
            return None
        except Exception as e:
            print(f"Error downloading {song_info['artist']} - {song_info['title']} with streamrip: {e}")
            return None
        finally:
            # Properly close the client and session to avoid unclosed session warnings
            if client:
                try:
                    # Try to close the session directly since DeezerClient doesn't have close()
                    if hasattr(client, 'session') and client.session:
                        await client.session.close()
                except Exception as e:
                    print(f"Warning: Error closing client session: {e}")

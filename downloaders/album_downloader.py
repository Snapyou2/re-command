import os
import subprocess
import asyncio
from streamrip.client import DeezerClient
from streamrip.media import Album, PendingAlbum
from streamrip.config import Config
from streamrip.db import Database, Downloads, Failed
from tqdm import tqdm
import sys
from config import *

class AlbumDownloader:
    def __init__(self, tagger):
        self.tagger = tagger
        self.temp_download_folder = TEMP_DOWNLOAD_FOLDER
        self.deezer_arl = DEEZER_ARL
        self.download_method = DOWNLOAD_METHOD

    async def download_album(self, album_info):
        """Downloads an album using the configured method."""
        print(f"Starting download for album: {album_info['artist']} - {album_info['album']}")
        deezer_link = self._get_deezer_album_link(album_info)
        if not deezer_link:
            error_msg = "Album not found on Deezer!"
            print(error_msg)
            return {"status": "error", "message": error_msg}

        print(f"Found Deezer link: {deezer_link}")

        downloaded_files = []
        if self.download_method == "deemix":
            downloaded_files = self._download_album_deemix(deezer_link, album_info)
        elif self.download_method == "streamrip":
            downloaded_files = await self._download_album_streamrip(deezer_link, album_info)
        else:
            error_msg = f"Unknown DOWNLOAD_METHOD: {self.download_method}. Skipping download for {album_info['artist']} - {album_info['album']}."
            print(error_msg)
            return {"status": "error", "message": error_msg}

        if downloaded_files:
            # Tag all tracks in the album
            for file_path in downloaded_files:
                self.tagger.tag_track(
                    file_path,
                    album_info['artist'],
                    "",  # title will be extracted from filename if needed
                    album_info['album'],
                    album_info['release_date'],
                    "",  # recording_mbid not available
                    "Fresh Releases",
                    album_info.get('album_art')
                )
            return {"status": "success", "files": downloaded_files}
        else:
            error_msg = f"Failed to download album {album_info['artist']} - {album_info['album']}."
            print(error_msg)
            return {"status": "error", "message": error_msg}

    def _get_deezer_album_link(self, album_info):
        """Fetches Deezer album link."""
        print(f"Deezer API: Searching for album '{album_info['album']}' by '{album_info['artist']}'")
        from apis.deezer_api import DeezerAPI
        deezer_api = DeezerAPI()
        link = deezer_api.get_deezer_album_link(album_info['artist'], album_info['album'])
        if link:
            print(f"Deezer API: Found album link: {link}")
        else:
            print("Deezer API: No album link found.")
        return link

    def _download_album_deemix(self, deezer_link, album_info):
        """Downloads an album using deemix."""
        try:
            output_dir = self.temp_download_folder
            print(f"Deemix: Using output directory: {output_dir}")
            if not os.path.exists(output_dir):
                print(f"WARNING: Output directory {output_dir} does not exist. Creating it.")
                os.makedirs(output_dir, exist_ok=True)
            deemix_command = [
                "deemix",
                "-p", output_dir,
                deezer_link
            ]
            print(f"Deemix: Running command: {' '.join(deemix_command)}")
            # Prepare the Environment for deemix
            env = os.environ.copy()
            env['XDG_CONFIG_HOME'] = '/root/.config'
            env['HOME'] = '/root'

            # Debugging information
            print(f"Deemix subprocess - Current User: {subprocess.getoutput('whoami')}")
            print(f"Deemix subprocess - Can read .arl?: {os.access('/root/.config/deemix/.arl', os.R_OK)}")
            print(f"Deemix subprocess - File listing: {subprocess.getoutput('ls -la /root/.config/deemix/')}")

            result = subprocess.run(deemix_command, capture_output=True, text=True, env=env)
            print(f"Deemix: Command completed with return code: {result.returncode}")

            downloaded_files = []
            print("Deemix: Parsing stdout for 'Completed download of'...")
            for line in result.stdout.splitlines():
                if "Completed download of" in line:
                    print(f"Deemix: Found completion line: {line}")
                    relative_path = line.split("Completed download of ")[1].strip()
                    if relative_path.startswith('/'):
                        relative_path = relative_path[1:]
                    album_dir = os.path.join(output_dir, relative_path)
                    print(f"Deemix: Checking album directory: {album_dir}")
                    if os.path.isdir(album_dir):
                        print(f"Deemix: Album directory exists, collecting audio files...")
                        # Collect all audio files in the album directory
                        for root, _, files in os.walk(album_dir):
                            for filename in files:
                                if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                                    downloaded_files.append(os.path.join(root, filename))
                        print(f"Deemix: Found {len(downloaded_files)} audio files in album directory.")
                    else:
                        print(f"Deemix: Album directory does not exist: {album_dir}")
                    break
            else:
                print("Deemix: No 'Completed download of' line found in stdout.")

            if not downloaded_files:
                print(f"Deemix: Full stdout: {result.stdout}")
                print(f"Deemix: Full stderr: {result.stderr}")
                print(f"Deemix: Could not determine downloaded album path from deemix output for {album_info['artist']} - {album_info['album']}.")
                # Fallback: directories with artist and album names
                from utils import sanitize_filename
                sanitized_artist = sanitize_filename(album_info['artist']).lower()
                sanitized_album = sanitize_filename(album_info['album']).lower()
                print(f"Deemix: Fallback search - looking for directories containing '{sanitized_artist}' and '{sanitized_album}' in {output_dir}")
                try:
                    items = os.listdir(output_dir)
                    print(f"Deemix: Items in output dir: {items}")
                    for item in items:
                        item_path = os.path.join(output_dir, item)
                        if os.path.isdir(item_path):
                            print(f"Deemix: Checking directory: {item}")
                            if sanitized_artist in item.lower() and sanitized_album in item.lower():
                                print(f"Deemix: Match found: {item}")
                                for root, _, files in os.walk(item_path):
                                    for filename in files:
                                        if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                                            downloaded_files.append(os.path.join(root, filename))
                                print(f"Deemix: Found {len(downloaded_files)} files in fallback directory.")
                                break
                        else:
                            print(f"Deemix: Skipping non-directory: {item}")
                except Exception as e:
                    print(f"Deemix: Error during fallback search: {e}")
            else:
                print(f"Deemix: Successfully found {len(downloaded_files)} downloaded files.")

            return downloaded_files
        except Exception as e:
            print(f"Error downloading album {album_info['artist']} - {album_info['album']} ({deezer_link}) with deemix: {e}")
            return None

    async def _download_album_streamrip(self, deezer_link: str, album_info):
        """Downloads an album using streamrip."""
        try:
            output_dir = self.temp_download_folder
            print(f"Streamrip: Using output directory: {output_dir}")
            if not os.path.exists(output_dir):
                print(f"WARNING: Output directory {output_dir} does not exist. Creating it.")
                os.makedirs(output_dir, exist_ok=True)
            print(f"Streamrip: Using config file: /root/.config/streamrip/config.toml")
            if not os.path.exists('/root/.config/streamrip/config.toml'):
                print("WARNING: Streamrip config file does not exist.")
            else:
                print("Streamrip: Config file exists.")
                print(f"Streamrip: Can read config?: {os.access('/root/.config/streamrip/config.toml', os.R_OK)}")
            print(f"Streamrip: Processing Deezer link: {deezer_link}")
            print("Streamrip: Loading config...")
            streamrip_config = Config("/root/.config/streamrip/config.toml")
            print("Streamrip: Creating client...")
            client = DeezerClient(config=streamrip_config)

            print("Streamrip: Logging in...")
            await client.login()
            print("Streamrip: Login successful.")

            album_id = deezer_link.split('/')[-1]
            print(f"Streamrip: Album ID: {album_id}")

            print("Streamrip: Setting up database...")
            rip_db = Database(downloads=Downloads("/app/temp_downloads/downloads.db"), failed=Failed("/app/temp_downloads/failed_downloads.db"))

            print("Streamrip: Creating pending album...")
            pending_album = PendingAlbum(id=album_id, client=client, config=streamrip_config, db=rip_db)

            print("Streamrip: Resolving album...")
            album = await pending_album.resolve()
            print(f"Streamrip: Resolve result: {album}")

            if album is None:
                print(f"ERROR: Skipping download for {album_info['artist']} - {album_info['album']} (Error resolving album).")
                return None

            print("Streamrip: Starting rip...")
            await album.rip()
            print("Streamrip: Rip completed.")

            # Find downloaded files
            downloaded_files = []
            output_dir = self.temp_download_folder
            print(f"Streamrip: Looking for downloaded files in {output_dir}")

            from utils import sanitize_filename
            sanitized_artist = sanitize_filename(album_info['artist']).lower()
            sanitized_album = sanitize_filename(album_info['album']).lower()
            print(f"Streamrip: Searching for directories with artist='{sanitized_artist}' and album='{sanitized_album}'")

            found_dir = False
            for root, _, files in os.walk(output_dir):
                dir_name = os.path.basename(root).lower()
                if sanitized_artist in dir_name and sanitized_album in dir_name:
                    print(f"Streamrip: Found matching directory: {root}")
                    found_dir = True
                    for filename in files:
                        if filename.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                            downloaded_files.append(os.path.join(root, filename))
                    print(f"Streamrip: Collected {len(downloaded_files)} audio files.")
                    break

            if not found_dir:
                print("Streamrip: No matching directory found in output dir.")

            if downloaded_files:
                print(f"Successfully downloaded album {album_info['artist']} - {album_info['album']} using streamrip")
                return downloaded_files
            else:
                print(f"ERROR: Successfully called rip() for album {album_info['artist']} - {album_info['album']}, but could not find the downloaded files in {output_dir}.")
                try:
                    print(f"Streamrip: Contents of {output_dir}:")
                    for item in os.listdir(output_dir):
                        item_path = os.path.join(output_dir, item)
                        if os.path.isdir(item_path):
                            print(f"  DIR: {item}")
                        else:
                            print(f"  FILE: {item}")
                except Exception as e:
                    print(f"Streamrip: Error listing output dir: {e}")
                return None

        except Exception as e:
            print(f"Error downloading album {album_info['artist']} - {album_info['album']} with streamrip: {e}")
            import traceback
            traceback.print_exc()
            return None
        finally:
            try:
                await client.session.close()
            except Exception as e:
                print(f"Error closing streamrip client session: {e}")

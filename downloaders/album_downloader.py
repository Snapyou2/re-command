import os
import subprocess
import asyncio
from streamrip.client import DeezerClient
from streamrip.media import Album, PendingAlbum
from streamrip.config import Config
from streamrip.db import Database, Downloads, Failed, Dummy
from tqdm import tqdm
import sys
import importlib
import config
import re
from apis.deezer_api import DeezerAPI

class AlbumDownloader:
    def __init__(self, tagger, album_recommendation_comment=None):
        self.tagger = tagger
        self.album_recommendation_comment = album_recommendation_comment
        # Initial load, will be reloaded dynamically
        self.temp_download_folder = config.TEMP_DOWNLOAD_FOLDER
        self.deezer_arl = config.DEEZER_ARL

    async def download_album(self, album_info, is_album_recommendation=False):
        """Downloads an album using the configured method."""
        # Reload config to get the latest DOWNLOAD_METHOD
        importlib.reload(config)
        current_download_method = config.DOWNLOAD_METHOD
        temp_download_folder = config.TEMP_DOWNLOAD_FOLDER
        deezer_arl = config.DEEZER_ARL

        print(f"Starting download for album: {album_info['artist']} - {album_info['album']}")
        deezer_link, deezer_album_data = await self._get_deezer_album_link(album_info)
        if not deezer_link:
            error_msg = "Album not found on Deezer!"
            print(error_msg)
            return {"status": "error", "message": error_msg}

        # Update album_info with canonical data straight from Deezer API response
        if deezer_album_data:
            if 'artist' in deezer_album_data and 'name' in deezer_album_data['artist']:
                album_info['artist'] = deezer_album_data['artist']['name']
                print(f"Updated artist to canonical Deezer name: {album_info['artist']}")
            if 'title' in deezer_album_data:
                album_info['album'] = deezer_album_data['title']
                print(f"Updated album title to canonical Deezer title: {album_info['album']}")
            if 'release_date' in deezer_album_data:
                album_info['release_date'] = deezer_album_data['release_date']
                print(f"Updated release date to canonical Deezer date: {album_info['release_date']}")
            # Also update album_art if available in deezer_album_data
            if 'cover_xl' in deezer_album_data:
                 album_info['album_art'] = deezer_album_data['cover_xl']
                 print(f"Updated album art URL from Deezer.")


        print(f"Found Deezer link: {deezer_link}")

        downloaded_files = []
        if current_download_method == "deemix":
            downloaded_files = self._download_album_deemix(deezer_link, album_info, temp_download_folder, deezer_arl)
        elif current_download_method == "streamrip":
            downloaded_files = await self._download_album_streamrip(deezer_link, album_info, temp_download_folder, deezer_arl)
        else:
            error_msg = f"Unknown DOWNLOAD_METHOD: {current_download_method}. Skipping download for {album_info['artist']} - {album_info['album']}."
            print(error_msg)
            return {"status": "error", "message": error_msg}

        if downloaded_files:
            album_id = deezer_link.split('/')[-1] # Extract album ID

            # Fetch tracklist from Deezer
            deezer_api_instance = DeezerAPI()
            deezer_tracks = await deezer_api_instance.get_deezer_album_tracks(album_id)

            track_title_map = {}
            if deezer_tracks:
                for track in deezer_tracks:
                    sanitized_deezer_title = self._sanitize_for_matching(track['title'])
                    track_title_map[sanitized_deezer_title] = track['title']
            else:
                print(f"WARNING: Could not retrieve tracklist from Deezer for album {album_info['album']}.")

            # Tag all tracks in the album
            for file_path in downloaded_files:
                base_filename = os.path.splitext(os.path.basename(file_path))[0]
                sanitized_local_filename = self._sanitize_for_matching(base_filename)
                
                current_track_title = base_filename # Default to filename if no match

                found_deezer_title = None
                
                # First strat: Direct match of sanitized local filename to sanitized Deezer titles
                if sanitized_local_filename in track_title_map:
                    found_deezer_title = track_title_map[sanitized_local_filename]
                else:
                    # Second strat: More robust matching by trying to remove artist/track number
                    temp_filename = base_filename
                    artist_to_match = album_info['artist']
                    
                    # Remove leading track number patterns
                    temp_filename = re.sub(r"^\d+\s*[-–—\.]\s*", "", temp_filename, 1)

                    # More flexible regex
                    artist_name_escaped = re.escape(artist_to_match)

                    # Even more flexible regex
                    artist_prefix_pattern = re.compile(fr"^{artist_name_escaped}\s*[-–—_.]?\s*", re.IGNORECASE)
                    temp_filename = artist_prefix_pattern.sub("", temp_filename, 1)

                    temp_filename = temp_filename.strip(' -_.') # Cleanup

                    sanitized_temp_filename = self._sanitize_for_matching(temp_filename)

                    if sanitized_temp_filename in track_title_map:
                        found_deezer_title = track_title_map[sanitized_temp_filename]
                    else:
                        # Fallback w/ simple substring search
                        for deezer_orig_title, deezer_san_title in track_title_map.items():
                            if deezer_san_title in sanitized_temp_filename or sanitized_temp_filename in deezer_san_title:
                                found_deezer_title = deezer_orig_title
                                break

                if found_deezer_title:
                    current_track_title = found_deezer_title
                    print(f"Matched local file '{os.path.basename(file_path)}' to Deezer title: '{current_track_title}'")
                else:
                    print(f"WARNING: Could not find matching Deezer title for file '{os.path.basename(file_path)}'. Using cleaned filename as title.")
                    cleaned_fallback_title = re.sub(r"^\d+\s*[-–—\.]\s*", "", base_filename, 1)
                    cleaned_fallback_title = re.sub(r"^\s*[-–—]\s*", "", cleaned_fallback_title, 1)
                    cleaned_fallback_title = re.sub(r"^\s*{}\s*[-–—_.]?\s*".format(re.escape(album_info['artist'])), "", cleaned_fallback_title, 1, flags=re.IGNORECASE) # Remove artist from filename
                    cleaned_fallback_title = cleaned_fallback_title.strip(' -.')
                    current_track_title = cleaned_fallback_title if cleaned_fallback_title else base_filename


                self.tagger.tag_track(
                    file_path,
                    album_info['artist'],
                    current_track_title,
                    album_info['album'],
                    album_info['release_date'],
                    "",  # recording_mbid sadly not available
                    "Fresh Releases",
                    album_info.get('album_art'),
                    is_album_recommendation=is_album_recommendation
                )
            return {"status": "success", "files": downloaded_files}
        else:
            error_msg = f"Failed to download album {album_info['artist']} - {album_info['album']}."
            print(error_msg)
            return {"status": "error", "message": error_msg}

    async def _get_deezer_album_link(self, album_info):
        """Fetches Deezer album link."""
        deezer_api = DeezerAPI()
        link, deezer_album_data = await deezer_api.get_deezer_album_link(album_info['artist'], album_info['album'])
        return link, deezer_album_data

    def _sanitize_for_matching(self, s):
        """Sanitizes strings for comparison: lowercase, remove non-alphanumeric, etc."""
        s = s.lower()
        s = s.replace('’', "'")
        s = s.replace('ø', 'o')
        s = s.replace('é', 'e')
        s = re.sub(r'[^\w\s]', '', s)
        s = re.sub(r'\s+', ' ', s)
        return s.strip()

    def _download_album_deemix(self, deezer_link, album_info, temp_download_folder, deezer_arl):
        """Downloads an album using deemix."""
        try:
            output_dir = temp_download_folder
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
            env = os.environ.copy()
            env['XDG_CONFIG_HOME'] = '/root/.config'
            env['HOME'] = '/root'

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
                # Fallback w/ directories with artist and album names
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

            if downloaded_files:
                # Fix permissions
                album_dir = os.path.dirname(downloaded_files[0])
                os.system(f'chown -R 1000:1000 "{album_dir}"')

            return downloaded_files
        except Exception as e:
            print(f"Error downloading album {album_info['artist']} - {album_info['album']} ({deezer_link}) with deemix: {e}")
            return None

    async def _download_album_streamrip(self, deezer_link: str, album_info, temp_download_folder, deezer_arl):
        """Downloads an album using streamrip."""
        try:
            output_dir = temp_download_folder
            print(f"Streamrip: Using output directory: {output_dir}")
            if not os.path.exists(output_dir):
                print(f"WARNING: Output directory {output_dir} does not exist. Creating it.")
                os.makedirs(output_dir, exist_ok=True)
            print(f"Streamrip: Using config file: /root/.config/streamrip/config.toml")
            streamrip_config = Config("/root/.config/streamrip/config.toml")
            
            client = DeezerClient(config=streamrip_config)

            print("Streamrip: Logging in...")
            await client.login()
            print("Streamrip: Login successful.")

            album_id = deezer_link.split('/')[-1]
            print(f"Streamrip: Album ID: {album_id}")

            print("Streamrip: Setting up database...")
            # Use Dummy DB so streamrip never skips previously-seen tracks
            rip_db = Database(downloads=Dummy(), failed=Dummy())

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
            output_dir = temp_download_folder
            print(f"Streamrip: Looking for downloaded files in {output_dir}")

            # Look for any directory containing audio files
            found_dir = None
            for root, dirs, files in os.walk(output_dir):
                # Skip the root directory itself
                if root == output_dir:
                    continue

                audio_files = [f for f in files if f.endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma"))]
                if audio_files:
                    found_dir = root
                    print(f"Streamrip: Found directory with audio files: {root}")
                    for filename in audio_files:
                        downloaded_files.append(os.path.join(root, filename))
                    print(f"Streamrip: Collected {len(audio_files)} audio files.")
                    break

            if not found_dir:
                print("Streamrip: No directory with audio files found in output dir.")
                try:
                    print(f"Streamrip: Contents of {output_dir}:")
                    for item in os.listdir(output_dir):
                        item_path = os.path.join(output_dir, item)
                        if os.path.isdir(item_path):
                            print(f"  DIR: {item}")
                            # List contents of subdirs
                            try:
                                subitems = os.listdir(item_path)
                                for subitem in subitems[:5]:
                                    print(f"    {subitem}")
                                if len(subitems) > 5:
                                    print(f"    ... and {len(subitems) - 5} more items")
                            except Exception as e:
                                print(f"    Error listing: {e}")
                        else:
                            print(f"  FILE: {item}")
                except Exception as e:
                    print(f"Streamrip: Error listing output dir: {e}")

            if downloaded_files:
                print(f"Successfully downloaded album {album_info['artist']} - {album_info['album']} using streamrip")
                # Fix permissions
                os.system(f'chown -R 1000:1000 "{found_dir}"')
                return downloaded_files
            else:
                print(f"ERROR: Successfully called rip() for album {album_info['artist']} - {album_info['album']}, but could not find the downloaded files in {output_dir}.")
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

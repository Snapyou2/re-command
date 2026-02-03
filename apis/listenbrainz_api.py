import requests
import time
import os
import asyncio
import concurrent.futures
import sys
from streamrip.client import DeezerClient
from mutagen.id3 import ID3, COMM
from apis.deezer_api import DeezerAPI
from config import PLAYLIST_HISTORY_FILE, FRESH_RELEASES_CACHE_DURATION

class ListenBrainzAPI:
    def __init__(self, root_lb, token_lb, user_lb, listenbrainz_enabled):
        self._root_lb = root_lb
        self._token_lb = token_lb
        self._user_lb = user_lb
        self._listenbrainz_enabled = listenbrainz_enabled
        self.playlist_history_file = PLAYLIST_HISTORY_FILE
        self._fresh_releases_cache = None
        self._fresh_releases_cache_timestamp = 0

    @property
    def root_lb(self):
        return self._root_lb

    @property
    def token_lb(self):
        return self._token_lb

    @property
    def user_lb(self):
        return self._user_lb

    @property
    def auth_header_lb(self):
        return {"Authorization": f"Token {self.token_lb}"}

    def _get_last_playlist_name(self):
        """Retrieves the last playlist name from the history file."""
        try:
            with open(self.playlist_history_file, "r") as f:
                return f.readline().strip()
        except FileNotFoundError:
            return None

    def _save_playlist_name(self, playlist_name):
        """Saves the playlist name to the history file."""
        try:
            with open(self.playlist_history_file, "w") as f:
                f.write(playlist_name)
        except OSError as e:
            print(f"Error saving playlist name to file: {e}")

    async def has_playlist_changed(self):
        """Checks if the playlist has changed since the last run asynchronously."""
        current_playlist_name = await self.get_latest_playlist_name()
        last_playlist_name = self._get_last_playlist_name()

        if current_playlist_name == last_playlist_name:
            return False

        self._save_playlist_name(current_playlist_name)
        return True

    async def get_latest_playlist_name(self):
        """Retrieves the name of the latest *recommendation* playlist from ListenBrainz asynchronously."""
        playlist_json = await self._get_recommendation_playlist(self.user_lb)

        for playlist in playlist_json["playlists"]:
            if playlist["playlist"]["title"].startswith(f"Weekly Exploration for {self.user_lb}"):
                latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                latest_playlist = await self._get_playlist_by_mbid(latest_playlist_mbid)
                return latest_playlist['playlist']['title']

        print("Error: 'Weekly Exploration' playlist not found.")
        return None

    async def _make_request_with_retries(self, method, url, headers, params=None, json=None, max_retries=5, retry_delay=5):
        """
        Makes an HTTP request with retry logic for connection errors, asynchronously.
        """
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = await loop.run_in_executor(None, lambda: requests.get(url, headers=headers, params=params))
                elif method == "POST":
                    response = await loop.run_in_executor(None, lambda: requests.post(url, headers=headers, json=json))
                elif method == "HEAD":
                    response = await loop.run_in_executor(None, lambda: requests.head(url, headers=headers, params=params))
                response.raise_for_status()
                return response
            except requests.exceptions.ConnectionError as e:
                print(f"ListenBrainz API: Connection error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.Timeout as e:
                print(f"ListenBrainz API: Timeout error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.HTTPError as e:
                print(f"ListenBrainz API: HTTP error on attempt {attempt + 1}/{max_retries} to {url}: {e.response.status_code} - {e.response.text}", file=sys.stderr)
                raise
            except requests.exceptions.RequestException as e:
                print(f"ListenBrainz API: General request error on attempt {attempt + 1}/{max_retries} to {url}: {e}", file=sys.stderr)
                raise
        return None

    async def _get_recommendation_playlist(self, username, **params):
        """Fetches the recommendation playlist from ListenBrainz asynchronously."""
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/user/{username}/playlists/recommendations",
            params=params,
            headers=self.auth_header_lb,
        )
        return response.json()

    async def _get_playlist_by_mbid(self, playlist_mbid, **params):
        """Fetches a playlist by its MBID from ListenBrainz asynchronously."""
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/playlist/{playlist_mbid}",
            params=params,
            headers=self.auth_header_lb,
        )
        return response.json()

    async def get_recording_mbid_from_track(self, artist, title):
        """Fetches recording MBID from MusicBrainz using artist and title."""
        headers = {
            'User-Agent': 'TrackDrop/1.0'
        }
        url = f"https://musicbrainz.org/ws/2/recording/?query=artist:\"{artist}\" AND recording:\"{title}\"&fmt=json"
        try:
            response = await self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers,
                retry_delay=2
            )
            data = response.json()
            if data.get("recordings") and len(data["recordings"]) > 0:
                return data["recordings"][0].get("id")
        except Exception as e:
            print(f"Error getting MBID for {artist} - {title}: {e}", file=sys.stderr)
        return None

    async def get_artist_mbids_from_recording(self, recording_mbid):
        """Fetches artist MBIDs from a recording MBID via MusicBrainz API.
        Returns a list of artist MBID strings, or empty list on failure."""
        headers = {
            'User-Agent': 'TrackDrop/1.0'
        }
        url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?fmt=json&inc=artist-credits"
        try:
            response = await self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers,
                retry_delay=2
            )
            data = response.json()
            artist_mbids = []
            for credit in data.get("artist-credit", []):
                artist_obj = credit.get("artist", {})
                if artist_obj.get("id"):
                    artist_mbids.append(artist_obj["id"])
            return artist_mbids
        except Exception as e:
            print(f"Error getting artist MBIDs for recording {recording_mbid}: {e}", file=sys.stderr)
        return []

    async def lookup_mbids(self, artist, title, recording_mbid=None):
        """Look up recording MBID and artist MBIDs for a track.
        If recording_mbid is already known, skips the search step.
        Returns (recording_mbid, artist_mbids_list). Respects MusicBrainz rate limit (1 req/sec)."""
        import asyncio
        if not recording_mbid:
            recording_mbid = await self.get_recording_mbid_from_track(artist, title)
            await asyncio.sleep(1.1)  # MusicBrainz rate limit

        artist_mbids = []
        if recording_mbid:
            artist_mbids = await self.get_artist_mbids_from_recording(recording_mbid)
            await asyncio.sleep(1.1)  # MusicBrainz rate limit

        return recording_mbid, artist_mbids

    async def get_track_info(self, recording_mbid):
        """Fetches track information from MusicBrainz asynchronously."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        url = f"https://musicbrainz.org/ws/2/recording/{recording_mbid}?fmt=json&inc=artist-credits+releases"

        try:
            response = await self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers,
                retry_delay=2
            )
            data = response.json()
            artist_credit = data["artist-credit"][0]
            artist = artist_credit["name"]
            title = data["title"]
            if data["releases"]:
                album = data["releases"][0]["title"]
                release_date = data["releases"][0].get("date")
                release_mbid = data["releases"][0]["id"]
            else:
                album = "Unknown Album"
                release_date = None
                release_mbid = None
            return artist, title, album, release_date, release_mbid
        except requests.exceptions.RequestException as e:
            print(f"Error getting track info for {recording_mbid}: {e}", file=sys.stderr)
            return None, None, None, None, None

    async def get_listenbrainz_recommendations(self):
        """Fetches recommended songs from ListenBrainz and returns them as a list."""
        if not self._listenbrainz_enabled:
            return []

        print("\nChecking for new ListenBrainz recommendations...")
        print("\n")
        print("       @                                                                                                                    ")
        print("   @@@@@ @@@@@      @@@      @@@@          @@@@                      @@@@@@@@                   @@@                         ")
        print(" @@@@@@@ @@@@@@     @@@              @@    @@@@@    @@@    @   @@    @@@   @@@  @   @     @              @@    @@    @      ")
        print(" @@@@@@@ @@@@@@     @@@      @@@  @@@ @@@@ @@@@@ @@@@ @@@  @@@@@@@@  @@@ @@@@   @@@@@@@@@  @@@  @@@ @@@@@@@@@ @@@@@@@@      ")
        print(" @@@@@@@ @@@@@@     @@@      @@@  @@@@@     @@   @@@@@@@@@ @@   @@@  @@@ @ @@@  @@@      @@@@@  @@@  @@   @@@    @@@        ")
        print(" @@@@@@@ @@@@@@     @@@      @@@       @@@  @@   @@@       @@   @@@  @@     @@@ @@@   @@@   @@  @@@  @@   @@@   @@          ")
        print("    @@@@ @@@        @@@@@@@@ @@@@ @@@@@@@   @@@@  @@@@@@@  @@@  @@@  @@@@@@@@   @@@   @@@@@@@@  @@@ @@@   @@@ @@@@@@@@      ")
        print("       @                                                                                                                    ")
        print("                                                                                                                            ")

        try:
            latest_playlist_name = await self.get_latest_playlist_name()

            if latest_playlist_name is None:
                print("Error: Could not retrieve the latest ListenBrainz playlist name.")
                return []

            playlist_json = await self._get_recommendation_playlist(self.user_lb)

            latest_playlist_mbid = None
            for playlist in playlist_json["playlists"]:
                if playlist["playlist"]["title"] == latest_playlist_name:
                    latest_playlist_mbid = playlist["playlist"]["identifier"].split("/")[-1]
                    break

            if latest_playlist_mbid is None:
                print(f"Error: Could not find ListenBrainz playlist with name '{latest_playlist_name}'.")
                return []

            latest_playlist = await self._get_playlist_by_mbid(latest_playlist_mbid)

            tracks = latest_playlist["playlist"]["track"]
            
            # Create a list of tasks for processing each track
            processing_tasks = [self._process_track_for_recommendations(track) for track in tracks]
            
            # Run all tasks concurrently
            recommended_songs = await asyncio.gather(*processing_tasks)

            return recommended_songs
        except Exception as e:
            print(f"ERROR in get_listenbrainz_recommendations: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return []

    async def _process_track_for_recommendations(self, track):
        """Helper to fetch full track details and album art for a single track asynchronously."""
        artist = track.get("creator", "Unknown Artist")
        title = track.get("title", "Unknown Title")
        album = track.get("album", "Unknown Album")
        
        recording_mbid = None
        release_mbid_found = None
        caa_id = None

        # Extract recording MBID
        if "identifier" in track:
            identifiers = track["identifier"]
            if isinstance(identifiers, list) and identifiers:
                for ident in identifiers:
                    if isinstance(ident, str) and ident.startswith("https://musicbrainz.org/recording/"):
                        recording_mbid = ident.split("/")[-1]
                        break
            elif isinstance(identifiers, str) and identifiers.startswith("https://musicbrainz.org/recording/"):
                recording_mbid = identifiers.split("/")[-1]

        if not recording_mbid:
            for field in ["id", "recording_mbid", "mbid"]:
                value = track.get(field)
                if value and value != "null" and value is not None:
                    recording_mbid = value
                    break

        # Extract release MBID and CAA ID directly from ListenBrainz data if available
        if "extension" in track and "https://musicbrainz.org/doc/jspf#track" in track["extension"]:
            mb_extension = track["extension"]["https://musicbrainz.org/doc/jspf#track"]
            if "additional_metadata" in mb_extension:
                additional_metadata = mb_extension["additional_metadata"]
                if "caa_release_mbid" in additional_metadata and additional_metadata["caa_release_mbid"] and additional_metadata["caa_release_mbid"] != "null":
                    release_mbid_found = str(additional_metadata["caa_release_mbid"])
                if "caa_id" in additional_metadata and additional_metadata["caa_id"] and additional_metadata["caa_id"] != "null":
                    caa_id = str(additional_metadata["caa_id"])
        
        # Fallback for release_mbid if not found in extension
        if not release_mbid_found:
            if "release_mbid" in track and track["release_mbid"] and track["release_mbid"] != "null":
                release_mbid_found = track["release_mbid"]
            elif "caa_release_mbid" in track and track["caa_release_mbid"] and track["caa_release_mbid"] != "null":
                release_mbid_found = track["caa_release_mbid"]

        fetched_artist, fetched_title, fetched_album, release_date, musicbrainz_release_mbid = \
            artist, title, album, None, release_mbid_found # Default values

        # Pass caa_release_mbid and caa_id to the frontend for album art loading
        return {
            "artist": artist,
            "title": title,
            "album": album,
            "release_date": release_date,
            "recording_mbid": recording_mbid,
            "source": "ListenBrainz",
            "caa_release_mbid": release_mbid_found,
            "caa_id": caa_id
        }

    async def get_fresh_releases(self, sort="release_date", past=True, future=False):
        """Fetches fresh releases for the user from ListenBrainz asynchronously."""
        params = {
            "sort": sort,
            "past": str(past).lower(),
            "future": str(future).lower()
        }
        
        if self._fresh_releases_cache and (time.time() - self._fresh_releases_cache_timestamp) < FRESH_RELEASES_CACHE_DURATION:
            print(f"Returning cached fresh releases (cached at {time.ctime(self._fresh_releases_cache_timestamp)})")
            return self._fresh_releases_cache

        print("Fetching fresh releases from ListenBrainz API...")
        response = await self._make_request_with_retries(
            method="GET",
            url=f"{self.root_lb}/1/user/{self.user_lb}/fresh_releases",
            headers=self.auth_header_lb,
            params=params
        )
        data = response.json()
        releases = data.get('payload', {}).get('releases', [])

        # Sort by release_date (descending) and then confidence (descending)
        releases.sort(key=lambda x: (x.get('release_date', ''), x.get('confidence', 0)), reverse=True)
        latest_10_releases = releases[:10]

        # Album art is fetched by the frontend
        for release in latest_10_releases:
            release['album_art'] = None 
        
        result = {'payload': {'releases': latest_10_releases}}
        self._fresh_releases_cache = result
        self._fresh_releases_cache_timestamp = time.time()
        print(f"Cached fresh releases at {time.ctime(self._fresh_releases_cache_timestamp)}")
        
        return result

    async def get_weekly_scrobbles(self, count=200):
        """Fetches the user's scrobbles from the last 7 days."""
        if not self._listenbrainz_enabled:
            return []

        now = int(time.time())
        one_week_ago = now - (7 * 24 * 60 * 60)

        params = {
            "min_ts": one_week_ago,
            "max_ts": now,
            "count": count
        }
        url = f"{self.root_lb}/1/user/{self.user_lb}/listens"
        
        try:
            response = await self._make_request_with_retries("GET", url, headers=self.auth_header_lb, params=params)
            data = response.json()
            listens = data.get('payload', {}).get('listens', [])
            scrobbles = [{'artist': listen['track_metadata']['artist_name'], 'track': listen['track_metadata']['track_name']} for listen in listens]
            return scrobbles
        except Exception as e:
            print(f"Error fetching weekly scrobbles from ListenBrainz: {e}", file=sys.stderr)
            return []



    async def submit_feedback(self, recording_mbid, score):
        """Submits feedback for a recording to ListenBrainz."""
        payload = {"recording_mbid": recording_mbid, "score": score}
        url = f"{self.root_lb}/1/feedback/recording-feedback"

        print(f"Submitting feedback to {url}")
        print(f"Payload: {payload}")
        print(f"Headers: {self.auth_header_lb}")

        try:
            response = await self._make_request_with_retries(
                method="POST",
                url=url,
                headers=self.auth_header_lb,
                json=payload
            )
            print(f"Response status: {response.status_code}")
            print(f"Response text: {response.text}")
            print(f"Feedback submitted for {recording_mbid}: {score}")
        except Exception as e:
            print(f"Error submitting feedback: {e}")
            raise

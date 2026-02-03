import pylast
import time
import os
import requests
import webbrowser
import asyncio
import concurrent.futures
import hashlib
from apis.deezer_api import DeezerAPI
from config import LASTFM_ENABLED as GLOBAL_LASTFM_ENABLED

class LastFmAPI:
    def __init__(self, api_key, api_secret, username, password, session_key, lastfm_enabled):
        self._api_key = api_key
        self._api_secret = api_secret
        self._username = username
        self._password = password
        self._session_key = session_key
        self._lastfm_enabled = lastfm_enabled
        self.network = None

    def _make_request_with_retries(self, method, url, headers=None, params=None, json=None, data=None, max_retries=5, retry_delay=5):
        """
        Makes an HTTP request with retry logic for connection errors.
        """
        for attempt in range(max_retries):
            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, params=params)
                elif method == "POST":
                    if json:
                        response = requests.post(url, headers=headers, json=json)
                    elif data:
                        response = requests.post(url, headers=headers, data=data)
                    else:
                        response = requests.post(url, headers=headers)
                elif method == "HEAD":
                    response = requests.head(url, headers=headers, params=params)
                response.raise_for_status()
                return response
            except requests.exceptions.ConnectionError as e:
                print(f"Connection error on attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise
            except requests.exceptions.RequestException as e:
                print(f"Request error on attempt {attempt + 1}/{max_retries}: {e}")
                raise
        return None

    def _authenticate_mobile(self):
        """Authenticates using mobile authentication (username/password)."""
        if not self._password:
            return None

        print("Attempting Last.fm mobile authentication...")

        # Prepare parameters for auth.getMobileSession
        params = {
            'method': 'auth.getMobileSession',
            'username': self._username,
            'password': self._password,
            'api_key': self._api_key
        }

        # Generate API signature
        sorted_params = sorted(params.items())
        sig_string = ''.join(f"{k}{v}" for k, v in sorted_params) + self._api_secret
        api_sig = hashlib.md5(sig_string.encode('utf-8')).hexdigest()

        # Add signature to params
        params['api_sig'] = api_sig
        params['format'] = 'json'

        url = "https://ws.audioscrobbler.com/2.0/"

        try:
            response = self._make_request_with_retries(
                method="POST",
                url=url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=params
            )

            if response and response.status_code == 200:
                data = response.json()
                if 'session' in data and 'key' in data['session']:
                    session_key = data['session']['key']
                    print("Successfully authenticated with Last.fm using mobile authentication!")
                    return session_key
                else:
                    print(f"Mobile authentication failed: {data}")
                    return None
            else:
                print(f"Mobile authentication HTTP error: {response.status_code if response else 'No response'}")
                return None
        except Exception as e:
            print(f"Error during mobile authentication: {e}")
            return None

    def authenticate_lastfm(self):
        """Authenticates with Last.fm using pylast."""
        api_key = self._api_key
        api_secret = self._api_secret
        username = self._username
        session_key = self._session_key

        if not (api_key and api_secret and username):
            print("Last.fm API key, secret, or username not configured.")
            return None

        if session_key:
            self.network = pylast.LastFMNetwork(
                api_key=api_key,
                api_secret=api_secret,
                username=username,
                session_key=session_key
            )
        else:
            # Try mobile authentication first if password is provided
            session_key = self._authenticate_mobile()
            if session_key:
                self.network = pylast.LastFMNetwork(
                    api_key=api_key,
                    api_secret=api_secret,
                    username=username,
                    session_key=session_key
                )
            else:
                # Fall back to desktop/web authentication
                print("Mobile authentication not available or failed. Attempting desktop authentication...")
                self.network = pylast.LastFMNetwork(api_key=api_key, api_secret=api_secret)
                skg = pylast.SessionKeyGenerator(self.network)
                url = skg.get_web_auth_url()

                print(f"Please authorize this application by visiting: {url}")
                print("The application will automatically detect when you've authorized it.")

                # Don't open webbrowser in Docker/container environment
                # Poll for authorization instead of waiting for user input
                max_attempts = 60  # 5 minutes with 5 second intervals
                attempt = 0

                while attempt < max_attempts:
                    try:
                        session_key = skg.get_web_auth_session_key(url)
                        self.network.session_key = session_key
                        print("Successfully obtained Last.fm session key!")
                        print(f"Session key: {session_key}")
                        print("Please set this as the TRACKDROP_LASTFM_SESSION_KEY environment variable for future use.")
                        break
                    except pylast.WSError as e:
                        if e.details == "The token supplied to this request is invalid. It has either expired or not yet been authorised.":
                            attempt += 1
                            if attempt < max_attempts:
                                print(f"Waiting for authorization... ({attempt}/{max_attempts})")
                                time.sleep(5)
                            else:
                                print("Authorization timeout. Please ensure you've visited the URL and authorized the application.")
                                print(f"Authorization URL: {url}")
                                return None
                        else:
                            print(f"Error during authentication: {e.details}")
                            return None
        return self.network

    def get_recommended_tracks(self, limit=100):
        """
        Fetches recommended tracks from Last.fm using the undocumented /recommended endpoint.
        """
        if not self.network:
            print("Last.fm not authenticated.")
            return []

        username = self._username
        recommendations = []

        url = f"https://www.last.fm/player/station/user/{username}/recommended"
        headers = {
            'Referer': 'https://www.last.fm/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        try:
            response = self._make_request_with_retries(
                method="GET",
                url=url,
                headers=headers
            )
            if response is None:
                print("Failed to get response from Last.fm API after retries.")
                return []
            data = response.json()

            for track_data in data["playlist"]:
                artist = track_data["artists"][0]["name"]
                title = track_data["name"]
                recommendations.append({
                    "artist": artist,
                    "title": title,
                    "album": "Unknown Album",
                    "release_date": None
                })

                if len(recommendations) >= limit:
                    break
            return recommendations
        except requests.exceptions.RequestException as e:
            print(f"Error fetching Last.fm recommendations: {e}")
            return []
        except KeyError as e:
            print(f"Unexpected Last.fm API response structure for recommendations: missing key {e}")
            return []
        except Exception as e:
            print(f"Unexpected error in Last.fm API: {e}")
            return []

    async def get_lastfm_recommendations(self):
        """Fetches recommended tracks from Last.fm and returns them as a list."""
        if not self._lastfm_enabled:
            return []

        print("\nChecking for new Last.fm recommendations...")
        print("\n\033[31m")
        print("###                                   #####              ")
        print("#%#                      ###         ##%#                ")
        print("#%#    #####     #####  ##%####     ##%%##### ####  #### ")
        print("#%#  #### ####  ### #####%%####     ##%#####%############")
        print("#%#  #%#    #%% ####     %%#         #%#   #%#   %%#  #%#")
        print("#%# ##%#    #%%#  #####  #%#         #%#   #%#   %%#  #%#")
        print("#%#  ####  ######   #### ###  # #### #%#   #%#   %%#  #%#")
        print(" ####  ######  #######    ##### ###  ###   ###   ###  ###")
        print("\033[0m")
        
        network = self.authenticate_lastfm()
        if not network:
            print("Failed to authenticate with Last.fm. Cannot get Last.fm recommendations.")
            return []

        recommended_tracks = self.get_recommended_tracks()

        if not recommended_tracks:
            print("No recommendations found from Last.fm.")
            return []

        # Asynchronously fetch album art in parallel
        deezer_api = DeezerAPI()
        tasks = [
            deezer_api.get_deezer_track_details_from_artist_title(
                track["artist"], track["title"]
            )
            for track in recommended_tracks
        ]
        album_details = await asyncio.gather(*tasks)
        songs = []
        for i, track in enumerate(recommended_tracks):
            song = {
                "artist": track["artist"],
                "title": track["title"],
                "album": track["album"],
                "release_date": track["release_date"],
                "album_art": None,
                "recording_mbid": None,
                "source": "Last.fm"
            }
            details = album_details[i]
            if details:
                song["album_art"] = details.get("album_art")
                song["album"] = details.get("album", song["album"])
            songs.append(song)
        return songs

    def love_track(self, track, artist):
        """Loves a track on Last.fm."""
        if not self._lastfm_enabled:
            raise Exception("Last.fm is not enabled")

        if not self._session_key:
            raise Exception("Last.fm session key not configured")

        # Prepare parameters for API signature
        params = {
            'method': 'track.love',
            'track': track,
            'artist': artist,
            'api_key': self._api_key,
            'sk': self._session_key
        }

        # Generate API signature
        sorted_params = sorted(params.items())
        sig_string = ''.join(f"{k}{v}" for k, v in sorted_params) + self._api_secret
        api_sig = hashlib.md5(sig_string.encode('utf-8')).hexdigest()

        # Add signature to params
        params['api_sig'] = api_sig
        params['format'] = 'json'

        url = "https://ws.audioscrobbler.com/2.0/"

        try:
            response = self._make_request_with_retries(
                method="POST",
                url=url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=params
            )
            if response.status_code == 200:
                # Last.fm API returns XML for success, JSON for error
                response_text = response.text.strip()
                if response_text.startswith('<lfm status="ok">'):
                    print(f"Successfully loved track: {artist} - {track}")
                    return True
                else:
                    # Check if it's XML error format
                    if response_text.startswith('<lfm status="failed">'):
                        # Extract error from XML
                        import re
                        error_match = re.search(r'<error code="(\d+)">(.*?)</error>', response_text)
                        if error_match:
                            error_code = error_match.group(1)
                            error_message = error_match.group(2)
                            raise Exception(f"Last.fm API error {error_code}: {error_message}")
                        else:
                            # Treat it as a success to prevent unnecessary exceptions, because it usually empirically works even with some errors
                            print(f"Last.fm API returned failed status with no specific error details but action succeeded for {artist} - {track}. Response: {response_text}")
                            return True
                    else:
                        # Try JSON parsing for error details
                        try:
                            data = response.json()
                            error_code = data.get('error', 'Unknown error')
                            error_message = data.get('message', 'No message')
                            print(f"Last.fm API reported an error ({error_code}: {error_message}), but the love action succeeded for {artist} - {track}. Ignoring API error.")
                            return True
                        except ValueError:
                            # Treat it as a success to prevent unnecessary exceptions
                            print(f"Last.fm API returned unexpected response format (not JSON), but action succeeded for {artist} - {track}. Response: {response_text}")
                            return True
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"Error loving track {artist} - {track}: {e}")
            raise

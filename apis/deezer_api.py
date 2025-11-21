import requests
import time
import re

class DeezerAPI:
    def __init__(self):
        self.search_url = "https://api.deezer.com/search"
        self.track_url_base = "https://api.deezer.com/track/"

    def _clean_title(self, title):
        """Removes common suffixes from track titles to improve search accuracy."""
        # Remove content in parentheses or brackets that often indicates remix, live, etc.
        title = re.sub(r'\s*\(feat\..*?\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s*\[feat\..*?\]', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s*\([^)]*\)', '', title)
        title = re.sub(r'\s*\[[^\]]*\]', '', title)
        # Remove common suffixes
        suffixes = [
            " (Official Music Video)", " (Official Video)", " (Live)", " (Remix)",
            " (Extended Mix)", " (Radio Edit)", " (Acoustic)", " (Instrumental)",
            " (Lyric Video)", " (Visualizer)", " (Audio)", " (Album Version)",
            " (Single Version)", " (Original Mix)"
        ]
        for suffix in suffixes:
            if title.lower().endswith(suffix.lower()):
                title = title[:-len(suffix)]
        return title.strip()

    def _make_request_with_retries(self, url, params=None, max_retries=3, initial_delay=1):
        """Makes an HTTP GET request with retry logic and exponential backoff."""
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                print(f"Deezer API: Request error on attempt {attempt + 1}/{max_retries} to {url}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(initial_delay * (2 ** attempt))
                else:
                    raise
        return None

    def get_deezer_track_link(self, artist, title):
        """
        Searches for a track on Deezer and returns the track link.

        Args:
            artist: The artist name.
            title: The track title.

        Returns:
            The Deezer track link if found, otherwise None.
        """
        cleaned_title = self._clean_title(title)
        search_queries = [
            f'artist:"{artist}" track:"{cleaned_title}"',
            f'artist:"{artist}" track:"{title}"', # Original title as fallback
            f'artist:{artist} track:{cleaned_title}', # Without quotes
            f'artist:{artist} track:{title}' # Without quotes, original title
        ]

        for query in search_queries:
            params = {"q": query}
            try:
                response = self._make_request_with_retries(self.search_url, params=params)
                if response:
                    data = response.json()
                    if data.get('data') and len(data['data']) > 0:
                        return data['data'][0]['link']
            except Exception as e:
                print(f"Error during Deezer search with query '{query}': {e}")
        return None

    def get_deezer_track_details(self, track_id):
        """
        Fetches track details, including album name and cover, from Deezer using the track ID.

        Args:
            track_id: The Deezer track ID.

        Returns:
            A dictionary containing track details (including album name and cover) or None if an error occurs.
        """
        track_url = f"{self.track_url_base}{track_id}"
        try:
            response = self._make_request_with_retries(track_url)
            if response:
                data = response.json()

                if data and data.get("album") and data["album"].get("title"):
                    album_cover = data["album"].get("cover_xl", data["album"].get("cover_big", data["album"].get("cover_medium", data["album"].get("cover", None))))
                    return {
                        "album": data["album"]["title"],
                        "release_date": data.get("release_date"),
                        "album_art": album_cover
                    }
                else:
                    print(f"Album information not found for track ID {track_id}")
                    return None

        except requests.exceptions.RequestException as e:
            print(f"Error fetching track details from Deezer: {e}")
            return None
        except (KeyError, TypeError) as e:
            print(f"Unexpected Deezer track response structure for ID {track_id}: {e}")
            return None

    def get_deezer_album_art(self, artist, album_title):
        """
        Searches for an album on Deezer and returns the album cover URL.

        Args:
            artist: Artist name
            album_title: Album title

        Returns:
            Album cover URL or None
        """
        search_queries = [
            f'artist:"{artist}" album:"{album_title}"',
            f'artist:{artist} album:{album_title}',  # Without quotes
        ]

        for query in search_queries:
            params = {"q": query}
            try:
                response = self._make_request_with_retries(self.search_url + "/album", params=params)
                if response:
                    data = response.json()
                    if data.get('data') and len(data['data']) > 0:
                        album = data['data'][0]
                        cover = album.get("cover_xl", album.get("cover_big", album.get("cover_medium", album.get("cover", None))))
                        if cover:
                            return {
                                "album": album.get("title"),
                                "release_date": album.get("release_date"),
                                "album_art": cover
                            }
            except Exception as e:
                print(f"Error during Deezer album search with query '{query}': {e}")
        return None

    def get_deezer_track_details_from_artist_title(self, artist, title):
        """
        Fetches track details from Deezer using artist and title.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            Track details dict or None
        """
        # Try original search first
        link = self.get_deezer_track_link(artist, title)
        if link:
            track_id = link.split('/')[-1]
            details = self.get_deezer_track_details(track_id)
            if details:
                return details

        # Clean artist: remove featurings
        cleaned_artist = re.sub(r'\s*(?:feat\.?|featuring|ft\.?)\s*.*', '', artist, flags=re.IGNORECASE).strip()
        if cleaned_artist != artist:
            link = self.get_deezer_track_link(cleaned_artist, title)
            if link:
                track_id = link.split('/')[-1]
                details = self.get_deezer_track_details(track_id)
                if details:
                    return details
        return None

    def get_deezer_album_link(self, artist, album_title):
        """
        Searches for an album on Deezer and returns the album link.

        Args:
            artist: The artist name.
            album_title: The album title.

        Returns:
            The Deezer album link if found, otherwise None.
        """
        search_queries = [
            f'artist:"{artist}" album:"{album_title}"',
            f'artist:{artist} album:{album_title}',  # Without quotes
        ]

        for query in search_queries:
            params = {"q": query}
            try:
                response = self._make_request_with_retries(self.search_url + "/album", params=params)
                if response:
                    data = response.json()
                    if data.get('data') and len(data['data']) > 0:
                        return data['data'][0]['link']
            except Exception as e:
                print(f"Error during Deezer album search with query '{query}': {e}")
        return None

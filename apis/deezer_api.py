import requests

class DeezerAPI:
    def __init__(self):
        self.search_url = "https://api.deezer.com/search"
        self.track_url_base = "https://api.deezer.com/track/"

    def get_deezer_track_link(self, artist, title):
        """
        Searches for a track on Deezer and returns the track link.

        Args:
            artist: The artist name.
            title: The track title.

        Returns:
            The Deezer track link if found, otherwise None.
        """
        params = {
            "q": f'artist:"{artist}" track:"{title}"'
        }
        try:
            response = requests.get(self.search_url, params=params)
            response.raise_for_status()
            data = response.json()

            if data['data']:
                return data['data'][0]['link']
            else:
                return None

        except requests.exceptions.RequestException as e:
            print(f"Error searching Deezer: {e}")
            return None

    def get_deezer_track_details(self, track_id):
        """
        Fetches track details, including album name, from Deezer using the track ID.

        Args:
            track_id: The Deezer track ID.

        Returns:
            A dictionary containing track details (including album name) or None if an error occurs.
        """
        track_url = f"{self.track_url_base}{track_id}"
        try:
            response = requests.get(track_url)
            response.raise_for_status()
            data = response.json()

            if data and "album" in data and "title" in data["album"]:
                return {
                    "album": data["album"]["title"],
                    "release_date": data.get("release_date")
                }
            else:
                print(f"Album information not found for track ID {track_id}")
                return None

        except requests.exceptions.RequestException as e:
            print(f"Error fetching track details from Deezer: {e}")
            return None

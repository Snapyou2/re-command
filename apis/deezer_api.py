import requests
import time
import re
import sys
import asyncio
import os
import datetime

class DeezerAPI:
    def __init__(self):
        self.search_url = "https://api.deezer.com/search"
        self.track_url_base = "https://api.deezer.com/track/"
        self.log_file_path = "/app/deezer_api_debug.log"
        self._availability_cache = {}

    def _log_to_file(self, message):
        """Logs messages to a specified file."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    def _normalize_string(self, s):
        """Normalizes strings for comparison by replacing special characters."""
        s = s.lower()
        s = s.replace('’', "'") 
        s = s.replace('ø', 'o')
        s = s.replace('é', 'e')
        s = re.sub(r'\W+', ' ', s)
        return s.strip()

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

    async def _make_request_with_retries(self, url, params=None, max_retries=3, initial_delay=1):
        """Makes an HTTP GET request with retry logic and exponential backoff."""
        for attempt in range(max_retries):
            try:
                # Log the full URL being requested
                full_url = requests.Request('GET', url, params=params).prepare().url
                self._log_to_file(f"Deezer API: Attempt {attempt + 1}/{max_retries} - Requesting URL: {full_url}")

                response = await asyncio.to_thread(requests.get, url, params=params)
                response.raise_for_status()

                # Log the raw response content
                self._log_to_file(f"Deezer API: Response (status {response.status_code}): {response.text}")
                return response
            except requests.exceptions.RequestException as e:
                self._log_to_file(f"Deezer API: Request error on attempt {attempt + 1}/{max_retries} to {full_url}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(initial_delay * (2 ** attempt))
                else:
                    raise
        return None

    async def get_deezer_track_link(self, artist, title, album=None):
        """
        Searches for a track on Deezer and returns the track link.
        When album is provided, prefers the result from the matching album
        to avoid pulling the wrong version (e.g. remaster vs original).

        Args:
            artist: The artist name.
            title: The track title.
            album: Optional album name to prefer the correct version.

        Returns:
            The Deezer track link if found, otherwise None.
        """
        cleaned_title = self._clean_title(title)
        search_queries = [
            f'artist:"{artist}" track:"{cleaned_title}"',
            f'artist:"{artist}" track:"{title}"', # Original title as fallback
            f'artist:{artist} track:{cleaned_title}', # Without quotes
            f'artist:{artist} track:{title}', # Without quotes, original title
            f'{artist} {title}' # Broad search without specific field tags
        ]

        # If album is provided, also try album-specific queries first
        if album:
            cleaned_album = self._clean_title(album)
            search_queries = [
                f'artist:"{artist}" track:"{cleaned_title}" album:"{album}"',
                f'artist:"{artist}" track:"{cleaned_title}" album:"{cleaned_album}"',
            ] + search_queries

        for query in search_queries:
            params = {"q": query}
            try:
                response = await self._make_request_with_retries(self.search_url, params=params)
                if response:
                    data = response.json()
                    results = data.get('data', [])
                    if not results:
                        continue

                    # If we have an album hint, prefer the result from that album
                    if album and len(results) > 1:
                        album_lower = album.lower().strip()
                        for result in results:
                            result_album = result.get('album', {}).get('title', '').lower().strip()
                            if result_album == album_lower:
                                return result['link']

                    return results[0]['link']
            except Exception as e:
                print(f"Error during Deezer search with query '{query}': {e}")
        return None

    async def get_deezer_track_details(self, track_id):
        """
        Fetches track details, including album name and cover, from Deezer using the track ID.

        Args:
            track_id: The Deezer track ID.

        Returns:
            A dictionary containing track details (including album name and cover) or None if an error occurs.
        """
        track_url = f"{self.track_url_base}{track_id}"
        try:
            response = await self._make_request_with_retries(track_url)
            if response:
                data = response.json()

                if data and data.get("album") and data["album"].get("title"):
                    album_cover = data["album"].get("cover_xl", data["album"].get("cover_big", data["album"].get("cover_medium", data["album"].get("cover", None))))

                    # Extract individual artist names from contributors
                    contributors = data.get("contributors", [])
                    if contributors:
                        artist_list = [c.get("name", "") for c in contributors if c.get("name")]
                    else:
                        artist_list = [data.get("artist", {}).get("name", "")]

                    # Album artist: fetch from the album's own artist info if possible,
                    # otherwise use the track's primary artist
                    album_id = data["album"].get("id")
                    album_artist = data.get("artist", {}).get("name", artist_list[0] if artist_list else "")

                    return {
                        "album": data["album"]["title"],
                        "release_date": data.get("release_date"),
                        "album_art": album_cover,
                        "artists": artist_list,
                        "album_artist": album_artist,
                        "title": data.get("title", ""),
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

    async def get_deezer_album_art(self, artist, album_title):
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
                response = await self._make_request_with_retries(self.search_url + "/album", params=params)
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

    async def get_deezer_track_details_from_artist_title(self, artist, title):
        """
        Fetches track details from Deezer using artist and title.

        Args:
            artist: Artist name
            title: Track title

        Returns:
            Track details dict or None
        """
        # Try original search first
        link = await self.get_deezer_track_link(artist, title)
        if link:
            track_id = link.split('/')[-1]
            details = await self.get_deezer_track_details(track_id)
            if details:
                return details

        # Clean artist: remove featurings
        cleaned_artist = re.sub(r'\s*(?:feat\.?|featuring|ft\.?)\s*.*', '', artist, flags=re.IGNORECASE).strip()
        if cleaned_artist != artist:
            link = await self.get_deezer_track_link(cleaned_artist, title)
            if link:
                track_id = link.split('/')[-1]
                details = await self.get_deezer_track_details(track_id)
                if details:
                    return details
        return None

    async def get_deezer_track_preview(self, artist, title):
        """
        Searches for a track on Deezer and returns the preview URL.

        Args:
            artist: The artist name.
            title: The track title.

        Returns:
            The Deezer track preview URL if found, otherwise None.
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
                response = await self._make_request_with_retries(self.search_url, params=params)
                if response:
                    data = response.json()
                    if data.get('data') and len(data['data']) > 0:
                        track = data['data'][0]
                        preview_url = track.get('preview')
                        if preview_url:
                            return preview_url
            except Exception as e:
                print(f"Error during Deezer preview search with query '{query}': {e}")
        return None

    async def get_deezer_album_link(self, artist, album_title):
        """
        Searches for an album on Deezer and returns the album link.

        Args:
            artist: The artist name.
            album_title: The album title.

        Returns:
            The Deezer album link if found, otherwise None.
        """

        # Normalize and clean artist/album names for more flexible matching
        original_artist_lower = self._normalize_string(artist)
        original_album_lower = self._normalize_string(album_title)

        # Handle various forms of the artist name for queries
        cleaned_artist_for_query_strict = artist.replace('’', "'").replace('Ø', 'O')
        cleaned_artist_for_query_spaces = cleaned_artist_for_query_strict.replace('&', ' ')

        artists_split_original = []
        if '&' in artist:
            artists_split_original = [a.strip() for a in artist.split('&')]
        
        # Collect all artist variations for matching purposes
        all_artist_variations = {
            original_artist_lower,
            self._normalize_string(cleaned_artist_for_query_strict),
            self._normalize_string(cleaned_artist_for_query_spaces),
            self._normalize_string(artist.replace('&', 'and'))
        }
        for part in artists_split_original:
            all_artist_variations.add(self._normalize_string(part))
        
        # Prepare various search queries for robustness
        search_queries = []

        # 1. Original queries with and without quotes
        search_queries.append(f'artist:"{artist}" album:"{album_title}"')
        search_queries.append(f'artist:{artist} album:{album_title}')

        # 2. Queries with cleaned artist name (using different cleaning strategies for queries)
        if cleaned_artist_for_query_strict != artist:
            search_queries.append(f'artist:"{cleaned_artist_for_query_strict}" album:"{album_title}"')
            search_queries.append(f'artist:{cleaned_artist_for_query_strict} album:{album_title}')
        
        if cleaned_artist_for_query_spaces != artist and cleaned_artist_for_query_spaces != cleaned_artist_for_query_strict:
            search_queries.append(f'artist:"{cleaned_artist_for_query_spaces}" album:"{album_title}"')
            search_queries.append(f'artist:{cleaned_artist_for_query_spaces} album:{album_title}')

        # 3. Queries for split artists (if applicable)
        if len(artists_split_original) > 1:
            for part_artist in artists_split_original:
                search_queries.append(f'artist:"{part_artist}" album:"{album_title}"')
                search_queries.append(f'artist:{part_artist} album:{album_title}')
            
            # Search with '&' replaced by 'and'
            artist_and_replaced = artist.replace('&', 'and')
            search_queries.append(f'artist:"{artist_and_replaced}" album:"{album_title}"')
            search_queries.append(f'artist:{artist_and_replaced} album:{album_title}')
            
            # Explicitly URL encode & if some APIs prefer it this way
            artist_amp_encoded = artist.replace('&', '%26')
            search_queries.append(f'artist:"{artist_amp_encoded}" album:"{album_title}"')

        # 4. Broad search without specific field tags
        search_queries.append(f'{artist} {album_title}')
        search_queries.append(f'{cleaned_artist_for_query_spaces} {album_title}')
        
        # Use a set to store unique queries to avoid redundant API calls
        unique_search_queries = list(dict.fromkeys(search_queries))

        for query in unique_search_queries:
            params = {"q": query}
            self._log_to_file(f"Deezer API: Searching for album with query: '{query}'")
            try:
                response = await self._make_request_with_retries(self.search_url + "/album", params=params)
                if response:
                    data = response.json()
                    self._log_to_file(f"Deezer API: Response for album query '{query}': {data}")
                    if data.get('data') and len(data['data']) > 0:
                        first_result = data['data'][0]
                        found_album_title = self._normalize_string(first_result.get('title', ''))
                        found_artist_name = self._normalize_string(first_result.get('artist', {}).get('name', ''))

                        # Relaxed matching logic using normalized strings
                        album_title_match = (found_album_title == original_album_lower)

                        # Check if found artist name is an exact match for any of the normalized variations or if any part of the original normalized artist names is in the found artist name
                        artist_name_match = (
                            found_artist_name in original_artist_lower or 
                            any(found_artist_name == var for var in all_artist_variations) or 
                            any(var in found_artist_name for var in all_artist_variations if var)
                        )
                        
                        if album_title_match and artist_name_match:
                            self._log_to_file(f"Deezer API: Found a matching album link: {first_result['link']}")
                            return first_result['link'], first_result # Return both link and full result
                        else:
                            self._log_to_file(f"Deezer API: First result '{found_artist_name} - {found_album_title}' not a close enough match for normalized '{original_artist_lower} - {original_album_lower}'. Album match: {album_title_match}, Artist match: {artist_name_match}. Trying next query...")


            except Exception as e:
                self._log_to_file(f"Error during Deezer album search with query '{query}': {e}")
        return None, None # Return None for both link and result if not found

    async def get_deezer_album_tracks(self, album_id):
        """
        Fetches all tracks for a given Deezer album ID. Handles pagination.

        Args:
            album_id: The Deezer album ID.

        Returns:
            A list of track dictionaries, or None if an error occurs.
        """
        tracks = []
        next_page_url = f"https://api.deezer.com/album/{album_id}/tracks"
        
        while next_page_url:
            try:
                response = await self._make_request_with_retries(next_page_url)
                if response:
                    data = response.json()
                    if data.get('data'):
                        tracks.extend(data['data'])
                        next_page_url = data.get('next')
                    else:
                        break
                else:
                    break
            except Exception as e:
                return None

        if not tracks:
            return []

        return tracks

    async def get_deezer_album_tracklist_by_search(self, artist, album_title):
        """
        Searches for tracks belonging to a specific album and artist on Deezer.
        This is a fallback if the direct /album/{id}/tracks endpoint fails.

        Args:
            artist: The album artist name.
            album_title: The album title.

        Returns:
            A list of track dictionaries (each with 'artist', 'title', 'id'), or an empty list.
        """
        tracks = []
        search_queries = [
            f'artist:"{artist}" album:"{album_title}"',
            f'artist:{artist} album:{album_title}',
            f'{artist} {album_title}'
        ]

        # Use a set to store unique track IDs to avoid duplicates if multiple queries return the same track
        found_track_ids = set()

        for query in search_queries:
            params = {"q": query, "limit": 100}
            next_page_url = self.search_url + "/track"
            
            while next_page_url:
                try:
                    response = await self._make_request_with_retries(next_page_url, params=params if next_page_url == self.search_url + "/track" else None)
                    if response:
                        data = response.json()
                        if data.get('data'):
                            for track_item in data['data']:
                                # Only add tracks that are confirmed to be from this album by title matching and not already added
                                if track_item.get('album', {}).get('title', '').lower() == album_title.lower() and \
                                   track_item.get('id') not in found_track_ids:
                                    tracks.append({
                                        'id': str(track_item.get('id')),
                                        'title': track_item.get('title'),
                                        'artist': track_item.get('artist', {}).get('name')
                                    })
                                    found_track_ids.add(track_item.get('id'))
                            next_page_url = data.get('next')
                            params = None
                        else:
                            break
                    else:
                        break
                except Exception as e:
                    break

        return tracks

    async def check_album_download_availability(self, artist, album_title):
        """
        Checks if an album is available for download on Deezer by finding its link
        and then attempting to retrieve its tracklist.

        Args:
            artist: The artist name.
            album_title: The album title.

        Returns:
            True if the album is found and has at least one track, False otherwise.
        """
        cache_key = f"{artist}-{album_title}".lower()

        # Check cache only for 'True' results, which are effectively permanent
        if cache_key in self._availability_cache and self._availability_cache[cache_key] is True:
            self._log_to_file(f"DeezerAPI: Returning cached 'available' status for '{artist}' - '{album_title}'")
            return True

        self._log_to_file(f"DeezerAPI: Checking download availability for artist='{artist}', album='{album_title}' (not cached 'True' or new album)")
        is_available = False

        try:
            album_link, album_info = await self.get_deezer_album_link(artist, album_title)

            if album_link:
                album_id = album_link.split('/')[-1]
                tracks = await self.get_deezer_album_tracks(album_id)
                if tracks and len(tracks) > 0:
                    self._log_to_file(f"DeezerAPI: Album '{album_title}' by '{artist}' is available for download (found {len(tracks)} tracks).")
                    is_available = True
                else:
                    self._log_to_file(f"DeezerAPI: Album '{album_title}' by '{artist}' found on Deezer, but no tracks could be retrieved, so not considered available for download.")
            else:
                self._log_to_file(f"DeezerAPI: Album '{album_title}' by '{artist}' not found on Deezer, so not available for download.")
        except Exception as e:
            self._log_to_file(f"DeezerAPI: Error during availability check for album '{album_title}' by '{artist}': {e}. Not considered available.")
            is_available = False
        
        # Cache only if available is True (effectively permanent cache for available albums)
        if is_available:
            self._availability_cache[cache_key] = True
            self._log_to_file(f"DeezerAPI: Cached 'available' status for '{artist}' - '{album_title}'")
        
        return is_available

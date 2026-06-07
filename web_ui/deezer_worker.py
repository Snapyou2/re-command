"""
Subprocess worker for Deezer track/album downloads.
Spawned by QueueManager so the download can be killed on cancel.
Reads request from a JSON file, writes result to a status file.
"""
import sys, os, json, time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import *
from utils import initialize_streamrip_db, DeezerAuthError


def do_track(request):
    import asyncio
    from downloaders.track_downloader import TrackDownloader
    from utils import Tagger
    tagger = Tagger(request.get('album_recommendation_comment', ''))
    td = TrackDownloader(tagger)
    track_info = {
        'artist': request['artist'],
        'title': request['title'],
        'album': request.get('album', ''),
        'release_date': request.get('release_date', ''),
        'recording_mbid': request.get('recording_mbid', ''),
        'source': request.get('source', 'Manual'),
        'download_id': request['download_id'],
        'lb_recommendation': request.get('lb_recommendation', False),
    }
    return asyncio.run(td.download_track(track_info, lb_recommendation=request.get('lb_recommendation', False)))


def do_album(request):
    import asyncio
    from downloaders.album_downloader import AlbumDownloader
    from utils import Tagger
    tagger = Tagger(request.get('album_recommendation_comment', ''))
    ad = AlbumDownloader(tagger, request.get('album_recommendation_comment', ''))
    album_info = {
        'artist': request['artist'],
        'album': request['album'],
        'release_date': request.get('release_date', ''),
        'album_art': None,
        'download_id': request['download_id'],
    }
    return asyncio.run(ad.download_album(album_info, is_album_recommendation=request.get('is_album_recommendation', False)))


def main():
    request_file = sys.argv[1]
    with open(request_file) as f:
        req = json.load(f)

    download_id = req['download_id']
    status_dir = req['status_dir']
    music_library = req.get('music_library_path', '')
    temp_folder = req.get('temp_download_folder', '')
    cancel_file = os.path.join(status_dir, f"{download_id}.cancel")

    def write_status(s, msg, title='', ctrack='', ccount=None, tcount=None):
        os.makedirs(status_dir, exist_ok=True)
        data = {
            "status": s, "message": msg, "title": title or '',
            "current_track": ctrack or '', "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if ccount is not None:
            data["current_track_count"] = ccount
        if tcount is not None:
            data["total_track_count"] = tcount
        with open(os.path.join(status_dir, f"{download_id}.json"), 'w') as f:
            json.dump(data, f)

    if os.path.exists(cancel_file):
        write_status("failed", "Cancelled")
        return

    initialize_streamrip_db()

    try:
        mode = req.get('mode', 'track')
        if mode == 'track':
            result = do_track(req)
        elif mode == 'album':
            result = do_album(req)
        else:
            write_status("failed", f"Unknown mode: {mode}")
            return
    except Exception as e:
        if os.path.exists(cancel_file):
            write_status("failed", "Cancelled")
        else:
            write_status("failed", str(e))
        return

    if os.path.exists(cancel_file):
        write_status("failed", "Cancelled")
        return

    if result:
        write_status("completed", f"Downloaded successfully.")
        if music_library and temp_folder:
            try:
                from apis.navidrome_api import NavidromeAPI
                nav = NavidromeAPI(
                    root_nd="", user_nd="", password_nd="",
                    music_library_path=music_library,
                    target_comment="", lastfm_target_comment="",
                    listenbrainz_enabled=False, lastfm_enabled=False,
                )
                nav.organize_music_files(temp_folder, music_library)
            except Exception:
                pass
    else:
        write_status("failed", "Download failed.")


if __name__ == '__main__':
    main()

import os
import asyncio
import json
import threading
import time
import logging
import shutil

# Apply aiofiles 0.7.0 compat shim before any aioslsk imports
import aioslsk_compat  # noqa: F401

from aioslsk.client import SoulSeekClient
from aioslsk.shares.model import DirectoryShareMode
from aioslsk.settings import Settings, CredentialsSettings
from aioslsk.events import SearchResultEvent, TransferProgressEvent, TransferRemovedEvent
from aioslsk.transfer.state import TransferState

logger = logging.getLogger(__name__)


class SoulseekQueueManager:
    def __init__(self, username, password, download_dir, music_library_path,
                 search_timeout=15, keep_alive=True, min_quality=128,
                 share_music=False):
        self.username = username
        self.password = password
        self.download_dir = download_dir
        self.music_library_path = music_library_path
        self.search_timeout = search_timeout
        self.keep_alive = keep_alive
        self.min_quality = min_quality  # 0=lossless, 128, 192, 320
        self.share_music = share_music

        self._loop = None
        self._client = None
        self._thread = None
        self._queue = None
        self._running = False
        self._current_download_id = None
        self._current_transfer = None
        self._status_dir = "/tmp/recommand_download_status"
        self._parent_download_id = None
        self._parent_total = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._queue = asyncio.Queue()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._queue:
            self._queue.put_nowait(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.set_exception_handler(lambda loop, ctx: None)
        try:
            self._loop.run_until_complete(self._worker())
        finally:
            self._loop.close()

    async def _worker(self):
        while self._running:
            try:
                await self._ensure_client()
                break
            except Exception as e:
                logger.exception("failed to connect Soulseek, retrying in 10s: %s", e)
                await asyncio.sleep(10)

        queue_idle_since = None
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if item is None:
                    break
                queue_idle_since = None
                await self._ensure_client()
                await self._process_item(item)
            except asyncio.TimeoutError:
                if not self.keep_alive and self._client:
                    if queue_idle_since is None:
                        queue_idle_since = time.monotonic()
                    elif time.monotonic() - queue_idle_since > 30:
                        await self._disconnect_client()
                continue
            except Exception as e:
                logger.exception("queue worker error: %s", e)
        await self._disconnect_client()

    async def _ensure_client(self):
        if self._client is None:
            settings = Settings(
                credentials=CredentialsSettings(
                    username=self.username,
                    password=self.password,
                ),
                network=dict(upnp=dict(enabled=False)),
                shares=dict(
                    scan_on_start=False,
                    directories=[],
                    download=self.download_dir,
                ),
                searches=dict(sent=dict(request_timeout=120)),
            )
            self._client = SoulSeekClient(settings)
            await self._client.start()
            await self._client.login()
            # Suppress noisy Soulseek network logs
            logging.getLogger('aioslsk').setLevel(logging.CRITICAL)
            # Suppress distributed network logs instead of stopping it (needed for NAT traversal)
            logging.getLogger('aioslsk.distributed').setLevel(logging.CRITICAL)

            if self.share_music and self.music_library_path:
                if os.path.isdir(self.music_library_path):
                    logger.info("sharing music library: %s", self.music_library_path)
                    shared_dir = self._client.shares.add_shared_directory(
                        self.music_library_path,
                        share_mode=DirectoryShareMode.EVERYONE
                    )
                    await self._client.shares.scan_directory_files(shared_dir)
                    await self._client.shares.scan_directory_file_attributes(shared_dir)
                else:
                    logger.warning("music library path does not exist: %s", self.music_library_path)

    async def _disconnect_client(self):
        if self._client:
            try:
                await self._client.stop()
            except Exception:
                pass
            self._client = None

    def enqueue(self, download_id, artist, title, source="Manual",
                lb_recommendation=False, album="", release_date="",
                recording_mbid="", album_art=None,
                parent_download_id=None, parent_total=0):
        self._write_status(download_id, "queued", "Waiting in queue...",
                           f"{artist} - {title}",
                           parent_download_id=parent_download_id)
        item = {
            "download_id": download_id,
            "artist": artist,
            "title": title,
            "album": album,
            "release_date": release_date,
            "recording_mbid": recording_mbid,
            "album_art": album_art,
            "source": source,
            "lb_recommendation": lb_recommendation,
            "parent_download_id": parent_download_id,
            "parent_total": parent_total,
        }
        if self._queue:
            self._queue.put_nowait(item)

    def enqueue_many(self, items):
        for item in items:
            self.enqueue(**item)

    async def _is_cancelled(self, download_id):
        cancel_file = os.path.join(self._status_dir, f"{download_id}.cancel")
        return os.path.exists(cancel_file)

    async def _process_item(self, item):
        download_id = item["download_id"]
        artist = item["artist"]
        title = item["title"]
        parent_id = item.get("parent_download_id")
        parent_total = item.get("parent_total", 0)
        self._current_download_id = download_id

        # Show searching track in parent entry immediately
        if parent_id:
            parent_file = os.path.join(self._status_dir, f"{parent_id}.json")
            done_so_far = 0
            if os.path.exists(parent_file):
                try:
                    with open(parent_file) as f:
                        pd = json.load(f)
                    done_so_far = pd.get('current_track_count', 0)
                except Exception:
                    pass
            label = f"{artist} - {title}"
            self._write_status(
                parent_id, "in_progress",
                f"Searching for '{label}' ({done_so_far + 1}/{parent_total})...",
                current_track=label,
                current_track_count=done_so_far,
                total_track_count=parent_total,
            )

        # Check both own and parent cancel files
        if await self._is_cancelled(download_id):
            self._write_status(download_id, "failed", "Cancelled by user",
                               f"{artist} - {title}")
            return
        if parent_id:
            parent_cancel = os.path.join(self._status_dir, f"{parent_id}.cancel")
            if os.path.exists(parent_cancel):
                self._write_status(download_id, "failed", "Parent cancelled",
                                   f"{artist} - {title}", parent_download_id=parent_id)
                return

        self._write_status(download_id, "in_progress",
                           f"Searching Soulseek for '{artist} - {title}'...",
                           f"{artist} - {title}",
                           parent_download_id=parent_id)

        try:
            result = await self._search_and_download(
                artist, title, download_id
            )
            if result:
                self._organize()
                self._write_status(download_id, "completed",
                                   f"Downloaded: {os.path.basename(result)}",
                                   f"{artist} - {title}",
                                   parent_download_id=parent_id)
            else:
                self._write_status(download_id, "failed",
                                   "No suitable file found on Soulseek",
                                   f"{artist} - {title}",
                                   parent_download_id=parent_id)
        except Exception as e:
            logger.exception("download failed for %s - %s", artist, title)
            self._write_status(download_id, "failed", str(e),
                               f"{artist} - {title}",
                               parent_download_id=parent_id)
        finally:
            self._current_download_id = None
            self._current_transfer = None

        if parent_id:
            # Read current count from parent file to avoid stale _parent_completed
            parent_file = os.path.join(self._status_dir, f"{parent_id}.json")
            completed = 1
            if os.path.exists(parent_file):
                try:
                    with open(parent_file) as f:
                        pd = json.load(f)
                    prev = pd.get('current_track_count', 0)
                    completed = prev + 1
                except Exception:
                    pass
            label = f"{artist} - {title}"
            self._write_status(
                parent_id, "in_progress",
                f"Downloaded {completed}/{parent_total} tracks",
                current_track=label,
                current_track_count=completed,
                total_track_count=parent_total,
            )
            if completed >= parent_total:
                self._write_status(parent_id, "completed",
                                   f"Downloaded {completed} tracks")

    async def _search_and_download(self, artist, title, download_id):
        query = f"{artist} {title}"
        print(f"  Soulseek Queue: Searching for '{query}'...")

        request = await self._client.searches.search(query)
        await asyncio.sleep(self.search_timeout)

        if await self._is_cancelled(download_id):
            print(f"  Soulseek Queue: Cancelled after search for '{query}'")
            return None

        if not request.results:
            print(f"  Soulseek Queue: No results for '{query}'")
            return None

        best = self._pick_best_result(request.results, artist, title)
        if not best:
            print(f"  Soulseek Queue: No suitable match for '{query}'")
            return None

        if await self._is_cancelled(download_id):
            print(f"  Soulseek Queue: Cancelled before download for '{query}'")
            return None

        item = best.shared_items[0] if best.shared_items else None
        if not item:
            return None

        filename = item.filename
        remote_filename = os.path.basename(filename)
        print(f"  Soulseek Queue: Downloading '{remote_filename}' from {best.username}")

        self._write_status(download_id, "in_progress",
                           f"Downloading '{remote_filename}' from {best.username}...",
                           f"{artist} - {title}")

        transfer = await self._client.transfers.download(best.username, filename)
        self._current_transfer = transfer
        return await self._wait_for_download(transfer, remote_filename)

    def _pick_best_result(self, results, artist, title):
        scored = []
        artist_lower = artist.lower()
        title_lower = title.lower()

        SOULSEEK_BITRATE_PRIORITY = {320: 5, 256: 4, 192: 3, 160: 2, 128: 1}

        AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")

        for res in results[:50]:
            if not res.shared_items:
                continue
            item = res.shared_items[0]
            fname_lower = item.filename.lower()

            if not any(fname_lower.endswith(ext) for ext in AUDIO_EXTS):
                continue

            is_flac = fname_lower.endswith('.flac')

            bitrate = 0
            if hasattr(item, 'attributes') and item.attributes:
                for attr in item.attributes:
                    if getattr(attr, 'type', None) == 4 or getattr(attr, 'name', '').lower() == 'bitrate':
                        bitrate = getattr(attr, 'value', 0) or getattr(attr, 'int_value', 0)

            if self.min_quality == 0:
                if not is_flac:
                    continue
            elif self.min_quality > 0 and bitrate > 0 and bitrate < self.min_quality:
                continue

            score = 0
            if is_flac:
                score += 5
            if artist_lower in fname_lower:
                score += 3
            if title_lower in fname_lower:
                score += 5

            score += SOULSEEK_BITRATE_PRIORITY.get(bitrate, 0)

            if item.filesize and item.filesize > 0:
                score += 1

            scored.append((score, res, item))

        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    async def _wait_for_download(self, transfer, remote_filename):
        transfer_completed = asyncio.Event()
        transfer_failed = False

        def on_progress(event):
            nonlocal transfer_failed
            for t, prev, curr in event.updates:
                if t == transfer:
                    if curr.state == TransferState.State.COMPLETE:
                        transfer_completed.set()
                    elif curr.state in (TransferState.State.FAILED, TransferState.State.ABORTED):
                        transfer_failed = True
                        transfer_completed.set()

        def on_removed(event):
            nonlocal transfer_failed
            if event.transfer == transfer:
                if transfer.state.VALUE != TransferState.State.COMPLETE:
                    transfer_failed = True
                transfer_completed.set()

        unreg_progress = None
        unreg_removed = None
        try:
            unreg_progress = self._client.events.register(
                TransferProgressEvent, on_progress)
            unreg_removed = self._client.events.register(
                TransferRemovedEvent, on_removed)

            try:
                await asyncio.wait_for(transfer_completed.wait(), timeout=300)
            except asyncio.TimeoutError:
                print(f"  Soulseek Queue: Download timed out for '{remote_filename}'")
                return None

            if transfer_failed:
                print(f"  Soulseek Queue: Download failed for '{remote_filename}'")
                return None

            local_path = transfer.local_path
            if local_path and os.path.exists(local_path):
                if not local_path.lower().endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    print(f"  Soulseek Queue: Deleting non-music file: {local_path}")
                    os.remove(local_path)
                    return None
                print(f"  Soulseek Queue: Downloaded to {local_path}")
                return local_path

            print(f"  Soulseek Queue: File not found at {local_path}")
            return None
        finally:
            if unreg_progress:
                unreg_progress()
            if unreg_removed:
                unreg_removed()

    def _write_status(self, download_id, status, message, title=None,
                      current_track=None,
                      current_track_count=None, total_track_count=None,
                      parent_download_id=None):
        os.makedirs(self._status_dir, exist_ok=True)
        filepath = os.path.join(self._status_dir, f"{download_id}.json")
        data = {
            "status": status,
            "message": message,
            "title": title or "",
            "current_track": current_track or "",
            "current_track_count": current_track_count,
            "total_track_count": total_track_count,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if parent_download_id:
            data["parent_download_id"] = parent_download_id
        with open(filepath, "w") as f:
            json.dump(data, f)

    def _organize(self):
        try:
            from apis.navidrome_api import NavidromeAPI
            nav = NavidromeAPI(
                root_nd="", user_nd="", password_nd="",
                music_library_path=self.music_library_path,
                target_comment="", lastfm_target_comment="",
                listenbrainz_enabled=False, lastfm_enabled=False,
            )
            nav.organize_music_files(self.download_dir, self.music_library_path)
        except Exception as e:
            logger.exception("organize error: %s", e)

    def cancel(self, download_id):
        cancel_file = os.path.join(self._status_dir, f"{download_id}.cancel")
        with open(cancel_file, "w") as f:
            f.write("cancelled")
        # Write cancel files for all children of this parent
        if os.path.exists(self._status_dir):
            for fname in os.listdir(self._status_dir):
                if fname.endswith('.json'):
                    fpath = os.path.join(self._status_dir, fname)
                    try:
                        with open(fpath) as f:
                            data = json.load(f)
                        if data.get('parent_download_id') == download_id:
                            child_id = fname[:-5]
                            with open(os.path.join(self._status_dir, f"{child_id}.cancel"), 'w') as cf:
                                cf.write("cancelled")
                    except Exception:
                        pass
        # Abort active transfer if it belongs to this download (or its children)
        if self._current_transfer:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._client.transfers.abort(self._current_transfer),
                    self._loop
                )
            except Exception:
                pass

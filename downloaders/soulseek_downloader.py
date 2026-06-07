import os
import asyncio
import logging
import re

from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings
from aioslsk.events import SearchResultEvent, TransferProgressEvent, TransferRemovedEvent
from aioslsk.transfer.state import TransferState

logging.basicConfig(level=logging.WARNING)

SOULSEEK_BITRATE_PRIORITY = {320: 5, 256: 4, 192: 3, 160: 2, 128: 1}


class SoulseekDownloader:
    def __init__(self, username, password, download_dir, search_timeout=15, max_results=50):
        self.username = username
        self.password = password
        self.download_dir = download_dir
        self.search_timeout = search_timeout
        self.max_results = max_results

    async def create_client(self):
        settings = Settings(
            credentials=CredentialsSettings(
                username=self.username,
                password=self.password,
            ),
            network=dict(
                upnp=dict(enabled=False),
            ),
            shares=dict(
                scan_on_start=False,
                directories=[],
                download=self.download_dir,
            ),
            searches=dict(
                sent=dict(
                    request_timeout=120,
                ),
            ),
        )
        settings.transfers.report_interval = 1
        client = SoulSeekClient(settings)
        return client

    async def download_track(self, artist, title):
        client = await self.create_client()
        await client.start()
        await client.login()

        try:
            result = await self._search_and_download(client, artist, title)
            return result
        finally:
            try:
                await client.stop()
            except Exception:
                pass

    async def download_track_with_client(self, client, artist, title):
        return await self._search_and_download(client, artist, title)

    async def search_and_download_with_client(self, client, artist, title, search_timeout=15):
        query = f"{artist} {title}"
        print(f"  Soulseek: Searching for '{query}'...")

        try:
            request = await asyncio.wait_for(
                client.searches.search(query),
                timeout=search_timeout + 5
            )
        except asyncio.TimeoutError:
            print(f"  Soulseek: Search timed out for '{query}'")
            return None

        await asyncio.sleep(search_timeout)

        if not request.results:
            print(f"  Soulseek: No results for '{query}'")
            return None

        best = self._pick_best_result(request.results, artist, title)
        if not best:
            print(f"  Soulseek: No suitable match found for '{query}'")
            return None

        item = best.shared_items[0] if best.shared_items else None
        if not item:
            return None

        filename = item.filename
        remote_filename = os.path.basename(filename)
        print(f"  Soulseek: Downloading '{remote_filename}' from {best.username}")

        transfer = await client.transfers.download(best.username, filename)
        return await self._wait_for_download(client, transfer, remote_filename)

    async def _search_and_download(self, client, artist, title):
        query = f"{artist} {title}"
        print(f"  Soulseek: Searching for '{query}'...")

        request = await client.searches.search(query)
        await asyncio.sleep(self.search_timeout)

        if not request.results:
            print(f"  Soulseek: No results for '{query}'")
            return None

        best = self._pick_best_result(request.results, artist, title)
        if not best:
            print(f"  Soulseek: No suitable match found for '{query}'")
            return None

        item = best.shared_items[0] if best.shared_items else None
        if not item:
            print(f"  Soulseek: No shared items in result")
            return None

        filename = item.filename
        remote_filename = os.path.basename(filename)
        print(f"  Soulseek: Downloading '{remote_filename}' from {best.username}")

        transfer = await client.transfers.download(best.username, filename)
        result_path = await self._wait_for_download(client, transfer, remote_filename)

        if result_path:
            print(f"  Soulseek: Downloaded to {result_path}")
        else:
            print(f"  Soulseek: Download failed for '{query}'")

        return result_path

    AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")

    def _pick_best_result(self, results, artist, title):
        scored = []
        artist_lower = artist.lower()
        title_lower = title.lower()

        for res in results[:self.max_results]:
            if not res.shared_items:
                continue
            item = res.shared_items[0]
            fname_lower = item.filename.lower()

            if not any(fname_lower.endswith(ext) for ext in self.AUDIO_EXTS):
                continue

            score = 0
            if artist_lower in fname_lower:
                score += 3
            if title_lower in fname_lower:
                score += 5

            bitrate = self._get_bitrate(item)
            score += SOULSEEK_BITRATE_PRIORITY.get(bitrate, 0)

            if item.filesize and item.filesize > 0:
                score += 1

            scored.append((score, res, item))

        if not scored:
            return None

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def _get_bitrate(self, item):
        if hasattr(item, 'attributes') and item.attributes:
            for attr in item.attributes:
                if getattr(attr, 'type', None) == 4 or getattr(attr, 'name', '').lower() == 'bitrate':
                    return getattr(attr, 'value', 0) or getattr(attr, 'int_value', 0)
        return 0

    async def _wait_for_download(self, client, transfer, remote_filename):
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
            unreg_progress = client.events.register(TransferProgressEvent, on_progress)
            unreg_removed = client.events.register(TransferRemovedEvent, on_removed)

            try:
                await asyncio.wait_for(transfer_completed.wait(), timeout=300)
            except asyncio.TimeoutError:
                print(f"  Soulseek: Download timed out for '{remote_filename}'")
                return None

            if transfer_failed:
                print(f"  Soulseek: Download failed for '{remote_filename}'")
                return None

            local_path = transfer.local_path
            if local_path and os.path.exists(local_path):
                if not local_path.lower().endswith((".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma")):
                    print(f"  Soulseek: Deleting non-music file: {local_path}")
                    os.remove(local_path)
                    return None
                return local_path

            print(f"  Soulseek: File not found at expected path: {local_path}")
            return None
        finally:
            if unreg_progress:
                unreg_progress()
            if unreg_removed:
                unreg_removed()

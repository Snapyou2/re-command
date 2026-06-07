import aiofiles
import aiofiles.os
import types
import asyncio
import os as std_os


async def _makedirs(path, mode=0o777, exist_ok=False):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, std_os.makedirs, path, mode, exist_ok)


async def _path_exists(path):
    try:
        await aiofiles.os.stat(path)
        return True
    except OSError:
        return False


async def _path_getsize(path):
    st = await aiofiles.os.stat(path)
    return st.st_size


aiofiles.os.makedirs = _makedirs
aiofiles.os.path = types.ModuleType('aiofiles.os.path')
aiofiles.os.path.exists = _path_exists
aiofiles.os.path.getsize = _path_getsize

"""Updating Flash32 itself from its GitHub releases.

The latest release's Windows zip is downloaded, checked against the SHA-256
GitHub publishes for it (or the one in the release notes), and unpacked
beside the app. A running exe can't replace itself, so a small PowerShell
script waits for Flash32 to exit, swaps in the new files, and starts it
again; if the copy fails it puts the old files back.

Only the exe and its own _internal folder are touched, never anything else
that happens to sit next to them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .manifest import USER_AGENT

REPO = "rw0104/Flash32"
ASSET = "Flash32-win64.zip"
EXE = "Flash32.exe"
MAX_DOWNLOAD = 300 << 20


class UpdateError(ValueError):
    """An update that can't be used; str() is an i18n key."""


@dataclass
class Release:
    version: str          # "0.4.0"
    tag: str
    notes: str
    page: str             # the release's web page
    asset_url: str = ""
    asset_size: int = 0
    sha256: str = ""      # lower-case hex, if published


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2); anything after the numbers is ignored."""
    match = re.match(r"\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(n) for n in match.group(1).split(".")) if match else ()


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def parse_release(data: dict, asset_name: str = ASSET) -> Release:
    tag = str(data.get("tag_name") or "")
    notes = str(data.get("body") or "")
    release = Release(tag[1:] if tag[:1] in "vV" else tag, tag, notes, str(data.get("html_url") or ""))
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == asset_name:
            release.asset_url = str(asset.get("browser_download_url") or "")
            release.asset_size = int(asset.get("size") or 0)
            digest = str(asset.get("digest") or "")
            if digest.lower().startswith("sha256:"):
                release.sha256 = digest[7:].lower()
    if not release.sha256:
        # Older releases state it in the notes: "SHA-256  Flash32-win64.zip  <hex>".
        match = re.search(re.escape(asset_name) + r"\s+([0-9a-fA-F]{64})", notes)
        if match:
            release.sha256 = match.group(1).lower()
    return release


def latest_release(repo: str = REPO) -> Release:
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return parse_release(json.loads(response.read(4 << 20).decode("utf-8")))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def can_self_update() -> bool:
    """Only the packaged Windows app replaces itself, and only where it may write."""
    if not getattr(sys, "frozen", False) or os.name != "nt":
        return False
    folder = install_dir()
    if not (folder / "_internal").is_dir():
        return False
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return False
    return True


def download(release: Release, folder: Path, progress=None, cancelled=lambda: False) -> Path:
    """The release's zip in `folder`, checked against its SHA-256."""
    if not release.asset_url:
        raise UpdateError("err_update_no_asset")
    if not release.sha256:
        raise UpdateError("err_update_checksum")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ASSET
    digest, done = hashlib.sha256(), 0
    request = urllib.request.Request(release.asset_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or release.asset_size or 0)
        while True:
            if cancelled():
                raise InterruptedError
            chunk = response.read(1 << 16)
            if not chunk:
                break
            done += len(chunk)
            if done > MAX_DOWNLOAD:
                raise UpdateError("download_too_large")
            digest.update(chunk)
            out.write(chunk)
            if progress and total:
                progress(done / total)
    if digest.hexdigest() != release.sha256:
        target.unlink(missing_ok=True)
        raise UpdateError("err_update_checksum")
    return target


def unpack(archive: Path, folder: Path) -> Path:
    """Extracts the zip and returns the folder holding the new exe and _internal."""
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            # zipfile already drops absolute paths and "..", but refuse such an archive outright.
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise UpdateError("err_update_bad_zip")
        zf.extractall(folder)
    for exe in sorted(folder.rglob(EXE), key=lambda p: len(p.parts)):
        if (exe.parent / "_internal").is_dir():
            return exe.parent
    raise UpdateError("err_update_bad_zip")


def _ps(path) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(path).replace("'", "''") + "'"


def installer_script(new: Path, target: Path, exe_name: str, pid: int, work: Path) -> str:
    """PowerShell that waits for `pid`, swaps target's exe and _internal for
    new ones (restoring the old ones if that fails), and starts the app."""
    old = work / "previous"
    return f"""$ErrorActionPreference = 'Continue'
$log = {_ps(work / 'update.log')}
function Note($text) {{ Add-Content -LiteralPath $log -Value ("{{0:HH:mm:ss}} {{1}}" -f (Get-Date), $text) }}
Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 700
$target = {_ps(target)}
$new = {_ps(new)}
$old = {_ps(old)}
$exe = {_ps(exe_name)}
# Keep the current files, to put back if the copy fails.
robocopy (Join-Path $target '_internal') (Join-Path $old '_internal') /MIR /IS /IT /R:3 /W:1 /NP /NFL /NDL | Out-Null
Copy-Item -LiteralPath (Join-Path $target $exe) -Destination (Join-Path $old $exe) -Force
robocopy (Join-Path $new '_internal') (Join-Path $target '_internal') /MIR /IS /IT /R:10 /W:1 /NP /NFL /NDL | Out-Null
$code = $LASTEXITCODE
$ok = $code -lt 8
if ($ok) {{
    try {{ Copy-Item -LiteralPath (Join-Path $new {_ps(EXE)}) -Destination (Join-Path $target $exe) -Force -ErrorAction Stop }}
    catch {{ $ok = $false; Note "exe copy failed: $_" }}
}} else {{ Note "robocopy failed: $code" }}
if (-not $ok) {{
    robocopy (Join-Path $old '_internal') (Join-Path $target '_internal') /MIR /IS /IT /R:10 /W:1 /NP /NFL /NDL | Out-Null
    Copy-Item -LiteralPath (Join-Path $old $exe) -Destination (Join-Path $target $exe) -Force
    Note 'restored the previous version'
}}
Start-Process -FilePath (Join-Path $target $exe) -WorkingDirectory $target
if ($ok) {{ Note 'updated'; Start-Sleep -Seconds 2; Remove-Item -LiteralPath {_ps(work)} -Recurse -Force -ErrorAction SilentlyContinue }}
"""


def launch_installer(new: Path, work: Path) -> None:
    """Starts the swap; the caller then quits so it can happen."""
    target = install_dir()
    script = work / "install.ps1"
    # UTF-8 with BOM, so Windows PowerShell 5 reads non-ASCII paths correctly.
    script.write_text(installer_script(new, target, Path(sys.executable).name, os.getpid(), work),
                      encoding="utf-8-sig")
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def work_folder(version: str) -> Path:
    return Path(tempfile.gettempdir()) / f"flash32-update-{version}"

#!/usr/bin/env python3
#-
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2018 A. Theodore Markettos
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#

"""Download and install Quartus Prime on headless servers (no browser/GUI)."""

from __future__ import annotations

import argparse
import glob
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

if sys.version_info < (3, 11):
    sys.exit("quartus-install.py requires Python 3.11+")

import tomllib

UrlDB = dict[str, dict[str, str]]

# Canonical download host.  Altera now owns the FPGA tools; this host
# 301-redirects to the Intel-operated Akamai CDN and serves the file on a
# GET that follows redirects (HEAD shows a bogus 404-redirector, but
# urllib/aria2 use GET).  The whole URL database uses this host - never
# downloads.intel.com - so the redirect target can move without edits here.
BASE_URL = "https://download.altera.com/akdlm/software/acdsinst"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARALLEL = 16

DATA_FILE = os.path.join(SCRIPT_DIR, "quartus-urls.toml")
DATA_URL = ("https://raw.githubusercontent.com/drizzt/quartus-install/"
            "master/quartus-urls.toml")

# Non-device parts that are always installed alongside the requested
# devices, keyed by the prefix before the first '_' in the part name.
SPECIAL_PREFIXES = {"patch", "update"}

FPGA_KEY = {
    "a2": "arria",
    "a5": "arriav",
    "a10": "arria10",
    "a5gz": "arriavgz",
    "c4": "cyclone",
    "c5": "cyclonev",
    "c10lp": "cyclone10lp",
    "m2": "max",
    "m10": "max10",
    "s4": "stratixiv",
    "s5": "stratixv",
}


def version_tuple(text: str) -> tuple[int, ...]:
    """Parse a dotted version into ints for correct numeric comparison.

    Non-digit/non-dot characters are stripped (e.g. '13.0sp1web' -> (13, 1)).
    This deliberately corrects the old lexicographic string compares (where
    e.g. '9.x' > '17.1'); tuple comparison orders versions numerically.
    """
    cleaned = "".join(c for c in text if c.isdigit() or c == ".")
    return tuple(int(p) for p in cleaned.split(".") if p != "")


# --------------------------------------------------------------------------
# URL builders
# --------------------------------------------------------------------------

def generate_pro_url(quartus_version: str, minor_version: str,
                      revision: str) -> dict[str, str]:
    full_version = f"{quartus_version}.{minor_version}.{revision.split('.')[0]}"
    version_url = f"{BASE_URL}/{quartus_version}/{revision}/ib_installers"
    qv = version_tuple(quartus_version)
    pro_urls: dict[str, str] = {}
    pro_urls["setup"] = f"{version_url}/QuartusProSetup-{full_version}-linux.run"
    pro_urls["setupwindows"] = f"{version_url}/QuartusProSetup-{full_version}-windows.exe"
    pro_urls["modelsim_part1"] = f"{version_url}/ModelSimProSetup-{full_version}-linux.run"

    if qv >= (19, 2):
        pro_urls["modelsim_part1"] = f"{version_url}/ModelSimProSetup-{full_version}-linux.run"
        pro_urls["modelsim_part2"] = f"{version_url}/ModelSimProSetup-part2-{full_version}-linux.run"
        pro_urls["modelsimwindows_part1"] = f"{version_url}/ModelSimProSetup-{full_version}-windows.exe"
        pro_urls["modelsimwindows_part2"] = f"{version_url}/ModelSimProSetup-part2-{full_version}-windows.exe"
    if qv == (19, 2):
        pro_urls["modelsim_part2"] = f"{version_url}/modelsim-part2-{full_version}-linux.qdz"
        pro_urls["modelsimwindows_part2"] = f"{version_url}/modelsim-part2-{full_version}-windows.qdz"
    if qv >= (19, 3):
        pro_urls["setup"] = f"{version_url}/QuartusProSetup-{full_version}-linux.run"
        pro_urls["setup_part2"] = f"{version_url}/QuartusProSetup-part2-{full_version}-linux.run"
        pro_urls["setupwindows"] = f"{version_url}/QuartusProSetup-{full_version}-windows.exe"
        pro_urls["setupwindows_part2"] = f"{version_url}/QuartusProSetup-part2-{full_version}-windows.exe"
    if qv >= (20, 1):
        pro_urls["agilex"] = f"{version_url}/agilex-{full_version}.qdz"
    if qv >= (20, 3):
        pro_urls["diamondmesa"] = f"{version_url}/diamondmesa-{full_version}.qdz"
        pro_urls["setup_part2"] = f"{version_url}/quartus_part2-{full_version}.qdz"
        pro_urls["setupwindows_part2"] = f"{version_url}/quartus_part2-{full_version}.qdz"
    if qv >= (20, 4):
        pro_urls["setup_part2"] = f"{version_url}/quartus_part2-{full_version}-linux.qdz"
        pro_urls["setupwindows_part2"] = f"{version_url}/quartus_part2-{full_version}-windows.qdz"
    if qv >= (21, 1):
        pro_urls["modelsim_part2"] = f"{version_url}/modelsim_part2-{full_version}-linux.qdz"
        pro_urls["questa_part1"] = f"{version_url}/QuestaSetup-{full_version}-linux.run"
        pro_urls["questa_part2"] = f"{version_url}/questa_part2-{full_version}-linux.qdz"
        pro_urls["modelsimwindows_part2"] = f"{version_url}/modelsim_part2-{full_version}-windows.qdz"
        pro_urls["questawindows_part1"] = f"{version_url}/QuestaSetup-{full_version}-windows.exe"
        pro_urls["questawindows_part2"] = f"{version_url}/questa_part2-{full_version}-windows.qdz"
    if qv >= (21, 3):
        pro_urls.pop("modelsim_part1", None)
        pro_urls.pop("modelsim_part2", None)
        pro_urls.pop("modelsimwindows_part1", None)
        pro_urls.pop("modelsimwindows_part2", None)
    if qv >= (22, 4):
        pro_urls["easicn5x"] = f"{version_url}/easicn5x-{full_version}.qdz"
        pro_urls.pop("diamondmesa", None)
    if qv >= (23, 1):
        pro_urls["setup_part2"] = f"{version_url}/QuartusProSetup-part2-{full_version}-linux.qdz"
        pro_urls["questa_part2"] = f"{version_url}/QuestaSetup-part2-{full_version}-linux.qdz"
        pro_urls["setupwindows_part2"] = f"{version_url}/QuartusProSetup-part2-{full_version}-windows.qdz"
        pro_urls["questawindows_part2"] = f"{version_url}/QuestaSetup-part2-{full_version}-windows.qdz"
        pro_urls.pop("agilex", None)
        pro_urls["agilex7"] = f"{version_url}/agilex7-{full_version}.qdz"
    if qv >= (23, 3):
        pro_urls.pop("questa_part2", None)
        pro_urls.pop("questawindows_part2", None)
    if qv >= (24, 1):
        pro_urls["agilex5"] = f"{version_url}/agilex5-{full_version}.qdz"
        pro_urls["agilex_common"] = f"{version_url}/agilex_common-{full_version}.qdz"
    if qv >= (25, 1):
        pro_urls["agilex3"] = f"{version_url}/agilex3-{full_version}.qdz"
    pro_urls["a10"] = f"{version_url}/arria10-{full_version}.qdz"
    pro_urls["c10gx"] = f"{version_url}/cyclone10gx-{full_version}.qdz"
    pro_urls["s10"] = f"{version_url}/stratix10-{full_version}.qdz"
    return pro_urls


def generate_std_url(quartus_version: str, minor_version: str,
                     revision: str, edition: str, *,
                     sim: str = "modelsim", embed_edition: bool = False,
                     arria10_single: bool = False) -> dict[str, str]:
    """Build the Standard/Lite {part: url} map.

    Three things shift across releases (verified against the CDN):
      - embed_edition: from 22.1std the edition is also baked into the
        filename, eg QuartusSetup-22.1std.0.915-linux.run (older: 21.1.0.842).
      - sim: ModelSim was renamed to Questa from 21.1 (dict key stays
        'modelsim' so the install logic and CLI are unchanged).
      - arria10_single: from 23.1std Arria 10 ships as one arria10-*.qdz
        instead of the older arria10_part1/2/3 split.
    """
    version_url = f"{BASE_URL}/{quartus_version}{edition}/{revision}/ib_installers"
    edition_tag = edition.split(".")[0]
    if embed_edition:
        full_version = f"{quartus_version}{edition_tag}.{minor_version}.{revision}"
    else:
        full_version = f"{quartus_version}.{minor_version}.{revision}"
    sim_leaf = "QuestaSetup" if sim == "questa" else "ModelSimSetup"
    urls: dict[str, str] = {}
    urls["setup"] = f"{version_url}/QuartusSetup-{full_version}-linux.run"
    urls["modelsim"] = f"{version_url}/{sim_leaf}-{full_version}-linux.run"
    if arria10_single:
        urls["a10"] = f"{version_url}/arria10-{full_version}.qdz"
    else:
        for part in (1, 2, 3):
            urls[f"a10_part{part}"] = f"{version_url}/arria10_part{part}-{full_version}.qdz"
    # a10 handled above; skip it in the generic family loop.
    for fpga, family in FPGA_KEY.items():
        if fpga == "a10":
            continue
        urls[fpga] = f"{version_url}/{family}-{full_version}.qdz"
    return urls


def lite_from_std(std: dict[str, str]) -> dict[str, str]:
    """Lite shares Std device files; only the installer and the Arria II
    leaf differ (QuartusLiteSetup, arria_lite-*.qdz)."""
    lite = dict(std)
    lite["setup"] = std["setup"].replace("/QuartusSetup-", "/QuartusLiteSetup-")
    if "a2" in std:
        lite["a2"] = std["a2"].replace("/arria-", "/arria_lite-")
    return lite


def load_versions_data() -> dict:
    """Read the URL database TOML.

    Prefer the copy next to the script (git clone / normal install); if the
    script was copied out on its own, fetch it from GitHub into memory so it
    still runs standalone.
    """
    try:
        with open(DATA_FILE, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        pass  # script copied out on its own - fall back to GitHub
    try:
        with urllib.request.urlopen(DATA_URL, timeout=10) as resp:
            return tomllib.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - offline, 404, bad TOML, ...
        sys.exit(f"Cannot load URL database: {DATA_FILE} not found and "
                 f"fetching {DATA_URL} failed: {exc}")


def build_versions() -> UrlDB:
    """Build the full {version: {part: url}} database from the TOML data.

    Generator helpers stay in code; the TOML carries only per-version
    parameters.  References (base / copy_parts_from) are resolved
    recursively so entries may appear in any order in the file.
    """
    data = load_versions_data()
    raw = data.get("versions", {})
    built: dict[str, dict[str, str]] = {}
    building: set[str] = set()

    def resolve(key: str) -> dict[str, str]:
        if key in built:
            return built[key]
        if key not in raw:
            sys.exit(f"URL database: unknown version '{key}'")
        if key in building:
            sys.exit(f"URL database: circular reference at '{key}'")
        building.add(key)
        entry = raw[key]
        kind = entry.get("type")
        if kind == "pro":
            urls = generate_pro_url(entry["quartus_version"],
                                    entry["minor"], entry["revision"])
        elif kind == "std":
            urls = generate_std_url(
                entry["quartus_version"], entry["minor"],
                entry["revision"], entry["edition"],
                sim=entry.get("sim", "modelsim"),
                embed_edition=entry.get("embed_edition", False),
                arria10_single=entry.get("arria10_single", False))
            src = entry.get("copy_parts_from")
            if src:
                base = resolve(src)
                for part in entry.get("copy_parts", []):
                    urls[part] = base[part]
        elif kind == "lite_from_std":
            urls = lite_from_std(resolve(entry["base"]))
        elif kind == "copy":
            urls = dict(resolve(entry["base"]))
        elif kind == "explicit":
            urls = dict(entry["urls"])
        else:
            sys.exit(f"URL database: unknown type '{kind}' for '{key}'")
        urls.update(entry.get("override", {}))
        building.discard(key)
        built[key] = urls
        return urls

    return {key: resolve(key) for key in raw}


VERSIONS: UrlDB = build_versions()


# --------------------------------------------------------------------------
# Part / URL selection
# --------------------------------------------------------------------------

def match_wanted_parts(version: str, devices: list[str]) -> list[str]:
    """Filter the available parts of a version down to what was requested."""
    parts = list(VERSIONS[version].keys())
    parts.remove("setup")
    wanted_parts = []
    for part in parts:
        prefix = part.split("_", 1)[0]
        # agilex_common is the shared Agilex device DB every agilexN part
        # needs; pull it whenever present, like setup_part2.
        if (prefix in devices or prefix in SPECIAL_PREFIXES
                or part in ("setup_part2", "agilex_common")):
            wanted_parts.append(part)
    return wanted_parts


def collect_urls(version: str, parts: list[str]) -> list[str]:
    """Convert the requested parts to a list of URLs."""
    return [VERSIONS[version][p] for p in parts]


# --------------------------------------------------------------------------
# Download / install
# --------------------------------------------------------------------------

def download_quartus(version: str, parts: list[str],
                     args: argparse.Namespace) -> tuple[int, list[str]]:
    urls = collect_urls(version, parts)

    fd, urllistfile = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "w") as urlfile:
            for url in urls:
                urlfile.write(f"{url}\n")

        if args.parallel is not None:
            parallel = "-x" + args.parallel
        else:
            print(f"Using default of {DEFAULT_PARALLEL} parallel download connections")
            parallel = "-x" + str(DEFAULT_PARALLEL)
        command = ["aria2c", "--continue", "--file-allocation=none",
                   "--download-result=full", "--summary=300", parallel,
                   "--input-file", urllistfile]
        process = subprocess.Popen(command, bufsize=1)
        try:
            process.wait()
        except KeyboardInterrupt:
            try:
                process.terminate()
            except OSError:
                pass
            sys.exit(3)
        rc = process.wait()
    finally:
        os.remove(urllistfile)
    return rc, urls


def install_patch(version: str, installdir: str, partname: str) -> int:
    return run_installer(version, VERSIONS[version][partname], installdir)


def run_installer(version: str, installerfile: str, installdir: str) -> int:
    leafname = os.path.basename(installerfile)
    target = os.path.abspath(installdir)
    # The Quartus BitRock installer never exits unattended: it writes
    # nothing to stdout and, even with DISPLAY/WAYLAND_DISPLAY stripped (so
    # no progress window can be drawn), a post-install thread waits forever
    # after the work is done. Two logs matter:
    #  - live progress goes to /tmp/bitrock_installer_<pid>.log, written
    #    incrementally during the run -> tail that for real-time output;
    #  - "Installation completed" is recorded in <installdir>/logs/
    #    quartus-<ver>-linux-install.log, which BitRock only materialises at
    #    the end -> use that for completion detection.
    # The base .run also chain-spawns the bundled update with its own pair
    # of logs. So we launch detached in its own process group, tail the
    # bitrock logs for progress, and once every installdir log reports
    # completion (tree fully written) reap the whole group ourselves.
    args = ["--mode", "unattended"]
    if version_tuple(version) >= (17, 1):
        args = args + ["--accept_eula", "1"]
    env = os.environ.copy()
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    logglob = os.path.join(target, "logs", "quartus-*-linux-install.log")
    progressglob = "/tmp/bitrock_installer_*.log"
    start_ts = time.time()
    offsets: dict[str, int] = {}

    def matching(globpat: str) -> list[str]:
        out = []
        for f in glob.glob(globpat):
            try:
                if os.path.getmtime(f) >= start_ts - 5:
                    out.append(f)
            except OSError:
                pass
        return sorted(out)

    def tail(globpat: str) -> None:
        # Stream newly-appended content of every matching file to our
        # stdout so CI/console shows real installer progress instead of a
        # silent multi-minute black box.
        for f in matching(globpat):
            try:
                with open(f, errors="ignore") as fh:
                    fh.seek(offsets.get(f, 0))
                    chunk = fh.read()
                    offsets[f] = fh.tell()
            except OSError:
                continue
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()

    process = subprocess.Popen(
        ["./" + leafname] + args + ["--installdir", target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True)
    deadline = start_ts + 3 * 3600
    while True:
        rc = process.poll()
        tail(progressglob)
        tail(logglob)
        if rc is not None:
            return rc                       # exited on its own
        logs = matching(logglob)
        if logs and all(
                "Installation completed" in open(f, errors="ignore").read()
                for f in logs):
            break
        if time.time() > deadline:
            sys.stderr.write("run_installer: timed out waiting for "
                             "completion banner; reaping anyway\n")
            break
        time.sleep(5)
    # Tree is fully written; kill the whole process group (base installer +
    # chained update) since it will never return on its own.
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    return 0


def cmd_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# --------------------------------------------------------------------------
# Informational actions
# --------------------------------------------------------------------------

def test_url(quartus: str, part: str, url: str, print_url: bool) -> bool:
    """Check a URL and return True if it can be reached."""
    if print_url:
        print(url)
    else:
        print(f"\rChecking {quartus}/{part}         ", end="")
    try:
        # KeyboardInterrupt is a BaseException, so Ctrl-C still propagates;
        # any reachability failure (URL/HTTP/OS error) just means "missing".
        with urllib.request.urlopen(url):
            return True
    except Exception:
        return False


def check_urls(print_urls: bool) -> bool:
    """Iterate through the URL database and report unreachable URLs."""
    success = True
    for quartus, parts in VERSIONS.items():
        for part, url in parts.items():
            if not test_url(quartus, part, url, print_urls):
                print(f"\nMissing {quartus}/{part} url={url}")
                success = False
    return success


def list_versions() -> None:
    """Print the supported Quartus versions."""
    for key in VERSIONS:
        print(key)


def list_parts(version: str) -> None:
    for key in VERSIONS[version]:
        print(key)


def foreign_pre(target: str) -> None:
    if platform.machine() == "x86_64":
        print("Warning: Quartus will run natively here, not modifying the system")
        return
    subprocess.run([os.path.join(SCRIPT_DIR, "foreign", "foreign-pre.sh"), target],
                   check=False)


def foreign_post(target: str) -> None:
    if platform.machine() == "x86_64":
        return
    subprocess.run(
        [os.path.join(SCRIPT_DIR, "foreign", "foreign-post.sh"),
         os.path.join(target, "quartus")],
        check=False)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and install Quartus.")
    parser.add_argument("--list-versions", action="store_true",
                        help="Print supported versions")
    parser.add_argument("--list-parts", action="store_true",
                        help="Print supported devices (and other parts) for a version")
    parser.add_argument("--download-only", action="store_true",
                        help="Only download, don't install")
    parser.add_argument("--install-only", action="store_true",
                        help="Only install, don't download")
    parser.add_argument("--print-urls", action="store_true",
                        help="Just print URLs that would be downloaded")
    parser.add_argument("--prune", action="store_true",
                        help="Delete install files when finished")
    parser.add_argument("--nosetup", action="store_true",
                        help="Don't download Quartus setup frontend")
    parser.add_argument("--parallel", "-j", action="store",
                        help="Number of parallel download connections")
    parser.add_argument("--fix-libpng", action="store_true",
                        help="Build and add libpng12.so binary")
    parser.add_argument("--fix-libncurses", action="store_true",
                        help="Build and add libncurses5.so binary")
    parser.add_argument("--foreign", action="store_true",
                        help="Patch non-x86 system to run x86 Quartus via QEMU "
                             "- very experimental, requires root")
    parser.add_argument("--check-urls", action="store_true",
                        help="Report any download URLs that are unreachable")
    # Positionals are optional so the informational flags above work on
    # their own.  A single trailing list (instead of separate target/device
    # slots) avoids the ambiguity where "VERSION DEVICE" would bind DEVICE to
    # the target.  main() resolves it by mode: install uses
    # "VERSION TARGET DEVICE..."; --print-urls/--download-only (no install,
    # downloads land in the CWD) use "VERSION DEVICE...".
    parser.add_argument("version", nargs="?",
                        help="Quartus version, eg 18.0pro, 17.1lite, 16.1std")
    parser.add_argument("rest", nargs="*", metavar="target device ...",
                        help="Install dir then devices (eg /opt/q s5 a10); "
                             "for --print-urls/--download-only just devices")
    return parser


def reject(message: str) -> int:
    """Print an error plus the supported version list; return exit code 1."""
    print(message)
    print("Supported versions are:")
    list_versions()
    return 1


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    # --- informational actions: no positionals required -------------------
    if args.check_urls:
        passed = check_urls(args.print_urls)
        print("\rAll URLs reached successfully" if passed
              else "\rSome URLs could not be reached")
        return 0 if passed else 1

    if args.list_versions:
        list_versions()
        return 0

    version = args.version

    if args.list_parts:
        if version is None:
            return reject("--list-parts needs a version, eg: --list-parts 17.1std")
        if version not in VERSIONS:
            return reject(f"Unrecognised Quartus version '{version}'")
        list_parts(version)
        return 0

    # --- everything below needs a valid version ---------------------------
    if version is None:
        return reject("A Quartus version is required (or use --list-versions).")
    if version not in VERSIONS:
        return reject(f"Unrecognised Quartus version '{version}'")

    # --print-urls and --download-only download into the CWD and never
    # install, so the trailing args are all devices (no target directory).
    no_install = args.print_urls or args.download_only
    if no_install:
        target = None
        devices = args.rest
    else:
        if not args.rest:
            print("A target install directory is required.")
            return 1
        target, devices = args.rest[0], args.rest[1:]

    if not args.print_urls and not devices:
        print("At least one device is required (or use --list-parts).")
        return 1

    parts: list[str] = []
    if not args.nosetup:
        parts.append("setup")
    parts += match_wanted_parts(version, devices)

    if args.print_urls:
        for url in collect_urls(version, parts):
            print(url)
        return 0

    if not cmd_exists("aria2c"):
        print("Please install the 'aria2' tool (command line executable 'aria2c')")
        return 2

    if args.foreign and target is not None:
        print("Running pre-installation script to configure for cross-arch qemu-user")
        foreign_pre(target)

    urls: list[str] = []
    if not args.install_only:
        print(f"Downloading Quartus {version} parts {parts}\n")
        _rc, urls = download_quartus(version, parts, args)
        for url in urls:
            leafname = os.path.basename(url)
            if leafname.endswith(".run"):
                try:
                    os.chmod(leafname, stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP
                             | stat.S_IXOTH | stat.S_IROTH)
                except FileNotFoundError:
                    pass

    if not args.download_only:
        print("Installing Quartus\n")
        install_patch(version, target, "setup")
        for part in parts:
            prefix = part.split("_", 1)[0]
            if prefix in SPECIAL_PREFIXES:
                print(f"Installing {prefix} {part}\n")
                install_patch(version, target, part)
            elif part == "modelsim":
                print("Installing ModelSim\n")
                install_patch(version, target, part)

    if args.prune and not args.install_only:
        for url in urls:
            try:
                os.remove(os.path.basename(url))
            except FileNotFoundError:
                pass

    if args.foreign and target is not None:
        print("Running post-installation script to configure for cross-arch qemu-user")
        foreign_post(target)

    if target is not None and (args.fix_libpng or args.fix_libncurses):
        libdir = os.path.join(target, "quartus", "linux64")
        for enabled, script in ((args.fix_libpng, "install-libpng.sh"),
                                (args.fix_libncurses, "install-libncurses.sh")):
            if enabled:
                subprocess.run([os.path.join(SCRIPT_DIR, script), libdir],
                               check=False)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

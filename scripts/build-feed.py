#!/usr/bin/env python3
"""Publish existing GitHub Release APKs as a signed OpenWrt feed."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


APK_IMAGE = "mirror.gcr.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "releases.json"


def download(url, headers=None):
    request = Request(url, headers={"User-Agent": "aethersailor-openwrt-package-feed", **(headers or {})})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except (URLError, TimeoutError) as error:
            if attempt == 2 or (isinstance(error, HTTPError) and error.code < 500):
                raise
            time.sleep(2 ** attempt)


def release_assets(release):
    repo = release["repository"]
    tag = quote(release["tag"], safe="")
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    data = json.loads(download(url, headers))
    if data["draft"] or data["prerelease"]:
        raise ValueError(f"{repo} {tag} is not a stable release")

    assets = [asset for asset in data["assets"] if asset["name"].endswith(".apk")]
    if len(assets) != release["asset_count"]:
        raise ValueError(f"{repo} {tag} has {len(assets)} APKs, expected {release['asset_count']}")
    return assets


def apk(site, *args, key=None):
    command = [
        "docker", "run", "--rm", "--network", "none",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", f"type=bind,source={site},target=/feed",
    ]
    if key:
        command += ["--mount", f"type=bind,source={key},target=/signing-key.pem,readonly"]
    command += [APK_IMAGE, "apk", *args]
    return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE).stdout


def stage_package(site, release, asset, raw):
    archive = download(asset["browser_download_url"])
    digest = "sha256:" + hashlib.sha256(archive).hexdigest()
    if len(archive) != asset["size"] or digest != asset.get("digest"):
        raise ValueError(f"Release asset checksum mismatch: {asset['name']}")

    temporary = raw / asset["name"]
    temporary.write_bytes(archive)
    package = json.loads(apk(site, "adbdump", "--format", "json", f"/feed/.incoming/{asset['name']}"))["info"]
    name, version, arch = package["name"], package["version"], package["arch"]
    if (name, version) != (release["package"], release["version"]):
        raise ValueError(f"Unexpected package identity in {asset['name']}: {name} {version}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]+", arch):
        raise ValueError(f"Invalid architecture in {asset['name']}: {arch}")

    directory = site / "immortalwrt" / "apk" / "packages" / arch
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{name}-{version}.apk"
    destination = directory / filename
    if destination.exists():
        raise ValueError(f"Duplicate package {name} for {arch}")
    temporary.rename(destination)
    print(f"{arch}: {name} {version}", flush=True)
    return arch, name, version, filename


def index_packages(site, key, arch, entries):
    directory = site / "immortalwrt" / "apk" / "packages" / arch
    prefix = f"/feed/immortalwrt/apk/packages/{arch}"
    apk(site, "mkndx", "--allow-untrusted", "--sign-key", "/signing-key.pem",
        "--output", f"{prefix}/packages.adb",
        *(f"{prefix}/{filename}" for _, _, filename in entries), key=key)
    packages = json.loads(apk(site, "adbdump", "--format", "json", f"{prefix}/packages.adb"))["packages"]
    versions = {package["name"]: package["version"] for package in packages}
    expected = {name: version for name, version, _ in entries}
    if versions != expected or any(package["arch"] != arch for package in packages):
        raise ValueError(f"Unexpected index content for {arch}")
    apk(site, "--keys-dir", "/feed/keys", "--repositories-file", "/dev/null",
        "--repository", f"{prefix}/packages.adb", "--arch", arch,
        "fetch", "--simulate", *sorted(versions))
    index = {"version": 2, "architecture": arch, "packages": dict(sorted(versions.items()))}
    (directory / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def write_site_home(site, releases, architectures):
    release_links = "\n".join(
        f'<li><a href="https://github.com/{escape(item["repository"])}/releases/tag/{escape(item["tag"])}">'
        f'{escape(item["repository"].split("/")[-1])} {escape(item["tag"])}</a></li>'
        for item in releases
    )
    feeds = "\n".join(
        f'<li><a href="immortalwrt/apk/packages/{arch}/packages.adb">{arch}</a>'
        f' · <a href="immortalwrt/apk/packages/{arch}/index.json">版本索引</a></li>'
        for arch in sorted(architectures)
    )
    (site / "index.html").write_text(
        '<!doctype html>\n<html lang="zh-CN">\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Aethersailor OpenWrt Package Feed</title>\n'
        '<h1>Aethersailor OpenWrt Package Feed</h1>\n'
        '<p><a href="https://github.com/Aethersailor/openwrt-package-feed#readme">安装说明</a>'
        ' · <a href="keys/aethersailor-feed.pem">签名公钥</a></p>\n'
        f'<h2>软件发布与对应源码</h2>\n<ul>\n{release_links}\n</ul>\n'
        f'<h2>按架构浏览软件源</h2>\n<ul>\n{feeds}\n</ul>\n</html>\n',
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    signing_key = os.environ.pop("APK_SIGNING_KEY")
    site = args.output.resolve()
    site.mkdir(parents=True)
    incoming = site / ".incoming"
    incoming.mkdir()
    releases = json.loads(CATALOG.read_text(encoding="utf-8"))["releases"]
    packages = [(release, asset) for release in releases for asset in release_assets(release)]
    architectures = defaultdict(list)
    with ThreadPoolExecutor(max_workers=6) as workers:
        for arch, name, version, filename in workers.map(
            lambda item: stage_package(site, *item, incoming), packages
        ):
            architectures[arch].append((name, version, filename))
    incoming.rmdir()

    (site / "keys").mkdir()
    with tempfile.TemporaryDirectory(prefix="codex-apk-signing-") as private:
        key = Path(private) / "feed.pem"
        key.write_text(signing_key.rstrip() + "\n", encoding="ascii")
        key.chmod(0o600)
        subprocess.run(["openssl", "pkey", "-in", key, "-pubout",
                        "-out", site / "keys" / "aethersailor-feed.pem"], check=True)
        public_key = site / "keys" / "aethersailor-feed.pem"
        if public_key.read_bytes() != (ROOT / "keys" / public_key.name).read_bytes():
            raise ValueError("Signing secret does not match the published feed key")
        for arch, entries in sorted(architectures.items()):
            index_packages(site, key, arch, entries)

    write_site_home(site, releases, architectures)
    print(f"Published candidate: {len(packages)} APKs across {len(architectures)} architectures")


if __name__ == "__main__":
    main()

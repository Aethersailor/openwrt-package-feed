#!/usr/bin/env python3
"""Publish upstream APKs as a signed OpenWrt feed."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from html import escape
import json
import os
from pathlib import Path
import re
from shutil import copyfile
import subprocess
import tempfile
from urllib.error import HTTPError

from bluetooth import resolve_build as resolve_bluetooth_build
from feed_sources import VERSION_TAG, download, resolve_release, verified_asset
from fm350 import resolve_build as resolve_fm350_build


APK_IMAGE = "mirror.gcr.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "releases.json"
PUBLISHED_STATE = "https://aethersailor.github.io/openwrt-package-feed/release-state.json"


def package_names(release):
    return release["packages"] if "packages" in release else [release["package"]]


def published_state():
    try:
        return json.loads(download(PUBLISHED_STATE, {"Cache-Control": "no-cache"}))
    except HTTPError as error:
        if error.code != 404:
            raise
        return None



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


def stage_package(site, release, asset, source, raw):
    archive = verified_asset(asset, source)

    with tempfile.NamedTemporaryFile(dir=raw, suffix=".apk", delete=False) as staged:
        staged.write(archive)
    temporary = Path(staged.name)
    package = json.loads(apk(site, "adbdump", "--format", "json", f"/feed/.incoming/{temporary.name}"))["info"]
    name, version, arch = package["name"], package["version"], package["arch"]
    if name not in package_names(release):
        raise ValueError(f"Unexpected package identity in {asset['name']}: {name} {version}")
    filename = f"{name}-{version}.apk"
    if "build_architectures" in release:
        target_arch, _, asset_filename = asset["name"].partition("--")
        if target_arch not in release["build_architectures"] or asset_filename != filename or arch not in ("noarch", target_arch):
            raise ValueError(f"Unexpected build identity in {asset['name']}: {name} {version} {arch}")
        arch = target_arch
    elif identity := asset.get("apk_identity"):
        target_arch = asset.get("apk_arch") or source.get("apk_arch", arch)
        expected_version = identity.get("version")
        if (
            name != identity["name"]
            or (expected_version is not None and version != expected_version)
            or arch not in ("noarch", target_arch)
        ):
            raise ValueError(f"Unexpected release asset identity in {asset['name']}: {name} {version} {arch}")
        arch = target_arch
    else:
        tag_version = VERSION_TAG.fullmatch(release["tag"])[1]
        version_pattern = re.escape(tag_version)
        if "-r" not in tag_version and "package_commit" not in release:
            version_pattern += r"-r\d+"
        if not re.fullmatch(version_pattern, version):
            raise ValueError(f"Unexpected package version in {asset['name']}: {version}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]+", arch) or release.get("architecture", arch) != arch:
        raise ValueError(f"Invalid architecture in {asset['name']}: {arch}")

    directory = site / "immortalwrt" / "apk" / "packages" / arch
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    if destination.exists():
        raise ValueError(f"Duplicate package {name} for {arch}")
    temporary.chmod(0o644)
    temporary.rename(destination)
    print(f"{arch}: {name} {version}", flush=True)
    return arch, name, version, filename


def add_shared_packages(package_root, architectures):
    shared = architectures.pop("noarch", [])
    if not shared:
        return

    staging = package_root / "noarch"
    for arch, entries in architectures.items():
        names = {name for name, _, _ in entries}
        for name, version, filename in shared:
            if name in names:
                raise ValueError(f"Duplicate package {name} for {arch}")
            copyfile(staging / filename, package_root / arch / filename)
            entries.append((name, version, filename))
    for _, _, filename in shared:
        (staging / filename).unlink()
    staging.rmdir()


def index_packages(site, key, arch, entries):
    directory = site / "immortalwrt" / "apk" / "packages" / arch
    prefix = f"/feed/immortalwrt/apk/packages/{arch}"
    apk(site, "mkndx", "--allow-untrusted", "--sign-key", "/signing-key.pem",
        "--output", f"{prefix}/packages.adb",
        *(f"{prefix}/{filename}" for _, _, filename in entries), key=key)
    packages = json.loads(apk(site, "adbdump", "--format", "json", f"{prefix}/packages.adb"))["packages"]
    versions = {package["name"]: package["version"] for package in packages}
    expected = {name: version for name, version, _ in entries}
    if versions != expected or any(package["arch"] not in (arch, "noarch") for package in packages):
        raise ValueError(f"Unexpected index content for {arch}")
    apk(site, "--keys-dir", "/feed/keys", "--repositories-file", "/dev/null",
        "--repository", f"{prefix}/packages.adb", "--arch", arch,
        "fetch", "--simulate", *sorted(versions))
    index = {"version": 2, "architecture": arch, "packages": dict(sorted(versions.items()))}
    (directory / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def source_archive_path(release):
    name = release["repository"].split("/")[-1]
    return f"sources/{name}-{release['tag']}.tar.gz"


def stage_source_archive(site, release, source):
    url = f"https://api.github.com/repos/{release['repository']}/tarball/{release['source_commit']}"
    headers = {"Authorization": f"Bearer {os.environ[source['read_token_env']]}"}
    destination = site / source_archive_path(release)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(download(url, headers))


def write_site_home(site, releases, architectures):
    release_links = []
    for item in releases:
        repo, tag = item["repository"], item["tag"]
        if "source_commit" in item:
            url = source_archive_path(item)
            label = f"{repo.split('/')[-1]} {tag} 源码"
        elif "build_tag" in item:
            url = f"https://github.com/{repo}/releases/tag/{item['build_tag']}"
            label = f"{item['package']} {tag} ({item['architecture']})"
            release_links.append(
                f'<li><a href="{escape(url)}">{escape(label)}</a>'
                f' · <a href="{escape(item["build_source"])}">对应源码</a></li>'
            )
            continue
        elif "package_commit" in item:
            url = f"https://github.com/{repo}/tree/{item['package_commit']}/{item['package_directory']}"
            label = f"{repo.split('/')[-1]} {item['package_directory']} {tag}"
        elif "build_architectures" in item:
            url = f"https://github.com/{repo}/releases/tag/{tag}"
            label = item["packages"][0]
        else:
            url = f"https://github.com/{repo}/releases/tag/{tag}"
            label = f"{repo.split('/')[-1]} {tag}"
        release_links.append(f'<li><a href="{escape(url)}">{escape(label)}</a></li>')
    releases_html = "\n".join(release_links)
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
        f'<h2>软件发布与对应源码</h2>\n<ul>\n{releases_html}\n</ul>\n'
        f'<h2>按架构浏览软件源</h2>\n<ul>\n{feeds}\n</ul>\n</html>\n',
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true", help="Rebuild an unchanged feed")
    args = parser.parse_args()

    sources = json.loads(CATALOG.read_text(encoding="utf-8"))["releases"]
    resolved = [resolve_release(source) for source in sources]
    for resolve_build in (resolve_fm350_build, resolve_bluetooth_build):
        if build := resolve_build():
            resolved.append(build)
            sources.append({"repository": build[0]["repository"]})
    releases = [release for release, _ in resolved]
    state = {"releases": releases}
    if not args.force and state == published_state():
        print("Published APKs already match the configured sources.")
        return

    signing_key = os.environ.pop("APK_SIGNING_KEY")
    subprocess.run(["docker", "pull", APK_IMAGE], check=True)
    site = args.output.resolve()
    site.mkdir(parents=True)
    incoming = site / ".incoming"
    incoming.mkdir()
    packages = [
        (release, asset, source)
        for source, (release, assets) in zip(sources, resolved)
        for asset in assets
    ]
    architectures = defaultdict(list)
    with ThreadPoolExecutor(max_workers=6) as workers:
        for arch, name, version, filename in workers.map(
            lambda item: stage_package(site, *item, incoming), packages
        ):
            architectures[arch].append((name, version, filename))
    incoming.rmdir()
    expected = {name for release in releases for name in package_names(release)}
    found = {name for entries in architectures.values() for name, _, _ in entries}
    if missing := expected - found:
        raise ValueError(f"Missing APKs for configured packages: {sorted(missing)}")
    add_shared_packages(site / "immortalwrt" / "apk" / "packages", architectures)

    for source, release in zip(sources, releases):
        if "source_commit" in release:
            stage_source_archive(site, release, source)

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
    (site / "release-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Published candidate: {len(packages)} APKs across {len(architectures)} architectures")


if __name__ == "__main__":
    main()

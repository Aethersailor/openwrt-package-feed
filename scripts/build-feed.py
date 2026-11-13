#!/usr/bin/env python3
"""Publish upstream APKs as a signed OpenWrt feed."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html import escape
import json
import os
from pathlib import Path
import re
from shutil import copyfile
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


APK_IMAGE = "mirror.gcr.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "releases.json"
PUBLISHED_STATE = "https://aethersailor.github.io/openwrt-package-feed/release-state.json"
VERSION_TAG = re.compile(r"v\d+\.\d+(?:\.\d+(?:-r\d+)?|-r\d+)")


def download(url, headers=None):
    headers = {"User-Agent": "aethersailor-openwrt-package-feed", **(headers or {})}
    authorization = headers.pop("Authorization", None)
    request = Request(url, headers=headers)
    if authorization:
        request.add_unredirected_header("Authorization", authorization)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except (URLError, TimeoutError) as error:
            if attempt == 2 or (isinstance(error, HTTPError) and error.code < 500):
                raise
            time.sleep(2 ** attempt)


def verified_asset(asset, source):
    if token_env := source.get("read_token_env"):
        payload = download(
            f"https://api.github.com/repos/{source['repository']}/releases/assets/{asset['id']}",
            {"Accept": "application/octet-stream", "Authorization": f"Bearer {os.environ[token_env]}"},
        )
    else:
        payload = download(asset["browser_download_url"])
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if digest != asset.get("digest") or ("size" in asset and len(payload) != asset["size"]):
        raise ValueError(f"APK feed download checksum mismatch: {asset['name']}")
    return payload


def apk_checksums(payload):
    checksums = {}
    for line in payload.decode("utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})\s+\*?(?:\./)?([^/]+\.apk)", line)
        if match:
            digest, filename = match.groups()
            if filename in checksums:
                raise ValueError(f"Duplicate checksum entry for {filename}")
            checksums[filename] = f"sha256:{digest}"
    return checksums


def resolve_branch_package(source, api, headers):
    repo = source["repository"]
    directory = source["package_directory"]
    query = urlencode({"sha": source["package_branch"], "path": directory, "per_page": 1})
    commit = json.loads(download(f"{api}/commits?{query}", headers))[0]["sha"]
    base_url = f"https://raw.githubusercontent.com/{repo}/{commit}/{directory}"
    tag = download(f"{base_url}/version").decode("utf-8").partition("\n")[0].strip()
    if not VERSION_TAG.fullmatch(tag):
        raise ValueError(f"{repo}/{directory} has an invalid version: {tag}")

    name = f"{source['package']}-{tag[1:]}.apk"
    checksums = apk_checksums(download(f"{base_url}/{source['checksums']}"))
    if checksums.keys() != {name}:
        raise ValueError(f"{repo}/{directory} {tag} has an unexpected APK checksum list")
    release = {
        "repository": repo,
        "package": source["package"],
        "tag": tag,
        "package_commit": commit,
        "package_directory": directory,
        "assets": checksums,
    }
    asset = {"name": name, "browser_download_url": f"{base_url}/{name}", "digest": checksums[name]}
    return release, [asset]


def resolve_release(source):
    repo = source["repository"]
    api = f"https://api.github.com/repos/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    token_env = source.get("read_token_env", "GH_TOKEN")
    token = os.environ.get(token_env)
    if source.get("read_token_env") and not token:
        raise ValueError(f"{repo} requires the {token_env} Actions secret")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "package_branch" in source:
        return resolve_branch_package(source, api, headers)

    data = json.loads(download(f"{api}/releases/latest", headers))
    tag = data["tag_name"]
    if data["draft"] or data["prerelease"] or not VERSION_TAG.fullmatch(tag):
        raise ValueError(f"{repo} {tag} is not a stable release")

    assets = {asset["name"]: asset for asset in data["assets"] if asset["state"] == "uploaded"}
    checksum_asset = assets.get(source["checksums"])
    if not checksum_asset:
        raise ValueError(f"{repo} {tag} is missing {source['checksums']}")

    checksums = apk_checksums(verified_asset(checksum_asset, source))

    apk_assets = {name: asset for name, asset in assets.items() if name.endswith(".apk")}
    if not checksums or apk_assets.keys() != checksums.keys():
        missing = sorted(checksums.keys() - apk_assets.keys())
        unlisted = sorted(apk_assets.keys() - checksums.keys())
        raise ValueError(f"{repo} {tag} has an incomplete APK set: missing {missing}, unlisted {unlisted}")
    for name, digest in checksums.items():
        if apk_assets[name].get("digest") != digest:
            raise ValueError(f"{repo} {tag} checksum differs from GitHub for {name}")

    package_field = "packages" if "packages" in source else "package"
    release = {"repository": repo, package_field: source[package_field], "tag": tag, "assets": checksums}
    if source.get("read_token_env"):
        commit = json.loads(download(f"{api}/commits/{tag}", headers))
        release["source_commit"] = commit["sha"]
    return release, list(apk_assets.values())


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

    temporary = raw / asset["name"]
    temporary.write_bytes(archive)
    package = json.loads(apk(site, "adbdump", "--format", "json", f"/feed/.incoming/{asset['name']}"))["info"]
    name, version, arch = package["name"], package["version"], package["arch"]
    tag_version = release["tag"][1:]
    version_pattern = re.escape(tag_version)
    if "-r" not in tag_version and "package_commit" not in release:
        version_pattern += r"-r\d+"
    if name not in package_names(release) or not re.fullmatch(version_pattern, version):
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
        elif "package_commit" in item:
            url = f"https://github.com/{repo}/tree/{item['package_commit']}/{item['package_directory']}"
            label = f"{repo.split('/')[-1]} {item['package_directory']} {tag}"
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

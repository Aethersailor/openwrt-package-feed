#!/usr/bin/env python3
"""Publish upstream APKs as a signed OpenWrt feed."""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import file_digest
from html import escape
import json
import os
from pathlib import Path
import re
from shutil import copyfile, rmtree
import subprocess
import sys
import tempfile
from urllib.error import HTTPError

from bluetooth import resolve_build as resolve_bluetooth_build
from feed_sources import VERSION_TAG, download, resolve_release, verified_asset
from fm350 import resolve_build as resolve_fm350_build


APK_IMAGE = "mirror.gcr.io/library/alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "releases.json"
PUBLISHED_FEED = "https://aethersailor.github.io/openwrt-package-feed"
PACKAGE_ROOT = Path("immortalwrt/apk/packages")
PACKAGE_FILES = PACKAGE_ROOT / "files"
PACKAGE_NAME_SPEC = f"../{PACKAGE_FILES.name}/" + '${name}-${hash}.apk'


def package_names(release):
    return [release["package"]] if "package" in release else release.get("packages", [])


def published_state():
    try:
        return json.loads(download(f"{PUBLISHED_FEED}/release-state.json", {"Cache-Control": "no-cache"}))
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
    result = subprocess.run(command, text=True, capture_output=True)
    if result.stderr:
        warning = f"not matching package name specification '{PACKAGE_NAME_SPEC}'"
        for line in result.stderr.splitlines():
            if args[0] == "mkndx" and not result.returncode and line.startswith("WARNING: ") and line.endswith(warning):
                continue
            print(line, file=sys.stderr)
    result.check_returncode()
    return result.stdout


def stage_package(site, release, asset, source, raw):
    archive = verified_asset(asset, source)

    with tempfile.NamedTemporaryFile(dir=raw, suffix=".apk", delete=False) as staged:
        staged.write(archive)
    temporary = Path(staged.name)
    package = json.loads(apk(site, "adbdump", "--format", "json", f"/feed/.incoming/{temporary.name}"))["info"]
    name, version, arch = package["name"], package["version"], package["arch"]
    expected_names = package_names(release)
    if expected_names and name not in expected_names:
        raise ValueError(f"Unexpected package identity in {asset['name']}: {name} {version}")
    filename = Path(f"{name}-{version}.apk").name
    target_arch = asset.get("apk_arch") or source.get("apk_arch", arch)
    if "build_architectures" in release:
        target_arch, _, asset_filename = asset["name"].partition("--")
        if target_arch not in release["build_architectures"] or asset_filename != filename:
            raise ValueError(f"Unexpected build identity in {asset['name']}: {name} {version} {arch}")
    elif expected_names:
        tag_version = VERSION_TAG.fullmatch(release["tag"])[1]
        version_pattern = re.escape(tag_version)
        if "-r" not in tag_version and "package_commit" not in release:
            version_pattern += r"-r\d+"
        if not re.fullmatch(version_pattern, version):
            raise ValueError(f"Unexpected package version in {asset['name']}: {version}")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9_.-]+", target_arch)
        or arch not in ("noarch", target_arch)
        or release.get("architecture", target_arch) != target_arch
    ):
        raise ValueError(f"Invalid architecture in {asset['name']}: {arch}")
    arch = target_arch

    temporary.chmod(0o644)
    return arch, name, version, filename, temporary


def place_package(incoming, release, asset, staged, published):
    arch, name, version, filename, temporary = staged
    directory = incoming / arch
    destination = directory / filename
    if owner := published.get(destination):
        if owner is not release or package_names(release) or "archive_key" in asset:
            raise ValueError(f"Duplicate package {name} for {arch}")
        # A Release may contain two builds that declare the same APK architecture.
        if asset["name"].endswith((f"_{arch}.apk", f"-{arch}.apk")):
            temporary.replace(destination)
        else:
            temporary.unlink()
        return None

    directory.mkdir(parents=True, exist_ok=True)
    temporary.rename(destination)
    published[destination] = release
    print(f"{arch}: {name} {version}", flush=True)
    return destination


def add_shared_packages(architectures):
    shared = architectures.pop("noarch", [])
    if not shared:
        return

    for arch, entries in architectures.items():
        names = {name for name, _, _ in entries}
        for name, version, path in shared:
            if name in names:
                raise ValueError(f"Duplicate package {name} for {arch}")
            entries.append((name, version, path))


def write_index(site, key, index_path, files):
    # Staged filenames use versions. Published filenames use index digests.
    apk(site, "mkndx", "--allow-untrusted", "--sign-key", "/signing-key.pem",
        "--pkgname-spec", PACKAGE_NAME_SPEC, "--output", index_path,
        *(f"/feed/{package.relative_to(site).as_posix()}" for package in files), key=key)


def index_packages(site, key, arch, entries, published_files):
    path = PACKAGE_ROOT / arch
    directory = site / path
    directory.mkdir()
    index_path = f"/feed/{(path / 'packages.adb').as_posix()}"
    write_index(site, key, index_path, (package for _, _, package in entries))
    packages = json.loads(apk(site, "adbdump", "--format", "json", index_path))["packages"]
    versions = {package["name"]: package["version"] for package in packages}
    expected = {name: version for name, version, _ in entries}
    if versions != expected or any(package["arch"] not in (arch, "noarch") for package in packages):
        raise ValueError(f"Unexpected index content for {arch}")

    staged_paths = {name: path for name, _, path in entries}
    files = []
    sizes_match = True
    for package in packages:
        name = package["name"]
        staged = staged_paths[name]
        filename = Path(f"{name}-{package['hashes']}.apk").name
        path = PACKAGE_FILES / filename
        destination = site / path
        if not destination.exists():
            copyfile(staged, destination)
        published_files[staged] = path
        files.append(destination)
        sizes_match = sizes_match and destination.stat().st_size == package["file-size"]

    if not sizes_match:
        # APKs with the same identity can have different signature bytes.
        write_index(site, key, index_path, files)

    apk(site, "--keys-dir", "/feed/keys", "--repositories-file", "/dev/null",
        "--repository", index_path, "--arch", arch,
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


def write_site_home(site, releases, feeds):
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
    feed_links = []
    for arch in sorted(feeds):
        relative = (PACKAGE_ROOT / arch).as_posix()
        feed_links.append(
            f'<li><a href="{escape(relative)}/packages.adb">{escape(arch)}</a>'
            f' · <a href="{escape(relative)}/index.json">版本索引</a></li>'
        )
    feed_links_html = "\n".join(feed_links)
    (site / "index.html").write_text(
        '<!doctype html>\n<html lang="zh-CN">\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Aethersailor OpenWrt Package Feed</title>\n'
        '<h1>Aethersailor OpenWrt Package Feed</h1>\n'
        '<p><a href="https://github.com/Aethersailor/openwrt-package-feed#readme">安装说明</a>'
        ' · <a href="keys/aethersailor-feed.pem">签名公钥</a></p>\n'
        f'<h2>软件发布与对应源码</h2>\n<ul>\n{releases_html}\n</ul>\n'
        f'<h2>按架构浏览软件源</h2>\n<ul>\n{feed_links_html}\n</ul>\n</html>\n',
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--force", action="store_true", help="Rebuild an unchanged feed")
    args = parser.parse_args()

    sources = json.loads(CATALOG.read_text(encoding="utf-8"))["releases"]
    previous_state = None if args.force else published_state()
    previous_archives = {
        release["repository"]: release
        for release in (previous_state or {}).get("releases", [])
        if "archives" in release
    }
    resolved = [
        resolve_release(source, previous=previous_archives.get(source["repository"]), feed_url=PUBLISHED_FEED)
        for source in sources
    ]
    for resolve_build in (resolve_fm350_build, resolve_bluetooth_build):
        if build := resolve_build():
            resolved.append(build)
            sources.append({"repository": build[0]["repository"]})
    releases = [release for release, _ in resolved]
    state = {"package_name_spec": PACKAGE_NAME_SPEC, "releases": releases}
    if state == previous_state:
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
    feeds = defaultdict(list)
    archived_packages = []
    published = {}
    with ThreadPoolExecutor(max_workers=6) as workers:
        staged = workers.map(
            lambda item: stage_package(site, *item, incoming), packages
        )
        for (release, asset, _), package in zip(packages, staged):
            if path := place_package(incoming, release, asset, package, published):
                arch, name, version, _, _ = package
                feeds[arch].append((name, version, path))
                if key := asset.get("archive_key"):
                    archived_packages.append((release, key, path))
    expected = {name for release in releases for name in package_names(release)}
    found = {name for entries in feeds.values() for name, _, _ in entries}
    if missing := expected - found:
        raise ValueError(f"Missing APKs for configured packages: {sorted(missing)}")
    add_shared_packages(feeds)
    (site / PACKAGE_FILES).mkdir(parents=True)

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
        published_files = {}
        for arch, entries in sorted(feeds.items()):
            index_packages(site, key, arch, entries, published_files)

    archived_file_digests = {}
    for release, key, path in archived_packages:
        relative = published_files[path]
        archive = release["archives"]
        archive["paths"][key] = relative.as_posix()
        if relative not in archived_file_digests:
            with (site / relative).open("rb") as file:
                archived_file_digests[relative] = "sha256:" + file_digest(file, "sha256").hexdigest()
        digest = archived_file_digests[relative]
        if digest != release["assets"][key]:
            archive.setdefault("published_digests", {})[key] = digest
    rmtree(incoming)
    write_site_home(site, releases, feeds)
    (site / "release-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Published candidate: {len(set(published_files.values()))} unique APKs and {len(feeds)} signed indexes")


if __name__ == "__main__":
    main()

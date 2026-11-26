"""Resolve and verify the APKs from configured release sources."""

import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import tarfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


VERSION_TAG = re.compile(r"v\.?(\d+\.\d+(?:\.\d+(?:-r\d+)?|-r\d+))")
SHA256_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


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


def github_json(repository, path):
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(download(f"https://api.github.com/repos/{repository}/{path}", headers))


def verified_asset(asset, source):
    if "payload" in asset:
        payload = asset["payload"]
    elif published_url := asset.get("published_url"):
        payload = download(published_url, {"Cache-Control": "no-cache"})
    elif "id" in asset and (token := os.environ.get(source.get("read_token_env", "GH_TOKEN"))):
        payload = download(
            f"https://api.github.com/repos/{source['repository']}/releases/assets/{asset['id']}",
            {"Accept": "application/octet-stream", "Authorization": f"Bearer {token}"},
        )
    else:
        payload = download(asset["browser_download_url"])
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    expected_digest = asset.get("published_digest", asset.get("digest"))
    if digest != expected_digest or ("size" in asset and len(payload) != asset["size"]):
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
    if not (version := VERSION_TAG.fullmatch(tag)):
        raise ValueError(f"{repo}/{directory} has an invalid version: {tag}")

    name = f"{source['package']}-{version[1]}.apk"
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


def is_apk_release(release):
    return (
        not release["draft"] and not release["prerelease"]
        and any(name.endswith(".apk") for name in uploaded_assets(release))
    )


def resolve_release(source, *, previous=None, feed_url=None):
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
    if source.get("search_apk_releases") and not is_apk_release(data):
        # FM350 publishes both package and modem firmware releases.
        candidates = json.loads(download(f"{api}/releases?per_page=100", headers))
        data = next((item for item in candidates if is_apk_release(item)), data)
    tag = data["tag_name"]

    release = {"repository": repo, "tag": tag}
    if "apk_archive_pattern" in source:
        assets = uploaded_assets(data)
        apks, archive_state = bundled_apks(source, assets, previous or {}, feed_url)
        checksums, apk_assets = select_release_apks(source, tag, apks)
        release["archives"] = archive_state
    else:
        checksums, apk_assets = release_apks(source, data)
    release["assets"] = checksums
    if source.get("read_token_env"):
        commit = json.loads(download(f"{api}/commits/{quote(tag, safe='')}", headers))
        release["source_commit"] = commit["sha"]
    return release, apk_assets


def bundled_apks(source, assets, previous, feed_url):
    previous_archives = previous.get("archives", {})
    archive_state = {
        "asset_pattern": source.get("apk_asset_pattern"),
        "apk_arch": source.get("apk_arch"),
        "digests": {},
        "paths": {},
    }
    pattern = re.compile(source["apk_archive_pattern"])
    bundles = [
        (match.groupdict().get("arch"), asset)
        for name, asset in assets.items() if (match := pattern.fullmatch(name))
    ]
    if "arch" not in pattern.groupindex and len(bundles) != 1:
        raise ValueError(f"{source['repository']} expected one APK archive matching {pattern.pattern}, found {len(bundles)}")

    apks = {}
    for arch, bundle in bundles:
        digest = bundle.get("digest")
        if not SHA256_DIGEST.fullmatch(digest or ""):
            raise ValueError(f"{source['repository']} has no valid GitHub SHA-256 for {bundle['name']}")
        slot = arch or ""
        archive_state["digests"][slot] = digest
        if (
            feed_url
            and previous_archives.get("asset_pattern") == archive_state["asset_pattern"]
            and previous_archives.get("digests", {}).get(slot) == digest
        ):
            paths = {key: path for key, path in previous_archives["paths"].items() if key.rpartition("/")[0] == slot}
            if paths:
                print(f"Archive unchanged: {source['repository']} {bundle['name']}", flush=True)
                archive_state["paths"].update(paths)
                published_digests = previous_archives.get("published_digests", {})
                for key, path in paths.items():
                    asset = {
                        "name": PurePosixPath(key).name,
                        "digest": previous["assets"][key],
                        "published_url": f"{feed_url}/{quote(path)}",
                        "apk_arch": arch,
                        "archive_key": key,
                    }
                    if key in published_digests:
                        published_digest = published_digests[key]
                        asset["published_digest"] = published_digest
                        archive_state.setdefault("published_digests", {})[key] = published_digest
                    apks[key] = asset
                continue
        print(f"Downloading archive: {source['repository']} {bundle['name']}", flush=True)
        apks.update(extract_archive_apks(source, bundle, arch))
    return apks, archive_state


def extract_archive_apks(source, asset, arch):
    payload = verified_asset(asset, source)
    apks = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
        for member in bundle:
            name = PurePosixPath(member.name).name
            if not member.isfile() or not name.endswith(".apk"):
                continue
            key = f"{arch}/{name}" if arch else name
            if key in apks:
                raise ValueError(f"Duplicate APK in {asset['name']}: {name}")
            with bundle.extractfile(member) as file:
                package = file.read()
            apks[key] = {
                "name": name,
                "digest": "sha256:" + hashlib.sha256(package).hexdigest(),
                "payload": package,
                "apk_arch": arch,
                "archive_key": key,
            }
    return apks


def uploaded_assets(data):
    return {asset["name"]: asset for asset in data["assets"] if asset["state"] == "uploaded"}


def release_apks(source, data):
    assets = uploaded_assets(data)
    apks = {name: asset for name, asset in assets.items() if name.endswith(".apk")}
    return select_release_apks(source, data["tag_name"], apks)


def select_release_apks(source, tag, apk_assets):
    repo = source["repository"]
    if pattern := source.get("apk_asset_pattern"):
        apk_assets = {
            key: asset for key, asset in apk_assets.items() if re.fullmatch(pattern, asset["name"])
        }
    if not apk_assets:
        raise ValueError(f"{repo} {tag} has no matching APK assets")

    unique = {}
    for key, asset in sorted(apk_assets.items()):
        digest = asset.get("digest")
        if not SHA256_DIGEST.fullmatch(digest or ""):
            raise ValueError(f"{repo} {tag} has no valid GitHub SHA-256 for {key}")
        unique.setdefault((digest, asset.get("apk_arch")), (key, asset))
    return {key: asset["digest"] for key, asset in unique.values()}, [asset for _, asset in unique.values()]

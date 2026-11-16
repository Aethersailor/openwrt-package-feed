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
from urllib.parse import urlencode
from urllib.request import Request, urlopen


VERSION_TAG = re.compile(r"v\.?(\d+\.\d+(?:\.\d+(?:-r\d+)?|-r\d+))")


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
    elif token_env := source.get("read_token_env"):
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
    if "packages" in source:
        return resolve_branch_apks(source, api, headers, commit, base_url)

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


def resolve_branch_apks(source, api, headers, commit, base_url):
    directory = source["package_directory"]
    files = json.loads(download(f"{api}/contents/{directory}?ref={commit}", headers))
    pattern = re.compile(source["apk_asset_pattern"])
    selected = {}
    for file in files:
        if file["type"] != "file" or not (match := pattern.fullmatch(file["name"])):
            continue
        identity = match.groupdict()
        name = identity["name"]
        if name in selected:
            raise ValueError(f"Multiple APKs for {name} in {source['repository']}/{directory}")
        payload = download(f"{base_url}/{file['name']}")
        blob = f"blob {len(payload)}\0".encode("ascii") + payload
        if len(payload) != file["size"] or hashlib.sha1(blob).hexdigest() != file["sha"]:
            raise ValueError(f"Git blob checksum mismatch for {file['name']}")
        selected[name] = {
            "name": file["name"],
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "payload": payload,
            "apk_identity": identity,
        }
    if missing := set(source["packages"]) - selected.keys():
        raise ValueError(f"Missing APKs in {source['repository']}/{directory}: {sorted(missing)}")
    assets = [selected[name] for name in source["packages"]]
    release = {
        "repository": source["repository"],
        "packages": source["packages"],
        "tag": assets[0]["apk_identity"]["version"],
        "package_commit": commit,
        "package_directory": directory,
        "assets": {asset["name"]: asset["digest"] for asset in assets},
    }
    return release, assets


def is_stable_release(release, tag_pattern):
    return not release["draft"] and not release["prerelease"] and re.fullmatch(tag_pattern, release["tag_name"]) is not None


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

    tag_pattern = source.get("release_tag_pattern", VERSION_TAG)
    data = json.loads(download(f"{api}/releases/latest", headers))
    if source.get("versioned_releases") and not is_stable_release(data, tag_pattern):
        # Some upstreams also publish firmware under non-version tags.
        candidates = json.loads(download(f"{api}/releases?per_page=100", headers))
        data = next((item for item in candidates if is_stable_release(item, tag_pattern)), data)
    if source.get("apk_release_fallback") and not has_release_apk(data):
        # A failed upstream build can leave the newest release without APKs.
        candidates = json.loads(download(f"{api}/releases?per_page=20", headers))
        data = next((item for item in candidates if is_stable_release(item, tag_pattern) and has_release_apk(item)), data)
    tag = data["tag_name"]
    if not is_stable_release(data, tag_pattern):
        raise ValueError(f"{repo} {tag} is not a stable release")

    checksums, apk_assets = release_apks(source, data)

    package_field = "packages" if "packages" in source else "package"
    release = {"repository": repo, package_field: source[package_field], "tag": tag, "assets": checksums}
    if "feed_subdirectory" in source:
        release["feed_subdirectory"] = source["feed_subdirectory"]
    if source.get("read_token_env"):
        commit = json.loads(download(f"{api}/commits/{tag}", headers))
        release["source_commit"] = commit["sha"]
    return release, apk_assets


def has_release_apk(release):
    return any(asset["state"] == "uploaded" and asset["name"].endswith(".apk") for asset in release["assets"])


def bundled_apks(source, assets):
    pattern = source["apk_archive_pattern"]
    apks = {}
    for arch in source.get("apk_archive_architectures", [None]):
        archive_pattern = pattern.replace("{arch}", re.escape(arch)) if arch else pattern
        bundles = [asset for name, asset in assets.items() if re.fullmatch(archive_pattern, name)]
        if len(bundles) != 1:
            raise ValueError(f"{source['repository']} expected one APK archive matching {archive_pattern}, found {len(bundles)}")

        payload = verified_asset(bundles[0], source)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            for member in bundle:
                name = PurePosixPath(member.name).name
                if not member.isfile() or not name.endswith(".apk"):
                    continue
                key = f"{arch}/{name}" if arch else name
                if key in apks:
                    raise ValueError(f"Duplicate APK in {bundles[0]['name']}: {name}")
                with bundle.extractfile(member) as file:
                    package = file.read()
                apks[key] = {
                    "name": name,
                    "digest": "sha256:" + hashlib.sha256(package).hexdigest(),
                    "payload": package,
                    "apk_arch": arch,
                }
    return apks


def release_apks(source, data):
    repo, tag = source["repository"], data["tag_name"]
    assets = {asset["name"]: asset for asset in data["assets"] if asset["state"] == "uploaded"}
    if "apk_archive_pattern" in source:
        apk_assets = bundled_apks(source, assets)
    else:
        apk_assets = {name: asset for name, asset in assets.items() if name.endswith(".apk")}
    if not apk_assets:
        raise ValueError(f"{repo} {tag} has no APK assets")
    checksums = {name: asset.get("digest") for name, asset in apk_assets.items()}
    for name, digest in checksums.items():
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
            raise ValueError(f"{repo} {tag} has no valid GitHub SHA-256 for {name}")

    if "checksums" in source:
        checksum_asset = assets.get(source["checksums"])
        if not checksum_asset:
            raise ValueError(f"{repo} {tag} is missing {source['checksums']}")
        manifest = apk_checksums(verified_asset(checksum_asset, source))
        if apk_assets.keys() != manifest.keys():
            missing = sorted(manifest.keys() - apk_assets.keys())
            unlisted = sorted(apk_assets.keys() - manifest.keys())
            raise ValueError(f"{repo} {tag} has an incomplete APK set: missing {missing}, unlisted {unlisted}")
        for name, digest in manifest.items():
            if checksums[name] != digest:
                raise ValueError(f"{repo} {tag} checksum differs from GitHub for {name}")

    if pattern := source.get("apk_asset_pattern"):
        selected = {
            key: {**asset, "apk_identity": match.groupdict()}
            for key, asset in apk_assets.items() if (match := re.fullmatch(pattern, asset["name"]))
        }
        if not selected:
            raise ValueError(f"{repo} {tag} has no APK assets matching {pattern}")
        for arch in source.get("apk_archive_architectures", []):
            names = {asset["apk_identity"]["name"] for asset in selected.values() if asset["apk_arch"] == arch}
            if missing := set(source["packages"]) - names:
                raise ValueError(f"{repo} {tag} is missing APKs for {arch}: {sorted(missing)}")
        return {key: asset["digest"] for key, asset in selected.items()}, list(selected.values())

    return checksums, list(apk_assets.values())

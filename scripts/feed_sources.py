"""Resolve and verify the APKs from configured release sources."""

import hashlib
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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


def is_stable_release(release):
    return not release["draft"] and not release["prerelease"] and VERSION_TAG.fullmatch(release["tag_name"]) is not None


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
    if source.get("versioned_releases") and not is_stable_release(data):
        # Some upstreams also publish firmware under non-version tags.
        candidates = json.loads(download(f"{api}/releases?per_page=100", headers))
        data = next((item for item in candidates if is_stable_release(item)), data)
    tag = data["tag_name"]
    if not is_stable_release(data):
        raise ValueError(f"{repo} {tag} is not a stable release")

    assets = {asset["name"]: asset for asset in data["assets"] if asset["state"] == "uploaded"}
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

    package_field = "packages" if "packages" in source else "package"
    release = {"repository": repo, package_field: source[package_field], "tag": tag, "assets": checksums}
    if source.get("read_token_env"):
        commit = json.loads(download(f"{api}/commits/{tag}", headers))
        release["source_commit"] = commit["sha"]
    return release, list(apk_assets.values())

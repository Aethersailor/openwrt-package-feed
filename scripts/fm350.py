"""Check for a new FM350 build and resolve the last published generic APK."""

import argparse
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode

from feed_sources import VERSION_TAG, download, resolve_release, verified_asset


UPSTREAM = "LianXia233/luci-app-fm350"
BUILD_REPOSITORY = "Aethersailor/openwrt-package-feed"
PACKAGE = "luci-app-fm350"
ARCH = "aarch64_generic"
TAG_PREFIX = "fm350-generic-"
MANIFEST = "build-info.json"


def github_json(repository, path):
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return json.loads(download(f"https://api.github.com/repos/{repository}/{path}", headers))


def latest_build():
    releases = github_json(BUILD_REPOSITORY, "releases?per_page=100")
    return next(
        (release for release in releases if not release["draft"] and release["tag_name"].startswith(TAG_PREFIX)),
        None,
    )


def resolve_build():
    build = latest_build()
    if build is None:
        return None

    source = {"repository": BUILD_REPOSITORY}
    assets = {asset["name"]: asset for asset in build["assets"] if asset["state"] == "uploaded"}
    info = json.loads(verified_asset(assets[MANIFEST], source))
    version = info["version"]
    tag = f"v{version}"
    if not VERSION_TAG.fullmatch(tag) or info["architecture"] != ARCH:
        raise ValueError(f"Invalid FM350 build identity in {build['tag_name']}")
    name = f"{PACKAGE}-{version}.apk"
    asset = assets[name]
    if info["apk_digest"] != asset["digest"] or build["tag_name"] != TAG_PREFIX + info["upstream_digest"][7:]:
        raise ValueError(f"FM350 build manifest differs from {build['tag_name']}")
    if not re.fullmatch(r"[a-f0-9]{40}", info["source_commit"]):
        raise ValueError("Invalid FM350 source commit")

    release = {
        "repository": BUILD_REPOSITORY,
        "package": PACKAGE,
        "tag": tag,
        "assets": {name: asset["digest"]},
        "build_tag": build["tag_name"],
        "build_source": f"https://github.com/{UPSTREAM}/tree/{info['source_commit']}",
        "architecture": ARCH,
    }
    return release, [asset]


def source_commit(upstream_tag, asset, version):
    # A workflow_dispatch can replace APKs without moving the release tag.
    # Find the successful upstream release run active when the bot uploaded this APK.
    uploaded = asset["created_at"]
    query = urlencode({"status": "success", "created": f"<={uploaded}", "per_page": 30})
    runs = github_json(UPSTREAM, f"actions/workflows/release.yml/runs?{query}")["workflow_runs"]
    matches = [
        run for run in runs
        if run["run_started_at"] <= uploaded <= run["updated_at"]
    ]
    if asset["uploader"]["login"] != "github-actions[bot]" or len(matches) != 1:
        raise ValueError(f"Cannot identify the upstream release run for {asset['name']} uploaded at {uploaded}")
    run = matches[0]
    artifact_query = urlencode({"name": f"{PACKAGE}-{upstream_tag}"})
    artifacts = github_json(UPSTREAM, f"actions/runs/{run['id']}/artifacts?{artifact_query}")["artifacts"]
    if not artifacts:
        raise ValueError(f"Upstream run {run['id']} has no artifact for {upstream_tag}")

    commit = run["head_sha"]
    makefile = download(f"https://raw.githubusercontent.com/{UPSTREAM}/{commit}/Makefile").decode("utf-8")
    fields = dict(re.findall(r"^(PKG_VERSION|PKG_RELEASE):=([^\r\n]+)", makefile, re.MULTILINE))
    if f"{fields['PKG_VERSION']}-r{fields['PKG_RELEASE']}" != version:
        raise ValueError(f"FM350 build run {run['id']} does not match package version {version}")
    return commit, run["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_plan", type=Path)
    args = parser.parse_args()
    catalog = json.loads((Path(__file__).resolve().parents[1] / "releases.json").read_text(encoding="utf-8"))
    source = next(source for source in catalog["releases"] if source["repository"] == UPSTREAM)
    release, assets = resolve_release(source)
    (asset,) = assets
    digest = asset["digest"]
    build_tag = TAG_PREFIX + digest[7:]
    if (build := latest_build()) and build["tag_name"] == build_tag:
        print(f"Upstream {asset['name']} is already built for {ARCH}.")
        return

    match = re.fullmatch(r"luci-app-fm350-([0-9.]+-r[0-9]+)\.apk", asset["name"])
    if not match:
        raise ValueError(f"Unexpected upstream APK name: {asset['name']}")
    version = match[1]
    commit, run_url = source_commit(release["tag"], asset, version)
    info = {
        "version": version,
        "architecture": ARCH,
        "upstream_tag": release["tag"],
        "upstream_apk": asset["name"],
        "upstream_digest": digest,
        "upstream_run": run_url,
        "source_commit": commit,
        "build_tag": build_tag,
    }
    args.build_plan.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    print(f"Build {PACKAGE} {version} for {ARCH} from {commit}.")


if __name__ == "__main__":
    main()

"""Build and resolve the locally patched noarch owut package."""

import argparse
import json
from pathlib import Path
import re

from feed_sources import download, github_json, verified_asset


BUILD_REPOSITORY = "Aethersailor/openwrt-package-feed"
RECIPE_REPOSITORY = "immortalwrt/packages"
RECIPE_PATH = "utils/owut/Makefile"
TAG_PREFIX = "owut-"
VERSION = re.compile(r"\d{4}\.\d{2}\.\d{2}~[0-9a-f]{8}-r[1-9]\d*")
MANIFEST = "build-info.json"


def build_tag(version):
    return TAG_PREFIX + version.replace("~", "-")


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
    if (
        not VERSION.fullmatch(version)
        or build["tag_name"] != build_tag(version)
        or not re.fullmatch(r"[0-9a-f]{40}", info["source_commit"])
        or info["architecture"] != "noarch"
    ):
        raise ValueError(f"Invalid owut build identity in {build['tag_name']}")

    # GitHub normalizes characters such as "~" in uploaded asset names.
    asset = next(
        (asset for asset in assets.values()
         if asset["name"].endswith(".apk") and asset["digest"] == info["apk_digest"]),
        None,
    )
    if asset is None:
        raise ValueError(f"owut APK digest differs from {build['tag_name']}")

    release = {
        "repository": BUILD_REPOSITORY,
        "package": "owut",
        "tag": f"v{version}",
        "build_version": version,
        "assets": {asset["name"]: asset["digest"]},
        "build_tag": build["tag_name"],
        "build_source": f"https://github.com/efahl/owut/tree/{info['source_commit']}",
        "architecture": "noarch",
    }
    return release, [asset]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_plan", type=Path)
    args = parser.parse_args()

    recipe_commit = github_json(RECIPE_REPOSITORY, f"commits?path={RECIPE_PATH}&per_page=1")[0]["sha"]
    recipe_url = f"https://raw.githubusercontent.com/{RECIPE_REPOSITORY}/{recipe_commit}/{RECIPE_PATH}"
    recipe = download(recipe_url).decode()
    fields = dict(re.findall(r"^(PKG_SOURCE_DATE|PKG_SOURCE_VERSION|PKG_RELEASE):=(\S+)$", recipe, re.MULTILINE))
    date = fields["PKG_SOURCE_DATE"].replace("-", ".")
    source_commit = fields["PKG_SOURCE_VERSION"]
    package_release = int(fields["PKG_RELEASE"]) + 1
    version = f"{date}~{source_commit[:8]}-r{package_release}"
    if not VERSION.fullmatch(version) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("Unrecognized upstream owut version")

    tag = build_tag(version)
    if (published := latest_build()) and published["tag_name"] == tag:
        print(f"owut {version} is already published.")
        return

    plan = {
        "architecture": "noarch",
        "version": version,
        "package_release": package_release,
        "source_commit": source_commit,
        "recipe_commit": recipe_commit,
        "recipe_url": recipe_url,
        "build_tag": tag,
    }
    args.build_plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

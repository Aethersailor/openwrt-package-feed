"""Plan Bluetooth builds and resolve the latest complete package set."""

from hashlib import sha256
import json
import re

from feed_sources import github_json, release_apks


REPOSITORY = "Aethersailor/openwrt-package-feed"
TAG_PREFIX = "bluetooth-"
SOURCES = {
    "luci-app-bluetooth": "sbwml/luci-app-bluetooth",
    "bluez-alsa": "sbwml/package_new_bluez-alsa",
    "expect": "sbwml/package_new_expect",
}
TARGETS = {
    "aarch64_generic": "rockchip/armv8",
    "aarch64_cortex-a53": "mediatek/filogic",
    "x86_64": "x86/64",
}
PACKAGES = (*SOURCES, "bluez-alsa-utils", "luci-i18n-bluetooth-zh-cn")


def latest_build():
    releases = github_json(REPOSITORY, "releases?per_page=100")
    return max(
        (release for release in releases if not release["draft"] and release["tag_name"].startswith(TAG_PREFIX)),
        key=lambda release: release["published_at"],
        default=None,
    )


def resolve_build():
    build = latest_build()
    if build is None:
        return None
    checksums, assets = release_apks({"repository": REPOSITORY}, build)
    by_architecture = {arch: set() for arch in TARGETS}
    for name in checksums:
        arch, _, filename = name.partition("--")
        package = next((pkg for pkg in PACKAGES if re.match(rf"{re.escape(pkg)}-[0-9]", filename)), None)
        if arch not in TARGETS or package is None or package in by_architecture[arch]:
            raise ValueError(f"Unexpected Bluetooth build asset: {name}")
        by_architecture[arch].add(package)
    if any(names != set(PACKAGES) for names in by_architecture.values()):
        raise ValueError("Bluetooth build is missing packages for a configured architecture")
    release = {
        "repository": REPOSITORY,
        "tag": build["tag_name"],
        "packages": list(PACKAGES),
        "assets": checksums,
        "build_architectures": sorted(TARGETS),
    }
    return release, assets


def main():
    commits = {package: github_json(repo, "commits/main") for package, repo in SOURCES.items()}
    sources = {
        package: {"repository": repo, "commit": commits[package]["sha"]}
        for package, repo in SOURCES.items()
    }
    digest = sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    tag = TAG_PREFIX + digest[:16]
    if (build := latest_build()) and build["tag_name"] == tag:
        return
    date = commits["luci-app-bluetooth"]["commit"]["committer"]["date"]
    release_date = date[:10].replace("-", "")
    release_time = int(date[11:19].replace(":", ""))
    print(json.dumps({
        "tag": tag,
        "version": f"0.{release_date}.{release_time}",
        "sources": sources,
        "targets": [{"arch": arch, "target": target} for arch, target in TARGETS.items()],
        "packages": PACKAGES,
    }))


if __name__ == "__main__":
    main()

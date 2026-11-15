#!/usr/bin/env bash
set -euo pipefail

plan=$1
arch=$2
target_name=$3
output=$(realpath -m "$4")
mkdir -p "$output"
work=$(mktemp -d "${RUNNER_TEMP:?}/bluetooth-sdk-XXXXXX")
cd "$work"

base=https://downloads.immortalwrt.org/snapshots
target="$base/targets/$target_name"
curl -fsSL --retry 3 -O "$target/sha256sums"
sdk=$(awk -v prefix="*immortalwrt-sdk-${target_name//\//-}_" 'index($2, prefix) == 1 && $2 ~ /\.Linux-x86_64\.tar\.zst$/ {print substr($2, 2); exit}' sha256sums)
test -n "$sdk"
sdk_sha=$(awk -v name="*$sdk" '$2 == name {print $1}' sha256sums)
curl -fsSL --retry 3 -O "$target/$sdk" -O "$target/version.buildinfo"
awk -v name="*$sdk" '$2 == name || $2 == "*version.buildinfo"' sha256sums | sha256sum -c -
snapshot=$(cat version.buildinfo)
echo "Building Bluetooth packages for $arch using $snapshot"

mkdir sdk
tar --zstd -xf "$sdk" --strip-components=1 -C sdk
rm -- "$sdk"
cd sdk
while IFS=$'\t' read -r package repository commit; do
    mkdir -p "package/$package"
    curl -fsSL --retry 3 "https://codeload.github.com/$repository/tar.gz/$commit" |
        tar -xz --strip-components=1 -C "package/$package"
done < <(jq -r '.sources | to_entries[] | [.key, .value.repository, .value.commit] | @tsv' <<< "$plan")

patch_path=$(jq -er .patch.path <<< "$plan")
patch --batch --fuzz=0 --strip=1 --directory=package/luci-app-bluetooth < "$GITHUB_WORKSPACE/$patch_path"

# Upstream tracks a branch without tags. Use a version that tracks the app commit.
version=$(jq -er .version <<< "$plan")
release=$(jq -er .release <<< "$plan")
sed -i "/^include .*luci\.mk/i PKG_VERSION:=$version\nPKG_RELEASE:=$release\nPKG_PO_VERSION:=$version-r$release" package/luci-app-bluetooth/Makefile

# The SDK installs the target Tcl configuration and headers into staging.
sed -i '/^\$(eval .*BuildPackage,expect))/i CONFIGURE_ARGS += --with-tcl=$(STAGING_DIR)/usr/lib --with-tclinclude=$(STAGING_DIR)/usr/include' package/expect/Makefile

./scripts/feeds update -a
# Expect also reads private headers from the Tcl source tree via tclConfig.sh.
sed -i '/^include .*host-build\.mk/i CONFIG_AUTOREMOVE:=' feeds/packages/lang/tcl/Makefile
./scripts/feeds install luci-base bluez-daemon tcl coreutils-stty coreutils-timeout dbus-utils alsa-lib fdk-aac sbc libmpg123
target_board=${target_name%/*}
target_subtarget=${target_name#*/}
cat > .config <<EOF
CONFIG_TARGET_$target_board=y
CONFIG_TARGET_${target_board}_$target_subtarget=y
# CONFIG_ALL is not set
# CONFIG_ALL_KMODS is not set
# CONFIG_ALL_NONSHARED is not set
EOF
mapfile -t packages < <(jq -er '.packages[]' <<< "$plan")
translation=luci-i18n-bluetooth-zh-cn
for package in "${packages[@]}"; do
    [[ $package == "$translation" ]] || printf 'CONFIG_PACKAGE_%s=m\n' "$package"
done >> .config
make defconfig
for package in "${packages[@]}"; do
    [[ $package == "$translation" ]] && continue
    if ! grep -E "^CONFIG_PACKAGE_$package=[my]$" .config; then
        echo "SDK did not select $package" >&2
        grep -E '(^| )CONFIG_PACKAGE_(bluez|expect|luci-app-bluetooth|luci-i18n-bluetooth)' .config || true
        exit 1
    fi
done
# LuCI's translation package is hidden from the SDK's defconfig.
compile_log="$RUNNER_TEMP/bluetooth-compile.log"
make package/luci-app-bluetooth/compile "CONFIG_PACKAGE_$translation=m" \
    -j"$(nproc)" V=s > "$compile_log" 2>&1 || {
    grep -E '^make(\[[0-9]+\])?: \*\*\*|: (fatal )?error: |^configure: error|^ERROR:|No more mirrors|Hash mismatch' "$compile_log" | tail -n 40 || true
    tail -n 20 "$compile_log"
    exit 1
}

apk_image=$(sed -n 's/^APK_IMAGE = "\(.*\)"$/\1/p' "$GITHUB_WORKSPACE/scripts/build-feed.py")
docker pull "$apk_image"
archives=()
for package in "${packages[@]}"; do
    mapfile -t built < <(find bin -type f -name "$package-[0-9]*.apk")
    test "${#built[@]}" -eq 1
    filename=$(basename "${built[0]}")
    archive="$arch--$filename"
    cp "${built[0]}" "$output/$archive"
    metadata=$(docker run --rm --network none -v "$output:/packages:ro" "$apk_image" \
        apk adbdump --format json "/packages/$archive")
    jq -e --arg package "$package" --arg arch "$arch" --arg filename "$filename" \
        '.info | .name == $package and (.arch == $arch or .arch == "noarch")
        and (.name + "-" + .version + ".apk") == $filename' <<< "$metadata"
    archives+=("/packages/$archive")
done
test -f "$output/$arch--luci-app-bluetooth-$version-r$release.apk"

kmods=$(awk '$2 ~ /^\*kmods\/[^/]+\/kmod-bluetooth-[0-9].*\.apk$/ {
    split(substr($2, 2), path, "/"); print path[1] "/" path[2]; exit
}' "$work/sha256sums")
test -n "$kmods"
docker run --rm -v "$output:/packages:ro" "$apk_image" sh -ec '
    arch=$1
    shift
    apk add --initdb --root /tmp --arch "$arch" --repositories-file /dev/null
    apk add --no-cache --simulate --root /tmp --arch "$arch" "$@"
' bluetooth "$arch" --allow-untrusted --repositories-file /dev/null \
    --repository "$target/packages/packages.adb" \
    --repository "$target/$kmods/packages.adb" \
    --repository "$base/packages/$arch/base/packages.adb" \
    --repository "$base/packages/$arch/luci/packages.adb" \
    --repository "$base/packages/$arch/packages/packages.adb" \
    "${archives[@]}"

jq -n --arg architecture "$arch" --arg target "$target_name" --arg snapshot "$snapshot" \
    --arg sdk "$sdk" --arg sdk_sha256 "$sdk_sha" \
    '{$architecture, $target, $snapshot, $sdk, $sdk_sha256}' > "$output/sdk-$arch.json"

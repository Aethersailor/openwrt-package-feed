#!/usr/bin/env bash
set -euo pipefail

plan=$(realpath "$1")
output=$(realpath -m "$2")
mkdir -p "$output"
work=$(mktemp -d "${RUNNER_TEMP:?}/fm350-sdk-XXXXXX")
cd "$work"

package=luci-app-fm350
version=$(jq -er .version "$plan")
commit=$(jq -er .source_commit "$plan")
expected="$package-$version.apk"
target=https://downloads.immortalwrt.org/snapshots/targets/rockchip/armv8

curl --fail --silent --show-error --location --retry 3 -O "$target/sha256sums"
sdk=$(awk '$2 ~ /^\*immortalwrt-sdk-rockchip-armv8_.*\.Linux-x86_64\.tar\.zst$/ {print substr($2, 2); exit}' sha256sums)
test -n "$sdk"
sdk_sha=$(awk -v name="*$sdk" '$2 == name {print $1}' sha256sums)
curl --fail --silent --show-error --location --retry 3 -O "$target/$sdk" -O "$target/version.buildinfo"
awk -v name="*$sdk" '$2 == name || $2 == "*version.buildinfo"' sha256sums | sha256sum -c -
snapshot=$(cat version.buildinfo)
echo "Building $expected with $snapshot ($sdk)"

mkdir sdk
tar --zstd -xf "$sdk" --strip-components=1 -C sdk
rm -- "$sdk"
mkdir -p "sdk/package/$package"
curl --fail --silent --show-error --location --retry 3 \
    "https://codeload.github.com/LianXia233/luci-app-fm350/tar.gz/$commit" |
    tar -xz --strip-components=1 -C "sdk/package/$package"
cp "sdk/package/$package/rust/Cargo.lock" cargo.lock

rustup target add aarch64-unknown-linux-musl
cd sdk
./scripts/feeds update -a
./scripts/feeds install luci-base
cat > .config <<'EOF'
CONFIG_TARGET_rockchip=y
CONFIG_TARGET_rockchip_armv8=y
CONFIG_PACKAGE_luci-app-fm350=m
# CONFIG_ALL is not set
# CONFIG_ALL_KMODS is not set
# CONFIG_ALL_NONSHARED is not set
EOF
make defconfig
grep -Fx 'CONFIG_PACKAGE_luci-app-fm350=m' .config
make "package/$package/compile" -j"$(nproc)" V=s
cmp "$work/cargo.lock" "package/$package/rust/Cargo.lock"
mapfile -t apks < <(find bin -type f -name "$expected")
test "${#apks[@]}" -eq 1
cp "${apks[0]}" "$output/$expected"

backend="package/$package/rust/target/aarch64-unknown-linux-musl/release/fm350d"
file "$backend" | grep -F 'ARM aarch64'
if readelf -l "$backend" | grep -F 'INTERP'; then
    echo "Expected a statically linked fm350d" >&2
    exit 1
fi

digest=$(sha256sum "$output/$expected" | cut -d' ' -f1)
jq --arg apk_digest "sha256:$digest" --arg sdk "$sdk" --arg sdk_sha256 "$sdk_sha" --arg snapshot "$snapshot" \
    '. + {$apk_digest, $sdk, $sdk_sha256, $snapshot}' "$plan" > "$output/build-info.json"

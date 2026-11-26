#!/usr/bin/env bash
set -euo pipefail

plan=$(realpath "$1")
output=$(realpath -m "$2")
sdk=$(realpath -m "$3")
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(dirname "$sdk")
mkdir -p "$work" "$output"

target=https://downloads.immortalwrt.org/snapshots/targets/x86/64
cd "$work"
if [[ ! -f "$sdk/rules.mk" ]]; then
    curl --fail --silent --show-error --location --retry 3 -O "$target/sha256sums"
    sdk_archive=$(awk '$2 ~ /^\*immortalwrt-sdk-x86-64_.*\.Linux-x86_64\.tar\.zst$/ {print substr($2, 2); exit}' sha256sums)
    test -n "$sdk_archive"
    curl --fail --silent --show-error --location --retry 3 -O "$target/$sdk_archive" -O "$target/version.buildinfo"
    awk -v name="*$sdk_archive" '$2 == name || $2 == "*version.buildinfo"' sha256sums | sha256sum -c -
    mkdir -p "$sdk"
    tar --zstd -xf "$sdk_archive" --strip-components=1 -C "$sdk"
    rm -- "$sdk_archive"
fi

version=$(jq -er .version "$plan")
package_release=$(jq -er .package_release "$plan")
recipe_url=$(jq -er .recipe_url "$plan")
expected="owut-$version.apk"
mkdir -p "$sdk/package/owut/patches"
curl --fail --silent --show-error --location --retry 3 "$recipe_url" -o "$sdk/package/owut/Makefile"
sed -i -E "s/^PKG_RELEASE:=[0-9]+$/PKG_RELEASE:=$package_release/" "$sdk/package/owut/Makefile"
grep -Fx "PKG_RELEASE:=$package_release" "$sdk/package/owut/Makefile"
patch --fuzz=0 -p1 -d "$sdk/package/owut" < "$repo/patches/owut-package/010-add-curl-dependency.patch"
cp "$repo"/patches/owut/*.patch "$sdk/package/owut/patches/"

cd "$sdk"
./scripts/feeds update base packages luci
./scripts/feeds install attendedsysupgrade-common curl rpcd ucode
cat > .config <<'EOF'
CONFIG_TARGET_x86=y
CONFIG_TARGET_x86_64=y
CONFIG_PACKAGE_owut=y
# CONFIG_ALL is not set
# CONFIG_ALL_KMODS is not set
# CONFIG_ALL_NONSHARED is not set
EOF
make defconfig
grep -Fx 'CONFIG_PACKAGE_owut=y' .config
# The SDK top-level build normally creates this key before compiling packages.
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
make -C package/owut TOPDIR="$sdk" compile V=s
apk_path=$(find bin -type f -name "$expected")
test -f "$apk_path"
cp "$apk_path" "$output/$expected"

digest=$(sha256sum "$output/$expected" | cut -d' ' -f1)
snapshot=$(cat "$work/version.buildinfo")
jq --arg apk_digest "sha256:$digest" --arg snapshot "$snapshot" \
    '. + {$apk_digest, $snapshot}' "$plan" > "$output/build-info.json"

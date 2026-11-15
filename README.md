# Aethersailor OpenWrt Package Feed

Aethersailor 自行维护的公开软件源。软件源同步下列项目发布的 APK，保留原包内容和版本号。

| 软件 | APK 包名 | 发布来源 |
| --- | --- | --- |
| [SubConverter-Extended](https://github.com/Aethersailor/SubConverter-Extended/releases) | `subconverter-extended` | 自动同步 |
| [Rule-Bot Client](https://github.com/Aethersailor/Rule-Bot-Client/releases) | `luci-app-rule-bot-client` | 自动同步 |
| [Vantage](https://github.com/anothaDev/luci-theme-vantage/releases) | `luci-theme-vantage`、`luci-app-vantage`、`luci-i18n-vantage-theme-zh-cn`、`luci-i18n-vantage-zh-cn` | 自动同步 |
| [Aurora](https://github.com/eamonxg/luci-theme-aurora/releases) | `luci-theme-aurora` | 自动同步 |
| [LuCI 蓝牙管理](https://github.com/sbwml/luci-app-bluetooth) | `luci-app-bluetooth`、`luci-i18n-bluetooth-zh-cn` | 本仓库构建 |
| OpenClash | `luci-app-openclash` | [官方 dev APK](https://github.com/vernesong/OpenClash/tree/package/dev) |
| 西瓜皮 V3 状态屏 | `luci-app-xgp-v3-screen` | 自动同步 |
| [FM350-GL 模组管理](https://github.com/LianXia233/luci-app-fm350/releases) | `luci-app-fm350` | 自动同步 |

## 使用软件源

适用于使用 APK 包管理器且依赖可用的 ImmortalWrt 和 OpenWrt。通过 [软件源首页](https://aethersailor.github.io/openwrt-package-feed/) 查看架构、版本和签名公钥。设备的包架构可用 `apk --print-arch` 查询。

1. 下载软件源的签名公钥，并与仓库内的 [公钥](keys/aethersailor-feed.pem) 核对。将公钥安装到设备的 `/etc/apk/keys/aethersailor-feed.pem`。
2. 将对应架构的 `packages.adb` URL 加入 `/etc/apk/repositories.d/customfeeds.list`。
3. 运行 `apk update`，再用 `apk add` 安装所需的软件包。

Vantage 的主题与仪表盘可分别安装。仅使用主题时，安装 `luci-theme-vantage`，简体中文包为 `luci-i18n-vantage-theme-zh-cn`。Vantage 的 APK 由上游面向 OpenWrt 25.12 构建，实际安装由设备的软件包管理器检查依赖。

`luci-app-xgp-v3-screen` 面向西瓜皮 V3，通过 `spidev` 驱动板载 GC9307。软件包位于 `aarch64_generic` 索引中，对应源码快照可从软件源首页下载。

`luci-app-fm350` 的 `aarch64_cortex-a53` 包同步自上游。`aarch64_generic` 包使用 ImmortalWrt 主线 SNAPSHOT 的 `rockchip/armv8` SDK 构建。构建记录提供对应的上游源码提交、SDK 校验值和 APK 校验值。

使用私有 ASU 时，服务端可放行 `https://aethersailor.github.io/openwrt-package-feed/immortalwrt/apk/packages/`。路由器上的 `owut` 需要启用额外软件源传递。保留配置升级前，可用 `sysupgrade -l` 确认软件源配置与公钥都在备份列表中。

## 发布

[Publish APK feed](.github/workflows/publish-feed.yml) 每 8 小时检查 [releases.json](releases.json) 中配置的发布来源。工作流核对 APK 的 SHA-256 和包元数据。对于 GitHub Release，工作流使用 GitHub 记录的 SHA-256，并核对配置的上游校验清单。OpenClash 从上游同一个提交读取 dev 版本、校验清单和 APK。校验失败时继续提供上一次成功发布的软件包。

可手动运行工作流立即检查，或启用 `force` 重新发布。GitHub 的定时任务可能延后，且公开仓库连续 60 天无活动时可能被停用。当前线上版本见[发布状态](https://aethersailor.github.io/openwrt-package-feed/release-state.json)。

[FM350 构建工作流](.github/workflows/build-fm350.yml) 每日检查上游稳定版。首次发布或上游 APK 的 SHA-256 变化时，工作流下载最新的 SNAPSHOT SDK 并编译。构建通过后，软件源从本仓库的 Release 同步产物。

[蓝牙构建工作流](.github/workflows/build-bluetooth.yml) 每日检查插件和配套依赖的上游提交。源码有更新时，使用当时的 ImmortalWrt 主线 SNAPSHOT SDK 为 `aarch64_generic`、`aarch64_cortex-a53` 和 `x86_64` 构建插件、简体中文包及 `expect`、`bluez-alsa`、`bluez-alsa-utils`。官方软件源提供 `bluez-daemon` 等其余依赖。

同一目录中的 `packages.adb`、`index.json` 和 APK 由同次发布生成。各软件包的许可证与对应源码可通过软件源首页查阅。

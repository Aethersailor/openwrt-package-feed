# Aethersailor OpenWrt Package Feed

Aethersailor 自行维护的公开软件源。软件源同步下列项目发布的 APK，保留原包内容和版本号。

| 软件 | APK 包名 | 发布来源 |
| --- | --- | --- |
| [SubConverter-Extended](https://github.com/Aethersailor/SubConverter-Extended/releases) | `subconverter-extended` | 自动同步 |
| [Rule-Bot Client](https://github.com/Aethersailor/Rule-Bot-Client/releases) | `luci-app-rule-bot-client` | 自动同步 |
| [Vantage](https://github.com/anothaDev/luci-theme-vantage/releases) | `luci-theme-vantage`、`luci-app-vantage`、`luci-i18n-vantage-theme-zh-cn`、`luci-i18n-vantage-zh-cn` | 自动同步 |
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

`luci-app-fm350` 目前位于 `aarch64_cortex-a53` 索引中。上游使用 `v1.2.0` 标签对应的 Release 上传了 `1.2.0-r6`，该包的构建源码见 [上游提交](https://github.com/LianXia233/luci-app-fm350/tree/56389482f094411c784b9baaacc4aeb0658a6b2c)。

使用私有 ASU 时，服务端可放行 `https://aethersailor.github.io/openwrt-package-feed/immortalwrt/apk/packages/`。路由器上的 `owut` 需要启用额外软件源传递。保留配置升级前，可用 `sysupgrade -l` 确认软件源配置与公钥都在备份列表中。

## 发布

[Publish APK feed](.github/workflows/publish-feed.yml) 每 15 分钟检查 [releases.json](releases.json) 中配置的发布来源。工作流核对 APK 清单、SHA-256 和包元数据。OpenClash 从上游同一个提交读取 dev 版本、校验清单和 APK。校验失败时继续提供上一次成功发布的软件包。

可手动运行工作流立即检查，或启用 `force` 重新发布。GitHub 的定时任务可能延后，且公开仓库连续 60 天无活动时可能被停用。当前线上版本见[发布状态](https://aethersailor.github.io/openwrt-package-feed/release-state.json)。

同一目录中的 `packages.adb`、`index.json` 和 APK 由同次发布生成。各软件包的许可证与对应源码可通过软件源首页查阅。

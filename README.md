# Aethersailor OpenWrt Package Feed

Aethersailor 自行维护的公开软件源。软件源收录两个项目正式 Release 中的 APK，保留原包内容和版本号。

| 软件 | APK 包名 | 当前版本 |
| --- | --- | --- |
| [SubConverter-Extended](https://github.com/Aethersailor/SubConverter-Extended/releases/tag/v1.11.4) | `subconverter-extended` | `1.11.4-r0` |
| [Rule-Bot Client](https://github.com/Aethersailor/Rule-Bot-Client/releases/tag/v0.3.13) | `luci-app-rule-bot-client` | `0.3.13-r1` |

## 使用软件源

适用于使用 APK 包管理器且依赖可用的 ImmortalWrt 和 OpenWrt。通过 [软件源首页](https://aethersailor.github.io/openwrt-package-feed/) 查看架构、版本和签名公钥。设备的包架构可用 `apk --print-arch` 查询。

1. 下载软件源的签名公钥，并与仓库内的 [公钥](keys/aethersailor-feed.pem) 核对。将公钥安装到设备的 `/etc/apk/keys/aethersailor-feed.pem`。
2. 将对应架构的 `packages.adb` URL 加入 `/etc/apk/repositories.d/customfeeds.list`。
3. 运行 `apk update`，再用 `apk add` 安装所需的软件包。

使用私有 ASU 时，服务端可放行 `https://aethersailor.github.io/openwrt-package-feed/immortalwrt/apk/packages/`。路由器上的 `owut` 需要启用额外软件源传递。保留配置升级前，可用 `sysupgrade -l` 确认软件源配置与公钥都在备份列表中。

## 发布

更新 [releases.json](releases.json) 中的正式 Release 后，运行 [Publish APK feed](.github/workflows/publish-feed.yml)。流程核对 GitHub Release 的 SHA-256 和 APK 元数据，从同一批包生成签名索引和 `owut` 版本索引，再发布到 GitHub Pages。

同一目录中的 `packages.adb`、`index.json` 和 APK 由同次发布生成。各软件包的许可证与对应源码见上表中的 Release。

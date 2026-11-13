# Aethersailor OpenWrt Package Feed

Aethersailor 自行维护的公开软件源。软件源收录两个项目正式 Release 中的 APK，保留原包内容和版本号。

| 软件 | APK 包名 | 正式 Release |
| --- | --- | --- |
| [SubConverter-Extended](https://github.com/Aethersailor/SubConverter-Extended/releases) | `subconverter-extended` | 自动同步 |
| [Rule-Bot Client](https://github.com/Aethersailor/Rule-Bot-Client/releases) | `luci-app-rule-bot-client` | 自动同步 |

## 使用软件源

适用于使用 APK 包管理器且依赖可用的 ImmortalWrt 和 OpenWrt。通过 [软件源首页](https://aethersailor.github.io/openwrt-package-feed/) 查看架构、版本和签名公钥。设备的包架构可用 `apk --print-arch` 查询。

1. 下载软件源的签名公钥，并与仓库内的 [公钥](keys/aethersailor-feed.pem) 核对。将公钥安装到设备的 `/etc/apk/keys/aethersailor-feed.pem`。
2. 将对应架构的 `packages.adb` URL 加入 `/etc/apk/repositories.d/customfeeds.list`。
3. 运行 `apk update`，再用 `apk add` 安装所需的软件包。

使用私有 ASU 时，服务端可放行 `https://aethersailor.github.io/openwrt-package-feed/immortalwrt/apk/packages/`。路由器上的 `owut` 需要启用额外软件源传递。保留配置升级前，可用 `sysupgrade -l` 确认软件源配置与公钥都在备份列表中。

## 发布

[Publish APK feed](.github/workflows/publish-feed.yml) 每 15 分钟检查 [releases.json](releases.json) 中的项目。工作流核对各项目最新正式 Release 的 APK 清单、SHA-256 和包元数据，确认附件齐全后发布。版本或文件摘要发生变化才会重新构建 Pages。校验失败时继续提供上一次成功发布的软件包。

可手动运行工作流立即检查，或启用 `force` 重新发布。GitHub 的定时任务可能延后，且公开仓库连续 60 天无活动时可能被停用。当前线上版本见[发布状态](https://aethersailor.github.io/openwrt-package-feed/release-state.json)。

同一目录中的 `packages.adb`、`index.json` 和 APK 由同次发布生成。各软件包的许可证与对应源码见上表中的 Release。

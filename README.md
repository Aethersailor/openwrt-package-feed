# Aethersailor OpenWrt Package Feed

为使用 APK 的 OpenWrt 和 ImmortalWrt 提供官方软件源尚未收录或更新滞后的软件包。本源由 Aethersailor 维护，可与官方软件源同时启用，也可配合 `owut` 和允许使用本源的 Attended Sysupgrade（ASU）服务，在升级固件时保留插件。

安装新软件包或升级指定软件包时，如果多个已启用的软件源提供同名包，`apk` 默认优先选择可满足依赖和版本约束的较高版本。添加软件源后，可用 `apk policy 包名` 查看各来源的候选版本。

[软件源首页](https://aethersailor.github.io/openwrt-package-feed/) · [当前发布状态](https://aethersailor.github.io/openwrt-package-feed/release-state.json) · [签名公钥](keys/aethersailor-feed.pem)

## 通过 SSH 添加软件源

首次添加时，用 `root` 登录路由器后执行。以后刷新索引只需运行 `apk update`。

```sh
base='https://aethersailor.github.io/openwrt-package-feed'
wget -O /etc/apk/keys/aethersailor-feed.pem "$base/keys/aethersailor-feed.pem" &&
  echo "$base/immortalwrt/apk/packages/$(apk --print-arch)/packages.adb" >> /etc/apk/repositories.d/customfeeds.list &&
  apk update
```

以 Aurora 主题为例，查看可用版本和来源，然后安装。更新已安装的软件包时，可单独指定包名，例如 `apk upgrade luci-theme-aurora`。

```sh
apk policy luci-theme-aurora
apk add luci-theme-aurora
```

## 升级固件时保留插件

Attended Sysupgrade 会把目标版本、设备型号和要保留的软件包清单提交给 ASU 服务。服务端使用目标固件对应的 ImageBuilder，从可用软件源解析依赖，并把软件包安装到新的升级镜像中。刷写时，`sysupgrade` 将备份列表中的配置恢复到新系统，软件包和配置分别通过镜像和备份保留。[ASU 构建实现](https://github.com/openwrt/asu/blob/de08b1dd64e4ed9ebc8413cd67e9459892708213/asu/build.py)

### 安装升级工具

`owut` 提供 SSH 命令，`luci-app-attendedsysupgrade` 提供 LuCI 中的「系统 → 值守式系统升级」页面。安装两种客户端及简体中文包。

```sh
apk update
apk add owut luci-app-attendedsysupgrade luci-i18n-attendedsysupgrade-zh-cn
```

### 让 ASU 服务使用本源

路由器上的 `apk update` 成功后，ASU 服务仍需允许 ImageBuilder 访问本源。可通过 ASU 的 API 查看当前的 `server.repository_allow_list`。[OpenWrt ASU](https://sysupgrade.openwrt.org/api/v1/overview) · [ImmortalWrt ASU](https://sysupgrade.immortalwrt.org/api/v1/overview)

要在固件升级时保留本源中的插件，请部署自建 ASU，并在服务端 `asu.toml` 的 `repository_allow_list` 中加入下列 URL 前缀。保留白名单中的其他条目。保存后重启 ASU 服务和构建进程，使新配置生效。[ASU 部署说明](https://github.com/openwrt/asu) · [白名单实现](https://github.com/openwrt/asu/blob/de08b1dd64e4ed9ebc8413cd67e9459892708213/asu/repositories.py)

```toml
repository_allow_list = ["https://aethersailor.github.io/openwrt-package-feed/immortalwrt/apk/packages/"]
```

查看路由器当前使用的 ASU 地址。

```sh
uci get attendedsysupgrade.server.url
```

如需切换到已放行本源的 ASU，将示例地址换成实际服务地址后执行。

```sh
uci set attendedsysupgrade.server.url='https://asu.example.org'
uci commit attendedsysupgrade
```

### 在 owut 中启用额外软件源

设置 `repositories_mode=append` 后，`owut` 会把额外 APK 软件源和签名公钥提交给 ASU，构建时同时使用服务端默认源。检查请求中是否包含本源的 `packages.adb` URL、公钥和需要保留的插件。[owut 软件源处理](https://github.com/efahl/owut/blob/55ed17f8199719ad4c4f3ff6881b5648fae1f612/files/owut#L809-L927) · [请求字段](https://github.com/efahl/owut/blob/55ed17f8199719ad4c4f3ff6881b5648fae1f612/files/owut#L2053-L2084)

```sh
uci -q get attendedsysupgrade.owut >/dev/null ||
  uci set attendedsysupgrade.owut='owut'
uci set attendedsysupgrade.owut.repositories_mode='append'
uci commit attendedsysupgrade

owut check
owut blob
```

把本源配置和公钥加入固件升级备份列表，并确认下面的检查输出包含两个路径。插件在其他路径存放的数据也应按需备份。

```sh
for path in /etc/apk/repositories.d/customfeeds.list /etc/apk/keys/aethersailor-feed.pem; do
  grep -Fqx "$path" /etc/sysupgrade.conf 2>/dev/null ||
    printf '\n%s\n' "$path" >> /etc/sysupgrade.conf
done

sysupgrade -l | grep -E '^/etc/apk/(repositories\.d/customfeeds\.list|keys/aethersailor-feed\.pem)$'
```

确认目标版本正确、检查结果没有需要处理的缺包或降级后，请求构建并下载镜像。

```sh
owut download
```

下载和校验成功后，再执行安装。此命令会刷写固件并重启路由器。

```sh
owut install
```

### LuCI 会使用 repositories_mode 吗？

LuCI Attended Sysupgrade 和 `owut` 共用 `attendedsysupgrade.server.url`。OpenWrt 和 ImmortalWrt 当前的 LuCI 页面自行向 ASU 提交请求，从 `owut` 配置中只读取根文件系统大小。启用 `repositories_mode=append` 后，请通过 `owut` 请求构建，它会提交额外软件源和公钥。[OpenWrt LuCI 源码](https://github.com/openwrt/luci/blob/85c7f60405d8f6dab5b2805151d9b334731ba0a3/applications/luci-app-attendedsysupgrade/htdocs/luci-static/resources/view/attendedsysupgrade/overview.js#L717-L746) · [ImmortalWrt LuCI 源码](https://github.com/immortalwrt/luci/blob/ad75cde88e694b511100576bfbe0a280e5efe6ef/applications/luci-app-attendedsysupgrade/htdocs/luci-static/resources/view/attendedsysupgrade/overview.js#L717-L746)

## 收录软件包

各架构可用的软件包和版本见[软件源首页](https://aethersailor.github.io/openwrt-package-feed/)。点开软件名称可查看用途、包名和上游仓库。

<details>
<summary>SubConverter-Extended · <code>subconverter-extended</code></summary>

面向多个代理客户端的订阅转换后端，支持 Mihomo Proxy Provider 和客户端原生远程资源。同步上游稳定版。[上游仓库](https://github.com/Aethersailor/SubConverter-Extended)

</details>

<details>
<summary>Rule-Bot Client · <code>luci-app-rule-bot-client</code></summary>

收集 Mihomo 中命中兜底规则 `MATCH` 的域名，默认保存在本地，可选发送到 Rule-Bot。同步上游稳定版。[上游仓库](https://github.com/Aethersailor/Rule-Bot-Client)

</details>

<details>
<summary>OpenClash · <code>luci-app-openclash</code></summary>

在 LuCI 中管理 Mihomo 代理及规则。同步项目发布的 dev APK。[上游仓库](https://github.com/vernesong/OpenClash) · [dev 包](https://github.com/vernesong/OpenClash/tree/package/dev)

</details>

<details>
<summary>Aurora · <code>luci-theme-aurora</code></summary>

支持深色和浅色模式的 LuCI 主题。使用上游发布的通用 APK。[上游仓库](https://github.com/eamonxg/luci-theme-aurora)

</details>

<details>
<summary>Argon · <code>luci-theme-argon</code></summary>

支持深色和浅色模式、登录背景和外观设置的 LuCI 主题。同步上游的通用 APK 和简体中文包 `luci-i18n-argon-config-zh-cn`。已安装旧版独立配置插件时，请按上游说明先单独安装与目标主题同次发布的 `luci-app-argon-config` 兼容包，再升级主题。[上游安装说明](https://github.com/jerrykuku/luci-theme-argon/blob/master/README_ZH.md#安装)

</details>

<details>
<summary>Vantage · <code>luci-theme-vantage</code>、<code>luci-app-vantage</code></summary>

LuCI 主题与仪表盘可分别安装。主题和仪表盘的简体中文包分别为 `luci-i18n-vantage-theme-zh-cn` 和 `luci-i18n-vantage-zh-cn`。上游 APK 面向 OpenWrt 25.12 构建。[上游仓库](https://github.com/anothaDev/luci-theme-vantage)

</details>

<details>
<summary>FM350-GL 模组管理 · <code>luci-app-fm350</code></summary>

在 LuCI 中管理 Fibocom FM350-GL 5G 模组。`aarch64_cortex-a53` 使用上游 APK，`aarch64_generic` 由本仓库使用 ImmortalWrt 主线 SNAPSHOT SDK 构建。[上游仓库](https://github.com/LianXia233/luci-app-fm350) · [构建工作流](.github/workflows/build-fm350.yml)

</details>

<details>
<summary>LuCI 蓝牙管理 · <code>luci-app-bluetooth</code></summary>

在 LuCI 中管理蓝牙适配器、设备配对和音频。本仓库为 `aarch64_generic`、`aarch64_cortex-a53` 和 `x86_64` 构建插件、简体中文包 `luci-i18n-bluetooth-zh-cn` 及依赖 `expect`、`bluez-alsa`、`bluez-alsa-utils`。蓝牙和音频内核模块来自设备固件对应的官方源。[上游仓库](https://github.com/sbwml/luci-app-bluetooth) · [构建工作流](.github/workflows/build-bluetooth.yml)

</details>

<details>
<summary>西瓜皮 V3 状态屏 · <code>luci-app-xgp-v3-screen</code></summary>

通过 `spidev` 驱动西瓜皮 V3 的板载 GC9307 屏幕，提供 LuCI 配置页。软件包位于 `aarch64_generic` 索引，公开源码快照可从软件源首页下载。[维护仓库（需访问权限）](https://github.com/Aethersailor/xgp-v3-screen) · [公开源码快照](https://aethersailor.github.io/openwrt-package-feed/)

</details>

<details>
<summary>Argon ONE V3 风扇控制 · <code>luci-app-argononev3-fancontrol</code></summary>

在 LuCI 中管理 Raspberry Pi 5 的 Argon ONE V3 外壳风扇。同步上游的 `aarch64_cortex-a76` APK，仅适用于配有该外壳的设备。安装前请按上游说明启用 I²C，并安装 `i2c-tools`。[上游仓库](https://github.com/ciwga/luci-app-argononev3-fancontrol)

</details>

<details>
<summary>GPoint GNSS · <code>luci-app-gpoint</code></summary>

在 LuCI 中查看 GNSS 定位信息。支持 u-blox VK-172 接收器、Quectel EC25 / RM520N 和 Sierra Wireless EM7455 等设备。同步上游发布的通用 APK，完整型号清单见[上游仓库](https://github.com/Kodo-kakaku/luci-app-gpoint)。

</details>

<details>
<summary>Hiveton H5000M 风扇控制 · <code>luci-app-h5000m-fancontrol</code></summary>

在 LuCI 中查看温度并设置风扇曲线。同步上游 OpenWrt 25.12 APK 和简体中文包，适用于提供 `pwm-fan` 控制节点的 H5000M。固件保留设备树中的主动散热映射时，内核仍会参与调速。[上游仓库](https://github.com/FAN789/luci-app-h5000m-fancontrol)

</details>

<details>
<summary>AirPi AP3000M 风扇控制 · <code>luci-app-airpi-fancontrol</code></summary>

在 LuCI 中设置 AP3000M 的风扇曲线。同步上游 OpenWrt 25.12 应用 APK。搭载 8 GB eMMC 的型号使用硬件 PWM，搭载 16 GB eMMC 的型号需要与固件内核匹配的 `kmod-airpi-gpio-fan`，请按上游说明确认驱动。[上游仓库](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)

</details>

<details>
<summary>鼎桥 MT5700M 模组管理 · <code>luci-app-mt5700m</code></summary>

在 LuCI 中管理 MT5700M 5G 模组的拨号、频段和短信。同步上游 OpenWrt 25.12 发布归档中的应用、简体中文包和 `mt5700m-transport`。三个包位于 `aarch64_cortex-a53` 索引。[上游仓库](https://github.com/FAN789/luci-app-mt5700m)

</details>

<details>
<summary>MosDNS · <code>mosdns</code>、<code>luci-app-mosdns</code></summary>

DNS 转发服务及其 LuCI 管理界面。同步上游 OpenWrt 25.12 正式发布的本体、LuCI、简体中文包 `luci-i18n-mosdns-zh-cn` 和依赖 `geo2txt`。`v2ray-geoip` 和 `v2ray-geosite` 由固件的官方源提供。[上游仓库](https://github.com/sbwml/luci-app-mosdns)

</details>

<details>
<summary>Bandix · <code>luci-app-bandix</code></summary>

按设备或接口查看实时流量、连接和历史统计。同步核心服务 `bandix`、LuCI 界面和简体中文包。[核心服务](https://github.com/timsaya/openwrt-bandix) · [LuCI 界面](https://github.com/timsaya/luci-app-bandix)

</details>

<details>
<summary>Traffic Control · <code>luci-app-trafficctl</code></summary>

按设备查看流量，设置限速、流量整形和联网规则。同步 LuCI 界面、服务 `trafficctl` 和简体中文包。[上游仓库](https://github.com/YusDyr/luci-app-trafficctl)

</details>

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 添加时提示 404，或索引中找不到软件包 | 用 `apk --print-arch` 确认架构，在软件源首页查看该架构的版本索引。 |
| `apk update` 提示索引签名不可信 | 检查 `/etc/apk/keys/aethersailor-feed.pem` 是否存在，再核对公钥摘要。 |
| 安装时提示依赖缺失 | 检查官方软件源是否可用。内核模块需与当前固件的内核 ABI 一致。 |
| `owut` 提示 `Repository not allowed` | 用 `owut blob` 查看请求中的源地址，确认 ASU 白名单允许该地址。 |
| `owut` 报告不预期的缺包或降级 | 查看 `owut blob` 中的额外源和目标版本。部分版本的 `owut` 会读取 `/etc/apk/repositories.d/` 中的备份文件。确认属于备份的文件后，可移出该目录再检查。 |

公钥校验可用 `sha256sum /etc/apk/keys/aethersailor-feed.pem`。预期 SHA-256 为 `7f3c86c80ac9b37a41874c8739df14bae73a004b6725b64c3b257719e31cd184`。

停用本源时，从 `/etc/apk/repositories.d/customfeeds.list` 删除本源的 URL，再运行 `apk update`。

## 同步与发布

[软件源发布工作流](.github/workflows/publish-feed.yml) 每 8 小时检查一次上游，也可手动运行。工作流核对 APK 的 SHA-256、包名、版本和架构，并生成签名后的 `packages.adb` 及供 `owut` 读取的 `index.json`。校验失败时，线上继续提供上一次成功发布的内容。GitHub 的定时任务可能延后，公开仓库连续 60 天无活动时也可能暂停定时运行。

上游 APK 同步时保留原包内容和版本号。FM350 的 `aarch64_generic` 包和蓝牙包组每天检查上游，分别在 APK 校验值和源码提交变化时重新构建。构建记录附有源码和 SDK 信息。许可证随各上游项目发布，对应源码和构建记录可从[软件源首页](https://aethersailor.github.io/openwrt-package-feed/)查阅。

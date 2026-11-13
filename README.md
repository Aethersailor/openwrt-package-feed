# Aethersailor OpenWrt Package Feed

Aethersailor 自行维护的公开软件源，用于发布自有软件的 OpenWrt 和 ImmortalWrt 软件包。当前处于筹备阶段，软件包清单待定。

## 发布规划

- 首批面向 ImmortalWrt 的 APK 软件包。每个软件的发行分支和架构以实际构建及测试结果为准。
- GitHub Pages 提供签名的 `packages.adb`、供 `owut` 查询版本的 `index.json`、`.apk` 和签名公钥。
- 普通软件包按发行版、发行分支和架构存放。内核模块如需发布，按目标、子目标和完整内核 ABI 单独存放。

计划中的站点目录如下。占位符将在首批软件包发布时替换为实际值。

```text
keys/
immortalwrt/<series>/packages/<arch>/
immortalwrt/<series>/kmods/<target>/<subtarget>/<kernel-abi>/
```

## 首次发布

1. 确认软件清单、上游版本和目标平台，使用对应发行分支的 SDK 或构建环境打包。
2. 生成并签名 APK 索引，从同一批产物生成 `index.json`，将索引和软件包一起发布。
3. 验证 APK 能从该源安装。通过允许此源的 ASU 执行 `owut download`，检查生成的镜像清单是否包含已安装的私有源软件包。

相关文档：[GitHub Pages 发布方式](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) · [owut 的额外软件源处理](https://github.com/efahl/owut/blob/65d67aad/files/owut#L868-L990) · [ImmortalWrt ASU 的源地址匹配规则](https://github.com/immortalwrt/asu/blob/main/asu/repositories.py)

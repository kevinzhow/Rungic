# Android VPN 已联网但容器无法解析域名

## 问题与范围

2026-10-04，在 Motorola G100（portov_cn，Android 16，W1VT36H.1-51-8）上检查到：Android 和 Clash Meta 正常联网，但 Rungic 应用无法访问域名，Codex 安装入口请求报 `curl: (6) Could not resolve host: chatgpt.com`。

本记录提供实机诊断和临时恢复方法，不包含自动同步实现。最新源码核对基线为 `d37788a76e1f6b9a8c86c2f3f52a91af8dad8ff0`；该基线没有部署到本次测试设备，不能将源码检查等同于最新版本实机验收。

## 已验证原因

Clash 全局模式下，容器桌面用户 UID 1000 查询公网目的地址的路由，结果为 `tun0`，源地址为 `172.19.0.1`。不设置 HTTP 代理，直接访问 `https://1.1.1.1` 返回 HTTP 301；直接向当前 VPN DNS `172.19.0.2:53` 查询域名也成功。

然而，容器 `/etc/resolv.conf` 只有 `# Set from Android network on first boot` 注释，没有 nameserver。Linux 应用无法通过默认解析器解析域名，因此表现为整体断网。

以 Clash DNS 的解析结果执行单次 `curl --noproxy '*' --resolve` 后，百度、GitHub Codex 发布接口及 releases.openai.com 渠道接口均返回 HTTP 200，chatgpt.com 安装入口返回 HTTP 302。此时 VPN 已接管流量，显式 HTTP 代理不是必要修复。

## 源码检查

- [镜像构建](../../tools/ci/build_rootfs_image.py)生成占位 resolv.conf。
- [首次安装](../../tools/ci/rungic-firstboot.sh)仅在 PHONE_HTTP_PROXY 非空时写入静态代理配置，没有在该路径补齐 DNS。
- [Android 网络桥](../../android/app/src/com/rungic/plasma/AndroidNetworkBridge.java)读取 DNS 并放入网络快照。
- [桌面网络桥](../../shared/platform/network-manager.py)将 DNS 映射为 NetworkManager D-Bus 属性；未发现将其应用到 Linux 解析器的实现。

## 临时恢复

先读取 Android 当前默认网络的 DNS；本次 VPN 的地址是 `172.19.0.2`，它不是所有设备和网络都适用的固定地址。确认当前仍为该地址后，在容器 root shell 中执行：

```sh
set -eu
backup=$(mktemp /etc/resolv.conf.before-manual-dns.XXXXXX)
cp -p /etc/resolv.conf "$backup"
temporary=$(mktemp /etc/resolv.conf.manual.XXXXXX)
printf '%s\n' 'nameserver 172.19.0.2' > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" /etc/resolv.conf
printf '原配置备份：%s\n' "$backup"
```

本方法针对本次实际存在的普通文件；若 resolv.conf 是由其他解析服务管理的符号链接，应通过对应服务配置。恢复时将上述备份复制回原路径；本次原文件没有 DNS，恢复它也会恢复解析故障。

写入后，以桌面普通用户测试 getent 和 curl，不指定代理或解析覆盖：域名解析成功，百度及 GitHub 发布接口返回 HTTP 200，Codex 安装入口返回 HTTP 302。没有执行完整 Codex 安装，也没有验证关闭 VPN 后继续联网。

## 后续修复与验收

共享网络层应在启动时读取 Android 默认网络的 DNS，在默认网络或 LinkProperties 变化时重新同步到容器解析器。应验证地址、原子更新、处理 IPv6 和现有解析服务，并保留用户手动配置的明确覆盖方式。断网时不能注入未经验证的固定公共 DNS；VPN 关闭后不能保留仅在 VPN 内可达的地址。

需分别验收 Wi-Fi、移动网络、VPN 开启、关闭与重连，检查普通用户域名解析和实际 HTTPS 请求；最后验证完整 Codex 下载、校验、安装与版本输出。不要把网络状态显示正常或安装入口返回重定向当作完整安装成功。

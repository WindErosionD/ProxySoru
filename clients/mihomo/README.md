# Mihomo（Clash Meta）内核说明

测速程序**仅**通过本目录或系统 `PATH` 中的 **Mihomo / Clash Meta** 可执行文件承载所有协议；不再内置 Shadowsocks、V2Ray、Trojan 等独立客户端。

## 放置路径（按查找顺序）

程序在 `PROJECT_ROOT`（仓库根）下依次查找：

| 平台 | 候选路径 |
|------|-----------|
| Windows | `clients/mihomo/mihomo.exe` → `clients/clash-meta/mihomo.exe` → `clients/clash/mihomo.exe` |
| Linux / macOS | `clients/mihomo/mihomo` → `clients/clash-meta/mihomo` → `clients/clash/mihomo` |

以上均不存在时，再在系统 `PATH` 中查找：`mihomo`、`clash-meta`、`clash`（Windows 会尝试带 `.exe` 的名称）。

## 版本与自检

将官方发布的 **Mihomo** 或 **Clash Meta** 对应平台的可执行文件放入 `clients/mihomo/` 即可（本仓库便携目录默认名为 `mihomo.exe` / `mihomo`）。

在终端执行（以 Windows 为例）：

```text
clients\mihomo\mihomo.exe -v
```

或：

```text
clients\mihomo\mihomo.exe version
```

应能看到内核版本与 Meta 标识。程序启动节点测速前也会尝试对选中的二进制打一次版本日志（失败则仅记录路径）。

**建议**：使用与订阅节点特性匹配的较新 **Meta** 内核（需支持 SSR / vmess / trojan / vless 等你实际在用的协议）。

## 运行时配置

程序会将当前节点写成 `项目根目录/config_mihomo.yaml`，并以：

```text
mihomo -f config_mihomo.yaml
```

方式启动；本地 SOCKS 端口来自 `ssrspeed_config.json` 中的 `localPort`。

## 转换失败

若某条节点无法从旧格式转为 Mihomo 的 `proxies` 单条配置，日志中会输出**脱敏后的节点摘要**与具体异常；请根据报错检查协议字段或升级内核。常见不支持项示例：`v2ray-plugin` 的 SS。

# Ninja（Mihomo SuperCore）内核说明

Ninja 是基于 Mihomo 深度魔改的内核，**额外支持 `type: ninja` 协议**。公版 MetaCubeX Mihomo **无法**测速 Ninja 节点。

## 仓库内放置（Windows）

| 文件 | 说明 |
|------|------|
| `ninja.exe` | 测速 Ninja 节点时优先使用的内核 |
| `VERSION` | 对应上游 release 标签 |

下载地址示例：[kachetong1314/mihomo-ninja Releases](https://github.com/kachetong1314/mihomo-ninja/releases)  
Windows amd64 资产名一般为：`ninja-windows-amd64.exe`（下载后重命名为 `ninja.exe`）。

Linux / macOS：下载对应 `ninja-linux-*` / `ninja-darwin-*`，放到本目录并命名为 `ninja`，`chmod +x`。

## 查找顺序

出现 `type: ninja` 节点时，程序优先：

1. `clients/ninja/ninja(.exe)`
2. `clients/mihomo-ninja/`
3. 系统 PATH 中的 `ninja`

普通协议仍可使用 `clients/mihomo/mihomo(.exe)`。

## 订阅说明

- Ninja 订阅常为 Clash YAML，节点 `type: ninja`，必填字段包括：`server`、`port`、`password`、`method`、`node-password`（或 `node_password`）。
- 拉取时使用官方 UA：`clash-ninja/2.5.2 pass 1.0`（与 [Clash Verge Ninja](https://github.com/kachetong1314/mihomo-ninja) 一致）。错误 UA 常直接 `Access denied`。
- 部分订阅带 `#!PASS-INFO` / `#!PASS2-INFO` / `#!PASS3-INFO` 混淆注释；程序会保留并写入运行时配置，由 Ninja 内核自动还原。
- 远程拉失败时常见三种响应：
  - **Access denied**：UA/令牌不对
  - **Cloudflare 520**：官方 UA 已到源站，但源站生成 Ninja 订阅失败（机场侧问题）
  - **Just a moment / 安全验证**：CF 人机验证，换代理出口或改用官方客户端
- 上述情况请在 **Clash Verge Ninja** 里导入订阅并导出 YAML，再本地测速：

```bat
python main.py -u "D:\path\to\ninja-sub.yaml"
```

## 自检

```text
clients\ninja\ninja.exe -v
```

应看到类似：`Mihomo Meta <commit> ...`

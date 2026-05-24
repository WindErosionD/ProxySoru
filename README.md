基于 SSRSpeedN 修改，添加了大量近年新出的协议、更全的流媒体测试，以及拓扑测试。

## 快速开始（Windows）

1. 安装 [Python 3.10+](https://www.python.org/downloads/)，在项目根目录执行：`pip install -r requirements.txt`
2. **Mihomo 内核已内置**于 `clients/mihomo/mihomo.exe`，克隆后即可使用
3. 首次运行会自动从 `ssrspeed_config.example.json` 生成 `ssrspeed_config.json`
4. 测速（需自备订阅或本地节点配置）：

```bat
python main.py -u "你的订阅链接"
```

或使用本地 Clash 配置：`python main.py -c 你的配置.yaml`

也可双击 `一键测速.bat`（需在命令行传入 `-u` / `-c` 等参数，或自行修改启动脚本）。

## 配置说明

| 文件 | 是否提交仓库 | 说明 |
|------|----------------|------|
| `ssrspeed_config.example.json` | 是 | 示例配置，首次运行会复制为 `ssrspeed_config.json` |
| `ssrspeed_config.json` | 否（本地） | 端口、测速项等，勿提交 |
| `config_mihomo.yaml.example` | 是 | 运行时配置格式参考 |
| `config_mihomo.yaml` | 否（运行时生成） | 含节点敏感信息 |

## Mihomo 内核

详见 [clients/mihomo/README.md](clients/mihomo/README.md)。

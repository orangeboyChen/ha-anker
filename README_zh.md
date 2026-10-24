# Anker 蓝牙充电宝 Home Assistant 集成

此自定义集成通过本地蓝牙读取 Anker A110A Prime 充电宝电量，提供电量传感器、连接状态、手动连接/断开按钮，并保留尚未解码的遥测 TLV 原始字段供后续核对。配置流也已经预留 Wi-Fi 充电器的云账号/设备选择路径。

## 安装

1. 在 HACS 的自定义仓库中添加 `https://github.com/orangeboyChen/ha-anker`，类别选择 Integration。
2. 安装 **Anker BLE Power Bank** 并重启 Home Assistant。
3. 在“设置 → 设备与服务”添加集成。附近的 FF09 设备会自动发现，也可以手动输入蓝牙地址。云端路径设计为输入 Anker 邮箱/密码、复用缓存账号、自动刷新会话，然后从一个账号下选择多台设备。
4. 让充电宝处于 HA 主机或蓝牙代理的覆盖范围内。

集成使用 Home Assistant 本地蓝牙，不需要 Anker 云账号、手机 App 或 HAR 会话凭据。

## 当前支持范围

- A110A 蓝牙：GATT 发现、FF09 GCM 会话、电量快照和实时通知。
- 手动连接控制：连接按钮立即恢复更新；断开按钮关闭 GATT 并暂停自动重连。
- `raw_fields` 保留遥测 TLV，但不猜测未知字段的含义和比例。
- `mqtt_protocol.py` 已包含确认过的 Wi-Fi/MQTT 外层 envelope 编解码；实时 Wi-Fi 实体尚未启用，因为当前没有真实充电器的 broker 凭据和型号专用遥测/控制 payload。
- `cloud_auth.py` 和 `config_flow.py` 已实现云账号缓存与多设备选择骨架，不保存密码；`async_refresh_expiring_accounts` 已准备好供 HA 周期 coordinator 调用。认证失败会删除失效账号；网络/协议暂不可用会保留缓存。
- 默认云端 transport 会在 APK 的 `/passport/login` 加密登录和刷新交换尚未独立验证前明确提示不可用，不会发送猜测请求。
- 充电模式选择暂未开放。APK 中虽然存在模式页面和 native bridge，但目前没有证据证明其具体命令及编码适用于 A110A；不会把其他型号的命令照搬过来。

功率、电流、电压、温度、电池健康度和循环次数的 A110A TLV ID/换算规则尚未验证，所以暂不创建独立实体；相关原始字段可在电量传感器属性中查看。

## 开发

```sh
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest
```

协议测试完全离线，不会连接实体设备或发送控制命令。

## 许可证

MIT，见 [LICENSE](LICENSE)。本项目与 Anker Innovations 无隶属关系。

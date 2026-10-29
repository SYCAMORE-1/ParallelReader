# iPhone 个人异地访问

本方式让自己的 iPhone 通过 Tailscale 私网访问电脑上的阅读器。书架、原书、译文、模型配置和阅读进度沿用同一份本机数据。

## 准备

需要使用远程访问时，自行在服务器电脑安装 Tailscale，在 iPhone 的 App Store 安装官方 Tailscale，并登录同一个个人账号。iPhone 首次使用时按系统提示允许 VPN 配置，并打开连接。

阅读器只读取已经安装的客户端状态，不安装软件，不代为登录、连接 VPN 或修改防火墙。未安装或未连接时，远程启动器会提示原因并停止；普通 `start.bat` 仍可独立使用。

- [Windows 安装说明](https://tailscale.com/docs/install/windows)
- [iPhone 安装说明](https://tailscale.com/docs/install/ios)
- [Tailscale CLI 状态查询](https://tailscale.com/docs/reference/tailscale-cli)

## 启动和使用

1. 电脑和 iPhone 上的 Tailscale 都保持已登录、已连接状态。
2. 如果原阅读器仍在运行，先在它的启动窗口按 Ctrl+C，等待正常退出。无需复制或迁移书库。
3. 双击 `start-tailscale.bat`。电脑浏览器仍打开本机地址，启动窗口额外显示 `iPhone / Tailscale` 访问网址。
4. iPhone 使用手机流量或其他 Wi-Fi，打开 Tailscale 连接，然后在 Safari 输入启动窗口给出的完整网址。网址格式为 `http://电脑的Tailscale-IPv4地址:端口/`，实际地址由程序检测，不需要手填配置。
5. 电脑保持开机、联网，并保留阅读器启动窗口。关机、睡眠或停止阅读器期间无法访问。

更换端口或不自动打开电脑浏览器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Tailscale -Port 8766 -NoBrowser
```

也可直接运行：

```powershell
runtime\python.exe -X utf8 server.py --tailscale --port 8765
```

同 Wi-Fi 预览仍用 `start-mobile.bat`，普通本机阅读用 `start.bat`；两者和远程启动器使用同一个书库。运行中的阅读器占用相同端口时，新远程启动器会先报错，不会启动第二套书库任务。

## 访问范围

启动器读取本机 `tailscale status --json`，确认已连接，并仅采用 `Self.TailscaleIPs` 中唯一的 Tailscale IPv4 地址。其他设备的地址、旧的顶层地址或普通网卡地址不能作为启动地址。

服务允许本机回环地址及检测到的 Tailscale 地址，并保留 Host、Origin 和请求类型检查。通过 Tailscale 地址进入的请求还必须来自 Tailscale IPv4 范围；普通局域网接口没有启用。页面使用 IP 地址访问，暂不提供 MagicDNS 名称、Serve 或 Funnel 的代理入口。

HTTP 连接承载于 Tailscale 的 WireGuard 加密网络中。模型由电脑上的服务调用，手机不需要单独访问模型端口。[Tailscale 加密说明](https://tailscale.com/docs/concepts/tailscale-encryption)。

这是个人可信设备入口：能通过你的 Tailscale 访问规则连接此电脑阅读器端口的设备，可以使用完整书架和设置。请将它用于自己的个人账号和设备。

## 常见问题

- **未找到 Tailscale**：在需要提供服务的电脑上自行安装客户端。程序支持 PATH 中的安装及标准 Program Files 安装位置。
- **需要登录／授权**：在 Tailscale 客户端完成登录和设备授权，再重新运行启动器。
- **未连接或离线**：检查电脑网络并打开 Tailscale 连接。启动器不会自动执行 `tailscale up`。
- **没有 IPv4 地址**：确认客户端启用了 Tailscale IPv4。当前启动方式使用 IPv4 私网网址。
- **端口占用**：正常关闭此前启动的阅读器，或明确选择其他空闲端口。
- **手机打不开**：确认手机 Tailscale 已连接、输入的是电脑地址、端口与窗口一致；检查电脑防火墙是否允许阅读器端口在 Tailscale 网络上接收入站连接。保留防火墙，仅按需要放行该服务。
- **电脑更换 Wi-Fi**：Tailscale 地址通常保持不变；若重新注册设备或地址改变，重新运行启动器读取当前地址。[地址说明](https://tailscale.com/docs/concepts/tailscale-ip-addresses)。

未安装 Tailscale 时，可以验证程序逻辑、模拟状态与访问限制；真实 iPhone 外网连接需要在安装并登录后验证。

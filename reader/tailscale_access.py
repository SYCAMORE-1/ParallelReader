"""Read an existing Tailscale connection; never install or configure it."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess


TAILSCALE_IPV4 = ipaddress.IPv4Network((0x64400000, 10))


def is_tailscale_ipv4(value):
    try:
        return ipaddress.IPv4Address(value) in TAILSCALE_IPV4
    except (ValueError, TypeError):
        return False


def executable():
    command = shutil.which('tailscale.exe' if os.name == 'nt' else 'tailscale')
    if command:
        return command
    for name in ('ProgramW6432', 'ProgramFiles'):
        home = os.environ.get(name)
        if home:
            candidate = Path(home) / 'Tailscale' / 'tailscale.exe'
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError('未找到 Tailscale。请在需要远程使用的电脑上自行安装并登录，然后重新运行 start-tailscale.bat。程序不会自动安装。')


def address():
    """Only Running state and addresses owned by this device are accepted."""
    command = executable()
    try:
        result = subprocess.run([command, 'status', '--json'], capture_output=True,
                                encoding='utf-8', errors='replace', timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('读取 Tailscale 状态超时，请打开 Tailscale，确认已连接后重试。') from exc
    except OSError as exc:
        raise RuntimeError('无法读取 Tailscale 状态，请确认客户端和后台服务已启动。') from exc
    if result.returncode:
        raise RuntimeError('Tailscale 尚未就绪。请打开客户端，确认已启动并登录。')
    try:
        status = json.loads(result.stdout)
        if not isinstance(status, dict):
            raise ValueError('Invalid status')
    except (ValueError, TypeError) as exc:
        raise RuntimeError('无法识别 Tailscale 状态，请更新客户端后重试。') from exc
    state = status.get('BackendState')
    if state in {'NeedsLogin', 'NeedsMachineAuth'}:
        raise RuntimeError('Tailscale 需要登录或设备授权，请在客户端完成后重试。')
    if state != 'Running':
        raise RuntimeError('Tailscale 尚未连接，请在客户端打开连接后重试。')
    own = status.get('Self')
    if not isinstance(own, dict):
        raise RuntimeError('Tailscale 尚未返回本机信息，请等待连接完成后重试。')
    if own.get('Online') is False:
        raise RuntimeError('Tailscale 当前离线，请检查电脑网络并重新连接。')
    values = own.get('TailscaleIPs')
    if not isinstance(values, list):
        raise RuntimeError('Tailscale 未返回本机 IPv4 地址，请检查客户端连接。')
    ips = list(dict.fromkeys(str(ipaddress.IPv4Address(value)) for value in values if isinstance(value, str) and is_tailscale_ipv4(value)))
    if len(ips) != 1:
        raise RuntimeError('没有唯一可用的 Tailscale IPv4 地址，请在客户端启用 IPv4 并重新连接。')
    return ips[0]


def request_allowed(local_address, peer_address, tailscale_address):
    # Normal local/LAN mode retains its existing policy. In Tailscale mode,
    # remote requests must reach the detected VPN address from another VPN IP.
    if tailscale_address is None:
        return True
    return (local_address == '127.0.0.1' and peer_address == '127.0.0.1'
            or local_address == tailscale_address and is_tailscale_ipv4(peer_address))


def check_port(port):
    """Fail before opening a library or launching workers on an occupied port."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise RuntimeError('无效的阅读器端口。')
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(('0.0.0.0', port))
    except OSError as exc:
        raise RuntimeError(f'无法使用端口 {port}。请先正常关闭原阅读器窗口，再运行 start-tailscale.bat；也可使用 start.ps1 -Tailscale -Port 指定其他端口。') from exc

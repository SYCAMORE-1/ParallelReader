import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from reader import tailscale_access as access
from server import make_server
from test_reader import epub_fixture

ROOT = Path(__file__).resolve().parents[1]
VPN = str(ipaddress.IPv4Address(0x64400009))
PHONE = str(ipaddress.IPv4Address(0x6440000A))
LAN = str(ipaddress.IPv4Address(0xC0000204))


class TailscaleConnectionTests(unittest.TestCase):
    def status(self, value, returncode=0):
        result = subprocess.CompletedProcess(['tailscale'], returncode,
                    stdout=json.dumps(value), stderr='private-status-sentinel')
        with patch.object(access, 'executable', return_value='existing-client'):
            with patch.object(access.subprocess, 'run', return_value=result) as run:
                value = access.address()
        self.assertEqual(run.call_args.args[0], ['existing-client', 'status', '--json'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)
        return value

    def test_connected_client_uses_only_its_own_ipv4_and_read_only_command(self):
        self.assertEqual(self.status({'BackendState': 'Running',
            'Self': {'Online': True, 'TailscaleIPs': ['fd7a:115c:a1e0::1', VPN, VPN]},
            'Peer': {'another-device': {'TailscaleIPs': [PHONE]}}}), VPN)

    def test_missing_client_exits_without_installing_or_running_commands(self):
        with patch.object(access.shutil, 'which', return_value=None), patch.dict(access.os.environ, {}, clear=True):
            with patch.object(access.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, '未找到 Tailscale.*不会自动安装'):
                    access.address()
                run.assert_not_called()

    def test_standard_windows_install_is_found_without_path(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'Tailscale' / 'tailscale.exe'
            with patch.object(access.shutil, 'which', return_value=None), patch.dict(access.os.environ, {'ProgramFiles': temp}, clear=True):
                with patch.object(Path, 'is_file', lambda path: path == target):
                    self.assertEqual(access.executable(), str(target))

    def test_logged_out_stopped_and_offline_states_do_not_enable_access(self):
        for state in ('NeedsLogin', 'NeedsMachineAuth', 'Stopped', 'Starting', None):
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                self.status({'BackendState': state, 'Self': {'TailscaleIPs': [VPN]}})
        with self.assertRaisesRegex(RuntimeError, '离线'):
            self.status({'BackendState': 'Running', 'Self': {'Online': False, 'TailscaleIPs': [VPN]}})

    def test_peer_top_level_and_non_vpn_addresses_cannot_be_used(self):
        for own in (None, {}, {'TailscaleIPs': [LAN]}, {'TailscaleIPs': [VPN, PHONE]}, {'TailscaleIPs': ['fd7a:115c:a1e0::1']}):
            with self.subTest(own=own), self.assertRaises(RuntimeError):
                self.status({'BackendState': 'Running', 'TailscaleIPs': [VPN], 'Self': own,
                             'Peer': {'another-device': {'TailscaleIPs': [PHONE]}}})

    def test_client_failures_are_bounded_and_do_not_echo_private_diagnostics(self):
        with patch.object(access, 'executable', return_value='existing-client'):
            for failure in (subprocess.TimeoutExpired(['tailscale'], 10), OSError('private-status-sentinel')):
                with patch.object(access.subprocess, 'run', side_effect=failure):
                    with self.assertRaises(RuntimeError) as error: access.address()
                    self.assertNotIn('private-status-sentinel', str(error.exception))
            for result in (subprocess.CompletedProcess([], 1, '', 'private-status-sentinel'),
                           subprocess.CompletedProcess([], 0, 'invalid-json', ''),
                           subprocess.CompletedProcess([], 0, '[]', '')):
                with patch.object(access.subprocess, 'run', return_value=result):
                    with self.assertRaises(RuntimeError) as error: access.address()
                    self.assertNotIn('private-status-sentinel', str(error.exception))

    def test_vpn_listener_accepts_vpn_source_and_rejects_other_interfaces(self):
        self.assertTrue(access.request_allowed(VPN, PHONE, VPN))
        self.assertTrue(access.request_allowed('127.0.0.1', '127.0.0.1', VPN))
        for local, peer in ((LAN, PHONE), (VPN, LAN), ('127.0.0.1', PHONE), (VPN, '127.0.0.1'), (PHONE, VPN)):
            with self.subTest(local=local, peer=peer):
                self.assertFalse(access.request_allowed(local, peer, VPN))
        self.assertTrue(access.request_allowed(LAN, LAN, None))

    def test_occupied_port_is_rejected_before_any_library_work(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as current:
            current.bind(('127.0.0.1', 0)); current.listen(1)
            with self.assertRaisesRegex(RuntimeError, '正常关闭原阅读器'):
                access.check_port(current.getsockname()[1])
        access.check_port(0)
        with self.assertRaises(RuntimeError): access.check_port(-1)

    def test_unsafe_mode_combinations_fail_before_opening_store(self):
        for options in ({'host': '0.0.0.0', 'tailscale_address': LAN},
                        {'host': '127.0.0.1', 'tailscale_address': VPN},
                        {'host': '0.0.0.0', 'tailscale_address': VPN, 'allowed_hosts': [LAN]}):
            with self.subTest(options=options), patch('server.Store') as store:
                with self.assertRaises(ValueError): make_server(**options)
                store.assert_not_called()


class TailscaleHttpTests(unittest.TestCase):
    def test_private_mode_keeps_shared_library_and_local_origin_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_server(host='0.0.0.0', port=0, tailscale_address=VPN, data_dir=Path(temp) / '.data')
            thread = threading.Thread(target=app.serve_forever, daemon=True); thread.start()
            base = 'http://127.0.0.1:' + str(app.server_port)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path, value=None, headers=None, epub=False):
                data = value if epub else json.dumps(value).encode() if value is not None else None
                defaults = {'Content-Type': 'application/epub+zip' if epub else 'application/json'} if data is not None else {}
                return opener.open(urllib.request.Request(base + path, data=data, headers={**defaults, **(headers or {})}))
            try:
                with request('/api/import', epub_fixture(), epub=True) as response: book = json.load(response)
                with request('/api/books/' + book['id'] + '/bookmark', {'page': 0, 'manual': True}, {'Origin': base}) as response:
                    self.assertTrue(json.load(response)['manual'])
                with request('/api/books') as response:
                    self.assertEqual(json.load(response)[0]['id'], book['id'])
                for headers in ({'Host': LAN + ':' + str(app.server_port)}, {'Host': 'attacker.test'}, {'Origin': 'https://attacker.test'}):
                    with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                        request('/api/health', headers=headers)
                    self.assertEqual(error.exception.code, 403)
                with patch.object(access, 'request_allowed', return_value=False):
                    with self.assertRaises(urllib.error.HTTPError) as error: request('/api/health')
                    self.assertEqual(error.exception.code, 403)
            finally:
                app.translator.close(); app.shutdown(); app.server_close()
                for worker in app.translator.threads: worker.join(2)
                app.store.db.close()


@unittest.skipUnless(os.name == 'nt', 'Windows launcher integration')
class TailscaleLauncherTests(unittest.TestCase):
    def launch(self, *args):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy2(ROOT / 'start.ps1', root / 'start.ps1')
            (root / 'server.py').write_text('import json,sys; print(json.dumps(sys.argv[1:]))', encoding='utf-8')
            env = os.environ.copy()
            env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
            return subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'),
                                   '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root / 'start.ps1'), *args],
                                  capture_output=True, encoding='utf-8', errors='replace', env=env, timeout=20,
                                  creationflags=subprocess.CREATE_NO_WINDOW)

    def test_launcher_passes_remote_flag_port_and_browser_choice(self):
        result = self.launch('-Tailscale', '-Port', '8766', '-NoBrowser')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout.strip())
        self.assertIn('--tailscale', args); self.assertNotIn('--open', args)
        self.assertEqual(args[args.index('--port') + 1], '8766')
        self.assertNotIn('--host', args); self.assertNotIn('--allow-host', args)

    def test_launcher_rejects_remote_and_lan_combination(self):
        result = self.launch('-Tailscale', '-Mobile', '-NoBrowser')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Choose either', result.stderr)

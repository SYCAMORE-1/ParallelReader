"""Build an isolated Windows x64 folder from the verified local Python runtime.

Build dependencies: Pillow; fontTools + brotli only if reader-sans.ttf is absent.
No packages need installing on the destination PC.
"""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SKIP = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.DS_Store')


def log(message):
    print(message, flush=True)


def copy_runtime(destination):
    home = Path(sys.executable).resolve().parent
    tag = f'python{sys.version_info.major}{sys.version_info.minor}'
    destination.mkdir()
    for name in ['python.exe', 'pythonw.exe', 'python3.dll', tag + '.dll', 'vcruntime140.dll', 'vcruntime140_1.dll']:
        path = home / name
        if not path.is_file():
            raise RuntimeError('Required portable runtime file missing: ' + str(path))
        shutil.copy2(path, destination / name)
    shutil.copytree(home / 'DLLs', destination / 'DLLs', ignore=SKIP)
    exclusions = {'site-packages', 'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', '__pycache__'}
    with zipfile.ZipFile(destination / (tag + '.zip'), 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted((home / 'Lib').rglob('*.py')):
            relative = path.relative_to(home / 'Lib')
            if not any(part in exclusions for part in relative.parts):
                archive.write(path, relative.as_posix())
    packages = destination / 'Lib' / 'site-packages'
    packages.mkdir(parents=True)
    pillow = importlib.metadata.distribution('Pillow')
    for name in sorted({str(path).replace('\\', '/').split('/')[0] for path in pillow.files}):
        source = Path(pillow.locate_file(name))
        if source.is_dir():
            shutil.copytree(source, packages / name, ignore=SKIP)
    (destination / (tag + '._pth')).write_text(
        f'{tag}.zip\nDLLs\nLib/site-packages\n..\nimport site\n', encoding='ascii')
    shutil.copy2(home / 'LICENSE.txt', destination / 'LICENSE-Python.txt')
    (destination / 'versions.txt').write_text(f'Python {platform.python_version()} Windows AMD64\nPillow {pillow.version}\n', encoding='utf-8')


def build(destination, include_data=False, make_zip=False):
    if include_data:
        raise ValueError('公开发布包不能包含私人书库。请单独复制 .data 或使用 tools/export_library.py。')
    from tools.audit_public import audit_public
    audit_public(ROOT)
    if sys.platform != 'win32' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise RuntimeError('Build this package with a 64-bit Windows Python installation')
    if destination.exists():
        raise FileExistsError('Refusing to overwrite an existing release: ' + str(destination))
    destination = destination.resolve()
    destination.mkdir(parents=True)
    log('Copying application sources and resources...')
    for name in ['reader', 'public', 'tools', 'tests', 'docs']:
        shutil.copytree(ROOT / name, destination / name, ignore=SKIP)
    for name in ['server.py', 'start.bat', 'start-mobile.bat', 'start.ps1', 'README.md', 'requirements.txt', '.gitignore', '.gitattributes']:
        shutil.copy2(ROOT / name, destination / name)
    font_path = destination / 'public' / 'fonts' / 'reader-sans.ttf'
    if not font_path.is_file():
        sys.path.insert(0, str(ROOT / 'test-results' / 'font-build'))
        from fontTools.ttLib import TTFont
        font = TTFont(str(destination / 'public' / 'fonts' / 'reader-sans.woff2'), recalcTimestamp=False)
        font.flavor = None; font.save(font_path); font.close()
    log('Bundling isolated Python and Pillow...')
    copy_runtime(destination / 'runtime')
    statistics = {}
    (destination / 'check-portable.bat').write_text(
        '@echo off\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python.exe" -X utf8 "%~dp0tools\\verify_release.py"\r\npause\r\n', encoding='ascii')
    instructions = '''# Windows 便携版使用说明

适用于 Windows 10 / 11 64 位电脑，无需安装 Python 或 pip。

1. 复制整个文件夹，或先完整解压 ZIP。不要在压缩包里直接运行。
2. 放在当前用户有写入权限的位置，如“文档”文件夹。双击 start.bat，浏览器会自动打开。
3. 默认地址 http://127.0.0.1:8765/ 。启动窗口需保持打开；在窗口按 Ctrl+C 可停止服务。
4. 手机预览双击 start-mobile.bat，启动窗口会显示新电脑的局域网地址。手机与电脑使用同一 Wi-Fi，按 Windows 提示允许局域网访问。
5. 更换端口：powershell -NoProfile -ExecutionPolicy Bypass -File .\\start.ps1 -Port 8766
6. 若有启动问题，双击 check-portable.bat 检查程序、字体与书库是否完整。

## 书库与翻译

公开程序包不携带图书、译文、书签或模型接口配置；首次启动为空书架。
已有私人书库时，将单独保存的 .data 文件夹放到程序根目录，再启动即可读取书籍、已保存译文、阅读进度和书签。
.data 属于私人数据，不应加入代码仓库或公开发布包。复制前先关闭阅读器；也可用 tools/export_library.py 生成一致性快照。
按书迁移时，正常关闭阅读器后，只复制 .data/books/ 中选定的“书名 -- 短编号”文件夹到新程序的同名 books/ 目录，启动时自动恢复。
每书文件夹包含 original.epub、book.sqlite3 迁移快照、译图和导出译本。保留 book.json 和文件夹全部内容；单书快照不携带模型密钥。
旧版平铺目录会在启动时自动整理；tools/organize_library.py --apply 可先备份数据库再整理。运行中定期同步单书快照，正常退出时完成最后同步。
模型地址、模型名称和可选 API Key 由使用者在设置中填写，仅保存在本地 .data 中。默认没有任何模型接口地址。
已保存译文可离线阅读；新增翻译、查词和校对需配置兼容的模型服务，程序包不包含模型权重。
图书翻译在 LLM 与 Azure Translate 之间选择；Azure Translate 只翻译文字，图片保留原图。
单词／语境翻译独立选择 LLM 或 Azure Translate，悬浮框显示提供方；查词 LLM 需单独填写接口和模型。
译文分别缓存。切换配置后可继续使用本地已有译文，缺失部分由当前服务补齐；“本地译文”按钮可切换阅读版本。
关闭阅读器后，tools/clean_library.py 可预览清理计划；添加 --apply --prune-history 可删除过期导出、未引用译图和旧校对快照并压缩数据库，原书和当前译文保留。
字体、主题和阅读模式属于浏览器偏好，新电脑可重新调整；书签和阅读页数已随数据库迁移。

## 源码与许可

server.py、reader/、public/ 为完整应用源码，tools/ 为构建与检查工具，tests/ 为测试源码。
runtime/ 内含独立 Python、标准库和 Pillow；Python 许可位于 runtime/LICENSE-Python.txt，Pillow 及其依赖许可位于 runtime/Lib/site-packages/pillow-*.dist-info/LICENSE。
中文字体许可、来源在 public/fonts/ 的 OFL 文本和 NOTICE.json；译图排版自带 reader-sans.ttf，不依赖目标电脑安装中文字体。
本发布文件夹只包含程序和许可允许分发的资源。私人书库与程序分别迁移。
'''
    (destination / '使用说明.md').write_text(instructions, encoding='utf-8-sig')
    metadata = {'builtAt': datetime.now(timezone.utc).isoformat(), 'platform': 'Windows x64',
                'python': platform.python_version(), 'pillow': importlib.metadata.version('Pillow'),
                'includesLibrary': False, 'library': statistics,
                'apiKeysRemoved': True, 'unfinishedJobsPaused': True}
    (destination / 'release-info.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    log('Writing resource checksums...')
    files = {}
    for path in sorted(destination.rglob('*')):
        relative = path.relative_to(destination)
        if path.is_file() and relative.parts[0] != '.data' and '__pycache__' not in relative.parts:
            files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / 'release-manifest.json').write_text(json.dumps({'algorithm': 'sha256', 'files': files}, ensure_ascii=False, indent=2), encoding='utf-8')
    log('Verifying with the bundled Python, isolated from the host installation...')
    subprocess.run([str(destination / 'runtime' / 'python.exe'), '-X', 'utf8', str(destination / 'tools' / 'verify_release.py')],
                   cwd=destination, check=True)
    if make_zip:
        log('Creating ZIP archive...')
        compressed = {'.epub', '.png', '.jpg', '.jpeg', '.webp', '.woff2', '.zip', '.pyd', '.dll'}
        with zipfile.ZipFile(destination.with_suffix('.zip'), 'x', allowZip64=True) as archive:
            for path in sorted(destination.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.tmp'}:
                    if path.name.endswith(('-wal', '-shm')): continue
                    kind = zipfile.ZIP_STORED if path.suffix.lower() in compressed else zipfile.ZIP_DEFLATED
                    archive.write(path, (Path(destination.name) / path.relative_to(destination)).as_posix(), compress_type=kind, compresslevel=4)
    size = sum(path.stat().st_size for path in destination.rglob('*') if path.is_file())
    log(json.dumps({'folder': str(destination), 'bytes': size, 'library': statistics,
                    'zip': str(destination.with_suffix('.zip')) if make_zip else None}, ensure_ascii=False))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ('ParallelReader-Windows-x64-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--include-data', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()
    build(args.output, args.include_data, args.zip)

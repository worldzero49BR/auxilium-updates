#!/usr/bin/env python3
"""AUXILIUM — instalador local com interface GTK/WebKit.

O instalador é deliberadamente separado do backend do AUXILIUM: ele só copia
arquivos, cria atalhos e prepara `data/settings.json`. A interface é uma página
local servida em 127.0.0.1 e exibida dentro de uma janela WebKit.

Esta pasta (`instalador/`) é a área independente do instalador. O aplicativo
fica na pasta pai; o instalador nunca é copiado para a instalação final.

Uso:
    ./instalador/INSTALAR
    python3 instalador/installer.py --no-window --port 8791
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

INSTALADOR_DIR = Path(__file__).resolve().parent
ROOT = INSTALADOR_DIR.parent
INSTALLER_DIR = INSTALADOR_DIR / 'ui'
VERSION_FILE = ROOT / 'VERSION'
ICON_FILE = ROOT / 'ui' / 'assets' / 'icon.svg'
DEFAULT_DEST = Path.home() / '.local' / 'share' / 'auxilium'
TERMS_VERSION = '1.0'
MODEL_COPY_LIMIT = 4 * 1024 ** 3
APP_ITEMS = [
    'AUXILIUM',
    'launcher.py',
    'core',
    'ui',
    'bin',
    'docs',
    'VERSION',
    'CHANGELOG.md',
    'README.md',
]
DEFAULT_SYSTEM = (
    'Você é AUXILIUM, um agente de programação local. Seja direto, preciso e prático. '
    'Responda em português, com código completo quando necessário e sem introduções longas.'
)


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------
def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding='utf-8').strip() or '0.0.0'
    except Exception:
        return '0.0.0'


def human_size(value: int | float) -> str:
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    v = float(value or 0)
    i = 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f'{v:.0f} {units[i]}' if i == 0 else f'{v:.1f} {units[i]}'


def total_ram_gb() -> float:
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            if line.startswith('MemTotal:'):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except Exception:
        pass
    return 0.0


def module_state(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def gtk_state() -> bool:
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False


def webkit_state() -> bool:
    try:
        import gi
        gi.require_version('WebKit2', '4.1')
        from gi.repository import WebKit2  # noqa: F401
        return True
    except Exception:
        return False


def port_busy(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.18)
            return sock.connect_ex(('127.0.0.1', port)) == 0
    except Exception:
        return False


def desktop_path() -> Path:
    try:
        r = subprocess.run(
            ['xdg-user-dir', 'DESKTOP'],
            capture_output=True, text=True, timeout=3,
        )
        value = (r.stdout or '').strip()
        if value:
            return Path(value).expanduser()
    except Exception:
        pass
    return Path.home() / 'Desktop'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def write_text(path: Path, text: str) -> None:
    """Escreve texto sem seguir um symlink existente."""
    if path.is_symlink():
        path.unlink()
    path.write_text(text, encoding='utf-8')


def copy_file(source: Path, target: Path) -> None:
    """Copia um arquivo preservando metadados, sem escrever através de symlink."""
    if target.is_symlink():
        target.unlink()
    shutil.copy2(source, target)


def default_settings() -> dict[str, Any]:
    return {
        'apiPort': 8787,
        'llamaPort': 8788,
        'llamaServer': '',
        'model': '',
        'gpuLayers': 0,
        'ctxSize': 4096,
        'threads': 0,
        'parallel': 1,
        'temperature': 0.15,
        'maxTokens': 2048,
        'systemPrompt': DEFAULT_SYSTEM,
        'autoStartModel': True,
        'appearance': {
            'theme': 'dark',
            'accent': '#4f7fae',
            'fontScale': 1.0,
            'reduceMotion': False,
            'glow': True,
        },
        'legal': {
            'termsVersion': TERMS_VERSION,
            'accepted': False,
            'acceptedAt': None,
        },
    }


def desktop_entry(destination: Path) -> str:
    exec_path = destination / 'AUXILIUM'
    return (
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Version=1.0\n'
        'Name=AUXILIUM\n'
        'GenericName=Agente de Programação Local\n'
        'Comment=Central de Controle do AUXILIUM\n'
        f'Exec="{exec_path}"\n'
        f'Path="{destination}"\n'
        'Icon=auxilium\n'
        'Terminal=false\n'
        'Categories=Development;Utility;IDE;\n'
        'StartupNotify=true\n'
        'StartupWMClass=Auxilium\n'
    )


def uninstall_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
DEST="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || printf '%s' "$HOME/Desktop")"
printf 'Removendo AUXILIUM de %s...\\n' "$DEST"
rm -f "$HOME/.local/share/applications/auxilium.desktop" 2>/dev/null || true
rm -f "$DESKTOP_DIR/AUXILIUM.desktop" 2>/dev/null || true
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/auxilium.svg" 2>/dev/null || true
rm -rf "$DEST"
printf 'AUXILIUM removido.\\n'
"""


# --------------------------------------------------------------------------
# catálogo / ambiente
# --------------------------------------------------------------------------
def source_models() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted((ROOT / 'models').glob('*.gguf')):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        copyable = size <= MODEL_COPY_LIMIT
        out.append({
            'filename': path.name,
            'name': path.name.replace('.gguf', ''),
            'size': size,
            'sizeLabel': human_size(size),
            'copyable': copyable,
            'recommended': '1.5b' in path.name.lower(),
            'reason': '' if copyable else 'Acima de 4 GB; não é copiado pelo instalador.',
        })
    return out


def environment() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    py_ok = sys.version_info >= (3, 8)
    checks.append({
        'id': 'python', 'label': 'Python',
        'state': 'ok' if py_ok else 'fail',
        'detail': platform.python_version(),
        'blocking': not py_ok,
    })
    for name, label in (('fastapi', 'FastAPI'), ('uvicorn', 'Uvicorn'), ('httpx', 'HTTPX')):
        ok = module_state(name)
        checks.append({
            'id': name, 'label': label,
            'state': 'ok' if ok else 'fail',
            'detail': 'disponível' if ok else 'não encontrado',
            'blocking': not ok,
        })
    gtk_ok = gtk_state()
    checks.append({
        'id': 'gtk', 'label': 'GTK3',
        'state': 'ok' if gtk_ok else 'warn',
        'detail': 'janela nativa disponível' if gtk_ok else 'a interface abrirá no navegador',
        'blocking': False,
    })
    webkit_ok = webkit_state()
    checks.append({
        'id': 'webkit', 'label': 'WebKit2',
        'state': 'ok' if webkit_ok else 'warn',
        'detail': 'janela integrada disponível' if webkit_ok else 'fallback para navegador',
        'blocking': False,
    })
    llama = ROOT / 'bin' / 'llama' / 'llama-server'
    llama_ok = llama.is_file() and os.access(llama, os.X_OK)
    checks.append({
        'id': 'llama', 'label': 'llama-server',
        'state': 'ok' if llama_ok else 'warn',
        'detail': 'incluído no pacote' if llama_ok else 'será necessário instalar ou informar o caminho',
        'blocking': False,
    })
    free = 0
    try:
        free = shutil.disk_usage(ROOT).free
    except OSError:
        pass
    disk_ok = free >= 512 * 1024 * 1024
    checks.append({
        'id': 'disk', 'label': 'Espaço em disco',
        'state': 'ok' if disk_ok else 'fail',
        'detail': f'{human_size(free)} livres',
        'blocking': not disk_ok,
    })
    busy = [p for p in (8787, 8788) if port_busy(p)]
    checks.append({
        'id': 'ports', 'label': 'Portas locais',
        'state': 'ok' if not busy else 'warn',
        'detail': '8787 e 8788 livres' if not busy else 'ocupadas: ' + ', '.join(map(str, busy)),
        'blocking': False,
    })
    if getattr(os, 'geteuid', lambda: 1)() == 0:
        checks.append({
            'id': 'root', 'label': 'Permissão',
            'state': 'fail',
            'detail': 'execute sem sudo; a instalação é feita no seu usuário',
            'blocking': True,
        })
    blocking = any(c['blocking'] and c['state'] == 'fail' for c in checks)
    return {
        'version': read_version(),
        'source': str(ROOT),
        'home': str(Path.home()),
        'defaultDestination': str(DEFAULT_DEST),
        'destinationExists': DEFAULT_DEST.exists(),
        'desktopDir': str(desktop_path()),
        'python': platform.python_version(),
        'ramGb': total_ram_gb(),
        'diskFreeGb': round(free / (1024 ** 3), 1),
        'checks': checks,
        'models': source_models(),
        'blocking': blocking,
        'canInstall': not blocking,
    }


# --------------------------------------------------------------------------
# motor de instalação
# --------------------------------------------------------------------------
class Installer:
    def __init__(self, source: Path):
        self.source = source
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.log_file: Path | None = None
        self.state = self._blank_state()

    @staticmethod
    def _steps() -> list[dict[str, str]]:
        return [
            {'id': 'prepare', 'label': 'Preparando destino', 'state': 'pending'},
            {'id': 'copy', 'label': 'Copiando aplicação', 'state': 'pending'},
            {'id': 'models', 'label': 'Copiando modelos', 'state': 'pending'},
            {'id': 'data', 'label': 'Preparando dados', 'state': 'pending'},
            {'id': 'shortcuts', 'label': 'Criando atalhos', 'state': 'pending'},
            {'id': 'validate', 'label': 'Validando instalação', 'state': 'pending'},
        ]

    def _blank_state(self) -> dict[str, Any]:
        return {
            'phase': 'idle',
            'progress': 0,
            'current': 'Aguardando início',
            'log': [],
            'steps': self._steps(),
            'done': False,
            'error': '',
            'destination': '',
            'startedAt': None,
            'finishedAt': None,
            'copiedBytes': 0,
            'totalBytes': 0,
            'opened': False,
        }

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime('%H:%M:%S')
        line = f'{stamp}  {message}'
        with self.lock:
            self.state['log'].append(line)
            if len(self.state['log']) > 400:
                del self.state['log'][:-400]
        if self.log_file:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with self.log_file.open('a', encoding='utf-8') as fh:
                    fh.write(line + '\n')
            except Exception:
                pass

    def _update(self, **fields: Any) -> None:
        with self.lock:
            self.state.update(fields)

    def _step(self, step_id: str, status: str) -> None:
        with self.lock:
            for step in self.state['steps']:
                if step['id'] == step_id:
                    step['state'] = status
                    break

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _app_files(self) -> list[tuple[Path, Path, int, bool]]:
        files: list[tuple[Path, Path, int, bool]] = []
        for item in APP_ITEMS:
            source = self.source / item
            if not source.exists() and not source.is_symlink():
                continue
            if source.is_symlink():
                files.append((source, Path(item), 0, True))
                continue
            if source.is_file():
                try:
                    files.append((source, Path(item), source.stat().st_size, False))
                except OSError:
                    continue
                continue
            for path in source.rglob('*'):
                if '__pycache__' in path.parts or path.suffix == '.pyc':
                    continue
                if path.is_symlink():
                    files.append((path, path.relative_to(self.source), 0, True))
                    continue
                if not path.is_file():
                    continue
                try:
                    files.append((path, path.relative_to(self.source), path.stat().st_size, False))
                except OSError:
                    continue
        return files

    def validate(self, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        raw_dest = str(raw.get('destination') or '').strip()
        if not raw_dest:
            return None, 'Informe uma pasta de destino.'
        try:
            destination = Path(raw_dest).expanduser()
            if not destination.is_absolute():
                destination = Path.home() / destination
            destination = destination.resolve()
        except Exception:
            return None, 'Caminho de destino inválido.'
        source = self.source.resolve()
        if destination == source or source in destination.parents or destination in source.parents:
            return None, 'Escolha uma pasta fora da pasta do instalador.'
        blocked = {Path('/'), Path.home(), Path('/home'), Path('/usr'), Path('/etc'), Path('/opt')}
        if destination in blocked or len(destination.parts) < 3:
            return None, 'Escolha uma pasta específica, como ~/.local/share/auxilium.'
        if destination.exists() and not destination.is_dir():
            return None, 'O destino existe, mas não é uma pasta.'
        if destination.exists() and any(destination.iterdir()):
            looks_like_auxilium = (destination / 'AUXILIUM').exists() or (destination / 'launcher.py').exists()
            if not looks_like_auxilium:
                return None, 'A pasta já existe e não parece ser uma instalação do AUXILIUM. Escolha uma pasta vazia.'
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return None, f'Não foi possível criar a pasta pai: {e}'
        if not os.access(destination.parent, os.W_OK):
            return None, 'Sem permissão de escrita na pasta de destino.'
        if destination.exists() and not os.access(destination, os.W_OK):
            return None, 'Sem permissão de escrita na pasta de destino.'

        models_by_name = {m['filename']: m for m in source_models()}
        copy_models: list[str] = []
        for filename in raw.get('copyModels') or []:
            filename = str(filename)
            if filename not in models_by_name:
                return None, f'Modelo inválido: {filename}'
            model = models_by_name[filename]
            if not model['copyable']:
                return None, f'{filename} é grande demais para o instalador copiar.'
            if filename not in copy_models:
                copy_models.append(filename)
        model_bytes = sum(models_by_name[f]['size'] for f in copy_models)
        free = shutil.disk_usage(destination.parent).free
        if free < model_bytes + 512 * 1024 * 1024:
            return None, 'Espaço insuficiente no destino para copiar os modelos selecionados.'

        options = {
            'destination': str(destination),
            'createMenu': bool(raw.get('createMenu', True)),
            'createDesktop': bool(raw.get('createDesktop', False)),
            'openAfter': bool(raw.get('openAfter', True)),
            'copyModels': copy_models,
        }
        return options, ''

    def start(self, raw: dict[str, Any]) -> dict[str, Any]:
        options, error = self.validate(raw)
        if error:
            return {'ok': False, 'error': error}
        with self.lock:
            if self.thread and self.thread.is_alive():
                return {'ok': False, 'error': 'Já existe uma instalação em andamento.'}
            self.state = self._blank_state()
            self.state['phase'] = 'running'
            self.state['destination'] = options['destination']
            self.state['startedAt'] = now_iso()
            self.log_file = Path(options['destination']) / 'data' / 'logs' / 'installer.log'
            self.thread = threading.Thread(target=self._run, args=(options,), daemon=True)
            self.thread.start()
        return {'ok': True, 'state': self.snapshot()}

    def _copy_stream(self, source: Path, destination: Path, on_chunk=None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + '.part')
        with source.open('rb') as src, partial.open('wb') as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                if on_chunk:
                    on_chunk(len(chunk))
        shutil.copystat(source, partial)
        partial.replace(destination)

    def _run(self, options: dict[str, Any]) -> None:
        destination = Path(options['destination'])
        try:
            self._step('prepare', 'running')
            self._update(current='Preparando destino', progress=2)
            self._log(f'AUXILIUM {read_version()} será instalado em {destination}')
            destination.mkdir(parents=True, exist_ok=True)
            (destination / 'data' / 'logs').mkdir(parents=True, exist_ok=True)
            (destination / 'data' / 'webkit').mkdir(parents=True, exist_ok=True)
            (destination / 'data' / 'webkit-cache').mkdir(parents=True, exist_ok=True)
            (destination / 'models').mkdir(parents=True, exist_ok=True)
            self.log_file = destination / 'data' / 'logs' / 'installer.log'
            self._log('Destino preparado.')
            self._step('prepare', 'done')

            self._step('copy', 'running')
            self._update(current='Copiando aplicação', progress=5)
            files = self._app_files()
            total = sum(size for _src, _rel, size, is_link in files if not is_link)
            copied = 0
            last_band = -1
            for source, rel, size, is_link in files:
                target = destination / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if is_link:
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    target.symlink_to(os.readlink(source))
                    continue
                copy_file(source, target)
                copied += size
                pct = 5 + int((copied / total) * 50) if total else 55
                self._update(progress=pct, copiedBytes=copied, totalBytes=total)
                band = pct // 10
                if band != last_band:
                    last_band = band
                    self._log(f'Aplicação: {pct}% ({human_size(copied)} de {human_size(total)})')
            for exe in ('AUXILIUM', 'INSTALAR', 'bin/llama/llama-server', 'scripts/check.sh'):
                path = destination / exe
                if path.exists():
                    try:
                        path.chmod(path.stat().st_mode | 0o111)
                    except OSError:
                        pass
            self._log(f'Aplicação copiada: {len(files)} arquivos, {human_size(total)}.')
            self._step('copy', 'done')

            selected_models = options.get('copyModels') or []
            if not selected_models:
                self._step('models', 'skipped')
                self._log('Nenhum modelo local selecionado; o primeiro uso oferecerá 1.5B e 3B.')
            else:
                self._step('models', 'running')
                self._update(current='Copiando modelos', progress=55)
                models_by_name = {m['filename']: m for m in source_models()}
                model_total = sum(models_by_name[f]['size'] for f in selected_models)
                model_copied = 0
                for filename in selected_models:
                    meta = models_by_name[filename]
                    source = self.source / 'models' / filename
                    target = destination / 'models' / filename
                    if target.exists() and target.stat().st_size == meta['size']:
                        self._log(f'Modelo já presente, mantido: {filename}')
                        model_copied += meta['size']
                        continue
                    self._log(f'Copiando modelo {filename} ({meta["sizeLabel"]})...')
                    def tick(n: int, base=model_copied) -> None:
                        pct = 55 + int(((base + n) / model_total) * 30) if model_total else 85
                        self._update(progress=pct)
                    self._copy_stream(source, target, tick)
                    model_copied += meta['size']
                    self._log(f'Modelo copiado: {filename}')
                self._update(progress=85)
                self._step('models', 'done')

            self._step('data', 'running')
            self._update(current='Preparando dados', progress=86)
            settings_path = destination / 'data' / 'settings.json'
            if not settings_path.exists():
                settings_path.write_text(
                    json.dumps(default_settings(), indent=2, ensure_ascii=False) + '\n',
                    encoding='utf-8',
                )
                self._log('Configuração inicial criada (acento #4f7fae).')
            else:
                self._log('Configuração existente preservada.')
            info = {
                'version': read_version(),
                'installedAt': now_iso(),
                'source': str(self.source),
                'models': selected_models,
            }
            write_text(
                destination / 'data' / 'install.json',
                json.dumps(info, indent=2, ensure_ascii=False) + '\n',
            )
            uninstall = destination / 'uninstall.sh'
            write_text(uninstall, uninstall_script())
            uninstall.chmod(0o755)
            self._step('data', 'done')

            self._step('shortcuts', 'running')
            self._update(current='Criando atalhos', progress=91)
            desktop = desktop_entry(destination)
            write_text(destination / 'AUXILIUM.desktop', desktop)
            if options.get('createMenu') or options.get('createDesktop'):
                try:
                    icon_dir = Path.home() / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps'
                    icon_dir.mkdir(parents=True, exist_ok=True)
                    copy_file(ICON_FILE, icon_dir / 'auxilium.svg')
                except Exception as e:
                    self._log(f'Ícone do menu não instalado: {e}')
            if options.get('createMenu'):
                menu_dir = Path.home() / '.local' / 'share' / 'applications'
                menu_dir.mkdir(parents=True, exist_ok=True)
                menu_file = menu_dir / 'auxilium.desktop'
                write_text(menu_file, desktop)
                menu_file.chmod(0o755)
                self._log('Atalho criado no menu de aplicativos.')
            if options.get('createDesktop'):
                try:
                    desk_dir = desktop_path()
                    desk_dir.mkdir(parents=True, exist_ok=True)
                    desk_file = desk_dir / 'AUXILIUM.desktop'
                    write_text(desk_file, desktop)
                    desk_file.chmod(0o755)
                    subprocess.run(
                        ['gio', 'set', str(desk_file), 'metadata::trusted', 'true'],
                        capture_output=True, timeout=4, check=False,
                    )
                    self._log(f'Atalho criado na área de trabalho: {desk_file}')
                except Exception as e:
                    self._log(f'Atalho da área de trabalho não criado: {e}')
            self._step('shortcuts', 'done')

            self._step('validate', 'running')
            self._update(current='Validando instalação', progress=96)
            required = [
                'AUXILIUM', 'launcher.py', 'core/main.py', 'ui/index.html',
                'ui/assets/app.js', 'ui/assets/app.css',
            ]
            missing = [rel for rel in required if not (destination / rel).exists()]
            if missing:
                raise RuntimeError('Instalação incompleta: ' + ', '.join(missing))
            if (self.source / 'bin' / 'llama' / 'llama-server').exists() and not (
                destination / 'bin' / 'llama' / 'llama-server'
            ).exists():
                raise RuntimeError('O binário llama-server não foi copiado.')
            for rel in ('core/main.py', 'launcher.py'):
                path = destination / rel
                try:
                    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
                except SyntaxError as e:
                    raise RuntimeError(f'Falha ao validar {rel}: {e.msg} (linha {e.lineno})') from e
            for cache in destination.rglob('__pycache__'):
                shutil.rmtree(cache, ignore_errors=True)
            self._step('validate', 'done')
            self._update(current='Instalação concluída', progress=100, phase='done', done=True, finishedAt=now_iso())
            self._log('Instalação concluída com sucesso.')
            self._log(f'Abra pelo atalho do menu ou execute: {destination / "AUXILIUM"}')
        except Exception as e:
            message = str(e) or e.__class__.__name__
            self._log(f'ERRO: {message}')
            with self.lock:
                for step in self.state['steps']:
                    if step['state'] == 'running':
                        step['state'] = 'error'
            self._update(phase='error', error=message, done=False, current='Instalação interrompida')
            self._log('A instalação não foi concluída.')

    def open_installed(self) -> dict[str, Any]:
        with self.lock:
            done = bool(self.state.get('done'))
            destination = Path(self.state.get('destination') or '')
        if not done:
            return {'ok': False, 'error': 'A instalação ainda não foi concluída.'}
        launcher = destination / 'AUXILIUM'
        if not launcher.is_file():
            return {'ok': False, 'error': 'O executável instalado não foi encontrado.'}
        try:
            subprocess.Popen(
                [str(launcher)],
                cwd=str(destination),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            return {'ok': False, 'error': f'Não foi possível abrir o AUXILIUM: {e}'}
        self._update(opened=True)
        self._log('AUXILIUM iniciado.')
        return {'ok': True}


# --------------------------------------------------------------------------
# servidor local da interface
# --------------------------------------------------------------------------
class InstallerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, installer: Installer, root: Path):
        self.installer = installer
        self.root = root
        self.on_quit = None
        super().__init__(address, InstallerHandler)


class InstallerHandler(BaseHTTPRequestHandler):
    server: InstallerServer

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_json({'ok': False, 'error': 'Arquivo não encontrado.'}, 404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(length) if length else b'{}'
            value = json.loads(raw.decode('utf-8'))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ('/', '/index.html'):
            self._send_file(INSTALLER_DIR / 'index.html', 'text/html; charset=utf-8')
        elif path == '/assets/installer.css':
            self._send_file(INSTALLER_DIR / 'installer.css', 'text/css; charset=utf-8')
        elif path == '/assets/installer.js':
            self._send_file(INSTALLER_DIR / 'installer.js', 'application/javascript; charset=utf-8')
        elif path in ('/icon.svg', '/assets/icon.svg'):
            self._send_file(ICON_FILE, 'image/svg+xml')
        elif path == '/api/status':
            self._send_json({
                'ok': True,
                'environment': environment(),
                'state': self.server.installer.snapshot(),
            })
        elif path == '/api/progress':
            self._send_json({'ok': True, 'state': self.server.installer.snapshot()})
        else:
            self._send_json({'ok': False, 'error': 'Rota não encontrada.'}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        payload = self._read_json()
        if path == '/api/install':
            result = self.server.installer.start(payload)
            self._send_json(result, 202 if result.get('ok') else 400)
        elif path == '/api/open':
            result = self.server.installer.open_installed()
            self._send_json(result, 200 if result.get('ok') else 400)
        elif path == '/api/quit':
            self._send_json({'ok': True})
            callback = self.server.on_quit
            if callable(callback):
                threading.Timer(0.15, callback).start()
        else:
            self._send_json({'ok': False, 'error': 'Rota não encontrada.'}, 404)


# --------------------------------------------------------------------------
# janela GTK/WebKit
# --------------------------------------------------------------------------
def run_window(url: str, server: InstallerServer) -> bool:
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        gi.require_version('WebKit2', '4.1')
        from gi.repository import Gdk, GLib, Gtk, WebKit2
    except Exception as e:
        print(f'GTK/WebKit indisponível: {e}', file=sys.stderr)
        return False

    try:
        data_dir = Path(tempfile.mkdtemp(prefix='auxilium-installer-'))
        manager = WebKit2.WebsiteDataManager(
            base_data_directory=str(data_dir / 'data'),
            base_cache_directory=str(data_dir / 'cache'),
        )
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        webview = WebKit2.WebView.new_with_context(context)
        window = Gtk.Window(title='AUXILIUM — Instalação')
        window.set_default_size(1160, 760)
        window.set_size_request(980, 660)
        window.set_position(Gtk.WindowPosition.CENTER)
        try:
            window.set_icon_from_file(str(ICON_FILE))
        except Exception:
            pass
        rgba = Gdk.RGBA()
        rgba.parse('#08090b')
        webview.set_background_color(rgba)
        webview.load_uri(url)
        window.add(webview)

        def quit_ui() -> None:
            GLib.idle_add(window.destroy)

        server.on_quit = quit_ui

        def on_delete(_window: Any, _event: Any) -> bool:
            snapshot = server.installer.snapshot()
            if snapshot.get('phase') == 'running' and not snapshot.get('done'):
                dialog = Gtk.MessageDialog(
                    transient_for=window,
                    flags=0,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.NONE,
                    text='Instalação em andamento',
                )
                dialog.format_secondary_text(
                    'Se fechar agora, a instalação pode ficar incompleta. '
                    'Deseja realmente sair?'
                )
                dialog.add_button('Continuar instalando', Gtk.ResponseType.CANCEL)
                dialog.add_button('Sair mesmo assim', Gtk.ResponseType.OK)
                response = dialog.run()
                dialog.destroy()
                if response != Gtk.ResponseType.OK:
                    return True
            return False

        def on_destroy(*_args: Any) -> None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            shutil.rmtree(data_dir, ignore_errors=True)
            Gtk.main_quit()

        window.connect('delete-event', on_delete)
        window.connect('destroy', on_destroy)
        window.show_all()
        Gtk.main()
        return True
    except Exception as e:
        print(f'Não foi possível abrir a janela: {e}', file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description='Instalador do AUXILIUM')
    parser.add_argument('--no-window', action='store_true', help='serve a interface e abre no navegador')
    parser.add_argument('--port', type=int, default=0, help='porta local (0 = automática)')
    args = parser.parse_args()

    if getattr(os, 'geteuid', lambda: 1)() == 0:
        print('Execute o instalador sem sudo. A instalação é feita na sua pasta de usuário.', file=sys.stderr)
        return 1

    installer = Installer(ROOT)
    server = InstallerServer(('127.0.0.1', args.port), installer, ROOT)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    url = f'http://127.0.0.1:{port}/'
    print(f'AUXILIUM Instalador: {url}')
    print('Pressione Ctrl+C para encerrar.')

    if args.no_window:
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
        return 0

    if run_window(url, server):
        return 0

    print('Abrindo a interface no navegador...', file=sys.stderr)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

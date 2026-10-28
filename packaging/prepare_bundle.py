"""Copy versioned media tool and notices into the freshly built local preview."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--app', required=True, type=Path)
APP = parser.parse_args().app.resolve(strict=True)
if not APP.is_relative_to(ROOT / 'dist'):
    raise ValueError('Bundle preparation must stay under this project dist directory')
archive = ROOT / '.packaging/vendor/ffmpeg-9.0.2-essentials_build.zip'
(APP / 'tools').mkdir(exist_ok=True)
(APP / 'licenses').mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as source:
    for item in source.infolist():
        name = Path(item.filename).name
        if item.filename.endswith('/bin/ffmpeg.exe'):
            target = APP / 'tools/ffmpeg.exe'
        elif name.lower().startswith(('license', 'readme')) and not item.is_dir():
            target = APP / 'licenses' / ('FFmpeg-' + name)
        else:
            continue
        with source.open(item) as reader, target.open('wb') as writer:
            shutil.copyfileobj(reader, writer)
shutil.copyfile(ROOT / 'packaging/PORTABLE_README.md', APP / '使用说明.txt')
components = {}
for dist in importlib.metadata.distributions():
    name = dist.metadata['Name']
    components[name] = dist.version
    for item in dist.files or ():
        if any(word in str(item).lower() for word in ('license', 'copying')):
            file = Path(dist.locate_file(item))
            if file.is_file():
                folder = APP / 'licenses' / name
                folder.mkdir(exist_ok=True)
                shutil.copyfile(file, folder / (hashlib.sha256(str(item).encode()).hexdigest()[:8] + '-' + file.name))
files = {}
for path in sorted(APP.rglob('*')):
    if path.is_file() and path.name != 'build-manifest.json':
        with path.open('rb') as stream:
            files[str(path.relative_to(APP))] = hashlib.file_digest(stream, 'sha256').hexdigest()
sources = {}
for path in sorted([ROOT / 'pyproject.toml', *ROOT.joinpath('src').rglob('*.py'),
                    *(p for p in (ROOT / 'packaging').iterdir() if p.is_file())]):
    with path.open('rb') as stream:
        sources[str(path.relative_to(ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
manifest = dict(status='local-acceptance-only', components=components,
    source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    source_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
    source_files=sources,
    redistribution_review='FLIR SDK EULA vs redistribution docs unresolved; FFmpeg corresponding-source review pending',
    ffmpeg=dict(version='9.0.2', provider='https://www.gyan.dev/ffmpeg/builds/',
        source='https://github.com/FFmpeg/FFmpeg/commit/946fcce07b'),
    files=files)
(APP / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(APP)

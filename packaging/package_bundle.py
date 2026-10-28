"""Archive only a pristine, manifest-verified app folder inside project dist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package(app):
    project = Path(__file__).resolve().parents[1]
    app = app.resolve(strict=True)
    if not app.is_relative_to(project / 'dist'):
        raise ValueError('Only app folders inside project dist can be packaged')
    for name in ('output', 'cache', 'logs', 'raw'):
        if (app / name).exists():
            raise ValueError('Use a pristine build; runtime or experimental data must not be packaged')
    manifest_path = app / 'build-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    actual = {str(p.relative_to(app)): p for p in app.rglob('*') if p.is_file()}
    if set(actual) != set(manifest['files']) | {'build-manifest.json'}:
        raise ValueError('Bundle file list differs from build manifest')
    for name, expected in manifest['files'].items():
        if actual[name].is_symlink() or digest(actual[name]) != expected:
            raise ValueError(f'Bundle changed after preparation: {name}')
    archive = app.parent / 'FLIR_SEQ_Tool-portable-preview-win64.zip'
    checksum = archive.with_suffix('.zip.sha256')
    if archive.exists() or checksum.exists():
        raise FileExistsError('Archive or checksum already exists; choose a new build folder')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for name, path in sorted(actual.items()):
            target.write(path, str(Path(app.name) / name))
    with zipfile.ZipFile(archive) as target:
        bad = target.testzip()
        if bad:
            raise ValueError(f'ZIP verification failed: {bad}')
    value = digest(archive)
    with checksum.open('x', encoding='ascii') as target:
        target.write(f'{value}  {archive.name}\n')
    print(archive)
    print(f'SHA256 {value}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', required=True, type=Path)
    package(parser.parse_args().app)

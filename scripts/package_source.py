"""Create a portable source handoff without dependencies, device data, caches or build outputs."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
destination = root / 'artifacts' / 'roomform-source.zip'
destination.parent.mkdir(exist_ok=True)
roots = ['src', 'server', 'tests', 'e2e', 'docs', 'scripts', 'public', 'android', 'ios', '.github']
files = [root / name for name in ['package.json', 'package-lock.json', 'README.md', '.gitignore', '.env.example', 'index.html', 'tsconfig.json', 'vite.config.ts', 'vitest.config.ts', 'playwright.config.ts', 'capacitor.config.ts']]
excluded_dirs = {'node_modules', 'build', '.gradle', 'Pods', 'xcuserdata', 'capacitor-cordova-android-plugins', '__pycache__'}
excluded_names = {'local.properties', 'capacitor.config.json', 'capacitor.plugins.json', '.DS_Store'}
for name in roots:
    for file in (root / name).rglob('*'):
        if not file.is_file():
            continue
        relative = file.relative_to(root)
        if any(part in excluded_dirs for part in relative.parts) or file.name in excluded_names:
            continue
        if str(relative).replace('\\', '/').startswith(('ios/App/App/public/', 'android/app/src/main/assets/public/')):
            continue
        files.append(file)
with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
    for file in sorted(files):
        archive.write(file, Path('roomform') / file.relative_to(root))
print(f'Source handoff: {destination} ({destination.stat().st_size:,} bytes, {len(files)} files)')

from pathlib import Path
import zipfile
root=Path(__file__).resolve().parents[1]
(root/'dist/downloads').mkdir(exist_ok=True)
with zipfile.ZipFile(root/'dist/downloads/nexo-monitor.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for p in sorted((root/'agent').iterdir()):
        if p.is_file():archive.write(p,'nexo-monitor/'+p.name)
print('Nexo Monitor package ready.')

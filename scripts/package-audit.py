"""Package the reviewed read-only collector, with no runtime data or credentials."""
from pathlib import Path
import shutil
import zipfile

root=Path(__file__).resolve().parents[1]
destination=root/'dist/downloads'
destination.mkdir(exist_ok=True)
shutil.copyfile(root/'audit/xui_structure.py',destination/'xui_structure.py')
with zipfile.ZipFile(destination/'nexo-audit.zip','w',zipfile.ZIP_DEFLATED) as archive:
    for name in ('xui_structure.py','README.md'):
        archive.write(root/'audit'/name,'nexo-audit/'+name)
print('Read-only audit package ready.')

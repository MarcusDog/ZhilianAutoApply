"""Allow only program files in release archives. Never include local account data."""
from pathlib import Path
import zipfile
root=Path(__file__).resolve().parent.parent
bundle=root/'dist'/'ZhilianApplyDesk'
target=root/'dist'/'ZhilianApplyDesk-Windows-x64.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in bundle.rglob('*'):
        if not path.is_file():continue
        relative=path.relative_to(bundle)
        if relative.parts[:2]==('docs','docs'):
            continue
        if relative.parts[0]=='data' or path.suffix.lower() in ('.db','.log','.csv'):
            continue
        if any(name in path.name.lower() for name in ('state.json','cookies.json','desktop_error','desktop_last_launch')):
            continue
        archive.write(path,Path('ZhilianApplyDesk')/relative)
print(target)

"""Freeze and assemble a complete Windows release, without private user data."""
import json,os,shutil,subprocess,sys
from pathlib import Path
import cv2

ROOT=Path(__file__).resolve().parents[1]
def main():
    work=ROOT/'build-work';work.mkdir(exist_ok=True)
    cascade=Path(cv2.data.haarcascades)/'haarcascade_frontalface_default.xml'
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','PhotoBooth','--icon',str(ROOT/'assets/app.ico'),'--version-file',str(ROOT/'tools/version_info.txt'),'--distpath',str(work/'dist'),'--workpath',str(work/'build'),'--specpath',str(work),'--collect-binaries','mediapipe','--collect-submodules','comtypes','--add-data',str(cascade)+os.pathsep+'cv2/data',str(ROOT/'source/app.py')]
    subprocess.run(command,check=True)
    release=work/'dist/PhotoBooth'
    for name in ('assets','licenses','docs'):shutil.copytree(ROOT/name,release/name,dirs_exist_ok=True)
    # Never assemble with a customer-edited config or an imported logo.
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    if config.get('sdk_path') or config.get('brand_logo')!='logo-placeholder.png' or (ROOT/'assets/library').exists():
        raise RuntimeError('Use a clean checkout for public releases; customer settings/assets detected.')
    for name in ('config.json','LICENSE','README.md','THIRD_PARTY.md','CHANGELOG.md'):shutil.copy2(ROOT/name,release/name)
    print('Release: '+str(release))

if __name__=='__main__':main()

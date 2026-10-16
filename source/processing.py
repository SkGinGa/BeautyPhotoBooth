"""Local portrait softening and alpha-aware print composition."""
import json
import io
import time
import sys
import threading
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageCms
from template_geometry import crop_photo

SRGB=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
APP_ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
_detectors = threading.local()


def atomic_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix('.tmp')
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    partial.replace(path)


def load_photo(path, max_side=3000):
    with Image.open(path) as source:
        im = ImageOps.exif_transpose(source).convert('RGB')
        if source.info.get('icc_profile'):
            try:
                im=ImageCms.profileToProfile(im,ImageCms.ImageCmsProfile(io.BytesIO(source.info['icc_profile'])),SRGB,outputMode='RGB')
            except (OSError,ValueError,ImageCms.PyCMSError):
                pass
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return im


def detect_faces(rgb):
    """YuNet runs locally; landmarks and boxes are returned in source pixels."""
    model = APP_ROOT / 'assets/models/face_detection_yunet_2023mar.onnx'
    if not model.is_file(): raise RuntimeError('缺少本地人脸模型，请保留完整的 assets 文件夹。')
    ratio = min(1.0, 640 / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    if not hasattr(_detectors, 'yunet'):
        # Native narrow-path file APIs fail with Chinese Windows user/folder names.
        _detectors.yunet = cv2.FaceDetectorYN.create('onnx', np.frombuffer(model.read_bytes(),dtype=np.uint8), np.empty(0,dtype=np.uint8), (320,320), .78, .3, 100)
    detector = _detectors.yunet
    detector.setInputSize((small.shape[1], small.shape[0]))
    _, faces = detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    if faces is None: return []
    faces = faces.copy(); faces[:, :14] /= ratio
    return faces


def beauty_tone(rgb, strength):
    # Lift shadows and midtones; keep pure black/white and roll off highlights.
    level = np.linspace(0, 1, 256, dtype=np.float32)
    lift = (0.15 * np.maximum(0,np.sin(np.pi * level)) ** .85 * (1-level) ** .55) * strength
    lut = np.clip(np.rint((level + lift)*255), 0, 255).astype(np.uint8)
    return cv2.LUT(rgb, lut)


def detect_preview_faces(photo):
    faces=detect_faces(np.asarray(photo))
    return [(float(f[0]/photo.width),float(f[1]/photo.height),float(f[2]/photo.width),float(f[3]/photo.height)) for f in faces]


def beautify_base(photo, strength=0.9):
    """Stronger one-click tone and skin smoothing, without reshaping faces."""
    if not 0 <= strength <= 1: raise ValueError('美颜强度必须在 0 到 1 之间')
    if strength == 0: return photo.copy(), {'faces': 0, 'effect': '关闭'}
    rgb = np.asarray(photo)
    faces = detect_faces(rgb)
    result = rgb.copy()
    for face in faces:
        x,y,w,h = face[:4]
        left=max(0,int(x-w*.12)); top=max(0,int(y-h*.15))
        right=min(photo.width,int(x+w*1.12)); bottom=min(photo.height,int(y+h*1.12))
        if right<=left or bottom<=top: continue
        roi=rgb[top:bottom,left:right]; mask=np.zeros(roi.shape[:2],np.float32)
        center=(round(x+w*.5-left),round(y+h*.48-top))
        cv2.ellipse(mask,center,(max(1,round(w*.48)),max(1,round(h*.58))),0,0,360,1,-1)
        eyes=face[4:8].reshape(2,2); nose=face[8:10]; mouth=face[10:14].reshape(2,2)
        for eye in eyes:
            cv2.ellipse(mask,(round(eye[0]-left),round(eye[1]-top-h*.025)),(max(1,round(w*.18)),max(1,round(h*.09))),0,0,360,0,-1)
        lip=mouth.mean(axis=0)
        cv2.ellipse(mask,(round(lip[0]-left),round(lip[1]-top)),(max(1,round(w*.26)),max(1,round(h*.085))),0,0,360,0,-1)
        cv2.ellipse(mask,(round(nose[0]-left),round(nose[1]-top)),(max(1,round(w*.10)),max(1,round(h*.055))),0,0,360,0,-1)
        ycrcb=cv2.cvtColor(roi,cv2.COLOR_RGB2YCrCb)
        skin=cv2.inRange(ycrcb,(28,125,65),(255,185,145)).astype(np.float32)/255
        mask*=skin; mask=cv2.GaussianBlur(mask,(0,0),max(1.3,w*.012))*strength*.94
        # Adaptive spatial scale removes skin texture while preserving boundaries.
        d=min(31,max(9,round(w*.045)//2*2+1))
        smooth=cv2.bilateralFilter(roi,d,65,max(8,w*.045))
        smooth=cv2.bilateralFilter(smooth,9,40,8)
        blended=roi.astype(np.float32)*(1-mask[:,:,None])+smooth*mask[:,:,None]
        result[top:bottom,left:right]=np.clip(blended,0,255).astype(np.uint8)
    result=beauty_tone(result,strength)
    return Image.fromarray(result), {'faces': len(faces), 'effect': '提亮、阴影提升与人脸磨皮' if len(faces) else '提亮与阴影提升；未检测到人脸', 'strength': strength, 'preset':'portrait-v2'}


def beautify(photo,strength=.9):
    base,note=beautify_base(photo,strength)
    if strength==0:return base,note
    from portrait_effects import apply_portrait_effects
    result,extra=apply_portrait_effects(photo,base,APP_ROOT/'assets/models')
    note.update(extra);note['preset']='portrait-v3';note['base_preset']='portrait-v2-unchanged'
    return Image.fromarray(result),note


def compose(template, asset_root, photos):
    with Image.open(Path(asset_root) / template['overlay']) as source:
        overlay = source.convert('RGBA')
    return compose_on_overlay(template,overlay,photos)


def compose_on_overlay(template,overlay,photos):
    canvas = Image.new('RGB', overlay.size, 'white')
    for slot in template['slots']:
        photo_index = slot['photo']
        if photo_index < 0 or photo_index >= len(photos): raise ValueError('拍摄张数不足，不能生成模板')
        x, y, w, h = slot['box']
        fitted = crop_photo(photos[photo_index], (w,h), slot.get('centering',(.5,.42)),slot.get('zoom',1))
        canvas.paste(fitted, (x,y))
    canvas.paste(overlay, (0,0), overlay)
    return canvas


def process_session(root, session_dir, template, originals, settings, progress=None):
    root = Path(root); session_dir = Path(session_dir)
    if len(originals) != template['shots']: raise ValueError('拍摄张数与模板不一致')
    session_dir.mkdir(parents=True, exist_ok=True)
    processed = []; unretouched = []; notes = []
    mode = settings.get('beauty_mode', 'local')
    for i, source in enumerate(originals):
        if progress: progress(f'正在美化第 {i+1} / {len(originals)} 张照片')
        original = load_photo(source)
        unretouched.append(original)
        if mode == 'local':
            photo, note = beautify(original, settings.get('beauty_strength', .9))
        elif mode == 'external':
            # Only exact session/shot IDs are accepted; stale photos cannot match.
            inbox = root / 'bridge' / 'in'; outbox = root / 'bridge' / 'out'
            inbox.mkdir(parents=True, exist_ok=True); outbox.mkdir(parents=True, exist_ok=True)
            name = f'{session_dir.name}_{i+1:02d}.jpg'
            incoming = inbox / name; temporary = incoming.with_suffix('.tmp')
            original.save(temporary, 'JPEG', quality=97); temporary.replace(incoming)
            deadline = time.monotonic() + settings.get('external_timeout', 120)
            photo = None
            while time.monotonic() < deadline:
                candidate = outbox / name
                if candidate.is_file():
                    try:
                        size = candidate.stat().st_size; time.sleep(.3)
                        if size and size == candidate.stat().st_size:
                            photo = load_photo(candidate); break
                    except (OSError, ValueError): pass
                time.sleep(.3)
            if photo is None: raise TimeoutError('未收到外部美颜成片。检查像素蛋糕自动导出，文件名须完全保持一致。')
            note = {'effect':'外部美颜文件已接收'}
        else: raise ValueError('未知美颜模式')
        out = session_dir / f'beauty_{i+1:02d}.jpg'
        photo.save(out, quality=97, subsampling=0, dpi=(300,300),icc_profile=SRGB.tobytes())
        processed.append(photo); notes.append(note)
    if progress: progress('正在套入所选模板')
    for name, photos in (('print_original.jpg',unretouched),('print_beauty.jpg',processed)):
        result=compose(template,root/'assets',photos)
        final=session_dir/name; partial=final.with_suffix('.tmp')
        result.save(partial,'JPEG',quality=98,subsampling=0,dpi=(300,300),icc_profile=SRGB.tobytes()); partial.replace(final)
    final=session_dir/'print_beauty.jpg'
    return str(final), notes


def sample_photo(index=0):
    """A clearly labelled synthetic test card; never impersonates a camera shot."""
    im = Image.new('RGB',(1500,1000), ('#a9bdc0','#d5c0a9','#b6b5d0','#c0cbaa')[index%4])
    draw=ImageDraw.Draw(im)
    font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',72)
    draw.ellipse((580,170,920,510),fill='#eac4ab')
    draw.rounded_rectangle((440,520,1060,1080),radius=150,fill='#465665')
    draw.text((750,80),f'模拟测试 · 第 {index+1} 张',font=font,fill='#24323a',anchor='mm')
    draw.text((750,880),'模拟图片',font=font,fill='#FFFFFF',anchor='mm')
    return im

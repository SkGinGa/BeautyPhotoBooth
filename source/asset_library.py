"""Immutable asset revisions, transparent-frame detection and recoverable removal."""
import copy
import uuid
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageOps,ImageDraw
from processing import atomic_json,compose,sample_photo


def migrate_config(config):
    config.setdefault('preview_title','拍照免费，删一张50（本标题可编辑）')
    config.setdefault('brand_logo','logo-placeholder.png')
    config['app_name']='美颜自拍亭'
    config.setdefault('camera_backend','auto')
    config.setdefault('video_device','')
    config.setdefault('auto_driver_check',True)
    config.setdefault('sdk_path','')
    config.setdefault('covers',[{'id':'welcome-default','name':'默认欢迎封面','image':'welcome.jpg'}])
    config.setdefault('active_cover',config['covers'][0]['id'] if config['covers'] else None)
    config.setdefault('archived_templates',[]);config.setdefault('archived_covers',[])
    config['version']=5
    return config


def asset_path(root,name):
    base=(Path(root)/'assets').resolve();path=(base/name).resolve()
    if not path.is_relative_to(base):raise ValueError('素材路径无效')
    return path


def load_asset_image(source,template=False):
    with Image.open(source) as im:
        if im.width*im.height>65_000_000:raise ValueError('图片过大，请先缩小至 6500 万像素以内')
        rgba=ImageOps.exif_transpose(im).convert('RGBA')
        if template:image=rgba
        else:
            backdrop=Image.new('RGBA',rgba.size,'#F4EEE6');backdrop.alpha_composite(rgba);image=backdrop.convert('RGB')
    image.thumbnail((3600,3600) if template else (2560,2560),Image.Resampling.LANCZOS)
    if min(image.size)<100:raise ValueError('图片尺寸过小，宽高至少需要 100 像素')
    return image


def import_logo(root,source):
    """Create a private, bounded RGBA derivative; never modify the selected file."""
    with Image.open(source) as original:
        if original.width*original.height>120_000_000:
            raise ValueError('Logo 过大，请先缩小到 1.2 亿像素以内')
        if min(original.size)<8:raise ValueError('Logo 宽高至少需要 8 像素')
        image=ImageOps.exif_transpose(original).convert('RGBA')
    image.thumbnail((1800,800),Image.Resampling.LANCZOS)
    name=f'library/logo-{uuid.uuid4().hex}.png'
    path=asset_path(root,name);path.parent.mkdir(parents=True,exist_ok=True)
    image.save(path);return name


def detect_slots(image):
    alpha=np.asarray(image.convert('RGBA'))[:,:,3]
    _,labels,stats,_=cv2.connectedComponentsWithStats((alpha<128).astype(np.uint8),8)
    total=image.width*image.height;boxes=[]
    for x,y,w,h,area in stats[1:]:
        # Transparent outer background is not a photo aperture.
        if x<=1 or y<=1 or x+w>=image.width-1 or y+h>=image.height-1:continue
        if area<total*.008 or w<40 or h<40 or area/(w*h)<.50:continue
        boxes.append([int(x),int(y),int(w),int(h)])
    boxes.sort(key=lambda b:(round(b[1]/max(20,image.height*.025)),b[0]))
    if len(boxes)>16:raise ValueError('检测到超过 16 个透明区域，请使用照片框编辑器手动设置')
    return [{'box':box,'photo':i} for i,box in enumerate(boxes)]


def detect_template_slots(image):
    slots=detect_slots(image)
    if slots:return slots,'transparent'
    rgba=np.asarray(image.convert('RGBA'));rgb=rgba[:,:,:3]
    light=((rgb.min(axis=2)>=235)&(rgb.max(axis=2)-rgb.min(axis=2)<=18)&(rgba[:,:,3]>240)).astype(np.uint8)
    _,_,stats,_=cv2.connectedComponentsWithStats(light,8);total=image.width*image.height;boxes=[]
    for x,y,w,h,area in stats[1:]:
        if x<=2 or y<=2 or x+w>=image.width-2 or y+h>=image.height-2:continue
        if area<total*.012 or min(w,h)<60 or not .22<w/h<4.6 or area/(w*h)<.94:continue
        boxes.append([int(x),int(y),int(w),int(h)])
    boxes.sort(key=lambda b:(round(b[1]/max(20,image.height*.025)),b[0]))
    if len(boxes)>16:return [],'manual'
    return [{'box':b,'photo':i,'cutout':True} for i,b in enumerate(boxes)],'light' if boxes else 'manual'


def prepare_overlay(image,slots,old_slots=None):
    """Fill vacated windows using their surrounding colour, then cut new ones."""
    result=image.convert('RGBA');arr=np.array(result)
    old_slots=detect_template_slots(image)[0] if old_slots is None else old_slots
    boxes=[s['box'] for s in slots]
    for old in old_slots:
        x,y,w,h=old['box']
        if old['box'] in boxes:continue
        gap=max(2,round(min(w,h)*.02));x0=max(0,x-gap*2);y0=max(0,y-gap*2);x1=min(image.width,x+w+gap*2);y1=min(image.height,y+h+gap*2)
        ring=arr[y0:y1,x0:x1].copy();valid=ring[:,:,3]>240
        valid[max(0,y-y0-gap):min(y1-y0,y+h-y0+gap),max(0,x-x0-gap):min(x1-x0,x+w-x0+gap)]=False
        pixels=ring[:,:,:3][valid];colour=np.median(pixels,axis=0).astype(np.uint8) if len(pixels) else np.array([244,238,230],np.uint8)
        arr[y:y+h,x:x+w,:3]=colour;arr[y:y+h,x:x+w,3]=255
    result=Image.fromarray(arr);alpha=result.getchannel('A');draw=ImageDraw.Draw(alpha)
    for slot in slots:
        if slot.get('cutout',False):
            x,y,w,h=slot['box'];draw.rectangle((x,y,x+w-1,y+h-1),fill=0)
    result.putalpha(alpha);return result


def validate_slots(canvas,slots):
    if not 1<=len(slots)<=16:raise ValueError('请设置 1 至 16 个照片框')
    w,h=canvas;indices=[]
    for slot in slots:
        x,y,bw,bh=slot['box'];index=slot['photo']
        if not all(isinstance(v,int) for v in (x,y,bw,bh,index)):raise ValueError('照片框坐标及照片编号必须为整数')
        if x<0 or y<0 or bw<20 or bh<20 or x+bw>w or y+bh>h:raise ValueError('照片框必须位于模板内，宽高至少 20 像素')
        if not 0<=index<12:raise ValueError('照片编号必须在 1 至 12 之间')
        indices.append(index)
        center=slot.get('centering',(.5,.42));zoom=slot.get('zoom',1)
        if len(center)!=2 or not all(isinstance(v,(float,int)) and 0<=v<=1 for v in center) or not isinstance(zoom,(float,int)) or not 1<=zoom<=3:raise ValueError('照片位置与放大倍数无效')
    if set(indices)!=set(range(max(indices)+1)):raise ValueError('照片编号必须从 1 连续排列；相同编号表示重复使用同一张')
    return max(indices)+1


def template_ready(template):
    try:return validate_slots(template['canvas'],template['slots'])==template['shots']
    except (ValueError,KeyError,TypeError):return False


def template_thumbnail(root,template):
    if template_ready(template):
        images=[sample_photo(i) for i in range(template['shots'])]
        image=compose(template,Path(root)/'assets',images)
    else:
        with Image.open(asset_path(root,template['overlay'])) as source:
            rgba=source.convert('RGBA');image=Image.new('RGB',rgba.size,'#CDDCE3');image.paste(rgba,(0,0),rgba)
    image.thumbnail((720,720),Image.Resampling.LANCZOS)
    name=f"library/preview-{uuid.uuid4().hex}.jpg";path=asset_path(root,name);path.parent.mkdir(parents=True,exist_ok=True)
    image.save(path,quality=93);template['thumbnail']=name


def import_template(root,source):
    source=Path(source);image=load_asset_image(source,True);ident=uuid.uuid4().hex
    name=f'library/template-{ident}.png';path=asset_path(root,name);path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
    try:slots,kind=detect_template_slots(image)
    except ValueError:slots=[];kind='manual'
    repeated=(image.height>image.width*1.3 and len(slots)==6 and all(abs(slots[i]['box'][1]-slots[i+1]['box'][1])<image.height*.025 and slots[i+1]['box'][0]-slots[i]['box'][0]>image.width*.25 for i in (0,2,4)))
    if repeated:
        for i,slot in enumerate(slots):slot['photo']=i//2
    overlay_name=name
    if kind=='light':
        overlay_name=f'library/overlay-{ident}.png';prepare_overlay(image,slots).save(asset_path(root,overlay_name))
    result={'id':ident,'name':source.stem[:40],'subtitle':'两列同组（可编辑）' if repeated else '自动识别照片框' if slots else '需设置照片框','shots':max([s['photo'] for s in slots],default=-1)+1,
            'overlay':overlay_name,'source_overlay':name,'source_name':source.name,'detection':kind,'canvas':list(image.size),'slots':slots}
    template_thumbnail(root,result);return result


def import_cover(root,source):
    image=load_asset_image(source);ident=uuid.uuid4().hex;name=f'library/cover-{ident}.jpg'
    path=asset_path(root,name);path.parent.mkdir(parents=True,exist_ok=True);image.save(path,quality=95)
    return {'id':ident,'name':Path(source).stem[:40],'image':name}


def import_batch(root,paths,kind):
    imported=[];errors=[]
    action=import_template if kind=='templates' else import_cover
    for source in dict.fromkeys(paths):
        try:imported.append(action(root,source))
        except (OSError,ValueError,Image.DecompressionBombError) as exc:errors.append(f'{Path(source).name}：{exc}')
    return imported,errors


def revise_template(root,template,name,slots):
    shots=validate_slots(template['canvas'],slots)
    if not name.strip():raise ValueError('请填写模板名称')
    revised=copy.deepcopy(template);revised['id']=uuid.uuid4().hex;revised['name']=name.strip()[:40]
    revised['slots']=copy.deepcopy(slots);revised['shots']=shots;revised['subtitle']=f'{len(slots)} 个照片框'
    revised['source_overlay']=template.get('source_overlay',template['overlay'])
    # Cut apertures in a private derivative. The imported source stays intact.
    with Image.open(asset_path(root,template.get('source_overlay',template['overlay']))) as source:overlay=source.convert('RGBA')
    overlay=prepare_overlay(overlay,slots)
    new_name=f"library/template-{revised['id']}.png";path=asset_path(root,new_name);path.parent.mkdir(parents=True,exist_ok=True);overlay.save(path);revised['overlay']=new_name
    template_thumbnail(root,revised);return revised


def remove_items(config,kind,ids):
    ids=set(ids);removed=[item for item in config[kind] if item['id'] in ids]
    config['archived_'+kind].extend(copy.deepcopy(removed))
    config[kind]=[item for item in config[kind] if item['id'] not in ids]
    if kind=='covers' and config.get('active_cover') in ids:config['active_cover']=config[kind][0]['id'] if config[kind] else None
    return len(removed)


def replace_item(config,kind,old_id,new_item):
    index=next(i for i,item in enumerate(config[kind]) if item['id']==old_id)
    was_active=kind=='covers' and config.get('active_cover')==old_id
    config['archived_'+kind].append(copy.deepcopy(config[kind][index]));config[kind][index]=new_item
    if was_active:config['active_cover']=new_item['id']


def restore_items(config,kind,ids):
    ids=set(ids);existing={item['id'] for item in config[kind]}
    restored=[item for item in config['archived_'+kind] if item['id'] in ids and item['id'] not in existing]
    config[kind].extend(copy.deepcopy(restored));config['archived_'+kind]=[item for item in config['archived_'+kind] if item['id'] not in ids]
    if kind=='covers' and not config.get('active_cover') and restored:config['active_cover']=restored[0]['id']


def save_library(root,config):
    config=copy.deepcopy(config);migrate_config(config)
    title=config['preview_title'].strip()
    if not title or len(title)>40:raise ValueError('预览标题请填写 1 至 40 个字符')
    config['preview_title']=title
    if not asset_path(root,config['brand_logo']).is_file():raise ValueError('Logo 文件不存在，请重新选择')
    all_ids=[]
    for kind in ('templates','covers'):
        for item in config[kind]:
            all_ids.append(item['id'])
            for key in ('overlay','thumbnail') if kind=='templates' else ('image',):
                if not asset_path(root,item[key]).is_file():raise ValueError(f"素材文件不存在：{item['name']}")
    if len(all_ids)!=len(set(all_ids)):raise ValueError('素材编号重复，请取消本次修改后重试')
    atomic_json(Path(root)/'config.json',config);return config

"""Original neutral artwork; rebuilding never touches existing user config."""
import json,sys
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
from processing import compose,sample_photo

def main():
    assets=ROOT/'assets';assets.mkdir(exist_ok=True)
    def font(size):return ImageFont.truetype('C:/Windows/Fonts/arial.ttf',size)
    logo=Image.new('RGBA',(700,180));d=ImageDraw.Draw(logo)
    d.text((350,90),'logo',font=font(130),fill='#075394',anchor='mm');# Keep the supplied default logo; do not overwrite it.
    icon=Image.new('RGBA',(256,256),'#075394');d=ImageDraw.Draw(icon)
    d.rounded_rectangle((36,72,220,202),radius=24,fill='#F4EEE6');d.rounded_rectangle((70,48,132,90),radius=12,fill='#F4EEE6')
    d.ellipse((80,94,178,192),fill='#075394');d.ellipse((101,115,157,171),fill='#F4EEE6')
    icon.save(assets/'app.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    cover=Image.new('RGB',(1600,900),'#E9EFF1');d=ImageDraw.Draw(cover)
    d.ellipse((1120,-180,1840,540),fill='#D9E5E8');d.ellipse((-200,550,420,1170),fill='#E4D5C3')
    d.text((800,340),'BEAUTY PHOTO BOOTH',font=font(88),fill='#075394',anchor='mm')
    d.text((800,455),'CAPTURE A MOMENT',font=font(40),fill='#796E64',anchor='mm')
    d.rounded_rectangle((660,560,940,730),radius=24,fill='#075394');d.ellipse((736,585,864,713),outline='#F4EEE6',width=13)
    cover.save(assets/'welcome.jpg',quality=95)
    specs=[
        ('single','简约 · 单张',[1800,1200],[[60,60,1680,990,0]],'#EFF3F4'),
        ('four','浅蓝 · 四格',[1800,1200],[[60,60,810,480,0],[930,60,810,480,1],[60,580,810,480,2],[930,580,810,480,3]],'#DDEBF2'),
        ('strips','双三格 · 同组',[1200,1800],[[45,45,510,420,0],[645,45,510,420,0],[45,495,510,420,1],[645,495,510,420,1],[45,945,510,420,2],[645,945,510,420,2]],'#EAE4D9'),
        ('two','竖版 · 双格',[1200,1800],[[60,60,1080,680,0],[60,780,1080,680,1]],'#E5E7F0'),
        ('wide','暖色 · 单张',[1800,1200],[[80,80,1300,1040,0]],'#EEDBC7')]
    templates=[]
    for ident,name,canvas,boxes,color in specs:
        overlay=Image.new('RGBA',canvas,color);d=ImageDraw.Draw(overlay)
        slots=[]
        for x,y,w,h,n in boxes:
            d.rectangle((x,y,x+w-1,y+h-1),fill=(0,0,0,0));slots.append({'box':[x,y,w,h],'photo':n})
        if ident=='wide':d.text((1590,570),'SMILE',font=font(65),fill='#075394',anchor='mm')
        else:d.text((canvas[0]//2,canvas[1]-65),'BEAUTY PHOTO BOOTH',font=font(37),fill='#075394',anchor='mm')
        overlay.save(assets/(ident+'.png'))
        template={'id':ident,'name':name,'subtitle':'两列同组' if ident=='strips' else '6 英寸成片','shots':max(b[-1] for b in boxes)+1,'overlay':ident+'.png','thumbnail':ident+'-preview.jpg','canvas':canvas,'slots':slots}
        thumb=compose(template,assets,[sample_photo(i) for i in range(template['shots'])]);thumb.thumbnail((600,600));thumb.save(assets/template['thumbnail'],quality=92)
        templates.append(template)
    config={'version':5,'app_name':'美颜自拍亭','printer':'DP-DS620','sdk_path':'','countdown_seconds':5,'beauty_mode':'local','beauty_strength':.9,'external_timeout':120,'mirror_preview':True,'print_media':'6x4','templates':templates,'preview_title':'拍照免费，删一张50（本标题可编辑）','brand_logo':'logo-placeholder.png','covers':[{'id':'welcome-default','name':'默认欢迎封面','image':'welcome.jpg'}],'active_cover':'welcome-default','archived_templates':[],'archived_covers':[]}
    if not (ROOT/'config.json').exists():(ROOT/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Neutral assets ready')

if __name__=='__main__':main()

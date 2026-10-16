"""Chinese offline photo booth for Canon + DNP, Windows desktop application."""
import argparse
import ctypes
import copy
import io
import json
import logging
import math
import os
import queue
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageOps, ImageFilter
from camera import CanonCamera, focus_status
from camera_backends import CameraHub
from preview_geometry import PreviewTransform
from processing import atomic_json, load_photo, process_session, sample_photo, detect_preview_faces
import printer
import asset_library

ROOT = Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
BG = '#F4EEE6'; INK = '#342D29'; RED = '#932E30'; MUTED = '#796E64'; CREAM = '#FAF7F2'


class Booth:
    def __init__(self, root, demo=False, fullscreen=False):
        self.root=root;self.demo=demo;self.fullscreen=fullscreen
        self.ui_scale=max(1.0,float(root.tk.call('tk','scaling'))/(96/72))
        self.config=asset_library.migrate_config(json.loads((ROOT/'config.json').read_text(encoding='utf-8')))
        self.templates=self.config['templates'];self.state='welcome';self.session=None
        self.capture_busy=False;self.auto_sequence=False;self.timer=None;self.tick_count=0
        self.focus_busy=False;self.focus_target=None;self.focus_result=None;self.live_transform=None
        self.face_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='PreviewFaces')
        self.images=[];self.messages=queue.Queue();self.ui_token=0;self.closing=False
        self.layout_job=None;self.preview_source=None;self.welcome_source=None;self.template_layout_key=None
        self.camera=None if demo else CameraHub(self.config)
        self.root.title(self.config['app_name'] + (' · 模拟测试（不打印）' if demo else ''))
        try:self.root.iconbitmap(str(ROOT/'assets'/'app.ico'))
        except tk.TclError:pass
        width=min(round(1280*self.ui_scale),root.winfo_screenwidth()-60)
        height=min(round(820*self.ui_scale),root.winfo_screenheight()-100)
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(min(width,round(1000*self.ui_scale)),min(height,round(680*self.ui_scale)))
        self.root.configure(bg=BG);self.root.attributes('-fullscreen',fullscreen)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.bind('<F11>',lambda e:self.toggle_fullscreen())
        self.root.bind('<F10>',lambda e:self.settings())
        self.root.bind('<space>',lambda e:self.manual_key())
        self.root.bind('<Escape>',lambda e:self.escape())
        self.header=tk.Frame(root,bg=BG,height=84);self.header.pack(fill='x',padx=38,pady=(20,0))
        self.brand_label=tk.Label(self.header,bg=BG);self.brand_label.pack(side='left');self.refresh_brand()
        self.top_status=tk.Label(self.header,text='',font=('Microsoft YaHei UI',11),bg=BG,fg=MUTED)
        self.top_status.pack(side='right')
        self.content=tk.Frame(root,bg=BG);self.content.pack(fill='both',expand=True,padx=38,pady=18)
        self.content.bind('<Configure>',self.schedule_layout)
        footer=tk.Frame(root,bg=BG);footer.pack(fill='x',padx=38,pady=(0,14))
        tk.Label(footer,text='选模板  →  拍照  →  自动美颜  →  对比与打印',font=('Microsoft YaHei UI',11),bg=BG,fg=MUTED).pack(side='left')
        tk.Label(footer,text='工作人员：空格立即拍摄  ·  F10 设置  ·  F11 全屏',font=('Microsoft YaHei UI',10),bg=BG,fg=MUTED).pack(side='right')
        self.show_welcome()
        self.root.after(100,self.tick)
        if not demo: self.root.after(400,self.connect_camera)

    def button(self,parent,text,command,primary=True,**kwargs):
        return tk.Button(parent,text=text,command=command,font=('Microsoft YaHei UI',17,'bold'),
                         bg=RED if primary else '#E7DDD0',fg='white' if primary else INK,
                         activebackground='#78272A' if primary else '#D9CAB8',activeforeground='white' if primary else INK,
                         relief='flat',bd=0,padx=24,pady=15,cursor='hand2',disabledforeground='#B4A59B',**kwargs)

    def label(self,parent,text,size=18,color=INK):
        return tk.Label(parent,text=text,font=('Microsoft YaHei UI',size),bg=BG,fg=color)

    def refresh_brand(self):
        try:
            with Image.open(asset_library.asset_path(ROOT,self.config['brand_logo'])) as source:logo=source.convert('RGBA')
            self.set_picture(self.brand_label,logo,(round(300*self.ui_scale),round(70*self.ui_scale)))
            self.brand_label.config(text='')
        except (OSError,ValueError):self.brand_label.config(image='',text=self.config['app_name'],font=('Microsoft YaHei UI',21,'bold'),fg='#075394')

    def clear(self,state):
        self.cancel_timer();self.ui_token+=1;self.state=state;self.images=[]
        if self.layout_job is not None:
            self.root.after_cancel(self.layout_job);self.layout_job=None
        self.preview_source=None;self.welcome_source=None;self.template_layout_key=None
        for child in self.content.winfo_children():child.destroy()

    def schedule_layout(self,event=None):
        if self.closing or self.state not in ('welcome','templates','preview','printing'):return
        if self.layout_job is not None:self.root.after_cancel(self.layout_job)
        self.layout_job=self.root.after(80,self.resize_page)

    def resize_page(self):
        self.layout_job=None
        if self.state=='welcome':
            if self.welcome_source is not None:self.draw_fitted_photo(self.welcome_picture,self.welcome_source)
            else:
                self.welcome_picture.delete('photo')
                self.welcome_picture.create_text(self.welcome_picture.winfo_width()/2,self.welcome_picture.winfo_height()/2,
                    text='欢迎拍照\n留下这一刻的美好',font=('Microsoft YaHei UI',34,'bold'),fill='#075394',tags='photo',justify='center')
        elif self.state=='templates':self.layout_templates()
        elif self.state in ('preview','printing') and self.preview_source is not None:
            self.preview_title.config(wraplength=max(240,self.content.winfo_width()-self.beauty_btn.winfo_reqwidth()-24))
            self.draw_fitted_photo(self.preview_picture,self.preview_source,8)

    def draw_fitted_photo(self,canvas,photo,margin=0):
        width=canvas.winfo_width();height=canvas.winfo_height()
        if width<=2 or height<=2:return
        factor=min(max(1,width-margin*2)/photo.width,max(1,height-margin*2)/photo.height)
        size=(max(1,round(photo.width*factor)),max(1,round(photo.height*factor)))
        # Canvas image items do not change the widget's requested size.
        # This keeps large images from pushing buttons outside the window.
        canvas.image=ImageTk.PhotoImage(photo.resize(size,Image.Resampling.LANCZOS))
        canvas.delete('photo');canvas.create_image(width/2,height/2,image=canvas.image,tags='photo')

    def set_picture(self,widget,photo,max_size,upscale=False):
        im=photo.copy()
        if upscale:
            factor=min(max_size[0]/im.width,max_size[1]/im.height)
            im=im.resize((max(1,round(im.width*factor)),max(1,round(im.height*factor))),Image.Resampling.BILINEAR)
        else:im.thumbnail(max_size,Image.Resampling.LANCZOS)
        rendered=ImageTk.PhotoImage(im);widget.configure(image=rendered);widget.image=rendered

    def show_welcome(self):
        self.clear('welcome');self.session=None;self.capture_busy=False
        frame=tk.Frame(self.content,bg=BG);frame.pack(fill='both',expand=True)
        self.button(frame,'留下今天的笑容  ·  选择模板',self.show_templates).pack(side='bottom',pady=(12,0))
        self.welcome_picture=tk.Canvas(frame,bg=BG,highlightthickness=0,width=1,height=1,cursor='hand2')
        self.welcome_picture.pack(fill='both',expand=True)
        self.welcome_picture.bind('<Configure>',self.schedule_layout)
        cover=next((c for c in self.config['covers'] if c['id']==self.config.get('active_cover')),None)
        if cover:
            with Image.open(asset_library.asset_path(ROOT,cover['image'])) as source:self.welcome_source=source.copy()
        self.welcome_picture.bind('<Button-1>',lambda e:self.show_templates())
        self.schedule_layout()

    def show_templates(self,page=0):
        self.clear('templates');self.session=None
        self.available_templates=[t for t in self.templates if asset_library.template_ready(t)]
        self.template_page=max(0,page);self.template_capacity=None;self.template_cards=[]
        heading=tk.Frame(self.content,bg=BG);heading.pack(fill='x')
        self.label(heading,'选一款喜欢的版式',28).pack(side='left')
        self.template_next=tk.Button(heading,text='下一页 →',command=lambda:self.change_template_page(1),font=('Microsoft YaHei UI',14),padx=15,pady=10,bg=CREAM,relief='flat');self.template_next.pack(side='right')
        self.template_page_label=self.label(heading,'',15,MUTED);self.template_page_label.pack(side='right',padx=18)
        self.template_previous=tk.Button(heading,text='← 上一页',command=lambda:self.change_template_page(-1),font=('Microsoft YaHei UI',14),padx=15,pady=10,bg=CREAM,relief='flat');self.template_previous.pack(side='right')
        self.label(self.content,'拍摄完成后可以预览，不满意就重新拍。',13,MUTED).pack(anchor='w',pady=(4,8))
        self.button(self.content,'返回欢迎页',self.show_welcome,False).pack(side='bottom',anchor='w',pady=(10,0))
        self.template_area=tk.Frame(self.content,bg=BG);self.template_area.pack(fill='both',expand=True)
        self.template_area.bind('<Configure>',self.schedule_layout)
        self.schedule_layout()

    def change_template_page(self,delta):
        if self.state!='templates':return
        self.template_page+=delta;self.layout_templates()

    def layout_templates(self):
        cards=self.template_area;width=cards.winfo_width();height=cards.winfo_height()
        if width<=2 or height<=2:return
        available=self.available_templates
        max_columns=max(1,min(3,int(width/(320*self.ui_scale))))
        max_rows=max(1,min(3,int(height/(310*self.ui_scale))))
        rows=min(max_rows,max(1,math.ceil(len(available)/max_columns)))
        columns=min(max_columns,max(1,math.ceil(len(available)/rows)))
        capacity=rows*columns
        if self.template_capacity and self.template_capacity!=capacity:
            self.template_page=self.template_page*self.template_capacity//capacity
        self.template_capacity=capacity
        pages=max(1,math.ceil(len(available)/capacity));self.template_page=max(0,min(self.template_page,pages-1))
        self.template_page_label.config(text=f'{self.template_page+1} / {pages}')
        self.template_previous.config(state='normal' if self.template_page else 'disabled')
        self.template_next.config(state='normal' if self.template_page<pages-1 else 'disabled')
        shown=available[self.template_page*capacity:(self.template_page+1)*capacity]
        key=(rows,columns,self.template_page,tuple(t['id'] for t in shown))
        if key!=self.template_layout_key:
            self.template_layout_key=key
            for child in cards.winfo_children():child.destroy()
            self.template_cards=[]
            for index in range(3):
                cards.columnconfigure(index,weight=1 if index<columns else 0,uniform='templates' if index<columns else '')
                cards.rowconfigure(index,weight=1 if index<rows else 0,uniform='templates' if index<rows else '')
            if not shown:self.label(cards,'暂无可用模板，请工作人员按 F10 添加并设置照片框。',20,MUTED).grid(row=0,column=0,sticky='nsew')
            for i,template in enumerate(shown):self.add_template_card(cards,i,columns,template)
        self.root.update_idletasks()
        for card,pic,photo,title in self.template_cards:
            title.config(wraplength=max(100,card.winfo_width()-20))
            self.draw_fitted_photo(pic,photo,4)

    def add_template_card(self,cards,index,columns,template):
        card=tk.Frame(cards,bg=CREAM,highlightbackground='#DDD0C2',highlightthickness=1)
        card.grid(row=index//columns,column=index%columns,sticky='nsew',padx=6,pady=6)
        self.button(card,'选这个',lambda t=template:self.select_template(t)).pack(side='bottom',fill='x',padx=10,pady=(5,8))
        tk.Label(card,text=f"拍 {template['shots']} 张 · {template['subtitle']}",font=('Microsoft YaHei UI',11),bg=CREAM,fg=MUTED).pack(side='bottom',pady=(0,2))
        title=tk.Label(card,text=template['name'],font=('Microsoft YaHei UI',15,'bold'),bg=CREAM,fg=INK)
        title.pack(side='bottom',pady=(3,3))
        pic=tk.Canvas(card,bg=CREAM,cursor='hand2',highlightthickness=0,width=1,height=1)
        pic.pack(fill='both',expand=True,padx=7,pady=(7,0));pic.bind('<Configure>',self.schedule_layout)
        with Image.open(ROOT/'assets'/template['thumbnail']) as source:photo=source.copy()
        pic.bind('<Button-1>',lambda e,t=template:self.select_template(t))
        self.template_cards.append((card,pic,photo,title))

    def select_template(self,template):
        self.template=template
        if self.camera and self.camera.connected:self.camera.request('reset_focus')
        now=datetime.now(timezone(timedelta(hours=8)))
        session_id=now.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
        folder=ROOT/'data'/('demo' if self.demo else 'sessions')/session_id
        folder.mkdir(parents=True,exist_ok=False)
        self.session={'id':session_id,'started_at':now.isoformat(),'template':template['id'],
                      'template_snapshot':copy.deepcopy(template),'originals':[],'status':'ready','print_state':'not_requested','demo':self.demo,'beauty_enabled':True}
        self.session_dir=folder;self.save_session();self.show_capture()

    def save_session(self):
        if self.session:atomic_json(self.session_dir/'session.json',self.session)

    def show_capture(self):
        self.clear('capture');self.capture_busy=False;self.auto_sequence=False
        self.focus_busy=False;self.focus_target=None;self.focus_result=None;self.live_transform=None;self.render_key=None
        self.face_busy=False;self.face_at=0;self.face_boxes=[];self.face_time=0
        title=tk.Frame(self.content,bg=BG);title.pack(fill='x')
        self.label(title,self.template['name'],24).pack(side='left')
        self.shot_label=self.label(title,'',16,MUTED);self.shot_label.pack(side='right')
        self.live_panel=tk.Canvas(self.content,bg='#282725',highlightthickness=0,cursor='crosshair',height=300)
        self.live_panel.pack(fill='both',expand=True,pady=15)
        self.live_panel.bind('<Button-1>',self.tap_focus)
        self.count_label=tk.Label(self.live_panel,text='',font=('Microsoft YaHei UI',68,'bold'),bg=RED,fg='white')
        self.capture_note=self.label(self.content,'看向镜头，准备好后选择拍摄方式。',14,MUTED);self.capture_note.pack(pady=6)
        row=tk.Frame(self.content,bg=BG);row.pack(fill='x',pady=(5,8))
        self.auto_btn=self.button(row,'5 秒倒计时' + (' · 自动连拍' if self.template['shots']>1 else '拍摄'),self.start_auto)
        self.auto_btn.pack(side='left',fill='x',expand=True,padx=(0,10))
        self.manual_btn=self.button(row,'立即拍摄 · 工作人员',self.manual_capture,False)
        self.manual_btn.pack(side='left',fill='x',expand=True,padx=(10,0))
        bottom=tk.Frame(self.content,bg=BG);bottom.pack(fill='x')
        self.back_btn=tk.Button(bottom,text='← 重新选模板',command=self.back_from_capture,font=('Microsoft YaHei UI',12),relief='flat',bg=BG,fg=MUTED)
        self.back_btn.pack(side='left')
        self.reconnect_btn=tk.Button(bottom,text='重新连接相机',command=self.connect_camera,font=('Microsoft YaHei UI',12),relief='flat',bg=BG,fg=RED)
        self.reconnect_btn.pack(side='right')
        self.focus_btn=tk.Button(bottom,text='◎ 重新对焦',command=self.refocus,font=('Microsoft YaHei UI',13,'bold'),relief='flat',bg=BG,fg=RED,padx=18)
        self.focus_btn.pack(side='right',padx=25)
        self.update_shot_label()

    def tap_focus(self,event):
        if self.state!='capture' or self.capture_busy or self.focus_busy or not self.live_transform:return
        target=self.live_transform.to_camera(event.x,event.y)
        if target:self.start_focus(target)

    def refocus(self):
        if self.state!='capture' or self.capture_busy or self.focus_busy:return
        if not self.live_transform:return
        dx,dy,w,h=self.live_transform.display
        self.start_focus(self.focus_target or self.live_transform.to_camera(dx+w/2,dy+h/2))

    def start_focus(self,target):
        self.focus_target=target;self.focus_result=None
        if self.demo:
            self.focus_result={'state':'demo','target':target}
            self.capture_note.config(text='模拟对焦框；正式拍摄时会驱动相机对焦。',fg=MUTED);self.render_key=None;return
        if not self.camera.supports_point_focus:
            self.focus_result={'state':'unconfirmed','target':target};self.render_key=None
            self.capture_note.config(text='USB视频模式不支持镜头点按对焦，请在相机上调焦或启用相机自身自动对焦。',fg=MUTED);return
        self.focus_busy=True
        for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='disabled')
        self.capture_note.config(text='正在对焦，请稍候…',fg=MUTED)
        self.watch_future(self.camera.request('focus',x=target[0],y=target[1]),self.focus_done,self.focus_failed)

    def focus_done(self,result):
        self.focus_busy=False;self.focus_result=dict(result,expires=time.monotonic()+3);self.focus_target=result['target']
        text={'focused':'已合焦，可以拍摄；移动位置后可再次点按对焦。','failed':'此处未能合焦，请点选人物或更清晰的边缘。','unconfirmed':'未确认合焦，请观察清晰度或再次点按对焦。'}[result['state']]
        self.capture_note.config(text=text,fg='#237A57' if result['state']=='focused' else RED)
        for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='normal')
        self.render_key=None

    def focus_failed(self,error):
        self.focus_done({'state':'unconfirmed','target':self.focus_target})
        self.capture_note.config(text=str(error),fg=RED)

    def update_shot_label(self):
        n=len(self.session['originals']);self.shot_label.config(text=f"已拍 {n} / {self.template['shots']} 张")

    def back_from_capture(self):
        if self.capture_busy or self.focus_busy:return
        self.cancel_timer();self.auto_sequence=False
        self.session['status']='abandoned';self.save_session();self.show_templates()

    def can_capture(self):
        if self.state not in ('capture','countdown') or self.capture_busy or self.focus_busy:return False
        if not self.demo and (not self.camera.connected or self.camera.tainted):
            messagebox.showwarning('相机未就绪',self.camera.error or '请唤醒相机，然后点击“重新连接相机”。',parent=self.root);return False
        return True

    def start_auto(self):
        if not self.can_capture():return
        self.auto_sequence=True;self.start_countdown()

    def start_countdown(self):
        self.state='countdown';self.auto_btn.config(state='disabled');self.back_btn.config(state='disabled')
        self.manual_btn.config(state='normal')
        self.reconnect_btn.config(state='disabled')
        self.focus_btn.config(state='disabled');self.focus_result=None
        self.capture_note.config(text=f"第 {len(self.session['originals'])+1} 张，准备好笑容！")
        self.deadline=time.monotonic()+5
        self.count_label.place(relx=.5,rely=.5,anchor='center')
        self.countdown_step()

    def countdown_step(self):
        if self.state!='countdown':return
        remaining=self.deadline-time.monotonic()
        if remaining<=0:
            self.timer=None;self.take_photo();return
        self.count_label.config(text=str(min(5,math.ceil(remaining))));self.timer=self.root.after(80,self.countdown_step)

    def cancel_timer(self):
        if self.timer:
            try:self.root.after_cancel(self.timer)
            except tk.TclError:pass
            self.timer=None

    def manual_key(self):
        if self.state in ('capture','countdown'):self.manual_capture()

    def manual_capture(self):
        if not self.can_capture():return
        self.cancel_timer();self.auto_sequence=False;self.take_photo()

    def take_photo(self):
        if self.capture_busy or not self.session:return
        self.cancel_timer();self.state='capture';self.capture_busy=True;self.count_label.place_forget()
        for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='disabled')
        self.capture_note.config(text='正在拍摄并接收照片，请稍候…')
        index=len(self.session['originals'])+1
        path=self.session_dir/f'original_{index:02d}.jpg'
        if self.demo:
            def synthetic():
                sample_photo(index-1).save(path,quality=96);return str(path)
            self.background(synthetic,self.photo_received,self.capture_failed)
        else:
            future=self.camera.request('capture',path=path)
            self.watch_future(future,self.photo_received,self.capture_failed)

    def photo_received(self,path):
        self.capture_busy=False;self.session['originals'].append(str(path));self.session['status']='capturing';self.save_session()
        self.update_shot_label()
        if len(self.session['originals'])>=self.template['shots']:
            self.begin_processing();return
        self.capture_note.config(text='已收到照片，换个姿势吧。')
        if self.auto_sequence:
            self.timer=self.root.after(850,self.start_countdown)
        else:
            for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='normal')

    def capture_failed(self,error):
        self.capture_busy=False;self.auto_sequence=False;self.state='capture'
        self.session['status']='capture_error';self.session['error']=str(error);self.save_session()
        self.capture_note.config(text=str(error),wraplength=max(600,self.content.winfo_width()-20),fg=RED)
        for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='normal')

    def begin_processing(self):
        self.clear('processing');self.session['status']='processing';self.save_session()
        self.label(self.content,'请稍等，正在制作你的照片',28).pack(pady=(100,25))
        self.progress_text=self.label(self.content,'正在准备美颜',18,MUTED);self.progress_text.pack(pady=20)
        self.progressbar=ttk.Progressbar(self.content,mode='indeterminate',length=400);self.progressbar.pack(pady=20);self.progressbar.start(20)
        self.label(self.content,'完成后可选择打印或重新拍摄',14,MUTED).pack(pady=25)
        originals=list(self.session['originals']);folder=self.session_dir;template=self.template;config=dict(self.config)
        token=self.ui_token
        def work():
            return process_session(ROOT,folder,template,originals,config,lambda text:self.messages.put(('progress',token,text)))
        self.background(work,self.processing_done,self.processing_failed)

    def processing_done(self,result):
        self.progressbar.stop();final,notes=result
        self.session['final']=final;self.session['beauty']=notes;self.session['beauty_enabled']=True;self.session['status']='preview';self.save_session();self.show_preview()

    def processing_failed(self,error):
        self.progressbar.stop();self.clear('process_error');self.session['status']='processing_error';self.session['error']=str(error);self.save_session()
        self.label(self.content,'照片已保存，处理未完成',26).pack(pady=(70,25))
        label=self.label(self.content,str(error),16,RED);label.config(wraplength=900);label.pack(pady=20)
        self.button(self.content,'重试处理',self.begin_processing).pack(pady=10)
        self.button(self.content,'重新拍摄',self.retake,False).pack(pady=10)
        self.button(self.content,'返回模板选择',self.show_templates,False).pack(pady=10)

    def show_preview(self):
        # Recover older, unprinted sessions by building both variants from their originals.
        if not all((self.session_dir/name).is_file() for name in ('print_original.jpg','print_beauty.jpg')) and len(self.session.get('originals',[]))==self.template.get('shots'):
            self.begin_processing();return
        self.clear('preview')
        heading=tk.Frame(self.content,bg=BG);heading.pack(fill='x')
        self.beauty_btn=self.button(heading,'',self.toggle_beauty,False);self.beauty_btn.pack(side='right')
        self.preview_title=self.label(heading,self.config.get('preview_title','拍照免费，删一张50（本标题可编辑）'),27);self.preview_title.config(justify='left',wraplength=max(240,self.content.winfo_width()-round(420*self.ui_scale)));self.preview_title.pack(side='left',fill='x',expand=True)
        self.label(self.content,'点击美颜开关对比效果，打印当前显示的版本。',14,MUTED).pack(anchor='w',pady=(5,10))
        row=tk.Frame(self.content,bg=BG);row.pack(side='bottom',fill='x')
        self.print_btn=self.button(row,'模拟完成 · 返回首页' if self.demo else '打印这张照片',self.print_current)
        self.print_btn.pack(side='left',fill='x',expand=True,padx=(0,10))
        self.retake_btn=self.button(row,'重新拍摄',self.retake,False);self.retake_btn.pack(side='left',fill='x',expand=True,padx=(10,0))
        self.preview_note=self.label(self.content,'模拟模式：不会调用打印机' if self.demo else '每次只打印 1 张，原片和两个版本均保存在本机。',12,MUTED);self.preview_note.pack(side='bottom',pady=8)
        self.preview_picture=tk.Canvas(self.content,bg=BG,highlightthickness=0,width=1,height=1);self.preview_picture.pack(fill='both',expand=True)
        self.preview_picture.bind('<Configure>',self.schedule_layout)
        self.refresh_preview()

    def toggle_beauty(self):
        if self.state!='preview' or self.session['print_state']!='not_requested':return
        enabled=not self.session.get('beauty_enabled',True)
        path=self.session_dir/('print_beauty.jpg' if enabled else 'print_original.jpg')
        if not path.is_file():
            messagebox.showwarning('对比照片缺失','请重新处理这组照片后再切换。',parent=self.root);return
        self.session['beauty_enabled']=enabled;self.session['final']=str(path)
        self.save_session();self.refresh_preview()

    def refresh_preview(self):
        enabled=self.session.get('beauty_enabled',True)
        self.beauty_btn.config(text='● 自动美颜：开  ·  点击看原片' if enabled else '○ 自动美颜：关  ·  点击开启',bg='#267554' if enabled else '#E7DDD0',fg='white' if enabled else INK)
        with Image.open(self.session['final']) as source:self.preview_source=source.copy()
        self.schedule_layout()

    def retake(self):
        self.session['status']='retaken';self.save_session();self.select_template(self.template)

    def restore_latest_preview(self):
        base=ROOT/'data'/('demo' if self.demo else 'sessions')
        for folder in sorted(base.glob('*'),reverse=True):
            try:
                saved=json.loads((folder/'session.json').read_text(encoding='utf-8'))
                if saved.get('status') not in ('preview','print_error') or saved.get('print_state')!='not_requested':continue
                final=Path(saved['final']).resolve()
                if final.parent!=folder.resolve() or not final.is_file():continue
                template=saved.get('template_snapshot') or next(t for t in self.templates+getattr(self,'config',{}).get('archived_templates',[]) if t['id']==saved['template'])
                self.session=saved;self.session_dir=folder;self.template=template
                self.session['status']='preview';self.save_session();self.show_preview();return
            except (OSError,ValueError,KeyError,StopIteration):continue
        messagebox.showinfo('没有待打印成片','没有找到尚未提交打印的成片。已提交或状态不明的任务不会再次开放打印。',parent=self.root)

    def print_current(self):
        if self.state!='preview' or self.session['print_state']!='not_requested':return
        if self.demo:
            self.session['status']='demo_completed';self.save_session();self.show_welcome();return
        self.state='printing';self.print_btn.config(state='disabled');self.retake_btn.config(state='disabled')
        self.beauty_btn.config(state='disabled')
        self.preview_note.config(text='正在提交打印，请勿重复点击。',fg=RED)
        self.session['print_state']='submitting';self.save_session()
        path=self.session['final'];name=self.config['printer'];session_id=self.session['id']
        def run_print():
            return printer.print_photo(path,name,'PhotoBooth '+session_id,
                on_job=lambda job:self.messages.put(('print_job',self.ui_token,job)))
        self.background(run_print,self.print_done,self.print_failed)

    def print_done(self,result):
        self.session['print_state']='submitted';self.session['print_result']=result;self.session['status']='completed';self.save_session()
        self.clear('printed');self.label(self.content,'照片已提交打印',30).pack(pady=(110,30))
        self.label(self.content,'请在打印机处领取照片，正在返回欢迎页…',19,MUTED).pack(pady=20)
        self.label(self.content,f"打印任务号：{result['job_id']}",13,MUTED).pack(pady=10)
        self.timer=self.root.after(4000,self.show_welcome)

    def print_failed(self,error):
        # If StartDoc was reached, retry is deliberately gated to prevent duplicates.
        uncertain='print_job_id' in self.session
        self.session['print_state']='uncertain' if uncertain else 'not_requested'
        self.session['status']='print_error';self.session['print_error']=str(error);self.save_session()
        self.state='preview';self.preview_note.config(text=str(error),wraplength=1000,fg=RED)
        if not uncertain:
            self.print_btn.config(state='normal',text='检查打印机后重试打印');self.beauty_btn.config(state='normal')
        else:self.print_btn.config(state='disabled',text='请工作人员检查打印任务')
        self.retake_btn.config(state='normal',text='保留照片 · 返回首页',command=self.show_welcome)

    def background(self,work,success,failure):
        token=self.ui_token
        def runner():
            try:result=work();self.messages.put(('callback',token,(success,result)))
            except Exception as exc:
                logging.exception('Background operation failed');self.messages.put(('callback',token,(failure,exc)))
        threading.Thread(target=runner,daemon=True).start()

    def watch_future(self,future,success,failure):
        token=self.ui_token
        def complete(f):
            try:result=f.result();self.messages.put(('callback',token,(success,result)))
            except Exception as exc:self.messages.put(('callback',token,(failure,exc)))
        future.add_done_callback(complete)

    def connect_camera(self):
        if self.demo or self.capture_busy or self.focus_busy or self.state in ('processing','printing','countdown'):return
        self.top_status.config(text='正在连接相机…')
        self.watch_future(self.camera.request('connect'),self.camera_ready,self.camera_failed)

    def camera_ready(self,name):
        self.top_status.config(text=name+' · 已连接')
        self.focus_result=None;self.focus_target=None
        if self.state=='capture':self.capture_note.config(text='相机已连接，准备好后开始拍摄。',fg=MUTED)

    def camera_failed(self,error):
        self.top_status.config(text='相机未连接 · 唤醒后重连')
        if self.state=='capture':self.capture_note.config(text=str(error),fg=RED,wraplength=1000)

    def render_live(self,im,meta):
        current=min(len(self.session['originals']),self.template['shots']-1)
        slot=next(s for s in self.template['slots'] if s['photo']==current)
        panel=self.live_panel;w=max(20,panel.winfo_width());h=max(20,panel.winfo_height())
        transform=PreviewTransform(im.size,(w,h),slot['box'][2]/slot['box'][3],self.config.get('mirror_preview',True),meta.get('coord'),meta.get('visible_rect'),slot.get('centering',(.5,.42)),slot.get('zoom',1))
        self.live_transform=transform
        dx,dy,dw,dh=transform.display
        view=im.resize((dw,dh),Image.Resampling.LANCZOS,box=transform.crop)
        if self.config.get('mirror_preview',True):view=ImageOps.mirror(view)
        view=view.filter(ImageFilter.UnsharpMask(radius=.7,percent=55,threshold=3))
        panel.delete('all');panel.image=ImageTk.PhotoImage(view)
        panel.create_image(dx,dy,image=panel.image,anchor='nw')
        faces=self.face_boxes if time.monotonic()-self.face_time<1.2 else []
        vx,vy,vw,vh=transform.visible;cw,ch=transform.coord
        for x,y,fw,fh in faces:
            rect=transform.box_on_canvas(((vx+x*vw)/cw,(vy+y*vh)/ch,fw*vw/cw,fh*vh/ch))
            if rect:
                panel.create_rectangle(*rect,outline='#FFFFFF',width=2)
                panel.create_text(rect[0]+4,rect[1]+4,anchor='nw',text='人脸',fill='white',font=('Microsoft YaHei UI',10))
        for point in meta.get('focus_points',[])[:12]:
            rect=transform.box_on_canvas(point['box'])
            if rect:panel.create_rectangle(*rect,outline='#FFFFFF',width=2)
        text=f'检测到 {len(faces)} 张人脸 · 点按对焦' if faces else '点按人物选择对焦位置';color='white'
        if self.focus_target:
            x,y=self.focus_target;result=self.focus_result or {}
            state=result.get('state') if time.monotonic()<result.get('expires',float('inf')) else None
            color='#5DE5A7' if state=='focused' else '#FFA075' if state in ('failed','unconfirmed') else '#FFE079'
            box=result.get('box',(x-.036,y-.054,.072,.108))
            rect=transform.box_on_canvas(box)
            if rect:panel.create_rectangle(*rect,outline=color,width=3)
            text='正在对焦…' if self.focus_busy else '已合焦' if state=='focused' else '未确认合焦，请再点按' if state in ('failed','unconfirmed') else '已选择对焦位置'
        panel.create_rectangle(14,12,round(300*self.ui_scale),round(44*self.ui_scale),fill='#242424',outline='')
        panel.create_text(24,round(27*self.ui_scale),anchor='w',text=text,fill=color,font=('Microsoft YaHei UI',12,'bold'))
        panel.create_text(w-16,h-16,anchor='se',text='模拟画面' if self.demo else f'实时取景  {im.width} × {im.height}',fill='white',font=('Microsoft YaHei UI',10))

    def faces_detected(self,boxes):
        self.face_busy=False;self.face_boxes=boxes;self.face_time=time.monotonic()

    def face_detection_failed(self,error):
        self.face_busy=False;self.face_boxes=[]

    def tick(self):
        if self.closing:return
        while True:
            try:kind,token,payload=self.messages.get_nowait()
            except queue.Empty:break
            if token!=self.ui_token:continue
            if kind=='callback':callback,arg=payload;callback(arg)
            elif kind=='progress' and self.state=='processing':self.progress_text.config(text=payload)
            elif kind=='print_job' and self.session:
                self.session['print_job_id']=payload;self.save_session()
        self.tick_count+=1
        if self.tick_count%10==0:
            self.top_status.config(text='模拟测试 · 禁止实际打印' if self.demo else
                (self.camera.name+' · 已连接' if self.camera.connected else '相机未连接 · F10 检查设置'))
        if self.state in ('capture','countdown'):
            frame=None if self.demo else self.camera.latest_frame()
            key=(frame[0] if frame else 'demo',self.live_panel.winfo_width(),self.live_panel.winfo_height(),self.focus_target)
            if self.demo and key!=self.render_key:
                self.render_live(sample_photo(0),{});self.render_key=key
            if frame and time.monotonic()-frame[0]<3 and not self.capture_busy:
                try:
                    if key!=self.render_key:
                        with Image.open(io.BytesIO(frame[1])) as source:im=source.convert('RGB')
                        if not self.face_busy and time.monotonic()>self.face_at:
                            self.face_busy=True;self.face_at=time.monotonic()+.5
                            self.watch_future(self.face_worker.submit(detect_preview_faces,im.copy()),self.faces_detected,self.face_detection_failed)
                        self.render_live(im,frame[2] if len(frame)>2 else {});self.render_key=key
                except Exception:logging.exception('Live preview failed')
            elif not self.capture_busy and not self.demo:
                self.live_panel.delete('all');self.live_panel.image=None;self.live_transform=None
                self.live_panel.create_text(self.live_panel.winfo_width()/2,self.live_panel.winfo_height()/2,text='相机画面未就绪，请工作人员检查连接。',fill='white',font=('Microsoft YaHei UI',17))
        self.root.after(80,self.tick)

    def settings(self):
        if self.state not in ('welcome','templates','capture','preview','process_error'):return
        if self.capture_busy or self.focus_busy:return
        from library_ui import fit_window
        window=tk.Toplevel(self.root);window.title('美颜自拍亭 · 工作人员设置');fit_window(window,self.root,850,920)
        self.label(window,'工作人员设置',23).pack(pady=12)
        self.label(window,'打印机（固定使用 DNP DS620，6×4 英寸相纸）',13).pack(anchor='w',padx=30)
        selected=tk.StringVar(value=self.config['printer']);combo=ttk.Combobox(window,textvariable=selected,values=printer.queues(),state='readonly',width=60);combo.pack(padx=30,pady=10,fill='x')
        sdk_row=tk.Frame(window,bg=BG);sdk_row.pack(fill='x',padx=30,pady=5)
        sdk_name=tk.StringVar(value=Path(self.config['sdk_path']).name if self.config['sdk_path'] else '尚未设置 EDSDK.dll')
        tk.Label(sdk_row,textvariable=sdk_name,bg=BG,fg=MUTED,font=('Microsoft YaHei UI',11)).pack(side='left')
        def choose_sdk():
            path=filedialog.askopenfilename(parent=window,title='选择已安装的佳能 64 位 EDSDK.dll',filetypes=[('Canon EDSDK','EDSDK.dll')])
            if not path:return
            if Path(path).name.lower()!='edsdk.dll':messagebox.showwarning('接口文件','请选择 EDSDK.dll。',parent=window);return
            self.config['sdk_path']=path;self.config['camera_backend']='canon';atomic_json(ROOT/'config.json',self.config);sdk_name.set('已设置：EDSDK.dll')
            if not self.demo:self.watch_future(self.camera.request('configure',sdk_path=path),self.camera_ready,self.camera_failed)
        tk.Button(sdk_row,text='选择相机接口文件',command=choose_sdk,font=('Microsoft YaHei UI',11),padx=10,pady=6).pack(side='right')
        tk.Button(window,text='相机自动检测 / 连接方式 / 驱动',command=lambda:(window.destroy(),self.manage_cameras()),font=('Microsoft YaHei UI',12),padx=14,pady=8).pack(pady=6)
        self.label(window,'自动美颜：提亮、磨皮、大眼、瘦脸、去油光、淡皱和轻微瘦身。\n成片页点开关对比；使用本机处理，照片不会上传。',12).pack(anchor='w',padx=30,pady=(10,0))
        mirror=tk.BooleanVar(value=self.config.get('mirror_preview',True));tk.Checkbutton(window,text='实时取景镜像（保存与打印保持相机方向）',variable=mirror,bg=BG,font=('Microsoft YaHei UI',12)).pack(anchor='w',padx=30,pady=10)
        row=tk.Frame(window,bg=BG);row.pack(pady=16)
        def save():
            try:printer.inspect(selected.get())
            except Exception as exc:messagebox.showwarning('打印机检查',str(exc),parent=window);return
            self.config['printer']=selected.get();self.config['mirror_preview']=mirror.get();atomic_json(ROOT/'config.json',self.config);window.destroy()
        self.button(row,'保存设置',save).pack(side='left',padx=8)
        self.button(row,'重连相机',self.connect_camera,False).pack(side='left',padx=8)
        self.button(window,'Logo / 标题 / 封面 / 模板管理',lambda:(window.destroy(),self.manage_library()),False).pack(pady=5)
        self.button(window,'打开本机照片目录',lambda:os.startfile(ROOT/'data'),False).pack(pady=5)
        if self.state in ('welcome','templates'):
            self.button(window,'恢复最近未打印成片',lambda:(window.destroy(),self.restore_latest_preview()),False).pack(pady=5)
        tk.Button(window,text='退出自拍亭',command=lambda:(window.destroy(),self.close()),bg=BG,fg=MUTED,relief='flat',font=('Microsoft YaHei UI',12)).pack(pady=8)
        tk.Label(window,text='作者：爱打游戏的Arweil  ·  QQ:759377831\n本项目由 Codex 制作；作者不懂代码，欢迎大佬交流。',font=('Microsoft YaHei UI',10),bg=BG,fg=MUTED).pack(pady=6)

    def manage_cameras(self):
        from camera_ui import CameraWindow
        CameraWindow(self.root,self.config,self.apply_camera_settings,self.camera)

    def apply_camera_settings(self,settings):
        self.config.update(settings);atomic_json(ROOT/'config.json',self.config)
        if not self.demo:self.watch_future(self.camera.request('select',**settings),self.camera_ready,self.camera_failed)

    def manage_library(self):
        from library_ui import LibraryWindow
        LibraryWindow(self.root,ROOT,self.config,self.library_saved)

    def library_saved(self,config):
        self.config=config;self.templates=config['templates'];self.refresh_brand()
        if self.state=='welcome':self.show_welcome()
        elif self.state=='templates':self.show_templates()
        elif self.state=='preview':self.show_preview()

    def toggle_fullscreen(self):
        self.fullscreen=not self.fullscreen;self.root.attributes('-fullscreen',self.fullscreen)

    def escape(self):
        if self.fullscreen:self.toggle_fullscreen()
        elif self.state=='countdown':
            self.cancel_timer();self.state='capture';self.auto_sequence=False;self.count_label.place_forget()
            for control in (self.auto_btn,self.manual_btn,self.back_btn,self.reconnect_btn,self.focus_btn):control.config(state='normal')
            self.capture_note.config(text='倒计时已取消，可以继续拍摄。')

    def close(self):
        if self.state in ('processing','printing') or self.capture_busy:
            messagebox.showinfo('请稍候','当前照片正在处理或提交，请完成后再退出。',parent=self.root);return
        self.closing=True;self.cancel_timer()
        if self.layout_job is not None:self.root.after_cancel(self.layout_job);self.layout_job=None
        self.face_worker.shutdown(wait=False,cancel_futures=True)
        if self.camera:
            self.camera.request('close');self.camera.join(timeout=4)
        self.root.destroy()


def main():
    args=argparse.ArgumentParser();args.add_argument('--demo',action='store_true');args.add_argument('--fullscreen',action='store_true');args.add_argument('--repair-camera-drivers',action='store_true');options=args.parse_args()
    if options.repair_camera_drivers:
        from camera_ui import DriverRepairWindow
        root=tk.Tk();DriverRepairWindow(root);root.mainloop();return
    (ROOT/'data').mkdir(exist_ok=True);logging.basicConfig(filename=ROOT/'data'/'booth.log',level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')
    # One live instance owns the camera and printer; demo uses a different mutex.
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];kernel.CreateMutexW.restype=ctypes.c_void_p
    mutex=kernel.CreateMutexW(None,False,'Local\\PhotoBoothDemo' if options.demo else 'Local\\PhotoBoothLive')
    if ctypes.get_last_error()==183:
        root=tk.Tk();root.withdraw();messagebox.showinfo('自拍亭已运行','软件已经打开，请切换到现有窗口。');root.destroy();return
    try:
        try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:pass
        root=tk.Tk();Booth(root,options.demo,options.fullscreen);root.mainloop()
    finally:
        kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(mutex)


if __name__=='__main__':main()

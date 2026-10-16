"""Staff-only branding, cover library and batch template editor."""
import copy
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from PIL import Image,ImageTk,ImageDraw
import asset_library as library
from template_geometry import move_box,resize_box
from processing import compose_on_overlay,sample_photo

BG='#F4EEE6';INK='#342D29';ACCENT='#075394'
TYPES=[('图片','*.png *.jpg *.jpeg *.webp *.bmp'),('所有文件','*.*')]


def button(parent,text,command):
    return tk.Button(parent,text=text,command=command,font=('Microsoft YaHei UI',12),padx=12,pady=9,bg='#E3EBEF',fg=INK,relief='flat',cursor='hand2')


def fit_window(window,parent,width,height):
    scale=max(1,float(parent.tk.call('tk','scaling'))/(96/72))
    w=min(round(width*scale),parent.winfo_screenwidth()-50);h=min(round(height*scale),parent.winfo_screenheight()-70)
    x=max(0,(parent.winfo_screenwidth()-w)//2);y=max(0,(parent.winfo_screenheight()-h)//2)
    window.geometry(f'{w}x{h}+{x}+{y}');window.configure(bg=BG);window.transient(parent)
    window.grab_set()


def staff_styles(parent):
    scale=max(1,float(parent.tk.call('tk','scaling'))/(96/72))
    style=ttk.Style(parent)
    style.configure('Asset.Treeview',font=('Microsoft YaHei UI',12),rowheight=round(32*scale))
    style.configure('Asset.Treeview.Heading',font=('Microsoft YaHei UI',12,'bold'))
    style.configure('Asset.TNotebook.Tab',font=('Microsoft YaHei UI',12),padding=(12,8))


class LibraryWindow:
    def __init__(self,parent,root,config,on_save):
        self.root=root;self.config=library.migrate_config(copy.deepcopy(config));self.on_save=on_save
        self.window=tk.Toplevel(parent);self.window.title('Logo、标题、封面与模板管理');fit_window(self.window,parent,1100,820)
        staff_styles(self.window)
        self.window.protocol('WM_DELETE_WINDOW',self.cancel);self.busy=False;self.tasks=queue.Queue();self.trees={};self.pictures={}
        bottom=tk.Frame(self.window,bg=BG);bottom.pack(side='bottom',fill='x',padx=20,pady=12)
        self.status=tk.Label(bottom,text='修改后点击“保存并应用”。删除的素材可恢复，原始文件保留。',bg=BG,fg='#6E625A',anchor='w');self.status.pack(side='left',fill='x',expand=True)
        self.save_btn=button(bottom,'保存并应用',self.save);self.save_btn.pack(side='right',padx=6)
        button(bottom,'取消',self.cancel).pack(side='right',padx=6)
        book=ttk.Notebook(self.window,style='Asset.TNotebook');book.pack(fill='both',expand=True,padx=20,pady=18)
        covers=tk.Frame(book,bg=BG);templates=tk.Frame(book,bg=BG);trash=tk.Frame(book,bg=BG)
        book.add(covers,text=' Logo / 标题 / 封面 ');book.add(templates,text=' 拍照模板 · 支持批量 ');book.add(trash,text=' 已删除 / 旧版本 ')
        logo_row=tk.Frame(covers,bg=BG);logo_row.pack(fill='x',pady=(4,8))
        self.logo_preview=tk.Label(logo_row,bg=BG,width=210);self.logo_preview.pack(side='left',padx=(0,16))
        button(logo_row,'替换 Logo',self.choose_logo).pack(side='left',padx=6)
        button(logo_row,'使用默认 logo',self.reset_logo).pack(side='left',padx=6)
        self.refresh_logo()
        title_row=tk.Frame(covers,bg=BG);title_row.pack(fill='x',pady=(8,14))
        tk.Label(title_row,text='成片预览标题',font=('Microsoft YaHei UI',13),bg=BG).pack(side='left',padx=(0,12))
        self.title=tk.StringVar(value=self.config['preview_title']);tk.Entry(title_row,textvariable=self.title,font=('Microsoft YaHei UI',17)).pack(side='left',fill='x',expand=True)
        tk.Label(covers,text='标题 1–40 个字符。Logo 支持透明 PNG；保存后立即应用，取消则保留原设置。',bg=BG,fg='#796E64',anchor='w').pack(fill='x',pady=(0,8))
        self.make_list(covers,'covers');self.make_list(templates,'templates')
        tk.Label(trash,text='删除和替换只移出顾客可选列表；旧素材继续供历史成片使用。',bg=BG,fg='#796E64',font=('Microsoft YaHei UI',12)).pack(anchor='w',pady=12)
        self.trash=ttk.Treeview(trash,columns=('kind','name'),show='headings',selectmode='extended',style='Asset.Treeview')
        self.trash.heading('kind',text='类型');self.trash.heading('name',text='名称');self.trash.column('kind',width=160,stretch=False);self.trash.pack(fill='both',expand=True)
        button(trash,'恢复所选素材',self.restore).pack(anchor='e',pady=12)
        self.refresh();self.window.after(100,self.poll)

    def make_list(self,parent,kind):
        bar=tk.Frame(parent,bg=BG);bar.pack(fill='x',pady=8)
        noun='模板' if kind=='templates' else '封面'
        button(bar,'批量添加'+noun,lambda:self.add(kind)).pack(side='left',padx=(0,6))
        button(bar,'替换所选'+noun,lambda:self.replace(kind)).pack(side='left',padx=6)
        if kind=='templates':button(bar,'编辑照片框 / 名称',self.edit_template).pack(side='left',padx=6)
        else:button(bar,'设为欢迎封面',self.activate_cover).pack(side='left',padx=6)
        button(bar,'删除所选',lambda:self.remove(kind)).pack(side='left',padx=6)
        body=tk.Frame(parent,bg=BG);body.pack(fill='both',expand=True)
        left=tk.Frame(body,bg=BG);left.pack(side='left',fill='both',expand=True)
        tree=ttk.Treeview(left,columns=('name','status'),show='headings',selectmode='extended',height=12,style='Asset.Treeview')
        tree.heading('name',text=noun+'名称');tree.heading('status',text='状态 / 张数')
        tree.column('name',width=260);tree.column('status',width=175)
        scroll=ttk.Scrollbar(left,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True);self.trees[kind]=tree
        tree.bind('<<TreeviewSelect>>',lambda e:self.preview(kind));tree.bind('<Double-1>',lambda e:self.edit_template() if kind=='templates' else self.activate_cover())
        picture=tk.Label(body,bg='#DDD8D0',width=32);picture.pack(side='right',fill='both',padx=(18,0));self.pictures[kind]=picture
        foot=tk.Frame(parent,bg=BG);foot.pack(fill='x',pady=10)
        button(foot,'全选',lambda:tree.selection_set(tree.get_children())).pack(side='left')
        button(foot,'取消选择',lambda:tree.selection_remove(tree.selection())).pack(side='left',padx=6)
        if kind=='templates':
            button(foot,'上移',lambda:self.move(-1)).pack(side='right',padx=6);button(foot,'下移',lambda:self.move(1)).pack(side='right')
        text='自动识别透明框及独立浅色空白框；可编辑位置、大小与照片内容。Ctrl / Shift 可多选。' if kind=='templates' else '支持 JPG、PNG、WebP、BMP。Ctrl / Shift 可多选后批量删除。'
        tk.Label(parent,text=text,bg=BG,fg='#796E64',font=('Microsoft YaHei UI',11),anchor='w').pack(fill='x',pady=6)

    def refresh_logo(self):
        try:
            with Image.open(library.asset_path(self.root,self.config['brand_logo'])) as source:im=source.convert('RGBA')
            im.thumbnail((210,56),Image.Resampling.LANCZOS)
            photo=ImageTk.PhotoImage(im);self.logo_preview.config(image=photo,text='',width=210,height=60);self.logo_preview.image=photo
        except (OSError,ValueError):self.logo_preview.config(image='',text='Logo 无法打开',width=25,height=3);self.logo_preview.image=None

    def choose_logo(self):
        if self.busy:return
        path=filedialog.askopenfilename(parent=self.window,title='选择 Logo（推荐透明 PNG）',filetypes=TYPES)
        if not path:return
        self.busy=True;self.save_btn.config(state='disabled');self.status.config(text='正在导入 Logo，请稍候…')
        def work():
            try:self.tasks.put(('logo',library.import_logo(self.root,path)))
            except Exception as exc:self.tasks.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def reset_logo(self):
        if self.busy:return
        self.config['brand_logo']='logo-placeholder.png';self.refresh_logo();self.status.config(text='已选择默认 logo，点击“保存并应用”生效。')

    def selected(self,kind,one=False):
        ids=self.trees[kind].selection()
        if not ids or (one and len(ids)!=1):
            messagebox.showinfo('选择素材','请先选中一项素材。' if one else '请先选中需要操作的素材。',parent=self.window);return []
        return [item for item in self.config[kind] if item['id'] in ids]

    def refresh(self,selection=None):
        for kind,tree in self.trees.items():
            chosen=tree.selection();tree.delete(*tree.get_children())
            for item in self.config[kind]:
                status=('当前欢迎封面' if item['id']==self.config.get('active_cover') else '可使用') if kind=='covers' else (f"拍 {item['shots']} 张 / {len(item['slots'])} 框" if library.template_ready(item) else '待设置照片框 · 未上架')
                tree.insert('','end',iid=item['id'],values=(item['name'],status))
            keep=[i for i in (selection or chosen) if tree.exists(i)]
            if keep:tree.selection_set(keep);tree.see(keep[0])
            self.preview(kind)
        self.trash.delete(*self.trash.get_children())
        for kind in ('templates','covers'):
            for item in self.config['archived_'+kind]:
                iid=kind+':'+item['id']
                if not self.trash.exists(iid):self.trash.insert('','end',iid=iid,values=('模板 / 旧版本' if kind=='templates' else '封面 / 旧版本',item['name']))

    def preview(self,kind):
        tree=self.trees[kind];ids=tree.selection();widget=self.pictures[kind]
        if not ids:widget.config(image='',text='选择素材查看预览');widget.image=None;return
        item=next((x for x in self.config[kind] if x['id']==ids[0]),None)
        if not item:return
        try:
            with Image.open(library.asset_path(self.root,item['thumbnail' if kind=='templates' else 'image'])) as source:im=source.convert('RGB')
            im.thumbnail((350,410),Image.Resampling.LANCZOS);photo=ImageTk.PhotoImage(im);widget.config(image=photo,text='',width=350);widget.image=photo
        except OSError:widget.config(image='',text='预览文件无法打开');widget.image=None

    def add(self,kind):
        if self.busy:return
        paths=filedialog.askopenfilenames(parent=self.window,title='可一次选择多张图片 · Ctrl / Shift 多选',filetypes=TYPES)
        if paths:self.run_import(kind,paths)

    def replace(self,kind):
        if self.busy:return
        selected=self.selected(kind,True)
        if not selected:return
        path=filedialog.askopenfilename(parent=self.window,title='选择替换图片',filetypes=TYPES)
        if path:self.run_import(kind,[path],selected[0]['id'])

    def run_import(self,kind,paths,replacement=None):
        self.busy=True;self.save_btn.config(state='disabled');self.status.config(text=f'正在导入 {len(paths)} 张图片，请稍候…')
        def work():
            try:self.tasks.put(('import',kind,replacement,library.import_batch(self.root,paths,kind)))
            except Exception as exc:self.tasks.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        if not self.window.winfo_exists():return
        try:result=self.tasks.get_nowait()
        except queue.Empty:result=None
        if result:
            self.busy=False;self.save_btn.config(state='normal')
            if result[0]=='error':messagebox.showerror('导入失败',result[1],parent=self.window)
            elif result[0]=='logo':
                self.config['brand_logo']=result[1];self.refresh_logo();self.status.config(text='Logo 已导入，点击“保存并应用”生效。')
            else:
                _,kind,replacement,(items,errors)=result
                if replacement and items:library.replace_item(self.config,kind,replacement,items[0])
                else:self.config[kind].extend(items)
                if kind=='covers' and items and not self.config.get('active_cover'):self.config['active_cover']=items[0]['id']
                self.refresh([item['id'] for item in items])
                pending=sum(not library.template_ready(item) for item in items) if kind=='templates' else 0
                text=f'已导入 {len(items)} 项。'+(f'其中 {pending} 项需编辑照片框后才能上架。' if pending else '')
                self.status.config(text=text+' 点击“保存并应用”生效。')
                if errors:messagebox.showwarning('部分图片未导入',text+'\n\n'+'\n'.join(errors[:12]),parent=self.window)
        self.window.after(100,self.poll)

    def remove(self,kind):
        if self.busy:return
        items=self.selected(kind)
        if not items:return
        library.remove_items(self.config,kind,[item['id'] for item in items]);self.refresh()
        self.status.config(text=f'已移出 {len(items)} 项，可在“已删除 / 旧版本”恢复；保存后生效。')

    def activate_cover(self):
        if self.busy:return
        selected=self.selected('covers',True)
        if selected:self.config['active_cover']=selected[0]['id'];self.refresh();self.status.config(text='欢迎封面已选择，保存后生效。')

    def restore(self):
        if self.busy:return
        chosen=self.trash.selection()
        for kind in ('templates','covers'):library.restore_items(self.config,kind,[item.split(':',1)[1] for item in chosen if item.startswith(kind+':')])
        self.refresh();self.status.config(text='所选素材已恢复，保存后生效。')

    def move(self,direction):
        if self.busy:return
        selected=self.selected('templates',True)
        if not selected:return
        ident=selected[0]['id'];items=self.config['templates'];index=next(i for i,t in enumerate(items) if t['id']==ident);other=index+direction
        if 0<=other<len(items):items[index],items[other]=items[other],items[index];self.refresh([ident])

    def edit_template(self):
        if self.busy:return
        selected=self.selected('templates',True)
        if not selected:return
        original=selected[0]
        def accept(revised):
            library.replace_item(self.config,'templates',original['id'],revised);self.refresh([revised['id']]);self.status.config(text='照片框已更新，保存后生效。')
        TemplateEditor(self.window,self.root,original,accept)

    def save(self):
        if self.busy:return
        self.config['preview_title']=self.title.get()
        try:config=library.save_library(self.root,self.config)
        except (ValueError,OSError) as exc:messagebox.showwarning('尚未保存',str(exc),parent=self.window);return
        self.window.destroy();self.on_save(config)

    def cancel(self):
        if self.busy:
            self.status.config(text='正在导入，请完成后再关闭。');return
        self.window.destroy()


class TemplateEditor:
    def __init__(self,parent,root,template,on_save):
        self.root=root;self.template=copy.deepcopy(template);self.slots=copy.deepcopy(template['slots']);self.on_save=on_save;self.parent=parent
        self.window=tk.Toplevel(parent);self.window.title('模板编辑 · 移动、缩放与照片位置');fit_window(self.window,parent,1160,860)
        staff_styles(self.window)
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.drag_start=None;self.drag_action=None;self.scale=1;self.offset=(0,0);self.history=[];self.drag_original=None
        with Image.open(library.asset_path(root,template.get('source_overlay',template['overlay']))) as source:self.overlay=source.convert('RGBA')
        try:self.original_windows=library.detect_template_slots(self.overlay)[0]
        except ValueError:self.original_windows=[]
        self.samples=[sample_photo(i) for i in range(16)];self.preview_key=None
        header=tk.Frame(self.window,bg=BG);header.pack(fill='x',padx=16,pady=10)
        tk.Label(header,text='模板名称',bg=BG,font=('Microsoft YaHei UI',12)).pack(side='left')
        self.name=tk.StringVar(value=template['name']);tk.Entry(header,textvariable=self.name,font=('Microsoft YaHei UI',15)).pack(side='left',fill='x',expand=True,padx=12)
        footer=tk.Frame(self.window,bg=BG);footer.pack(side='bottom',fill='x',padx=16,pady=12)
        self.note=tk.Label(footer,text='空白处画框；框内拖动移动；右下角缩放。',bg=BG,fg='#796E64');self.note.pack(side='left')
        button(footer,'保存照片框',self.save).pack(side='right');button(footer,'取消',self.close).pack(side='right',padx=10)
        modes=tk.Frame(self.window,bg=BG);modes.pack(fill='x',padx=16,pady=2)
        self.drag_mode=tk.StringVar(value='frame')
        tk.Label(modes,text='拖动模式：',bg=BG,font=('Microsoft YaHei UI',12)).pack(side='left')
        for text,value in (('照片框：移动 / 缩放','frame'),('照片内容：调整取景位置','photo')):
            tk.Radiobutton(modes,text=text,variable=self.drag_mode,value=value,bg=BG,font=('Microsoft YaHei UI',12)).pack(side='left',padx=8)
        button(modes,'撤销',self.undo).pack(side='right')
        body=tk.Frame(self.window,bg=BG);body.pack(fill='both',expand=True,padx=16,pady=8)
        self.staff_scale=max(1,float(parent.tk.call('tk','scaling'))/(96/72))
        side=tk.Frame(body,bg=BG,width=round(315*self.staff_scale));side.pack(side='right',fill='y',padx=(15,0));side.pack_propagate(False)
        self.canvas=tk.Canvas(body,bg='#C9CFD1',highlightthickness=0,cursor='crosshair');self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw());self.canvas.bind('<ButtonPress-1>',self.start_drag);self.canvas.bind('<B1-Motion>',self.drag);self.canvas.bind('<ButtonRelease-1>',self.end_drag)
        frame_list=tk.Frame(side,bg=BG);frame_list.pack(fill='x')
        self.tree=ttk.Treeview(frame_list,columns=('frame','photo'),show='headings',height=5,selectmode='browse',style='Asset.Treeview');self.tree.heading('frame',text='照片框');self.tree.heading('photo',text='使用照片');self.tree.column('frame',width=100);self.tree.column('photo',width=120)
        frame_scroll=ttk.Scrollbar(frame_list,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=frame_scroll.set);frame_scroll.pack(side='right',fill='y');self.tree.pack(fill='x',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.frame_selected)
        row=tk.Frame(side,bg=BG);row.pack(fill='x',pady=12)
        tk.Label(row,text='使用第',bg=BG).pack(side='left');self.number=tk.IntVar(value=1);ttk.Spinbox(row,from_=1,to=12,textvariable=self.number,width=5).pack(side='left',padx=6);tk.Label(row,text='张照片',bg=BG).pack(side='left')
        compact=tk.Frame(side,bg=BG);compact.pack(fill='x')
        button(compact,'应用编号',self.apply_number).pack(side='left',padx=(0,4));button(compact,'删除框',self.delete_frame).pack(side='left')
        self.box_info=tk.Label(side,text='',bg=BG,fg='#796E64',font=('Microsoft YaHei UI',10));self.box_info.pack(fill='x',pady=5)
        button(side,'精确调整位置 / 大小',self.edit_coordinates).pack(fill='x',pady=3)
        self.zoom=tk.DoubleVar(value=1)
        tk.Label(side,text='照片放大（选框后拖动内容可调位置）',bg=BG,font=('Microsoft YaHei UI',10),wraplength=round(290*self.staff_scale),justify='left').pack(anchor='w',pady=(8,0))
        self.zoom_scale=tk.Scale(side,from_=1,to=3,resolution=.05,orient='horizontal',variable=self.zoom,command=self.zoom_changed,bg=BG,highlightthickness=0);self.zoom_scale.pack(fill='x')
        button(side,'照片位置复位',self.reset_photo).pack(fill='x',pady=3)
        button(side,'自动识别照片框',self.detect).pack(fill='x',pady=3)
        button(side,'每个框使用不同照片',self.unique_photos).pack(fill='x',pady=3)
        button(side,'两列同组照片',self.repeat_columns).pack(fill='x',pady=3)
        self.refresh()

    def refresh(self):
        selected=self.tree.selection();self.tree.delete(*self.tree.get_children())
        for i,slot in enumerate(self.slots):self.tree.insert('','end',iid=str(i),values=(f'框 {i+1}',f"第 {slot['photo']+1} 张"))
        if selected and self.tree.exists(selected[0]):self.tree.selection_set(selected)
        shots=max((s['photo'] for s in self.slots),default=-1)+1
        self.note.config(text=f'{len(self.slots)} 个照片框 · 需拍 {shots} 张；右下角拖动缩放。')
        self.draw()

    def draw(self):
        if not self.canvas.winfo_exists():return
        width=max(1,self.canvas.winfo_width()-16);height=max(1,self.canvas.winfo_height()-16)
        self.scale=min(width/self.overlay.width,height/self.overlay.height)
        w=max(1,round(self.overlay.width*self.scale));h=max(1,round(self.overlay.height*self.scale))
        self.offset=((self.canvas.winfo_width()-w)/2,(self.canvas.winfo_height()-h)/2)
        key=(w,h,repr(self.slots))
        if key!=self.preview_key:
            def scaled(items):
                result=copy.deepcopy(items)
                for s in result:s['box']=[round(v*self.scale) for v in s['box']];s['box'][2:]=[max(1,v) for v in s['box'][2:]]
                return result
            slots=scaled(self.slots);old=scaled(self.original_windows)
            im=library.prepare_overlay(self.overlay.resize((w,h),Image.Resampling.LANCZOS),slots,old)
            back=compose_on_overlay({'slots':slots},im,self.samples)
            self.canvas.image=ImageTk.PhotoImage(back);self.preview_key=key
        self.canvas.delete('all');dx,dy=self.offset;self.canvas.create_image(dx,dy,image=self.canvas.image,anchor='nw')
        selected=self.tree.selection()
        for i,slot in enumerate(self.slots):
            x,y,bw,bh=slot['box'];x=dx+x*self.scale;y=dy+y*self.scale
            color='#D94A37' if selected and selected[0]==str(i) else '#075394'
            self.canvas.create_rectangle(x,y,x+bw*self.scale,y+bh*self.scale,outline=color,width=3)
            self.canvas.create_text(x+6,y+6,anchor='nw',text=f"框 {i+1} · 照片 {slot['photo']+1}",fill=color,font=('Microsoft YaHei UI',12,'bold'))
            if selected and selected[0]==str(i):self.canvas.create_rectangle(x+bw*self.scale-7,y+bh*self.scale-7,x+bw*self.scale+7,y+bh*self.scale+7,fill=color,outline='white')

    def image_point(self,x,y):
        dx,dy=self.offset
        return (max(0,min(self.overlay.width,round((x-dx)/self.scale))),max(0,min(self.overlay.height,round((y-dy)/self.scale))))

    def start_drag(self,event):
        dx,dy=self.offset
        if not dx<=event.x<=dx+self.overlay.width*self.scale or not dy<=event.y<=dy+self.overlay.height*self.scale:return
        self.drag_start=self.image_point(event.x,event.y)
        self.drag_original=copy.deepcopy(self.slots);self.drag_action='new';self.drag_index=None
        for i in reversed(range(len(self.slots))):
            x,y,w,h=self.slots[i]['box'];px,py=self.drag_start
            if x-8/self.scale<=px<=x+w+8/self.scale and y-8/self.scale<=py<=y+h+8/self.scale:
                self.drag_index=i;self.tree.selection_set(str(i));self.frame_selected()
                self.drag_action='photo' if self.drag_mode.get()=='photo' else 'resize' if abs(px-x-w)<12/self.scale and abs(py-y-h)<12/self.scale else 'move'
                break
        if self.drag_mode.get()=='photo' and self.drag_index is None:self.drag_start=None

    def drag(self,event):
        if self.drag_start is None:return
        if self.drag_action!='new':
            i=self.drag_index;slot=self.slots[i];old=self.drag_original[i];px,py=self.image_point(event.x,event.y);sx,sy=self.drag_start
            if self.drag_action=='move':slot['box']=move_box(old['box'],px-sx,py-sy,self.overlay.size);slot['cutout']=True
            elif self.drag_action=='resize':slot['box']=resize_box(old['box'],px,py,self.overlay.size);slot['cutout']=True
            else:
                cx,cy=old.get('centering',(.5,.42));slot['centering']=[max(0,min(1,cx-(px-sx)/old['box'][2])),max(0,min(1,cy-(py-sy)/old['box'][3]))]
            self.draw();return
        self.canvas.delete('drag');x,y=self.drag_start;ex,ey=self.image_point(event.x,event.y);dx,dy=self.offset
        self.canvas.create_rectangle(dx+x*self.scale,dy+y*self.scale,dx+ex*self.scale,dy+ey*self.scale,outline='#E08718',width=3,tags='drag')

    def end_drag(self,event):
        if self.drag_start is None:return
        if self.drag_action!='new':
            self.drag(event)
            if self.slots!=self.drag_original:self.history.append(self.drag_original)
            self.drag_start=None;self.refresh();return
        x,y=self.drag_start;ex,ey=self.image_point(event.x,event.y);self.drag_start=None
        box=[min(x,ex),min(y,ey),abs(ex-x),abs(ey-y)]
        if min(box[2:])<20:self.draw();return
        if len(self.slots)>=16:messagebox.showwarning('照片框数量','最多 16 个照片框。',parent=self.window);self.draw();return
        self.remember();self.slots.append({'box':box,'photo':min(11,max([s['photo'] for s in self.slots],default=-1)+1),'cutout':True});self.refresh();self.tree.selection_set(str(len(self.slots)-1));self.frame_selected()

    def frame_selected(self,event=None):
        selected=self.tree.selection()
        if selected:
            slot=self.slots[int(selected[0])];self.number.set(slot['photo']+1);self.zoom.set(slot.get('zoom',1));x,y,w,h=slot['box'];self.box_info.config(text=f'位置 {x}, {y}  ·  大小 {w} × {h}')
        self.draw()

    def apply_number(self):
        selected=self.tree.selection()
        try:number=self.number.get()
        except tk.TclError:number=0
        if not selected or not 1<=number<=12:return
        self.remember();self.slots[int(selected[0])]['photo']=number-1;self.refresh()

    def delete_frame(self):
        selected=self.tree.selection()
        if selected:self.remember();self.slots.pop(int(selected[0]));self.renumber();self.refresh()

    def remember(self):
        self.history.append(copy.deepcopy(self.slots));self.history=self.history[-30:]

    def undo(self):
        if self.history:self.slots=self.history.pop();self.refresh()

    def zoom_changed(self,value):
        selected=self.tree.selection()
        if not selected:return
        slot=self.slots[int(selected[0])];value=round(float(value),2)
        if abs(slot.get('zoom',1)-value)>.001:slot['zoom']=value;self.draw()

    def reset_photo(self):
        selected=self.tree.selection()
        if selected:self.remember();slot=self.slots[int(selected[0])];slot['zoom']=1;slot['centering']=[.5,.42];self.zoom.set(1);self.draw()

    def edit_coordinates(self):
        selected=self.tree.selection()
        if not selected:return
        i=int(selected[0]);dialog=tk.Toplevel(self.window);dialog.title('精确调整照片框');fit_window(dialog,self.window,420,330)
        values=[]
        for label,value in zip(('横向位置 X','纵向位置 Y','宽度','高度'),self.slots[i]['box']):
            row=tk.Frame(dialog,bg=BG);row.pack(fill='x',padx=24,pady=6);tk.Label(row,text=label,bg=BG,width=13,anchor='w').pack(side='left');var=tk.StringVar(value=str(value));tk.Entry(row,textvariable=var,font=('Microsoft YaHei UI',14)).pack(side='left',fill='x',expand=True);values.append(var)
        def apply():
            try:
                box=[int(v.get()) for v in values];test=copy.deepcopy(self.slots);test[i]['box']=box;library.validate_slots(self.overlay.size,test)
            except (ValueError,tk.TclError) as exc:messagebox.showwarning('位置无效',str(exc),parent=dialog);return
            self.remember();self.slots[i]['box']=box;self.slots[i]['cutout']=True;dialog.destroy();self.window.grab_set();self.refresh()
        button(dialog,'应用位置和大小',apply).pack(pady=15)
        dialog.protocol('WM_DELETE_WINDOW',lambda:(dialog.destroy(),self.window.grab_set()))

    def renumber(self):
        values=sorted({s['photo'] for s in self.slots});mapping={v:i for i,v in enumerate(values)}
        for slot in self.slots:slot['photo']=mapping[slot['photo']]

    def detect(self):
        try:self.remember();self.slots=library.detect_template_slots(self.overlay)[0];self.refresh()
        except ValueError as exc:messagebox.showwarning('照片框识别',str(exc),parent=self.window)

    def unique_photos(self):
        if len(self.slots)>12:messagebox.showwarning('拍摄张数','最多拍 12 张，请为重复照片指定相同编号。',parent=self.window);return
        self.remember()
        for i,slot in enumerate(self.slots):slot['photo']=i
        self.refresh()

    def repeat_columns(self):
        if len(self.slots)<2 or len(self.slots)%2:
            messagebox.showinfo('两列同组照片','请先设置偶数个、左右两列的照片框。',parent=self.window);return
        ordered=sorted(self.slots,key=lambda s:(round(s['box'][1]/max(20,self.overlay.height*.025)),s['box'][0]))
        for i in range(0,len(ordered),2):
            a,b=ordered[i]['box'],ordered[i+1]['box']
            if abs(a[1]-b[1])>self.overlay.height*.05 or abs(a[0]-b[0])<self.overlay.width*.15:
                messagebox.showinfo('两列同组照片','照片框没有组成整齐的左右两列，请逐框指定照片编号。',parent=self.window);return
        self.remember()
        for i,slot in enumerate(ordered):slot['photo']=i//2
        self.refresh()

    def clear_frames(self):self.remember();self.slots=[];self.refresh()

    def save(self):
        try:revised=library.revise_template(self.root,self.template,self.name.get(),self.slots)
        except (ValueError,OSError) as exc:messagebox.showwarning('照片框未保存',str(exc),parent=self.window);return
        self.on_save(revised);self.close()

    def close(self):
        self.window.destroy()
        if self.parent.winfo_exists():self.parent.grab_set()

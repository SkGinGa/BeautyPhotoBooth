"""Camera setup, honest capability labels, and Windows driver repair progress."""
import ctypes,queue,subprocess,sys,threading,webbrowser
import tkinter as tk
from tkinter import ttk,messagebox
from camera_devices import detect_devices,OFFICIAL,repair_missing_drivers

BG='#F4EEE6'

def elevated_driver_repair():
    args=['--repair-camera-drivers']
    if not getattr(sys,'frozen',False):
        from pathlib import Path
        args.insert(0,str(Path(__file__).with_name('app.py')))
    code=ctypes.windll.shell32.ShellExecuteW(None,'runas',sys.executable,subprocess.list2cmdline(args),None,1)
    if code<=32:raise RuntimeError('未启动管理员驱动修复。可以从 Windows 更新中安装可选相机驱动。')

class DriverRepairWindow:
    def __init__(self,root):
        self.root=root;root.title('美颜自拍亭 · 相机驱动修复');root.geometry('680x330');root.configure(bg=BG)
        tk.Label(root,text='仅修复缺失的相机驱动',font=('Microsoft YaHei UI',21),bg=BG).pack(pady=25)
        self.status=tk.Label(root,text='正在读取 Windows 相机设备…',font=('Microsoft YaHei UI',12),wraplength=600,bg=BG);self.status.pack(padx=25,pady=20)
        tk.Label(root,text='使用 Windows Update 精确匹配硬件 ID；不会安装固件或自动重启。',font=('Microsoft YaHei UI',10),bg=BG).pack(pady=10)
        self.close=tk.Button(root,text='完成后关闭',command=root.destroy,state='disabled',font=('Microsoft YaHei UI',12));self.close.pack(pady=10)
        self.messages=queue.Queue();self.running=True;root.protocol('WM_DELETE_WINDOW',self.try_close)
        def work():
            try:self.messages.put(('done',repair_missing_drivers(progress=lambda s:self.messages.put(('progress',s)))))
            except Exception as exc:self.messages.put(('error','驱动修复未完成：'+str(exc)))
        threading.Thread(target=work,daemon=True).start();root.after(100,self.poll)

    def try_close(self):
        if self.running:self.status.config(text='驱动操作进行中，请完成后关闭。')
        else:self.root.destroy()

    def poll(self):
        try:
            while True:
                kind,data=self.messages.get_nowait()
                if kind=='progress':self.status.config(text=data)
                else:
                    self.running=False;self.close.config(state='normal')
                    self.status.config(text=data if kind=='error' else data['message']+(' Windows 建议重启后重新检测。' if data.get('reboot_required') else ''))
        except queue.Empty:pass
        self.root.after(100,self.poll)

class CameraWindow:
    def __init__(self,parent,config,on_apply,hub=None):
        from library_ui import fit_window,button,staff_styles
        self.window=tk.Toplevel(parent);self.window.title('相机自动检测、连接与驱动');fit_window(self.window,parent,1050,780);staff_styles(self.window)
        self.config=config;self.on_apply=on_apply;self.messages=queue.Queue();self.inventory=None;self.choices={}
        tk.Label(self.window,text='相机自动检测与连接',font=('Microsoft YaHei UI',22),bg=BG).pack(anchor='w',padx=20,pady=14)
        self.tree=ttk.Treeview(self.window,columns=('name','type','driver'),show='headings',style='Asset.Treeview',height=6)
        for key,label,width in (('name','Windows 检测到的型号 / 名称',480),('type','设备类型',170),('driver','驱动状态',220)):
            self.tree.heading(key,text=label);self.tree.column(key,width=width)
        self.tree.pack(fill='both',expand=True,padx=20)
        row=tk.Frame(self.window,bg=BG);row.pack(fill='x',padx=20,pady=10)
        button(row,'重新检测',self.scan).pack(side='left');button(row,'管理员修复缺失驱动',self.repair).pack(side='left',padx=10)
        self.auto=tk.BooleanVar(value=config.get('auto_driver_check',True));tk.Checkbutton(self.window,text='启动时通过 Windows Update 自动检查并修复缺失相机驱动（需要网络与相应权限）',variable=self.auto,bg=BG,font=('Microsoft YaHei UI',11)).pack(anchor='w',padx=20,pady=6)
        tk.Label(self.window,text='拍摄连接方式',font=('Microsoft YaHei UI',13),bg=BG).pack(anchor='w',padx=20)
        self.selected=tk.StringVar();self.combo=ttk.Combobox(self.window,textvariable=self.selected,state='readonly',font=('Microsoft YaHei UI',12));self.combo.pack(fill='x',padx=20,pady=8)
        tk.Label(self.window,text='佳能原生：需要合法安装 EDSDK。USB视频：支持能输出 Windows 视频流的相机 / 采集卡，按实际视频分辨率取帧，不触发原生照片快门，不支持镜头点按对焦。索尼、松下原生 SDK 尚未接入。',font=('Microsoft YaHei UI',11),bg=BG,fg='#796E64',wraplength=980,justify='left').pack(fill='x',padx=20,pady=8)
        links=tk.Frame(self.window,bg=BG);links.pack(fill='x',padx=20,pady=4)
        for key,label in (('canon','佳能官方支持'),('sony','索尼官方 SDK'),('panasonic','松下官方 SDK')):button(links,label,lambda k=key:webbrowser.open(OFFICIAL[k])).pack(side='left',padx=(0,8))
        foot=tk.Frame(self.window,bg=BG);foot.pack(fill='x',padx=20,pady=15)
        self.status=tk.Label(foot,text='正在检测设备…',bg=BG,fg='#796E64',wraplength=700,justify='left');self.status.pack(side='left',fill='x',expand=True)
        button(foot,'保存并连接',self.apply).pack(side='right')
        self.scan();self.window.after(100,self.poll)

    def scan(self):
        self.status.config(text='正在读取设备型号和可用视频接口…')
        def work():
            try:self.messages.put(('scan',detect_devices()))
            except Exception as exc:self.messages.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        if not self.window.winfo_exists():return
        try:
            while True:
                kind,data=self.messages.get_nowait()
                if kind=='error':self.status.config(text=data);continue
                self.inventory=data;self.tree.delete(*self.tree.get_children())
                for device in data['pnp']:
                    status='已安装（正常）' if device.get('problem_code')==0 else '缺少驱动' if device.get('problem_code')==28 else device.get('status','未知')
                    self.tree.insert('','end',values=(device['name'],device.get('class','USB'),status))
                self.choices={'自动检测（优先外接相机）':{'camera_backend':'auto'},'佳能 · 原生联机拍照':{'camera_backend':'canon'}}
                for video in data['video']:self.choices[f"USB视频 {video['index']+1} · {video['name']}"]={'camera_backend':'video','video_device':video['name'],'video_index':video['index']}
                self.combo.config(values=list(self.choices))
                match=next((label for label,value in self.choices.items() if value['camera_backend']==self.config.get('camera_backend','auto') and (value['camera_backend']!='video' or value['video_device']==self.config.get('video_device'))),next(iter(self.choices)))
                self.selected.set(match);self.status.config(text='检测完成。驱动正常仅表示 Windows 识别设备，不代表已支持原生遥控。'+(' '.join(data['errors']) if data['errors'] else ''))
        except queue.Empty:pass
        self.window.after(100,self.poll)

    def repair(self):
        try:elevated_driver_repair();self.status.config(text='请在 Windows 确认后，等待修复窗口完成，再重新检测。')
        except Exception as exc:messagebox.showinfo('驱动修复',str(exc),parent=self.window)

    def apply(self):
        choice=self.choices.get(self.selected.get())
        if not choice:return
        choice=dict(choice,auto_driver_check=self.auto.get());self.window.destroy();self.on_apply(choice)

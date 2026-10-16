"""Canon native tethering and explicitly labelled DirectShow video capture."""
import queue,threading,time
from concurrent.futures import Future,ThreadPoolExecutor
from pathlib import Path
import cv2
from PIL import Image
from camera import CanonCamera,CameraError
from camera_devices import detect_devices,discover_canon_sdk,list_video_devices,missing_camera_devices,repair_missing_drivers

class VideoCamera(threading.Thread):
    supports_point_focus=False
    def __init__(self,name,index):
        super().__init__(name='VideoCamera',daemon=True)
        self.device_name=name;self.device_index=index;self.name=name+' · USB视频'
        self.commands=queue.Queue();self.connected=False;self.tainted=False;self.error='';self.frame=None;self.bgr=None;self.stop_flag=False;self.cap=None;self.lock=threading.Lock();self.start()

    def request(self,action,**kwargs):
        f=Future();self.commands.put((action,kwargs,f));return f

    def latest_frame(self):
        with self.lock:return self.frame

    def connect(self):
        devices=list_video_devices();matches=[d for d in devices if d['name']==self.device_name]
        if len(matches)!=1:raise CameraError('视频设备已变更或存在同名设备，请重新选择明确的设备。')
        if self.cap:self.cap.release()
        self.connected=False
        self.cap=cv2.VideoCapture(matches[0]['index'],cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,1920);self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT,1080)
        if not self.cap.isOpened():raise CameraError('无法打开该视频设备，请关闭占用程序，并检查相机视频模式。')
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            ok,bgr=self.cap.read()
            if ok and bgr is not None:
                self.publish(bgr);self.connected=True
                h,w=bgr.shape[:2];self.name=f'{self.device_name} · USB视频 {w}×{h}';return self.name
        raise CameraError('视频设备没有返回画面，请检查相机或采集卡。')

    def publish(self,bgr):
        ok,jpeg=cv2.imencode('.jpg',bgr,[cv2.IMWRITE_JPEG_QUALITY,94])
        if not ok:return
        h,w=bgr.shape[:2]
        with self.lock:self.bgr=bgr.copy();self.frame=(time.monotonic(),jpeg.tobytes(),{'coord':(w,h),'visible_rect':(0,0,w,h),'source':'video'})

    def capture(self,path):
        with self.lock:
            if not self.connected or self.frame is None or time.monotonic()-self.frame[0]>2:raise CameraError('视频画面未就绪，请重连后再拍。')
            bgr=self.bgr.copy()
        image=Image.fromarray(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB));path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');image.save(temp,'JPEG',quality=98,subsampling=0);temp.replace(path);return str(path)

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            while not self.stop_flag:
                try:action,kwargs,future=self.commands.get(timeout=.01)
                except queue.Empty:action=None
                if action:
                    try:
                        if action=='connect':value=self.connect()
                        elif action=='capture':value=self.capture(kwargs['path'])
                        elif action in ('reset_focus','live'):value=True
                        elif action=='focus':raise CameraError('USB视频模式不提供镜头点按对焦，请在相机上调焦。')
                        elif action=='close':self.stop_flag=True;value=True
                        else:raise CameraError('未知视频相机指令')
                        future.set_result(value)
                    except Exception as exc:self.error=str(exc);future.set_exception(exc)
                if self.connected and not self.stop_flag:
                    ok,bgr=self.cap.read()
                    if ok and bgr is not None:self.publish(bgr)
                    elif self.frame and time.monotonic()-self.frame[0]>3:self.connected=False;self.error='视频连接已断开，请重连。'
        finally:
            self.connected=False
            if self.cap:self.cap.release()
            pythoncom.CoUninitialize()

class CameraHub:
    def __init__(self,settings):
        self.settings=dict(settings);self.child=None;self.inventory=None;self.driver_status=None;self.driver_checked=False
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='CameraRouter');self.closed=threading.Event()

    @property
    def connected(self):return bool(self.child and self.child.connected)
    @property
    def tainted(self):return bool(self.child and self.child.tainted)
    @property
    def name(self):return self.child.name if self.child else ''
    @property
    def error(self):return self.child.error if self.child else ''
    @property
    def supports_point_focus(self):return self.child is not None and not isinstance(self.child,VideoCamera)

    def latest_frame(self):return self.child.latest_frame() if self.child else None

    def request(self,action,**kwargs):return self.pool.submit(self.execute,action,kwargs)

    def close_child(self):
        if self.child:
            child=self.child;child.request('close').result(timeout=8);child.join(timeout=8)
            if child.is_alive():raise CameraError('旧相机接口尚未释放，请退出软件后重试。')
            self.child=None

    def execute(self,action,kwargs):
        if action=='close':
            try:self.close_child()
            finally:self.closed.set();self.pool.shutdown(wait=False,cancel_futures=True)
            return True
        if action=='configure':self.settings.update(sdk_path=kwargs['sdk_path'],camera_backend='canon');action='connect'
        if action=='select':self.settings.update(kwargs);action='connect'
        if action=='connect':
            self.close_child();backend=self.settings.get('camera_backend','auto');name=self.settings.get('video_device','')
            sdk=discover_canon_sdk(self.settings.get('sdk_path',''))
            if backend=='auto':
                self.inventory=detect_devices()
                if self.settings.get('auto_driver_check',True) and not self.driver_checked and missing_camera_devices(self.inventory['pnp']):
                    self.driver_checked=True
                    try:
                        self.driver_status=repair_missing_drivers(self.inventory['pnp'])
                        if self.driver_status['status']=='installed':self.inventory=detect_devices()
                    except Exception as exc:self.driver_status={'status':'error','message':'自动驱动修复未完成，请在 F10 相机检测中使用管理员修复或官方入口。','detail':str(exc)}
                canon=[d for d in self.inventory['pnp'] if d['brand']=='canon' and d.get('class') in ('WPD','Image','Camera')]
                external=[d for d in self.inventory['video'] if d['brand'] in ('sony','panasonic','canon')]
                if len(canon)==1 and not external:backend='canon'
                elif len(external)==1 and not canon:backend='video';name=external[0]['name']
                elif len(canon)+len(external)>1:raise CameraError('检测到多个相机，请按 F10 在“相机检测与连接”中选择。')
                elif any(d['brand'] in ('sony','panasonic') and d.get('class') in ('WPD','Image','Camera') for d in self.inventory['pnp']):raise CameraError('已识别索尼或松下设备；请在 F10 切换 USB视频方式或查看官方接口说明，当前尚未接入其原生遥控 SDK。')
                elif sdk:backend='canon'
                else:raise CameraError('请按 F10 查看自动检测结果，安装所需接口或选择 USB视频设备。')
            if backend=='canon':
                if not sdk:raise CameraError('检测到佳能连接方式，但未找到合法安装的 EDSDK.dll；请按 F10 选择接口文件。')
                self.child=CanonCamera(sdk)
            elif backend=='video':
                if not name:raise CameraError('请先选择视频设备。')
                self.child=VideoCamera(name,self.settings.get('video_index',0))
            else:raise CameraError('未配置该相机连接方式。')
            return self.child.request('connect').result(timeout=35)
        if not self.child:raise CameraError('相机未连接，请先重连。')
        return self.child.request(action,**kwargs).result(timeout=35)

    def join(self,timeout=None):return self.closed.wait(timeout)

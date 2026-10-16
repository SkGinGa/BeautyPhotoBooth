"""Canon EDSDK adapter. All SDK calls live on one COM STA thread.

The Canon SDK is loaded from the existing installation, never redistributed.
No camera exposure parameters or SD-card contents are changed.
"""
import ctypes as C
import io
import os
import queue
import struct
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from PIL import Image

R = C.c_void_p
U = C.c_uint32


class DeviceInfo(C.Structure):
    _fields_ = [('port', C.c_char * 256), ('name', C.c_char * 256),
                ('subtype', U), ('reserved', U)]


class DirectoryInfo(C.Structure):
    _fields_ = [('size', C.c_uint64), ('folder', U), ('group', U),
                ('option', U), ('name', C.c_char * 256), ('format', U), ('date', U)]


class Capacity(C.Structure):
    _fields_ = [('clusters', C.c_int32), ('sector', C.c_int32), ('reset', C.c_int32)]


class CameraError(RuntimeError):
    pass


def decode_focus_info(raw):
    if len(raw)<24: return []
    x,y,w,h,count=struct.unpack_from('<4iI',raw)
    if w<=0 or h<=0 or count>1053 or 20+count*32>len(raw): return []
    points=[]
    for i in range(count):
        valid,selected,status,px,py,pw,ph,_=struct.unpack_from('<3I4iI',raw,20+i*32)
        if valid and pw>0 and ph>0:
            points.append({'box':((px-x)/w,(py-y)/h,pw/w,ph/h),'status':status,'selected':bool(selected)})
    return points


def focus_status(code):
    # 0x11 was verified on the R6m2 One-Shot live-view path. Unknown codes
    # (including tracking/detection codes) must never be called in focus.
    return 'focused' if code in (1,0x11) else 'failed' if code in (2,0x12) else 'unknown'


class CanonCamera(threading.Thread):
    def __init__(self, sdk_path):
        super().__init__(name='CanonSDK', daemon=True)
        self.sdk_path = Path(sdk_path)
        self.commands = queue.Queue()
        self.camera = R()
        self.frame = None
        self.connected = False
        self.name = ''
        self.error = ''
        self.stop_flag = False
        self.pending = None
        self.focus_pending = None
        self.live_meta = {}
        self.old_focus_position = None
        self.tainted = False
        self.old = {}
        self.sdk = None
        self.initialized = False
        self.want_live = True
        self.frame_lock = threading.Lock()
        self.start()

    def request(self, action, **kwargs):
        future = Future()
        self.commands.put((action, kwargs, future))
        return future

    def latest_frame(self):
        with self.frame_lock:
            return self.frame

    def bind(self):
        if not self.sdk_path.is_file():
            raise CameraError('未找到佳能相机接口，请检查设置中的 EDSDK.dll 路径。')
        self.dll_cookie = os.add_dll_directory(str(self.sdk_path.parent))
        self.sdk = C.WinDLL(str(self.sdk_path))
        definitions = {
            'EdsInitializeSDK': [], 'EdsTerminateSDK': [], 'EdsGetEvent': [],
            'EdsGetCameraList': [C.POINTER(R)], 'EdsGetChildCount': [R, C.POINTER(U)],
            'EdsGetChildAtIndex': [R, C.c_int32, C.POINTER(R)],
            'EdsGetDeviceInfo': [R, C.POINTER(DeviceInfo)],
            'EdsOpenSession': [R], 'EdsCloseSession': [R], 'EdsRelease': [R],
            'EdsGetPropertyData': [R, U, C.c_int32, U, R],
            'EdsSetPropertyData': [R, U, C.c_int32, U, R],
            'EdsSetCapacity': [R, Capacity], 'EdsSendCommand': [R, U, C.c_int32],
            'EdsSetObjectEventHandler': [R, U, R, R],
            'EdsSetCameraStateEventHandler': [R, U, R, R],
            'EdsGetDirectoryItemInfo': [R, C.POINTER(DirectoryInfo)],
            'EdsCreateMemoryStream': [C.c_uint64, C.POINTER(R)],
            'EdsDownload': [R, C.c_uint64, R], 'EdsDownloadComplete': [R],
            'EdsDownloadCancel': [R], 'EdsGetLength': [R, C.POINTER(C.c_uint64)],
            'EdsGetPointer': [R, C.POINTER(R)],
            'EdsCreateEvfImageRef': [R, C.POINTER(R)], 'EdsDownloadEvfImage': [R, R],
        }
        for name, args in definitions.items():
            f = getattr(self.sdk, name); f.argtypes = args; f.restype = U
        self.object_cb = C.WINFUNCTYPE(U, U, R, R)(self.object_event)
        self.state_cb = C.WINFUNCTYPE(U, U, U, R)(self.state_event)
        self.check(self.sdk.EdsInitializeSDK(), '初始化相机接口')
        self.initialized = True

    @staticmethod
    def check(code, action):
        if code:
            tips = {
                0x81: '相机正忙，请稍候；若持续出现，请重新连接。',
                0x80: '相机不可用，请唤醒相机并检查 USB 连接。',
                0x8D01: '自动对焦未成功，请增加照明或将镜头调到合适焦点。',
                0xA102: '相机处于无法拍摄的状态，请检查相机屏幕提示。',
                0x20: '相机连接已断开，请唤醒相机并重新连接。',
            }
            raise CameraError(f'{action}失败（0x{code:08X}）。' + tips.get(code, '请检查相机状态，关闭其他占用相机的软件后重连。'))

    def get_prop(self, prop):
        value = U(); self.check(self.sdk.EdsGetPropertyData(self.camera, prop, 0, 4, C.byref(value)), '读取相机设置')
        return value.value

    def set_prop(self, prop, value):
        data = U(value); self.check(self.sdk.EdsSetPropertyData(self.camera, prop, 0, 4, C.byref(data)), '设置相机')

    def connect(self):
        self.disconnect()
        if not self.initialized:
            self.bind()
        listing = R()
        try:
            self.check(self.sdk.EdsGetCameraList(C.byref(listing)), '查找相机')
            count = U(); self.check(self.sdk.EdsGetChildCount(listing, C.byref(count)), '查找相机')
            if not count.value:
                raise CameraError('没有找到佳能相机。请唤醒相机、保持 USB 连接，并关闭像素蛋糕的联机拍摄或其他相机软件。')
            if count.value != 1:
                raise CameraError('连接了多台佳能相机，请只保留自拍机使用的那一台。')
            self.check(self.sdk.EdsGetChildAtIndex(listing, 0, C.byref(self.camera)), '选择相机')
            info = DeviceInfo(); self.check(self.sdk.EdsGetDeviceInfo(self.camera, C.byref(info)), '读取型号')
            self.name = info.name.decode('utf-8', errors='replace')
            self.check(self.sdk.EdsOpenSession(self.camera), '连接相机')
            self.connected = True
            self.old = {p: self.get_prop(p) for p in (0xB, 0x100, 0x500, 0x501, 0x50E, 0x404)}
            # JPEG to host, restoring the camera's original values at shutdown.
            self.set_prop(0xB, 2)
            self.check(self.sdk.EdsSetCapacity(self.camera, Capacity(0x7FFFFFFF, 512, 1)), '配置传输空间')
            self.set_prop(0x100, 0x0013FF0F)
            self.check(self.sdk.EdsSetObjectEventHandler(self.camera, 0x200, self.object_cb, None), '注册照片传输')
            self.check(self.sdk.EdsSetCameraStateEventHandler(self.camera, 0x300, self.state_cb, None), '注册相机状态')
            # EDSDK: 0x501 is EVF mode, 0x500 is the EVF output device.
            self.set_prop(0x501, 1)
            self.set_prop(0x500, self.get_prop(0x500) | 2)
            self.tainted = False; self.error = ''; self.want_live = True
            return self.name
        except Exception:
            self.disconnect()
            raise
        finally:
            if listing: self.sdk.EdsRelease(listing)

    def configure(self,sdk_path):
        """Switch SDK only on its owning STA thread, after closing the old session."""
        path=Path(sdk_path)
        if not path.is_file():raise CameraError('请选择已经合法安装的 64 位 EDSDK.dll 文件。')
        if self.pending or self.focus_pending:raise CameraError('请等待拍摄或对焦结束后更换接口。')
        if path!=self.sdk_path:
            self.disconnect()
            if self.initialized:self.sdk.EdsTerminateSDK()
            self.initialized=False;self.sdk=None
            if hasattr(self,'dll_cookie'):self.dll_cookie.close()
            self.sdk_path=path
        return self.connect()

    def disconnect(self):
        if self.focus_pending:
            self.finish_focus(error=CameraError('相机已断开，对焦已取消。'))
        if self.pending:
            self.fail_pending(CameraError('相机已断开，本次拍摄取消。'))
        if self.camera and self.sdk:
            if self.connected:
                try:self.reset_focus()
                except Exception:pass
                for prop in (0x500, 0x501, 0x100, 0xB):
                    if prop in self.old:
                        try: self.set_prop(prop, self.old[prop])
                        except Exception: pass
                self.sdk.EdsCloseSession(self.camera)
            self.sdk.EdsRelease(self.camera)
        self.camera = R(); self.connected = False; self.old = {}; self.frame = None
        self.live_meta={};self.old_focus_position=None

    def bytes_of(self, stream):
        size = C.c_uint64(); pointer = R()
        self.check(self.sdk.EdsGetLength(stream, C.byref(size)), '读取照片大小')
        self.check(self.sdk.EdsGetPointer(stream, C.byref(pointer)), '读取照片')
        if not 0 < size.value < 150_000_000: raise CameraError('收到异常大小的照片。')
        return C.string_at(pointer, size.value)

    def object_event(self, event, item, context):
        try:
            if event != 0x208: return 0
            if not self.pending:
                self.sdk.EdsDownloadCancel(item); return 0
            info = DirectoryInfo(); self.check(self.sdk.EdsGetDirectoryItemInfo(item, C.byref(info)), '读取拍摄文件')
            if not info.name.lower().endswith((b'.jpg', b'.jpeg')):
                self.sdk.EdsDownloadCancel(item); return 0
            stream = R()
            try:
                self.check(self.sdk.EdsCreateMemoryStream(info.size, C.byref(stream)), '接收照片')
                self.check(self.sdk.EdsDownload(item, info.size, stream), '下载照片')
                self.check(self.sdk.EdsDownloadComplete(item), '确认照片下载')
                data = self.bytes_of(stream)
                with Image.open(io.BytesIO(data)) as im: im.verify()
                pending = self.pending
                destination = pending['path']; destination.parent.mkdir(parents=True, exist_ok=True)
                partial = destination.with_suffix('.part'); partial.write_bytes(data); partial.replace(destination)
                self.pending = None
                pending['future'].set_result(str(destination))
            finally:
                if stream: self.sdk.EdsRelease(stream)
        except Exception as exc:
            self.tainted = True; self.fail_pending(exc)
        finally:
            if item: self.sdk.EdsRelease(item)
        return 0

    def state_event(self, event, parameter, context):
        if event == 0x303 and self.connected:
            self.sdk.EdsSendCommand(self.camera, 1, 0)
        elif event == 0x301:
            self.connected = False; self.frame = None
            self.error = '相机已断开或关机，请唤醒后点击“重新连接相机”。'
            self.fail_pending(CameraError(self.error))
            if self.focus_pending:self.finish_focus(error=CameraError(self.error))
        return 0

    def fail_pending(self, error):
        if self.pending:
            p = self.pending; self.pending = None
            if not p['future'].done(): p['future'].set_exception(error)

    def capture(self, path, future):
        if not self.connected: raise CameraError('相机尚未连接，请先连接相机。')
        if self.tainted: raise CameraError('上次传输未正常完成，请重新连接相机后再拍摄。')
        if self.pending: raise CameraError('正在接收上一张照片，请稍候。')
        if self.focus_pending: raise CameraError('正在对焦，请等对焦框状态更新后再拍摄。')
        self.pending = {'path': Path(path), 'future': future, 'deadline': time.monotonic() + 25}
        code = self.sdk.EdsSendCommand(self.camera, 0, 0)
        if code:
            self.pending = None; self.check(code, '拍摄')

    def prop_values(self, obj, prop, count):
        value=(C.c_int32*count)()
        if self.sdk.EdsGetPropertyData(obj,prop,0,C.sizeof(value),value):return None
        return list(value)

    def reset_focus(self):
        if self.focus_pending:raise CameraError('正在对焦，请稍候。')
        if self.old_focus_position:
            point=(C.c_int32*2)(*self.old_focus_position)
            self.check(self.sdk.EdsSetPropertyData(self.camera,0x508,0,8,point),'恢复对焦位置')
        for p in (0x50E,0x404):
            if p in self.old:self.set_prop(p,self.old[p])
        return True

    def focus(self, x, y, future):
        if not self.connected or self.tainted:raise CameraError('相机未就绪，请先重新连接。')
        if self.pending or self.focus_pending:raise CameraError('正在拍摄或对焦，请稍候。')
        if not self.frame or time.monotonic()-self.frame[0]>2:raise CameraError('实时画面未就绪，请稍候再点按对焦。')
        coord=self.live_meta.get('coord');zoom=self.live_meta.get('zoom_rect')
        if not coord or not zoom:raise CameraError('相机没有返回对焦坐标，无法点按定位。')
        cw,ch=coord;zw,zh=zoom[2:]
        # Evf_ZoomPosition is the top-left of the zoom rectangle; AF is at
        # its centre. Verified against two actual R6m2 FocusInfo rectangles.
        px=round(max(0,min(cw-zw,float(x)*cw-zw/2)))
        py=round(max(0,min(ch-zh,float(y)*ch-zh/2)))
        previous={p:self.get_prop(p) for p in (0x50E,0x404)}
        try:
            self.set_prop(0x50E,1);self.set_prop(0x404,0)
            point=(C.c_int32*2)(px,py)
            self.check(self.sdk.EdsSetPropertyData(self.camera,0x508,0,8,point),'移动对焦点')
            now=time.monotonic()
            self.focus_pending={'future':future,'target':((px+zw/2)/cw,(py+zh/2)/ch),
                                'phase':'position','start':now+.25,'deadline':now+5,'armed':False}
        except Exception:
            for p,v in previous.items():
                try:self.set_prop(p,v)
                except Exception:pass
            raise

    def advance_focus(self):
        pending=self.focus_pending
        if not pending:return
        now=time.monotonic()
        if pending['phase']=='position' and now>=pending['start']:
            try:
                self.check(self.sdk.EdsSendCommand(self.camera,0x102,1),'自动对焦')
                pending['armed']=True;pending['phase']='focusing';pending['started']=now
            except Exception as exc:self.finish_focus(error=exc);return
        if now>=pending['deadline']:
            self.finish_focus({'state':'unconfirmed','target':pending['target']});return
        if pending['phase']!='focusing' or now-pending['started']<.4:return
        target=pending['target']
        # A previous frame can retain the old point/status. Require a fresh
        # frame and a returned AF rectangle at the requested position.
        if not self.frame or now-self.frame[0]>.5:return
        for point in self.live_meta.get('focus_points',[]):
            x,y,w,h=point['box'];state=focus_status(point['status'])
            if point['selected'] and abs(x+w/2-target[0])<.025 and abs(y+h/2-target[1])<.025 and state!='unknown':
                self.finish_focus({'state':state,'target':target,'box':point['box'],'camera_status':point['status']});return

    def finish_focus(self,result=None,error=None):
        pending=self.focus_pending
        if not pending:return
        self.focus_pending=None
        if pending.get('armed') and self.camera:
            code=self.sdk.EdsSendCommand(self.camera,0x102,0)
            if code:
                self.tainted=True;error=CameraError('结束自动对焦失败，请重新连接相机。')
        future=pending['future']
        if not future.done():
            if error:future.set_exception(error)
            else:future.set_result(result)

    def live_frame(self):
        stream = R(); evf = R()
        try:
            self.check(self.sdk.EdsCreateMemoryStream(0, C.byref(stream)), '实时取景')
            self.check(self.sdk.EdsCreateEvfImageRef(stream, C.byref(evf)), '实时取景')
            code = self.sdk.EdsDownloadEvfImage(self.camera, evf)
            if code: return
            data = self.bytes_of(stream)
            coord=self.prop_values(evf,0x540,2)
            zoom=self.prop_values(evf,0x541,4)
            visible=self.prop_values(evf,0x01000546,4) or self.prop_values(evf,0x545,4)
            position=self.prop_values(evf,0x508,2)
            if self.old_focus_position is None and position:self.old_focus_position=position
            # SDK 13.18 returns NOT_SUPPORTED from GetPropertySize(FocusInfo),
            # but supports direct GetPropertyData on the camera, not the EVF ref.
            raw=C.create_string_buffer(33720)
            code=self.sdk.EdsGetPropertyData(self.camera,0x104,0,len(raw),raw)
            points=[] if code else decode_focus_info(raw.raw)
            self.live_meta={'coord':coord,'zoom_rect':zoom,'visible_rect':visible,'focus_points':points}
            with self.frame_lock: self.frame = (time.monotonic(), data, self.live_meta)
        finally:
            if evf: self.sdk.EdsRelease(evf)
            if stream: self.sdk.EdsRelease(stream)

    def run(self):
        ole = C.OleDLL('ole32'); ole.CoInitialize(None)
        live_at = 0; keepalive_at = 0
        try:
            while not self.stop_flag:
                try:
                    action, kwargs, future = self.commands.get(timeout=0.015)
                except queue.Empty:
                    action = None
                if action:
                    try:
                        if action == 'connect': future.set_result(self.connect())
                        elif action == 'configure':future.set_result(self.configure(kwargs['sdk_path']))
                        elif action == 'capture': self.capture(kwargs['path'], future)
                        elif action == 'focus':self.focus(kwargs['x'],kwargs['y'],future)
                        elif action == 'reset_focus':future.set_result(self.reset_focus())
                        elif action == 'live': self.want_live = kwargs['enabled']; future.set_result(True)
                        elif action == 'close': self.stop_flag = True; future.set_result(True)
                        else: raise CameraError('未知相机指令')
                    except Exception as exc:
                        self.error = str(exc)
                        if not future.done(): future.set_exception(exc)
                if self.initialized:
                    self.sdk.EdsGetEvent()
                now = time.monotonic()
                self.advance_focus()
                if self.pending and now > self.pending['deadline']:
                    self.tainted = True
                    self.fail_pending(CameraError('25 秒内未收到照片。请检查对焦、相机电源和 USB；重新连接后再拍，避免旧照片混入。'))
                if self.connected and now > keepalive_at:
                    self.sdk.EdsSendCommand(self.camera, 1, 0); keepalive_at = now + 15
                if self.connected and self.want_live and not self.pending and now > live_at:
                    try: self.live_frame()
                    except Exception as exc: self.error = str(exc)
                    live_at = now + 0.075
        finally:
            self.disconnect()
            if self.initialized: self.sdk.EdsTerminateSDK()
            ole.CoUninitialize()

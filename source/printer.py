"""Explicit Windows print queue, one print per session, no automatic retry."""
from pathlib import Path
import ctypes as C
import win32print
import win32ui
import win32gui
import win32con
from PIL import Image, ImageWin

BAD_FLAGS = 0x1 | 0x2 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80 | 0x800 | 0x1000 | 0x40000 | 0x100000 | 0x200000 | 0x400000 | 0x800000


class DocInfo(C.Structure):
    _fields_ = [('cbSize', C.c_int), ('lpszDocName', C.c_wchar_p),
                ('lpszOutput', C.c_wchar_p), ('lpszDatatype', C.c_wchar_p),
                ('fwType', C.c_uint32)]


gdi = C.WinDLL('gdi32', use_last_error=True)
gdi.StartDocW.argtypes = [C.c_void_p, C.POINTER(DocInfo)]
gdi.StartDocW.restype = C.c_int
for _name in ('StartPage', 'EndPage', 'EndDoc', 'AbortDoc'):
    getattr(gdi, _name).argtypes = [C.c_void_p]
    getattr(gdi, _name).restype = C.c_int


def checked_gdi(name, hdc):
    if getattr(gdi, name)(hdc) <= 0:
        raise RuntimeError(f'Windows 打印操作 {name} 失败，请工作人员检查打印队列。')


def queues():
    return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]


def inspect(name):
    if name not in queues(): raise RuntimeError('指定的打印机不存在，请在设置中选择已安装的 DNP DS620。')
    handle = win32print.OpenPrinter(name)
    try:
        info = win32print.GetPrinter(handle,2)
        status = info['Status']
        if info['Attributes'] & 0x400: raise RuntimeError('打印队列设置为脱机使用，请恢复在线。')
        if status & BAD_FLAGS: raise RuntimeError(f'打印机报告异常（状态 {status}），请检查缺纸、卡纸、暂停或脱机。')
        if 'DS620' not in info['pDriverName'].upper(): raise RuntimeError('为防止错打，只允许使用 DNP DS620 驱动对应的打印队列。')
        return {'name':name, 'driver':info['pDriverName'], 'port':info['pPortName'], 'status':status, 'jobs':info['cJobs']}
    finally: win32print.ClosePrinter(handle)


def print_photo(path, name, document_name, on_job=None):
    inspect(name)
    handle=win32print.OpenPrinter(name)
    dc=None; started=False
    try:
        info=win32print.GetPrinter(handle,2); dm=info['pDevMode']
        dm.Copies=1; dm.Fields |= 0x100
        # Respect the installed driver's media/cutter/overcoat settings.
        # The validated base workflow expects its 6x4 paper form, standard cut.
        native=win32gui.CreateDC('WINSPOOL',name,dm)
        dc=win32ui.CreateDCFromHandle(native)
        width=dc.GetDeviceCaps(win32con.HORZRES); height=dc.GetDeviceCaps(win32con.VERTRES)
        dpi_x=dc.GetDeviceCaps(win32con.LOGPIXELSX); dpi_y=dc.GetDeviceCaps(win32con.LOGPIXELSY)
        inches=sorted((width/dpi_x,height/dpi_y))
        if not (3.8<inches[0]<4.3 and 5.8<inches[1]<6.3):
            raise RuntimeError('打印机当前纸张不是 6×4 英寸，请先在 DNP 打印首选项设为 (6x4)。')
        with Image.open(path) as source: image=source.convert('RGB')
        if (image.width>image.height)!=(width>height): image=image.transpose(Image.Transpose.ROTATE_90)
        # Fit to printable area without stretching or dropping template text.
        scale=min(width/image.width,height/image.height)
        w=round(image.width*scale);h=round(image.height*scale)
        x=(width-w)//2;y=(height-h)//2
        # PyCDC.StartDoc discards the native return value. StartDocW preserves
        # the actual spooler job ID, which is required for duplicate protection.
        hdc=dc.GetHandleOutput()
        document=DocInfo(C.sizeof(DocInfo),document_name,None,None,0)
        job=gdi.StartDocW(hdc,C.byref(document))
        if job<=0: raise RuntimeError('Windows 未返回有效打印任务号。')
        started=True
        if on_job: on_job(job)
        checked_gdi('StartPage',hdc)
        ImageWin.Dib(image).draw(hdc,(x,y,x+w,y+h))
        checked_gdi('EndPage',hdc);checked_gdi('EndDoc',hdc);started=False
        return {'job_id':job,'queue':name,'page_pixels':[width,height],'status':'submitted'}
    except Exception:
        if dc and started:
            try: gdi.AbortDoc(dc.GetHandleOutput())
            except Exception: pass
        raise
    finally:
        if dc: dc.DeleteDC()
        win32print.ClosePrinter(handle)

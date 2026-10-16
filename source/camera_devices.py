"""Windows camera inventory and hardware-ID-scoped Windows Update repair."""
import ctypes as C
import os
from pathlib import Path

OFFICIAL={
 'canon':'https://www.canon.com.cn/supports/',
 'sony':'https://support.d-imaging.sony.co.jp/app/sdk/en/index.html',
 'panasonic':'https://av.jpn.support.panasonic.com/support/global/cs/soft/tool/sdk.html',
 'video':'https://support.microsoft.com/windows/camera-doesn-t-work-in-windows-32adb016-b29c-a928-0073-53d31da0dad5'}

def brand_of(name,device_id=''):
    text=(name+' '+device_id).lower()
    if 'canon' in text or 'vid_04a9' in text:return 'canon'
    if any(s in text for s in ('sony','ilce-','ilme-','vid_054c')):return 'sony'
    if any(s in text for s in ('panasonic','lumix','vid_04da')):return 'panasonic'
    return 'video'

def list_video_devices():
    import pythoncom
    pythoncom.CoInitialize()
    try:
        from pygrabber.dshow_graph import FilterGraph
        graph=FilterGraph()
        return [{'index':i,'name':name,'brand':brand_of(name)} for i,name in enumerate(graph.get_input_devices())]
    finally:pythoncom.CoUninitialize()

def list_pnp_devices():
    """Read present devices through SetupAPI; no shell, driver writes or camera claim."""
    class GUID(C.Structure):_fields_=[('a',C.c_uint32),('b',C.c_uint16),('c',C.c_uint16),('d',C.c_ubyte*8)]
    class Info(C.Structure):_fields_=[('size',C.c_uint32),('guid',GUID),('instance',C.c_uint32),('reserved',C.c_size_t)]
    api=C.WinDLL('setupapi',use_last_error=True);cfg=C.WinDLL('cfgmgr32')
    api.SetupDiGetClassDevsW.argtypes=[C.c_void_p,C.c_wchar_p,C.c_void_p,C.c_uint32];api.SetupDiGetClassDevsW.restype=C.c_void_p
    api.SetupDiEnumDeviceInfo.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(Info)];api.SetupDiEnumDeviceInfo.restype=C.c_int
    api.SetupDiGetDeviceRegistryPropertyW.argtypes=[C.c_void_p,C.POINTER(Info),C.c_uint32,C.POINTER(C.c_uint32),C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32)];api.SetupDiGetDeviceRegistryPropertyW.restype=C.c_int
    api.SetupDiGetDeviceInstanceIdW.argtypes=[C.c_void_p,C.POINTER(Info),C.c_wchar_p,C.c_uint32,C.POINTER(C.c_uint32)];api.SetupDiGetDeviceInstanceIdW.restype=C.c_int
    api.SetupDiDestroyDeviceInfoList.argtypes=[C.c_void_p]
    cfg.CM_Get_DevNode_Status.argtypes=[C.POINTER(C.c_uint32),C.POINTER(C.c_uint32),C.c_uint32,C.c_uint32];cfg.CM_Get_DevNode_Status.restype=C.c_uint32
    handle=api.SetupDiGetClassDevsW(None,None,None,6)
    if handle==C.c_void_p(-1).value:raise OSError(C.get_last_error(),'Windows 相机设备检测失败')
    def prop(info,key,multi=False):
        buffer=C.create_unicode_buffer(4096);required=C.c_uint32();kind=C.c_uint32()
        if not api.SetupDiGetDeviceRegistryPropertyW(handle,C.byref(info),key,C.byref(kind),buffer,C.sizeof(buffer),C.byref(required)):return [] if multi else ''
        values=[x for x in buffer[:max(0,required.value//C.sizeof(C.c_wchar))].split('\0') if x]
        return values if multi else (values[0] if values else '')
    devices=[];index=0
    try:
        while True:
            info=Info();info.size=C.sizeof(info)
            if not api.SetupDiEnumDeviceInfo(handle,index,C.byref(info)):
                if C.get_last_error()!=259:raise OSError(C.get_last_error(),'枚举相机设备失败')
                break
            index+=1;kind=prop(info,7)
            if kind not in ('Camera','Image','WPD','USB','Unknown',''):continue
            name=prop(info,12) or prop(info,0);identifier=C.create_unicode_buffer(1024);required=C.c_uint32()
            if not api.SetupDiGetDeviceInstanceIdW(handle,C.byref(info),identifier,1024,C.byref(required)):continue
            device_id=identifier.value;brand=brand_of(name+' '+prop(info,11),device_id)
            status=C.c_uint32();problem=C.c_uint32();code=cfg.CM_Get_DevNode_Status(C.byref(status),C.byref(problem),info.instance,0)
            if kind not in ('Camera','Image','WPD') and not (brand in ('canon','sony','panasonic') and problem.value==28):continue
            devices.append({'name':name,'device_id':device_id,'class':kind or None,'status':'OK' if code==0 and problem.value==0 else 'Error','problem_code':problem.value if code==0 else None,'hardware_ids':prop(info,1,True),'compatible_ids':prop(info,2,True),'brand':brand})
    finally:api.SetupDiDestroyDeviceInfoList(handle)
    return devices

def detect_devices():
    errors=[]
    try:pnp=list_pnp_devices()
    except Exception as exc:pnp=[];errors.append(str(exc))
    try:video=list_video_devices()
    except Exception as exc:video=[];errors.append('视频设备枚举失败：'+str(exc))
    return {'pnp':pnp,'video':video,'errors':errors}

def discover_canon_sdk(configured=''):
    if configured and Path(configured).is_file():return str(Path(configured).resolve())
    roots=[Path(os.environ.get(key,'')) for key in ('ProgramFiles','ProgramFiles(x86)','LOCALAPPDATA') if os.environ.get(key)]
    candidates=[]
    for root in roots:
        candidates.extend(root.glob('像素蛋糕/pixcake*/EDSDK.dll'))
        candidates.extend(root.glob('Canon/EOS Utility/**/EDSDK.dll'))
    return str(candidates[0].resolve()) if candidates else ''

def missing_camera_devices(devices):
    # Code 28 means no driver; never update healthy devices or firmware.
    return [d for d in devices if d.get('problem_code')==28 and d.get('class') in ('Camera','Image','WPD',None,'Unknown') and d.get('brand') in ('canon','sony','panasonic')]

def matching_driver(update,devices):
    if str(getattr(update,'Type','')).lower() not in ('2','driver'):return False
    if str(getattr(update,'DriverClass','')).lower() not in ('camera','image','wpd'):return False
    hardware=str(getattr(update,'DriverHardwareID','')).upper()
    return bool(hardware) and 'VID_' in hardware and any(hardware in {str(v).upper() for v in d.get('hardware_ids',[])+d.get('compatible_ids',[])} for d in devices)

def repair_missing_drivers(devices=None,progress=None):
    import pythoncom,win32com.client
    pythoncom.CoInitialize()
    tell=progress or (lambda text:None)
    try:
        targets=missing_camera_devices(list_pnp_devices() if devices is None else devices)
        if not targets:return {'status':'not_needed','message':'相机设备未报告缺失驱动；无需下载安装。','installed':[]}
        tell('正在通过 Windows Update 匹配缺失的相机驱动…')
        session=win32com.client.Dispatch('Microsoft.Update.Session');session.ClientApplicationID='BeautyPhotoBooth'
        found=session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Driver'")
        collection=win32com.client.Dispatch('Microsoft.Update.UpdateColl');chosen=[]
        for i in range(found.Updates.Count):
            update=found.Updates.Item(i)
            try:driver=win32com.client.CastTo(update,'IWindowsDriverUpdate')
            except Exception:continue
            if not matching_driver(driver,targets):continue
            if update.InstallationBehavior.CanRequestUserInput:continue
            if not update.EulaAccepted:update.AcceptEula()
            collection.Add(update);chosen.append(str(update.Title))
        if not chosen:return {'status':'not_found','message':'Windows Update 未找到精确匹配的相机驱动。请使用官方安装入口；遥控 SDK 需要另行取得。','installed':[]}
        tell('正在下载相机驱动：'+'、'.join(chosen))
        downloader=session.CreateUpdateDownloader();downloader.Updates=collection;download=downloader.Download()
        if int(download.ResultCode)!=2:raise RuntimeError('Windows 驱动下载未全部成功，请检查网络后重试。')
        tell('正在安装匹配的相机驱动…')
        installer=session.CreateUpdateInstaller();installer.Updates=collection;result=installer.Install()
        succeeded=[chosen[i] for i in range(len(chosen)) if int(result.GetUpdateResult(i).ResultCode)==2]
        status='installed' if len(succeeded)==len(chosen) else 'partial'
        return {'status':status,'message':'相机驱动安装完成。' if status=='installed' else '部分相机驱动未安装成功，请查看 Windows 更新。','installed':succeeded,'reboot_required':bool(result.RebootRequired)}
    finally:pythoncom.CoUninitialize()

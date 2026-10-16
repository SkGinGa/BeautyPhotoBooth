import copy,json,sys,tempfile,threading,time,unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import numpy as np
from PIL import Image,ImageDraw,ImageOps
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'source'))
import asset_library as library,processing,camera_devices as devices,camera_backends as backends
from template_geometry import crop_photo,move_box,resize_box
from preview_geometry import PreviewTransform

class NewReleaseTests(unittest.TestCase):
    def panel(self,transparent):
        image=Image.new('RGBA',(900,600),'#236599');draw=ImageDraw.Draw(image)
        for box in ((50,50,400,400),(500,50,850,400)):draw.rectangle(box,fill=(0,0,0,0) if transparent else 'white')
        return image

    def test_transparent_and_opaque_panels_detect_and_count(self):
        for transparent in (True,False):
            with self.subTest(transparent=transparent),tempfile.TemporaryDirectory() as tmp:
                image=self.panel(transparent);source=Path(tmp)/'template.png';image.save(source);before=source.read_bytes()
                result=library.import_template(tmp,source)
                self.assertEqual(result['shots'],2);self.assertTrue(library.template_ready(result));self.assertEqual(source.read_bytes(),before)
                with Image.open(library.asset_path(tmp,result['overlay'])) as overlay:self.assertEqual(overlay.getpixel((100,100))[3],0)

    def test_plain_white_page_is_not_invented_as_photo_frame(self):
        self.assertEqual(library.detect_template_slots(Image.new('RGBA',(900,600),'white'))[0],[])

    def test_move_and_resize_stay_inside_template(self):
        self.assertEqual(move_box([100,100,300,200],900,-500,(900,600)),[600,0,300,200])
        self.assertEqual(resize_box([100,100,300,200],20,900,(900,600)),[100,100,20,500])

    def test_vacated_aperture_filled_and_new_window_cut(self):
        image=self.panel(True);before=image.tobytes();original=library.detect_slots(image)
        slots=copy.deepcopy(original);slots[0]['box']=[90,120,250,220];slots[0]['cutout']=True
        overlay=library.prepare_overlay(image,slots)
        self.assertEqual(overlay.getpixel((60,60)),(35,101,153,255));self.assertEqual(overlay.getpixel((100,150))[3],0)
        self.assertEqual(overlay.getpixel((510,60))[3],0);self.assertEqual(image.tobytes(),before)

    def test_content_pan_zoom_and_default_crop(self):
        image=Image.fromarray(np.tile(np.arange(200,dtype=np.uint8),(100,1)),'L').convert('RGB')
        expected=ImageOps.fit(image,(80,80),Image.Resampling.LANCZOS,centering=(.5,.42))
        self.assertTrue(np.array_equal(np.asarray(expected),np.asarray(crop_photo(image,(80,80)))))
        self.assertLess(crop_photo(image,(80,80),(0,.5),2).getpixel((40,40))[0],crop_photo(image,(80,80),(1,.5),2).getpixel((40,40))[0])

    def test_custom_crop_and_touch_af_share_live_geometry(self):
        transform=PreviewTransform((1500,1000),(900,700),1.5,True,centering=(.2,.8),zoom=2)
        self.assertEqual(transform.crop,(150,400,900,900))
        x,y,w,h=transform.display;target=transform.to_camera(x+w*.3,y+h*.6)
        point=transform.to_canvas(*target)
        self.assertAlmostEqual(point[0],x+w*.3);self.assertAlmostEqual(point[1],y+h*.6)

    def test_photo_placement_saved_in_revision_without_source_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'template.png';self.panel(True).save(source);template=library.import_template(tmp,source)
            path=library.asset_path(tmp,template['source_overlay']);before=path.read_bytes();slots=copy.deepcopy(template['slots']);slots[0].update(centering=[.2,.8],zoom=1.75)
            revision=library.revise_template(tmp,template,'修改位置',slots)
            self.assertEqual(revision['slots'][0]['centering'],[.2,.8]);self.assertEqual(path.read_bytes(),before)
            with Image.open(library.asset_path(tmp,revision['overlay'])) as a,Image.open(path) as b:self.assertEqual(a.tobytes(),b.tobytes())

    def test_invalid_crop_parameters_rejected(self):
        for value in (([-1,.5],1),([.5,.5],4)):
            with self.assertRaises(ValueError):library.validate_slots([900,600],[{'box':[0,0,100,100],'photo':0,'centering':value[0],'zoom':value[1]}])

    def test_driver_match_only_camera_exact_hardware(self):
        target={'hardware_ids':['USB\\VID_054C&PID_1234'],'compatible_ids':['USB\\CLASS_06']}
        update=SimpleNamespace(Type=2,DriverClass='Camera',DriverHardwareID='usb\\vid_054c&pid_1234')
        self.assertTrue(devices.matching_driver(update,[target]))
        for prop,value in (('DriverClass','Firmware'),('DriverHardwareID','USB\\VID_054C&PID_9999'),('DriverHardwareID','USB\\CLASS_06')):
            changed=copy.copy(update);setattr(changed,prop,value);self.assertFalse(devices.matching_driver(changed,[target]))

    def test_healthy_driver_is_never_downloaded_or_updated(self):
        healthy={'brand':'canon','class':'WPD','problem_code':0}
        import win32com.client
        with mock.patch.object(win32com.client,'Dispatch') as dispatch:
            self.assertEqual(devices.repair_missing_drivers([healthy])['status'],'not_needed');dispatch.assert_not_called()

    def test_auto_does_not_choose_laptop_camera(self):
        hub=backends.CameraHub({'camera_backend':'auto','sdk_path':''})
        with mock.patch.object(backends,'detect_devices',return_value={'pnp':[],'video':[{'name':'Integrated Camera','brand':'video','index':0}]}),mock.patch.object(backends,'discover_canon_sdk',return_value=''):
            with self.assertRaises(backends.CameraError):hub.execute('connect',{})
            self.assertIsNone(hub.child)
        hub.pool.shutdown()

    def test_video_frame_keeps_resolution_and_supports_chinese_path(self):
        camera=backends.VideoCamera.__new__(backends.VideoCamera);camera.lock=threading.Lock();camera.connected=True;camera.frame=(time.monotonic(),b'',{})
        camera.bgr=np.full((720,1280,3),(10,50,190),np.uint8)
        with tempfile.TemporaryDirectory(prefix='视频测试 ') as tmp:
            path=Path(tmp)/'照片.jpg';camera.capture(path)
            with Image.open(path) as photo:self.assertEqual(photo.size,(1280,720));self.assertLess(np.abs(np.array(photo.getpixel((50,50)))-[190,50,10]).max(),4)
            camera.frame=(time.monotonic()-5,b'',{})
            with self.assertRaises(backends.CameraError):camera.capture(Path(tmp)/'stale.jpg')

    def test_new_name_title_and_default_logo_alpha(self):
        config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['app_name'],'美颜自拍亭');self.assertEqual(config['preview_title'],'拍照免费，删一张50（本标题可编辑）')
        with Image.open(ROOT/'assets/logo-placeholder.png') as logo:self.assertEqual(logo.mode,'RGBA');self.assertEqual(logo.getchannel('A').getextrema(),(0,255))
        info=(ROOT/'tools/version_info.txt').read_text(encoding='utf-8');self.assertIn('爱打游戏的Arweil',info);self.assertIn('759377831',info)

if __name__=='__main__':unittest.main(verbosity=2)

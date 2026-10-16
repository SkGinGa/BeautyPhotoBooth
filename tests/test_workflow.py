"""Integration checks use synthetic inputs and never submit a print job."""
import json,sys,unittest,tempfile,time,threading,struct
from pathlib import Path
from unittest import mock
from PIL import Image
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
import processing,printer,app
from camera import CanonCamera,CameraError,decode_focus_info,focus_status
from preview_geometry import PreviewTransform
from concurrent.futures import Future

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))

    def test_all_five_templates_keep_photo_order_and_artwork(self):
        colors=['#8cbed6','#e7bc8a','#bd94cf','#aacb85']
        for template in self.config['templates']:
            with self.subTest(template=template['id']):
                photos=[Image.new('RGB',(1500,1000),colors[i]) for i in range(template['shots'])]
                output=processing.compose(template,ROOT/'assets',photos)
                self.assertEqual(output.size,tuple(template['canvas']))
                for slot in template['slots']:
                    x,y,w,h=slot['box'];center=(x+w//2,y+h//2)
                    self.assertEqual(output.getpixel(center),photos[slot['photo']].getpixel((0,0)))
                overlay=Image.open(ROOT/'assets'/template['overlay']).convert('RGBA')
                self.assertEqual(output.getpixel((3,3)),overlay.convert('RGB').getpixel((3,3)))

    def test_wrong_shot_count_is_rejected(self):
        template=self.config['templates'][1]
        with self.assertRaises(ValueError):processing.compose(template,ROOT/'assets',[Image.new('RGB',(100,100))])

    def test_zero_strength_preserves_pixels_and_on_lifts_shadows(self):
        photo=Image.new('RGB',(400,300),'#123456')
        result,meta=processing.beautify(photo,0)
        self.assertTrue(np.array_equal(np.asarray(photo),np.asarray(result)))
        result,meta=processing.beautify(photo,.9)
        self.assertEqual(meta['faces'],0)
        self.assertGreater(np.asarray(result).mean(),np.asarray(photo).mean()+10)
        gradient=np.repeat(np.arange(256,dtype=np.uint8)[None,:,None],3,axis=2)
        toned=processing.beauty_tone(gradient,.9)[0,:,0].astype(int)
        self.assertTrue(np.all(np.diff(toned)>=0));self.assertEqual(toned[0],0);self.assertEqual(toned[-1],255)
        self.assertGreater(toned[64]-64,toned[240]-240)



    def test_complete_processing_pipeline(self):
        for template in self.config['templates']:
            with self.subTest(template=template['id']),tempfile.TemporaryDirectory(dir=None) as folder:
                folder=Path(folder);inputs=[]
                for i in range(template['shots']):
                    filename=folder/f'original_{i}.jpg';processing.sample_photo(i).save(filename);inputs.append(str(filename))
                before=[Path(x).read_bytes() for x in inputs]
                final,notes=processing.process_session(ROOT,folder,template,inputs,self.config)
                with Image.open(final) as result:
                    self.assertEqual(result.size,tuple(template['canvas']));self.assertEqual(result.info['dpi'],(300,300))
                    self.assertTrue(result.info.get('icc_profile'))
                self.assertEqual(before,[Path(x).read_bytes() for x in inputs])
                self.assertEqual(len(notes),template['shots'])
                self.assertTrue((folder/'print_original.jpg').is_file())
                self.assertEqual(Path(final).name,'print_beauty.jpg')
                with Image.open(folder/'print_original.jpg') as raw:
                    self.assertEqual(raw.size,tuple(template['canvas']))

    def test_external_timeout_never_falls_back_to_unretouched_photo(self):
        template=self.config['templates'][0]
        with tempfile.TemporaryDirectory(dir=None) as folder:
            folder=Path(folder);(folder/'assets').mkdir()
            source=folder/'original.jpg';processing.sample_photo().save(source)
            settings=dict(self.config,beauty_mode='external',external_timeout=.01)
            with self.assertRaises(TimeoutError):processing.process_session(folder,folder/'unique_session',template,[str(source)],settings)
            self.assertFalse((folder/'unique_session'/'print.jpg').exists())

    def test_wrong_printer_rejected_before_creating_job(self):
        with mock.patch.object(printer.gdi,'StartDocW') as start:
            with self.assertRaises(RuntimeError):printer.print_photo('missing.jpg','Microsoft Print to PDF','test')
            start.assert_not_called()

    def test_restore_skips_submitted_or_uncertain_and_recovers_unprinted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'data'/'sessions'
            for ident,state in [('001','not_requested'),('002','submitted'),('003','uncertain')]:
                session_dir=base/ident;session_dir.mkdir(parents=True)
                final=session_dir/'print.jpg';Image.new('RGB',(10,10)).save(final)
                data={'id':ident,'status':'print_error','print_state':state,'template':'one','final':str(final)}
                (session_dir/'session.json').write_text(json.dumps(data),encoding='utf-8')
            booth=app.Booth.__new__(app.Booth);booth.demo=False;booth.templates=[{'id':'one'}]
            booth.save_session=mock.Mock();booth.show_preview=mock.Mock()
            with mock.patch.object(app,'ROOT',root):booth.restore_latest_preview()
            self.assertEqual(booth.session['id'],'001');booth.show_preview.assert_called_once()

    def test_restore_rejects_final_outside_its_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);session_dir=root/'data'/'sessions'/'001';session_dir.mkdir(parents=True)
            final=root/'foreign.jpg';Image.new('RGB',(10,10)).save(final)
            data={'id':'001','status':'preview','print_state':'not_requested','template':'one','final':str(final)}
            (session_dir/'session.json').write_text(json.dumps(data),encoding='utf-8')
            booth=app.Booth.__new__(app.Booth);booth.demo=False;booth.templates=[{'id':'one'}];booth.root=None
            booth.show_preview=mock.Mock()
            with mock.patch.object(app,'ROOT',root),mock.patch.object(app.messagebox,'showinfo') as info:booth.restore_latest_preview()
            booth.show_preview.assert_not_called();info.assert_called_once()

    def test_camera_disconnected_or_tainted_cannot_trigger(self):
        camera=CanonCamera.__new__(CanonCamera);camera.connected=False;camera.tainted=False;camera.pending=None
        with self.assertRaises(CameraError):camera.capture('x.jpg',Future())
        camera.connected=True;camera.tainted=True
        with self.assertRaises(CameraError):camera.capture('x.jpg',Future())

    def test_camera_refuses_overlapping_shots(self):
        camera=CanonCamera.__new__(CanonCamera);camera.connected=True;camera.tainted=False;camera.pending={'path':'old.jpg'}
        with self.assertRaises(CameraError):camera.capture('new.jpg',Future())

    def test_touch_mapping_mirror_crop_letterbox_and_inverse(self):
        for mirror in (True,False):
            for aspect in (.75,1.23,2.0):
                transform=PreviewTransform((960,640),(1400,500),aspect,mirror,coord=(6000,4000),visible=(0,0,6000,4000))
                dx,dy,w,h=transform.display
                self.assertIsNone(transform.to_camera(dx-5,dy+h/2))
                left=transform.to_camera(dx+w*.2,dy+h*.3);right=transform.to_camera(dx+w*.8,dy+h*.3)
                self.assertEqual(left[0]>right[0],mirror)
                cx,cy=transform.to_canvas(*left)
                self.assertAlmostEqual(cx,dx+w*.2);self.assertAlmostEqual(cy,dy+h*.3)
                _,top,_,bottom=transform.crop
                self.assertAlmostEqual(left[1],(top+.3*(bottom-top))/640)

    def test_visible_sensor_crop_is_included_in_af_mapping(self):
        transform=PreviewTransform((960,640),(960,640),1.5,False,(6000,4000),(600,400,4800,3200))
        dx,dy,w,h=transform.display
        self.assertAlmostEqual(transform.to_camera(dx,dy)[0],.1)
        self.assertAlmostEqual(transform.to_camera(dx+w,dy+h)[1],.9)

    def test_focus_metadata_and_unknown_status_never_confirm(self):
        raw=struct.pack('<4iI',0,0,6000,4000,1)+struct.pack('<3I4iI',1,1,17,1584,1784,432,432,0)+b'\0'*4
        point=decode_focus_info(raw)[0]
        self.assertAlmostEqual(point['box'][0]+point['box'][2]/2,.3)
        self.assertEqual(focus_status(point['status']),'focused')
        for value in (0,16,20,35,99):self.assertEqual(focus_status(value),'unknown')
        self.assertEqual(focus_status(18),'failed')
        self.assertEqual(decode_focus_info(raw[:25]),[])

    def test_switch_selects_exact_print_file_and_locks_during_submission(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            for filename in ('print_original.jpg','print_beauty.jpg'):Image.new('RGB',(20,20)).save(folder/filename)
            booth=app.Booth.__new__(app.Booth);booth.state='preview';booth.demo=False;booth.session_dir=folder
            booth.session={'id':'test','print_state':'not_requested','beauty_enabled':True,'final':str(folder/'print_beauty.jpg')}
            booth.config={'printer':'DP-DS620'};booth.save_session=mock.Mock();booth.refresh_preview=mock.Mock()
            booth.print_btn=mock.Mock();booth.retake_btn=mock.Mock();booth.beauty_btn=mock.Mock();booth.preview_note=mock.Mock()
            booth.background=lambda work,*callbacks:work()
            booth.toggle_beauty();self.assertFalse(booth.session['beauty_enabled'])
            with mock.patch.object(printer,'print_photo',return_value={'job_id':123}) as submit:
                booth.print_current()
                self.assertEqual(Path(submit.call_args.args[0]).name,'print_original.jpg')
            booth.toggle_beauty();self.assertFalse(booth.session['beauty_enabled'])
            self.assertEqual(booth.session['print_state'],'submitting')

    def test_camera_cannot_capture_during_focus(self):
        camera=CanonCamera.__new__(CanonCamera);camera.connected=True;camera.tainted=False;camera.pending=None;camera.focus_pending={'phase':'focusing'}
        with self.assertRaises(CameraError):camera.capture('new.jpg',Future())

    def test_focus_timeout_releases_af_and_does_not_report_success(self):
        camera=CanonCamera.__new__(CanonCamera);camera.sdk=mock.Mock();camera.sdk.EdsSendCommand.return_value=0;camera.camera=1
        future=Future();camera.focus_pending={'future':future,'phase':'focusing','armed':True,'target':(.4,.5),'deadline':time.monotonic()-1}
        camera.advance_focus()
        self.assertEqual(future.result()['state'],'unconfirmed');self.assertIsNone(camera.focus_pending)
        camera.sdk.EdsSendCommand.assert_called_once_with(1,0x102,0)

if __name__=='__main__':unittest.main(verbosity=2)

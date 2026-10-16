import copy,json,sys,tempfile,unittest,shutil,threading
from pathlib import Path
from unittest import mock
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
import asset_library as library
import processing,portrait_effects
from camera import CanonCamera,CameraError

class BrandingTests(unittest.TestCase):
    def test_models_load_from_chinese_directory(self):
        with tempfile.TemporaryDirectory(prefix='中文模型 ') as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'assets/models',root/'assets/models')
            with mock.patch.object(processing,'APP_ROOT',root),mock.patch.object(processing,'_detectors',threading.local()),mock.patch.object(portrait_effects,'_models',None):
                try:
                    image=processing.sample_photo();result,meta=processing.beautify(image,.9)
                    self.assertEqual(result.size,image.size);self.assertEqual(meta['strength'],.9)
                    self.assertIsNotNone(portrait_effects._models)
                finally:
                    if portrait_effects._models:
                        for model in portrait_effects._models:model.close()

    def test_transparency_source_unchanged_and_unique_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'logo.png';Image.new('RGBA',(2100,300),(5,83,148,96)).save(source);before=source.read_bytes()
            first=library.import_logo(root,source);second=library.import_logo(root,source)
            self.assertNotEqual(first,second);self.assertEqual(before,source.read_bytes())
            with Image.open(library.asset_path(root,first)) as im:
                self.assertEqual(im.mode,'RGBA');self.assertEqual(im.getpixel((100,30))[3],96);self.assertLessEqual(im.width,1800)

    def test_draft_and_cancel_do_not_change_persisted_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);assets=root/'assets';assets.mkdir();Image.new('RGBA',(100,30)).save(assets/'logo-placeholder.png')
            config=library.migrate_config({'templates':[],'covers':[]})
            library.save_library(root,config);before=(root/'config.json').read_bytes()
            draft=copy.deepcopy(config);source=root/'new.png';Image.new('RGB',(300,80),'red').save(source)
            draft['brand_logo']=library.import_logo(root,source)
            self.assertEqual((root/'config.json').read_bytes(),before);self.assertEqual(config['brand_logo'],'logo-placeholder.png')
            saved=library.save_library(root,draft)
            self.assertEqual(saved['brand_logo'],draft['brand_logo']);self.assertEqual(json.loads((root/'config.json').read_text(encoding='utf-8'))['brand_logo'],draft['brand_logo'])

    def test_invalid_logo_rejected_before_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=library.migrate_config({'templates':[],'covers':[]})
            for name in ('missing.png','../../private.png'):
                config['brand_logo']=name
                with self.assertRaises(ValueError):library.save_library(tmp,config)
                self.assertFalse((Path(tmp)/'config.json').exists())

    def test_tiny_logo_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'tiny.png';Image.new('RGB',(5,100)).save(path)
            with self.assertRaises(ValueError):library.import_logo(tmp,path)

    def test_missing_sdk_does_not_disconnect_working_camera(self):
        camera=CanonCamera.__new__(CanonCamera);camera.disconnect=mock.Mock()
        with self.assertRaises(CameraError):camera.configure('missing/EDSDK.dll')
        camera.disconnect.assert_not_called()

    def test_configure_sdk_releases_old_session_on_owner_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'EDSDK.dll';path.touch()
            camera=CanonCamera.__new__(CanonCamera);camera.pending=None;camera.focus_pending=None;camera.sdk_path=Path('old/EDSDK.dll')
            camera.initialized=True;camera.sdk=mock.Mock();old=camera.sdk;camera.disconnect=mock.Mock();camera.connect=mock.Mock(return_value='test')
            self.assertEqual(camera.configure(str(path)),'test');camera.disconnect.assert_called_once();old.EdsTerminateSDK.assert_called_once()
            self.assertEqual(camera.sdk_path,path);self.assertFalse(camera.initialized);camera.connect.assert_called_once()

if __name__=='__main__':unittest.main(verbosity=2)

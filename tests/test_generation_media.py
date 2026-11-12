"""Offline checks for production media gates and complete narration."""
import importlib.util
import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec = importlib.util.spec_from_file_location('retrobyte_generation', ROOT/'retrobyte_discovery_video.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class GenerationMediaTests(unittest.TestCase):
    def test_real_probe_and_complete_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            video, short, audio, result = [tmp/n for n in ('base.mp4','short.mp4','voice.wav','complete.mp4')]
            def run(*args):
                return subprocess.run(args, capture_output=True, check=True).stdout
            for path, duration in ((video,'3.2'),(short,'0.8')):
                run('ffmpeg','-y','-v','error','-f','lavfi','-i',
                    f'testsrc2=size=360x360:rate=30:duration={duration}',
                    '-c:v','libx264','-preset','ultrafast','-threads','1',str(path))
            self.assertTrue(m._probe_ok_for_reels(video))
            self.assertFalse(m._probe_ok_for_reels(short))
            run('ffmpeg','-y','-v','error','-f','lavfi','-i',
                'sine=frequency=440:duration=8',str(audio))
            self.assertEqual(m.mux_audio_cc(video,audio,'Full narration',result),str(result))
            data=json.loads(run('ffprobe','-v','error','-show_entries',
                                'format=duration:stream=codec_type,duration','-of','json',str(result)))
            self.assertAlmostEqual(float(data['format']['duration']),8,delta=.15)
            for stream in data['streams']:
                self.assertGreaterEqual(float(stream['duration']),7.9)
            self.assertTrue(m._probe_ok_for_reels(result))

    def test_success_cleanup_removes_only_legacy_generated_png_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=root/'disc_frame_old_art.png'; old.write_bytes(b'preview')
            unrelated=root/'retrobyte-video-model.png'; unrelated.write_bytes(b'source')
            record=root/'generation.json'; record.write_text('{}')
            removed=m.cleanup_stale_generated_images(root)
            self.assertEqual(removed,['disc_frame_old_art.png'])
            self.assertFalse(old.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(record.exists())

    def test_unmapped_topic_never_uses_filename_only_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'unlisted_connector.jpg').write_bytes(b'wrong image')
            with mock.patch.object(m,'IMG_CACHE',root):
                self.assertIsNone(m.fetch_artifact('The VGA Cable'))

    def test_reference_requires_catalog_source_and_matching_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            payload=b'curated reference'
            image=root/'game-boy.jpg';image.write_bytes(payload)
            import hashlib
            (root/'manifest.json').write_text(json.dumps({'subjects':{
                'the game boy':{
                    'file':'game-boy.jpg',
                    'source':'File:Nintendo-Game-Boy-FL.jpg',
                    'sha256':hashlib.sha256(payload).hexdigest()
                }
            }}))
            with mock.patch.object(m,'IMG_CACHE',root):
                self.assertEqual(m.fetch_artifact('The Game Boy'),image)
                data=json.loads((root/'manifest.json').read_text())
                data['subjects']['the game boy']['sha256']='0'*64
                (root/'manifest.json').write_text(json.dumps(data))
                self.assertIsNone(m.fetch_artifact('The Game Boy'))

    def test_diffusion_prompt_cannot_leak_subject_hardware_into_background(self):
        from retrobyte_still_scene import scene_prompt
        prompt=scene_prompt('The VGA Cable').casefold()
        self.assertNotIn('vga',prompt)
        self.assertNotIn('cable',prompt)
        self.assertNotIn('connector',prompt)

if __name__=='__main__':
    unittest.main()

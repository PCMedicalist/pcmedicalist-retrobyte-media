"""Offline checks for production media gates and complete narration."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
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

if __name__=='__main__':
    unittest.main()

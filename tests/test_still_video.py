"""Verify fixed background, avatar motion, caption pixels, and full narration."""
import sys
import tempfile
import subprocess
import json
import unittest
from pathlib import Path
from PIL import Image, ImageChops, ImageStat, ImageFont
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from retrobyte_still_video import render, caption_cues, CAPTION_BOX, BOLD
import retrobyte_discovery_video as generator

class StillVideoTests(unittest.TestCase):
    def test_script_is_grounded(self):
        script=generator.narrate('The PalmPilot','A pocket organizer for notes.',0)
        self.assertIn('PalmPilot',script);self.assertIn('appointments and notes',script)
        self.assertNotIn('kitchen',script.casefold())
        cues=caption_cues(script,10)
        self.assertEqual(cues[0]['start'],0);self.assertEqual(cues[-1]['end'],10)
        self.assertEqual(' '.join(c['text'] for c in cues),script)

    def test_real_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);scene=tmp/'scene.png';audio=tmp/'voice.wav';output=tmp/'out.mp4'
            Image.new('RGB',(640,640),(40,66,82)).save(scene)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3.2',str(audio)],capture_output=True,check=True)
            avatar=Path('/home/pcmedicalist/pcmedicalist/pcmedicalist-retrobyte/images/brand/retrobyte-video-avatar.mp4')
            result=render(scene,audio,avatar,'The PalmPilot','Pocket notes','Your calendar had a stylus. Would you use one today?',output)
            import textwrap
            for cue in result['caption_cues']:
                font=ImageFont.truetype(BOLD,cue['font_px'])
                self.assertLessEqual(max(font.getlength(line) for line in textwrap.wrap(cue['text'],38)),900)
            meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,width,height','-of','json',str(output)]))
            self.assertAlmostEqual(float(meta['format']['duration']),3.2,delta=.12)
            self.assertEqual(len([s for s in meta['streams'] if s['codec_type']=='video']),1)
            self.assertEqual((meta['streams'][0]['width'],meta['streams'][0]['height']),(1080,1920))
            frames=[]
            for sec in (.4,2.4):
                path=tmp/f'{sec}.png';subprocess.run(['ffmpeg','-v','error','-ss',str(sec),'-i',str(output),'-frames:v','1',str(path)],capture_output=True,check=True);frames.append(Image.open(path).convert('RGB'))
            # Device scene has no pan, zoom or animation outside the avatar.
            diff=ImageChops.difference(frames[0].crop((100,450,590,1000)),frames[1].crop((100,450,590,1000)))
            self.assertLess(sum(ImageStat.Stat(diff).mean),3)
            # Avatar changes across time, while the background stays fixed.
            diff=ImageChops.difference(frames[0].crop((620,1100,1010,1600)),frames[1].crop((620,1100,1010,1600)))
            self.assertGreater(sum(ImageStat.Stat(diff).mean),1)
            pixels=list(frames[0].crop((70,1680,1010,1805)).getdata())
            self.assertGreater(sum(1 for r,g,b in pixels if min(r,g,b)>200),500)

if __name__=='__main__':unittest.main()

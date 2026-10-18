"""Approved-format contract and rejection tests. No network or social posting."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from retrobyte_video_format import FORMAT, PROFILE_SHA256, file_hash, title_layout, caption_layout, detail_layout, validate_render, validate_publish_manifest
from retrobyte_still_video import caption_cues
import retrobyte_discovery_video as generator

class FormatContractTests(unittest.TestCase):
    def approved_provenance(self):
        script=generator.narrate('The PalmPilot','',0)
        cues=caption_cues(script,10.224)
        for cue in cues:
            cue['font_px']=caption_layout(cue['text'])[1]
        return {'duration':10.224,'narration':script,'voice':'kokoro-onnx:am_fenrir',
                'avatar_source':FORMAT['avatar']['path'],'avatar_sha256':FORMAT['avatar']['sha256'],
                'avatar_box':FORMAT['avatar']['box'],'caption_box':FORMAT['captions']['box'],
                'scene_mode':FORMAT['scene']['mode'],'caption_cues':cues}

    def test_approved_reference_passes_and_is_still_the_same_video(self):
        path=ROOT/FORMAT['approved_reference']['file']
        self.assertEqual(file_hash(path),FORMAT['approved_reference']['sha256'])
        qa=validate_render(path,self.approved_provenance())
        self.assertTrue(qa['passed'])
        self.assertEqual(qa['profile_sha256'],PROFILE_SHA256)
        manifest={'video':str(path),'format_validation':qa,'voice':'kokoro-onnx:am_fenrir',
                  'avatar_sha256':FORMAT['avatar']['sha256'],'scene_mode':FORMAT['scene']['mode'],
                  'captions_burned':True}
        validate_publish_manifest(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            replaced=Path(tmp)/'changed.mp4';replaced.write_bytes(b'changed after validation')
            changed=dict(manifest,video=str(replaced))
            with self.assertRaisesRegex(ValueError,'bytes changed'):
                validate_publish_manifest(changed)
        with self.assertRaisesRegex(ValueError,'current approved'):
            validate_publish_manifest({'video':str(path)})
        changed=copy.deepcopy(manifest);changed['format_validation']['profile_sha256']='stale'
        with self.assertRaisesRegex(ValueError,'current approved'):
            validate_publish_manifest(changed)

    def test_wrong_voice_avatar_and_missing_captions_are_rejected(self):
        path=ROOT/FORMAT['approved_reference']['file']
        for key,value,reason in [('voice','remote:other','Fenrir'),('avatar_sha256','changed','Avatar provenance'),('caption_cues',[],'complete narration'),('scene_mode','text_to_video','stationary')]:
            p=self.approved_provenance();p[key]=value
            with self.assertRaisesRegex(ValueError,reason):validate_render(path,p)

    def test_motion_background_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'moving.mp4'
            subprocess.run(['ffmpeg','-y','-v','error','-i',str(ROOT/FORMAT['approved_reference']['file']),
                '-vf',"crop=960:1920:x='20+8*t':y=0,scale=1080:1920",'-c:v','libx264',
                '-preset','ultrafast','-threads','2','-pix_fmt','yuv420p','-c:a','copy',str(path)],capture_output=True,check=True)
            with self.assertRaisesRegex(ValueError,'Scene motion'):
                validate_render(path,self.approved_provenance())

    def test_all_catalog_titles_and_captions_fit_without_lost_words(self):
        for title,angle in generator.load_episodes():
            text=title.removeprefix('The ').upper()
            lines,size=title_layout(text)
            self.assertEqual(' '.join(lines),text)
            self.assertLessEqual(len(lines),2)
            details,detail_px,spacing=detail_layout(title,angle)
            if 'palmpilot' not in title.casefold():
                self.assertEqual(' '.join(details),angle)
            self.assertLessEqual(len(details)*spacing,145)
            script=generator.narrate(title,angle)
            cues=caption_cues(script,12)
            self.assertEqual(' '.join(c['text'] for c in cues),script)
            for cue in cues:
                body,font=caption_layout(cue['text'])
                self.assertLessEqual(len(body.splitlines()),2)
                self.assertGreaterEqual(font,FORMAT['captions']['min_font_px'])

if __name__=='__main__':unittest.main()

"""Approved RetroByte video format and mandatory pre-publication checks."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / 'retrobyte_video_format.json'
FORMAT = json.loads(PROFILE_PATH.read_text())
PROFILE_SHA256 = hashlib.sha256(PROFILE_PATH.read_bytes()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def caption_layout(text):
    """Wrap and fit cue text without clipping, shrinking below policy, or dropping words."""
    from PIL import ImageFont
    import textwrap
    policy = FORMAT['captions']
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    for size in range(policy['max_font_px'], policy['min_font_px'] - 1, -1):
        font = ImageFont.truetype(font_path, size)
        lines = textwrap.wrap(text, 38)
        if len(lines) <= policy['max_lines'] and max((font.getlength(line) for line in lines), default=0) <= policy['max_line_width']:
            return '\n'.join(lines), size
    raise ValueError('Caption cannot fit the approved readable caption band')


def title_layout(text):
    """Fit the complete subject into the two-line title region without truncation."""
    from PIL import ImageFont
    for size in range(72, 39, -1):
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', size)
        lines = ['']
        for word in text.split():
            candidate = (lines[-1] + ' ' + word).strip()
            if font.getlength(candidate) <= 952:
                lines[-1] = candidate
            else:
                lines.append(word)
        if len(lines) <= 2 and all(font.getlength(line) <= 952 for line in lines):
            return lines, size
    raise ValueError('Subject does not fit the approved title band')


def detail_layout(subject, angle):
    """Keep on-screen facts specific to each catalog episode and fully visible."""
    from PIL import ImageFont
    if 'palmpilot' in subject.casefold():
        return ['Appointments.', 'Pocket notes.'], 44, 60
    policy = FORMAT['details'];x0,y0,x1,y1 = policy['box']
    for size in range(policy['max_font_px'],policy['min_font_px']-1,-1):
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',size)
        lines=['']
        for word in angle.split():
            candidate=(lines[-1]+' '+word).strip()
            if font.getlength(candidate)<=x1-x0:
                lines[-1]=candidate
            else:
                lines.append(word)
        spacing=round(size*1.2)
        if len(lines)<=policy['max_lines'] and len(lines)*spacing<=y1-y0 and all(font.getlength(line)<=x1-x0 for line in lines):
            return lines,size,spacing
    raise ValueError('Catalog fact does not fit the approved fact panel')


def validate_render(video, provenance):
    """Reject format drift before staging: inspect the actual encoded video."""
    if provenance.get('voice') != FORMAT['voice']['backend'] + ':' + FORMAT['voice']['id']:
        raise ValueError('Approved local Fenrir narration is required')
    if provenance.get('scene_mode') != FORMAT['scene']['mode']:
        raise ValueError('A stationary local diffusion scene is required')
    avatar = Path(provenance.get('avatar_source', ''))
    if not avatar.is_file() or file_hash(avatar) != FORMAT['avatar']['sha256']:
        raise ValueError('The approved animated RetroByte avatar is required')
    if provenance.get('avatar_sha256') != FORMAT['avatar']['sha256']:
        raise ValueError('Avatar provenance does not match the approved source')
    if list(provenance.get('avatar_box', [])) != FORMAT['avatar']['box'] or list(provenance.get('caption_box', [])) != FORMAT['captions']['box']:
        raise ValueError('Avatar/caption placement differs from the approved layout')
    cues = provenance.get('caption_cues') or []
    if not cues or ' '.join(c['text'] for c in cues) != provenance.get('narration'):
        raise ValueError('Captions must preserve the complete narration')
    for cue in cues:
        _, font_px = caption_layout(cue['text'])
        if cue.get('font_px') != font_px:
            raise ValueError('Caption typography differs from the approved readable layout')
    data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate,duration','-of','json',str(video)]))
    videos = [s for s in data['streams'] if s['codec_type'] == 'video']
    audios = [s for s in data['streams'] if s['codec_type'] == 'audio']
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError('Exactly one video and one narration stream are required')
    v, a = videos[0], audios[0]
    fps = v['avg_frame_rate'].split('/')
    if (v['width'],v['height']) != (FORMAT['canvas']['width'],FORMAT['canvas']['height']) or abs(float(fps[0])/float(fps[1])-FORMAT['canvas']['fps']) > .01 or v['codec_name'] != 'h264' or a['codec_name'] != 'aac':
        raise ValueError('Approved output is 1080x1920, 30 fps, H.264/AAC')
    duration = float(data['format']['duration'])
    if duration < 3 or abs(float(a['duration'])-duration) > FORMAT['quality']['max_audio_video_duration_difference'] or abs(duration-float(provenance['duration'])) > .2:
        raise ValueError('Complete narration must cover the video duration')
    if abs(cues[0]['start']) > .01 or abs(cues[-1]['end']-float(provenance['duration'])) > .02:
        raise ValueError('Captions must span the entire narration')
    for left, right in zip(cues, cues[1:]):
        if abs(left['end'] - right['start']) > .01:
            raise ValueError('Caption timeline contains a gap or overlap')
    frames = []
    with tempfile.TemporaryDirectory(prefix='retrobyte-format-') as tmp:
        for i, sec in enumerate([.35, duration*.47, duration-.35]):
            frame = Path(tmp)/f'{i}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(sec),'-i',str(video),'-frames:v','1',str(frame)],capture_output=True,check=True,timeout=30)
            with Image.open(frame) as im:
                frames.append(im.convert('RGB'))
    def difference(box):
        return max(sum(ImageStat.Stat(ImageChops.difference(frames[0].crop(box), f.crop(box))).mean)/765 for f in frames[1:])
    scene_difference = difference(tuple(FORMAT['scene']['box']))
    x,y,w,h = FORMAT['avatar']['box']
    avatar_difference = difference((x,y,x+w,y+h))
    if scene_difference > FORMAT['quality']['max_scene_difference_fraction']:
        raise ValueError('Scene motion detected; animated backgrounds are forbidden')
    if avatar_difference < FORMAT['quality']['min_avatar_difference_fraction']:
        raise ValueError('Avatar appears static instead of animated')
    for frame in frames:
        region = frame.crop(tuple(FORMAT['captions']['box']))
        pixels = region.get_flattened_data() if hasattr(region, 'get_flattened_data') else region.getdata()
        if sum(1 for rgb in pixels if min(rgb) > 200) < FORMAT['quality']['min_caption_white_pixels']:
            raise ValueError('Burned-in captions are missing or unreadable')
    return {'passed':True,'format_id':FORMAT['id'],'profile_sha256':PROFILE_SHA256,'video_sha256':file_hash(video),'scene_difference_fraction':scene_difference,'avatar_difference_fraction':avatar_difference,'duration':duration}


def validate_publish_manifest(manifest):
    """A publisher may only send bytes that passed this exact format profile."""
    qa = manifest.get('format_validation') or {}
    if qa.get('passed') is not True or qa.get('format_id') != FORMAT['id'] or qa.get('profile_sha256') != PROFILE_SHA256:
        raise ValueError('Video has not passed the current approved RetroByte format')
    path = Path(manifest.get('video', ''))
    if not path.is_file() or file_hash(path) != qa.get('video_sha256'):
        raise ValueError('Video bytes changed after format validation')
    if manifest.get('avatar_sha256') != FORMAT['avatar']['sha256'] or manifest.get('voice') != FORMAT['voice']['backend']+':'+FORMAT['voice']['id']:
        raise ValueError('Approved avatar and Fenrir voice are mandatory')
    if manifest.get('scene_mode') != FORMAT['scene']['mode'] or manifest.get('captions_burned') is not True:
        raise ValueError('Fixed background and burned captions are mandatory')
    return qa

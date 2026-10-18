"""Stationary diffusion scene, animated avatar, grounded voice, timed captions."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

COS = '/home/pcmedicalist/pcmedicalist/pcmedicalist-content-os'
if COS not in sys.path:
    sys.path.insert(0, COS)
from generators.video_avatar import overlay_avatar
from retrobyte_video_format import FORMAT, PROFILE_SHA256, caption_layout, title_layout, detail_layout

SIZE = (FORMAT['canvas']['width'], FORMAT['canvas']['height'])
AVATAR_BOX = tuple(FORMAT['avatar']['box'])
CAPTION_BOX = tuple(FORMAT['captions']['box'])
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def draw_pda(draw):
    """Interpretive PDA illustration: screen, writing panel, app keys, stylus."""
    draw.rounded_rectangle((341,450,765,1172),radius=45,fill=(3,7,12))
    draw.rounded_rectangle((325,432,749,1152),radius=40,fill=(112,119,124),outline=(198,205,209),width=5)
    draw.text((440,455),'PalmPilot',font=ImageFont.truetype(BOLD,26),fill=(23,29,33))
    draw.rounded_rectangle((358,508,716,850),radius=8,fill=(162,176,146),outline=(35,44,34),width=5)
    draw.rectangle((372,525,702,570),fill=(68,86,61))
    draw.text((385,532),'CALENDAR',font=ImageFont.truetype(BOLD,24),fill=(213,225,195))
    for index,label in enumerate(['9:00  Appointment','11:30  Write a note','14:00  Reminder']):
        y=600+index*72
        draw.text((382,y),label,font=ImageFont.truetype(FONT,23),fill=(39,55,35))
        draw.line((378,y+46,696,y+46),fill=(100,121,85),width=2)
    draw.rectangle((358,870,716,1030),fill=(72,79,82),outline=(154,162,164),width=3)
    draw.line((596,879,596,1020),fill=(146,155,159),width=2)
    draw.text((438,930),'abc',font=ImageFont.truetype(FONT,27),fill=(199,205,206))
    draw.text((629,930),'123',font=ImageFont.truetype(FONT,27),fill=(199,205,206))
    for x in [382,466,550,634]:
        draw.rounded_rectangle((x,1075,x+54,1114),radius=12,fill=(42,49,53),outline=(188,195,198),width=2)
    draw.rounded_rectangle((786,512,801,1134),radius=6,fill=(177,185,192),outline=(47,57,66),width=3)
    draw.polygon([(786,1130),(801,1130),(794,1158)],fill=(170,182,191))


def compose_frame(scene, subject, angle, output):
    canvas=Image.new('RGB',SIZE,tuple(FORMAT['colors']['background']));d=ImageDraw.Draw(canvas)
    amber=tuple(FORMAT['colors']['accent']);white=tuple(FORMAT['colors']['text']);blue=tuple(FORMAT['colors']['secondary'])
    d.text((70,110),FORMAT['copy']['header'],font=ImageFont.truetype(BOLD,35),fill=amber)
    title=subject.removeprefix('The ').upper()
    lines,title_px=title_layout(title)
    for i,line in enumerate(lines):
        d.text((64,180+i*86),line,font=ImageFont.truetype(BOLD,title_px),fill=white)
    # One fixed image: never animate, translate, zoom, or loop its camera.
    im=Image.open(scene).convert('RGB').resize((800,800),Image.Resampling.LANCZOS)
    canvas.paste(im,(140,400));d=ImageDraw.Draw(canvas)
    d.rectangle((140,400,940,1200),outline=(63,81,103),width=3)
    if 'palmpilot' in subject.casefold():
        draw_pda(d)
    d.text((76,354),FORMAT['copy']['eyebrow'],font=ImageFont.truetype(FONT,26),fill=blue)
    d.text((72,1395),'Before smartphones…' if 'palmpilot' in subject.casefold() else 'Remember this?',font=ImageFont.truetype(BOLD,37),fill=amber)
    details,detail_px,line_spacing=detail_layout(subject,angle)
    for i,line in enumerate(details):
        d.text((72,1460+i*line_spacing),line,font=ImageFont.truetype(BOLD,detail_px),fill=white)
    d.rounded_rectangle(CAPTION_BOX,radius=16,fill=(25,31,44))
    d.text((70,1850),FORMAT['copy']['cta'],font=ImageFont.truetype(FONT,32),fill=blue)
    canvas.save(output)
    return output


def caption_cues(script,duration):
    words=script.split()
    if not words or duration <= 0:
        raise ValueError('Complete narration is required for burned captions')
    limit=FORMAT['captions']['max_words_per_cue']
    groups=[' '.join(words[i:i+limit]) for i in range(0,len(words),limit)]
    cues=[];offset=0
    for group in groups:
        count=len(group.split());end=offset+count
        cues.append({'start':round(duration*offset/len(words),3),'end':round(duration*end/len(words),3),'text':group})
        offset=end
    return cues


def render(scene,audio,avatar,subject,angle,narration,output):
    output=Path(output);frame=output.with_suffix('.png')
    compose_frame(scene,subject,angle,frame)
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(audio)]))
    duration=float(meta['format']['duration'])
    cues=caption_cues(narration,duration)
    filters=[]
    for i,cue in enumerate(cues):
        body, font_px = caption_layout(cue['text'])
        text=output.with_name(output.stem+f'-cue-{i}.txt');text.write_text(body,encoding='utf-8')
        cue['font_px'] = font_px
        filters.append(f"drawtext=fontfile={BOLD}:textfile='{text}':expansion=none:fontsize={font_px}:fontcolor=white:x=(w-text_w)/2:y={FORMAT['captions']['y']}:enable='gte(t,{cue['start']})*lt(t,{cue['end']})'")
    filter_path=output.with_suffix('.filter');filter_path.write_text('[0:v]'+','.join(filters)+'[out]')
    base=output.with_name(output.stem+'-captioned.mp4')
    result=subprocess.run(['ffmpeg','-y','-v','error','-filter_complex_threads','1','-loop','1','-framerate','30','-i',str(frame),'-i',str(audio),'-filter_complex_script',str(filter_path),'-map','[out]','-map','1:a','-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-t',str(duration),'-movflags','+faststart',str(base)],capture_output=True,text=True,timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-1600:])
    overlay_avatar(base,Path(avatar),output,AVATAR_BOX)
    return {'path':str(output),'duration':duration,'scene_sha256':__import__('hashlib').sha256(Path(scene).read_bytes()).hexdigest(),'format_id':FORMAT['id'],'profile_sha256':PROFILE_SHA256,'scene_mode':FORMAT['scene']['mode'],'subject_visual':'interpretive_pda_illustration' if 'palmpilot' in subject.casefold() else 'diffusion_scene','avatar_box':AVATAR_BOX,'caption_box':CAPTION_BOX,'caption_cues':cues}

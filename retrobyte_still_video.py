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

SIZE = (1080, 1920)
AVATAR_BOX = (620, 1220, 390, 380)
CAPTION_BOX = (70, 1650, 1010, 1810)
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
    canvas=Image.new('RGB',SIZE,(13,16,25));d=ImageDraw.Draw(canvas)
    amber=(255,184,72);white=(241,242,247);blue=(87,203,255)
    d.text((70,110),'RETROBYTE DISCOVERS',font=ImageFont.truetype(BOLD,35),fill=amber)
    title=subject.removeprefix('The ').upper()
    lines=textwrap.wrap(title,18)
    for i,line in enumerate(lines[:2]):
        d.text((64,180+i*86),line,font=ImageFont.truetype(BOLD,72),fill=white)
    # One fixed image: never animate, translate, zoom, or loop its camera.
    im=Image.open(scene).convert('RGB').resize((800,800),Image.Resampling.LANCZOS)
    canvas.paste(im,(140,400));d=ImageDraw.Draw(canvas)
    d.rectangle((140,400,940,1200),outline=(63,81,103),width=3)
    if 'palmpilot' in subject.casefold():
        draw_pda(d)
    d.text((76,354),'A LITTLE TECH TIME TRAVEL',font=ImageFont.truetype(FONT,26),fill=blue)
    d.text((72,1395),'Before smartphones…' if 'palmpilot' in subject.casefold() else 'Remember this?',font=ImageFont.truetype(BOLD,37),fill=amber)
    d.text((72,1460),'Appointments.' if 'palmpilot' in subject.casefold() else 'A little piece',font=ImageFont.truetype(BOLD,44),fill=white)
    d.text((72,1520),'Pocket notes.' if 'palmpilot' in subject.casefold() else 'of tech history.',font=ImageFont.truetype(BOLD,44),fill=white)
    d.rounded_rectangle(CAPTION_BOX,radius=16,fill=(25,31,44))
    d.text((70,1850),'Would you use one today?',font=ImageFont.truetype(FONT,32),fill=blue)
    canvas.save(output)
    return output


def caption_cues(script,duration):
    words=script.split();groups=[' '.join(words[i:i+5]) for i in range(0,len(words),5)]
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
        text=output.with_name(output.stem+f'-cue-{i}.txt');text.write_text('\n'.join(textwrap.wrap(cue['text'],38)),encoding='utf-8')
        font_px = 43
        lines = text.read_text().splitlines()
        while font_px > 28 and max(ImageFont.truetype(BOLD,font_px).getlength(line) for line in lines) > 900:
            font_px -= 1
        cue['font_px'] = font_px
        filters.append(f"drawtext=fontfile={BOLD}:textfile='{text}':expansion=none:fontsize={font_px}:fontcolor=white:x=(w-text_w)/2:y=1705:enable='gte(t,{cue['start']})*lt(t,{cue['end']})'")
    filter_path=output.with_suffix('.filter');filter_path.write_text('[0:v]'+','.join(filters)+'[out]')
    base=output.with_name(output.stem+'-captioned.mp4')
    result=subprocess.run(['ffmpeg','-y','-v','error','-filter_complex_threads','1','-loop','1','-framerate','30','-i',str(frame),'-i',str(audio),'-filter_complex_script',str(filter_path),'-map','[out]','-map','1:a','-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-t',str(duration),'-movflags','+faststart',str(base)],capture_output=True,text=True,timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-1600:])
    overlay_avatar(base,Path(avatar),output,AVATAR_BOX)
    return {'path':str(output),'duration':duration,'scene_sha256':__import__('hashlib').sha256(Path(scene).read_bytes()).hexdigest(),'scene_mode':'stationary_local_diffusion','subject_visual':'interpretive_pda_illustration' if 'palmpilot' in subject.casefold() else 'diffusion_scene','avatar_box':AVATAR_BOX,'caption_box':CAPTION_BOX,'caption_cues':cues}

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from core.ffmpeg import probe
from core.queue import Cancelled
from modules.remix.planner import Timeline, duration as timeline_duration
from modules.remix.renderer import LOUDNESS_TARGET, render as remix_render, run_ffmpeg


def build_timeline(treatment, script, media, width=1080, height=1920, transition=False):
    latest = {}
    for m in media:
        if m['status']=='succeeded': latest[(m['kind'],m['shot_id'])]=m
    offset, words, segments, audio = 0., [], {}, []
    for segment in script['segments']:
        speech = latest.get(('speech',segment['id']))
        if not speech: raise ValueError('台词音频尚未生成')
        meta = json.loads(speech['metadata']) if isinstance(speech['metadata'],str) else speech['metadata']
        local = [dict(w,start=w['start']+offset,end=w['end']+offset) for w in meta['words']]
        segments[segment['id']] = (local,offset,offset+meta['duration'])
        words += local
        audio.append(speech['path'])
        offset += meta['duration']
    clips, assets, cursor = [], {}, 0.
    for i, shot in enumerate(treatment['shots']):
        m = latest.get(('video',shot['id']))
        if not m: raise ValueError('存在尚未完成的镜头')
        local, begin, end = segments[shot['audio_ref']]
        start = begin if shot['word_start']==0 else local[shot['word_start']]['start']
        stop = end if shot['word_end']==len(local)-1 else local[shot['word_end']+1]['start']
        if abs(start-cursor) > .05 or stop <= start:
            raise ValueError('镜头词区间必须按台词顺序连续覆盖，不能重叠或留空')
        length = stop-start
        speed = shot['duration']/length
        if not .25 <= speed <= 4: raise ValueError('台词与镜头时长差异过大，请修改台词或重生成')
        assets[m['id']] = dict(id=m['id'],path=m['path'])
        clips.append(dict(shot_id=shot['id'],asset_id=m['id'],src_start=0.,src_end=shot['duration'],
            speed=speed,mute=True,caption=None,transition_in=dict(type='fade' if transition else 'none',
                            duration=min(.15,length/4) if transition else 0.)))
        cursor = stop
    if abs(cursor-offset)>.05: raise ValueError('镜头未覆盖全部目标台词')
    timeline = Timeline.model_validate(dict(width=width,height=height,clips=clips)).model_dump()
    return dict(timeline, clone_audio=audio, clone_words=words, clone_assets=assets, clone_duration=offset)


def ass_time(seconds):
    centis = max(0,round(seconds*100))
    return f'{centis//360000}:{centis//6000%60:02}:{centis//100%60:02}.{centis%100:02}'


def ass_text(text):
    # Disallow ASS control syntax, keep only text. Newlines are normalized.
    return text.replace('\\','＼').replace('{','｛').replace('}','｝').replace('\r',' ').replace('\n',' ')


def write_ass(words, path, width, height):
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{max(18,round(height*.035))},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,30,30,{round(height*.12)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = []
    # Short phrases keep mobile subtitles readable; karaoke timing includes inter-word gaps.
    for i in range(0,len(words),6):
        chunk = words[i:i+6]
        text = ''
        cursor = chunk[0]['start']
        for w in chunk:
            gap = round((w['start']-cursor)*100)
            if gap>0: text += '{\\k' + str(gap) + '} '
            text += '{\\kf' + str(max(1,round((w['end']-w['start'])*100))) + '}' + ass_text(w['word']) + ' '
            cursor=w['end']
        lines.append(f"Dialogue: 0,{ass_time(chunk[0]['start'])},{ass_time(chunk[-1]['end'])},Default,,0,0,0,,{text}")
    Path(path).write_text(header+'\n'.join(lines)+'\n',encoding='utf-8-sig')


def render(timeline, target, brief, db, progress=lambda _: None, cancelled=lambda: False):
    target = Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    base = {k:v for k,v in timeline.items() if not k.startswith('clone_')}
    try:
        with TemporaryDirectory(dir=target.parent) as folder:
            work=Path(folder)
            silent=work/'silent.mp4'
            remix_render(base,timeline['clone_assets'],silent,lambda p:progress(p*.65),cancelled)
            write_ass(timeline['clone_words'],work/'captions.ass',base['width'],base['height'])
            if brief.get('font_path'):
                shutil.copyfile(brief['font_path'],work/'caption-font.ttf')
            args=['-i',str(silent)]
            for path in timeline['clone_audio']: args += ['-i',str(path)]
            count=len(timeline['clone_audio'])
            filters=[f'[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]' for i in range(1,count+1)]
            filters.append(''.join(f'[a{i}]' for i in range(1,count+1))+f'concat=n={count}:v=0:a=1[voice]')
            voice='voice'
            length=timeline['clone_duration']
            if brief.get('bgm_asset_id'):
                bgm=db.one('SELECT * FROM assets WHERE id=?',(brief['bgm_asset_id'],))
                args += ['-stream_loop','-1','-i',bgm['path']]
                filters += [f'[{count+1}:a]volume=0.15,atrim=duration={length},afade=t=out:st={max(0,length-1)}:d=1[music]',
                            '[voice][music]amix=inputs=2:duration=first:normalize=0[mixed]']
                voice='mixed'
            # Bring the voice + music mix to the short-video loudness target (the user's own tail is left untouched).
            filters.append(f'[{voice}]loudnorm=I={LOUDNESS_TARGET}:TP=-1.5:LRA=11,aresample=48000[normed]')
            voice='normed'
            filters += ["[0:v]ass=filename=captions.ass:fontsdir=.[captioned]"]
            main=work/'main.mp4'
            args += ['-filter_complex',';'.join(filters),'-map','[captioned]','-map',f'[{voice}]','-t',str(length),
                     '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(main)]
            run_ffmpeg(args,length,lambda p:progress(65+p*.25),cancelled,work)
            if brief.get('concat_tail_asset_id'):
                tail=db.one('SELECT * FROM assets WHERE id=?',(brief['concat_tail_asset_id'],))
                info=probe(tail['path'])
                t=Timeline.model_validate(dict(width=base['width'],height=base['height'],clips=[
                    dict(shot_id='main',asset_id='main',src_start=0.,src_end=length),
                    dict(shot_id='tail',asset_id='tail',src_start=0.,src_end=info['duration'])])).model_dump()
                remix_render(t,{'main':{'path':str(main)},'tail':tail},target,lambda p:progress(90+p*.09),cancelled)
            else: shutil.copyfile(main,target)
            if cancelled(): raise Cancelled()
            probe(target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

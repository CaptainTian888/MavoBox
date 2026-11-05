import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from core.queue import Cancelled


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Word(Strict):
    word: str = Field(min_length=1, max_length=1000)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class Visual(Strict):
    shot_type: str
    camera: str
    composition: str


class Audio(Strict):
    music: str
    sfx: str
    voice_style: str


class Beat(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    role: Literal['hook','setup','proof','turn','cta','other']
    what_happens: str
    speaker: str
    visual: Visual
    on_screen_text: list[str]
    graphics: list[str]
    audio: Audio


class System(Strict):
    kind: Literal['caption','mg','effect','audio']
    description: str
    persistence: str


class Analysis(Strict):
    language: str
    duration: float = Field(gt=0, le=3600)
    hook_type: str
    summary: str
    why_it_works: str
    beats: list[Beat] = Field(min_length=1, max_length=500)
    transcript: list[Word] = Field(max_length=30000)
    systems: list[System]
    open_questions: list[str]


class AdaptedBeat(Strict):
    beat_ref: int = Field(ge=0)
    preserve: str
    rebuild: str
    form: str


class Shot(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    beat_ref: int = Field(ge=0)
    kind: Literal['a_roll','b_roll','product','ui','title']
    duration: float = Field(gt=0, le=300)
    prompt: str = Field(min_length=1, max_length=10000)
    negative: str = ''
    reference_assets: list[str] = Field(default_factory=list)
    camera: str = ''
    audio_ref: str
    word_start: int = Field(default=0, ge=0)
    word_end: int = Field(default=0, ge=0)


class Treatment(Strict):
    beats: list[AdaptedBeat] = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1, max_length=200)


class ScriptWord(Strict):
    display: str = Field(min_length=1, max_length=1000)
    spoken: str = Field(min_length=1, max_length=1000)
    marks: list[Literal['emphasis','cta','cut','pause']] = Field(default_factory=list)


class Segment(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    role: str
    words: list[ScriptWord] = Field(min_length=1, max_length=5000)


class Script(Strict):
    language: str
    segments: list[Segment] = Field(min_length=1, max_length=200)


def checked(model, value):
    try:
        return model.model_validate(value).model_dump()
    except (ValidationError, TypeError):
        raise ValueError('JSON 字段或类型无效，请按 schema 返回完整结构') from None


def validate_words(words, duration):
    previous = 0.
    for w in words:
        if not previous <= w['start'] < w['end'] <= duration + .05:
            raise ValueError('词级时间必须有序且位于音频范围内')
        previous = w['end']
    return words


def validate_analysis(value, duration):
    a = checked(Analysis, value)
    if abs(a['duration'] - duration) > .25:
        raise ValueError('解读时长与参考片段不符')
    previous = 0.
    for b in a['beats']:
        if not previous <= b['start'] < b['end'] <= duration + .05:
            raise ValueError('beat 时间无效或重叠')
        previous = b['end']
    validate_words(a['transcript'], duration)
    return a


def validate_treatment(value, analysis, assets, seconds, extra):
    t = checked(Treatment, value)
    ids = [s['id'] for s in t['shots']]
    if len(set(ids)) != len(ids): raise ValueError('镜头 ID 重复')
    if any(b['beat_ref'] >= len(analysis['beats']) for b in t['beats'] + t['shots']):
        raise ValueError('beat 引用不存在')
    durations = extra.get('supported_durations', [5, 10])
    for s in t['shots']:
        if not set(s['reference_assets']) <= set(assets): raise ValueError('参考图引用不存在')
        if 'min_duration' in extra and 'max_duration' in extra:
            valid = extra['min_duration'] <= s['duration'] <= extra['max_duration']
        else:
            valid = any(abs(s['duration'] - d) < .001 for d in durations)
        if not valid: raise ValueError('镜头时长不在视频模型支持范围内')
        if s['word_end'] < s['word_start']: raise ValueError('词区间顺序错误')
    if abs(sum(s['duration'] for s in t['shots']) - seconds) > max(.5, seconds * .05):
        raise ValueError('镜头总时长超出目标容差')
    return t


def validate_script(value, treatment, forbidden=()):
    s = checked(Script, value)
    lookup = {v['id']: v for v in s['segments']}
    if len(lookup) != len(s['segments']): raise ValueError('台词段 ID 重复')
    for shot in treatment['shots']:
        segment = lookup.get(shot['audio_ref'])
        if not segment or shot['word_end'] >= len(segment['words']):
            raise ValueError('镜头台词段或词区间引用不存在')
    expected=[(seg['id'],i) for seg in s['segments'] for i in range(len(seg['words']))]
    actual=[(shot['audio_ref'],i) for shot in treatment['shots'] for i in range(shot['word_start'],shot['word_end']+1)]
    if actual != expected:
        raise ValueError('镜头词区间须按顺序覆盖全部台词，不能重叠或遗漏')
    text = ' '.join(w[k] for seg in s['segments'] for w in seg['words'] for k in ('display','spoken'))
    if any(word and word in text for word in forbidden): raise ValueError('台词含禁用词')
    return s


def parse_json(text):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.I)
    # Accept surrounding prose, but never eval or repair values silently.
    start = text.find('{')
    if start < 0: raise ValueError('需要 JSON 对象')
    return json.JSONDecoder().raw_decode(text[start:])[0]


def structured(client, messages, validate, cancelled=lambda: False):
    messages = list(messages)
    for attempt in range(3):
        if cancelled(): raise Cancelled()
        try:
            value=parse_json(client(messages))
        except Cancelled:
            raise
        except Exception:
            # Never echo provider output/errors, even into a later prompt.
            messages.append({'role':'user', 'content':'模型请求或 JSON 解析失败，请重新返回完整 JSON。'})
            continue
        try:
            return validate(value)
        except Cancelled:
            raise
        except ValueError as exc:
            # Validators emit only our fixed messages, never raw third-party data.
            messages.append({'role':'user','content':'JSON 校验失败：'+str(exc)+'。请修正后返回完整 JSON。'})
        except (TypeError,KeyError,IndexError):
            messages.append({'role':'user','content':'JSON 结构或引用无效，请重新返回完整 JSON。'})
    raise ValueError('模型输出连续三次未通过 JSON、引用或时长校验')

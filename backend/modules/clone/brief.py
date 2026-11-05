from pydantic import Field
from .schemas import Strict, checked


class Brief(Strict):
    product_name: str = Field(default='', max_length=300)
    selling_points: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    forbidden_words: list[str] = Field(default_factory=list, max_length=100)
    reference_assets: list[str] = Field(default_factory=list, max_length=20)
    requirements: str = Field(default='', max_length=20000)
    language: str = 'zh'
    platform: str = 'TikTok'
    target_seconds: float = Field(default=5., ge=.5, le=3600)
    style: str = Field(default='', max_length=4000)
    variant_count: int = Field(default=1, ge=1, le=20)
    dimensions: dict[str, list[str]] = Field(default_factory=dict)
    budget_usd: float | None = Field(default=None, ge=0)
    providers: dict[str, str] = Field(default_factory=dict)
    voice: str = 'alloy'
    bgm_asset_id: str | None = None
    concat_tail_asset_id: str | None = None
    font_path: str = ''
    transition: bool = False


def validate_brief(value):
    b = checked(Brief, value)
    if not set(b['dimensions']) <= {'person','product','dialogue','style','cta'}:
        raise ValueError('未知变体维度')
    if not set(b['providers']) <= {'llm','asr','image_gen','tts','video_gen'}:
        raise ValueError('未知 Provider 类型')
    return b

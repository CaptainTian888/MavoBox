import json
from .schemas import Script, structured, validate_script


def plan_script(client, treatment, brief, variant, cancelled=lambda: False):
    messages = [{'role':'system','content':'只返回 JSON。Script 是唯一口播来源，不拷贝参考台词。为每个镜头 audio_ref 写对应段，按词拆分；中文按有意义短词拆分。每段词数必须等于该段镜头最大 word_end 加一，段按镜头顺序排列，不得增添未被镜头引用的词或段。display 是字幕，spoken 是读音，品牌可不同。语速要符合目标时长。只使用用户提供的真实证据，遵守禁用词。'},
        {'role':'user','content':json.dumps(dict(treatment=treatment, brief=brief, variant=variant,
                                              schema=Script.model_json_schema()), ensure_ascii=False)}]
    return structured(client, messages, lambda v: validate_script(v, treatment, brief['forbidden_words']), cancelled)

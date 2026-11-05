from itertools import product, islice
import copy


def expand(brief, count, dimensions):
    if not 1 <= count <= 20: raise ValueError('变体数量须为 1–20')
    if not dimensions or not set(dimensions) <= {'person','product','dialogue','style','cta'}:
        raise ValueError('请选择人物、产品、台词、风格或 CTA 变体维度')
    if any(not isinstance(v,list) or not v or any(not isinstance(x,str) or not x.strip() for x in v) for v in dimensions.values()):
        raise ValueError('每个变体维度需要非空候选值')
    keys = list(dimensions)
    combinations = list(islice(product(*(dimensions[k] for k in keys)),count))
    if len(combinations) < count: raise ValueError('维度组合数量小于请求变体数')
    output = []
    for values in combinations:
        params = dict(zip(keys,values))
        b = copy.deepcopy(brief)
        b['variant_count'] = 1
        if 'style' in params: b['style'] = params['style']
        if 'product' in params: b['product_name'] = params['product']
        if 'person' in params:
            if params['person'] not in b['reference_assets']: raise ValueError('人物维度必须使用 Brief 中的参考图 asset ID')
            b['reference_assets'] = [params['person']] + [x for x in b['reference_assets'] if x != params['person']]
        output.append((b,params))
    return output

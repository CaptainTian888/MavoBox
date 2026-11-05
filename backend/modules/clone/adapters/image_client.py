import base64
from contextlib import ExitStack
from pathlib import Path
from .base import Client, Fatal


class ImageClient(Client):
    def generate(self, prompt, references, dest):
        body = {**self.extra.get('defaults', {}), 'model':self.provider['model'], 'prompt':prompt}
        if references:
            with ExitStack() as stack:
                files = [('image' if len(references) == 1 else 'image[]',
                          (Path(p).name, stack.enter_context(Path(p).open('rb')))) for p in references]
                response = self.request('POST', self.extra.get('edits_path','/images/edits'), data=body, files=files)
        else:
            response = self.request('POST', self.extra.get('generations_path','/images/generations'), json=body)
        try:
            item = self.json(response)['data'][0]
            if item.get('b64_json'):
                Path(dest).write_bytes(base64.b64decode(item['b64_json'], validate=True))
            else: self.download(item['url'], dest, verify=False)
            from core.ffmpeg import probe
            info=probe(dest)
            if not info.get('width') or info.get('duration',0)>0:
                raise ValueError('生成结果不是静态图片')
        except (KeyError, IndexError, ValueError):
            Path(dest).unlink(missing_ok=True)
            raise Fatal('图像生成结果不可读') from None
        return Path(dest)

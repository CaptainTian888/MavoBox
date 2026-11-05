import time
from .base import Client, Fatal, ContentPolicy, json_path


def template(value, context):
    if isinstance(value, dict): return {k:template(v, context) for k,v in value.items()}
    if isinstance(value, list): return [template(v, context) for v in value]
    if isinstance(value, str):
        for key, item in context.items():
            if value == '{' + key + '}': return item
        for key, item in context.items():
            if isinstance(item, (str,int,float)): value = value.replace('{' + key + '}', str(item))
    return value


class VideoClient(Client):
    def download(self,url,dest):
        from core.ffmpeg import probe
        from pathlib import Path
        path=super().download(url,dest)
        try:
            if probe(path)['type']!='video': raise Fatal('生成结果不包含视频流')
        except BaseException:
            Path(path).unlink(missing_ok=True)
            raise
        return path

    def submit(self, images, prompt, duration, aspect, extra):
        self.check()
        images = [self.image(p) for p in images]
        style = self.provider.get('api_style', 'custom_http')
        context = dict(images=images, image=images[0] if images else '', prompt=prompt,
                       duration=duration, aspect=aspect, model=self.provider['model'])
        body = {**self.extra.get('defaults', {}), **extra, 'prompt':prompt, 'duration':duration,
                'aspect_ratio':aspect}
        if images: body['image_url'] = images[0]
        if len(images)>1 and self.extra.get('multi_image_field'):
            body[self.extra['multi_image_field']]=images
        if self.extra.get('body_template'): body = template(self.extra['body_template'], context)
        if style == 'fal_queue':
            value = self.json(self.request('POST', '/' + self.provider['model'].lstrip('/'), json=body))
            return dict(id=json_path(value,'request_id'), status_url=json_path(value,'status_url'),
                        response_url=json_path(value,'response_url'))
        if style == 'replicate':
            value = self.json(self.request('POST', '/v1/predictions', json={'version':self.provider['model'], 'input':body}))
            return {'id':json_path(value,'id')}
        value = self.json(self.request('POST', self.extra.get('submit_path','/jobs'), json=body))
        return {'id':json_path(value, self.extra.get('job_id_path','id'))}

    def poll(self, job_id):
        job = {'id':job_id} if isinstance(job_id, str) else job_id
        style = self.provider.get('api_style','custom_http')
        if style == 'fal_queue': url = job['status_url']
        elif style == 'replicate': url = '/v1/predictions/' + str(job['id'])
        else: url = self.extra.get('poll_path','/jobs/{job_id}').replace('{job_id}', str(job['id']))
        value = self.json(self.request('GET', url))
        status = str(json_path(value, self.extra.get('status_path','status'))).lower()
        if status in self.extra.get('success_statuses', ['completed','succeeded','success']):
            if style == 'fal_queue':
                value = self.json(self.request('GET', job['response_url']))
                url = json_path(value, self.extra.get('fal_result_path','video.url'))
            else:
                url = json_path(value, self.extra.get('result_path','output'))
                if isinstance(url,list): url = url[0] if url else None
            if not isinstance(url,str): raise Fatal('视频结果 URL 不存在')
            return dict(status='succeeded',progress=100,url=url)
        if status in ('failed','error','canceled','cancelled','rejected'):
            raise ContentPolicy('视频生成失败或被拒绝，请检查内容与 Provider 控制台')
        try:
            progress=float(json_path(value,self.extra.get('progress_path','progress')))
            import math
            if not math.isfinite(progress): progress=0
        except (Fatal,TypeError,ValueError): progress=0
        return dict(status='running', progress=max(0,min(99,progress)), url=None)

    def wait_result(self, job, progress=lambda _: None):
        deadline = time.monotonic() + min(float(self.extra.get('timeout_seconds',900)),7200)
        while time.monotonic() < deadline:
            self.check()
            result = self.poll(job)
            progress(result['progress'])
            if result['status'] == 'succeeded': return result['url']
            self.wait(max(.1, min(float(self.extra.get('poll_seconds',2)),30)))
        raise Fatal('视频生成等待超时，任务 ID 已保存；重试将继续轮询')

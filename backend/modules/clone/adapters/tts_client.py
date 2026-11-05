from pathlib import Path
from .base import Client


class TTSClient(Client):
    def speak(self, text, dest, voice='alloy'):
        response = self.request('POST', self.extra.get('speech_path','/audio/speech'), json={
            **self.extra.get('defaults', {}), 'model':self.provider['model'], 'input':text,
            'voice':voice, 'response_format':'wav'})
        self.check()
        Path(dest).write_bytes(response.content)
        return Path(dest)

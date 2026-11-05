from uuid import uuid4
from .secrets import secret_status


class Providers:
    def __init__(self, db, secrets):
        self.db, self.secrets = db, secrets

    def list(self):
        return [dict(row, key_status=secret_status(self.secrets, row['id']))
                for row in self.db.all("SELECT * FROM providers ORDER BY name")]

    def save(self, data, provider_id=None):
        provider_id = provider_id or uuid4().hex
        self.db.execute('''INSERT INTO providers VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET type=excluded.type, name=excluded.name,
            base_url=excluded.base_url, model=excluded.model''',
            (provider_id, data.type, data.name, data.base_url, data.model))
        return next(row for row in self.list() if row['id'] == provider_id)

    def delete(self, provider_id):
        self.secrets.delete(provider_id)
        self.db.execute("DELETE FROM providers WHERE id=?", (provider_id,))

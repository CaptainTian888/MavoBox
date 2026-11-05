from typing import Protocol


class SecretStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, value: str) -> None: ...
    def delete(self, provider_id: str) -> None: ...


class KeyringSecrets:
    service = "TikTolTool.providers"

    def get(self, provider_id):
        import keyring
        try:
            return keyring.get_password(self.service, provider_id)
        except Exception:
            raise RuntimeError("系统凭据读取失败") from None

    def set(self, provider_id, value):
        import keyring
        try:
            keyring.set_password(self.service, provider_id, value)
        except Exception:
            raise RuntimeError("系统凭据写入失败") from None

    def delete(self, provider_id):
        import keyring
        if self.get(provider_id) is not None:
            try:
                keyring.delete_password(self.service, provider_id)
            except Exception:
                raise RuntimeError("系统凭据删除失败") from None


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, provider_id):
        return self.values.get(provider_id)

    def set(self, provider_id, value):
        self.values[provider_id] = value

    def delete(self, provider_id):
        self.values.pop(provider_id, None)


def secret_status(store, provider_id):
    # Deliberately disclose only presence (even a short key must never be echoed).
    return "已设置" if store.get(provider_id) else "未设置"

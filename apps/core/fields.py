import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models


def _fernet() -> MultiFernet:
    keys = [Fernet(k.encode() if isinstance(k, str) else k) for k in settings.FIELD_ENCRYPTION_KEYS]
    return MultiFernet(keys)


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Cannot decrypt value: unknown or rotated-out encryption key") from exc


class EncryptedTextField(models.TextField):
    """Fernet-encrypted at rest. Rotate by prepending a key to FIELD_ENCRYPTION_KEYS."""

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return decrypt_text(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return value if value in (None, "") else encrypt_text(value)


class EncryptedJSONField(EncryptedTextField):
    def from_db_value(self, value, expression, connection):
        text = super().from_db_value(value, expression, connection)
        return {} if text in (None, "") else json.loads(text)

    def to_python(self, value):
        if isinstance(value, str):
            return json.loads(value) if value else {}
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        return encrypt_text(json.dumps(value, separators=(",", ":"), sort_keys=True))

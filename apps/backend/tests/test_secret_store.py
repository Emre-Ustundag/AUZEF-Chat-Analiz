"""OpenRouter anahtarı saklama testleri — plan §4 ölçüt 5.

İki kümeye ayrılır:

* Şifreleme testleri Redis GEREKTİRMEZ: AES-GCM'in doğru kurulduğu, nonce'un
  tekrar etmediği ve AAD'nin analiz kimliğine bağlandığı altyapısız
  doğrulanabilir.
* Redis testleri `integration` işaretlidir; anahtarın gerçekten ŞİFRELİ
  yazıldığını, TTL'inin doğru olduğunu ve silindiğini kontrol eder.
"""

from __future__ import annotations

import base64
import uuid
from typing import cast

import pytest

from app.core.config import Settings
from app.services import secret_store
from app.services.secret_store import SecretDecryptionError

SAMPLE_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"


@pytest.fixture
def settings() -> Settings:
    return Settings()


# --------------------------------------------------------------- şifreleme


def test_sifreli_govdede_anahtar_gorunmez(settings: Settings) -> None:
    analysis_id = uuid.uuid4()
    blob = secret_store.encrypt_key(SAMPLE_KEY, analysis_id, settings)

    assert SAMPLE_KEY not in blob
    raw = base64.b64decode(blob)
    assert SAMPLE_KEY.encode() not in raw
    # Nonce + ciphertext + 16 baytlık GCM etiketi.
    assert len(raw) > secret_store.NONCE_BYTES + 16


def test_sifre_cozme_ayni_anahtari_verir(settings: Settings) -> None:
    analysis_id = uuid.uuid4()
    blob = secret_store.encrypt_key(SAMPLE_KEY, analysis_id, settings)

    assert secret_store.decrypt_key(blob, analysis_id, settings) == SAMPLE_KEY


def test_her_yazimda_nonce_degisir(settings: Settings) -> None:
    """Nonce tekrarı GCM'de anahtar akışını yeniden kullanmak demektir."""
    analysis_id = uuid.uuid4()
    blobs = {secret_store.encrypt_key(SAMPLE_KEY, analysis_id, settings) for _ in range(20)}
    assert len(blobs) == 20


def test_baska_analizin_kaydi_cozulemez(settings: Settings) -> None:
    """AAD analiz kimliğine bağlı: kayıtlar birbirinin yerine geçemez."""
    blob = secret_store.encrypt_key(SAMPLE_KEY, uuid.uuid4(), settings)

    with pytest.raises(SecretDecryptionError):
        secret_store.decrypt_key(blob, uuid.uuid4(), settings)


def test_master_key_degisirse_cozulemez(settings: Settings) -> None:
    analysis_id = uuid.uuid4()
    blob = secret_store.encrypt_key(SAMPLE_KEY, analysis_id, settings)

    other = Settings(secret_encryption_key="baska-bir-master-key")
    with pytest.raises(SecretDecryptionError):
        secret_store.decrypt_key(blob, analysis_id, other)


def test_bozuk_kayit_anlamli_hata_verir(settings: Settings) -> None:
    with pytest.raises(SecretDecryptionError):
        secret_store.decrypt_key("bozuk-veri", uuid.uuid4(), settings)


def test_hata_metninde_anahtar_izi_yok(settings: Settings) -> None:
    """ADR §9: anahtar loglara ve istisna metinlerine girmez."""
    analysis_id = uuid.uuid4()
    blob = secret_store.encrypt_key(SAMPLE_KEY, analysis_id, settings)

    with pytest.raises(SecretDecryptionError) as excinfo:
        secret_store.decrypt_key(blob, uuid.uuid4(), settings)

    message = str(excinfo.value)
    assert SAMPLE_KEY not in message
    assert blob not in message


# ------------------------------------------------------------------ TTL


def test_ttl_hard_timeout_arti_bes_dakika() -> None:
    """ADR §9: TTL HER ZAMAN hard timeout + 5 dakika (varsayılan 50 dk)."""
    settings = Settings()
    assert settings.openrouter_key_ttl_seconds == (
        settings.analysis_hard_timeout_seconds + settings.openrouter_key_ttl_margin_seconds
    )
    assert settings.openrouter_key_ttl_seconds == 50 * 60


def test_ttl_environment_ile_degisir() -> None:
    """Sınırlar sabit kodlanmaz (ADR §9 değişmez 7)."""
    settings = Settings(analysis_timeout_seconds=600, analysis_soft_timeout_seconds=540)
    assert settings.openrouter_key_ttl_seconds == 600 + 300


# ------------------------------------------------------- Redis (integration)


@pytest.mark.integration
def test_redis_kaydi_sifreli_ve_ttlli(settings: Settings) -> None:
    from redis import Redis

    analysis_id = uuid.uuid4()
    ttl = secret_store.store_key(analysis_id, SAMPLE_KEY, settings)

    client = Redis.from_url(settings.redis_url)
    try:
        stored = cast("bytes | None", client.get(secret_store.redis_key(analysis_id)))
        assert stored is not None
        assert SAMPLE_KEY.encode() not in stored
        assert SAMPLE_KEY not in stored.decode("ascii")

        remaining = cast("int", client.ttl(secret_store.redis_key(analysis_id)))
        assert 0 < remaining <= ttl
        assert ttl == settings.openrouter_key_ttl_seconds

        # Okuma yolu Faz 3 için hazır: kayıt çözülebiliyor.
        assert secret_store.load_key(analysis_id, settings) == SAMPLE_KEY

        secret_store.delete_key(analysis_id, settings)
        assert client.get(secret_store.redis_key(analysis_id)) is None
        assert secret_store.load_key(analysis_id, settings) is None
    finally:
        client.delete(secret_store.redis_key(analysis_id))
        client.close()

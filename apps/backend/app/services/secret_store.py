"""BYOK OpenRouter anahtarının şifreli, kısa ömürlü saklanması.

ADR §9 değişmezleri:

* "OpenRouter key PostgreSQL'e veya loglara yazılmaz."
* "BYOK anahtarı AES-GCM ile şifreli Redis kaydı olarak tutulur; TTL her
  zaman job hard timeout + 5 dakikadır (varsayılan 50 dakika) ve işlem
  bitince başarı/hata fark etmeksizin silinir."
* "Sunucu master encryption key yalnızca secret manager/environment içinde
  bulunur."

ADR §10 risk 7: "Kuyrukta API key — yalnızca şifreli TTL secret reference;
task payload içinde düz anahtar yok." Bu yüzden Celery task'ına giden tek
şey `analysis_id`'dir; worker anahtara ihtiyaç duyduğunda onu buradan okur.

FAZ 2 NOTU — ÖNEMLİ: bu fazda anahtar SAKLANIR ve SİLİNİR ama KULLANILMAZ.
OpenRouter'a hiçbir çağrı yapılmaz (plan §4). Okuyan taraf (`load_key`) yine
de yazılıp test edildi; Faz 3'te yalnızca çağrı yeri eklenecek, saklama
yolu değişmeyecek.

Şifreleme tasarımı:

* AES-256-GCM. Anahtar, config'teki master key'den SHA-256 ile türetilir —
  böylece environment'a rastgele uzunlukta bir dize konabilir ama AES her
  zaman tam 32 baytlık bir anahtar görür.
* Her yazımda YENİ ve rastgele 12 baytlık nonce. Nonce tekrarı GCM'de
  felakettir (anahtar akışı yeniden kullanılır); sabit nonce kullanmak
  şifrelemeyi tamamen anlamsız kılardı.
* AAD olarak `analysis_id` bağlanır: bir analizin şifreli anahtarı Redis'te
  başka bir analizin anahtarı yerine geçirilemez.
* Redis'te tutulan değer `nonce || ciphertext`'in base64'üdür. Ham anahtar
  hiçbir aşamada düz metin olarak Redis'e yazılmaz.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from typing import cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from redis import Redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: GCM için önerilen nonce uzunluğu. 12 bayt dışına çıkmak GCM'in güvenlik
#: kanıtının dışına çıkmak demektir.
NONCE_BYTES = 12

_KEY_PREFIX = "openrouter-key"


class SecretDecryptionError(Exception):
    """Şifre çözme başarısız: kayıt bozuk, master key değişmiş veya AAD uyuşmuyor.

    İstisna metni ASLA şifreli/açık anahtar parçası içermez.
    """


def redis_key(analysis_id: uuid.UUID) -> str:
    return f"{_KEY_PREFIX}:{analysis_id}"


def _derive_key(settings: Settings) -> bytes:
    """Master key'den 32 baytlık AES anahtarı türetir."""
    return hashlib.sha256(settings.encryption_key_material().encode("utf-8")).digest()


def encrypt_key(api_key: str, analysis_id: uuid.UUID, settings: Settings) -> str:
    """Anahtarı AES-GCM ile şifreler ve base64 metin döndürür."""
    aesgcm = AESGCM(_derive_key(settings))
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = aesgcm.encrypt(
        nonce,
        api_key.encode("utf-8"),
        str(analysis_id).encode("ascii"),
    )
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_key(blob: str, analysis_id: uuid.UUID, settings: Settings) -> str:
    """base64 kaydı çözer. Bozuk/uyumsuz kayıtta `SecretDecryptionError`."""
    try:
        raw = base64.b64decode(blob.encode("ascii"), validate=True)
        nonce, ciphertext = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        aesgcm = AESGCM(_derive_key(settings))
        plaintext = aesgcm.decrypt(nonce, ciphertext, str(analysis_id).encode("ascii"))
    except Exception:  # cryptography InvalidTag dâhil
        # Orijinal istisna ZİNCİRE BAĞLANMIYOR (`from None`): bazı istisna
        # metinleri girdinin bir parçasını taşıyabiliyor ve bu iz loga düşerdi.
        raise SecretDecryptionError("openrouter_key_decryption_failed") from None
    return plaintext.decode("utf-8")


def _redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url)


def store_key(analysis_id: uuid.UUID, api_key: str, settings: Settings | None = None) -> int:
    """Anahtarı şifreleyip Redis'e TTL ile yazar; kullanılan TTL'i döndürür.

    TTL ADR §9 gereği HER ZAMAN hard timeout + 5 dakikadır ve config'ten
    türetilir (`Settings.openrouter_key_ttl_seconds`), burada sabit değildir.
    """
    settings = settings or get_settings()
    ttl = settings.openrouter_key_ttl_seconds
    blob = encrypt_key(api_key, analysis_id, settings)

    client = _redis_client(settings)
    client.setex(redis_key(analysis_id), ttl, blob)

    # Loga yalnızca analiz kimliği ve TTL yazılır; anahtarın kendisi veya
    # şifreli hâli ASLA yazılmaz.
    logger.info(
        "openrouter_key_stored",
        extra={"analysis_id": str(analysis_id), "ttl_seconds": ttl},
    )
    return ttl


def load_key(analysis_id: uuid.UUID, settings: Settings | None = None) -> str | None:
    """Anahtarı çözerek döndürür; kayıt yoksa (TTL doldu, silindi) `None`.

    FAZ 2'de ÇAĞRILMIYOR — Faz 3'ün OpenRouter istemcisi için hazır duruyor.
    """
    settings = settings or get_settings()
    client = _redis_client(settings)
    raw = cast("bytes | None", client.get(redis_key(analysis_id)))
    if raw is None:
        return None
    return decrypt_key(raw.decode("ascii"), analysis_id, settings)


def delete_key(analysis_id: uuid.UUID, settings: Settings | None = None) -> None:
    """Anahtarı siler. İş bitince BAŞARI/HATA/İPTAL fark etmeksizin çağrılır.

    Hiçbir durumda istisna fırlatmaz: anahtarı silememek işin sonucunu
    değiştirmemeli, ama sessizce de geçilmemeli — loglanır. TTL zaten üst
    sınır güvencesidir.
    """
    settings = settings or get_settings()
    try:
        client = _redis_client(settings)
        client.delete(redis_key(analysis_id))
        logger.info("openrouter_key_deleted", extra={"analysis_id": str(analysis_id)})
    except Exception:
        logger.exception("openrouter_key_delete_failed", extra={"analysis_id": str(analysis_id)})

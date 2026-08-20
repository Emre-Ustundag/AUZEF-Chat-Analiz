"""Map önbelleği — bulgu A3.

Sınıflandırıcı tarafındaki davranış (isabette sağlayıcıya gidilmemesi,
`usage`'a eklenmemesi, bozuk kaydın ıska sayılması) `test_llm_classifier.py`
içinde. Burada önbelleğin KENDİ sözleşmesi var:

* TTL ayardan türer, sabit kodlanmaz.
* Redis hatası ASLA dışarı sızmaz — önbellek bir tasarruf katmanıdır.
* Anahtar `analysis_id` TAŞIMAZ: zaman aşımından sonra açılan YENİ analiz de
  aynı chunk'a isabet edebilmeli, yoksa A3'ün amacı kaybolur.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.config import Settings
from app.services.map_cache import MapCache, build_key, map_cache_for


@pytest.fixture
def settings() -> Settings:
    return Settings()


class _Backend:
    def __init__(self, *, fail_get: bool = False, fail_setex: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail_get = fail_get
        self.fail_setex = fail_setex

    def get(self, name: str) -> bytes | str | None:
        if self.fail_get:
            raise ConnectionError("redis yok")
        return self.data.get(name)

    def setex(self, name: str, time: int, value: str) -> object:
        if self.fail_setex:
            raise ConnectionError("redis yok")
        self.data[name] = value
        self.ttls[name] = time
        return True


def _key(text: str = "sınav ne zaman") -> str:
    return build_key(
        model="google/gemini-2.5-flash",
        prompt_text_hash="a" * 64,
        map_schema={"type": "object"},
        rendered_records=f'<kayit id="r1">{text}</kayit>',
    )


def test_yaz_oku_turu_ayni_metni_dondurur(settings: Settings) -> None:
    cache = MapCache(settings, backend=_Backend())
    cache.store(_key(), '{"categories": [], "assignments": []}')

    assert cache.load(_key()) == '{"categories": [], "assignments": []}'
    assert cache.hits == 1


def test_ttl_ayardan_turer(settings: Settings) -> None:
    """Rapor ne kadar yaşıyorsa ondan türeyen ara ürün de o kadar yaşar."""
    backend = _Backend()
    ozel = settings.model_copy(update={"report_retention_hours": 6})
    cache = MapCache(ozel, backend=backend)
    cache.store(_key(), "{}")

    assert cache.ttl_seconds == 6 * 3600
    assert backend.ttls[_key()] == 6 * 3600


def test_okuma_hatasi_disari_sizmaz(settings: Settings) -> None:
    cache = MapCache(settings, backend=_Backend(fail_get=True))
    assert cache.load(_key()) is None
    # Kendini kapattı: ikinci çağrı backend'e hiç gitmiyor.
    assert cache.load(_key()) is None


def test_yazma_hatasi_disari_sizmaz(settings: Settings) -> None:
    cache = MapCache(settings, backend=_Backend(fail_setex=True))
    cache.store(_key(), "{}")
    assert cache.stores == 0


def test_anahtar_analysis_id_tasimaz() -> None:
    """A3'ün ASIL amacı: aynı chunk yeni bir analizde tekrar ödenmesin.

    Anahtar yalnızca (model, prompt, şema, kayıtlar) fonksiyonu olduğu için
    aynı girdiyi gönderen İKİ AYRI analiz aynı anahtara düşer.
    """
    assert _key() == _key()
    assert _key("başka bir soru") != _key()


def test_ayar_kapaliyken_onbellek_kurulmaz(settings: Settings) -> None:
    assert map_cache_for(settings.model_copy(update={"llm_map_cache_enabled": False})) is None
    assert map_cache_for(settings) is not None


# ------------------------------------------------------- Redis (integration)


@pytest.mark.integration
def test_gercek_redis_kaydi_ttlli_yazilir(settings: Settings) -> None:
    from redis import Redis

    cache = MapCache(settings)
    key = _key("entegrasyon testi kaydı")
    client = Redis.from_url(settings.redis_url)
    try:
        cache.store(key, '{"categories": [], "assignments": []}')

        assert cache.load(key) == '{"categories": [], "assignments": []}'
        remaining = cast("int", cast("Any", client).ttl(key))
        assert 0 < remaining <= settings.report_retention_hours * 3600
    finally:
        cast("Any", client).delete(key)
        client.close()

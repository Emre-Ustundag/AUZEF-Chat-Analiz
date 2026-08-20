"""Tamamlanmış MAP çağrılarının kısa ömürlü Redis önbelleği — bulgu A3.

NEDEN VAR: 45 dakikalık hard timeout dolduğunda iş `PROVIDER_TIMEOUT` ile
kapanıyor ve o ana kadar TAMAMLANMIŞ map chunk'larının sonucu hiçbir yere
yazılmadığı için harcanan para karşılıksız kalıyordu. Yeniden deneme sıfırdan
başlıyor, aynı chunk'lar aynı modele ikinci kez ödeniyordu. Aynısı maliyet
tavanı (`CostLimitExceededError`) yolunda da geçerliydi.

ANAHTARDA `analysis_id` YOKTUR — asıl amaç budur. Zaman aşımına uğrayan bir
analizin yerine açılan YENİ analiz, aynı kayıtları aynı model ve aynı prompt
ile gönderdiğinde önbelleğe düşer ve o chunk'lar için tekrar ücretlendirilmez.
Anahtara `analysis_id` girseydi önbellek yalnızca aynı işin yeniden denenmesine
yarardı; oysa arayüzdeki "yeniden dene" akışı yeni bir analiz kaydı üretir.

DEĞER: sağlayıcının döndürdüğü map yanıtının HAM JSON'u — kategori etiketleri
ve kayıt kimlikleri. Kayıt kimlikleri normalize+REDAKTE metnin hash'i, kategori
metinleri de redakte metinden türetilmiş model çıktısıdır; ham hücre içeriği
buraya girmez (ADR §9). TTL `report_retention_hours` ile aynı penceredir —
rapor ne kadar yaşıyorsa ondan türeyen ara ürün de o kadar yaşar.

ÖNBELLEK İSABETİ `usage`'A EKLENMEZ. Rapordaki token ve maliyet alanları
"sağlayıcının bildirdiği gerçek tüketim" demek zorunda (`pipeline/cost.py`);
ödenmemiş bir çağrıyı ödenmiş gibi göstermek o cümleyi yalan yapardı. İsabet
sayısı yalnızca loga yazılır.

BİLİNEN SINIR: uçuş öncesi maliyet tahmini önbelleği HESABA KATMAZ. Analiz
oluşturulurken (`api/v1/analyses.py`) chunk'lar henüz kurulmadığı için hangi
chunk'ın önbellekte olduğu bilinemez; tahmin ve `max_cost_usd` kontrolü tam
fiyattan yapılır. Sonuç kullanıcı LEHİNE yanılır: koşu tahminden ucuza biter,
raporda gerçek tüketim görünür. Bunu düzeltmek chunk'lamayı istek anına
taşımak demektir; kazancı yok.

HİÇBİR REDIS HATASI ANALİZİ DÜŞÜRMEZ. Önbellek bir hızlandırma/tasarruf
katmanıdır; okuma da yazma da başarısız olabilir ve sonuç yalnızca "yeniden
ücretlendirilir" olur. Bu yüzden tüm hatalar yutulur ve loglanır — ilk hatadan
sonra önbellek kendini kapatır ki her chunk'ta yeniden bağlanmayı denemesin.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Protocol, cast

from redis import Redis

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Anahtar sürümü. Saklanan JSON'un ŞEKLİ değişirse (map yanıt şeması ya da
#: `MapResponse` alanları) burayı artırmak eski kayıtları okunamaz kılar;
#: aksi hâlde yeni kod eski biçimi okuyup sessizce yanlış davranabilir.
_KEY_PREFIX = "map:v1"


class MapCacheBackend(Protocol):
    """`Redis`'in kullanılan iki metodu. Testler bunu dict ile karşılar."""

    def get(self, name: str) -> bytes | str | None: ...

    def setex(self, name: str, time: int, value: str) -> object: ...


def build_key(
    *,
    model: str,
    prompt_text_hash: str,
    map_schema: dict[str, Any],
    rendered_records: str,
) -> str:
    """Chunk'ın içeriğinden türeyen deterministik anahtar.

    Anahtara giren HER ŞEY modelin çıktısını değiştirebilecek bir girdidir:

    * `model` — aynı prompt farklı modelde farklı kategoriler üretir.
    * `prompt_text_hash` — prompt sürümünün tam hash'i (`identifier`'daki
      kısaltılmış 12 karakter DEĞİL; çakışma payını gereksiz yere büyütür).
    * `map_schema` — `text_hash` şemaları BİLEREK dışarıda bırakıyor
      (izlenebilirlik çıpası açıklama düzeltmesinde değişmesin diye). Ama
      structured-output şeması yanıtın ŞEKLİNİ belirler; önbellek için
      hesaba katılmak zorunda.
    * `rendered_records` — sağlayıcıya GÖNDERİLEN `<kayit>` bloğunun ta
      kendisi. Kimlik ve metin listesi yerine bilerek gönderilen metin
      kullanılıyor: kaçış işlevi (`escape_record_text`) prompt sürümüyle
      birlikte değişebilir ve `text_hash` onu KAPSAMIYOR. Kimliklerle
      anahtarlansaydı, kaçış değiştiği gün istek gövdesi değişir ama anahtar
      aynı kalır ve eski yanıtlar sessizce yeni prompt'un cevabı sayılırdı.
      Kayıt sırası da bu metnin içinde korunur — modelin çıktısı girdi
      sırasına duyarlıdır.
    """
    schema_digest = hashlib.sha256(
        json.dumps(map_schema, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    payload = "\n\x00\n".join(
        (
            _KEY_PREFIX,
            model,
            prompt_text_hash,
            schema_digest,
            rendered_records,
        )
    )
    return f"{_KEY_PREFIX}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class MapCache:
    """Redis destekli map önbelleği. Hatada kendini kapatır, asla fırlatmaz."""

    def __init__(self, settings: Settings, *, backend: MapCacheBackend | None = None) -> None:
        self._settings = settings
        self._backend = backend
        self._disabled = False
        #: İstemci kurulumu kilit altında: map çağrıları eşzamanlı gönderilirse
        #: iki iş parçacığı aynı anda bağlanıp birbirinin istemcisinin üstüne
        #: yazar ve biri kapatılmadan sızardı. `hits`/`stores` sayaçları
        #: kilitsiz — yalnızca loglama içindir, bir eksik sayması zararsızdır.
        self._lock = threading.Lock()
        self.hits = 0
        self.stores = 0

    @property
    def ttl_seconds(self) -> int:
        return self._settings.report_retention_hours * 3600

    def _client(self) -> MapCacheBackend | None:
        if self._disabled:
            return None
        if self._backend is None:
            with self._lock:
                if self._backend is None and not self._disabled:
                    try:
                        self._backend = cast(
                            "MapCacheBackend", Redis.from_url(self._settings.redis_url)
                        )
                    except Exception:
                        self._fail("map_cache_connect_failed")
        return None if self._disabled else self._backend

    def _fail(self, event: str) -> None:
        """Önbelleği kapatır ve TEK bir satır loglar.

        Kapatmanın sebebi: 366 chunk'lık bir koşuda her chunk için yeniden
        bağlanmayı denemek, hem logu doldurur hem de her seferinde bağlantı
        zaman aşımı kadar bekletir.
        """
        self._disabled = True
        logger.warning(event, exc_info=True)

    def load(self, key: str) -> str | None:
        """Ham JSON metnini döndürür; kayıt yoksa veya Redis erişilemezse `None`."""
        client = self._client()
        if client is None:
            return None
        try:
            raw = client.get(key)
        except Exception:
            self._fail("map_cache_read_failed")
            return None
        if raw is None:
            return None
        self.hits += 1
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    def store(self, key: str, payload: str) -> None:
        """Ham JSON metnini TTL ile yazar. Başarısızlık sessizdir (loglanır)."""
        client = self._client()
        if client is None:
            return
        try:
            client.setex(key, self.ttl_seconds, payload)
        except Exception:
            self._fail("map_cache_write_failed")
            return
        self.stores += 1


def map_cache_for(settings: Settings) -> MapCache | None:
    """Ayar açıksa önbelleği kurar. Kapalıysa `None` — çağıran yeri bozmaz.

    Bağlantı burada AÇILMAZ: `MapCache` istemciyi ilk kullanımda kurar, yani
    önbellek hiç kullanılmayan bir kod yolunda Redis'e dokunulmaz.
    """
    if not settings.llm_map_cache_enabled:
        return None
    return MapCache(settings)

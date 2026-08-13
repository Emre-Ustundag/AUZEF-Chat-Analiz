"""Ortak Pydantic tabanları ve tel üstündeki tarih tipi.

Frontend'in Zod şemaları (`apps/web/src/lib/api/schemas/`) bu modellerin
aynasıdır. Alan adları iki tarafta da snake_case; çeviren bir eşleme katmanı
yoktur. Sözleşme kararları için ADR-0002.
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    PlainSerializer,
    WithJsonSchema,
)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    # _to_utc zaten çalıştı; offset her zaman tam olarak "+00:00".
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDateTime = Annotated[
    # AwareDatetime, düz datetime değil: naive bir datetime hiç offset'siz
    # serialize olur ("2026-08-11T10:00:00") ve Zod'un z.iso.datetime()'ı onu da
    # reddeder. Validation hatasına çevirmek bug'ı tarayıcı yerine burada patlatır.
    AwareDatetime,
    AfterValidator(_to_utc),
    # when_used="json": Python tarafı model_dump() gerçek datetime döndürmeye
    # devam eder; BE-02'nin SQLAlchemy yazımı buna muhtaç. Yalnızca tel biçimi
    # yeniden yazılır.
    PlainSerializer(_iso_z, return_type=str, when_used="json"),
    # Zorunlu: PlainSerializer'ın str dönüşü serialization JSON Schema'sını
    # çıplak {"type": "string"}'e çevirip format: date-time'ı openapi.json'dan
    # sessizce düşürür. Geri sabitliyoruz.
    WithJsonSchema({"type": "string", "format": "date-time"}, mode="serialization"),
]
"""ADR-0002 #4: çıktıda yalnızca `YYYY-MM-DDTHH:MM:SS.sssZ`.

Doğrulandı — Zod 4.4.3 `z.iso.datetime()`:
  "2026-08-11T10:00:00.000Z"  ACCEPT
  "2026-08-11T10:00:00+00:00" REJECT   <- Pydantic'in VARSAYILAN çıktısı
  "2026-08-11T10:00:00"       REJECT

Girişte her RFC 3339 instant kabul edilip normalize edilir (Postel), çıkışta
tek biçim (katı). Bu asimetri bilinçlidir ve constraints.json'da test
edilemez; çıktı kuralı olarak `test_datetime_serialization.py` doğrular.

timespec="milliseconds" bilinçli: çıplak isoformat() mikrosaniye sıfırken
kesirli kısmı atlar, değilse 6 hane yazar. Deterministik olmayan bu şekil
fixture yeniden üretiminde sahte diff üretir. Sabit 3 hane, JS
`Date.prototype.toISOString()` ile birebir aynı.
"""


class ApiModel(BaseModel):
    """Cevap gövdeleri için taban.

    `json_schema_serialization_defaults_required`: FastAPI response modelleri
    için Pydantic'in SERIALIZATION şemasını yayımlar ve default'u olan bir alan
    orada varsayılan olarak `required` dışında kalır. Oysa `model_dump()` o
    alanı HER cevapta yazıyor — bayrak olmadan openapi.json `status`,
    `warnings`, `error`, `profile` ve `estimated_seconds_remaining`'i
    "opsiyonel" diye belgeliyordu. Bu artefakttan üretilecek bir client'ta
    `report.status === "completed"` ve `job.error !== null` discriminator'ları
    tip düzeyinde buharlaşırdı (ADR-0002 #7, #8).
    """

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        json_schema_serialization_defaults_required=True,
    )


class ApiRequestModel(BaseModel):
    """İstek gövdeleri için taban.

    `extra="forbid"`: yazım hatası olan bir alan sessizce yutulmak yerine 422
    üretir. Zod bilinmeyen anahtarı *strip* ettiği için frontend backend'in
    reddedeceği bir alanı memnuniyetle gönderebilir; yön doğrudur (sunucu
    otoritedir) ama ADR-0002'de kayıtlıdır.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

"""UTC-Z tarih sözleşmesi — ADR-0002 #4.

Zod'un `z.iso.datetime()`'ı offset kabul etmiyor; Pydantic'in VARSAYILAN
datetime çıktısı ise "+00:00" üretiyor. `UtcDateTime` tam olarak bu ayrımı
kapatmak için var, dolayısıyla burada doğrulanan şey estetik değil sözleşme.
"""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.base import UtcDateTime

UTC_MILLIS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class Sample(BaseModel):
    at: UtcDateTime


def test_utc_input_serializes_with_z() -> None:
    dumped = Sample(at=datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC)).model_dump(mode="json")
    assert dumped["at"] == "2026-08-11T10:00:00.000Z"


def test_offset_input_is_normalized_to_utc() -> None:
    """Girişte Postel, çıkışta katı.

    Bu asimetri bilinçli ve bu yüzden `constraints.json` içinde temsil
    edilemez: aynı satır Zod tarafında "reddedilmeli" derken Pydantic
    tarafında "kabul edilmeli" derdi.
    """
    istanbul = timezone(timedelta(hours=3))
    dumped = Sample(at=datetime(2026, 8, 11, 13, 0, 0, tzinfo=istanbul)).model_dump(mode="json")
    assert dumped["at"] == "2026-08-11T10:00:00.000Z"


def test_naive_datetime_is_rejected() -> None:
    # Naive datetime hiç offset'siz serialize olurdu ve Zod onu da reddederdi;
    # hatayı tarayıcı yerine burada patlatıyoruz.
    with pytest.raises(ValidationError):
        Sample(at=datetime(2026, 8, 11, 10, 0, 0))


@pytest.mark.parametrize(
    "microsecond",
    [0, 1, 123_456, 999_999],
)
def test_output_shape_is_always_three_fraction_digits(microsecond: int) -> None:
    """Çıplak isoformat() mikrosaniye sıfırken kesirli kısmı atlar.

    Bu deterministik olmayan şekil, fixture yeniden üretiminde sahte diff
    üretirdi. Sabit 3 hane JS `toISOString()` ile birebir aynı.
    """
    at = datetime(2026, 8, 11, 10, 0, 0, microsecond, tzinfo=UTC)
    assert UTC_MILLIS.match(Sample(at=at).model_dump(mode="json")["at"])


def test_python_mode_still_returns_datetime() -> None:
    """`when_used="json"`: yalnızca tel biçimi değişir.

    BE-02'nin SQLAlchemy yazımı gerçek bir datetime'a muhtaç.
    """
    at = datetime(2026, 8, 11, 10, 0, 0, tzinfo=UTC)
    assert Sample(at=at).model_dump()["at"] == at


def test_json_schema_keeps_date_time_format() -> None:
    """`WithJsonSchema` olmadan `format: date-time` sessizce düşerdi.

    PlainSerializer'ın `str` dönüşü serialization şemasını çıplak
    {"type": "string"}'e çeviriyor ve openapi.json bu bilgiyi kaybediyor.
    """
    schema = Sample.model_json_schema(mode="serialization")["properties"]["at"]
    assert schema["type"] == "string"
    assert schema["format"] == "date-time"

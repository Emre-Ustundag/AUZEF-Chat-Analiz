"""Framework bağımsız readiness kontrol sözleşmesi."""

import asyncio
from dataclasses import dataclass
from typing import Final, Protocol

CHECK_TIMEOUT_SECONDS: Final = 2.0
"""Tek bir bağımlılık kontrolünün süre bütçesi.

Timeout OLMADAN asılı kalan bir PostgreSQL/Redis bağlantısı `/ready`'i süresiz
bloklar: orkestratör "hazır değil" yerine "cevapsız" görür, probe kendi
timeout'una düşer ve pod hiç sinyal üretmemiş gibi davranır. Kendi süresini
garanti etmek bir readiness ucunun temel görevidir.
"""


class ReadinessCheck(Protocol):
    """Sonraki kartların PostgreSQL/Redis kontrolleri için adapter sınırı."""

    @property
    def name(self) -> str: ...

    async def check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    name: str
    ready: bool


async def _run_one(dependency: ReadinessCheck) -> ReadinessResult:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            ready = await dependency.check()
    # Adapter hatası da timeout da public cevaba kaçmamalı; readiness yalnızca
    # başarılı/başarısız sinyalini dışarı verir. `CancelledError` BaseException
    # olduğu için burada yakalanmaz: istek gerçekten iptal edildiyse yayılmalı.
    except Exception:
        ready = False
    return ReadinessResult(name=dependency.name, ready=ready)


async def run_readiness_checks(checks: tuple[ReadinessCheck, ...]) -> list[ReadinessResult]:
    """Kontrolleri paralel ve süre bütçeli çalıştırır.

    Paralel: toplam süre en yavaş kontrol kadardır, kontrollerin toplamı kadar
    değil. Sıra korunur — cevaptaki `checks` listesi kayıt sırasını yansıtır.
    """
    results: list[ReadinessResult] = list(
        await asyncio.gather(*(_run_one(dependency) for dependency in checks))
    )
    return results

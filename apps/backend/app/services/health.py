"""Framework bağımsız readiness kontrol sözleşmesi."""

from dataclasses import dataclass
from typing import Protocol


class ReadinessCheck(Protocol):
    """Sonraki kartların PostgreSQL/Redis kontrolleri için adapter sınırı."""

    @property
    def name(self) -> str: ...

    async def check(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    name: str
    ready: bool


async def run_readiness_checks(checks: tuple[ReadinessCheck, ...]) -> list[ReadinessResult]:
    """Kontrolleri sırayla çalıştırır; exception'ı güvenli başarısızlığa çevirir."""
    results: list[ReadinessResult] = []
    for dependency in checks:
        try:
            ready = await dependency.check()
        # Adapter hatası public cevaba kaçmamalı; readiness yalnızca
        # başarılı/başarısız sinyalini dışarı verir.
        except Exception:
            ready = False
        results.append(ReadinessResult(name=dependency.name, ready=ready))
    return results

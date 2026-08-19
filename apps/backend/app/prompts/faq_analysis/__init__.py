"""Sürümlü SSS-analiz promptları — ADR §9.

Prompt metinleri backend'de sürümlenir ve istemciden GELMEZ. Frontend
yalnızca bir sürüm ETİKETİ (`prompt_version`) gönderir; o etiketin
karşılığı burada çözülür. Kullanıcının prompt metnini etkileyebildiği bir
yol olsaydı, prompt injection savunmasının tamamı anlamsız olurdu.

Yeni sürüm eklerken: yeni bir modül (`v2.py`) yaz, `_REGISTRY`'ye ekle.
Var olan bir sürümün metnini DEĞİŞTİRME — `prompt_hash` metinden türetiliyor
ve eski raporların izlenebilirliği buna dayanıyor.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.prompts.faq_analysis import v1, v2


@dataclass(frozen=True)
class PromptBundle:
    """Bir prompt sürümünün tüm parçaları.

    DİKKAT: burada da sayısal alan YOK. Şemalar modelin sayı döndürmesine
    izin vermez (ADR §4).
    """

    version: str

    map_system: str
    map_user_template: str
    map_schema: dict[str, Any]

    reduce_system: str
    reduce_user_template: str
    reduce_schema: dict[str, Any]

    @property
    def text_hash(self) -> str:
        """Prompt METİNLERİNİN SHA-256'sı — rapordaki izlenebilirlik çıpası.

        Şemalar da dâhil edilseydi hash, prompt davranışını değiştirmeyen
        bir açıklama düzeltmesinde bile değişirdi. Modelin davranışını
        belirleyen asıl şey metinlerdir; hash onları izler.
        """
        payload = "\n\x00\n".join(
            (self.version, self.map_system, self.map_user_template, self.reduce_system)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


V1 = PromptBundle(
    version=v1.VERSION,
    map_system=v1.MAP_SYSTEM_PROMPT,
    map_user_template=v1.MAP_USER_TEMPLATE,
    map_schema=v1.MAP_SCHEMA,
    reduce_system=v1.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v1.REDUCE_USER_TEMPLATE,
    reduce_schema=v1.REDUCE_SCHEMA,
)

V2 = PromptBundle(
    version=v2.VERSION,
    map_system=v2.MAP_SYSTEM_PROMPT,
    map_user_template=v2.MAP_USER_TEMPLATE,
    map_schema=v2.MAP_SCHEMA,
    reduce_system=v2.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v2.REDUCE_USER_TEMPLATE,
    reduce_schema=v2.REDUCE_SCHEMA,
)

_REGISTRY: dict[str, PromptBundle] = {V1.version: V1, V2.version: V2}

#: `domain/model_catalog.DEFAULT_PROMPT_VERSION` ile aynı olmalı.
DEFAULT_VERSION = V2.version


class UnknownPromptVersionError(Exception):
    """İstenen prompt sürümü backend'de tanımlı değil."""


def get_prompt(version: str) -> PromptBundle:
    """Sürüm etiketini prompt paketine çözer.

    Bilinmeyen sürümde SESSİZCE VARSAYILANA DÜŞMEZ: kullanıcı `v2` isteyip
    `v1` sonucu alsaydı, rapordaki `prompt_version` alanı yalan söylerdi.
    """
    bundle = _REGISTRY.get(version)
    if bundle is None:
        raise UnknownPromptVersionError(version)
    return bundle


def is_known_version(version: str) -> bool:
    return version in _REGISTRY


__all__ = [
    "DEFAULT_VERSION",
    "V1",
    "V2",
    "PromptBundle",
    "UnknownPromptVersionError",
    "get_prompt",
    "is_known_version",
]

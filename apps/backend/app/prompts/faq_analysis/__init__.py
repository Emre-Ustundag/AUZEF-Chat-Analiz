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

from app.prompts.faq_analysis import v1, v2, v3, v4, v5, v6, v7


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

    refine_system: str | None = None
    refine_user_template: str | None = None
    refine_schema: dict[str, Any] | None = None

    @property
    def text_hash(self) -> str:
        """Prompt METİNLERİNİN SHA-256'sı — rapordaki izlenebilirlik çıpası.

        Şemalar da dâhil edilseydi hash, prompt davranışını değiştirmeyen
        bir açıklama düzeltmesinde bile değişirdi. Modelin davranışını
        belirleyen asıl şey metinlerdir; hash onları izler.
        """
        parts = [self.version, self.map_system, self.map_user_template, self.reduce_system]
        if self.refine_system is not None and self.refine_user_template is not None:
            parts.extend((self.refine_system, self.refine_user_template))
        payload = "\n\x00\n".join(parts)
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

V3 = PromptBundle(
    version=v3.VERSION,
    map_system=v3.MAP_SYSTEM_PROMPT,
    map_user_template=v3.MAP_USER_TEMPLATE,
    map_schema=v3.MAP_SCHEMA,
    reduce_system=v3.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v3.REDUCE_USER_TEMPLATE,
    reduce_schema=v3.REDUCE_SCHEMA,
)

V4 = PromptBundle(
    version=v4.VERSION,
    map_system=v4.MAP_SYSTEM_PROMPT,
    map_user_template=v4.MAP_USER_TEMPLATE,
    map_schema=v4.MAP_SCHEMA,
    reduce_system=v4.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v4.REDUCE_USER_TEMPLATE,
    reduce_schema=v4.REDUCE_SCHEMA,
)

V5 = PromptBundle(
    version=v5.VERSION,
    map_system=v5.MAP_SYSTEM_PROMPT,
    map_user_template=v5.MAP_USER_TEMPLATE,
    map_schema=v5.MAP_SCHEMA,
    reduce_system=v5.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v5.REDUCE_USER_TEMPLATE,
    reduce_schema=v5.REDUCE_SCHEMA,
    refine_system=v5.REFINE_SYSTEM_PROMPT,
    refine_user_template=v5.REFINE_USER_TEMPLATE,
    refine_schema=v5.REFINE_SCHEMA,
)

V6 = PromptBundle(
    version=v6.VERSION,
    map_system=v6.MAP_SYSTEM_PROMPT,
    map_user_template=v6.MAP_USER_TEMPLATE,
    map_schema=v6.MAP_SCHEMA,
    reduce_system=v6.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v6.REDUCE_USER_TEMPLATE,
    reduce_schema=v6.REDUCE_SCHEMA,
    refine_system=v6.REFINE_SYSTEM_PROMPT,
    refine_user_template=v6.REFINE_USER_TEMPLATE,
    refine_schema=v6.REFINE_SCHEMA,
)

V7 = PromptBundle(
    version=v7.VERSION,
    map_system=v7.MAP_SYSTEM_PROMPT,
    map_user_template=v7.MAP_USER_TEMPLATE,
    map_schema=v7.MAP_SCHEMA,
    reduce_system=v7.REDUCE_SYSTEM_PROMPT,
    reduce_user_template=v7.REDUCE_USER_TEMPLATE,
    reduce_schema=v7.REDUCE_SCHEMA,
    refine_system=v7.REFINE_SYSTEM_PROMPT,
    refine_user_template=v7.REFINE_USER_TEMPLATE,
    refine_schema=v7.REFINE_SCHEMA,
)

_REGISTRY: dict[str, PromptBundle] = {
    V1.version: V1,
    V2.version: V2,
    V3.version: V3,
    V4.version: V4,
    V5.version: V5,
    V6.version: V6,
    V7.version: V7,
}

#: `domain/model_catalog.DEFAULT_PROMPT_VERSION` ile aynı olmalı.
DEFAULT_VERSION = V3.version


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
    "V3",
    "V4",
    "V5",
    "PromptBundle",
    "UnknownPromptVersionError",
    "get_prompt",
    "is_known_version",
]

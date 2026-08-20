"""LLM map kaydı render'ı — classifier ve maliyet hesabının ortak kaynağı."""

from app.pipeline.preprocess import RecordGroup
from app.prompts.faq_analysis.v1 import escape_record_text


def render_record(group: RecordGroup) -> str:
    """Legacy kaydı byte-for-byte korur; contextual kaydı güvenli XML'e çevirir."""
    if not group.contextual:
        return f'<kayit id="{group.record_id}">{escape_record_text(group.redacted_text)}</kayit>'

    context = "".join(
        f'<mesaj rol="{turn.role}">{escape_record_text(turn.redacted_text)}</mesaj>'
        for turn in group.context_turns
    )
    target = escape_record_text(group.redacted_text)
    return (
        f'<kayit id="{group.record_id}"><baglam>{context}</baglam><hedef>{target}</hedef></kayit>'
    )


__all__ = ["render_record"]

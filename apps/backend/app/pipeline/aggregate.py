"""Deterministik toplama — ADR §4 ve §5 Aşama B madde 8.

BU MODÜL MİMARİNİN EN KRİTİK KARARINI UYGULAR:

    "Nihai adet ve oranları backend, mesajların gerçek frekanslarından
    deterministik olarak hesaplar. Böylece LLM'in sayı uydurması engellenir."

Bu yüzden fonksiyonların girdisi bilinçle sınırlıdır:

* `PreprocessResult` — gerçek satır sayımları ve `RecordGroup.count`
  frekansları. Kaynağı dosyanın kendisidir.
* `Classification` — YALNIZCA kimlik eşlemesi. İçinde tek bir sayı yoktur.

Buradaki hiçbir sayı sınıflandırıcıdan gelmez. Faz 3'te `Classification`'ı
bir LLM üretecek; bu modül değişmeyecek ve LLM'in ürettiği bir "count" için
girecek yer olmayacak.

`top_n` kırpması ve plan §1.2 kararı:

* `top_questions` `top_n` ile kırpılır.
* Tema `count`'u kırpmadan ETKİLENMEZ — tema büyüklüğü o temaya düşen tüm
  mesajların sayısıdır. Kırpılsaydı dashboard'daki oranlar yanlış olurdu.
* `related_question_ids` yalnızca RAPORDA YER ALAN sorulara bağlanır; aksi
  hâlde arayüz çözemeyeceği bir kimliğe bağlantı verirdi.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from app.core.config import Settings
from app.pipeline.classifier import Classification
from app.pipeline.preprocess import PreprocessResult, RecordGroup
from app.schemas.analysis import PricingSnapshot, RowFilter
from app.schemas.report import (
    REPORT_SCHEMA_VERSION,
    AnalysisReport,
    AnalysisWarning,
    PreprocessingSummary,
    SourceSummary,
    Theme,
    TokenUsage,
    TopQuestion,
    percentage_half_up,
)


class AggregationError(Exception):
    """Toplama değişmezlerinden biri ihlal edildi.

    Örneğin sınıflandırıcı aynı kaydı iki soruya eşlemiş olabilir — bu, o
    mesajın iki kez sayılması demektir ve rapor sessizce yanlış çıkardı.
    Faz 3'te LLM'in bu hatayı yapması ÇOK OLASI; bu yüzden kontrol burada,
    vekil sınıflandırıcıyla birlikte kuruluyor.
    """


def _percentage(count: int, total: int) -> float:
    """Oranı adetten türetir. Tek yer burasıdır; hiçbir yüzde elle yazılmaz."""
    # Rapor şemasının değişmeziyle aynı, float sınır vakalarından bağımsız
    # half-up kuralı. Python ``round`` eşitlikte half-even kullandığı için
    # 54 / 2400 gibi değerlerde şema doğrulamasını bozabiliyordu.
    return percentage_half_up(count, total)


def _prompt_hash(classifier_id: str, prompt_version: str) -> str:
    """İzlenebilirlik: sonucu hangi sınıflandırıcı/prompt üretti.

    FAZ 2 NOTU: ortada gerçek bir prompt YOK, çünkü LLM çağrılmıyor. Hash
    vekil sınıflandırıcının kimliğinden üretiliyor ki rapora bakan biri
    sonucun bir LLM'den gelmediğini ayırt edebilsin. Faz 3'te girdi gerçek
    system prompt metni olacak.
    """
    digest = hashlib.sha256(f"{classifier_id}|{prompt_version}".encode()).hexdigest()
    return f"sha256:{digest[:12]}"


def _validate_assignment(
    classification: Classification,
    groups: dict[str, RecordGroup],
) -> None:
    """Eşlemenin toplama için güvenli olduğunu doğrular."""
    seen: set[str] = set()
    for question in classification.questions:
        for record_id in question.record_ids:
            if record_id in seen:
                raise AggregationError(f"record_assigned_twice:{record_id}")
            if record_id not in groups:
                raise AggregationError(f"unknown_record_id:{record_id}")
            seen.add(record_id)


def aggregate(
    *,
    analysis_id: UUID,
    preprocess_result: PreprocessResult,
    classification: Classification,
    filename: str,
    sheet_name: str,
    text_column: str,
    model: str,
    prompt_version: str,
    classifier_id: str,
    top_n: int,
    settings: Settings,
    row_filters: list[RowFilter] | None = None,
    extra_warnings: list[AnalysisWarning] | None = None,
    token_usage: TokenUsage | None = None,
    estimated_cost_usd: float = 0.0,
    cost_source: Literal["provider", "calculated"] = "calculated",
    pricing_snapshot: PricingSnapshot | None = None,
) -> AnalysisReport:
    """Gerçek frekanslardan raporu üretir.

    `token_usage` / `estimated_cost_usd` GEÇİŞ PARAMETRELERİDİR (Faz 3).
    Toplama matematiğine GİRMEZLER: hiçbir adet, oran veya Top N kararı
    bunlara bakmaz. Sağlayıcının faturalama ölçümünü rapora taşımanın tek
    yolu bu — `extra_warnings` ile aynı kalıp. Varsayılanları 0 olduğu için
    Faz 2'nin çağrıları (ve LLM'siz testler) davranış değiştirmeden çalışır.
    """
    groups = {group.record_id: group for group in preprocess_result.groups}
    _validate_assignment(classification, groups)

    analyzed = preprocess_result.analyzed_count

    # ---- soru adetleri: yalnızca RecordGroup.count toplamları ----
    question_counts: dict[str, int] = {}
    question_examples: dict[str, list[str]] = {}

    for question in classification.questions:
        members = [groups[record_id] for record_id in question.record_ids]
        total = sum(member.count for member in members)
        question_counts[question.question_id] = total

        # Örnekler: en sık kayıtlardan, redakte edilmiş hâlleriyle (ADR §9).
        examples: list[str] = []
        for member in sorted(members, key=lambda g: (-g.count, g.record_id)):
            for example in member.examples:
                if example not in examples:
                    examples.append(example)
                if len(examples) >= settings.report_examples_per_question:
                    break
            if len(examples) >= settings.report_examples_per_question:
                break
        question_examples[question.question_id] = examples

    # ---- Top N kırpması ----
    ordered_questions = sorted(
        classification.questions,
        key=lambda q: (-question_counts[q.question_id], q.question_id),
    )
    included = ordered_questions[:top_n]
    included_ids = {question.question_id for question in included}

    top_questions = [
        TopQuestion(
            id=question.question_id,
            canonical_question=question.canonical_question,
            count=question_counts[question.question_id],
            percentage=_percentage(question_counts[question.question_id], analyzed),
            redacted_examples=question_examples[question.question_id],
        )
        for question in included
    ]

    # ---- tema adetleri: KIRPMADAN ETKİLENMEZ (plan §1.2) ----
    themes: list[Theme] = []
    for theme in classification.themes:
        # Temanın adedi, ona bağlı TÜM soruların adedidir — raporda görünüp
        # görünmediklerine bakılmaz.
        count = sum(question_counts.get(qid, 0) for qid in theme.question_ids)
        themes.append(
            Theme(
                id=theme.theme_id,
                name=theme.name,
                count=count,
                percentage=_percentage(count, analyzed),
                # Plan §1.2: yalnızca raporda yer alan sorulara bağlanır.
                related_question_ids=[qid for qid in theme.question_ids if qid in included_ids],
            )
        )
    themes.sort(key=lambda theme: (-theme.count, theme.id))

    warnings = list(extra_warnings or [])
    for code, message in classification.warnings:
        warnings.append(AnalysisWarning(code=code, message=message))

    theme_total = sum(theme.count for theme in themes)
    if theme_total > analyzed:
        # Değişmez ihlali: temalar analiz edilen kayıttan fazlasını sayamaz.
        raise AggregationError(f"theme_total_exceeds_analyzed:{theme_total}>{analyzed}")

    return AnalysisReport(
        schema_version=REPORT_SCHEMA_VERSION,
        analysis_id=analysis_id,
        status="completed",
        generated_at=datetime.now(UTC),
        source_summary=SourceSummary(
            filename=filename,
            sheet_name=sheet_name,
            text_column=text_column,
            row_filters=list(row_filters or []),
            total_rows=preprocess_result.total_rows,
        ),
        preprocessing_summary=PreprocessingSummary(
            analyzed_count=analyzed,
            discarded_count=preprocess_result.discarded_count,
            duplicate_count=preprocess_result.duplicate_count,
            redacted_count=preprocess_result.redacted_count,
            unique_count=preprocess_result.unique_count,
        ),
        top_questions=top_questions,
        themes=themes,
        executive_summary=build_executive_summary(top_questions, themes, analyzed),
        warnings=warnings,
        model=model,
        prompt_version=prompt_version,
        prompt_hash=_prompt_hash(classifier_id, prompt_version),
        # Token sayacı SAĞLAYICININ `usage` bloğundan gelir, modelin
        # metninden değil. Çağıran vermezse 0 kalır — LLM'siz bir koşuda
        # (vekil sınıflandırıcı) tüketim GERÇEKTEN sıfırdır ve uydurma bir
        # tahmin yazmak raporu yalancı yapardı.
        token_usage=token_usage or TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        estimated_cost_usd=estimated_cost_usd,
        cost_source=cost_source,
        pricing_snapshot=pricing_snapshot,
    )


def build_executive_summary(
    top_questions: list[TopQuestion],
    themes: list[Theme],
    analyzed: int,
) -> str:
    """Özet metni SAYILARDAN türetilir, üretilmez.

    Faz 3'te LLM'in yazması düşünülebilir ama içindeki her sayı yine
    buradan gelmeli (ADR §4).
    """
    if analyzed == 0 or not top_questions:
        return "Analiz edilebilecek kayıt bulunamadı."

    first = top_questions[0]
    parts = [
        f"Analize giren {analyzed:,} mesajın %{first.percentage} kadarı "
        f"«{first.canonical_question}» başlığında toplanıyor.".replace(",", "."),
    ]

    if len(top_questions) >= 3:
        top3 = sum(question.count for question in top_questions[:3])
        # Ek yerine "kadarını": Türkçe'de sayıya göre değişen ekleri
        # (%23.6'sını / %8'ini) doğru üretmek ayrı bir iş; yanlış ek yazmaktansa
        # eksiz bir kalıp kullanılıyor.
        parts.append(
            f"İlk üç soru analiz edilen mesajların %{_percentage(top3, analyzed)} kadarını "
            "oluşturuyor."
        )

    if themes:
        biggest = themes[0]
        parts.append(
            f"En büyük tema «{biggest.name}» ({biggest.count} mesaj, %{biggest.percentage})."
        )

    return " ".join(parts)

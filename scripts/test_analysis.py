#!/usr/bin/env python3
"""AUZEF Chat Analiz — CLI Test Aracı.

Bu araç, terminal ortamınızdaki `OPENROUTER_API_KEY` ortam değişkenini okuyarak
güvenli bir şekilde dosyayı yükler, analizi başlatır ve sonuç raporunu ekrana basar.
API anahtarınız hiçbir dosyaya kaydedilmez veya loglanmaz.

Kullanım:
    export OPENROUTER_API_KEY="sk-or-v1-..."
    python3 scripts/test_analysis.py --file outputs/ornek-baglamli-120.xlsx --prompt-version faq_analysis/v7
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AUZEF Chat Analiz CLI Test Aracı")
    parser.add_argument(
        "--file",
        "-f",
        default="outputs/ornek-baglamli-120.xlsx",
        help="Analiz edilecek .xlsx dosya yolu (varsayılan: outputs/ornek-baglamli-120.xlsx)",
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:3000/api/v1",
        help="Backend API taban adresi (varsayılan: http://localhost:3000/api/v1)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default="google/gemini-2.5-flash",
        help="Kullanılacak model (varsayılan: google/gemini-2.5-flash)",
    )
    parser.add_argument(
        "--prompt-version",
        "-p",
        default="faq_analysis/v7",
        choices=[
            "faq_analysis/v1",
            "faq_analysis/v2",
            "faq_analysis/v3",
            "faq_analysis/v4",
            "faq_analysis/v5",
            "faq_analysis/v6",
            "faq_analysis/v7",
        ],
        help="Prompt sürümü (varsayılan: faq_analysis/v7)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Raporda listelenecek soru sayısı (varsayılan: 20)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=10.0,
        help="Maksimum maliyet tavanı USD (varsayılan: 10.0)",
    )
    return parser.parse_args()


def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("[!] OPENROUTER_API_KEY ortam değişkeni bulunamadı.", file=sys.stderr)
        key = getpass.getpass("Lütfen OpenRouter API Anahtarınızı girin: ").strip()
    if not key:
        print("Hata: Geçerli bir API anahtarı girilmedi.", file=sys.stderr)
        sys.exit(1)
    return key


def upload_file(api_url: str, file_path: str) -> str:
    if not os.path.isfile(file_path):
        print(f"Hata: Dosya bulunamadı: {file_path}", file=sys.stderr)
        sys.exit(1)

    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(
        b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    )
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        f"{api_url}/uploads",
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    print(f"[*] Dosya yükleniyor: {filename} ({len(file_bytes) / 1024:.1f} KB)...")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            upload_id = data["upload_id"]
            print(f"[✓] Dosya yüklendi (Upload ID: {upload_id})")
            return upload_id
    except urllib.error.HTTPError as e:
        print(f"Yükleme hatası ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)


def wait_for_upload_profile(api_url: str, upload_id: str) -> dict:
    print("[*] Dosya profili ve doğrulama bekleniyor...")
    while True:
        req = urllib.request.Request(f"{api_url}/uploads/{upload_id}")
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                status = data["status"]
                if status == "ready":
                    profile = data["profile"]
                    sheet = profile["sheets"][0]
                    cols_str = ", ".join([c["name"] for c in sheet["columns"][:6]])
                    if len(sheet["columns"]) > 6:
                        cols_str += ", ..."
                    print(
                        f"[✓] Dosya hazır: Sayfa '{sheet['name']}', {sheet['row_count']} satır. Kolonlar: [{cols_str}]"
                    )
                    return data
                elif status == "failed":
                    error = data.get("error", {})
                    print(f"Hata: Profil çıkarma başarısız: {error.get('title', '')} - {error.get('detail', '')}", file=sys.stderr)
                    sys.exit(1)
        except urllib.error.HTTPError as e:
            print(f"Durum kontrol hatası ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)


def start_analysis(
    api_url: str,
    upload_id: str,
    upload_data: dict,
    model: str,
    prompt_version: str,
    top_n: int,
    max_cost: float,
    api_key: str,
) -> str:
    profile = upload_data["profile"]
    sheet = profile["sheets"][0]
    sheet_name = sheet["name"]
    col_names = [c["name"] for c in sheet["columns"]]
    cols_set = set(col_names)

    # Kolon seçimi
    text_col = "message_text_clean" if "message_text_clean" in cols_set else col_names[0]

    is_contextual = prompt_version in ("faq_analysis/v4", "faq_analysis/v5", "faq_analysis/v6", "faq_analysis/v7")

    if is_contextual:
        has_conv_cols = all(c in cols_set for c in ("session_id", "message_order", "direction", "message_type"))
        if not has_conv_cols:
            print(
                f"[!] Uyarı: {prompt_version} bağlamsal analiz gerektirir ancak dosyada gerekli konuşma kolonları "
                f"(session_id, message_order, direction, message_type) bulunamadı.",
                file=sys.stderr,
            )
            print(f"    Mevcut kolonlar: {col_names}", file=sys.stderr)
            print(f"    Lütfen 'outputs/ornek-baglamli-120.xlsx' gibi konuşma kolonlarını içeren bir dosya kullanın.", file=sys.stderr)
            sys.exit(1)

        conv_config = {
            "session_id_column": "session_id",
            "message_order_column": "message_order",
            "role_column": "direction",
            "message_type_column": "message_type",
            "user_role_values": ["Kullanıcı"],
            "assistant_role_values": ["Bot"],
            "include_assistant_context": False,
            "target_message_types": ["text"],
            "context_message_types": ["text", "quick_reply", "single-choice"],
            "max_context_turns": 4,
            "max_context_tokens": 1000,
        }
        analysis_mode = "contextual_user_turns"
    else:
        conv_config = None
        analysis_mode = "message"

    payload = {
        "upload_id": upload_id,
        "sheet_name": sheet_name,
        "text_column": text_col,
        "analysis_mode": analysis_mode,
        "conversation_config": conv_config,
        "model": model,
        "prompt_version": prompt_version,
        "top_n": top_n,
        "max_cost_usd": max_cost,
        "row_filters": [],
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{api_url}/analyses",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-OpenRouter-Key": api_key,
        },
        method="POST",
    )

    print(f"[*] Analiz başlatılıyor (Model: {model}, Prompt: {prompt_version}, Mod: {analysis_mode})...")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            analysis_id = data["analysis_id"]
            print(f"[✓] Analiz işi kuyruğa alındı (Analysis ID: {analysis_id})")
            return analysis_id
    except urllib.error.HTTPError as e:
        print(f"Analiz başlatma hatası ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)


def monitor_analysis(api_url: str, analysis_id: str) -> dict:
    last_stage = None
    last_progress = -1
    print("[*] Analiz yürütülüyor...")

    while True:
        req = urllib.request.Request(f"{api_url}/analyses/{analysis_id}")
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                status = data["status"]
                stage = data.get("current_stage")
                progress = data.get("progress_percentage", 0)

                if (stage != last_stage or progress != last_progress) and stage:
                    print(f"  → [{stage.upper()}] İlerleme: %{progress}")
                    last_stage = stage
                    last_progress = progress

                if status == "completed":
                    print("[✓] Analiz başarıyla tamamlandı!")
                    break
                elif status in ("failed", "cancelled", "timeout"):
                    err = data.get("error", {})
                    print(f"Hata: Analiz sonlandı ({status}): {err.get('title')} - {err.get('detail')}", file=sys.stderr)
                    sys.exit(1)
        except urllib.error.HTTPError as e:
            print(f"Durum kontrol hatası ({e.code}): {e.read().decode('utf-8')}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1.0)

    # Sonuç raporunu al
    res_req = urllib.request.Request(f"{api_url}/analyses/{analysis_id}/result")
    with urllib.request.urlopen(res_req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def print_report(report: dict) -> None:
    print("\n" + "=" * 80)
    print("                    AUZEF SSS ANALİZ RAPORU")
    print("=" * 80)

    prep = report.get("preprocessing_summary", {})
    cost = report.get("cost_summary", {})
    source = report.get("source_summary", {})

    print(f"Model: {report.get('model')}  |  Prompt Sürümü: {report.get('prompt_version')}")
    print(f"Toplam Satır: {source.get('total_rows', 0)}  |  Analiz Edilen: {prep.get('analyzed_count', 0)}  |  Tekil: {prep.get('unique_count', 0)}")
    print(f"Tekilleştirilen: {prep.get('duplicate_count', 0)}  |  Maskelenen (PII): {prep.get('redacted_count', 0)}  |  Elenen: {prep.get('discarded_count', 0)}")
    if prep.get("context_only_count"):
        print(f"Yalnız Bağlam (Bot): {prep.get('context_only_count', 0)}")

    print(f"\nMaliyet: ${cost.get('actual_cost_usd', 0):.4f} USD (Tahmin: ${cost.get('estimated_cost_usd', 0):.4f} USD)")
    print(f"Token: {cost.get('actual_prompt_tokens', 0):,} Girdi / {cost.get('actual_completion_tokens', 0):,} Çıktı")

    print("\n" + "-" * 80)
    print("TEMA DAĞILIMI")
    print("-" * 80)
    themes = report.get("themes", [])
    for t in themes:
        pct = t.get("percentage", 0.0)
        cnt = t.get("count", 0)
        bar = "█" * int(pct / 2)
        print(f"  {t['name']:<35} : {cnt:>5} adet (%{pct:>5.1f}) {bar}")

    print("\n" + "-" * 80)
    print(f"SIK SORULAN SORULAR (Top {len(report.get('questions', []))})")
    print("-" * 80)
    print(f" {'#':<3} | {'Tema':<20} | {'Adet':>6} | {'Oran':>6} | {'Soru'}")
    print("-" * 80)
    for idx, q in enumerate(report.get("questions", []), 1):
        print(f" {idx:<3} | {q.get('theme', ''):<20} | {q.get('count', 0):>6} | %{q.get('percentage', 0.0):>5.1f} | {q.get('canonical_question', '')}")

    print("=" * 80 + "\n")


def main() -> None:
    args = parse_args()
    api_key = get_api_key()

    upload_id = upload_file(args.api_url, args.file)
    upload_data = wait_for_upload_profile(args.api_url, upload_id)
    analysis_id = start_analysis(
        api_url=args.api_url,
        upload_id=upload_id,
        upload_data=upload_data,
        model=args.model,
        prompt_version=args.prompt_version,
        top_n=args.top_n,
        max_cost=args.max_cost,
        api_key=api_key,
    )
    report = monitor_analysis(args.api_url, analysis_id)
    print_report(report)


if __name__ == "__main__":
    main()

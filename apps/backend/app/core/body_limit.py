"""İstek gövdesi boyut sınırı — ADR §9, plan §3.2e.

## Neden ayrı bir ASGI middleware gerekiyor

`POST /api/v1/uploads` imzası `file: Annotated[UploadFile, File()]`. FastAPI
bu bağımlılığı çözerken `await request.form()` çağırır, yani **multipart
gövdenin tamamı** parse edilip diske spool edilir — endpoint gövdesindeki ilk
satır çalışmadan önce. Sonuç olarak endpoint içindeki iki savunma da geç
kalıyordu:

* `Content-Length` kontrolü "gövdeyi okumadan" demiyordu; gövde çoktan
  okunmuştu.
* Akış sayacı (`while chunk := await file.read(...)`) bitmiş bir
  `SpooledTemporaryFile` üzerinde dönüyordu — ikinci bir tam disk kopyası.

Somut sonuç: `Content-Length` başlığı tutarlı 5 GB'lık bir POST, 413 dönmeden
önce 5 GB'ı diske yazıyordu. Birkaç eşzamanlı istek API container'ının geçici
alanını doldurmaya yeter.

Bu yüzden sınır form parse'ından ÖNCE, ASGI katmanında uygulanmak zorunda.

## Nasıl çalışıyor

İki katman, ikisi de gövdeyi tamponlamadan:

1. **`Content-Length`** — istemci bildirmişse tek karşılaştırmayla reddedilir,
   tek bayt okunmadan.
2. **Sayan `receive`** — `Transfer-Encoding: chunked` durumunda beyan yoktur.
   Gövde parçaları geçerken sayılır; sınır aşılınca `http.disconnect`
   döndürülür ve uygulama gövdenin KALANINI hiç okumaz.

## Neden istisna değil, `http.disconnect`

İlk uygulama sayaç aşıldığında özel bir istisna fırlatıyordu. ÇALIŞMADI:
FastAPI form parse'ını geniş bir `except` ile sarıyor ve istisnayı
`HTTPException(400)`'e çeviriyor, yani istisna bu middleware'e hiç
ulaşmıyordu. Ölçülen sonuç 413 yerine 400 `INTERNAL_ERROR` oldu (sayaç
tetiklenmiş, gövde tüketilmemiş, ama cevap yanlış).

`http.disconnect` ASGI'nin bu iş için tanımlı sinyali: uygulama okumayı
bırakır. Uygulamanın buna karşılık ürettiği cevap (ne olursa olsun)
`send` sarmalayıcısında YUTULUR ve yerine 413 gönderilir. Böylece davranış
FastAPI'nin veya Starlette'in iç hata yönetimindeki değişikliklere bağımlı
değil.

Cevap `errors.problem_response` ile üretiliyor: `JSONResponse` kendisi bir
ASGI uygulaması olduğu için doğrudan çağrılabiliyor. Gövdeyi elle kurmak
serileştirmenin (özellikle `exclude_none`) endpoint yolundan ayrışması
riskini taşırdı.

## Endpoint'teki sayaç KALDIRILMADI

Bu middleware HTTP gövdesinin tamamını sayar (multipart zarfı dahil), endpoint
ise gerçek dosya baytlarını sayar. İkisi farklı şeyleri ölçüyor ve endpoint'in
sayacı daha kesin; derinlik savunması olarak yerinde bırakıldı.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import build_problem, problem_response
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Multipart zarfı (boundary, başlıklar, satır sonları) için pay. Sınır dosya
#: boyutu için tanımlı; zarf yüzünden bir baytlık aşımda reddetmek yanlış olur.
MULTIPART_ENVELOPE_ALLOWANCE = 1024 * 1024


class BodySizeLimitMiddleware:
    """Gövdesi sınırı aşan istekleri form parse'ından önce reddeder."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.limit = max_bytes + MULTIPART_ENVELOPE_ALLOWANCE

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        # ---- Katman 1: beyan edilmiş uzunluk, tek bayt okumadan ----
        declared = _declared_length(scope)
        if declared is not None and declared > self.limit:
            logger.info("request_rejected_by_content_length", extra={"declared": declared})
            await _reject(scope, receive, send, max_bytes=self.max_bytes)
            return

        # ---- Katman 2: akış sırasında sayan receive ----
        total = 0
        exceeded = False
        response_started = False

        async def counting_receive() -> Message:
            nonlocal total, exceeded
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.limit and not exceeded:
                    logger.info("request_rejected_by_stream_counter", extra={"read": total})
                    exceeded = True
            if exceeded:
                # Gövdenin kalanını uygulamaya HİÇ vermiyoruz.
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            # Sınır aşıldıktan sonra uygulamanın ürettiği cevap yutuluyor:
            # yerine 413 gönderilecek. Uygulama sınır aşılmadan ÖNCE cevaba
            # başladıysa (upload akışında olmaz) ona dokunulmuyor.
            if exceeded and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            # Kopukluğun uygulamada yol açtığı istisna yalnızca sınır
            # aşıldığında yutulur; başka her hata yukarı gitmeye devam eder.
            if not exceeded:
                raise
            logger.debug("request_aborted_after_limit")

        if exceeded and not response_started:
            await _reject(scope, receive, send, max_bytes=self.max_bytes)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject(scope: Scope, receive: Receive, send: Send, *, max_bytes: int) -> None:
    """RFC 9457 biçiminde 413 gönderir.

    Metin endpoint'teki reddetme yoluyla aynı: kullanıcı sınırı hangi katmanın
    yakaladığına göre farklı bir mesaj görmemeli.
    """
    problem = build_problem(
        "UPLOAD_TOO_LARGE",
        f"En fazla {max_bytes // (1024 * 1024)} MB .xlsx yüklenebilir.",
    )
    await problem_response(problem)(scope, receive, send)

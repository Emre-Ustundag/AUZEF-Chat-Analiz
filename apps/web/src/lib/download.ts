/**
 * Bellekteki bir gövdeyi kullanıcının diskine indirir.
 *
 * Tarayıcıda dosya kaydetmenin standart bir API'si yok; yaygın çözüm geçici
 * bir object URL'e bağlı görünmez bir <a> öğesini tıklamak. Object URL elle
 * serbest bırakılmazsa gövde sekme kapanana kadar bellekte kalır — rapor
 * export'ları büyük olabildiği için burada bu önemli.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;

  // Firefox tıklamayı yalnızca öğe belgeye bağlıysa işliyor.
  document.body.append(anchor);
  anchor.click();
  anchor.remove();

  URL.revokeObjectURL(url);
}

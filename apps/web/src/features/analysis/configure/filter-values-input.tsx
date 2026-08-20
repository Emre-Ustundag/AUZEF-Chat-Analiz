"use client";

import { X } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

interface FilterValuesInputProps {
  id: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  "aria-describedby"?: string;
}

/**
 * Satır filtresinin "kabul edilen değerler" alanı — etiket (chip) girişi.
 *
 * Önceki hâl tek bir metin kutusuydu ve değerleri virgülden bölüyordu. İki
 * kusuru vardı, ikisi de gerçek veride ortaya çıkıyor:
 *
 * 1. Kullanıcı virgüle bastığı ANDA dizi `["Kullanıcı", ""]` oluyor ve Zod
 *    "Filtre değeri boş olamaz." diye hata veriyordu — daha ikinci değeri
 *    yazmaya başlamadan form kırmızıya dönüyordu.
 * 2. Virgül İÇEREN bir kolon değeri (örn. "Ankara, Çankaya") hiç girilemiyordu;
 *    ayraç ile verinin kendisi aynı karakterdi.
 *
 * Chip alanında değer ancak TAMAMLANDIĞINDA (Enter, virgül veya odak kaybı)
 * diziye giriyor. Bu yüzden ara durumda boş değer hiç üretilmiyor ve Zod
 * şeması DEĞİŞMEDEN geçerli kalıyor. Yapıştırılan "a, b, c" gibi metinler de
 * bölünüyor — eski alışkanlık çalışmaya devam ediyor.
 *
 * Virgül İÇEREN değerin yolu yapıştırmadır: yazarken virgül her zaman ayraç
 * olduğu için, yapıştırılan metin BÖLÜNMEDEN taslağa girer ve Enter tek bir
 * değer olarak tamamlar. Yapıştırdıktan sonra virgüle basmak isteyen kullanıcı
 * yine bölünmüş hâli alır — iki yol da açık.
 */
export function FilterValuesInput({
  id,
  values,
  onChange,
  placeholder,
  "aria-describedby": describedBy,
}: FilterValuesInputProps) {
  const [draft, setDraft] = React.useState("");

  // Backend TAM eşleşme yapıyor: boş değer anlamsız, tekrar eden değer
  // gereksiz. İkisi de burada eleniyor, çünkü şemaya ulaşmadan önce
  // temizlenmiş bir dizi üretmek hata mesajı göstermekten iyidir.
  const commit = (parts: string[]) => {
    const next = [...values];
    for (const part of parts) {
      const value = part.trim();
      if (value && !next.includes(value)) next.push(value);
    }
    if (next.length !== values.length) onChange(next);
  };

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const text = event.target.value;
    if (!text.includes(",")) {
      setDraft(text);
      return;
    }
    // Virgülden önceki parçalar tamamlanmış sayılır; son parça yazılmaya
    // devam ediyor olabilir, taslakta kalır.
    const parts = text.split(",");
    const remainder = parts.pop() ?? "";
    commit(parts);
    setDraft(remainder.trimStart());
  };

  // Yapıştırılan metin BÖLÜNMEZ. Virgül içeren bir değeri (örn. "Ankara,
  // Çankaya") girmenin tek yolu budur: yazarken virgül ayraç olmak zorunda,
  // yoksa alışılmış "a, b, c" akışı kırılır.
  const handlePaste = (event: React.ClipboardEvent<HTMLInputElement>) => {
    const text = event.clipboardData.getData("text");
    if (!text) return;
    event.preventDefault();
    setDraft(draft + text);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      // Alanın içinde Enter'a basmak formu GÖNDERMEMELİ: kullanıcı değeri
      // tamamlıyor, analizi başlatmıyor.
      event.preventDefault();
      commit([draft]);
      setDraft("");
      return;
    }
    if (event.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  };

  // Odak kaybında taslağı yazmak, "yazdım ama Enter'a basmadım" durumunda
  // değerin sessizce kaybolmasını engeller — kullanıcı "Analizi başlat"a
  // tıkladığında yazdığı son değer de isteğe girer.
  const handleBlur = () => {
    if (draft.trim()) {
      commit([draft]);
      setDraft("");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-input px-2 py-1.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
      {values.map((value) => (
        <Badge key={value} variant="secondary" className="gap-1">
          {value}
          <button
            type="button"
            aria-label={`${value} değerini kaldır`}
            className="text-muted-foreground hover:text-foreground"
            onClick={() => onChange(values.filter((item) => item !== value))}
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </Badge>
      ))}
      <Input
        id={id}
        value={draft}
        placeholder={values.length === 0 ? placeholder : undefined}
        aria-describedby={describedBy}
        className="h-6 w-auto min-w-32 flex-1 border-0 px-1 focus-visible:ring-0"
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onBlur={handleBlur}
      />
    </div>
  );
}

"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";

/**
 * Açık/koyu tema anahtarı.
 *
 * İkon seçimi bilinçli olarak CSS ile yapılıyor, React state'iyle değil.
 * Yaygın `useEffect(() => setMounted(true))` deseni hem ESLint'in
 * react-hooks/set-state-in-effect kuralına takılıyor hem de gereksiz: `.dark`
 * sınıfı zaten <html> üzerinde, dolayısıyla `dark:` varyantı doğru ikonu
 * hydration'dan önce, JS hiç çalışmadan gösterebiliyor.
 *
 * Etiket her iki durumda da aynı ("Temayı değiştir"); duruma göre değişen bir
 * etiket yine sunucuda bilinemeyen bir değere bağlı olurdu.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      aria-label="Temayı değiştir"
    >
      <Sun className="size-4 dark:hidden" aria-hidden="true" />
      <Moon className="hidden size-4 dark:block" aria-hidden="true" />
    </Button>
  );
}

import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * Her testten sonra render edilen ağaç sökülür.
 *
 * Vitest'te globals kapalı olduğu için RTL'in otomatik temizliği devreye
 * girmez; temizlenmezse bir testte render edilen bileşen sonrakinin
 * sorgularına karışır ve hatalar yanlış teste yazılır.
 */
afterEach(() => {
  cleanup();
});

import type { Metadata } from "next";

import { UploadStatusScreen } from "@/features/analysis/upload/upload-status-screen";

export const metadata: Metadata = {
  title: "Dosya hazırlanıyor",
};

export default async function UploadPage(
  props: PageProps<"/yuklemeler/[uploadId]">,
) {
  // Next 16'da params bir Promise; senkron erişim kaldırıldı.
  const { uploadId } = await props.params;

  return <UploadStatusScreen uploadId={uploadId} />;
}

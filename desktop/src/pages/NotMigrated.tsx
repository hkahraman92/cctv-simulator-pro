import { Construction } from "lucide-react";

/** Honest placeholder: the module exists in the classic app, not here yet. */
export default function NotMigrated({ title }: { title: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md rounded-md bg-bg-4 p-6 text-center">
        <Construction className="mx-auto mb-3 text-warn" size={28} aria-hidden />
        <div className="text-[15px] font-semibold">{title}</div>
        <p className="mt-2 text-text-2">
          Bu modül henüz yeni arayüze taşınmadı. Hesap çekirdeği hazır; ekran sırası gelince bağlanacak. Şimdilik klasik
          uygulamada kullanılabilir.
        </p>
      </div>
    </div>
  );
}

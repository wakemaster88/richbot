"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

export function PWAProvider({ children }: { children: React.ReactNode }) {
  const [deferredPrompt, setDeferredPrompt] = useState<unknown>(null);
  const [showBanner, setShowBanner] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const dismissed = localStorage.getItem("pwa-banner-dismissed");
    const isMobile = /Android|webOS|iPhone|iPad|iPod/i.test(navigator.userAgent);
    const isStandalone = window.matchMedia("(display-mode: standalone)").matches || (window.navigator as { standalone?: boolean }).standalone;
    setShowBanner(isMobile && !isStandalone && !dismissed);
  }, [pathname]);

  useEffect(() => {
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }, []);

  const install = async () => {
    if (!deferredPrompt) return;
    (deferredPrompt as { prompt: () => Promise<{ outcome: string }> }).prompt?.();
    setShowBanner(false);
    localStorage.setItem("pwa-banner-dismissed", "1");
  };

  const dismiss = () => {
    setShowBanner(false);
    localStorage.setItem("pwa-banner-dismissed", "1");
  };

  const isLogin = pathname === "/login";

  return (
    <>
      {children}
      {showBanner && !isLogin && (
        <div
          className="fixed bottom-0 left-0 right-0 z-[100] p-4 pb-[env(safe-area-inset-bottom)]"
          style={{ background: "var(--bg-card)", borderTop: "1px solid var(--border)" }}
        >
          <div className="max-w-[1400px] mx-auto flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-[var(--text-primary)]">Zum Homescreen hinzufügen</p>
              <p className="text-[11px] text-[var(--text-tertiary)]">RichBot als App installieren</p>
            </div>
            <div className="flex gap-2">
              <button
                onClick={dismiss}
                className="px-3 py-2 rounded-lg text-[12px] text-[var(--text-tertiary)] hover:bg-[var(--bg-secondary)]"
              >
                Später
              </button>
              <button
                onClick={install}
                className="px-4 py-2 rounded-lg text-[12px] font-semibold"
                style={{ background: "var(--accent)", color: "white" }}
              >
                Hinzufügen
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

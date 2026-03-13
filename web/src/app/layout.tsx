import type { Metadata } from "next";
import { AppShell } from "@/components/AppShell";
import { PWAProvider } from "@/components/PWAProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "RichBot — Trading Dashboard",
  description: "Grid-Trading Bot — Fernsteuerung & Monitoring",
  manifest: "/manifest.json",
  appleWebApp: { capable: true, title: "RichBot" },
  themeColor: "#6366f1",
  viewport: { width: "device-width", initialScale: 1 },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="de" className="dark">
      <head>
        <link rel="icon" href="/icon.svg" type="image/svg+xml" />
      </head>
      <body className="min-h-screen bg-surface antialiased">
        <PWAProvider>
          <AppShell>{children}</AppShell>
        </PWAProvider>
      </body>
    </html>
  );
}

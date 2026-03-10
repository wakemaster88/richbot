import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "RichBot — Trading Dashboard",
  description: "Grid-Trading Bot — Fernsteuerung & Monitoring",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-surface antialiased">
        <Nav />
        {children}
      </body>
    </html>
  );
}

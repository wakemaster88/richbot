"use client";

import { usePathname } from "next/navigation";
import { Nav } from "./nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";

  return (
    <>
      {!isLogin && <Nav />}
      {children}
    </>
  );
}

"use client";

import { fmt } from "@/lib/format";

interface PnlCardProps {
  label: string;
  value: number;
  quoteCcy: string;
}

export function PnlCard({ label, value, quoteCcy }: PnlCardProps) {
  const up = value >= 0;
  return (
    <div className="card-inner px-3 py-2.5">
      <p className="text-[9px] text-[var(--text-quaternary)] uppercase tracking-wider font-medium">
        {label}
      </p>
      <p
        className={`text-[15px] font-bold font-mono tracking-tight mt-0.5 ${
          up ? "text-[var(--up)]" : "text-[var(--down)]"
        }`}
      >
        {up ? "+" : ""}
        {fmt(value, 4)} {quoteCcy}
      </p>
    </div>
  );
}

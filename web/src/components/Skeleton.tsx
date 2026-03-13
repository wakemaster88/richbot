"use client";

export function Skeleton({ h = 200, className = "" }: { h?: number; className?: string }) {
  return (
    <div
      className={`rounded-2xl overflow-hidden ${className}`}
      style={{
        height: h,
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
      }}
    >
      <div
        className="w-full h-full animate-pulse"
        style={{
          background:
            "linear-gradient(110deg, var(--bg-card) 30%, var(--bg-elevated) 50%, var(--bg-card) 70%)",
          backgroundSize: "200% 100%",
          animation: "shimmer 1.5s infinite",
        }}
      />
    </div>
  );
}

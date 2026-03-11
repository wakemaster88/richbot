import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const symbol = (searchParams.get("symbol") || "BTCUSDC").replace("/", "");
  const interval = searchParams.get("interval") || "5m";
  const limit = Math.min(Number(searchParams.get("limit") || 200), 500);

  try {
    const url = `https://api.binance.com/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
    const res = await fetch(url, { next: { revalidate: 30 } });
    if (!res.ok) return NextResponse.json({ error: "Binance API error" }, { status: 502 });
    const raw = await res.json();

    const klines = raw.map((k: unknown[]) => ({
      t: Number(k[0]),
      o: parseFloat(k[1] as string),
      h: parseFloat(k[2] as string),
      l: parseFloat(k[3] as string),
      c: parseFloat(k[4] as string),
      v: parseFloat(k[5] as string),
    }));

    return NextResponse.json(klines);
  } catch {
    return NextResponse.json({ error: "Failed to fetch klines" }, { status: 500 });
  }
}

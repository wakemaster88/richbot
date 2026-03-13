import { NextRequest, NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

function escapeCsv(val: unknown): string {
  const s = String(val ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const format = searchParams.get("format") || "csv";
    const from = searchParams.get("from");
    const to = searchParams.get("to");
    const pair = searchParams.get("pair");
    const limit = Math.min(parseInt(searchParams.get("limit") || "5000"), 10000);

    const where: Prisma.TradeWhereInput = { botId: BOT_ID };
    if (pair) where.pair = pair;
    if (from || to) {
      where.timestamp = {};
      if (from) (where.timestamp as Prisma.DateTimeFilter).gte = new Date(from);
      if (to) (where.timestamp as Prisma.DateTimeFilter).lte = new Date(to);
    }

    const trades = await prisma.trade.findMany({
      where,
      orderBy: { timestamp: "asc" },
      take: limit,
    });

    if (format === "csv") {
      const headers = [
        "id",
        "timestamp",
        "pair",
        "side",
        "price",
        "amount",
        "fee",
        "pnl",
        "gridLevel",
        "orderId",
        "fillPrice",
        "slippageBps",
        "isMaker",
      ];
      const rows = trades.map((t) => {
        const r = t as Record<string, unknown>;
        return [
          r.id,
          (r.timestamp as Date).toISOString(),
          r.pair,
          r.side,
          r.price,
          r.amount,
          r.fee,
          r.pnl,
          r.gridLevel,
          r.orderId,
          r.fillPrice,
          r.slippageBps,
          r.isMaker,
        ].map(escapeCsv);
      });
      const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
      return new NextResponse(csv, {
        headers: {
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": `attachment; filename="trades-${new Date().toISOString().slice(0, 10)}.csv"`,
        },
      });
    }

    return NextResponse.json(trades);
  } catch (error) {
    console.error("Trades export error:", error);
    return NextResponse.json({ error: "Failed to export trades" }, { status: 500 });
  }
}

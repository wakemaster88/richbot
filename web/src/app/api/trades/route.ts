import { NextRequest, NextResponse } from "next/server";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const limit = Math.min(parseInt(searchParams.get("limit") || "50"), 200);
    const offset = Math.max(0, parseInt(searchParams.get("offset") || "0"));
    const pair = searchParams.get("pair");
    const from = searchParams.get("from");
    const to = searchParams.get("to");
    const side = searchParams.get("side");

    const where: Prisma.TradeWhereInput = { botId: BOT_ID };
    if (pair) where.pair = pair;
    if (side && (side === "buy" || side === "sell")) where.side = side;
    if (from || to) {
      where.timestamp = {};
      if (from) (where.timestamp as Prisma.DateTimeFilter).gte = new Date(from);
      if (to) (where.timestamp as Prisma.DateTimeFilter).lte = new Date(to);
    }

    const trades = await prisma.trade.findMany({
      where,
      orderBy: { timestamp: "desc" },
      take: limit,
      skip: offset,
    });

    return NextResponse.json(trades);
  } catch (error) {
    console.error("Trades fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch trades" }, { status: 500 });
  }
}

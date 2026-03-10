import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const hours = parseInt(searchParams.get("hours") || "24");
    const pair = searchParams.get("pair");
    const since = new Date(Date.now() - hours * 3600 * 1000);

    const where: Record<string, unknown> = {
      botId: BOT_ID,
      timestamp: { gte: since },
    };
    if (pair) where.pair = pair;

    const snapshots = await prisma.equitySnapshot.findMany({
      where,
      orderBy: { timestamp: "asc" },
      select: { timestamp: true, equity: true, pair: true },
      take: 1000,
    });

    return NextResponse.json(snapshots);
  } catch (error) {
    console.error("Equity fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch equity" }, { status: 500 });
  }
}

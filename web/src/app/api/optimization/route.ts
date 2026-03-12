import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [optimizations, regimes, status] = await Promise.all([
      prisma.botEvent.findMany({
        where: { botId: BOT_ID, category: "optimization" },
        orderBy: { timestamp: "desc" },
        take: 10,
      }),
      prisma.botEvent.findMany({
        where: { botId: BOT_ID, category: "regime" },
        orderBy: { timestamp: "desc" },
        take: 10,
      }),
      prisma.botStatus.findUnique({
        where: { botId: BOT_ID },
      }),
    ]);

    const pairStatuses = (status?.pairStatuses as Record<string, Record<string, unknown>>) || {};

    const pairRegimes: Record<string, unknown> = {};
    for (const [pair, ps] of Object.entries(pairStatuses)) {
      if (pair.startsWith("__")) continue;
      const regime = ps?.regime as Record<string, unknown> | undefined;
      const allocation = ps?.allocation as Record<string, unknown> | undefined;
      const trailingTp = ps?.trailing_tp as unknown[] | undefined;
      const trailingTpActive = ps?.trailing_tp_active as boolean | undefined;
      pairRegimes[pair] = {
        regime: regime || null,
        allocation: allocation || null,
        trailing_tp_count: trailingTp?.length ?? 0,
        trailing_tp_active: trailingTpActive ?? false,
      };
    }

    return NextResponse.json({
      optimizations,
      regimes,
      pairRegimes,
    });
  } catch (error) {
    console.error("Optimization fetch error:", error);
    return NextResponse.json({
      optimizations: [],
      regimes: [],
      pairRegimes: {},
    });
  }
}

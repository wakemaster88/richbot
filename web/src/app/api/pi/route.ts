import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const hb = await prisma.heartbeat.findFirst({
      where: { botId: BOT_ID },
      orderBy: { timestamp: "desc" },
    });

    if (!hb || !hb.memory) {
      return NextResponse.json({ connected: false, system: null });
    }

    const ageMs = Date.now() - new Date(hb.timestamp).getTime();
    const online = ageMs < 90_000;

    return NextResponse.json({
      connected: online,
      lastSeen: hb.timestamp,
      uptime: hb.uptime,
      system: hb.memory,
    });
  } catch (error) {
    console.error("Pi status error:", error);
    return NextResponse.json({ connected: false, system: null }, { status: 200 });
  }
}

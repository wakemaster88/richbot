import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const status = await prisma.botStatus.findUnique({
      where: { botId: BOT_ID },
    });

    if (!status) {
      return NextResponse.json(
        {
          id: "",
          botId: BOT_ID,
          status: "unknown",
          lastHeartbeat: new Date().toISOString(),
          pairs: [],
          pairStatuses: {},
          startedAt: new Date().toISOString(),
          version: "2.0",
        },
        { status: 200 }
      );
    }

    return NextResponse.json(status);
  } catch (error) {
    console.error("Status fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch status" }, { status: 500 });
  }
}

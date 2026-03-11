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
          status: "waiting",
          lastHeartbeat: new Date().toISOString(),
          pairs: [],
          pairStatuses: {},
          startedAt: new Date().toISOString(),
          version: "2.0",
          dbConnected: true,
        },
        { status: 200 }
      );
    }

    return NextResponse.json({ ...status, dbConnected: true });
  } catch (error) {
    console.error("Status fetch error:", error);
    return NextResponse.json(
      { dbConnected: false, status: "error", error: "Database not reachable" },
      { status: 200 }
    );
  }
}

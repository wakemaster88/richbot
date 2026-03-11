import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

const VALID_KEYS = [
  "BINANCE_API_KEY",
  "BINANCE_SECRET",
  "TELEGRAM_TOKEN",
  "TELEGRAM_CHAT_ID",
  "XAI_API_KEY",
];

export async function GET() {
  try {
    const rows = await prisma.botSecret.findMany({
      where: { botId: BOT_ID },
      select: { key: true, updatedAt: true },
    });

    const masked: Record<string, { set: boolean; updatedAt: string }> = {};
    for (const k of VALID_KEYS) {
      const row = rows.find((r) => r.key === k);
      masked[k] = {
        set: !!row,
        updatedAt: row?.updatedAt?.toISOString() ?? "",
      };
    }

    return NextResponse.json({ secrets: masked });
  } catch (error) {
    console.error("Secrets fetch error:", error);
    return NextResponse.json({ secrets: {} }, { status: 200 });
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();
    const { secrets } = body as { secrets: Record<string, string> };

    if (!secrets || typeof secrets !== "object") {
      return NextResponse.json({ error: "Invalid payload" }, { status: 400 });
    }

    for (const [key, value] of Object.entries(secrets)) {
      if (!VALID_KEYS.includes(key)) continue;
      if (!value || value.trim() === "") {
        await prisma.botSecret.deleteMany({ where: { botId: BOT_ID, key } });
        continue;
      }
      await prisma.botSecret.upsert({
        where: { botId_key: { botId: BOT_ID, key } },
        update: { value: value.trim(), updatedAt: new Date() },
        create: { botId: BOT_ID, key, value: value.trim() },
      });
    }

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Secrets update error:", error);
    return NextResponse.json({ error: "Failed to save secrets" }, { status: 500 });
  }
}

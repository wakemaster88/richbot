import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const row = await prisma.botConfig.findUnique({
      where: { botId: BOT_ID },
    });

    if (!row) {
      return NextResponse.json({ config: null }, { status: 200 });
    }

    return NextResponse.json({ config: row.config, updatedAt: row.updatedAt });
  } catch (error) {
    console.error("Config fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch config" }, { status: 500 });
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = await request.json();
    const { config } = body;

    if (!config || typeof config !== "object") {
      return NextResponse.json({ error: "Invalid config payload" }, { status: 400 });
    }

    const row = await prisma.botConfig.upsert({
      where: { botId: BOT_ID },
      update: { config, updatedAt: new Date() },
      create: { botId: BOT_ID, config },
    });

    await prisma.command.create({
      data: {
        botId: BOT_ID,
        type: "update_config",
        payload: config,
        status: "pending",
      },
    });

    return NextResponse.json({ config: row.config, updatedAt: row.updatedAt });
  } catch (error) {
    console.error("Config update error:", error);
    return NextResponse.json({ error: "Failed to update config" }, { status: 500 });
  }
}

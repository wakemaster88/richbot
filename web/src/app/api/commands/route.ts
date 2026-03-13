import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const limit = Math.min(parseInt(searchParams.get("limit") || "20"), 100);

    const commands = await prisma.command.findMany({
      where: { botId: BOT_ID },
      orderBy: { createdAt: "desc" },
      take: limit,
    });

    return NextResponse.json(commands);
  } catch (error) {
    console.error("Commands fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch commands" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { type, payload } = body;

    if (!type || typeof type !== "string") {
      return NextResponse.json({ error: "Missing command type" }, { status: 400 });
    }

    const validTypes = ["stop", "resume", "pause", "status", "performance", "update_config", "update_software", "fetch_logs", "reset_rl", "rl_stats"];
    if (!validTypes.includes(type)) {
      return NextResponse.json({ error: `Invalid command type: ${type}` }, { status: 400 });
    }

    const command = await prisma.command.create({
      data: {
        botId: BOT_ID,
        type,
        payload: payload || undefined,
        status: "pending",
      },
    });

    return NextResponse.json(command, { status: 201 });
  } catch (error) {
    console.error("Command create error:", error);
    return NextResponse.json({ error: "Failed to create command" }, { status: 500 });
  }
}

import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

/**
 * POST /api/monte-carlo — start a Monte-Carlo stress test on the Pi
 * Body: { pair?: string, days?: number, simulations?: number, capital?: number }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const pair = body.pair || "BTC/USDC";
    const days = Math.min(Math.max(parseInt(body.days) || 30, 1), 90);
    const simulations = Math.min(Math.max(parseInt(body.simulations) || 200, 10), 1000);
    const capital = Math.min(Math.max(parseFloat(body.capital) || 200, 10), 100000);

    const command = await prisma.command.create({
      data: {
        botId: BOT_ID,
        type: "monte_carlo",
        payload: { pair, days, simulations, capital },
        status: "pending",
      },
    });

    return NextResponse.json(
      { id: command.id, status: "pending", pair, days, simulations, capital },
      { status: 201 },
    );
  } catch (error) {
    console.error("Monte-Carlo create error:", error);
    return NextResponse.json({ error: "Failed to start Monte-Carlo" }, { status: 500 });
  }
}

/**
 * GET /api/monte-carlo?id=<command-id> — poll result
 * GET /api/monte-carlo — list recent runs
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const id = searchParams.get("id");

    if (id) {
      const command = await prisma.command.findUnique({ where: { id } });
      if (!command) {
        return NextResponse.json({ error: "Not found" }, { status: 404 });
      }
      return NextResponse.json({
        id: command.id,
        status: command.status,
        createdAt: command.createdAt,
        processedAt: command.processedAt,
        result: command.result,
      });
    }

    const runs = await prisma.command.findMany({
      where: { botId: BOT_ID, type: "monte_carlo" },
      orderBy: { createdAt: "desc" },
      take: 10,
      select: {
        id: true, status: true, payload: true,
        createdAt: true, processedAt: true, result: true,
      },
    });

    return NextResponse.json(runs);
  } catch (error) {
    console.error("Monte-Carlo fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch Monte-Carlo" }, { status: 500 });
  }
}

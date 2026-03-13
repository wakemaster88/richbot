import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

/**
 * POST /api/backtest — start a backtest on the Pi
 * Body: { pair?: string, days?: number, capital?: number }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const pair = body.pair || "BTC/USDC";
    const days = Math.min(Math.max(parseInt(body.days) || 30, 1), 365);
    const capital = Math.min(Math.max(parseFloat(body.capital) || 200, 10), 100000);
    const param_overrides = body.param_overrides ?? null;

    const command = await prisma.command.create({
      data: {
        botId: BOT_ID,
        type: "backtest",
        payload: { pair, days, capital, ...(param_overrides && { param_overrides }) },
        status: "pending",
      },
    });

    return NextResponse.json(
      { id: command.id, status: "pending", pair, days, capital },
      { status: 201 },
    );
  } catch (error) {
    console.error("Backtest create error:", error);
    return NextResponse.json({ error: "Failed to start backtest" }, { status: 500 });
  }
}

/**
 * GET /api/backtest?id=<command-id> — poll backtest result
 * GET /api/backtest — list recent backtests
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const id = searchParams.get("id");

    if (id) {
      const command = await prisma.command.findUnique({ where: { id } });
      if (!command) {
        return NextResponse.json({ error: "Backtest not found" }, { status: 404 });
      }
      return NextResponse.json({
        id: command.id,
        status: command.status,
        createdAt: command.createdAt,
        processedAt: command.processedAt,
        result: command.result,
      });
    }

    const backtests = await prisma.command.findMany({
      where: { botId: BOT_ID, type: "backtest" },
      orderBy: { createdAt: "desc" },
      take: 10,
      select: {
        id: true,
        status: true,
        payload: true,
        createdAt: true,
        processedAt: true,
        result: true,
      },
    });

    return NextResponse.json(backtests);
  } catch (error) {
    console.error("Backtest fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch backtest" }, { status: 500 });
  }
}

import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [trades, snapshots, events24h] = await Promise.all([
      prisma.trade.findMany({
        where: { botId: BOT_ID },
        orderBy: { timestamp: "desc" },
        take: 500,
      }),
      prisma.botEvent.findMany({
        where: { botId: BOT_ID, category: "snapshot" },
        orderBy: { timestamp: "desc" },
        take: 100,
      }),
      prisma.botEvent.findMany({
        where: {
          botId: BOT_ID,
          timestamp: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) },
        },
        orderBy: { timestamp: "desc" },
        take: 200,
      }),
    ]);

    const wins = trades.filter((t) => t.pnl > 0).length;
    const losses = trades.filter((t) => t.pnl < 0).length;
    const totalPnl = trades.reduce((s, t) => s + t.pnl, 0);
    const totalFees = trades.reduce((s, t) => s + t.fee, 0);
    const avgWin = wins > 0 ? trades.filter((t) => t.pnl > 0).reduce((s, t) => s + t.pnl, 0) / wins : 0;
    const avgLoss = losses > 0 ? trades.filter((t) => t.pnl < 0).reduce((s, t) => s + t.pnl, 0) / losses : 0;

    let maxStreak = 0, currentStreak = 0, maxLossStreak = 0, currentLossStreak = 0;
    for (const t of [...trades].reverse()) {
      if (t.pnl > 0) {
        currentStreak++;
        currentLossStreak = 0;
        maxStreak = Math.max(maxStreak, currentStreak);
      } else if (t.pnl < 0) {
        currentLossStreak++;
        currentStreak = 0;
        maxLossStreak = Math.max(maxLossStreak, currentLossStreak);
      }
    }

    const hourlyPnl: Record<string, { pnl: number; count: number }> = {};
    for (const t of trades) {
      const h = new Date(t.timestamp).toISOString().slice(0, 13);
      if (!hourlyPnl[h]) hourlyPnl[h] = { pnl: 0, count: 0 };
      hourlyPnl[h].pnl += t.pnl;
      hourlyPnl[h].count++;
    }

    const pairStats: Record<string, { trades: number; pnl: number; wins: number; losses: number; volume: number }> = {};
    for (const t of trades) {
      if (!pairStats[t.pair]) pairStats[t.pair] = { trades: 0, pnl: 0, wins: 0, losses: 0, volume: 0 };
      const ps = pairStats[t.pair];
      ps.trades++;
      ps.pnl += t.pnl;
      ps.volume += t.price * t.amount;
      if (t.pnl > 0) ps.wins++;
      else if (t.pnl < 0) ps.losses++;
    }

    const eventCounts: Record<string, number> = {};
    for (const e of events24h) {
      eventCounts[e.category] = (eventCounts[e.category] || 0) + 1;
    }

    const latestSnapshots = snapshots.slice(0, 20).map((s) => ({
      timestamp: s.timestamp,
      detail: s.detail as Record<string, unknown>,
    }));

    return NextResponse.json({
      summary: {
        total_trades: trades.length,
        wins, losses,
        win_rate: trades.length > 0 ? (wins / trades.length * 100) : 0,
        total_pnl: totalPnl,
        total_fees: totalFees,
        net_pnl: totalPnl - totalFees,
        avg_win: avgWin,
        avg_loss: avgLoss,
        profit_factor: avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : 0,
        max_win_streak: maxStreak,
        max_loss_streak: maxLossStreak,
      },
      pair_stats: pairStats,
      hourly_pnl: Object.entries(hourlyPnl)
        .map(([hour, d]) => ({ hour, ...d }))
        .sort((a, b) => a.hour.localeCompare(b.hour))
        .slice(-48),
      event_counts_24h: eventCounts,
      snapshots: latestSnapshots,
    });
  } catch (error) {
    console.error("Analytics error:", error);
    return NextResponse.json({ summary: null }, { status: 200 });
  }
}

import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const events = await prisma.botEvent.findMany({
      where: { botId: BOT_ID, category: "rl_optimization" },
      orderBy: { timestamp: "desc" },
      take: 50,
    });

    const rewards: { episode: number; reward: number; exploration: number; timestamp: string }[] = [];
    let latestAction: Record<string, unknown> | null = null;
    let explorationRate = 0.15;
    let episodes = 0;
    let policyHints: Record<string, unknown> = {};

    for (const ev of events) {
      const d = ev.detail as Record<string, unknown> | null;
      if (!d) continue;

      const reward = typeof d.reward === "number" ? d.reward : 0;
      const ep = typeof d.episode === "number" ? d.episode : 0;
      const expl = typeof d.exploration_rate === "number" ? d.exploration_rate : 0.15;

      rewards.push({
        episode: ep,
        reward: Math.round(reward * 1000) / 1000,
        exploration: Math.round(expl * 1000) / 1000,
        timestamp: ev.timestamp.toISOString(),
      });

      if (!latestAction) {
        latestAction = {
          action: d.action,
          reward,
          was_exploration: d.was_exploration,
          episode: ep,
          heuristic_adj: d.heuristic_adj,
          merged_adj: d.merged_adj,
          timestamp: ev.timestamp.toISOString(),
        };
        explorationRate = expl;
        episodes = ep;
      }
    }

    rewards.reverse();

    return NextResponse.json({
      rewards,
      latestAction,
      explorationRate,
      episodes,
      policyHints,
    });
  } catch (error) {
    console.error("RL-Stats fetch error:", error);
    return NextResponse.json({
      rewards: [],
      latestAction: null,
      explorationRate: 0.15,
      episodes: 0,
      policyHints: {},
    });
  }
}

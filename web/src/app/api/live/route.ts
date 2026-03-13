import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * GET /api/live — Server-Sent Events stream for real-time updates.
 * Sends status_update every 5s. Client falls back to polling if SSE unavailable.
 */
export async function GET() {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: string, data: object) => {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        );
      };

      try {
        for (let i = 0; i < 24; i++) {
          const status = await prisma.botStatus.findUnique({
            where: { botId: BOT_ID },
          });
          send("status_update", {
            ...status,
            _ts: Date.now(),
          });
          await new Promise((r) => setTimeout(r, 5000));
        }
      } catch (e) {
        console.error("SSE error:", e);
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}

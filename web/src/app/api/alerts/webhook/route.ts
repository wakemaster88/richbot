import { NextRequest, NextResponse } from "next/server";
import webpush from "web-push";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

const WEBHOOK_SECRET = process.env.ALERT_WEBHOOK_SECRET;

function initVapid() {
  const pub = process.env.VAPID_PUBLIC_KEY;
  const priv = process.env.VAPID_PRIVATE_KEY;
  if (pub && priv) {
    webpush.setVapidDetails("mailto:richbot@local", pub, priv);
    return true;
  }
  return false;
}

export async function POST(request: NextRequest) {
  const secret = request.headers.get("x-alert-secret");
  if (WEBHOOK_SECRET && secret !== WEBHOOK_SECRET) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  if (!initVapid()) {
    return NextResponse.json(
      { error: "VAPID keys not configured" },
      { status: 503 }
    );
  }

  const body = await request.json().catch(() => ({}));
  const { title, message, severity } = body;

  if (!title && !message) {
    return NextResponse.json({ error: "title or message required" }, { status: 400 });
  }

  const subscriptions = await prisma.pushSubscription.findMany();

  const payload = JSON.stringify({
    title: title || "RichBot",
    body: typeof message === "string" ? message.replace(/<[^>]+>/g, "").slice(0, 200) : "Alert",
    severity: severity || "info",
    url: "/",
  });

  const results = await Promise.allSettled(
    subscriptions.map(async (sub) => {
      try {
        await webpush.sendNotification(
          {
            endpoint: sub.endpoint,
            keys: sub.keys as { p256dh: string; auth: string },
          },
          payload,
          { TTL: 60 }
        );
      } catch (e: unknown) {
        const err = e as { statusCode?: number };
        if (err?.statusCode === 410 || err?.statusCode === 404) {
          await prisma.pushSubscription.deleteMany({ where: { endpoint: sub.endpoint } });
        }
        throw e;
      }
    })
  );

  const failed = results.filter((r) => r.status === "rejected").length;
  return NextResponse.json({
    ok: true,
    sent: subscriptions.length - failed,
    failed,
  });
}

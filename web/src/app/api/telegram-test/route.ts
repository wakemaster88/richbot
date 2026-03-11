import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const BOT_ID = process.env.BOT_ID || "richbot-pi";

export const dynamic = "force-dynamic";

async function getSecret(key: string): Promise<string | null> {
  const row = await prisma.botSecret.findUnique({
    where: { botId_key: { botId: BOT_ID, key } },
  });
  return row?.value?.trim() || null;
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { action } = body as { action: string };

    if (action === "get_chat_id") {
      const token = await getSecret("TELEGRAM_TOKEN");
      if (!token) return NextResponse.json({ error: "Kein Bot-Token gespeichert" }, { status: 400 });

      const res = await fetch(`https://api.telegram.org/bot${token}/getUpdates?limit=10&offset=-10`);
      const data = await res.json();

      if (!data.ok) return NextResponse.json({ error: "Token ungueltig" }, { status: 400 });

      const chats: { id: number; name: string; type: string }[] = [];
      const seen = new Set<number>();
      for (const u of data.result || []) {
        const chat = u.message?.chat || u.my_chat_member?.chat;
        if (chat && !seen.has(chat.id)) {
          seen.add(chat.id);
          chats.push({
            id: chat.id,
            name: chat.title || chat.first_name || chat.username || String(chat.id),
            type: chat.type,
          });
        }
      }

      return NextResponse.json({ chats });
    }

    if (action === "test_message") {
      const token = await getSecret("TELEGRAM_TOKEN");
      const chatId = await getSecret("TELEGRAM_CHAT_ID");
      if (!token || !chatId) return NextResponse.json({ error: "Token oder Chat-ID fehlt" }, { status: 400 });

      const text = "✅ <b>RichBot Telegram-Test</b>\n\nVerbindung erfolgreich! Benachrichtigungen sind aktiv.";
      const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
      });
      const data = await res.json();

      if (!data.ok) return NextResponse.json({ error: data.description || "Senden fehlgeschlagen" }, { status: 400 });
      return NextResponse.json({ ok: true });
    }

    return NextResponse.json({ error: "Unbekannte Aktion" }, { status: 400 });
  } catch (error) {
    console.error("Telegram test error:", error);
    return NextResponse.json({ error: "Interner Fehler" }, { status: 500 });
  }
}

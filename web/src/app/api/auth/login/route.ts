import { NextRequest, NextResponse } from "next/server";
import { signToken, isAuthRequired, AUTH_COOKIE } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (!isAuthRequired()) {
    return NextResponse.json({ ok: true }, { status: 200 });
  }

  const body = await request.json().catch(() => ({}));
  const password = typeof body.password === "string" ? body.password : "";

  const expected = process.env.DASHBOARD_PASSWORD;
  if (!password || password !== expected) {
    return NextResponse.json({ error: "Ungültiges Passwort" }, { status: 401 });
  }

  const token = await signToken();

  const res = NextResponse.json({ ok: true });
  res.cookies.set(AUTH_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 7, // 7 days
    path: "/",
  });

  return res;
}

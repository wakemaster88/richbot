import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { verifyToken, isAuthRequired, AUTH_COOKIE } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!isAuthRequired()) {
    return NextResponse.json({ authenticated: true });
  }

  const cookieStore = await cookies();
  const token = cookieStore.get(AUTH_COOKIE)?.value;

  if (!token) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  const valid = await verifyToken(token);
  if (!valid) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }

  return NextResponse.json({ authenticated: true });
}

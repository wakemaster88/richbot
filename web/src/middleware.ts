import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { jwtVerify } from "jose";

const AUTH_COOKIE = "richbot_auth";

function getSecret() {
  const secret = process.env.DASHBOARD_PASSWORD || "richbot-dev-secret-change-me";
  return new TextEncoder().encode(secret);
}

async function isAuthenticated(request: NextRequest): Promise<boolean> {
  if (!process.env.DASHBOARD_PASSWORD) return true;

  const token = request.cookies.get(AUTH_COOKIE)?.value;
  if (!token) return false;

  try {
    await jwtVerify(token, getSecret(), {
      issuer: "richbot-dashboard",
      audience: "richbot-user",
    });
    return true;
  } catch {
    return false;
  }
}

const PUBLIC_PATHS = ["/login"];
const AUTH_API = ["/api/auth/login"];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "?"))) return true;
  if (AUTH_API.some((p) => pathname.startsWith(p))) return true;
  return false;
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublic(pathname)) {
    if (pathname === "/login") {
      const auth = await isAuthenticated(request);
      if (auth) {
        return NextResponse.redirect(new URL("/", request.url));
      }
    }
    return NextResponse.next();
  }

  const auth = await isAuthenticated(request);
  if (!auth) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    const login = new URL("/login", request.url);
    login.searchParams.set("from", pathname);
    return NextResponse.redirect(login);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.json|sw.js|icons/|icon.svg|offline.html).*)",
  ],
};

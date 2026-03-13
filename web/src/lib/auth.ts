/** Simple JWT auth for dashboard. Uses DASHBOARD_PASSWORD from env. */

import { SignJWT, jwtVerify } from "jose";

const COOKIE_NAME = "richbot_auth";
const JWT_SECRET = new TextEncoder().encode(
  process.env.DASHBOARD_PASSWORD || "richbot-dev-secret-change-me"
);
const JWT_ISSUER = "richbot-dashboard";
const JWT_AUDIENCE = "richbot-user";
const JWT_EXP = "7d";

export const AUTH_COOKIE = COOKIE_NAME;

export function isAuthRequired(): boolean {
  return !!process.env.DASHBOARD_PASSWORD;
}

export async function signToken(): Promise<string> {
  return new SignJWT({ sub: "user" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuer(JWT_ISSUER)
    .setAudience(JWT_AUDIENCE)
    .setExpirationTime(JWT_EXP)
    .sign(JWT_SECRET);
}

export async function verifyToken(token: string): Promise<boolean> {
  try {
    await jwtVerify(token, JWT_SECRET, {
      issuer: JWT_ISSUER,
      audience: JWT_AUDIENCE,
    });
    return true;
  } catch {
    return false;
  }
}

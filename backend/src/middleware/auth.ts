import type { NextFunction, Request, Response } from "express";
import { verifyFirebaseIdToken } from "../services/firebase.js";

export interface AuthRequest extends Request {
  auth?: { userId: string; email: string; name?: string };
}

export async function requireAuth(req: AuthRequest, res: Response, next: NextFunction) {
  const token = req.headers.authorization?.replace(/^Bearer\s+/i, "");
  if (!token) return res.status(401).json({ error: "Authentication required" });

  try {
    const decoded = await verifyFirebaseIdToken(token);
    const email = decoded.email;
    if (!email) return res.status(401).json({ error: "Firebase account email is required" });
    req.auth = { userId: decoded.uid, email, name: decoded.name };
    next();
  } catch {
    return res.status(401).json({ error: "Firebase session is invalid or expired" });
  }
}

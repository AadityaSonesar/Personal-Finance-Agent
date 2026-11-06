import { Router } from "express";
import { User } from "../models/index.js";
import { AuthRequest, requireAuth } from "../middleware/auth.js";
import { audit } from "../utils/audit.js";

const router = Router();
const publicUser = (user: any) => ({ _id: user.firebaseUid, name: user.name, email: user.email, preferences: user.preferences, accountStatus: user.accountStatus });

async function syncUser(req: AuthRequest) {
  const { userId: firebaseUid, email, name } = req.auth!;
  return User.findOneAndUpdate(
    { firebaseUid },
    { $set: { email, name: name || email.split("@")[0] }, $setOnInsert: { firebaseUid } },
    { new: true, upsert: true, setDefaultsOnInsert: true },
  );
}

router.post("/session", requireAuth, async (req: AuthRequest, res, next) => {
  try {
    const user = await syncUser(req);
    await audit(req.auth!.userId, "firebase_login", "success");
    res.json({ user: publicUser(user) });
  } catch (error) { next(error); }
});

router.get("/me", requireAuth, async (req: AuthRequest, res, next) => {
  try {
    const user = await syncUser(req);
    if (user.accountStatus !== "active") return res.status(403).json({ error: "Account is disabled" });
    res.json({ user: publicUser(user) });
  } catch (error) { next(error); }
});

router.post("/logout", requireAuth, async (req: AuthRequest, res, next) => {
  try {
    await audit(req.auth!.userId, "firebase_logout", "success");
    res.status(204).end();
  } catch (error) { next(error); }
});

export default router;

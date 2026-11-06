import { readFileSync } from "node:fs";
import { isAbsolute, resolve } from "node:path";
import { cert, getApps, initializeApp } from "firebase-admin/app";
import { getAuth } from "firebase-admin/auth";
import { env, projectRoot } from "../config/env.js";

function firebaseAuth() {
  if (!getApps().length) {
    const configuredPath = env.FIREBASE_SERVICE_ACCOUNT_PATH;
    const serviceAccountPath = isAbsolute(configuredPath) ? configuredPath : resolve(projectRoot, configuredPath);
    const serviceAccount = JSON.parse(readFileSync(serviceAccountPath, "utf8"));
    initializeApp({ credential: cert(serviceAccount) });
  }
  return getAuth();
}

export async function verifyFirebaseIdToken(idToken: string) {
  return firebaseAuth().verifyIdToken(idToken);
}

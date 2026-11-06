import { initializeApp } from "firebase/app";
import { getAuth, type Auth } from "firebase/auth";

const config = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

export const firebaseConfigurationError = "Firebase is not configured. Add the VITE_FIREBASE_* values from Firebase Console to frontend/.env, then restart the frontend server.";
export const firebaseAuth: Auth | null = Object.values(config).every(Boolean)
  ? getAuth(initializeApp(config))
  : null;

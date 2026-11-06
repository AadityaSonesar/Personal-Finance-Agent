import { createContext, useContext, useEffect, useState } from "react";
import { GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signOut } from "firebase/auth";
import { api } from "../services/api";
import { firebaseAuth, firebaseConfigurationError } from "../services/firebase";

type User = { _id: string; name: string; email: string };
type Auth = { user: User | null; loading: boolean; loginWithGoogle: () => Promise<void>; logout: () => Promise<void> };
const C = createContext<Auth>(null!);
const googleProvider = new GoogleAuthProvider();

async function syncSession() {
  return (await api.post("/auth/session")).data.user as User;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!firebaseAuth) { setLoading(false); return; }
    return onAuthStateChanged(firebaseAuth, async (firebaseUser) => {
      try { setUser(firebaseUser ? await syncSession() : null); }
      catch { setUser(null); }
      finally { setLoading(false); }
    });
  }, []);

  const loginWithGoogle = async () => {
    if (!firebaseAuth) throw new Error(firebaseConfigurationError);
    await signInWithPopup(firebaseAuth, googleProvider);
    setUser(await syncSession());
  };
  const logout = async () => { if (firebaseAuth) await signOut(firebaseAuth); setUser(null); };

  return <C.Provider value={{ user, loading, loginWithGoogle, logout }}>{children}</C.Provider>;
}
export const useAuth = () => useContext(C);

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { firebaseAuth, firebaseConfigurationError } from "../services/firebase";

export function AuthPage() {
  const auth = useAuth(), navigate = useNavigate(), [error, setError] = useState(""), [pending, setPending] = useState(false);
  async function signIn() {
    setPending(true); setError("");
    try { await auth.loginWithGoogle(); navigate("/dashboard"); }
    catch (error: any) { setError(error.message || "Google sign-in could not be completed."); }
    finally { setPending(false); }
  }
  return <main className="grid min-h-screen place-items-center bg-slate-950 p-6"><section className="card w-full max-w-md space-y-5 text-center"><div><h1 className="text-2xl font-bold">Welcome to Aurelia</h1><p className="mt-2 text-sm text-slate-400">Sign in securely to access your private financial workspace.</p></div>{!firebaseAuth && <p className="rounded border border-amber-500/40 bg-amber-500/10 p-3 text-left text-sm text-amber-200">{firebaseConfigurationError}</p>}{error && <p className="text-left text-sm text-rose-300">{error}</p>}<button className="button flex w-full items-center justify-center gap-3" onClick={signIn} disabled={pending || !firebaseAuth}><span className="grid h-5 w-5 place-items-center rounded-full bg-white text-xs font-bold text-slate-800">G</span>{pending ? "Opening Google…" : "Continue with Google"}</button><p className="text-xs text-slate-500">Aurelia uses Firebase Authentication. Your financial records are scoped to your verified account.</p></section></main>;
}

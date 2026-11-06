import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const links = [
  ["/dashboard", "Dashboard"],
  ["/transactions", "Transactions"],
  ["/budgets", "Budgets"],
  ["/goals", "Goals"],
  ["/intelligence", "Analysis"],
  ["/health", "Financial Health"],
  ["/forecast", "Cash-Flow Forecast"],
  ["/advisor", "AI Advisor"],
  ["/reports", "Reports"],
];

export function AppLayout() {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen bg-slate-950 md:flex">
      <aside className="border-b border-slate-800 bg-slate-900 p-5 md:min-h-screen md:w-64 md:border-b-0 md:border-r">
        <NavLink to="/dashboard" className="mb-8 block text-xl font-bold text-cyan-300">
          Aurelia
        </NavLink>
        <nav className="grid grid-cols-2 gap-1 md:block">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} className="nav">
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">
        <header className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
          <div>
            <p className="font-semibold">{user?.name}</p>
            <p className="text-xs text-slate-500">Personal financial workspace</p>
          </div>
          <button className="text-sm text-slate-400 hover:text-white" onClick={() => logout()}>
            Log out
          </button>
        </header>
        <div className="mx-auto max-w-7xl p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

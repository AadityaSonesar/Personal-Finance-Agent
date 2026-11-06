import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { AppLayout } from "./layouts/AppLayout";
import { AuthPage } from "./pages/Auth";
import {
  Dashboard,
  Transactions,
  Budgets,
  Goals,
  FinancialAnalysis,
  FinancialHealth,
  CashFlowForecast,
  AIAdvisor,
  Reports,
} from "./pages/AppPages";

function Protected() {
  const { user, loading } = useAuth();
  return loading ? (
    <p className="p-6 text-slate-400">Loading…</p>
  ) : user ? (
    <AppLayout />
  ) : (
    <Navigate to="/login" replace />
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<AuthPage />} />
          <Route element={<Protected />}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/budgets" element={<Budgets />} />
            <Route path="/goals" element={<Goals />} />
            <Route path="/intelligence" element={<FinancialAnalysis />} />
            <Route path="/health" element={<FinancialHealth />} />
            <Route path="/forecast" element={<CashFlowForecast />} />
            <Route path="/advisor" element={<AIAdvisor />} />
            <Route path="/reports" element={<Reports />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<App />);

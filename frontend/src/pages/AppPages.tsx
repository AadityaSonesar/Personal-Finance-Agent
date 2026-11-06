import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip,
  LineChart, Line, CartesianGrid, Legend, PieChart, Pie, Cell,
} from "recharts";
import { api } from "../services/api";

// ─── Helpers ────────────────────────────────────────────────────────────────

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

function fmt(v: number | null | undefined): string {
  if (v == null) return "—";
  return inr.format(v);
}

function Empty({ label }: { label: string }) {
  return (
    <p className="rounded-xl border border-dashed border-slate-700 p-6 text-sm text-slate-400">
      {label}
    </p>
  );
}

function Badge({ label, color = "slate" }: { label: string; color?: string }) {
  const c: Record<string, string> = {
    green: "bg-emerald-900 text-emerald-300",
    red: "bg-rose-900 text-rose-300",
    yellow: "bg-amber-900 text-amber-300",
    cyan: "bg-cyan-900 text-cyan-300",
    slate: "bg-slate-800 text-slate-300",
    purple: "bg-purple-900 text-purple-300",
  };
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${c[color] ?? c.slate}`}>
      {label}
    </span>
  );
}

function ConfidenceDot({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 60 ? "bg-amber-500" : "bg-rose-500";
  return (
    <span className="flex items-center gap-1 text-xs text-slate-400">
      <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
      {pct}%
    </span>
  );
}

const COLORS = ["#22d3ee", "#818cf8", "#34d399", "#fb923c", "#f472b6", "#a3e635", "#fbbf24", "#60a5fa"];

// ─── Dashboard ───────────────────────────────────────────────────────────────

export function Dashboard() {
  const [d, setD] = useState<any>();
  const [s, setS] = useState<any>();
  const [aiStatus, setAiStatus] = useState<any>();

  useEffect(() => {
    Promise.all([
      api.get("/dashboard/summary"),
      api.get("/dashboard/spending"),
      api.get("/ai/status").catch(() => null),
    ]).then(([a, b, c]) => {
      setD(a.data);
      setS(b.data);
      if (c) setAiStatus(c.data);
    });
  }, []);

  if (!d) return <p className="text-slate-400">Loading your financial workspace…</p>;
  const c = d.calculated;

  return (
    <>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Financial Overview</h1>
          <p className="text-sm text-slate-400">
            Values are calculated from your saved records — not verified bank balances.
          </p>
        </div>
        {aiStatus && (
          <Badge
            label={aiStatus.available ? `AI: ${aiStatus.models?.analysis ?? "Groq"}` : "AI: Offline"}
            color={aiStatus.available ? "cyan" : "slate"}
          />
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Total Income", c.totalIncome],
          ["Total Expenses", c.totalExpenses],
          ["Net Savings", c.savings],
          ["Health Score", d.health?.score ?? "—"],
        ].map(([l, v]) => (
          <div className="card" key={String(l)}>
            <p className="text-sm text-slate-400">{l}</p>
            <p className="mt-2 text-2xl font-bold">
              {typeof v === "number" ? fmt(v) : v}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <section className="card h-80">
          <h2 className="mb-4 font-semibold">Spending by Category</h2>
          <ResponsiveContainer>
            <BarChart data={s?.categories || []}>
              <XAxis dataKey="category" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v) => fmt(Number(v))} />
              <Bar dataKey="amount" fill="#22d3ee" />
            </BarChart>
          </ResponsiveContainer>
        </section>

        <section className="card">
          <h2 className="mb-4 font-semibold">Recent Transactions</h2>
          {d.recentTransactions.length ? (
            <div className="space-y-3">
              {d.recentTransactions.map((x: any) => (
                <div className="flex justify-between border-b border-slate-800 pb-2" key={x._id}>
                  <div>
                    <span className="font-medium">{x.merchant}</span>
                    <small className="ml-2 text-slate-500">{x.category}</small>
                  </div>
                  <span className={x.type === "income" ? "text-emerald-300" : "text-slate-200"}>
                    {x.type === "income" ? "+" : "-"}{fmt(x.amount)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty label="Add a transaction to see your activity." />
          )}
        </section>
      </div>
    </>
  );
}

// ─── Transactions ─────────────────────────────────────────────────────────────

const CATEGORIES = [
  "Food and Dining", "Groceries", "Transportation", "Shopping", "Entertainment",
  "Rent and Housing", "Utilities", "Healthcare", "Education", "Subscriptions",
  "Investments", "Savings", "Income", "Other",
];

export function Transactions() {
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [open, setOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<any>(null);
  const [bulkCategorizing, setBulkCategorizing] = useState(false);
  const [bulkResult, setBulkResult] = useState<any>(null);
  // editId removed — all categories are AI-assigned, no manual editing
  const [initialBalance, setInitialBalance] = useState<number>(0);
  const [savingBalance, setSavingBalance] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const LIMIT = 25;

  useEffect(() => {
    api.get("/user/initial-balance").then((res) => {
      if (res.data?.initialBalance != null) setInitialBalance(res.data.initialBalance);
    }).catch(() => null);
  }, []);

  async function saveInitialBalance() {
    setSavingBalance(true);
    try {
      await api.post("/user/initial-balance", { initialBalance });
      alert("Initial balance saved successfully.");
    } catch {
      alert("Failed to save initial balance.");
    } finally {
      setSavingBalance(false);
    }
  }

  const load = useCallback(() => {
    const params = new URLSearchParams({
      page: String(page),
      limit: String(LIMIT),
      ...(search && { search }),
      ...(categoryFilter && { category: categoryFilter }),
      ...(typeFilter && { type: typeFilter }),
    });
    api.get(`/transactions?${params}`).then((x) => {
      setItems(x.data.items);
      setTotal(x.data.total);
    });
  }, [page, search, categoryFilter, typeFilter]);

  useEffect(() => { void load(); }, [load]);

  async function submitNew(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    await api.post("/transactions", Object.fromEntries(f));
    setOpen(false);
    load();
  }

  async function deleteTransaction(id: string) {
    if (!confirm("Delete this transaction?")) return;
    await api.delete(`/transactions/${id}`);
    load();
  }

  async function deleteAllTransactions() {
    if (!confirm(`⚠️ This will permanently delete ALL ${total} transactions for your account. This cannot be undone. Are you sure?`)) return;
    if (!confirm("Final confirmation: delete ALL transactions?")) return;
    try {
      const res = await api.delete("/transactions");
      alert(`Deleted ${res.data.deleted} transactions.`);
      load();
    } catch (err: any) {
      alert(err.response?.data?.error || "Failed to delete all transactions.");
    }
  }

  async function handleImport(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setImporting(true);
    setImportResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api.post("/transactions/import", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setImportResult(res.data);
      load();
    } catch (err: any) {
      setImportResult({ error: err.response?.data?.error || "Import failed" });
    } finally {
      setImporting(false);
    }
  }

  async function bulkCategorize() {
    setBulkCategorizing(true);
    setBulkResult(null);
    try {
      const res = await api.post("/transactions/bulk-categorize");
      setBulkResult(res.data);
      load();
    } catch (err: any) {
      setBulkResult({ error: "Bulk categorization failed" });
    } finally {
      setBulkCategorizing(false);
    }
  }

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <>
      <div className="mb-6 flex flex-wrap gap-3">
        <h1 className="flex-1 text-2xl font-bold">Transactions</h1>
        <button className="button-sm" onClick={() => setImportOpen(!importOpen)}>
          Import CSV
        </button>
        <button
          className="button-sm"
          onClick={bulkCategorize}
          disabled={bulkCategorizing}
        >
          {bulkCategorizing ? "Categorizing…" : "Bulk AI Categorize"}
        </button>
        <button className="button" onClick={() => setOpen(!open)}>
          Add Transaction
        </button>
        {total > 0 && (
          <button
            className="button-sm bg-rose-700 hover:bg-rose-600 text-white border-rose-700"
            onClick={deleteAllTransactions}
          >
            🗑 Delete All
          </button>
        )}
      </div>

      {/* Initial Balance Panel */}
      <div className="card mb-6 flex flex-wrap items-center justify-between gap-4 bg-slate-900 border border-slate-800">
        <div>
          <h2 className="font-semibold text-slate-200">Starting Account Balance</h2>
          <p className="text-xs text-slate-400">
            Set your verified opening balance prior to these transactions.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative">
            <span className="absolute left-3 top-2.5 text-xs text-slate-400">₹</span>
            <input
              type="number"
              step="0.01"
              value={initialBalance}
              onChange={(e) => setInitialBalance(parseFloat(e.target.value) || 0)}
              className="input pl-7 w-44 text-sm"
              placeholder="0.00"
            />
          </div>
          <button
            className="button"
            onClick={saveInitialBalance}
            disabled={savingBalance}
          >
            {savingBalance ? "Saving…" : "Save Balance"}
          </button>
        </div>
      </div>

      {bulkResult && (
        <div className="card mb-4 text-sm">
          {bulkResult.error ? (
            <p className="text-rose-400">{bulkResult.error}</p>
          ) : (
            <p className="text-emerald-400">
              Bulk categorized: {bulkResult.high_confidence_applied} applied from{" "}
              {bulkResult.suggestions_received} suggestions across {bulkResult.total_uncategorized} transactions.
            </p>
          )}
        </div>
      )}

      {/* CSV Import Panel */}
      {importOpen && (
        <div className="card mb-6">
          <h2 className="mb-3 font-semibold">Import CSV Statement</h2>
          <p className="mb-3 text-xs text-slate-400">
            Upload a CSV file with columns: date, merchant/payee, amount (or debit/credit), and optionally description, type, category.
            The AI will suggest categories for imported transactions.
          </p>
          <form onSubmit={handleImport} className="flex flex-wrap gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              className="input text-sm"
              required
            />
            <button className="button" type="submit" disabled={importing}>
              {importing ? "Importing…" : "Upload & Import"}
            </button>
          </form>
          {importResult && (
            <div className="mt-4 rounded-lg bg-slate-900 p-4 text-sm">
              {importResult.error ? (
                <p className="text-rose-400">{importResult.error}</p>
              ) : (
                <>
                  <p className="text-emerald-400 font-medium">{importResult.message}</p>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-slate-300 sm:grid-cols-4">
                    <span>Imported: <strong>{importResult.imported}</strong></span>
                    <span>Skipped: <strong>{importResult.skipped}</strong></span>
                    <span>Duplicates: <strong>{importResult.duplicates}</strong></span>
                    <span>AI categorized: <strong>{importResult.ai_categorized}</strong></span>
                  </div>
                  {importResult.ai_error && (
                    <p className="mt-2 text-amber-400 text-xs">{importResult.ai_error}</p>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Add Transaction Form */}
      {open && (
        <form onSubmit={submitNew} className="card mb-6 grid gap-3 sm:grid-cols-2">
          <input className="input" name="merchant" placeholder="Merchant" required />
          <input className="input" name="amount" type="number" step="0.01" placeholder="Amount" required />
          <select className="input" name="type">
            <option value="expense">Expense</option>
            <option value="income">Income</option>
          </select>
          <div className="flex items-center px-3 py-2 bg-slate-900 border border-slate-800 rounded-lg text-xs text-cyan-300">
            🤖 Category will be determined automatically by AI
          </div>
          <input className="input" name="date" type="date" required />
          <input className="input" name="description" placeholder="Description (optional details for AI)" />
          <button className="button sm:col-span-2">Save & Let AI Categorize</button>
        </form>
      )}

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <input
          className="input flex-1"
          placeholder="Search merchant, description, category…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
        <select className="input w-40" value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); setPage(1); }}>
          <option value="">All Categories</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className="input w-32" value={typeFilter} onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}>
          <option value="">All Types</option>
          <option value="expense">Expense</option>
          <option value="income">Income</option>
        </select>
      </div>

      {/* Transaction Table */}
      <section className="card overflow-x-auto">
        <div className="mb-2 flex items-center justify-between text-sm text-slate-400">
          <span>{total} transactions</span>
          <span>Page {page} of {totalPages || 1}</span>
        </div>
        <table className="w-full text-left text-sm">
          <thead className="text-slate-400">
            <tr>
              <th className="pb-2">Date</th>
              <th className="pb-2">Merchant / Ref</th>
              <th className="pb-2">Category</th>
              <th className="pb-2">Source</th>
              <th className="pb-2 text-right">Amount</th>
              <th className="pb-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((x: any) => {
              return (
                <tr className="border-t border-slate-800" key={x._id}>
                  <td className="py-3 text-slate-400 text-xs">
                    {new Date(x.date).toLocaleDateString("en-IN")}
                  </td>
                  <td className="py-3">
                    <p className="font-medium">{x.merchant}</p>
                    {x.description && <p className="text-xs text-slate-500">{x.description}</p>}
                    {x.transactionId && (
                      <p className="text-xs text-slate-600 mt-0.5 font-mono" title="Transaction Reference">
                        Ref: {x.transactionId}
                      </p>
                    )}
                  </td>
                  <td className="py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-200">{x.category || "Needs Review"}</span>
                      <Badge label="AI Categorized" color="cyan" />
                    </div>
                    {x.aiSuggestion?.reason && (
                      <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{x.aiSuggestion.reason}</p>
                    )}
                  </td>
                  <td className="py-3 text-xs text-slate-500">{x.source || "manual"}</td>
                  <td className="py-3 text-right">
                    <span className={x.type === "income" ? "text-emerald-300" : "text-slate-200"}>
                      {x.type === "income" ? "+" : "-"}{fmt(x.amount)}
                    </span>
                  </td>
                  <td className="py-3">
                    <div className="flex gap-2">
                      <button
                        className="text-xs text-rose-400 hover:text-rose-200"
                        onClick={() => deleteTransaction(x._id)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!items.length && <Empty label="No transactions found. Add a record or import a CSV statement." />}
        {totalPages > 1 && (
          <div className="mt-4 flex gap-2">
            <button className="button-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
            <button className="button-sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next →</button>
          </div>
        )}
      </section>
    </>
  );
}

// ─── Budgets ─────────────────────────────────────────────────────────────────

export function Budgets() {
  const [budgets, setBudgets] = useState<any[]>([]);
  const [recommendations, setRecommendations] = useState<any>(null);
  const [loadingRecs, setLoadingRecs] = useState(false);
  const [recError, setRecError] = useState("");
  const [openAdd, setOpenAdd] = useState(false);
  const [savingAdd, setSavingAdd] = useState(false);
  const [addError, setAddError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editLimit, setEditLimit] = useState<number>(0);
  const [savingEdit, setSavingEdit] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const currentMonth = new Date().toISOString().slice(0, 7);

  function loadBudgets() {
    api.get("/budgets").then((x) => setBudgets(x.data.items || []));
  }

  useEffect(() => {
    loadBudgets();
  }, []);

  async function fetchRecommendations() {
    setLoadingRecs(true);
    setRecError("");
    try {
      const res = await api.post("/ai/budget-recommendations");
      setRecommendations(res.data);
    } catch (err: any) {
      setRecError(err.response?.data?.detail || "Failed to get budget recommendations");
    } finally {
      setLoadingRecs(false);
    }
  }

  async function acceptRecommendation(rec: any) {
    try {
      await api.post("/budgets/upsert", {
        category: rec.category,
        monthlyLimit: rec.recommended_budget,
        month: currentMonth,
      });
      loadBudgets();
      alert(`Budget for ${rec.category} set to ${fmt(rec.recommended_budget)}`);
    } catch (err: any) {
      alert(err.response?.data?.error || "Failed to save budget");
    }
  }

  async function handleAddBudget(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSavingAdd(true);
    setAddError("");
    const fd = new FormData(e.currentTarget);
    const category = fd.get("category") as string;
    const monthlyLimit = parseFloat(fd.get("monthlyLimit") as string);
    const month = (fd.get("month") as string) || currentMonth;

    try {
      await api.post("/budgets/upsert", { category, monthlyLimit, month });
      (e.target as HTMLFormElement).reset();
      setOpenAdd(false);
      loadBudgets();
    } catch (err: any) {
      setAddError(err.response?.data?.error || "Failed to save budget");
    } finally {
      setSavingAdd(false);
    }
  }

  function startEdit(b: any) {
    setEditingId(b._id);
    setEditLimit(b.monthlyLimit || 0);
  }

  function cancelEdit() {
    setEditingId(null);
  }

  async function saveEdit(b: any) {
    setSavingEdit(true);
    try {
      await api.put(`/budgets/${b._id}`, {
        category: b.category,
        monthlyLimit: editLimit,
        month: b.month || currentMonth,
      });
      setEditingId(null);
      loadBudgets();
    } catch (err: any) {
      alert(err.response?.data?.error || "Failed to update budget limit.");
    } finally {
      setSavingEdit(false);
    }
  }

  async function deleteBudget(b: any) {
    if (!confirm(`Are you sure you want to delete the budget for "${b.category}"?`)) return;
    setDeletingId(b._id);
    try {
      await api.delete(`/budgets/${b._id}`);
      loadBudgets();
    } catch (err: any) {
      alert(err.response?.data?.error || "Failed to delete budget.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Budgets</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Set monthly category spending limits and monitor utilization.
          </p>
        </div>
        <button className="button" onClick={() => setOpenAdd(!openAdd)}>
          {openAdd ? "✕ Cancel" : "+ Add Budget"}
        </button>
      </div>

      {/* Add Budget Form */}
      {openAdd && (
        <form onSubmit={handleAddBudget} className="card mb-6 grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-3">
            <h2 className="font-semibold text-slate-200">Set Monthly Budget</h2>
          </div>
          <select className="input" name="category" required defaultValue="">
            <option value="" disabled>Select Category</option>
            {CATEGORIES.filter(c => c !== "Income").map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <div className="relative">
            <span className="absolute left-3 top-2.5 text-xs text-slate-400">₹</span>
            <input
              className="input pl-7 w-full"
              name="monthlyLimit"
              type="number"
              step="1"
              min="1"
              placeholder="Monthly limit (e.g. 5000)"
              required
            />
          </div>
          <input
            className="input"
            name="month"
            type="month"
            defaultValue={currentMonth}
            required
          />
          {addError && <p className="sm:col-span-3 text-xs text-rose-400">{addError}</p>}
          <button className="button sm:col-span-3" disabled={savingAdd}>
            {savingAdd ? "Saving…" : "Save Budget"}
          </button>
        </form>
      )}

      {/* Current Budgets Section */}
      <section className="card mb-6">
        <h2 className="mb-4 font-semibold">Current Budgets</h2>
        {budgets.length ? (
          <div className="divide-y divide-slate-800">
            {budgets.map((b) => (
              <div key={b._id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-200">{b.category}</span>
                    {b.month && (
                      <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-400">
                        {b.month}
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  {editingId === b._id ? (
                    <div className="flex items-center gap-2">
                      <div className="relative">
                        <span className="absolute left-2.5 top-2 text-xs text-slate-400">₹</span>
                        <input
                          type="number"
                          step="1"
                          min="0"
                          value={editLimit}
                          onChange={(e) => setEditLimit(parseFloat(e.target.value) || 0)}
                          className="input pl-6 py-1 h-8 w-28 text-xs font-medium"
                          autoFocus
                        />
                      </div>
                      <button
                        className="button-sm h-8 px-2 text-xs"
                        onClick={() => saveEdit(b)}
                        disabled={savingEdit}
                      >
                        {savingEdit ? "…" : "Save"}
                      </button>
                      <button
                        className="button-sm h-8 px-2 text-xs bg-slate-800 hover:bg-slate-700 text-slate-400 border-slate-700"
                        onClick={cancelEdit}
                      >
                        ✕
                      </button>
                    </div>
                  ) : (
                    <span className="font-medium text-cyan-300">
                      {fmt(b.monthlyLimit)} <span className="text-xs text-slate-400 font-normal">/ mo</span>
                    </span>
                  )}

                  {editingId !== b._id && (
                    <div className="flex items-center gap-3 pl-2 border-l border-slate-800">
                      <button
                        className="text-xs text-cyan-400 hover:text-cyan-200"
                        onClick={() => startEdit(b)}
                        title="Edit limit"
                      >
                        Edit
                      </button>
                      <button
                        className="text-xs text-rose-400 hover:text-rose-200"
                        onClick={() => deleteBudget(b)}
                        disabled={deletingId === b._id}
                        title="Delete budget"
                      >
                        {deletingId === b._id ? "…" : "Delete"}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Empty label="No budgets set. Click '+ Add Budget' or use AI recommendations below to get started." />
        )}
      </section>

      {/* AI Budget Recommendations */}
      <section className="card">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-semibold">AI Budget Recommendations</h2>
          <button className="button" onClick={fetchRecommendations} disabled={loadingRecs}>
            {loadingRecs ? "Analyzing…" : "Get AI Recommendations"}
          </button>
        </div>
        <p className="mb-4 text-xs text-slate-400">
          Recommendations are based on your actual transaction history. You must approve each recommendation before it is applied.
        </p>

        {recError && <p className="text-rose-400 text-sm">{recError}</p>}

        {recommendations?.recommendations?.length > 0 && (
          <div className="space-y-4">
            {recommendations.recommendations.map((rec: any) => (
              <div key={rec.category} className="rounded-xl border border-slate-700 p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold">{rec.category}</h3>
                    <p className="mt-1 text-sm text-slate-300">{rec.reason}</p>
                  </div>
                  <ConfidenceDot confidence={rec.confidence || 0.7} />
                </div>
                <div className="mt-3 grid grid-cols-3 gap-3 text-xs text-slate-400">
                  <div>
                    <p>Historical Avg</p>
                    <p className="text-slate-200 font-medium">{fmt(rec.historical_average)}</p>
                  </div>
                  <div>
                    <p>Current Budget</p>
                    <p className="text-slate-200 font-medium">{rec.current_budget != null ? fmt(rec.current_budget) : "—"}</p>
                  </div>
                  <div>
                    <p>Recommended</p>
                    <p className="text-cyan-300 font-bold">{fmt(rec.recommended_budget)}</p>
                  </div>
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  Supporting period: {rec.supporting_period}
                  {rec.expected_savings_impact != null && (
                    <span className="ml-3">
                      Expected savings impact: {fmt(rec.expected_savings_impact)}/mo
                    </span>
                  )}
                </div>
                <div className="mt-3 flex gap-2">
                  <button
                    className="button"
                    onClick={() => acceptRecommendation(rec)}
                  >
                    Accept
                  </button>
                  <span className="text-xs text-slate-500 self-center">Requires your approval</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {recommendations?.overall_assessment && (
          <div className="mt-4 rounded-lg bg-slate-900 p-4 text-xs text-slate-400">
            <p>Total recommended monthly budget: <strong className="text-slate-200">{fmt(recommendations.overall_assessment.total_recommended_budget)}</strong></p>
            <p>As % of avg income: {recommendations.overall_assessment.vs_average_income}</p>
            {recommendations.overall_assessment.limitations?.map((l: string, i: number) => (
              <p key={i} className="mt-1 text-amber-400">⚠ {l}</p>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

// ─── Goals ───────────────────────────────────────────────────────────────────

function GoalImpactPanel({ goal }: { goal: any }) {
  const [impact, setImpact] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  async function fetchImpact() {
    if (impact) { setOpen(!open); return; }
    setLoading(true);
    setError("");
    setOpen(true);
    try {
      const res = await api.post(`/goals/${goal._id}/impact`);
      setImpact(res.data);
    } catch (err: any) {
      setError(err.response?.data?.error || "Could not compute cashflow impact.");
    } finally {
      setLoading(false);
    }
  }

  const pct = goal.targetAmount > 0
    ? Math.min(100, Math.round((goal.currentAmount / goal.targetAmount) * 100))
    : 0;

  return (
    <div className="border-b border-slate-800 py-4 last:border-0">
      {/* Goal header */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{goal.name}</span>
            <Badge
              label={goal.status}
              color={goal.status === "completed" ? "green" : goal.status === "paused" ? "yellow" : "cyan"}
            />
          </div>
          {goal.description && (
            <p className="text-xs text-slate-400 mt-0.5">{goal.description}</p>
          )}
        </div>
        <button
          onClick={fetchImpact}
          className="text-xs text-cyan-400 hover:text-cyan-200 font-medium border border-cyan-800 rounded px-2 py-1 flex items-center gap-1"
        >
          {loading ? "Analyzing…" : open ? "▲ Hide Impact" : "📊 Cashflow Impact"}
        </button>
      </div>

      {/* Amounts & Progress */}
      <div className="mt-2 flex flex-wrap gap-4 text-sm text-slate-400">
        <span>
          <span className="text-slate-200 font-medium">{fmt(goal.currentAmount)}</span>
          {" / "}
          <span>{fmt(goal.targetAmount)}</span>
          <span className="ml-1 text-slate-500">({pct}%)</span>
        </span>
        {goal.targetDate && (
          <span>🎯 Target: {new Date(goal.targetDate).toLocaleDateString("en-IN")}</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="mt-2 h-2 rounded-full bg-slate-800">
        <div
          className={`h-2 rounded-full transition-all ${pct >= 100 ? "bg-emerald-500" : "bg-cyan-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Cashflow Impact Panel */}
      {open && (
        <div className="mt-4 rounded-lg bg-slate-900 border border-slate-700 p-4">
          {loading && (
            <p className="text-xs text-cyan-300 animate-pulse">🤖 AI is analyzing your cashflow…</p>
          )}
          {error && <p className="text-xs text-rose-400">{error}</p>}
          {impact && !loading && (
            <>
              <p className="text-xs font-semibold text-slate-300 mb-3 uppercase tracking-wide">
                📊 Cashflow Impact Analysis
              </p>
              {/* Key metrics */}
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 mb-4">
                <div className="rounded bg-slate-800 p-2 text-center">
                  <p className="text-xs text-slate-400">Still Needed</p>
                  <p className="font-bold text-amber-300">{fmt(impact.remaining)}</p>
                </div>
                <div className="rounded bg-slate-800 p-2 text-center">
                  <p className="text-xs text-slate-400">Months Left</p>
                  <p className="font-bold text-slate-200">{impact.monthsToTarget}</p>
                </div>
                <div className="rounded bg-slate-800 p-2 text-center">
                  <p className="text-xs text-slate-400">Save / Month</p>
                  <p className="font-bold text-cyan-300">{fmt(impact.monthlySavingRequired)}</p>
                </div>
                {impact.impact.monthly_income != null && (
                  <div className="rounded bg-slate-800 p-2 text-center">
                    <p className="text-xs text-slate-400">Your Avg Income</p>
                    <p className="font-bold text-emerald-300">{fmt(impact.impact.monthly_income)}</p>
                  </div>
                )}
                {impact.impact.monthly_expense != null && (
                  <div className="rounded bg-slate-800 p-2 text-center">
                    <p className="text-xs text-slate-400">Avg Expenses</p>
                    <p className="font-bold text-rose-300">{fmt(impact.impact.monthly_expense)}</p>
                  </div>
                )}
                {impact.impact.net_monthly != null && (
                  <div className="rounded bg-slate-800 p-2 text-center">
                    <p className="text-xs text-slate-400">Monthly Surplus</p>
                    <p className={`font-bold ${impact.impact.net_monthly >= impact.monthlySavingRequired ? "text-emerald-300" : "text-rose-300"}`}>
                      {fmt(impact.impact.net_monthly)}
                    </p>
                  </div>
                )}
              </div>

              {/* Affordability badge */}
              {impact.impact.can_afford_monthly_saving != null && (
                <div className={`mb-3 rounded p-2 text-sm font-medium text-center ${impact.impact.can_afford_monthly_saving ? "bg-emerald-900/40 text-emerald-300" : "bg-rose-900/40 text-rose-300"}`}>
                  {impact.impact.can_afford_monthly_saving
                    ? `✅ You can afford to save ${fmt(impact.monthlySavingRequired)}/month for this goal`
                    : `⚠️ Saving ${fmt(impact.monthlySavingRequired)}/month may strain your cashflow`}
                </div>
              )}

              {/* AI Advisor explanation */}
              <div className="rounded bg-slate-800/50 p-3">
                <p className="text-xs text-cyan-300 font-medium mb-1">🤖 AI Financial Advisor Says:</p>
                <p className="text-sm text-slate-300 leading-relaxed">{impact.impact.advisor_message}</p>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function Goals() {
  const [items, setItems] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);

  function loadGoals() {
    api.get("/goals").then((x) => setItems(x.data.items || []));
  }

  useEffect(() => { loadGoals(); }, []);

  async function submitGoal(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    const fd = new FormData(e.currentTarget);
    const body: any = {
      name: fd.get("name") as string,
      description: fd.get("description") as string,
      targetAmount: parseFloat(fd.get("targetAmount") as string),
      currentAmount: parseFloat((fd.get("currentAmount") as string) || "0"),
      status: "active",
    };
    const td = fd.get("targetDate") as string;
    if (td) body.targetDate = td;
    try {
      await api.post("/goals", body);
      (e.target as HTMLFormElement).reset();
      setOpen(false);
      loadGoals();
    } catch (err: any) {
      setSaveError(err.response?.data?.error || "Failed to save goal.");
    } finally {
      setSaving(false);
    }
  }

  async function deleteGoal(id: string) {
    if (!confirm("Delete this goal?")) return;
    setDeletingId(id);
    try {
      await api.delete(`/goals/${id}`);
      loadGoals();
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Financial Goals</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Track purchases and savings targets. AI explains how each goal affects your cashflow.
          </p>
        </div>
        <button className="button" onClick={() => setOpen(!open)}>
          {open ? "✕ Cancel" : "+ Add Goal"}
        </button>
      </div>

      {/* Add Goal Form */}
      {open && (
        <form onSubmit={submitGoal} className="card mb-6 grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <p className="text-xs text-slate-400 mb-2">
              Describe what you want to buy or save for — e.g. "Buy a Laptop", "Emergency Fund", "Trip to Goa"
            </p>
          </div>
          <input
            className="input sm:col-span-2"
            name="name"
            placeholder="Goal name (e.g. Buy a Laptop, New Phone, Vacation)"
            required
          />
          <input
            className="input sm:col-span-2"
            name="description"
            placeholder="Description (optional — e.g. MacBook Air M2 from Apple Store)"
          />
          <div className="relative">
            <span className="absolute left-3 top-2.5 text-xs text-slate-400">₹</span>
            <input
              className="input pl-7"
              name="targetAmount"
              type="number"
              step="0.01"
              min="1"
              placeholder="Target amount (e.g. 80000)"
              required
            />
          </div>
          <div className="relative">
            <span className="absolute left-3 top-2.5 text-xs text-slate-400">₹</span>
            <input
              className="input pl-7"
              name="currentAmount"
              type="number"
              step="0.01"
              min="0"
              placeholder="Amount already saved (optional)"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">Target Date (optional)</label>
            <input className="input w-full" name="targetDate" type="date" />
          </div>
          <div className="flex items-end">
            <div className="flex items-center px-3 py-2 bg-slate-900 border border-slate-800 rounded-lg text-xs text-cyan-300 w-full">
              💡 After saving, click "Cashflow Impact" to get AI analysis
            </div>
          </div>
          {saveError && <p className="sm:col-span-2 text-xs text-rose-400">{saveError}</p>}
          <button className="button sm:col-span-2" disabled={saving}>
            {saving ? "Saving…" : "Save Goal"}
          </button>
        </form>
      )}

      {/* Goals List */}
      <section className="card">
        {items.length ? (
          items.map((x) => (
            <div key={x._id} className="relative group">
              <GoalImpactPanel goal={x} />
              <button
                className="absolute top-4 right-28 text-xs text-slate-600 hover:text-rose-400 opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={() => deleteGoal(x._id)}
                disabled={deletingId === x._id}
              >
                {deletingId === x._id ? "…" : "Delete"}
              </button>
            </div>
          ))
        ) : (
          <Empty label="No goals yet. Click '+ Add Goal' to set a savings target or purchase goal." />
        )}
      </section>
    </>
  );
}

// ─── Financial Analysis ───────────────────────────────────────────────────────

export function FinancialAnalysis() {
  const [output, setOutput] = useState<any>();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  async function runAnalysis() {
    setRunning(true);
    setError("");
    try {
      const res = await api.post("/ai/analyze");
      setOutput(res.data);
    } catch (err: any) {
      setError(err.response?.data?.error || "Analysis service is unavailable.");
    } finally {
      setRunning(false);
    }
  }

  const llmAnalysis = output?.result?.llm_financial_analysis;
  const spending = output?.result?.spending;
  const health = output?.result?.health;
  const anomalies: any[] = output?.result?.anomalies || [];
  const subscriptions: any[] = output?.result?.subscriptions || [];

  return (
    <>
      <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Financial Analysis</h1>
          <p className="mt-2 text-base text-slate-400">
            Analysis uses your complete transaction history with LLM-powered interpretation.
          </p>
        </div>
        <button className="button px-6 py-3 text-base" onClick={runAnalysis} disabled={running}>
          {running ? "Analyzing…" : "Run Financial Analysis"}
        </button>
      </div>

      {error && <div className="card mb-4 text-rose-400">{error}</div>}

      {running && (
        <div className="card mb-6 p-6">
          <p className="text-lg font-medium text-cyan-300">Running financial analysis workflow…</p>
          <div className="mt-4 space-y-2 text-base text-slate-400">
            <p>▸ Retrieving transaction history from MongoDB…</p>
            <p>▸ Running deterministic financial calculations…</p>
            <p>▸ Sending verified metrics to LLM for interpretation…</p>
            <p>▸ Validating outputs…</p>
          </div>
        </div>
      )}

      {output && (
        <>
          {/* Coverage report */}
          <div className="card mb-6 p-6">
            <div className="flex flex-wrap gap-x-6 gap-y-3 text-base">
              <span className="text-slate-400">Workflow: <strong className="text-slate-200">{output.workflow_id}</strong></span>
              <span className="text-slate-400">Status: <Badge label={output.status} color={output.status === "completed" ? "green" : "red"} /></span>
              <span className="text-slate-400">Transactions analyzed: <strong className="text-slate-200">{output.result?.transactions?.length ?? "N/A"}</strong></span>
            </div>
          </div>

          {/* LLM Executive Summary */}
          {llmAnalysis?.executive_summary && (
            <div className="card mb-6 p-6">
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-xl font-semibold">Executive Summary</h2>
                <Badge label="AI Analysis" color="cyan" />
              </div>
              <p className="text-base leading-7 text-slate-300">{llmAnalysis.executive_summary}</p>
              {llmAnalysis.confidence && (
                <div className="mt-2">
                  <ConfidenceDot confidence={llmAnalysis.confidence} />
                </div>
              )}
            </div>
          )}

          {/* Strengths and Weaknesses */}
          {(llmAnalysis?.financial_strengths?.length > 0 || llmAnalysis?.financial_weaknesses?.length > 0) && (
            <div className="mb-6 grid gap-4 lg:grid-cols-2">
              {llmAnalysis.financial_strengths?.length > 0 && (
                <div className="card p-6">
                  <h2 className="mb-4 text-xl font-semibold text-emerald-400">Financial Strengths</h2>
                  <ul className="space-y-3">
                    {llmAnalysis.financial_strengths.map((s: string, i: number) => (
                      <li key={i} className="flex gap-2 text-sm"><span className="text-emerald-400">✓</span>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {llmAnalysis.financial_weaknesses?.length > 0 && (
                <div className="card p-6">
                  <h2 className="mb-4 text-xl font-semibold text-amber-400">Areas to Watch</h2>
                  <ul className="space-y-3">
                    {llmAnalysis.financial_weaknesses.map((s: string, i: number) => (
                      <li key={i} className="flex gap-2 text-sm"><span className="text-amber-400">!</span>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Recommended Actions */}
          {llmAnalysis?.recommended_actions?.length > 0 && (
            <div className="card mb-6 p-6">
              <h2 className="mb-4 text-xl font-semibold">Recommended Actions</h2>
              <div className="space-y-4">
                {llmAnalysis.recommended_actions.map((a: any, i: number) => (
                  <div key={i} className="rounded-xl bg-slate-950 p-4">
                    <p className="font-medium text-cyan-300">{a.action}</p>
                    <p className="mt-1 text-sm text-slate-400">{a.reason}</p>
                    {a.expected_impact && <p className="mt-1 text-xs text-emerald-400">Impact: {a.expected_impact}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Spending chart */}
          {spending?.category_summary && Object.keys(spending.category_summary).length > 0 && (
            <div className="card mb-6 h-96 p-6">
              <h2 className="mb-5 text-xl font-semibold">Category-wise Spending</h2>
              <ResponsiveContainer>
                <BarChart
                  data={Object.entries(spending.category_summary).map(([k, v]: any) => ({
                    category: k,
                    amount: typeof v === "object" ? (v.total || 0) : v,
                  }))}
                >
                  <XAxis dataKey="category" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(v) => fmt(Number(v))} />
                  <Bar dataKey="amount" fill="#22d3ee" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Health Score */}
          {health && (
            <div className="card mb-6 p-6">
              <h2 className="mb-4 text-xl font-semibold">Financial Health Score</h2>
              <div className="flex items-center gap-5">
                <span className="text-6xl font-bold text-cyan-300">{health.score ?? "—"}</span>
                <div className="text-sm text-slate-400">
                  <p>{health.band?.label ?? ""}</p>
                  <p className="text-slate-500">{health.band?.summary ?? ""}</p>
                </div>
              </div>
            </div>
          )}

          {/* Anomalies */}
          {anomalies.length > 0 && (
            <div className="card mb-6 p-6">
              <h2 className="mb-4 text-xl font-semibold">Unusual Transactions ({anomalies.length})</h2>
              <div className="space-y-2">
                {anomalies.slice(0, 5).map((a: any, i: number) => (
                  <div key={i} className="rounded-lg bg-slate-900 p-3 text-sm">
                    <div className="flex justify-between">
                      <span className="font-medium">{a.merchant || a.transaction_id}</span>
                      <Badge label={a.severity || "medium"} color={a.severity === "high" ? "red" : "yellow"} />
                    </div>
                    <p className="mt-1 text-slate-400">{a.reason || a.anomaly_type}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Subscriptions */}
          {subscriptions.length > 0 && (
            <div className="card mb-6 p-6">
              <h2 className="mb-4 text-xl font-semibold">Recurring Payments ({subscriptions.length})</h2>
              <div className="space-y-2">
                {subscriptions.slice(0, 8).map((s: any, i: number) => (
                  <div key={i} className="flex justify-between border-b border-slate-800 py-2 text-sm">
                    <span>{s.merchant}</span>
                    <div className="flex items-center gap-3">
                      <Badge label={s.status || "potential"} color={s.status?.includes("confirmed") ? "green" : "yellow"} />
                      <span>{fmt(s.amount)}/mo</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Limitations */}
          {llmAnalysis?.data_limitations?.length > 0 && (
            <div className="card border border-amber-800 text-sm">
              <h3 className="mb-2 font-medium text-amber-400">Analysis Limitations</h3>
              <ul className="space-y-1 text-slate-400">
                {llmAnalysis.data_limitations.map((l: string, i: number) => (
                  <li key={i}>• {l}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </>
  );
}

// ─── Financial Health ─────────────────────────────────────────────────────────

export function FinancialHealth() {
  const [output, setOutput] = useState<any>();
  const [running, setRunning] = useState(false);

  useEffect(() => {
    // Automatically load health score on page mount
    api.post("/ai/analyze").then((res) => setOutput(res.data)).catch(() => null);
  }, []);

  async function run() {
    setRunning(true);
    try {
      const res = await api.post("/ai/analyze");
      setOutput(res.data);
    } finally {
      setRunning(false);
    }
  }

  const health = output?.result?.health;
  const components: any[] = health?.components || [];
  const bandLabel = typeof health?.band === "string" ? health.band : health?.band?.label || "Good";
  const interpretation = health?.interpretation || (typeof health?.band === "object" ? health?.band?.summary : "") || "";

  return (
    <>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Financial Health</h1>
        <button className="button" onClick={run} disabled={running}>
          {running ? "Calculating…" : "Recalculate Health Score"}
        </button>
      </div>
      <p className="mb-6 text-sm text-slate-400">
        Health score is calculated from your savings rate, budget adherence, and emergency fund.
        Values are deterministic — LLM interprets the patterns.
      </p>

      {health && (
        <>
          <div className="card mb-6 flex items-center gap-6">
            <div className="text-6xl font-bold text-cyan-300">{health.score != null ? Math.round(health.score) : "—"}</div>
            <div>
              <p className="text-xl font-semibold text-white">{bandLabel}</p>
              <p className="text-slate-400 text-sm mt-1">{interpretation}</p>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {components.length > 0 ? (
              components.map((c: any) => (
                <div key={c.key || c.label} className="card">
                  <div className="flex justify-between items-center mb-2">
                    <p className="text-sm font-semibold text-slate-300">{c.label}</p>
                    <Badge
                      label={c.available ? `${Math.round(c.points ?? 0)}/${c.max_points}` : "Missing data"}
                      color={c.available ? "green" : "slate"}
                    />
                  </div>
                  <p className="text-xs text-slate-400">{c.detail}</p>
                  {c.improvement && (
                    <p className="mt-2 text-xs text-cyan-400">Tip: {c.improvement}</p>
                  )}
                </div>
              ))
            ) : (
              <div className="card text-center sm:col-span-3">
                <p className="text-slate-400">No components available to score.</p>
              </div>
            )}
          </div>
        </>
      )}

      {!output && <Empty label="Click 'Recalculate Health Score' to compute your financial health from your transaction history." />}
    </>
  );
}

// ─── Cash-Flow Forecast ───────────────────────────────────────────────────────

export function CashFlowForecast() {
  const [output, setOutput] = useState<any>();
  const [running, setRunning] = useState(false);
  const [cashflowHistory, setCashflowHistory] = useState<any[]>([]);

  useEffect(() => {
    api.get("/dashboard/cashflow").then((x) => setCashflowHistory(x.data.actual || []));
  }, []);

  async function run() {
    setRunning(true);
    try {
      const res = await api.post("/ai/forecast");
      setOutput(res.data);
    } finally {
      setRunning(false);
    }
  }

  const forecast = output?.result?.forecast;
  const llmForecast = output?.result?.llm_forecast_interpretation;

  return (
    <>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Cash-Flow Forecast</h1>
          <p className="text-sm text-slate-400">
            Forecasts are estimates based on historical patterns — not guaranteed outcomes.
          </p>
        </div>
        <button className="button" onClick={run} disabled={running}>
          {running ? "Forecasting…" : "Generate Forecast"}
        </button>
      </div>

      {/* Historical Cash Flow Chart */}
      {cashflowHistory.length > 0 && (
        <div className="card mb-6 h-72">
          <h2 className="mb-4 font-semibold">Historical Cash Flow</h2>
          <ResponsiveContainer>
            <LineChart data={cashflowHistory}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="month" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v) => fmt(Number(v))} />
              <Legend />
              <Line type="monotone" dataKey="income" stroke="#34d399" name="Income" strokeWidth={2} />
              <Line type="monotone" dataKey="expense" stroke="#f87171" name="Expenses" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Deterministic Forecast */}
      {forecast && (
        <div className="card mb-6">
          <div className="mb-2 flex items-center gap-2">
            <h2 className="font-semibold">Month Forecast: {forecast.month}</h2>
            <Badge label="Estimate" color="yellow" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["Monthly Income", forecast.monthly_income, "emerald"],
              ["Projected Expenses", forecast.projected_expense, "rose"],
              ["Net Savings", forecast.net_savings, forecast.net_savings >= 0 ? "emerald" : "red"],
              ["Predicted Balance", forecast.predicted_end_balance, "cyan"],
            ].map(([label, val, color]) => (
              <div key={String(label)} className="rounded-xl bg-slate-900 p-4">
                <p className="text-xs text-slate-400">{label}</p>
                <p className={`mt-1 text-xl font-bold text-${color}-300`}>{fmt(val as any)}</p>
              </div>
            ))}
          </div>
          <div className="mt-4 text-xs text-slate-400">
            <p>Method: {forecast.projection_method}</p>
            {forecast.assumptions?.map((a: string, i: number) => <p key={i}>• {a}</p>)}
          </div>
          {forecast.deficit && (
            <p className="mt-3 text-sm text-rose-400 font-medium">
              ⚠ A deficit is projected for this month.
            </p>
          )}
        </div>
      )}

      {/* LLM Forecast Interpretation (if available) */}
      {llmForecast?.forecasts?.length > 0 && (
        <div className="card mb-6">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="font-semibold">AI Forecast Interpretation</h2>
            <Badge label="AI Estimate" color="cyan" />
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            {llmForecast.forecasts.map((f: any) => (
              <div key={f.forecast_period} className="rounded-xl bg-slate-900 p-4">
                <p className="mb-2 text-xs font-medium text-cyan-400">
                  {f.forecast_period.replace(/_/g, " ")}
                </p>
                <div className="space-y-1 text-sm">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Income</span>
                    <span className="text-emerald-300">{fmt(f.expected_income)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Expenses</span>
                    <span className="text-rose-300">{fmt(f.expected_expenses)}</span>
                  </div>
                  <div className="flex justify-between font-medium">
                    <span className="text-slate-400">Net</span>
                    <span className={f.expected_net_cashflow >= 0 ? "text-emerald-300" : "text-rose-300"}>
                      {fmt(f.expected_net_cashflow)}
                    </span>
                  </div>
                </div>
                <div className="mt-2">
                  <ConfidenceDot confidence={f.confidence || 0.5} />
                </div>
                {f.limitations?.length > 0 && (
                  <p className="mt-1 text-xs text-amber-400">{f.limitations[0]}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {!output && (
        <Empty label="Click 'Generate Forecast' to produce an evidence-based cash-flow forecast from your transaction history." />
      )}
    </>
  );
}

// ─── AI Advisor ───────────────────────────────────────────────────────────────

const STARTER_QUESTIONS = [
  "Where am I spending the most money?",
  "Which subscriptions am I paying for?",
  "How can I improve my savings rate?",
  "Am I spending too much on food?",
  "What changed in my spending last month?",
  "Which categories are over budget?",
  "How much should I save per month?",
];

export function AIAdvisor() {
  const [messages, setMessages] = useState<{ role: "user" | "assistant"; content: any }[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  async function sendMessage(question?: string) {
    const q = question || input.trim();
    if (!q) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", content: q }]);

    try {
      const res = await api.post("/ai/chat", { prompt: q });
      const result = res.data.result || {};
      const advisorResp = result.advisor_response || {};
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: advisorResp.direct_answer
            ? advisorResp
            : { direct_answer: result.advisor_message || "Analysis complete. Ask a specific question about your finances." },
        },
      ]);
    } catch (err: any) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: { direct_answer: "The advisor service is unavailable. Your financial records are intact." } },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">AI Financial Advisor</h1>
        <p className="text-sm text-slate-400">
          Answers are grounded in your actual transaction history. The advisor never fabricates financial data.
          Not certified financial advice.
        </p>
      </div>

      {/* Starter Questions */}
      {messages.length === 0 && (
        <div className="mb-6">
          <p className="mb-3 text-sm text-slate-400">Suggested questions:</p>
          <div className="flex flex-wrap gap-2">
            {STARTER_QUESTIONS.map((q) => (
              <button
                key={q}
                className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-cyan-500 hover:text-cyan-300"
                onClick={() => sendMessage(q)}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Chat History */}
      <div className="mb-4 space-y-4">
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            {m.role === "user" ? (
              <div className="max-w-2xl rounded-2xl bg-cyan-900 px-4 py-3 text-sm text-cyan-100">
                {m.content}
              </div>
            ) : (
              <div className="max-w-3xl">
                <div className="card">
                  {/* Direct Answer */}
                  <p className="font-medium text-slate-200">{m.content.direct_answer}</p>

                  {/* Evidence */}
                  {m.content.financial_evidence?.length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs font-medium text-cyan-400 mb-1">Evidence from your records:</p>
                      <div className="space-y-1">
                        {m.content.financial_evidence.map((ev: any, j: number) => (
                          <div key={j} className="flex gap-3 text-xs text-slate-300">
                            <span className="text-slate-500">{ev.period}</span>
                            <span>{ev.description}: <strong>{ev.value}</strong></span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Pattern */}
                  {m.content.pattern_identified && (
                    <div className="mt-3">
                      <p className="text-xs font-medium text-purple-400 mb-1">Pattern identified:</p>
                      <p className="text-sm text-slate-300">{m.content.pattern_identified}</p>
                    </div>
                  )}

                  {/* Recommendation */}
                  {m.content.personalized_recommendation && (
                    <div className="mt-3 rounded-lg bg-cyan-950 p-3">
                      <p className="text-xs font-medium text-cyan-300 mb-1">Personalized recommendation:</p>
                      <p className="text-sm text-slate-200">{m.content.personalized_recommendation}</p>
                      {m.content.expected_impact && (
                        <p className="mt-1 text-xs text-emerald-400">Expected impact: {m.content.expected_impact}</p>
                      )}
                    </div>
                  )}

                  {/* Limitations */}
                  {m.content.limitations?.length > 0 && (
                    <div className="mt-3">
                      <p className="text-xs text-amber-400">
                        Limitations: {m.content.limitations.join(". ")}
                      </p>
                    </div>
                  )}

                  {/* Follow-up */}
                  {m.content.follow_up_questions?.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {m.content.follow_up_questions.slice(0, 3).map((q: string, j: number) => (
                        <button
                          key={j}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:text-cyan-300"
                          onClick={() => sendMessage(q)}
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="card">
              <p className="text-sm text-slate-400">Analyzing your financial records…</p>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="flex gap-3">
        <input
          className="input flex-1"
          placeholder="Ask about your finances…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
          disabled={sending}
        />
        <button className="button" onClick={() => sendMessage()} disabled={sending || !input.trim()}>
          {sending ? "…" : "Ask"}
        </button>
      </div>
    </>
  );
}

// ─── Reports & Settings ───────────────────────────────────────────────────────

export function Reports() {
  const [workflows, setWorkflows] = useState<any[]>([]);
  useEffect(() => {
    api.get("/ai/workflows").catch(() => null).then((x) => {
      if (x) setWorkflows(x.data.workflows || []);
    });
  }, []);

  return (
    <>
      <h1 className="mb-6 text-2xl font-bold">Reports & Settings</h1>
      <section className="card mb-6">
        <h2 className="mb-4 font-semibold">Analysis History</h2>
        {workflows.length ? (
          <div className="space-y-2">
            {workflows.map((w: any) => (
              <div key={w.workflowId} className="flex justify-between border-b border-slate-800 py-2 text-sm">
                <div>
                  <span className="font-mono text-xs text-slate-500">{w.workflowId}</span>
                  <span className="ml-3">{new Date(w.createdAt).toLocaleDateString("en-IN")}</span>
                </div>
                <Badge label={w.workflowStatus || "completed"} color={w.workflowStatus === "completed" ? "green" : "yellow"} />
              </div>
            ))}
          </div>
        ) : (
          <Empty label="No analysis history yet. Run a financial analysis to generate reports." />
        )}
      </section>
      <div className="card border border-slate-700">
        <p className="text-sm text-slate-400">
          Reports are generated only when you explicitly request them. No financial action is performed automatically.
          All LLM-generated insights are clearly labeled as AI analysis and must be reviewed by you.
        </p>
      </div>
    </>
  );
}

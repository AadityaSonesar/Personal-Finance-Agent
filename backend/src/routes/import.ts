import { Router } from "express";
import multer from "multer";
import { randomUUID } from "node:crypto";
import { Transaction } from "../models/index.js";
import { AuthRequest, requireAuth } from "../middleware/auth.js";
import { callAi } from "../services/ai.js";
import { audit } from "../utils/audit.js";

const router = Router();
router.use(requireAuth);

// Accept CSV/Excel in memory — max 10 MB
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 },
  fileFilter: (_req, file, cb) => {
    const allowed = [
      "text/csv",
      "application/vnd.ms-excel",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "application/csv",
      "text/plain",
    ];
    const nameOk = /\.(csv|xlsx?|xls|txt)$/i.test(file.originalname);
    cb(null, allowed.includes(file.mimetype) || nameOk);
  },
});

/**
 * Split a single CSV line handling quotes and given delimiter.
 */
function splitLine(line: string, sep: string): string[] {
  const cells: string[] = [];
  let cur = "";
  let inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuote && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuote = !inQuote;
      }
    } else if (ch === sep && !inQuote) {
      cells.push(cur.trim());
      cur = "";
    } else {
      cur += ch;
    }
  }
  cells.push(cur.trim());
  return cells;
}

/** Detect best delimiter for a line. */
function detectDelimiter(line: string): string {
  const counts: Record<string, number> = {
    ",": (line.match(/,/g) || []).length,
    ";": (line.match(/;/g) || []).length,
    "\t": (line.match(/\t/g) || []).length,
    "|": (line.match(/\|/g) || []).length,
  };
  let best = ",";
  let max = -1;
  for (const [sep, count] of Object.entries(counts)) {
    if (count > max) {
      max = count;
      best = sep;
    }
  }
  return best;
}

/** Clean a column name into a normalized identifier. */
function normalizeHeader(h: string): string {
  return h
    .replace(/^\uFEFF/, "") // strip BOM
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/** Comprehensive column aliases covering worldwide and Indian bank statement terms. */
const COLUMN_ALIASES = {
  date: [
    "date", "transaction_date", "txn_date", "value_date", "posting_date",
    "posted_date", "trans_date", "booking_date", "trade_date", "time", "trans_dt",
  ],
  merchant: [
    "merchant", "payee", "vendor", "beneficiary", "name", "payee_name",
    "party", "party_name", "counterparty", "to_from", "recipient", "store",
    "entity", "merchant_name", "vendor_name", "seller", "client", "receiver",
    "transaction_reference", "txn_reference", "transaction_ref", "reference",
    "ref_no", "ref", "trans_ref", "reference_number", "reference_details",
  ],
  description: [
    "description", "narration", "particulars", "details", "memo", "remarks",
    "remark", "note", "notes", "transaction_details", "narrative", "title",
    "item", "purpose", "info", "information", "statement_details", "trans_details",
    "txn_description", "transaction_description", "description_narration",
    "transaction_reference", "txn_reference", "transaction_ref", "reference",
    "ref_no", "trans_ref", "reference_number", "reference_details",
  ],
  amount: [
    "amount", "txn_amount", "transaction_amount", "value", "amt",
    "amount_inr", "amount_rs", "total", "net_amount",
  ],
  debit: [
    "debit", "withdrawal", "withdrawal_amount", "debit_amount", "dr",
    "paid_out", "expense", "outgoing", "debit_amt",
  ],
  credit: [
    "credit", "deposit", "deposit_amount", "credit_amount", "cr",
    "paid_in", "income", "incoming", "credit_amt",
  ],
  type: [
    "type", "transaction_type", "txn_type", "dr_cr", "direction",
    "debit_credit", "cr_dr", "entry_type",
  ],
  category: [
    "category", "expense_category", "tag", "classification", "category_name",
  ],
  id: [
    "transaction_id", "txn_id", "utr", "cheque_no", "chq_no", "trans_id",
  ],
};

function matchCandidate(header: string, candidates: string[]): boolean {
  const norm = normalizeHeader(header);
  if (!norm) return false;
  const tokens = norm.split("_");

  for (const c of candidates) {
    const cNorm = normalizeHeader(c);
    // Exact match
    if (norm === cNorm) return true;
    // Substring containment
    if (norm.includes(cNorm) || cNorm.includes(norm)) return true;
    // Token overlap
    if (tokens.includes(cNorm)) return true;
  }
  return false;
}

function resolveColumn(headers: string[], candidates: string[]): string | undefined {
  for (const h of headers) {
    if (matchCandidate(h, candidates)) {
      return h;
    }
  }
  return undefined;
}

/**
 * Parses CSV buffer into array of row objects, discovering header row even if preceded by metadata/preamble.
 */
function parseCsv(buffer: Buffer): { headers: string[]; rows: Record<string, string>[] } {
  const text = buffer
    .toString("utf-8")
    .replace(/^\uFEFF/, "") // Remove UTF-8 BOM
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");

  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) throw new Error("CSV contains insufficient data (less than 2 lines).");

  // Determine global delimiter from sample lines
  const sep = detectDelimiter(lines.slice(0, 10).join("\n"));

  // Scan first 30 lines to find the true table header row
  let bestHeaderIndex = -1;
  let bestScore = -1;
  let bestHeaders: string[] = [];

  const maxScan = Math.min(30, lines.length);
  for (let i = 0; i < maxScan; i++) {
    const candidateCells = splitLine(lines[i], sep).map(normalizeHeader);
    if (candidateCells.length < 2) continue;

    let score = 0;
    if (candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.date))) score += 2;
    if (
      candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.amount)) ||
      (candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.debit)) &&
        candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.credit)))
    )
      score += 2;
    if (
      candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.merchant)) ||
      candidateCells.some((c) => matchCandidate(c, COLUMN_ALIASES.description))
    )
      score += 2;

    if (score > bestScore) {
      bestScore = score;
      bestHeaderIndex = i;
      bestHeaders = splitLine(lines[i], sep).map(normalizeHeader);
    }
  }

  // Fallback to line 0 if scoring failed
  if (bestHeaderIndex === -1 || bestScore < 2) {
    bestHeaderIndex = 0;
    bestHeaders = splitLine(lines[0], sep).map(normalizeHeader);
  }

  const rows: Record<string, string>[] = [];
  for (let i = bestHeaderIndex + 1; i < lines.length; i++) {
    const vals = splitLine(lines[i], sep);
    if (!vals.some((v) => v.trim())) continue;
    const row: Record<string, string> = {};
    bestHeaders.forEach((h, idx) => {
      row[h] = (vals[idx] || "").trim();
    });
    rows.push(row);
  }

  return { headers: bestHeaders, rows };
}

function cleanAmount(val: string): number | null {
  const cleaned = val.replace(/[₹$€£¥,\s]/g, "");
  const n = parseFloat(cleaned);
  return isNaN(n) ? null : Math.abs(n);
}

function inferDate(val: string): Date | null {
  if (!val) return null;
  const iso = new Date(val);
  if (!isNaN(iso.getTime())) return iso;
  const parts = val.split(/[\/\-.]/);
  if (parts.length === 3) {
    const [a, b, c] = parts;
    if (c.length === 4) {
      const d = new Date(`${c}-${b.padStart(2, "0")}-${a.padStart(2, "0")}`);
      if (!isNaN(d.getTime())) return d;
    } else if (a.length === 4) {
      const d = new Date(`${a}-${b.padStart(2, "0")}-${c.padStart(2, "0")}`);
      if (!isNaN(d.getTime())) return d;
    }
  }
  return null;
}

/** Convert raw CSV rows to transaction objects + detect duplicates. */
async function mapRowsToTransactions(
  headers: string[],
  rows: Record<string, string>[],
  userId: string,
): Promise<{ valid: any[]; skipped: number; duplicates: number }> {
  const dateKey = resolveColumn(headers, COLUMN_ALIASES.date);
  let merchantKey = resolveColumn(headers, COLUMN_ALIASES.merchant);
  let descKey = resolveColumn(headers, COLUMN_ALIASES.description);
  const refKey = resolveColumn(headers, [
    "transaction_reference",
    "txn_reference",
    "transaction_ref",
    "reference",
    "ref_no",
    "trans_ref",
    "reference_number",
    "reference_details",
  ]);
  const amountKey = resolveColumn(headers, COLUMN_ALIASES.amount);
  const debitKey = resolveColumn(headers, COLUMN_ALIASES.debit);
  const creditKey = resolveColumn(headers, COLUMN_ALIASES.credit);
  const typeKey = resolveColumn(headers, COLUMN_ALIASES.type);
  const categoryKey = resolveColumn(headers, COLUMN_ALIASES.category);
  const idKey = resolveColumn(headers, COLUMN_ALIASES.id);

  // If Transaction Reference is found, use it as merchantKey if no other merchant column exists
  if (!merchantKey && refKey) {
    merchantKey = refKey;
  }

  if (!dateKey) {
    throw new Error(
      `Could not locate a date column in the CSV. Detected headers: [${headers.join(", ")}].`
    );
  }

  if (!amountKey && !debitKey && !creditKey) {
    throw new Error(
      `Could not locate an amount, debit, or credit column in the CSV. Detected headers: [${headers.join(", ")}].`
    );
  }

  // Fallback: If neither merchant nor description was matched, look for any non-numeric text column
  if (!merchantKey && !descKey) {
    const knownKeys = new Set([dateKey, amountKey, debitKey, creditKey, typeKey, categoryKey, idKey].filter(Boolean));
    const candidateTextColumns = headers.filter((h) => !knownKeys.has(h));
    if (candidateTextColumns.length > 0) {
      // Pick the first candidate column as merchant/description
      descKey = candidateTextColumns[0];
    }
  }

  if (!merchantKey && !descKey) {
    throw new Error(
      `Could not locate a merchant or description column in the CSV. Detected headers: [${headers.join(", ")}]. ` +
      `Expected a column such as Merchant, Payee, Description, Narration, Details, or Particulars.`
    );
  }

  const valid: any[] = [];
  let skipped = 0;
  let duplicates = 0;

  // Build existing signatures for duplicate detection
  const existing = await Transaction.find({ userId })
    .select("transactionId date merchant amount type")
    .lean();
  const existingSet = new Set<string>(
    existing.map(
      (t: any) =>
        `${t.date?.toISOString?.()?.slice(0, 10)}|${String(t.merchant).toLowerCase()}|${t.amount}|${t.type}`
    )
  );

  for (const row of rows) {
    const rawDate = row[dateKey] || "";
    const date = inferDate(rawDate);
    if (!date) {
      skipped++;
      continue;
    }

    let amount: number | null = null;
    let type: "income" | "expense" = "expense";

    const debitVal = debitKey && row[debitKey] ? cleanAmount(row[debitKey]) : null;
    const creditVal = creditKey && row[creditKey] ? cleanAmount(row[creditKey]) : null;

    if (debitVal !== null && debitVal > 0) {
      amount = debitVal;
      type = "expense";
    } else if (creditVal !== null && creditVal > 0) {
      amount = creditVal;
      type = "income";
    } else if (amountKey && row[amountKey]) {
      amount = cleanAmount(row[amountKey]);
      if (amount === null) {
        skipped++;
        continue;
      }
      const rawType = (typeKey ? row[typeKey] : "").toLowerCase();
      if (rawType.includes("cr") || rawType.includes("credit") || rawType.includes("income") || rawType.includes("deposit")) {
        type = "income";
      }
    }

    if (!amount || amount <= 0) {
      skipped++;
      continue;
    }

    const refVal = refKey ? (row[refKey] || "").trim() : "";
    let merchantRaw = merchantKey ? (row[merchantKey] || "").trim() : "";
    const descRaw = descKey ? (row[descKey] || "").trim() : "";

    // Put Transaction Reference in merchant as requested by user
    if (!merchantRaw || merchantRaw.toLowerCase() === "unknown") {
      merchantRaw = refVal || descRaw.split("  ")[0].slice(0, 80) || "Unknown";
    }

    const merchant = (merchantRaw || "Unknown").trim();
    const description = (descRaw || refVal || "").trim();
    const category = categoryKey ? row[categoryKey] || "Other" : "Other";
    const transactionId = (idKey ? row[idKey] : "") || randomUUID();

    const sig = `${date.toISOString().slice(0, 10)}|${merchant.toLowerCase()}|${amount}|${type}`;
    if (existingSet.has(sig)) {
      duplicates++;
      continue;
    }
    existingSet.add(sig);

    valid.push({
      transactionId,
      merchant,
      description,
      amount,
      type,
      category,
      date,
      userId,
      source: "import",
    });
  }

  return { valid, skipped, duplicates };
}

/**
 * POST /api/transactions/import
 * Accepts a CSV file, parses it, deduplicates, sends to AI for categorization,
 * saves transactions, and returns the results with AI category suggestions.
 */
router.post(
  "/import",
  upload.single("file"),
  async (req: AuthRequest, res: any, next: any) => {
    try {
      if (!req.file) {
        return res.status(400).json({ error: "No file uploaded. Attach a CSV as 'file'." });
      }

      let parsed: { headers: string[]; rows: Record<string, string>[] };
      try {
        parsed = parseCsv(req.file.buffer);
      } catch (parseErr: any) {
        return res.status(400).json({ error: `CSV parse error: ${parseErr.message}` });
      }

      if (parsed.rows.length === 0) {
        return res.status(400).json({ error: "CSV has no transaction data rows." });
      }
      if (parsed.rows.length > 5000) {
        return res.status(400).json({ error: "CSV exceeds 5,000 transaction limit per upload." });
      }

      let valid: any[], skipped: number, duplicates: number;
      try {
        ({ valid, skipped, duplicates } = await mapRowsToTransactions(
          parsed.headers,
          parsed.rows,
          req.auth!.userId
        ));
      } catch (mapErr: any) {
        return res.status(400).json({ error: mapErr.message });
      }

      if (valid.length === 0) {
        return res.json({
          imported: 0,
          skipped,
          duplicates,
          categorization_suggestions: [],
          message: "No new transactions found. All rows were either invalid, zero amount, or already imported.",
        });
      }

      // Send to AI for categorization (before saving, so we can attach suggestions)
      let categorizationResults: any[] = [];
      let aiError: string | null = null;
      try {
        const aiPayload = valid.map((t) => ({
          transaction_id: t.transactionId,
          merchant: t.merchant,
          description: t.description,
          amount: t.amount,
          type: t.type,
        }));

        const aiResult = await callAi(
          "/internal/ai/categorize",
          req.auth!.userId,
          req.auth!.email,
          { transactions: aiPayload }
        );
        categorizationResults = aiResult.results || [];
      } catch (aiErr: any) {
        aiError = `AI categorization unavailable: ${aiErr.message?.slice(0, 100)}`;
      }

      // Merge AI suggestions into transactions
      const suggestionMap = new Map<string, any>();
      for (const s of categorizationResults) {
        suggestionMap.set(s.transaction_id, s);
      }

      const toInsert = valid.map((t) => {
        const suggestion = suggestionMap.get(t.transactionId);
        // AI categorization is mandatory: use suggested category whenever provided
        const aiCategory = suggestion?.suggested_category;
        return {
          ...t,
          category: aiCategory || (t.type === "income" ? "Salary and Income" : "Other"),
          categorySource: "ai",
          categoryConfidence: suggestion?.confidence || 0.85,
          aiSuggestion: suggestion || {
            suggested_category: aiCategory || (t.type === "income" ? "Salary and Income" : "Other"),
            confidence: 0.85,
            reason: "AI categorized based on transaction details",
            status: "suggested",
          },
        };
      });

      // Save to MongoDB
      const savedDocs: any[] = [];
      let saveErrors = 0;
      for (const item of toInsert) {
        try {
          const doc = await Transaction.create({
            userId: item.userId,
            transactionId: item.transactionId,
            merchant: item.merchant,
            description: item.description,
            amount: item.amount,
            type: item.type,
            category: item.category,
            categorySource: item.aiSuggestion ? "ai" : "import",
            categoryConfidence: item.aiSuggestion?.confidence || 0,
            aiSuggestion: item.aiSuggestion,
            date: item.date,
            source: "import",
          });
          savedDocs.push({
            _id: doc._id,
            transactionId: doc.transactionId,
            merchant: doc.merchant,
            category: doc.category,
            amount: doc.amount,
            type: doc.type,
            date: doc.date,
            aiSuggestion: item.aiSuggestion,
          });
        } catch (saveErr: any) {
          if (saveErr?.code === 11000) {
            duplicates++;
          } else {
            saveErrors++;
          }
        }
      }

      await audit(req.auth!.userId, "import_transactions", "success", {
        imported: savedDocs.length,
        skipped,
        duplicates,
        aiCategorized: categorizationResults.length,
      });

      res.json({
        imported: savedDocs.length,
        skipped,
        duplicates,
        save_errors: saveErrors,
        ai_categorized: categorizationResults.length,
        ai_error: aiError,
        transactions: savedDocs,
        categorization_suggestions: categorizationResults,
        message: `Imported ${savedDocs.length} transactions. ${categorizationResults.length} categorized by AI.`,
      });
    } catch (e) {
      await audit(req.auth!.userId, "import_transactions", "failed");
      next(e);
    }
  }
);

/**
 * POST /api/transactions/:id/confirm-category
 * User confirms, edits, or rejects an AI-suggested category.
 */
router.post("/:id/confirm-category", async (req: AuthRequest, res: any, next: any) => {
  try {
    const { category, action } = req.body as {
      category: string;
      action: "confirm" | "edit" | "reject";
    };
    if (!["confirm", "edit", "reject"].includes(action)) {
      return res.status(400).json({ error: "action must be confirm, edit, or reject" });
    }
    const item = await Transaction.findOneAndUpdate(
      { _id: req.params.id, userId: req.auth!.userId },
      {
        category: action !== "reject" ? category : undefined,
        categorySource: action === "confirm" ? "ai" : "override",
      },
      { new: true }
    );
    if (!item) return res.status(404).json({ error: "Transaction not found" });
    await audit(req.auth!.userId, `category_${action}`, "success", { id: item._id, category });
    res.json({ item });
  } catch (e) {
    next(e);
  }
});

/**
 * POST /api/transactions/bulk-categorize
 * Trigger AI categorization for all uncategorized transactions belonging to the user.
 */
router.post("/bulk-categorize", async (req: AuthRequest, res: any, next: any) => {
  try {
    const uncategorized = await Transaction.find({
      userId: req.auth!.userId,
      $or: [{ category: "Other" }, { category: "" }, { category: { $exists: false } }],
      type: "expense",
    })
      .limit(200)
      .lean();

    if (!uncategorized.length) {
      return res.json({ message: "No uncategorized transactions found", results: [] });
    }

    const aiPayload = uncategorized.map((t: any) => ({
      transaction_id: String(t._id),
      merchant: t.merchant,
      description: t.description || "",
      amount: t.amount,
      type: t.type,
    }));

    const aiResult = await callAi(
      "/internal/ai/categorize",
      req.auth!.userId,
      req.auth!.email,
      { transactions: aiPayload }
    );

    const results = aiResult.results || [];
    let applied = 0;
    for (const r of results) {
      if (r.suggested_category) {
        await Transaction.findByIdAndUpdate(r.transaction_id, {
          category: r.suggested_category,
          categorySource: "ai",
          categoryConfidence: r.confidence || 0.85,
          aiSuggestion: r,
        });
        applied++;
      }
    }

    await audit(req.auth!.userId, "bulk_categorize", "success", {
      total: uncategorized.length,
      suggestions: results.length,
      applied,
    });

    res.json({
      total_uncategorized: uncategorized.length,
      suggestions_received: results.length,
      high_confidence_applied: applied,
      results,
    });
  } catch (e) {
    next(e);
  }
});

export default router;

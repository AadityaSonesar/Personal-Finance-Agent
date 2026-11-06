import { Router } from "express";
import {
  AgentWorkflow,
  Budget,
  Transaction,
} from "../models/index.js";
import { AuthRequest, requireAuth } from "../middleware/auth.js";
import { callAi } from "../services/ai.js";
import { audit } from "../utils/audit.js";

const router = Router();
router.use(requireAuth);

/** Retrieve all user transactions and budgets for the AI service. */
async function getAiPayload(req: AuthRequest) {
  const [transactions, budgets] = await Promise.all([
    Transaction.find({ userId: req.auth!.userId }).sort({ date: -1 }).lean(),
    Budget.find({ userId: req.auth!.userId }).lean(),
  ]);
  return { transactions, budgets };
}

/** Run an AI endpoint and persist the workflow result. */
async function runAi(
  req: AuthRequest,
  res: any,
  next: any,
  path: string,
  extraBody: Record<string, unknown> = {},
) {
  try {
    const payload = await getAiPayload(req);
    const result = await callAi(path, req.auth!.userId, req.auth!.email, {
      ...payload,
      ...extraBody,
    });
    if (result.workflow_id) {
      await AgentWorkflow.findOneAndUpdate(
        { userId: req.auth!.userId, workflowId: result.workflow_id },
        { workflowStatus: result.status || "completed", result },
        { upsert: true, new: true },
      );
    }
    await audit(req.auth!.userId, path, "success", undefined, result.workflow_id, "ai-service");
    res.json(result);
  } catch (e) {
    await audit(req.auth!.userId, path, "failed");
    next(e);
  }
}

// -- Analysis endpoints -------------------------------------------------------

/** Full financial analysis (all deterministic + LLM interpretation) */
router.post("/analyze", (req: AuthRequest, res, next) =>
  runAi(req, res, next, "/internal/ai/analyze"),
);

/** Cash-flow forecast */
router.post("/forecast", (req: AuthRequest, res, next) =>
  runAi(req, res, next, "/internal/ai/forecast"),
);

/** Full report generation */
router.post("/generate-report", (req: AuthRequest, res, next) =>
  runAi(req, res, next, "/internal/ai/report"),
);

/** AI financial advisor chat */
router.post("/chat", async (req: AuthRequest, res, next) => {
  const { prompt = "" } = req.body as { prompt?: string };
  await runAi(req, res, next, "/internal/ai/chat", { prompt });
});

/** AI Budget recommendations */
router.post("/budget-recommendations", async (req: AuthRequest, res, next) => {
  try {
    const payload = await getAiPayload(req);
    const result = await callAi(
      "/internal/ai/budget-recommendations",
      req.auth!.userId,
      req.auth!.email,
      payload,
    );
    await audit(req.auth!.userId, "budget_recommendations", "success");
    res.json(result);
  } catch (e) {
    await audit(req.auth!.userId, "budget_recommendations", "failed");
    next(e);
  }
});

/** LLM provider status */
router.get("/status", async (req: AuthRequest, res, next) => {
  try {
    const result = await callAi(
      "/internal/ai/status",
      req.auth!.userId,
      req.auth!.email,
      {},
    );
    res.json(result);
  } catch (e) {
    res.json({ available: false, provider: "groq", error: "AI service unavailable" });
  }
});

// -- Workflow history ----------------------------------------------------------

router.get("/workflows/:id", async (req: AuthRequest, res, next) => {
  try {
    const record = await AgentWorkflow.findOne({
      userId: req.auth!.userId,
      workflowId: req.params.id,
    });
    if (!record) return res.status(404).json({ error: "Workflow not found" });
    res.json({ workflow: record });
  } catch (e) {
    next(e);
  }
});

router.get("/workflows", async (req: AuthRequest, res, next) => {
  try {
    const records = await AgentWorkflow.find({ userId: req.auth!.userId })
      .sort({ createdAt: -1 })
      .limit(20)
      .lean();
    res.json({ workflows: records });
  } catch (e) {
    next(e);
  }
});

export default router;

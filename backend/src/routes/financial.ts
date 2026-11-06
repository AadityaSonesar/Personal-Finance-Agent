import { Router } from "express"; import { randomUUID } from "node:crypto"; import { Budget,Goal,Transaction,User } from "../models/index.js"; import { AuthRequest,requireAuth } from "../middleware/auth.js"; import { validate } from "../middleware/validate.js"; import { budget,goal,transaction } from "../validators/schemas.js"; import { audit } from "../utils/audit.js"; import { callAi } from "../services/ai.js";
const router=Router(); router.use(requireAuth); const owner=(req:AuthRequest)=>({userId:req.auth!.userId});

// Initial balance endpoints
router.get("/user/initial-balance", async (req: AuthRequest, res, next) => {
  try {
    const u = await User.findOne({ firebaseUid: req.auth!.userId });
    res.json({ initialBalance: u?.initialBalance || 0 });
  } catch (e) {
    next(e);
  }
});

router.post("/user/initial-balance", async (req: AuthRequest, res, next) => {
  try {
    const { initialBalance = 0 } = req.body as { initialBalance?: number };
    const u = await User.findOneAndUpdate(
      { firebaseUid: req.auth!.userId },
      { initialBalance: Number(initialBalance) || 0 },
      { new: true, upsert: true }
    );
    await audit(req.auth!.userId, "update_initial_balance", "success", { initialBalance });
    res.json({ initialBalance: u.initialBalance });
  } catch (e) {
    next(e);
  }
});
function crud(path:string,Model:any,schema:any){router.get(path,async(req:AuthRequest,res,next)=>{try{res.json({items:await Model.find(owner(req)).sort({createdAt:-1})});}catch(e){next(e)}});router.post(path,validate(schema),async(req:AuthRequest,res,next)=>{try{const item=await Model.create({...owner(req), ...(req as any).validated.body});await audit(req.auth!.userId,`create_${path.slice(1)}`,"success",{id:item.id});res.status(201).json({item});}catch(e){next(e)}});router.put(`${path}/:id`,validate(schema),async(req:AuthRequest,res,next)=>{try{const item=await Model.findOneAndUpdate({_id:req.params.id,...owner(req)},(req as any).validated.body,{new:true,runValidators:true});if(!item)return res.status(404).json({error:"Record not found"});await audit(req.auth!.userId,`edit_${path.slice(1)}`,"success",{id:item.id});res.json({item});}catch(e){next(e)}});router.delete(`${path}/:id`,async(req:AuthRequest,res,next)=>{try{const item=await Model.findOneAndDelete({_id:req.params.id,...owner(req)});if(!item)return res.status(404).json({error:"Record not found"});await audit(req.auth!.userId,`delete_${path.slice(1)}`,"success",{id:item.id});res.status(204).end();}catch(e){next(e)}})}
router.get("/transactions",async(req:AuthRequest,res,next)=>{try{const page=Math.max(1,Number(req.query.page)||1),limit=Math.min(100,Math.max(1,Number(req.query.limit)||25));const search=String(req.query.search||"").trim();const category=String(req.query.category||"").trim();const type=String(req.query.type||"").trim();const filter:any={...owner(req)};if(category)filter.category=category;if(type)filter.type=type;if(search)filter.$or=[{merchant:{$regex:search,$options:"i"}},{description:{$regex:search,$options:"i"}},{category:{$regex:search,$options:"i"}}];const [items,total]=await Promise.all([Transaction.find(filter).sort({date:-1}).skip((page-1)*limit).limit(limit),Transaction.countDocuments(filter)]);res.json({items,page,limit,total});}catch(e){next(e)}});
router.post("/transactions",validate(transaction),async(req:AuthRequest,res,next)=>{
  try{
    const input=(req as any).validated.body;
    let aiCat = input.category || "Other";
    let aiSuggestion: any = null;
    let confidence = 0.85;

    // Categorize transaction using AI service
    try {
      const aiResult = await callAi(
        "/internal/ai/categorize",
        req.auth!.userId,
        req.auth!.email,
        {
          transactions: [{
            transaction_id: "temp",
            merchant: input.merchant,
            description: input.description || "",
            amount: input.amount,
            type: input.type
          }]
        }
      );
      if (aiResult.results && aiResult.results.length > 0) {
        aiCat = aiResult.results[0].suggested_category;
        confidence = aiResult.results[0].confidence || 0.85;
        aiSuggestion = aiResult.results[0];
      }
    } catch {
      // Fallback AI default
      aiCat = input.type === "income" ? "Salary and Income" : (input.category || "Other");
    }

    const item=await Transaction.create({
      ...owner(req),
      ...input,
      category: aiCat,
      categorySource: "ai",
      categoryConfidence: confidence,
      aiSuggestion,
      transactionId:input.transactionId||randomUUID(),
      source:"manual"
    });
    await audit(req.auth!.userId,"create_transaction","success",{id:item.id});
    res.status(201).json({item});
  }catch(e:any){
    if(e?.code===11000)return res.status(409).json({error:"Duplicate transaction ID"});
    next(e);
  }
});router.put("/transactions/:id",validate(transaction),async(req:AuthRequest,res,next)=>{try{const item=await Transaction.findOneAndUpdate({_id:req.params.id,...owner(req)},(req as any).validated.body,{new:true,runValidators:true});if(!item)return res.status(404).json({error:"Transaction not found"});await audit(req.auth!.userId,"edit_transaction","success",{id:item.id});res.json({item});}catch(e){next(e)}});router.delete("/transactions/:id",async(req:AuthRequest,res,next)=>{try{const item=await Transaction.findOneAndDelete({_id:req.params.id,...owner(req)});if(!item)return res.status(404).json({error:"Transaction not found"});await audit(req.auth!.userId,"delete_transaction","success",{id:item.id});res.status(204).end();}catch(e){next(e)}});
// DELETE all transactions for the authenticated user
router.delete("/transactions", async (req: AuthRequest, res, next) => {
  try {
    const result = await Transaction.deleteMany(owner(req));
    await audit(req.auth!.userId, "delete_all_transactions", "success", { count: result.deletedCount });
    res.json({ deleted: result.deletedCount });
  } catch (e) { next(e); }
});

// Goal cashflow impact — calls AI advisor to explain how saving for this goal affects finances
router.post("/goals/:id/impact", async (req: AuthRequest, res, next) => {
  try {
    const goal = await Goal.findOne({ _id: req.params.id, ...owner(req) });
    if (!goal) return res.status(404).json({ error: "Goal not found" });

    // Fetch recent transactions for context (40 is optimal for token limit & coverage)
    const transactions = await Transaction.find(owner(req))
      .sort({ date: -1 })
      .limit(40)
      .lean();

    const remaining = Math.max(0, goal.targetAmount - goal.currentAmount);
    const daysToTarget = goal.targetDate
      ? Math.max(1, Math.ceil((new Date(goal.targetDate).getTime() - Date.now()) / 86_400_000))
      : 365;
    const monthsToTarget = Math.max(1, Math.round(daysToTarget / 30));
    const monthlySaving = remaining > 0 ? +(remaining / monthsToTarget).toFixed(2) : 0;

    const question = `I want to ${goal.name} which costs ₹${goal.targetAmount.toLocaleString("en-IN")}. ` +
      `I have already saved ₹${goal.currentAmount.toLocaleString("en-IN")} towards this goal. ` +
      `I need ₹${remaining.toLocaleString("en-IN")} more. ` +
      (goal.targetDate ? `My target date is ${new Date(goal.targetDate).toLocaleDateString("en-IN")}, giving me ${monthsToTarget} months. ` : "") +
      `To reach this goal on time I need to save approximately ₹${monthlySaving.toLocaleString("en-IN")} per month. ` +
      `Based on my actual transaction history: how will this affect my monthly cashflow? ` +
      `Can I afford it? What should I cut back on? Give a specific, actionable explanation.`;

    const txPayload = transactions.map((t: any) => ({
      transaction_id: String(t.transactionId || t._id),
      merchant: t.merchant,
      description: t.description || "",
      amount: t.amount,
      type: t.type,
      category: t.category || "Other",
      date: (t.date instanceof Date ? t.date : new Date(t.date)).toISOString().slice(0, 10),
    }));

    const aiRes = await callAi(
      "/internal/ai/chat",
      req.auth!.userId,
      req.auth!.email,
      { prompt: question, transactions: txPayload }
    );

    const advisorMessage = aiRes?.result?.advisor_message
      || aiRes?.result?.advisor_response?.direct_answer
      || "Unable to generate cashflow impact analysis at this time.";

    const spending = aiRes?.result?.spending;

    res.json({
      goalId: goal._id,
      goalName: goal.name,
      targetAmount: goal.targetAmount,
      currentAmount: goal.currentAmount,
      remaining,
      monthsToTarget,
      monthlySavingRequired: monthlySaving,
      impact: {
        advisor_message: advisorMessage,
        monthly_income: spending?.month_income ?? null,
        monthly_expense: spending?.month_expense ?? null,
        net_monthly: spending ? (spending.month_income - spending.month_expense) : null,
        savings_rate: spending?.savings_rate ?? null,
        can_afford_monthly_saving: spending
          ? (spending.month_income - spending.month_expense) >= monthlySaving
          : null,
      },
    });
  } catch (e) { next(e); }
});

// Upsert budget by (userId, category, month)
router.post("/budgets/upsert", validate(budget), async (req: AuthRequest, res, next) => {
  try {
    const { category, monthlyLimit, month } = (req as any).validated.body;
    const item = await Budget.findOneAndUpdate(
      { ...owner(req), category, month },
      { monthlyLimit },
      { new: true, upsert: true }
    );
    await audit(req.auth!.userId, "upsert_budget", "success", { id: item.id, category, monthlyLimit });
    res.json({ item });
  } catch (e) { next(e); }
});

crud("/budgets",Budget,budget);crud("/goals",Goal,goal); export default router;

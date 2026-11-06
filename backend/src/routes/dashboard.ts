import { Router } from "express"; import { Budget,HealthHistory,Transaction,User } from "../models/index.js"; import { AuthRequest,requireAuth } from "../middleware/auth.js";
const router=Router();router.use(requireAuth); const owner=(r:AuthRequest)=>({userId:r.auth!.userId});
router.get("/summary",async(req:AuthRequest,res,next)=>{try{
  const [tx, u, health] = await Promise.all([
    Transaction.find(owner(req)),
    User.findOne({ firebaseUid: req.auth!.userId }),
    HealthHistory.findOne(owner(req)).sort({calculationDate:-1})
  ]);
  const initial = u?.initialBalance || 0;
  const income=tx.filter(x=>x.type==="income").reduce((n,x)=>n+x.amount,0);
  const expenses=tx.filter(x=>x.type==="expense").reduce((n,x)=>n+x.amount,0);
  res.json({
    calculated:{
      initialBalance: initial,
      totalIncome: income,
      totalExpenses: expenses,
      savings: income - expenses,
      estimatedBalance: initial + income - expenses
    },
    health: health || null,
    recentTransactions: tx.sort((a,b)=>+b.date-(+a.date)).slice(0,5)
  });
}catch(e){next(e)}});
router.get("/cashflow",async(req:AuthRequest,res,next)=>{try{const tx=await Transaction.find(owner(req)).sort({date:1});const monthly=new Map<string,{month:string;income:number;expense:number}>();for(const t of tx){const month=t.date.toISOString().slice(0,7),row=monthly.get(month)||{month,income:0,expense:0};row[t.type]+=t.amount;monthly.set(month,row)}res.json({actual:Array.from(monthly.values())});}catch(e){next(e)}});
router.get("/spending",async(req:AuthRequest,res,next)=>{try{const rows=await Transaction.aggregate([{$match:{userId:req.auth!.userId,type:"expense"}},{$group:{_id:"$category",amount:{$sum:"$amount"}}},{$sort:{amount:-1}}]);res.json({categories:rows.map(x=>({category:x._id,amount:x.amount}))});}catch(e){next(e)}});
router.get("/financial-health",async(req:AuthRequest,res,next)=>{try{res.json({history:await HealthHistory.find(owner(req)).sort({calculationDate:1})});}catch(e){next(e)}}); export default router;

import express from "express";import helmet from "helmet";import cors from "cors";import cookieParser from "cookie-parser";import rateLimit from "express-rate-limit";import auth from "./routes/auth.js";import financial from "./routes/financial.js";import dashboard from "./routes/dashboard.js";import ai from "./routes/ai.js";import importRoutes from "./routes/import.js";import whatsapp from "./routes/whatsapp.js";import { env } from "./config/env.js";
export const app=express();app.disable("x-powered-by");app.use(helmet());const allowedOrigins = new Set([env.FRONTEND_URL, "http://localhost:5173", "http://127.0.0.1:5173"]);
const loopbackOrigin = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]):\d+$/;const isAllowedOrigin = (origin: string) => allowedOrigins.has(origin) || (env.NODE_ENV !== "production" && loopbackOrigin.test(origin));
app.use(cors({ origin: (origin, callback) => callback(null, !origin || isAllowedOrigin(origin)), credentials: true }));app.use(rateLimit({windowMs:15*60*1000,max:300,standardHeaders:true,legacyHeaders:false}));app.use(express.json({limit:"10mb"}));app.use(cookieParser());app.get("/health",(_,res)=>res.json({status:"ok"}));app.use("/api/auth",auth);app.use("/api/whatsapp",whatsapp);app.use("/api/transactions",importRoutes);app.use("/api/dashboard",dashboard);app.use("/api/ai",ai);app.use("/api",financial);app.use((err:any,_req:any,res:any,_next:any)=>{
  console.error(err?.message||"Unhandled request error");
  if (err?.message?.includes("buffering timed out") || err?.name === "MongooseServerSelectionError") {
    return res.status(503).json({ error: "Database connection unavailable. Please ensure your current IP address is whitelisted in MongoDB Atlas Network Access." });
  }
  res.status(err?.status||502).json({error: err?.message || "Request could not be completed"});
});

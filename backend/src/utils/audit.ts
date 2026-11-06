import { AuditLog } from "../models/index.js";
export async function audit(userId:string,action:string,status:string, metadata?:unknown, workflowId?:string, agentOrTool="api"){ await AuditLog.create({userId,action,status,metadata,workflowId,agentOrTool}); }

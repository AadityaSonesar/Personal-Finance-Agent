import type { RequestHandler } from "express"; import { ZodType } from "zod";
export const validate=(schema:ZodType):RequestHandler=>(req,res,next)=>{const result=schema.safeParse({body:req.body,params:req.params,query:req.query}); if(!result.success)return res.status(400).json({error:"Invalid request",details:result.error.flatten()}); (req as any).validated=result.data; next();};

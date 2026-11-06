import { z } from "zod";
export const authRegister=z.object({body:z.object({name:z.string().trim().min(2).max(120),email:z.string().email(),password:z.string().min(12).max(128)})});
export const authLogin=z.object({body:z.object({email:z.string().email(),password:z.string().min(1).max(128)})});
export const transaction=z.object({body:z.object({transactionId:z.string().trim().min(1).max(100).optional(),description:z.string().max(1000).default(""),merchant:z.string().trim().min(1).max(200),amount:z.coerce.number().positive(),type:z.enum(["income","expense"]),category:z.string().trim().max(80).default("Other"),date:z.coerce.date(),paymentMethod:z.string().max(80).default("")})});
export const budget=z.object({body:z.object({category:z.string().trim().min(1).max(80),monthlyLimit:z.coerce.number().min(0),month:z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/)})});
export const goal=z.object({body:z.object({name:z.string().trim().min(1).max(160),description:z.string().max(500).default(""),targetAmount:z.coerce.number().positive(),currentAmount:z.coerce.number().min(0).default(0),targetDate:z.coerce.date().optional(),status:z.enum(["active","completed","paused"]).default("active")})});

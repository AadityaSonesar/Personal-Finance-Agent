import mongoose from "mongoose";import {app} from "./app.js";import {env} from "./config/env.js";
async function main(){await mongoose.connect(env.MONGODB_URI);app.listen(env.PORT,()=>console.log(`API listening on ${env.PORT}`));}main().catch(e=>{console.error("Startup failed",e.message);process.exit(1)});

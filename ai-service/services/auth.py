import os,jwt
from fastapi import Header,HTTPException
async def verified_context(authorization:str=Header(default="")) -> dict:
    if not authorization.startswith("Bearer "): raise HTTPException(401,"Internal authentication required")
    try:return jwt.decode(authorization[7:],os.environ["INTERNAL_SERVICE_SECRET"],algorithms=["HS256"],issuer="aurelia-api",audience="aurelia-ai")
    except (jwt.PyJWTError,KeyError):raise HTTPException(401,"Invalid internal authentication")

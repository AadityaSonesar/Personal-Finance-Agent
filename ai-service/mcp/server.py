"""Standalone FastMCP v2 server. It exposes only deterministic, validated functions."""
from fastmcp import FastMCP
from core.anomalies import detect_anomalies
from core.subscriptions import detect_subscriptions
from core.health import calculate_health
from core.cashflow import forecast_cashflow
from core.config import get_config
from core.models import Transaction
mcp=FastMCP("aurelia-financial-tools")
def rows(records:list[dict])->list[Transaction]: return [Transaction.model_validate(r) for r in records]
@mcp.tool
def detect_anomalies(records:list[dict])->list[dict]: """Find unusual transaction patterns from records already scoped by the application."""; return [x.model_dump(mode="json") for x in detect_anomalies(rows(records),get_config())]
@mcp.tool
def detect_subscriptions(records:list[dict])->list[dict]: """Find recurring payments from authorized transaction records."""; return [x.model_dump(mode="json") for x in detect_subscriptions(rows(records),get_config())]
@mcp.tool
def calculate_financial_health(records:list[dict],budgets:dict[str,float],emergency_fund:float|None=None)->dict: """Compute transparent weighted health from authorized data."""; return calculate_health(rows(records),budgets,emergency_fund,get_config()).model_dump(mode="json")
@mcp.tool
def forecast_cashflow(records:list[dict])->dict: """Forecast cash flow from supplied authorized records; this is not a bank balance."""; return forecast_cashflow(rows(records),None,None,get_config()).model_dump(mode="json")
if __name__=="__main__":mcp.run(transport="streamable-http",host="127.0.0.1",port=8001)

import re, json, time, os, logging
from typing import Any

logger = logging.getLogger(__name__)

INDIAN_RULES = [
    # Subscriptions
    (r"\b(netflix|spotify|prime|hotstar|disney|youtube|apple|itunes|google play|playstore|icloud|patreon|medium|substack|audible|times prime|sony liv|zee5)\b", "Subscriptions", 0.95),

    # Transportation
    (r"\b(bharatpet|hpcl|iocl|bpcl|shell|petrol|diesel|fuel|cng|gas station|uber|ola|rapido|auto|cab|taxi|irctc|railway|train|flight|indigo|air india|akasa|spicejet|redbus|abhibus|metro|fastag|toll|parking|garage|service center|fix my c)\b", "Transportation", 0.95),

    # Utilities
    (r"\b(upilite|upi lite|wallet|recharge|airtel|jio|vodafone|idea|vi\b|bsnl|broadband|wifi|act fibernet|tata play|dish tv|dth|electricity|mseb|adani electricity|tata power|bescom|uppcl|billdesk|bbps|water bill|gas bill|mahanagar gas|igl|hp gas|indane|bharat gas)\b", "Utilities", 0.95),

    # Food and Dining
    (r"\b(mcdonalds?|swiggy|zomato|kfc|dominos?|pizza|burger|subway|starbucks|cafe|restaurant|hotel|dhaba|bhojanalay|padmalay|gangotri|trofi|magicpin|bakery|cake|sweets?|mithai|chai|tea|coffee|canteen|food|dining|snack|bites|rolls?|kitchen|rasoi|treats?|darshini|tiffin|chips|dinner|lunch|breakfast)\b", "Food and Dining", 0.95),
    (r"\b(zepto|blinkit|bigbasket|instamart|dmart|grocery|groceries|supermarket|mart|vegetable|fruits?|dairy|milk|amul|mother dairy|ration|rasha|kirana)\b", "Food and Dining", 0.95),
    
    # Healthcare
    (r"\b(medi|pharma|pharmacy|chemist|hira medi|apollo|netmeds|1mg|pharmeasy|hospital|clinic|doctor|dr\.\s+|nursing|dental|dentist|pathology|diagnostic|lab|optical|eye|ayurved|homeopath)\b", "Healthcare", 0.95),
    
    # Shopping
    (r"\b(ekart|amazon|flipkart|myntra|ajio|meesho|nykaa|tata cliq|zara|h&m|pantaloons|max|westside|trends|lifestyle|decathlon|croma|reliance digital|vijay sales|retail|clothing|apparel|footwear|shoes|fashion|mall|jewell?er)\b", "Shopping", 0.95),
    
    # Entertainment
    (r"\b(bookmyshow|pvr|inox|cinepolis|cinema|movie|theatre|theater|gaming|steam|playstation|xbox|amusement|wonderla|club|resort)\b", "Entertainment", 0.95),
    
    # Education
    (r"\b(school|college|university|academy|classes|tuition|coaching|allen|fiitjee|byjus|unacademy|coursera|udemy|edx|books?|stationery|xerox|print)\b", "Education", 0.95),
    
    # Investments
    (r"\b(zerodha|groww|upstox|angel one|angelone|indmoney|coin|kite|mutual fund|sip|stocks?|shares?|sebi|bse|nse|brokerage|crypto|wazirx|coindcx)\b", "Investments", 0.95),
    
    # Rent and Housing
    (r"\b(nobroker|nestaway|magicbricks|housing\.com|landlord|flat rent|room rent|maintenance|society maint|society|deposit)\b", "Rent and Housing", 0.95),
    
    # Income
    (r"\b(salary|payroll|stipend|bonus|pension|dividend|interest cr|refund|cashback|subsidy)\b", "Income", 0.95),
]

def clean_merchant_text(merchant: str, description: str) -> str:
    raw = f"{merchant} {description}".strip()
    cleaned = re.sub(r"^UPI/(DR|CR)/\d+/", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bUPI/(DR|CR)/\b", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def rule_classify_single(merchant: str, description: str, amount: float, tx_type: str) -> dict:
    cleaned = clean_merchant_text(merchant, description)
    text = cleaned.lower()
    
    if tx_type == "income":
        return {
            "suggested_category": "Income",
            "confidence": 0.95,
            "reason": "Credit / Income transaction",
            "evidence": [cleaned[:80]],
        }
    
    for pattern, category, conf in INDIAN_RULES:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return {
                "suggested_category": category,
                "confidence": conf,
                "reason": f"Identified '{m.group(0)}' in transaction details as {category}.",
                "evidence": [m.group(0)],
            }
            
    # Personal P2P UPI logic
    if "upi" in merchant.lower() or "paytm" in merchant.lower() or "gpay" in merchant.lower():
        if amount >= 10000:
            return {
                "suggested_category": "Rent and Housing",
                "confidence": 0.80,
                "reason": f"High-value UPI transfer (₹{amount}) typically represents rent, housing, or major personal commitments.",
                "evidence": [merchant[:60]],
            }
        elif amount <= 600:
            return {
                "suggested_category": "Food and Dining",
                "confidence": 0.70,
                "reason": f"Low-value UPI payment (₹{amount}) to local vendor or merchant.",
                "evidence": [merchant[:60]],
            }
        else:
            return {
                "suggested_category": "Shopping",
                "confidence": 0.65,
                "reason": f"Peer/merchant UPI payment (₹{amount}).",
                "evidence": [merchant[:60]],
            }
            
    return {
        "suggested_category": "Other",
        "confidence": 0.50,
        "reason": "Unrecognized merchant transaction details.",
        "evidence": [cleaned[:60]],
    }

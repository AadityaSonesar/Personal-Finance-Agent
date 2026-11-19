import { Router, Request, Response } from "express";
import axios from "axios";
import { User, Transaction, Budget } from "../models/index.js";
import { callAi } from "../services/ai.js";
import { env } from "../config/env.js";

const router = Router();

// Cache recent message IDs to prevent Meta webhook retries from duplicate processing
const processedMessageIds = new Set<string>();
function markProcessed(id: string): boolean {
  if (processedMessageIds.has(id)) return false;
  processedMessageIds.add(id);
  if (processedMessageIds.size > 2000) {
    const first = processedMessageIds.values().next().value;
    if (first) processedMessageIds.delete(first);
  }
  return true;
}

/**
 * Send interactive message to WhatsApp via Meta Cloud API.
 */
export async function sendWhatsAppText(to: string, message: string): Promise<boolean> {
  const cleanPhone = to.replace(/[^0-9]/g, "");
  if (!env.WHATSAPP_PHONE_NUMBER_ID || !env.WHATSAPP_ACCESS_TOKEN) {
    console.log(`[WhatsApp Chat] Credentials not set. Simulated response to ${cleanPhone}:`);
    console.log(message);
    return true;
  }

  try {
    const url = `${env.WHATSAPP_API_URL}/${env.WHATSAPP_PHONE_NUMBER_ID}/messages`;
    await axios.post(
      url,
      {
        messaging_product: "whatsapp",
        recipient_type: "individual",
        to: cleanPhone,
        type: "text",
        text: { preview_url: false, body: message },
      },
      {
        headers: {
          Authorization: `Bearer ${env.WHATSAPP_ACCESS_TOKEN}`,
          "Content-Type": "application/json",
        },
        timeout: 15000,
      }
    );
    console.log(`[WhatsApp Chat] Sent interactive reply to ${cleanPhone}`);
    return true;
  } catch (error: any) {
    console.error("[WhatsApp Chat] Failed to send message:", error?.response?.data || error.message);
    return false;
  }
}

/**
 * Core interactive chat handler: answers questions grounded in user's financial transactions.
 */
export async function processInteractiveChatMessage(
  fromPhone: string,
  userMessage: string
): Promise<string> {
  // Normalize phone number (digits only)
  const normalizedPhone = fromPhone.replace(/[^0-9]/g, "");

  // Find user by saved whatsappPhone in preferences, or fall back to default user
  let user = await User.findOne({
    $or: [
      { "preferences.whatsappPhone": normalizedPhone },
      { "preferences.phone": normalizedPhone },
    ],
  });

  if (!user) {
    // Single-user / local default fallback so interactive chat works out of the box
    user = await User.findOne({ accountStatus: "active" }) || await User.findOne();
  }

  if (!user) {
    return "Welcome to Aurelia Financial Assistant! No account was found linked to your number. Please register on Aurelia first.";
  }

  // Load recent transactions and budgets for financial context
  const [transactions, budgets] = await Promise.all([
    Transaction.find({ userId: user.firebaseUid }).sort({ date: -1 }).limit(50).lean(),
    Budget.find({ userId: user.firebaseUid }).lean(),
  ]);

  try {
    const aiResult = await callAi("/internal/ai/chat", user.firebaseUid, user.email, {
      prompt: userMessage,
      transactions: transactions.map((t) => ({
        id: t.transactionId,
        date: t.date,
        merchant: t.merchant,
        amount: t.amount,
        type: t.type,
        category: t.category,
      })),
      budgets: budgets.map((b) => ({
        category: b.category,
        monthlyLimit: b.monthlyLimit,
        amountSpent: b.amountSpent,
      })),
    });

    const advisorResp = aiResult?.result?.advisor_response;
    let reply =
      advisorResp?.direct_answer ||
      aiResult?.result?.advisor_message ||
      "";

    if (!reply) {
      reply =
        "I reviewed your transactions, but couldn't generate a clear answer. You can ask me things like:\n" +
        "• *How much did I spend this month?*\n" +
        "• *What are my biggest expenses?*\n" +
        "• *How much did I spend on Food and Dining?*";
    }

    return reply;
  } catch (err: any) {
    console.error("[WhatsApp Chat] AI advisor error:", err?.message || err);
    return "Sorry, I had trouble analyzing your finances right now. Please try again in a moment.";
  }
}

/**
 * Meta Webhook verification handshake (GET /api/whatsapp/webhook)
 */
router.get("/webhook", (req: Request, res: Response) => {
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === env.WHATSAPP_VERIFY_TOKEN) {
    console.log("[WhatsApp] Webhook verified successfully.");
    return res.status(200).send(challenge);
  }

  return res.sendStatus(403);
});

/**
 * Inbound WhatsApp messages webhook (POST /api/whatsapp/webhook)
 */
router.post("/webhook", async (req: Request, res: Response) => {
  // Return 200 OK immediately to acknowledge receipt and avoid Meta retries
  res.status(200).json({ status: "ok" });

  try {
    const body = req.body;
    if (body.object !== "whatsapp_business_account") return;

    const entry = body.entry?.[0];
    const changes = entry?.changes?.[0];
    const value = changes?.value;
    const messages = value?.messages;

    if (!messages || !Array.isArray(messages) || messages.length === 0) {
      return;
    }

    const message = messages[0];
    const messageId = message.id;
    if (messageId && !markProcessed(messageId)) {
      return; // Already processed
    }

    // Interactive text chat only
    if (message.type !== "text" || !message.text?.body) {
      if (message.from) {
        await sendWhatsAppText(
          message.from,
          "I can currently assist with text questions about your finances, spending, and budgets. Feel free to type your question!"
        );
      }
      return;
    }

    const from = message.from;
    const userText = message.text.body.trim();
    console.log(`[WhatsApp Chat] Inbound message from ${from}: "${userText}"`);

    // Process chat response
    const reply = await processInteractiveChatMessage(from, userText);

    // Send reply back to WhatsApp user
    await sendWhatsAppText(from, reply);
  } catch (err: any) {
    console.error("[WhatsApp Chat] Inbound webhook error:", err?.message || err);
  }
});

/**
 * Direct testing endpoint for interactive chat simulation: POST /api/whatsapp/test-chat
 * Body: { "from": "919876543210", "message": "How much did I spend on Food?" }
 */
router.post("/test-chat", async (req: Request, res: Response) => {
  try {
    const { from = "919876543210", message = "What is my total spending?" } = req.body;
    const reply = await processInteractiveChatMessage(from, message);
    return res.json({
      success: true,
      from,
      user_message: message,
      reply,
    });
  } catch (err: any) {
    return res.status(500).json({ success: false, error: err?.message || "Chat test failed" });
  }
});

export default router;

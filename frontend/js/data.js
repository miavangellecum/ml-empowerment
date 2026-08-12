// Talks to the FastAPI backend (app.py). Falls back to mock data if the
// API isn't reachable yet, so the frontend is always demoable on its own.

const API_BASE = "http://localhost:8000";

const CATEGORY_META = {
  groceries: { icon: "🛒", tone: "tone-groceries", color: "var(--sage)" },
  dining:    { icon: "🍽️", tone: "tone-dining", color: "var(--red)" },
  delivery:  { icon: "📦", tone: "tone-delivery", color: "var(--mustard)" },
  household: { icon: "🏠", tone: "tone-household", color: "var(--teal)" },
  transport: { icon: "🚌", tone: "tone-transport", color: "var(--plum)" },
  other:     { icon: "✳️", tone: "tone-other", color: "#A9A296" },
};

// Placeholder data shaped like the FastAPI /receipts response, used only
// when the backend can't be reached.
const RECEIPTS = [
  { id: 1, store_name: "Albert Heijn", date: "2025-04-29", payment_method: "iDEAL", currency: "EUR", category: "groceries", subtotal: 50.10, tax: 4.20, total: 54.30,
    items: [
      { name: "Boodschappen, zie specificatie", quantity: 1, unit_price: 50.80, total_price: 50.80, category: "groceries" },
      { name: "Bezorgkosten", quantity: 1, unit_price: 5.70, total_price: 5.70, category: "delivery" },
    ] },
  { id: 2, store_name: "Uber Eats", date: "2025-05-02", payment_method: "Card", currency: "EUR", category: "dining", subtotal: 20.00, tax: 2.80, total: 22.80,
    items: [
      { name: "Ramen bowl", quantity: 1, unit_price: 14.50, total_price: 14.50, category: "dining" },
      { name: "Delivery fee", quantity: 1, unit_price: 5.50, total_price: 5.50, category: "delivery" },
      { name: "Tip", quantity: 1, unit_price: 2.80, total_price: 2.80, category: "dining" },
    ] },
  { id: 3, store_name: "IKEA", date: "2025-05-04", payment_method: "Card", currency: "EUR", category: "household", subtotal: 72.90, tax: 5.60, total: 78.50,
    items: [
      { name: "BILLY bookcase", quantity: 1, unit_price: 59.99, total_price: 59.99, category: "household" },
      { name: "Storage box, set of 3", quantity: 1, unit_price: 12.91, total_price: 12.91, category: "household" },
    ] },
  { id: 4, store_name: "NS Reizen", date: "2025-05-05", payment_method: "iDEAL", currency: "EUR", category: "transport", subtotal: 11.80, tax: 0.60, total: 12.40,
    items: [
      { name: "Single trip, 2nd class", quantity: 1, unit_price: 12.40, total_price: 12.40, category: "transport" },
    ] },
  { id: 5, store_name: "Bol.com", date: "2025-05-06", payment_method: "Card", currency: "EUR", category: "delivery", subtotal: 28.50, tax: 2.70, total: 31.20,
    items: [
      { name: "USB-C cable, 2m", quantity: 2, unit_price: 9.25, total_price: 18.50, category: "other" },
      { name: "Shipping", quantity: 1, unit_price: 10.00, total_price: 10.00, category: "delivery" },
    ] },
];

// Bank transactions pulled in via Plaid that have no matching scanned receipt yet.
const UNMATCHED_TRANSACTIONS = [
  { id: 101, store_name: "Shell Tankstation", date: "2025-05-07", category: "transport", total: 64.20, no_receipt: true },
  { id: 102, store_name: "Netflix", date: "2025-05-06", category: "other", total: 15.99, no_receipt: true },
];

async function fetchReceipts() {
  try {
    const res = await fetch(`${API_BASE}/receipts`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const rows = await res.json();
    return rows.map(r => ({
      id: r.id,
      store_name: r.store_name,
      date: r.date,
      payment_method: r.payment_method,
      currency: r.currency || "EUR",
      category: r.category || "other",
      subtotal: r.subtotal,
      tax: r.tax,
      total: r.total,
      items: r.items || [],
    }));
  } catch (err) {
    console.warn("Falling back to mock receipts — API not reachable:", err.message);
    return RECEIPTS;
  }
}

async function fetchUnmatchedTransactions() {
  try {
    const res = await fetch(`${API_BASE}/plaid/transactions`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    const transactions = await res.json();
    return transactions
      .filter(t => !t.matched_receipt_id)
      .map(t => ({
        id: `txn-${t.transaction_id || t.id}`,
        store_name: t.merchant_name || t.name || "Unknown",
        date: t.date,
        category: (t.category || "other").toLowerCase(),
        total: Math.abs(t.amount),
        no_receipt: true,
      }));
  } catch (err) {
    console.warn("Falling back to mock bank transactions — API not reachable:", err.message);
    return UNMATCHED_TRANSACTIONS;
  }
}

async function fetchReceiptById(id) {
  const all = await fetchReceipts();
  return all.find(r => String(r.id) === String(id));
}

async function fetchRecentActivity() {
  const [receiptsRaw, unmatched] = await Promise.all([
    fetchReceipts(),
    fetchUnmatchedTransactions(),
  ]);
  const receipts = receiptsRaw.map(r => ({ ...r, no_receipt: false }));
  const combined = [...receipts, ...unmatched];
  return combined.sort((a, b) => new Date(b.date) - new Date(a.date));
}

// ---------------------------------------------------------------------
// Plaid Link — shared connect-a-bank flow, used by index.html
// ---------------------------------------------------------------------
async function plaidGetLinkToken() {
  const res = await fetch(`${API_BASE}/plaid/create_link_token`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create link token (${res.status})`);
  const data = await res.json();
  return data.link_token;
}

async function plaidExchangePublicToken(publicToken) {
  const res = await fetch(`${API_BASE}/plaid/exchange_public_token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_token: publicToken }),
  });
  if (!res.ok) throw new Error(`Failed to exchange public token (${res.status})`);
  return res.json();
}

// Wires up a "Connect bank" button to Plaid Link. Call this once, passing
// the button's id, after the Plaid Link script and this file have loaded.
function wireConnectBank(buttonId, { onConnected } = {}) {
  const btn = document.getElementById(buttonId);
  if (!btn) {
    console.warn(`wireConnectBank: no element with id "${buttonId}"`);
    return;
  }

  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Preparing…";

    try {
      const linkToken = await plaidGetLinkToken();

      const handler = Plaid.create({
        token: linkToken,
        onSuccess: async (publicToken, metadata) => {
          btn.textContent = "Syncing…";
          try {
            const { item_id } = await plaidExchangePublicToken(publicToken);
            btn.textContent = `Connected ${metadata.institution?.name || "bank"}`;
            if (onConnected) await onConnected(item_id);
          } catch (err) {
            console.error(err);
            btn.disabled = false;
            btn.textContent = originalLabel;
            alert(`Error connecting bank: ${err.message}`);
          }
        },
        onExit: (err) => {
          btn.disabled = false;
          btn.textContent = originalLabel;
          if (err) {
            console.error(err);
            alert(`Link closed with error: ${err.error_message || err.error_code}`);
          }
        },
      });

      handler.open();
    } catch (err) {
      console.error(err);
      btn.disabled = false;
      btn.textContent = originalLabel;
      alert(`Error: ${err.message}`);
    }
  });
}
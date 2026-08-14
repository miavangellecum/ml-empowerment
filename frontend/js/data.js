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
        item_id: t.item_id || null,
      }));
  } catch (err) {
    console.warn("Falling back to mock bank transactions — API not reachable:", err.message);
    return UNMATCHED_TRANSACTIONS;
  }
}

async function fetchAccounts() {
  try {
    const res = await fetch(`${API_BASE}/plaid/accounts`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("Falling back to no accounts — API not reachable:", err.message);
    return [];
  }
}

async function fetchLinkedBanks() {
  try {
    const res = await fetch(`${API_BASE}/plaid/items`);
    if (!res.ok) throw new Error(`Server responded ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("Falling back to no linked banks — API not reachable:", err.message);
    return [];
  }
}

async function unlinkBank(itemId) {
  const res = await fetch(`${API_BASE}/plaid/unlink/${encodeURIComponent(itemId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to unlink (${res.status})`);
  return res.json();
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

async function plaidExchangePublicToken(publicToken, institutionName) {
  const res = await fetch(`${API_BASE}/plaid/exchange_public_token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_token: publicToken, institution_name: institutionName || null }),
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

// ---------------------------------------------------------------------
// Banks modal — shows every linked bank (with unlink) plus a button to
// connect another one. Any page just calls wireBanksButton('some-btn-id').
// ---------------------------------------------------------------------
function ensureBanksModal() {
  if (document.getElementById("banks-modal")) return;
  const modal = document.createElement("div");
  modal.id = "banks-modal";
  modal.className = "banks-modal";
  modal.innerHTML = `
    <div class="banks-modal-backdrop"></div>
    <div class="banks-modal-panel">
      <div class="banks-modal-head">
        <h3>Linked banks</h3>
        <button type="button" class="banks-modal-close" aria-label="Close">✕</button>
      </div>
      <div class="banks-modal-list" id="banksModalList"><div class="banks-modal-empty">Loading…</div></div>
      <button type="button" class="banks-modal-add" id="banksModalAdd">+ Connect another bank</button>
    </div>
  `;
  document.body.appendChild(modal);
  modal.querySelector(".banks-modal-backdrop").addEventListener("click", closeBanksModal);
  modal.querySelector(".banks-modal-close").addEventListener("click", closeBanksModal);
}

function closeBanksModal() {
  const modal = document.getElementById("banks-modal");
  if (modal) modal.classList.remove("open");
}

// Deterministic colored initials "logo" for a bank, since we don't have
// a real institution-logo API wired up — same name always gets the same
// color/initials so it still reads as an identity at a glance.
function bankAvatar(name) {
  const label = (name || 'Bank').trim();
  const initials = label
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase() || 'B';
  const palette = ['#E8492D', '#3E6E68', '#F0B93E', '#7A4B5C', '#7C9A79', '#5B7DB1'];
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  const color = palette[hash % palette.length];
  return `<div class="bank-avatar" style="background:${color}">${initials}</div>`;
}

async function renderBanksModalList() {
  const listEl = document.getElementById("banksModalList");
  listEl.innerHTML = `<div class="banks-modal-empty">Loading…</div>`;
  const [banks, accounts] = await Promise.all([fetchLinkedBanks(), fetchAccounts()]);
  if (!banks.length) {
    listEl.innerHTML = `<div class="banks-modal-empty">No banks connected yet.</div>`;
    return;
  }
  listEl.innerHTML = "";
  banks.forEach(bank => {
    const bankAccounts = accounts.filter(a => a.item_id === bank.item_id);
    const balanceTotal = bankAccounts.reduce((s, a) => s + (a.current_balance ?? a.available_balance ?? 0), 0);
    const mask = bankAccounts[0]?.mask ? `**** ${bankAccounts[0].mask}` : "";
    const balanceLabel = bankAccounts.length ? `€${balanceTotal.toFixed(2)}` : "Balance unavailable";
    const row = document.createElement("div");
    row.className = "banks-modal-row";
    row.innerHTML = `
      <div class="banks-modal-row-icon">${bankAvatar(bank.institution_name)}</div>
      <div class="banks-modal-row-meta">
        <div class="banks-modal-row-name">${bank.institution_name || "Connected bank"}</div>
        <div class="banks-modal-row-sub">${mask ? mask + " · " : ""}${balanceLabel}</div>
      </div>
      <button type="button" class="banks-modal-row-unlink" data-item-id="${bank.item_id}">Unlink</button>
    `;
    listEl.appendChild(row);
  });
  listEl.querySelectorAll(".banks-modal-row-unlink").forEach(btn => {
    btn.addEventListener("click", async () => {
      const itemId = btn.dataset.itemId;
      if (!confirm("Unlink this bank? Its transactions will stop syncing.")) return;
      btn.disabled = true;
      btn.textContent = "Unlinking…";
      try {
        await unlinkBank(itemId);
        await renderBanksModalList();
        if (window.__onBankChanged) window.__onBankChanged();
      } catch (err) {
        console.error(err);
        alert(`Error unlinking: ${err.message}`);
        btn.disabled = false;
        btn.textContent = "Unlink";
      }
    });
  });
}

async function openBanksModal() {
  ensureBanksModal();
  document.getElementById("banks-modal").classList.add("open");
  await renderBanksModalList();
}

// Wires a button to open the Banks modal (list + unlink + add-another-bank).
// Pass onBankChanged to refresh whatever balance/activity UI the page shows.
function wireBanksButton(buttonId, { onBankChanged } = {}) {
  const btn = document.getElementById(buttonId);
  if (!btn) {
    console.warn(`wireBanksButton: no element with id "${buttonId}"`);
    return;
  }
  if (onBankChanged) window.__onBankChanged = onBankChanged;

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    openBanksModal();
  });

  ensureBanksModal();
  document.getElementById("banksModalAdd").addEventListener("click", async () => {
    const addBtn = document.getElementById("banksModalAdd");
    const originalLabel = addBtn.textContent;
    addBtn.disabled = true;
    addBtn.textContent = "Preparing…";
    try {
      const linkToken = await plaidGetLinkToken();
      const handler = Plaid.create({
        token: linkToken,
        onSuccess: async (publicToken, metadata) => {
          addBtn.textContent = "Syncing…";
          try {
            await plaidExchangePublicToken(publicToken, metadata.institution?.name);
            await renderBanksModalList();
            if (window.__onBankChanged) window.__onBankChanged();
          } catch (err) {
            console.error(err);
            alert(`Error connecting bank: ${err.message}`);
          } finally {
            addBtn.disabled = false;
            addBtn.textContent = originalLabel;
          }
        },
        onExit: (err) => {
          addBtn.disabled = false;
          addBtn.textContent = originalLabel;
          if (err) {
            console.error(err);
            alert(`Link closed with error: ${err.error_message || err.error_code}`);
          }
        },
      });
      handler.open();
    } catch (err) {
      console.error(err);
      addBtn.disabled = false;
      addBtn.textContent = originalLabel;
      alert(`Error: ${err.message}`);
    }
  });
}
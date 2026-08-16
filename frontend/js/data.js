// Talks to the FastAPI backend (app.py). Falls back to mock data if the
// API isn't reachable yet, so the frontend is always demoable on its own.

// Use same-origin relative API base so frontend works when served by the backend
const API_BASE = "http://localhost:8000";

// Kept in sync with extraction/llm/extract.py's IRS_CATEGORIES list.
// No icons by design — color + label carry the category instead.
// Every category gets its own hue so the orbit bubbles are always visually
// distinguishable — no two categories (including the fallbacks) share a
// color. All hex values are hand-picked to sit in the same warm, muted,
// low-saturation family as the --sage/--red/--mustard/--teal/--plum theme
// variables, just extended out to 20 distinct swatches instead of 5.
const CATEGORY_META = {
  advertising:                      { icon: "", color: "var(--sage)" },     // #7C9A79
  car_and_truck_expenses:           { icon: "", color: "var(--red)" },      // #E8492D
  commissions_and_fees:             { icon: "", color: "var(--mustard)" },  // #F0B93E
  contract_labor:                   { icon: "", color: "var(--teal)" },     // #3E6E68
  insurance:                        { icon: "", color: "var(--plum)" },     // #7A4B5C
  interest:                         { icon: "", color: "#5B7DB1" },  // dusty blue
  legal_and_professional_services:  { icon: "", color: "#C9A876" },  // brass
  office_expense:                   { icon: "", color: "#6B8CAE" },  // slate blue
  rent_or_lease:                    { icon: "", color: "#B1785B" },  // terracotta
  repairs_and_maintenance:          { icon: "", color: "#8FA66B" },  // moss green
  supplies:                         { icon: "", color: "#D46A4C" },  // burnt coral
  taxes_and_licenses:               { icon: "", color: "#D9A441" },  // gold
  travel:                           { icon: "", color: "#3E9E8F" },  // bright teal
  meals:                            { icon: "", color: "#9A5A72" },  // wine
  utilities:                        { icon: "", color: "#A97C50" },  // bronze
  wages:                            { icon: "", color: "#B98B8B" },  // dusty rose
  other_expenses:                   { icon: "", color: "#8C6E4B" },  // umber
  personal_non_deductible:          { icon: "", color: "#6B5B95" },  // muted indigo
  // Fallbacks used elsewhere in this file when a category doesn't match
  // any of the above (e.g. an unmatched Plaid transaction's raw category).
  uncategorized:                    { icon: "", color: "#8C7A66" },  // warm taupe
  other:                            { icon: "", color: "#5E7A6B" },  // deep sage-grey
};

// Placeholder data shaped like the FastAPI /receipts response, used only
// when the backend can't be reached.
const RECEIPTS = [
  { id: 1, store_name: "Staples", date: "2026-07-02", payment_method: "card", currency: "USD", category: "office_expense", subtotal: 84.50, tax: 6.76, total: 91.26,
    items: [
      { name: "Printer paper (5 reams)", quantity: 5, unit_price: 8.50, total_price: 42.50, category: "office_expense" },
      { name: "Ink cartridges", quantity: 2, unit_price: 21.00, total_price: 42.00, category: "office_expense" },
    ] },
  { id: 2, store_name: "Delta Airlines", date: "2026-07-05", payment_method: "card", currency: "USD", category: "travel", subtotal: 412.00, tax: 0, total: 412.00,
    items: [
      { name: "Round-trip flight, client meeting", quantity: 1, unit_price: 412.00, total_price: 412.00, category: "travel" },
    ] },
  { id: 3, store_name: "The Grill House", date: "2026-07-06", payment_method: "card", currency: "USD", category: "meals", subtotal: 68.20, tax: 5.46, total: 78.66,
    items: [
      { name: "Client dinner", quantity: 1, unit_price: 68.20, total_price: 68.20, category: "meals" },
    ] },
  { id: 4, store_name: "Verizon Wireless", date: "2026-07-10", payment_method: "bank transfer", currency: "USD", category: "utilities", subtotal: 145.00, tax: 0, total: 145.00,
    items: [
      { name: "Business line, monthly", quantity: 1, unit_price: 145.00, total_price: 145.00, category: "utilities" },
    ] },
  { id: 5, store_name: "Whole Foods Market", date: "2026-07-11", payment_method: "cash", currency: "USD", category: "personal_non_deductible", subtotal: 52.10, tax: 0, total: 52.10,
    items: [
      { name: "Groceries", quantity: 1, unit_price: 52.10, total_price: 52.10, category: "personal_non_deductible" },
    ] },
];

// Bank transactions pulled in via Plaid that have no matching scanned receipt yet.
const UNMATCHED_TRANSACTIONS = [
  { id: 101, store_name: "AWS", date: "2026-07-08", category: "uncategorized", total: 63.40, no_receipt: true },
  { id: 102, store_name: "Adobe", date: "2026-07-09", category: "uncategorized", total: 54.99, no_receipt: true },
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
      currency: r.currency || "USD",
      category: r.category || "other_expenses",
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
        category: "uncategorized", // Plaid's own category taxonomy doesn't map to IRS categories — left uncategorized until reconciled
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
// Reports — deterministic summary numbers + the on-demand AI narrative.
// Used by reports.html.
// ---------------------------------------------------------------------

function _reportsQuery(startDate, endDate) {
  const params = new URLSearchParams();
  if (startDate) params.set('start_date', startDate);
  if (endDate) params.set('end_date', endDate);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function fetchExpenseSummary(startDate, endDate) {
  const res = await fetch(`${API_BASE}/reports/summary${_reportsQuery(startDate, endDate)}`);
  if (!res.ok) throw new Error(`Failed to load expense summary (${res.status})`);
  return res.json();
}

async function fetchExpenseLedger(startDate, endDate, category) {
  const params = new URLSearchParams();
  if (startDate) params.set('start_date', startDate);
  if (endDate) params.set('end_date', endDate);
  if (category) params.set('category', category);
  const qs = params.toString();
  const res = await fetch(`${API_BASE}/reports/ledger${qs ? '?' + qs : ''}`);
  if (!res.ok) throw new Error(`Failed to load ledger (${res.status})`);
  return res.json();
}

async function fetchAIAuditReport(startDate, endDate) {
  const res = await fetch(`${API_BASE}/reports/summary/ai${_reportsQuery(startDate, endDate)}`);
  if (!res.ok) throw new Error(`Failed to generate AI report (${res.status})`);
  return res.json(); // { report_markdown, data }
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
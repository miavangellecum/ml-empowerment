const API_BASE = "http://localhost:8000";

const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");

function setStatus(message, type) {
  statusEl.textContent = message;
  statusEl.className = type || "";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const file = fileInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  submitBtn.disabled = true;
  resultEl.textContent = "";
  setStatus("Uploading and processing (OCR + LLM)... this can take a moment.", "loading");

  try {
    const res = await fetch(`${API_BASE}/extract`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Server responded ${res.status}: ${text}`);
    }

    const data = await res.json();
    setStatus("Done. Receipt saved.", "success");
    resultEl.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    console.error(err);
    setStatus(`Error: ${err.message}`, "error");
  } finally {
    submitBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------
// Plaid Link — connect a bank account and pull in transactions
// ---------------------------------------------------------------------

const connectBankBtn = document.getElementById("connect-bank-btn");
const plaidStatusEl = document.getElementById("plaid-status");
const transactionsTable = document.getElementById("transactions-table");
const transactionsBody = document.getElementById("transactions-body");

function setPlaidStatus(message, type) {
  plaidStatusEl.textContent = message;
  plaidStatusEl.className = type || "";
}

async function getLinkToken() {
  const res = await fetch(`${API_BASE}/plaid/create_link_token`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to create link token (${res.status})`);
  const data = await res.json();
  return data.link_token;
}

async function exchangePublicToken(publicToken) {
  const res = await fetch(`${API_BASE}/plaid/exchange_public_token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ public_token: publicToken }),
  });
  if (!res.ok) throw new Error(`Failed to exchange public token (${res.status})`);
  return res.json();
}

async function loadTransactions(itemId) {
  const url = itemId
    ? `${API_BASE}/plaid/transactions?item_id=${encodeURIComponent(itemId)}`
    : `${API_BASE}/plaid/transactions`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load transactions (${res.status})`);
  return res.json();
}

function renderTransactions(transactions) {
  transactionsBody.innerHTML = "";
  if (!transactions.length) {
    transactionsTable.classList.add("hidden");
    return;
  }
  for (const t of transactions) {
    const row = document.createElement("tr");
    const amount = typeof t.amount === "number" ? t.amount.toFixed(2) : t.amount;
    row.innerHTML = `
      <td>${t.date ?? ""}</td>
      <td>${t.merchant_name || t.name || ""}</td>
      <td>${amount} ${t.iso_currency_code ?? ""}</td>
      <td>${t.category ?? ""}</td>
    `;
    transactionsBody.appendChild(row);
  }
  transactionsTable.classList.remove("hidden");
}

connectBankBtn.addEventListener("click", async () => {
  connectBankBtn.disabled = true;
  setPlaidStatus("Preparing secure connection...", "loading");

  try {
    const linkToken = await getLinkToken();

    const handler = Plaid.create({
      token: linkToken,
      onSuccess: async (publicToken, metadata) => {
        setPlaidStatus("Linking account and syncing transactions...", "loading");
        try {
          const { item_id } = await exchangePublicToken(publicToken);
          const transactions = await loadTransactions(item_id);
          renderTransactions(transactions);
          setPlaidStatus(
            `Connected ${metadata.institution?.name || "bank"}. Loaded ${transactions.length} transactions.`,
            "success"
          );
        } catch (err) {
          console.error(err);
          setPlaidStatus(`Error: ${err.message}`, "error");
        } finally {
          connectBankBtn.disabled = false;
        }
      },
      onExit: (err) => {
        connectBankBtn.disabled = false;
        if (err) {
          console.error(err);
          setPlaidStatus(`Link closed with error: ${err.error_message || err.error_code}`, "error");
        } else {
          setPlaidStatus("", "");
        }
      },
    });

    handler.open();
  } catch (err) {
    console.error(err);
    setPlaidStatus(`Error: ${err.message}`, "error");
    connectBankBtn.disabled = false;
  }
});

// On page load, show any transactions already stored from a previous session.
(async () => {
  try {
    const transactions = await loadTransactions();
    renderTransactions(transactions);
  } catch (err) {
    console.error("Could not preload transactions:", err);
  }
})();


(async function(){
  const receipts = await fetchReceipts();
  const activity = await fetchRecentActivity();
  const missing = activity.filter(a => a.no_receipt);

  document.getElementById('receiptCount').textContent = receipts.length;
  document.getElementById('missingCount').textContent = missing.length;
  const weekTotal = activity.reduce((s,a)=>s+a.total,0);
  document.getElementById('weekSpend').textContent = '−€' + weekTotal.toFixed(2);

  const totals = {};
  receipts.forEach(r => { totals[r.category] = (totals[r.category]||0) + r.total; });
  const grand = Object.values(totals).reduce((a,b)=>a+b,0);

  const field = document.getElementById('orbitField');
  const detail = document.getElementById('orbitDetail');
  const positions = [
    {top:'2%', left:'6%'}, {top:'0%', left:'52%'}, {top:'44%', left:'0%'},
    {top:'38%', left:'58%'}, {top:'66%', left:'30%'}, {top:'60%', left:'74%'}
  ];
  Object.entries(totals).forEach(([cat, amount], i)=>{
    const meta = CATEGORY_META[cat] || CATEGORY_META.other;
    const share = amount/grand;
    const size = Math.max(56, Math.round(140*Math.sqrt(share)*1.8));
    const b = document.createElement('a');
    b.href = `/receipts.html?cat=${cat}`;
    b.className = 'bubble' + (size<78 ? ' small':'');
    b.style.width = size+'px'; b.style.height = size+'px';
    b.style.background = meta.color;
    const pos = positions[i % positions.length];
    b.style.top = pos.top; b.style.left = pos.left;
    b.innerHTML = `<span class="cat">${cat}</span><span class="amt">€${amount.toFixed(0)}</span>`;
    b.addEventListener('click', (e)=>{
      e.preventDefault();
      document.querySelectorAll('.bubble').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      detail.innerHTML = `<span><span class="dot" style="background:${meta.color}"></span>${cat}</span><span>€${amount.toFixed(2)} · ${(share*100).toFixed(0)}% of spend</span>`;
    });
    field.appendChild(b);
  });

  const listEl = document.getElementById('activityList');
  activity.slice(0,6).forEach(r=>{
    const meta = CATEGORY_META[r.category] || CATEGORY_META.other;
    const a = document.createElement(r.no_receipt ? 'div' : 'a');
    a.className = 'activity-row' + (r.no_receipt ? ' warn' : '');
    if(!r.no_receipt) a.href = `/receipt.html?id=${r.id}`;
    a.innerHTML = `
      <div class="icon">${r.no_receipt ? '⚠️' : meta.icon}</div>
      <div class="meta">
        <div class="name">${r.store_name}</div>
        <div class="cat">${r.category}${r.no_receipt ? '<span class="warn-tag">No receipt</span>' : ''}</div>
      </div>
      <div class="amt">−€${r.total.toFixed(2)}</div>`;
    listEl.appendChild(a);
  });

  const circles = document.querySelectorAll('.circle');
  function tilt(x,y){
    const px = x/window.innerWidth - 0.5;
    const py = y/window.innerHeight - 0.5;
    circles.forEach((c,i)=>{
      const depth = (i+1)*14;
      c.style.transform = `translate(${px*depth}px, ${py*depth}px)`;
    });
  }
  window.addEventListener('pointermove', e => tilt(e.clientX, e.clientY));
  window.addEventListener('touchmove', e=>{ const t=e.touches[0]; tilt(t.clientX,t.clientY); }, {passive:true});

  document.getElementById('connectBankBtn').addEventListener('click', (e)=>{
    e.preventDefault();
    alert('This would open Plaid Link to connect another bank account.');
  });
})();

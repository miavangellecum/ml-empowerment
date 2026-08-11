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

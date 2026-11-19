const isBrowserPreview = window.location.pathname.startsWith('/preview/');

async function requestJson(path, method = 'GET', body) {
    const options = { method };
    if (body !== undefined) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify(body);
    }

    const response = await fetch(path, options);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
        const detail = payload?.detail;
        const message = typeof detail === 'string' ? detail : detail?.message;
        throw new Error(message || 'The request could not be completed. Please try again.');
    }
    return payload;
}

const browserDashboard = {
    get_dashboard_data: () => requestJson('/api/dashboard'),
    add_transaction: (category, amount, type, date) => requestJson('/api/dashboard/transactions', 'POST', {
        category, amount: Number(amount), type, date
    }),
    delete_transaction: (id) => requestJson(`/api/dashboard/transactions/${id}`, 'DELETE'),
    undo_last_action: () => requestJson('/api/dashboard/undo', 'POST', {}),
    add_upcoming_bill: (name, amount, dueDate) => requestJson('/api/dashboard/bills', 'POST', {
        name, amount: Number(amount), due_date: dueDate
    }),
    sort_transactions: (algorithm) => requestJson('/api/dashboard/sort', 'POST', { algorithm }),
    benchmark_runtime: (size) => requestJson('/api/dashboard/benchmark', 'POST', { dataset_size: Number(size) })
};

function dashboardApi() {
    return isBrowserPreview ? browserDashboard : window.pywebview.api;
}

async function runDashboardAction(action) {
    const error = document.getElementById('dashboard-error');
    error.hidden = true;
    try {
        return await action(dashboardApi());
    } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        return null;
    }
}

async function updateDashboard(action) {
    const data = await runDashboardAction(action);
    if (data) renderDashboard(data);
    return Boolean(data);
}

function renderDashboard(data) {
    document.getElementById('total-income').textContent = `₱${data.total_income.toFixed(2)}`;
    document.getElementById('total-expense').textContent = `₱${data.total_expense.toFixed(2)}`;
    document.getElementById('balance').textContent = `₱${data.balance.toFixed(2)}`;
    document.getElementById('undo-btn').disabled = !data.can_undo;

    const tbody = document.getElementById('tx-table-body');
    tbody.replaceChildren();
    for (const tx of data.transactions) {
        const row = tbody.insertRow();
        for (const value of [tx.date, tx.type, tx.category, `₱${tx.amount.toFixed(2)}`]) {
            row.insertCell().textContent = value;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Delete';
        button.addEventListener('click', () => handleDelete(tx.id));
        row.insertCell().append(button);
    }

    const billList = document.getElementById('bill-list');
    billList.replaceChildren();
    for (const bill of data.upcoming_bills) {
        const item = document.createElement('li');
        const name = document.createElement('strong');
        name.textContent = bill.name;
        item.append(`${bill.due_date} — `, name, `: ₱${bill.amount.toFixed(2)}`);
        billList.append(item);
    }
}

async function handleAddTransaction(event) {
    event.preventDefault();
    const form = event.target;
    const type = document.getElementById('tx-type').value;
    const category = document.getElementById('tx-category').value;
    const amount = document.getElementById('tx-amount').value;
    const date = document.getElementById('tx-date').value;

    if (await updateDashboard(api => api.add_transaction(category, amount, type, date))) {
        form.reset();
    }
}

function handleDelete(id) {
    return updateDashboard(api => api.delete_transaction(id));
}

function handleUndo() {
    return updateDashboard(api => api.undo_last_action());
}

async function handleAddBill(event) {
    event.preventDefault();
    const form = event.target;
    const name = document.getElementById('bill-name').value;
    const amount = document.getElementById('bill-amount').value;
    const dueDate = document.getElementById('bill-date').value;

    if (await updateDashboard(api => api.add_upcoming_bill(name, amount, dueDate))) {
        form.reset();
    }
}

function handleSort(algorithm) {
    return updateDashboard(api => api.sort_transactions(algorithm, 'amount'));
}

async function handleBenchmark() {
    const size = document.getElementById('bench-size').value;
    const result = await runDashboardAction(api => api.benchmark_runtime(size));
    if (!result) return;

    document.getElementById('bench-result').textContent =
        `Dataset: ${result.dataset_size} | Merge Sort: ${result.merge_sort_ms} ms | Insertion Sort: ${result.insertion_sort_ms} ms`;
}

let chatProfile;
let chatHistory = [];

function appendChatTurn(thread, role, content) {
    const turn = document.createElement('div');
    turn.className = role === 'user' ? 'chat-turn chat-turn-user' : 'chat-turn';
    const name = document.createElement('strong');
    name.textContent = role === 'user' ? 'You' : 'BudgetAI';
    const message = document.createElement('p');
    message.textContent = content;
    turn.append(name, message);
    thread.append(turn);
    thread.scrollTop = thread.scrollHeight;
    return turn;
}

async function handleChat(event) {
    event.preventDefault();
    const input = document.getElementById('chat-message');
    const send = document.getElementById('chat-send');
    const status = document.getElementById('chat-status');
    const thread = document.getElementById('chat-thread');
    const message = input.value.trim();
    if (send.disabled) return;
    if (!message) {
        status.textContent = 'Enter a budget question.';
        status.classList.add('status-error');
        return;
    }

    send.disabled = true;
    input.disabled = true;
    input.value = '';
    status.textContent = 'Waiting for BudgetAI…';
    status.classList.remove('status-error');
    const pendingTurn = appendChatTurn(thread, 'user', message);
    try {
        chatProfile ??= await requestJson('/api/demo-profile');
        const { reply } = await requestJson('/api/chat', 'POST', { profile: chatProfile, message, history: chatHistory });
        appendChatTurn(thread, 'assistant', reply);
        chatHistory = [...chatHistory, { role: 'user', content: message },
            { role: 'assistant', content: Array.from(reply).slice(0, 1000).join('') }].slice(-8);
        status.textContent = '';
    } catch (err) {
        pendingTurn.remove();
        input.value = message;
        status.textContent = err.message;
        status.classList.add('status-error');
    } finally {
        send.disabled = false;
        input.disabled = false;
        input.focus();
    }
}

function loadDashboard() {
    return updateDashboard(api => api.get_dashboard_data());
}

if (isBrowserPreview) {
    document.getElementById('chat-link').hidden = false;
    document.getElementById('budget-chat').hidden = false;
    document.getElementById('chat-form').addEventListener('submit', handleChat);
    loadDashboard();
} else {
    window.addEventListener('pywebviewready', loadDashboard);
}

const isBrowserPreview = window.location.pathname.startsWith('/preview/');

async function requestJson(path, method = 'GET', body) {
    const options = { method };
    if (body !== undefined) {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify(body);
    }

    let response;
    try {
        response = await fetch(path, options);
    } catch {
        throw new Error('Could not reach BahayBulsa. Check that the server is running and try again.');
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
        const detail = payload?.detail;
        const message = typeof detail === 'string' ? detail : detail?.message || detail?.[0]?.msg;
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
        name, amount: Number(amount), due_date: dueDate, recurring: document.getElementById('bill-recurring').checked
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
        item.append(`${bill.due_date} — `, name, `: ₱${bill.amount.toFixed(2)}${bill.recurring ? ' monthly' : ''}`);
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

const ACCOUNT_CHANGED = 'bahaybulsa-account-changed';

async function loadAccount() {
    const status = document.getElementById('account-status');
    const error = document.getElementById('account-error');
    try {
        const { username } = await requestJson('/api/account');
        status.textContent = username ? `Signed in as ${username}` : 'Sign in or create an account';
        document.getElementById('account-form').hidden = Boolean(username);
        document.getElementById('account-logout').hidden = !username;
    } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
    }
}

function refreshIdentity() {
    document.body.hidden = true;
    window.location.reload();
}

async function changeAccount(action, body) {
    const account = document.getElementById('browser-account');
    const error = document.getElementById('account-error');
    const controls = account.querySelectorAll('button, input');
    controls.forEach(control => { control.disabled = true; });
    error.hidden = true;
    account.setAttribute('aria-busy', 'true');
    try {
        await requestJson(`/api/account/${action}`, 'POST', body);
        try {
            localStorage.setItem(ACCOUNT_CHANGED, String(Date.now()));
        } catch {
            // A same-tab reload also works when browser storage is disabled.
        }
        refreshIdentity();
    } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
    } finally {
        document.getElementById('account-password').value = '';
        controls.forEach(control => { control.disabled = false; });
        account.removeAttribute('aria-busy');
    }
}

function handleAccount(event) {
    event.preventDefault();
    const username = document.getElementById('account-username').value.trim();
    const password = document.getElementById('account-password').value;
    return changeAccount(event.submitter?.value || 'login', { username, password });
}

const budgetCategories = [
    'housing', 'food', 'utilities', 'transportation', 'health', 'education',
    'insurance_contributions', 'family_support', 'personal_care', 'shopping',
    'dining_out', 'entertainment', 'subscriptions', 'other'
];
let budgetRead = 0;

function setupBudget() {
    const now = new Date();
    const month = document.getElementById('budget-month');
    month.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    const container = document.getElementById('budget-category-limits');
    for (const category of budgetCategories) {
        const label = document.createElement('label');
        const amount = document.createElement('input');
        amount.id = `budget-category-${category}`;
        amount.type = 'number';
        amount.min = '0';
        amount.max = '100000000';
        amount.step = '0.01';
        label.htmlFor = amount.id;
        const name = category.replaceAll('_', ' ');
        label.textContent = `${name[0].toUpperCase()}${name.slice(1)} (₱)`;
        container.append(label, amount);
    }
    month.addEventListener('change', loadBudget);
    document.getElementById('budget-form').addEventListener('submit', handleBudget);
}

function renderBudget(budget) {
    document.getElementById('budget-limit').value = budget.monthly_budget ?? '';
    for (const category of budgetCategories) {
        document.getElementById(`budget-category-${category}`).value = budget.category_limits[category] ?? '';
    }
    document.getElementById('goal-name').value = budget.savings_goal?.name ?? '';
    document.getElementById('goal-target').value = budget.savings_goal?.target_amount ?? '';
    document.getElementById('goal-saved').value = budget.savings_goal?.current_amount ?? '';
}

async function loadBudget() {
    const read = ++budgetRead;
    const month = document.getElementById('budget-month').value;
    const fields = document.getElementById('budget-values');
    const status = document.getElementById('budget-status');
    fields.disabled = true;
    status.classList.remove('status-error');
    status.textContent = month ? 'Loading budget…' : 'Choose a budget month.';
    if (!month) return;
    try {
        const budget = await requestJson(`/api/dashboard/budget?month=${encodeURIComponent(month)}`);
        if (read !== budgetRead) return;
        renderBudget(budget);
        fields.disabled = false;
        status.textContent = '';
    } catch (err) {
        if (read !== budgetRead) return;
        status.textContent = err.message;
        status.classList.add('status-error');
    }
}

async function handleBudget(event) {
    event.preventDefault();
    const month = document.getElementById('budget-month');
    const fields = document.getElementById('budget-values');
    const status = document.getElementById('budget-status');
    if (fields.disabled) return;
    const categoryLimits = {};
    for (const category of budgetCategories) {
        const amount = document.getElementById(`budget-category-${category}`).value;
        if (amount !== '') categoryLimits[category] = Number(amount);
    }
    const name = document.getElementById('goal-name').value.trim();
    const target = document.getElementById('goal-target').value;
    const saved = document.getElementById('goal-saved').value;
    const hasGoal = name !== '' || target !== '' || saved !== '';
    if (hasGoal && (!name || target === '' || saved === '')) {
        document.getElementById('goal-name').closest('details').open = true;
        status.textContent = 'Enter a name, target, and amount already saved for your goal.';
        status.classList.add('status-error');
        return;
    }

    const limit = document.getElementById('budget-limit').value;
    fields.disabled = true;
    month.disabled = true;
    status.textContent = 'Saving budget…';
    status.classList.remove('status-error');
    try {
        const budget = await requestJson('/api/dashboard/budget', 'PUT', {
            month: month.value,
            monthly_budget: limit === '' ? null : Number(limit),
            category_limits: categoryLimits,
            savings_goal: hasGoal ? { name, target_amount: Number(target), current_amount: Number(saved) } : null
        });
        renderBudget(budget);
        status.textContent = 'Budget saved.';
    } catch (err) {
        status.textContent = err.message;
        status.classList.add('status-error');
    } finally {
        fields.disabled = false;
        month.disabled = false;
    }
}

let chatProfile;
let demoChatHistory = [];

function updateChatSource() {
    const isDashboard = document.getElementById('chat-source').value === 'dashboard';
    document.getElementById('chat-thread-demo').hidden = isDashboard;
    document.getElementById('chat-thread-dashboard').hidden = !isDashboard;
    document.getElementById('chat-context').textContent = isDashboard
        ? 'Ask about your posted transactions, saved budget, upcoming bills, or savings goal.'
        : 'The sample household has ₱30,000 monthly income, ₱10,000 rent, and ₱8,000 for groceries.';
    document.getElementById('chat-message').placeholder = isDashboard
        ? 'How much did I spend this month?'
        : 'What if my rent goes up by ₱2,000?';
    const status = document.getElementById('chat-status');
    status.textContent = '';
    status.classList.remove('status-error');
}

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

function renderChatHistory(source, history) {
    const thread = document.getElementById(`chat-thread-${source}`);
    thread.replaceChildren();
    for (const { role, content } of history) appendChatTurn(thread, role, content);
}

function setChatBusy(busy) {
    for (const id of ['chat-message', 'chat-send', 'chat-source', 'chat-reset']) {
        document.getElementById(id).disabled = busy;
    }
}

async function loadDashboardHistory() {
    const status = document.getElementById('chat-status');
    try {
        const { history } = await requestJson('/api/preview-chat/history');
        renderChatHistory('dashboard', history);
    } catch (err) {
        status.textContent = err.message;
        status.classList.add('status-error');
    }
}

async function resetChat() {
    const source = document.getElementById('chat-source').value;
    const status = document.getElementById('chat-status');
    setChatBusy(true);
    status.textContent = '';
    status.classList.remove('status-error');
    try {
        if (source === 'dashboard') {
            const { history } = await requestJson('/api/preview-chat/reset', 'POST', {});
            renderChatHistory(source, history);
        } else {
            demoChatHistory = [];
            renderChatHistory(source, demoChatHistory);
        }
        document.getElementById('chat-message').value = '';
    } catch (err) {
        status.textContent = err.message;
        status.classList.add('status-error');
    } finally {
        setChatBusy(false);
        document.getElementById('chat-message').focus();
    }
}

async function handleChat(event) {
    event.preventDefault();
    const input = document.getElementById('chat-message');
    const send = document.getElementById('chat-send');
    const status = document.getElementById('chat-status');
    const source = document.getElementById('chat-source').value;
    const thread = document.getElementById(`chat-thread-${source}`);
    const message = input.value.trim();
    if (send.disabled) return;
    if (!message) {
        status.textContent = 'Enter a budget question.';
        status.classList.add('status-error');
        return;
    }

    setChatBusy(true);
    input.value = '';
    status.textContent = 'Waiting for BudgetAI…';
    status.classList.remove('status-error');
    const pendingTurn = appendChatTurn(thread, 'user', message);
    try {
        let answer;
        if (source === 'dashboard') {
            answer = await requestJson('/api/preview-chat', 'POST', { message });
        } else {
            chatProfile ??= await requestJson('/api/demo-profile');
            answer = await requestJson('/api/chat', 'POST', { profile: chatProfile, message, history: demoChatHistory });
        }
        const { reply } = answer;
        appendChatTurn(thread, 'assistant', reply);
        if (source === 'demo') {
            demoChatHistory = [...demoChatHistory, { role: 'user', content: message },
                { role: 'assistant', content: Array.from(reply).slice(0, 1000).join('') }].slice(-8);
        }
        status.textContent = '';
    } catch (err) {
        pendingTurn.remove();
        input.value = message;
        status.textContent = err.message;
        status.classList.add('status-error');
    } finally {
        setChatBusy(false);
        input.focus();
    }
}

function loadDashboard() {
    return updateDashboard(api => api.get_dashboard_data());
}

async function setupWebsite() {
    document.getElementById('chat-link').hidden = false;
    document.getElementById('budget-chat').hidden = false;
    document.getElementById('browser-account').hidden = false;
    document.getElementById('budget-settings').hidden = false;
    document.getElementById('bill-recurring-control').hidden = false;
    document.getElementById('account-form').addEventListener('submit', handleAccount);
    document.getElementById('account-logout').addEventListener('click', () => changeAccount('logout', {}));
    document.getElementById('chat-form').addEventListener('submit', handleChat);
    document.getElementById('chat-source').addEventListener('change', updateChatSource);
    document.getElementById('chat-reset').addEventListener('click', resetChat);
    window.addEventListener('storage', event => {
        if (event.key === ACCOUNT_CHANGED) refreshIdentity();
    });
    updateChatSource();
    setupBudget();
    setChatBusy(true);
    await loadDashboard();
    await Promise.all([loadAccount(), loadBudget(), loadDashboardHistory()]);
    setChatBusy(false);
}

if (isBrowserPreview) {
    setupWebsite();
} else {
    window.addEventListener('pywebviewready', loadDashboard);
}

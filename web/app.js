async function runDashboardAction(action) {
    const error = document.getElementById('dashboard-error');
    error.hidden = true;
    try {
        return await action(window.pywebview.api);
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

function updateChatSource() {
    const isDashboard = document.getElementById('chat-source').value === 'dashboard';
    document.getElementById('chat-thread-demo').hidden = isDashboard;
    document.getElementById('chat-thread-dashboard').hidden = !isDashboard;
    document.getElementById('chat-context').textContent = isDashboard
        ? 'Ask about your posted transactions, budget plan, upcoming bills, or savings goal.'
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

function appendBudgetPlan(turn, plan) {
    const sections = plan.sections;
    const formatMoney = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const money = amount => `${plan.currency} ${formatMoney.format(amount)}`;
    const details = document.createElement('details');
    const label = document.createElement('summary');
    label.textContent = 'Budget plan details';
    details.open = true;
    details.append(label);

    function addSection(title, lines) {
        if (!lines.length) return;
        const heading = document.createElement('h3');
        heading.textContent = title;
        const list = document.createElement('ul');
        for (const line of lines) {
            const item = document.createElement('li');
            item.textContent = line;
            list.append(item);
        }
        details.append(heading, list);
    }

    const income = sections.income_overview;
    const expenses = sections.expense_overview;
    addSection('Monthly overview', [
        `Income ${money(income.monthly_income)}`,
        `Income available for planning ${money(income.planning_income)}`,
        `Expenses ${money(expenses.monthly_estimate)}`,
        `Fixed expenses ${money(expenses.fixed)} and variable expenses ${money(expenses.variable)}`,
        `Required spending ${money(expenses.required)} including ${money(expenses.minimum_debt_payments)} for minimum debt payments`,
        `Total allocated ${money(plan.total_allocated)} and unallocated ${money(plan.unallocated)}`,
    ]);
    addSection('Recommended allocations', sections.recommended_allocations.map(line => (
        `${line.name} · ${money(line.amount)} (${line.percentage_of_planning_income}% of planning income)`
    )));
    const reductions = sections.spending_adjustments.map(item => (
        `${item.expense_name} · suggested change from ${money(item.current_amount)} to ${money(item.suggested_amount)}. ${item.reason}`
    ));
    addSection('Spending adjustments', reductions);
    addSection('Savings and emergency fund', [sections.savings_recommendations].filter(Boolean));
    addSection('Financial goals', sections.financial_goals.map(goal => {
        const target = `${goal.name} · Target ${money(goal.target)}.`;
        return goal.planned_monthly_contribution === null ? target
            : `${target} Planned monthly contribution ${money(goal.planned_monthly_contribution)}.`;
    }));
    addSection('How to use this plan', [sections.explanation, ...plan.action_steps, ...plan.missing_data].filter(Boolean));
    turn.append(details);
    turn.parentElement.scrollTop = turn.parentElement.scrollHeight;
}

function setChatBusy(busy) {
    for (const id of ['chat-message', 'chat-send', 'chat-source', 'chat-reset', 'chat-plan']) {
        document.getElementById(id).disabled = busy;
    }
}

async function requestDesktopChat(method, ...args) {
    let result;
    try {
        result = await window.pywebview.api[method](...args);
    } catch {
        throw new Error('BahayBulsa could not complete the request. Try again.');
    }
    if (result.error) throw new Error(result.error);
    return result;
}

async function loadChatHistory() {
    const status = document.getElementById('chat-status');
    try {
        const { histories } = await requestDesktopChat('get_chat_history');
        for (const [source, history] of Object.entries(histories)) renderChatHistory(source, history);
    } catch (err) {
        status.textContent = err.message;
        status.classList.add('status-error');
    }
}

async function resetChat() {
    const source = document.getElementById('chat-source').value;
    const status = document.getElementById('chat-status');
    if (document.getElementById('chat-reset').disabled) return;
    setChatBusy(true);
    status.textContent = '';
    status.classList.remove('status-error');
    try {
        const { history } = await requestDesktopChat('reset_chat', source);
        renderChatHistory(source, history);
        document.getElementById('chat-message').value = '';
    } catch (err) {
        status.textContent = err.message;
        status.classList.add('status-error');
    } finally {
        setChatBusy(false);
        document.getElementById('chat-message').focus();
    }
}

async function handleChat(event, generatePlan = false) {
    event.preventDefault();
    const input = document.getElementById('chat-message');
    const send = document.getElementById('chat-send');
    const status = document.getElementById('chat-status');
    const source = document.getElementById('chat-source').value;
    const thread = document.getElementById(`chat-thread-${source}`);
    const message = generatePlan ? 'Create a monthly budget plan.' : input.value.trim();
    if (send.disabled) return;
    if (!message) {
        status.textContent = 'Enter a budget question.';
        status.classList.add('status-error');
        return;
    }

    setChatBusy(true);
    if (!generatePlan) input.value = '';
    status.textContent = 'Waiting for BudgetAI…';
    status.classList.remove('status-error');
    const pendingTurn = appendChatTurn(thread, 'user', message);
    try {
        const { reply, plan, warning } = generatePlan
            ? await requestDesktopChat('generate_budget_plan', source)
            : await requestDesktopChat('ask_budget_ai', source, message);
        const turn = appendChatTurn(thread, 'assistant', reply);
        if (plan?.sections) appendBudgetPlan(turn, plan);
        status.textContent = warning || '';
    } catch (err) {
        pendingTurn.remove();
        if (!generatePlan) input.value = message;
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

async function setupDesktop() {
    document.getElementById('chat-link').hidden = false;
    document.getElementById('budget-chat').hidden = false;
    document.getElementById('chat-form').addEventListener('submit', handleChat);
    document.getElementById('chat-source').addEventListener('change', updateChatSource);
    document.getElementById('chat-reset').addEventListener('click', resetChat);
    document.getElementById('chat-plan').addEventListener('click', event => handleChat(event, true));
    updateChatSource();
    setChatBusy(true);
    await Promise.all([loadDashboard(), loadChatHistory()]);
    setChatBusy(false);
}

window.addEventListener('pywebviewready', setupDesktop);

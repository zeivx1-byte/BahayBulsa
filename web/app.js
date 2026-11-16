window.addEventListener('pywebviewready', async () => {
    const data = await window.pywebview.api.get_dashboard_data();
    renderDashboard(data);
});

function renderDashboard(data) {
    document.getElementById('total-income').textContent = `₱${data.total_income.toFixed(2)}`;
    document.getElementById('total-expense').textContent = `₱${data.total_expense.toFixed(2)}`;
    document.getElementById('balance').textContent = `₱${data.balance.toFixed(2)}`;
    document.getElementById('undo-btn').disabled = !data.can_undo;

    // Render Transactions
    const tbody = document.getElementById('tx-table-body');
    tbody.innerHTML = '';
    data.transactions.forEach(tx => {
        tbody.innerHTML += `
            <tr>
                <td>${tx.date}</td>
                <td>${tx.type}</td>
                <td>${tx.category}</td>
                <td>₱${tx.amount.toFixed(2)}</td>
                <td><button onclick="handleDelete(${tx.id})">Delete</button></td>
            </tr>
        `;
    });

    // Render Priority Queue Bills
    const billList = document.getElementById('bill-list');
    billList.innerHTML = '';
    data.upcoming_bills.forEach(bill => {
        billList.innerHTML += `<li>${bill.due_date} — <strong>${bill.name}</strong>: ₱${bill.amount.toFixed(2)}</li>`;
    });
}

async function handleAddTransaction(event) {
    event.preventDefault();
    const type = document.getElementById('tx-type').value;
    const category = document.getElementById('tx-category').value;
    const amount = document.getElementById('tx-amount').value;
    const date = document.getElementById('tx-date').value;

    const data = await window.pywebview.api.add_transaction(category, amount, type, date);
    event.target.reset();
    renderDashboard(data);
}

async function handleDelete(id) {
    const data = await window.pywebview.api.delete_transaction(id);
    renderDashboard(data);
}

async function handleUndo() {
    const data = await window.pywebview.api.undo_last_action();
    renderDashboard(data);
}

async function handleAddBill(event) {
    event.preventDefault();
    const name = document.getElementById('bill-name').value;
    const amount = document.getElementById('bill-amount').value;
    const dueDate = document.getElementById('bill-date').value;

    const data = await window.pywebview.api.add_upcoming_bill(name, amount, dueDate);
    event.target.reset();
    renderDashboard(data);
}

async function handleSort(algo) {
    const data = await window.pywebview.api.sort_transactions(algo, 'amount');
    renderDashboard(data);
}

async function handleBenchmark() {
    const size = document.getElementById('bench-size').value;
    const res = await window.pywebview.api.benchmark_runtime(size);
    document.getElementById('bench-result').innerHTML =
        `Dataset: <strong>${res.dataset_size}</strong> | Merge Sort: <strong>${res.merge_sort_ms} ms</strong> | Insertion Sort: <strong>${res.insertion_sort_ms} ms</strong>`;
}

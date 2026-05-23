// Vivify Dashboard 前端应用
(function() {
    'use strict';

    // --- 状态 ---
    let currentTab = 'overview';
    let kpiChart = null;
    let actionsChart = null;
    let logEventSource = null;

    // --- 初始化 ---
    document.addEventListener('DOMContentLoaded', init);

    function init() {
        setupTabs();
        loadStatus();
        connectLogStream();
        setInterval(loadStatus, 5000); // 每 5 秒刷新状态
    }

    // --- Tab 切换 ---
    function setupTabs() {
        document.querySelectorAll('.tab').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                const tabId = btn.dataset.tab;
                document.getElementById('tab-' + tabId).classList.add('active');
                currentTab = tabId;
                onTabSwitch(tabId);
            });
        });
        // 过滤器事件
        const actionFilter = document.getElementById('action-type-filter');
        if (actionFilter) actionFilter.addEventListener('change', loadActions);
        const levelFilter = document.getElementById('issue-level-filter');
        if (levelFilter) levelFilter.addEventListener('change', loadIssues);
    }

    function onTabSwitch(tab) {
        switch(tab) {
            case 'issues': loadIssues(); break;
            case 'actions': loadActions(); break;
            case 'features': loadFeatures(); break;
            case 'trends': loadTrends(); break;
        }
    }

    // --- API 请求 ---
    async function api(path) {
        try {
            const res = await fetch(path);
            return await res.json();
        } catch(e) {
            console.error('API error:', path, e);
            return null;
        }
    }

    // --- 状态加载 ---
    async function loadStatus() {
        const data = await api('/api/status');
        if (!data) return;

        const dot = document.getElementById('status-indicator');
        const text = document.getElementById('status-text');
        if (data.daemon_running) {
            dot.className = 'status-dot online';
            text.textContent = `运行中 (PID: ${data.daemon_pid})`;
        } else {
            dot.className = 'status-dot offline';
            text.textContent = '离线';
        }

        // 概览卡片
        const daemonEl = document.getElementById('daemon-status');
        daemonEl.textContent = data.daemon_running ? '运行中' : '已停止';
        daemonEl.style.color = data.daemon_running ? 'var(--success)' : 'var(--danger)';

        if (data.latest_round) {
            const r = data.latest_round;
            document.getElementById('latest-round').innerHTML =
                `<div>${r.action_count} 动作</div><div style="font-size:0.8rem;color:var(--text-muted)">${formatTime(r.started_at)}</div>`;
        }

        // 加载操作统计
        const actions = await api('/api/actions?limit=100');
        if (actions) {
            const success = actions.filter(a => a.status === 'success').length;
            const failed = actions.filter(a => a.status === 'failed').length;
            document.getElementById('action-stats').innerHTML =
                `<span style="color:var(--success)">${success} 成功</span> / <span style="color:var(--danger)">${failed} 失败</span>`;
        }

        // 特性统计
        const features = await api('/api/features');
        if (features) {
            const developing = features.filter(f => f.status === 'developing').length;
            const deployed = features.filter(f => ['deployed', 'verified'].includes(f.status)).length;
            document.getElementById('feature-stats').innerHTML =
                `${developing} 开发中 / ${deployed} 已完成`;
        }
    }

    // --- 问题列表 ---
    async function loadIssues() {
        const level = document.getElementById('issue-level-filter').value;
        let url = '/api/actions?action_type=detect&limit=100';
        const data = await api(url);
        if (!data) return;

        let filtered = data;
        if (level) {
            filtered = data.filter(a => a.level === level);
        }

        const tbody = document.querySelector('#issues-table tbody');
        tbody.innerHTML = filtered.map(item => `
            <tr>
                <td><span class="badge badge-${(item.level||'low').toLowerCase()}">${item.level || '-'}</span></td>
                <td>${item.category || '-'}</td>
                <td>${item.title || '-'}</td>
                <td>${formatTime(item.created_at)}</td>
                <td><span class="badge badge-${item.status}">${item.status}</span></td>
            </tr>
        `).join('');
    }

    // --- 动作时间线 ---
    async function loadActions() {
        const type = document.getElementById('action-type-filter').value;
        let url = '/api/actions?limit=50';
        if (type) url += '&action_type=' + type;
        const data = await api(url);
        if (!data) return;

        const container = document.getElementById('actions-timeline');
        container.innerHTML = data.map(item => `
            <div class="timeline-item ${item.status}">
                <div class="time">${formatTime(item.created_at)} · ${item.action_type} · ${(item.duration_seconds || 0).toFixed(1)}s</div>
                <div class="title">${item.title || '(无标题)'}</div>
                <div class="detail">${item.result_summary || ''}</div>
            </div>
        `).join('');
    }

    // --- 特性看板 ---
    async function loadFeatures() {
        const data = await api('/api/features?limit=100');
        if (!data) return;

        // 按状态分组
        const groups = { pending: [], developing: [], deployed: [], verified: [] };
        data.forEach(f => {
            const status = f.status || 'pending';
            if (status in groups) groups[status].push(f);
            else if (['evaluating', 'approved'].includes(status)) groups.pending.push(f);
            else if (status === 'deployed_with_issues') groups.deployed.push(f);
            else groups.pending.push(f);
        });

        Object.entries(groups).forEach(([status, items]) => {
            const col = document.querySelector(`.kanban-col[data-status="${status}"] .kanban-cards`);
            if (!col) return;
            col.innerHTML = items.map(f => `
                <div class="kanban-card">
                    <div class="priority">${f.priority || 'P2'} · ${f.type || 'feature'}</div>
                    <div class="title">${f.title}</div>
                </div>
            `).join('') || '<div style="color:var(--text-muted);font-size:0.8rem">暂无</div>';
        });
    }

    // --- KPI 趋势图 ---
    async function loadTrends() {
        const snapshots = await api('/api/kpi/snapshots?limit=50');
        if (!snapshots || !snapshots.length) return;

        // KPI 评分趋势
        const labels = snapshots.map(s => formatTime(s.captured_at)).reverse();
        const scores = snapshots.map(s => s.overall_score || 0).reverse();

        const ctx1 = document.getElementById('kpi-chart').getContext('2d');
        if (kpiChart) kpiChart.destroy();
        kpiChart = new Chart(ctx1, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'KPI 综合评分',
                    data: scores,
                    borderColor: '#7aa2f7',
                    backgroundColor: 'rgba(122, 162, 247, 0.1)',
                    fill: true, tension: 0.3,
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { labels: { color: '#c0caf5' } } },
                scales: {
                    x: { ticks: { color: '#565f89' }, grid: { color: '#3b4261' } },
                    y: { ticks: { color: '#565f89' }, grid: { color: '#3b4261' }, min: 0 }
                }
            }
        });

        // 操作统计趋势
        const rounds = await api('/api/rounds?limit=20');
        if (rounds && rounds.length) {
            const rLabels = rounds.map(r => formatTime(r.started_at)).reverse();
            const successes = rounds.map(r => r.success_count || 0).reverse();
            const failures = rounds.map(r => r.failed_count || 0).reverse();

            const ctx2 = document.getElementById('actions-chart').getContext('2d');
            if (actionsChart) actionsChart.destroy();
            actionsChart = new Chart(ctx2, {
                type: 'bar',
                data: {
                    labels: rLabels,
                    datasets: [
                        { label: '成功', data: successes, backgroundColor: '#9ece6a' },
                        { label: '失败', data: failures, backgroundColor: '#f7768e' },
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { labels: { color: '#c0caf5' } } },
                    scales: {
                        x: { stacked: true, ticks: { color: '#565f89' }, grid: { color: '#3b4261' } },
                        y: { stacked: true, ticks: { color: '#565f89' }, grid: { color: '#3b4261' } }
                    }
                }
            });
        }
    }

    // --- SSE 日志流 ---
    function connectLogStream() {
        if (logEventSource) logEventSource.close();
        logEventSource = new EventSource('/api/logs/stream');
        const container = document.getElementById('log-stream');

        logEventSource.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                const div = document.createElement('div');
                div.className = 'log-line';
                div.textContent = data.line;
                container.appendChild(div);
                // 保持最多 500 行
                while (container.children.length > 500) {
                    container.removeChild(container.firstChild);
                }
                container.scrollTop = container.scrollHeight;
            } catch(e) {}
        };

        logEventSource.onerror = function() {
            setTimeout(connectLogStream, 3000);
        };
    }

    // --- 工具函数 ---
    function formatTime(isoStr) {
        if (!isoStr) return '-';
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr;
        return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    }
})();

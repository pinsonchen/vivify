// Vivify Dashboard 前端应用
(function() {
    'use strict';

    // --- 状态 ---
    let currentTab = 'overview';
    let kpiChart = null;
    let actionsChart = null;
    let logEventSource = null;
    // 多实例状态
    let instances = [];
    let currentInstanceId = null;
    let defaultInstanceId = null;

    // --- 初始化 ---
    document.addEventListener('DOMContentLoaded', init);

    function init() {
        setupTabs();
        loadInstances();
        loadStatus();
        connectLogStream();
        setInterval(loadStatus, 5000);    // 每 5 秒刷新状态
        setInterval(loadInstances, 30000); // 每 30 秒刷新实例列表
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
            case 'instances': renderInstancesPanel(); break;
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

    // 返回当前实例的 API 基础路径
    function getApiBase() {
        if (currentInstanceId && currentInstanceId !== defaultInstanceId) {
            return '/api/instances/' + currentInstanceId;
        }
        return '/api';
    }

    // --- 状态加载 ---
    async function loadStatus() {
        const base = getApiBase();
        const data = await api(base + '/status');
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
        const actions = await api(base + '/actions?limit=100');
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
        const base = getApiBase();
        let url = base + '/actions?action_type=detect&limit=100';
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
        const base = getApiBase();
        let url = base + '/actions?limit=50';
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
        const base = getApiBase();
        const snapshots = await api(base + '/kpi/snapshots?limit=50');
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

    // --- 多实例管理 ---
    async function loadInstances() {
        try {
            const resp = await fetch('/api/instances');
            const data = await resp.json();
            instances = data.instances || [];
            // 首次加载时设置默认实例
            if (!defaultInstanceId && data.current_instance) {
                defaultInstanceId = data.current_instance;
                currentInstanceId = data.current_instance;
            }
            renderInstanceSelector();
            if (currentTab === 'instances') renderInstancesPanel();
        } catch (e) {
            console.error('Failed to load instances:', e);
        }
    }

    function renderInstanceSelector() {
        const select = document.getElementById('instance-select');
        if (!select) return;
        const prevValue = select.value || currentInstanceId;
        select.innerHTML = '';

        if (instances.length === 0) {
            const opt = document.createElement('option');
            opt.textContent = '暂无实例';
            select.appendChild(opt);
            return;
        }

        instances.forEach(inst => {
            const opt = document.createElement('option');
            opt.value = inst.id;
            const name = inst.project_name || inst.repo.split('/').pop();
            const scene = inst.scenario || 'unknown';
            opt.textContent = `${name} (${scene})`;
            if (inst.id === (prevValue || currentInstanceId)) opt.selected = true;
            select.appendChild(opt);
        });

        updateStatusBadge();
    }

    function updateStatusBadge() {
        const badge = document.getElementById('instance-status-badge');
        if (!badge) return;
        const current = instances.find(i => i.id === currentInstanceId);
        if (current && current.daemon_running) {
            badge.className = 'instance-status-badge';
        } else {
            badge.className = 'instance-status-badge stopped';
        }
    }

    function switchInstance(instanceId) {
        if (!instanceId) return;
        currentInstanceId = instanceId;
        updateStatusBadge();
        // 刷新当前 Tab 数据
        onTabSwitch(currentTab);
        // 如果当前在概览 Tab，也刷新状态
        if (currentTab === 'overview') loadStatus();
        renderInstancesPanel();
    }

    function renderInstancesPanel() {
        const grid = document.getElementById('instances-grid');
        const overview = document.getElementById('instance-overview');
        if (!grid) return;

        // 当前实例详情
        const current = instances.find(i => i.id === currentInstanceId);
        if (current && overview) {
            const deployLink = current.deploy_url
                ? `<a href="${current.deploy_url}" target="_blank" style="color:#7aa2f7">${current.deploy_url}</a>`
                : '-';
            const uptimeStr = current.uptime_seconds != null
                ? formatUptime(current.uptime_seconds) : '-';
            overview.innerHTML = `
              <div class="instance-detail-card">
                <h3>当前实例详情</h3>
                <div class="instance-meta">
                  <div class="instance-meta-item">
                    <span class="label">项目名称</span>
                    <span class="value">${current.project_name || '-'}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">场景类型</span>
                    <span class="value">${current.scenario || '-'}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">项目目录</span>
                    <span class="value">${current.repo}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">语言/框架</span>
                    <span class="value">${current.language || '-'}${current.framework ? ' / ' + current.framework : ''}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">部署地址</span>
                    <span class="value">${deployLink}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">守护进程</span>
                    <span class="value">${current.daemon_running ? '运行中 (PID ' + current.daemon_pid + ')' : '已停止'}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">运行时长</span>
                    <span class="value">${uptimeStr}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">KPI 分数</span>
                    <span class="value">${current.kpi_score != null ? current.kpi_score.toFixed(1) : '-'}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">目标</span>
                    <span class="value">${current.goals && current.goals.length > 0 ? current.goals.join('、') : '-'}</span>
                  </div>
                  <div class="instance-meta-item">
                    <span class="label">最近操作</span>
                    <span class="value">${current.last_action || '-'}</span>
                  </div>
                </div>
              </div>
            `;
        }

        // 所有实例卡片
        if (instances.length === 0) {
            grid.innerHTML = '<div style="color:var(--text-muted);padding:2rem">未发现其他实例</div>';
            return;
        }
        grid.innerHTML = instances.map(inst => {
            const name = inst.project_name || inst.repo.split('/').pop();
            const goalsHtml = inst.goals && inst.goals.length > 0
                ? `<div class="instance-card-goals">${inst.goals.map(g => `<span class="goal-tag">${g}</span>`).join('')}</div>`
                : '';
            const kpiWidth = inst.kpi_score != null ? Math.min(100, Math.max(0, inst.kpi_score)) : 0;
            return `
              <div class="instance-card ${inst.id === currentInstanceId ? 'active' : ''}" onclick="switchInstance('${inst.id}')">
                <div class="instance-card-header">
                  <span class="instance-card-name">${name}</span>
                  <span class="instance-card-badge ${inst.daemon_running ? '' : 'stopped'}">
                    ${inst.daemon_running ? '运行中' : '已停止'}
                  </span>
                </div>
                <div class="instance-card-info">
                  <div>场景: ${inst.scenario || '-'}</div>
                  <div>目录: ${inst.repo}</div>
                  <div>语言: ${inst.language || '-'}</div>
                </div>
                ${goalsHtml}
                <div class="kpi-bar">
                  <div class="kpi-bar-fill" style="width:${kpiWidth}%"></div>
                </div>
              </div>
            `;
        }).join('');
    }

    function formatUptime(seconds) {
        if (seconds < 60) return seconds + '秒';
        if (seconds < 3600) return Math.floor(seconds / 60) + '分钟';
        if (seconds < 86400) return Math.floor(seconds / 3600) + '小时';
        return Math.floor(seconds / 86400) + '天';
    }

    // 将 switchInstance 暴露到全局（HTML onchange 回调需要）
    window.switchInstance = switchInstance;

})();

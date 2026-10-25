/* AUXILIUM — instalador: lógica da interface */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const SCREEN_CONTEXT = {
    welcome: 'Preparando o ambiente local',
    setup: 'Configuração da instalação',
    progress: 'Instalando o AUXILIUM',
    done: 'Instalação concluída',
  };
  const SCREEN_ORDER = ['welcome', 'setup', 'progress', 'done'];

  const state = {
    env: null,
    screen: 'welcome',
    destination: '',
    options: {
      createMenu: true,
      createDesktop: false,
      openAfter: true,
      copyModels: [],
    },
    installing: false,
    done: false,
    poll: null,
    polling: false,
    opened: false,
    opening: false,
    lastLog: '',
    lastSteps: '',
  };

  function fmtBytes(value) {
    const n = Number(value) || 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = n;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return i === 0 ? `${Math.round(v)} ${units[i]}` : `${v.toFixed(1)} ${units[i]}`;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  function showToast(message, kind = '') {
    const box = document.createElement('div');
    box.className = 'toast' + (kind ? ' ' + kind : '');
    box.textContent = message;
    $('toasts').appendChild(box);
    setTimeout(() => {
      box.style.opacity = '0';
      box.style.transform = 'translateY(5px)';
      setTimeout(() => box.remove(), 180);
    }, 4300);
  }

  function showScreen(name) {
    if (!SCREEN_ORDER.includes(name)) return;
    state.screen = name;
    document.querySelectorAll('.screen').forEach((screen) => {
      screen.classList.toggle('active', screen.dataset.screen === name);
    });
    $('headContext').textContent = SCREEN_CONTEXT[name] || '';
    const index = SCREEN_ORDER.indexOf(name);
    document.querySelectorAll('.step').forEach((step) => {
      const stepIndex = SCREEN_ORDER.indexOf(step.dataset.step);
      step.classList.toggle('active', stepIndex === index);
      step.classList.toggle('done', stepIndex >= 0 && stepIndex < index);
    });
    const body = document.querySelector('.screen.active .screen-body');
    if (body) body.scrollTop = 0;
  }

  function updateDestinationHint() {
    const value = $('destination').value.trim();
    const hint = $('destinationHint');
    if (!value) {
      hint.textContent = 'Informe uma pasta para continuar.';
      return;
    }
    if (state.env && value === state.env.defaultDestination) {
      hint.textContent = 'Pasta padrão do usuário. Sem privilégios administrativos.';
    } else {
      hint.textContent = 'Pasta personalizada. O AUXILIUM ficará totalmente contido nela.';
    }
  }

  function renderEnvironment() {
    const env = state.env;
    if (!env) return;
    $('version').textContent = env.version;
    $('versionTop').textContent = env.version;
    $('envSummary').textContent = `Python ${env.python} · ${env.ramGb || '—'} GB RAM · ${env.diskFreeGb || '—'} GB livres`;
    const checks = env.checks || [];
    $('envChecks').innerHTML = checks.map((check) => {
      const symbol = check.state === 'ok' ? '✓' : check.state === 'warn' ? '!' : '×';
      return `
        <div class="check-item">
          <span class="check-icon ${esc(check.state)}">${symbol}</span>
          <span class="check-copy">
            <span class="check-label">${esc(check.label)}</span>
            <span class="check-detail">${esc(check.detail)}</span>
          </span>
        </div>`;
    }).join('');

    const blocking = checks.filter((check) => check.blocking && check.state === 'fail');
    $('blockingCallout').hidden = blocking.length === 0;
    $('blockingText').textContent = blocking.map((check) => `${check.label}: ${check.detail}`).join(' · ');
    $('installBtn').disabled = blocking.length > 0;
  }

  function updateModelSummary() {
    if (!state.env) return;
    const selected = new Set(state.options.copyModels);
    const models = (state.env.models || []).filter((model) => selected.has(model.filename));
    const total = models.reduce((sum, model) => sum + model.size, 0);
    $('modelSummary').textContent = models.length
      ? `${models.length} modelo${models.length > 1 ? 's' : ''} · ${fmtBytes(total)}`
      : 'Nenhum selecionado';
  }

  function renderModels() {
    const models = (state.env && state.env.models) || [];
    const list = $('modelList');
    if (!models.length) {
      list.innerHTML = '<div class="model-empty">Nenhum modelo local encontrado. O primeiro uso oferece o download de 1.5B ou 3B.</div>';
      updateModelSummary();
      return;
    }
    const selected = new Set(state.options.copyModels);
    list.innerHTML = models.map((model) => {
      const disabled = !model.copyable;
      const isSelected = selected.has(model.filename);
      const badge = model.recommended
        ? '<span class="model-badge">Recomendado</span>'
        : (disabled ? '<span class="model-badge warn">Não copiado</span>' : '');
      return `
        <label class="model-item ${isSelected ? 'selected' : ''} ${disabled ? 'disabled' : ''}" data-filename="${esc(model.filename)}">
          <input type="checkbox" value="${esc(model.filename)}" ${isSelected ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
          <span class="model-main">
            <span class="model-head">
              <span class="model-name">${esc(model.name)}</span>
              ${badge}
            </span>
            <span class="model-meta">${esc(model.sizeLabel)} · ${disabled ? 'não copiado pelo instalador' : 'pode ser copiado agora'}</span>
            ${model.reason ? `<span class="model-reason">${esc(model.reason)}</span>` : ''}
          </span>
        </label>`;
    }).join('');
    list.querySelectorAll('input[type="checkbox"]').forEach((input) => {
      input.addEventListener('change', () => {
        const filename = input.value;
        const set = new Set(state.options.copyModels);
        if (input.checked) set.add(filename);
        else set.delete(filename);
        state.options.copyModels = [...set];
        input.closest('.model-item').classList.toggle('selected', input.checked);
        updateModelSummary();
      });
    });
    updateModelSummary();
  }

  function resetProgress() {
    $('progressPct').textContent = '0%';
    $('ring').style.setProperty('--p', '0');
    $('progressTitle').textContent = 'Preparando destino…';
    $('progressDetail').textContent = 'Copiando o aplicativo e preparando o primeiro uso.';
    $('progressBar').style.width = '0%';
    $('log').textContent = '';
    $('consoleHint').textContent = 'aguardando…';
    $('progressFoot').textContent = 'Não feche esta janela durante a instalação.';
    $('closeBtn').disabled = true;
    $('retryBtn').hidden = true;
    $('retryBack').hidden = true;
    $('installSteps').innerHTML = '';
    state.lastLog = '';
    state.lastSteps = '';
  }

  function renderSteps(steps) {
    const key = JSON.stringify(steps || []);
    if (key === state.lastSteps) return;
    state.lastSteps = key;
    const icons = { done: '✓', error: '!', skipped: '–', pending: '', running: '' };
    $('installSteps').innerHTML = (steps || []).map((step) => `
      <div class="install-step ${esc(step.state)}">
        <span class="step-state">${icons[step.state] || ''}</span>
        <span class="step-label">${esc(step.label)}</span>
      </div>`).join('');
  }

  function updateProgress(snapshot) {
    if (!snapshot) return;
    const pct = Math.max(0, Math.min(100, Number(snapshot.progress) || 0));
    $('progressPct').textContent = pct + '%';
    $('ring').style.setProperty('--p', String(pct));
    $('progressBar').style.width = pct + '%';
    $('progressTitle').textContent = snapshot.current || 'Instalando…';
    $('consoleHint').textContent = snapshot.done ? 'concluído' : (snapshot.phase === 'error' ? 'falhou' : 'em andamento');
    if (snapshot.phase === 'error') {
      $('progressDetail').textContent = snapshot.error || 'A instalação não foi concluída.';
    } else if (snapshot.done) {
      $('progressDetail').textContent = 'Tudo pronto. Preparando a tela de conclusão.';
    } else {
      const copied = Number(snapshot.copiedBytes) || 0;
      const total = Number(snapshot.totalBytes) || 0;
      $('progressDetail').textContent = total
        ? `${fmtBytes(copied)} de ${fmtBytes(total)} processados.`
        : 'Copiando arquivos e preparando o primeiro uso.';
    }
    renderSteps(snapshot.steps);
    const log = (snapshot.log || []).join('\n');
    if (log !== state.lastLog) {
      state.lastLog = log;
      const logEl = $('log');
      logEl.textContent = log;
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (snapshot.done) {
      state.installing = false;
      state.done = true;
      stopPolling();
      $('progressFoot').textContent = 'Instalação concluída.';
      setTimeout(() => showDone(snapshot), 420);
    } else if (snapshot.phase === 'error') {
      state.installing = false;
      stopPolling();
      $('progressFoot').textContent = 'Você pode revisar o destino ou tentar novamente.';
      $('closeBtn').disabled = false;
      $('retryBtn').hidden = false;
      $('retryBack').hidden = false;
      showToast('A instalação não foi concluída: ' + (snapshot.error || 'erro desconhecido'), 'error');
    }
  }

  function stopPolling() {
    if (state.poll) {
      clearInterval(state.poll);
      state.poll = null;
    }
  }

  async function pollProgress() {
    if (state.polling) return;
    state.polling = true;
    try {
      const data = await api('/api/progress');
      updateProgress(data.state);
    } catch (error) {
      stopPolling();
      if (state.installing) {
        state.installing = false;
        $('progressFoot').textContent = 'Não foi possível acompanhar a instalação.';
        $('closeBtn').disabled = false;
        showToast('Falha ao acompanhar a instalação: ' + error.message, 'error');
      }
    } finally {
      state.polling = false;
    }
  }

  function startPolling() {
    stopPolling();
    state.poll = setInterval(pollProgress, 450);
    pollProgress();
  }

  async function startInstall() {
    if (state.installing) return;
    const destination = $('destination').value.trim();
    if (!destination) {
      showToast('Informe a pasta de destino.', 'error');
      showScreen('setup');
      return;
    }
    state.options.createMenu = $('createMenu').checked;
    state.options.createDesktop = $('createDesktop').checked;
    state.options.openAfter = $('openAfter').checked;
    state.installing = true;
    state.done = false;
    state.opened = false;
    resetProgress();
    showScreen('progress');
    try {
      await api('/api/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          destination,
          createMenu: state.options.createMenu,
          createDesktop: state.options.createDesktop,
          openAfter: state.options.openAfter,
          copyModels: state.options.copyModels,
        }),
      });
      startPolling();
    } catch (error) {
      state.installing = false;
      showScreen('progress');
      $('progressTitle').textContent = 'Não foi possível iniciar';
      $('progressDetail').textContent = error.message;
      $('progressFoot').textContent = 'Revise as opções e tente novamente.';
      $('closeBtn').disabled = false;
      $('retryBack').hidden = false;
      showToast(error.message, 'error');
    }
  }

  function showDone(snapshot) {
    if (state.screen === 'done') return;
    $('doneDestination').textContent = snapshot.destination || state.destination || '—';
    showScreen('done');
    if (state.options.openAfter && !state.opened) {
      state.opened = true;
      setTimeout(openApp, 900);
    }
  }

  async function openApp() {
    if (state.opening) return;
    state.opening = true;
    state.opened = true;
    try {
      await api('/api/open', { method: 'POST' });
      showToast('AUXILIUM iniciado.', 'success');
    } catch (error) {
      state.opened = false;
      showToast('Não foi possível abrir o AUXILIUM: ' + error.message, 'error');
    } finally {
      setTimeout(() => { state.opening = false; }, 1400);
    }
  }

  async function closeInstaller() {
    try {
      await api('/api/quit', { method: 'POST' });
    } catch (_) {
      /* a janela também pode ser fechada pelo botão do sistema */
    }
    window.close();
  }

  async function boot() {
    try {
      const data = await api('/api/status');
      state.env = data.environment;
      state.destination = state.env.defaultDestination;
      $('destination').value = state.destination;
      renderEnvironment();
      renderModels();
      updateDestinationHint();
      const snapshot = data.state;
      if (snapshot && snapshot.done) {
        state.done = true;
        state.options.openAfter = false;
        $('doneDestination').textContent = snapshot.destination || state.destination;
        showScreen('done');
      } else if (snapshot && snapshot.phase === 'running') {
        state.installing = true;
        showScreen('progress');
        updateProgress(snapshot);
        startPolling();
      }
    } catch (error) {
      showToast('Não foi possível consultar o ambiente: ' + error.message, 'error');
    }
  }

  function bindEvents() {
    document.addEventListener('click', (event) => {
      const target = event.target.closest('[data-action]');
      if (!target) return;
      const action = target.dataset.action;
      if (action === 'go-setup') showScreen('setup');
      else if (action === 'go-welcome') showScreen('welcome');
      else if (action === 'default-destination') {
        if (!state.env) return;
        $('destination').value = state.env.defaultDestination;
        updateDestinationHint();
      } else if (action === 'install') startInstall();
      else if (action === 'retry') startInstall();
      else if (action === 'open') openApp();
      else if (action === 'close') closeInstaller();
    });

    $('destination').addEventListener('input', updateDestinationHint);
    $('destination').addEventListener('blur', () => {
      if (!$('destination').value.trim() && state.env) {
        $('destination').value = state.env.defaultDestination;
        updateDestinationHint();
      }
    });
    $('createMenu').addEventListener('change', () => { state.options.createMenu = $('createMenu').checked; });
    $('createDesktop').addEventListener('change', () => { state.options.createDesktop = $('createDesktop').checked; });
    $('openAfter').addEventListener('change', () => { state.options.openAfter = $('openAfter').checked; });

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (state.screen === 'setup') showScreen('welcome');
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    boot();
  });
})();

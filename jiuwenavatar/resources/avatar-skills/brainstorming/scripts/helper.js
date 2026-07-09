(function() {
  const SESSION_TOKEN = window.__BRAINSTORM_TOKEN__ || '';
  const pageParams = new URLSearchParams(window.location.search);
  const tokenQuery = pageParams.get('token') || '';
  const wsParams = new URLSearchParams();
  const effectiveToken = SESSION_TOKEN || tokenQuery;
  if (effectiveToken) wsParams.set('token', effectiveToken);
  const WS_URL = 'ws://' + window.location.host + '/'
    + (wsParams.toString() ? '?' + wsParams.toString() : '');

  let ws = null;
  let eventQueue = [];

  function connect() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      eventQueue.forEach(e => ws.send(JSON.stringify(e)));
      eventQueue = [];
    };

    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.type === 'reload') {
        window.location.reload();
      }
    };

    ws.onclose = () => {
      setTimeout(connect, 1000);
    };
  }

  function sendEvent(event) {
    event.timestamp = Date.now();
    if (effectiveToken) event.token = effectiveToken;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(event));
    } else {
      eventQueue.push(event);
    }
  }

  function setIndicatorMessage(message, selectedLabel) {
    const indicator = document.getElementById('indicator-text');
    if (!indicator) return;
    indicator.textContent = '';
    if (selectedLabel) {
      const selectedSpan = document.createElement('span');
      selectedSpan.className = 'selected-text';
      selectedSpan.textContent = selectedLabel + ' selected';
      indicator.appendChild(selectedSpan);
      indicator.appendChild(document.createTextNode(' — return to terminal to continue'));
    } else {
      indicator.textContent = message;
    }
  }

  document.addEventListener('click', (e) => {
    const target = e.target.closest('[data-choice]');
    if (!target) return;

    sendEvent({
      type: 'click',
      text: target.textContent.trim(),
      choice: target.dataset.choice,
      id: target.id || null
    });

    setTimeout(() => {
      const container = target.closest('.options') || target.closest('.cards');
      const selected = container ? container.querySelectorAll('.selected') : [];
      if (selected.length === 0) {
        setIndicatorMessage('Click an option above, then return to the terminal');
      } else if (selected.length === 1) {
        const label = selected[0].querySelector('h3, .content h3, .card-body h3')?.textContent?.trim()
          || selected[0].dataset.choice;
        setIndicatorMessage(null, label);
      } else {
        setIndicatorMessage(null, String(selected.length));
      }
    }, 0);
  });

  window.selectedChoice = null;

  window.toggleSelect = function(el) {
    const container = el.closest('.options') || el.closest('.cards');
    const multi = container && container.dataset.multiselect !== undefined;
    if (container && !multi) {
      container.querySelectorAll('.option, .card').forEach(o => o.classList.remove('selected'));
    }
    if (multi) {
      el.classList.toggle('selected');
    } else {
      el.classList.add('selected');
    }
    window.selectedChoice = el.dataset.choice;
  };

  window.brainstorm = {
    send: sendEvent,
    choice: (value, metadata = {}) => sendEvent({
      type: 'choice',
      choice: value,
      value,
      ...metadata
    })
  };

  connect();
})();

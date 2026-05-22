// Interface em português conforme decisão operacional do projeto.

// --- Funções auxiliares de localStorage ---

function isLocalStorageAvailable() {
  try {
    const chave = '__teste_ls__';
    localStorage.setItem(chave, '1');
    localStorage.removeItem(chave);
    return true;
  } catch (e) {
    return false;
  }
}

function lerDoLocalStorage(chave) {
  try {
    const item = localStorage.getItem(chave);
    return item ? JSON.parse(item) : null;
  } catch (e) {
    return null;
  }
}

function salvarNoLocalStorage(chave, valor) {
  try {
    localStorage.setItem(chave, JSON.stringify(valor));
  } catch (e) {
    // Falha silenciosa — app continua funcionando em memória
  }
}

// --- Estado da aplicação ---

let localStorageDisponivel = false;
let tarefas = [];

// --- Analytics ---

function inicializarTracking() {
  if (!localStorageDisponivel) return;

  // sv-SE é usado aqui porque produz datas no formato "YYYY-MM-DD" no timezone
  // local do browser, ao contrário de toISOString() que usa UTC e pode retornar
  // a data de ontem ou de amanhã dependendo do fuso horário do usuário.
  const hoje = new Date().toLocaleDateString('sv-SE');
  const agora = new Date().toISOString();

  let dados = lerDoLocalStorage('analytics');

  if (!dados) {
    dados = {
      first_visit_at: agora,
      last_visit_at: agora,
      visit_count: 1,
      visit_dates: [hoje]
    };
  } else {
    dados.last_visit_at = agora;
    dados.visit_count += 1;
    if (!dados.visit_dates.includes(hoje)) {
      dados.visit_dates.push(hoje);
    }
    // first_visit_at nunca é sobrescrito após definido
  }

  salvarNoLocalStorage('analytics', dados);
}

// --- Renderização ---

function renderizarLista() {
  const lista = document.getElementById('task-list');
  const estadoVazio = document.getElementById('empty-state');

  lista.innerHTML = '';

  if (tarefas.length === 0) {
    estadoVazio.hidden = false;
    return;
  }

  estadoVazio.hidden = true;

  tarefas.forEach(tarefa => {
    const li = document.createElement('li');
    li.className = 'item-tarefa' + (tarefa.completed ? ' concluida' : '');
    li.dataset.id = tarefa.id;

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = tarefa.completed;
    checkbox.setAttribute('aria-label', 'Marcar como concluída');

    const texto = document.createElement('span');
    texto.className = 'texto-tarefa';
    texto.textContent = tarefa.title;

    checkbox.addEventListener('change', () => alternarConclusao(tarefa.id));

    li.appendChild(checkbox);
    li.appendChild(texto);
    lista.appendChild(li);
  });
}

// --- Ações ---

function adicionarTarefa() {
  const campo = document.getElementById('task-input');
  const texto = campo.value;

  if (texto.trim().length === 0) {
    campo.value = '';
    campo.focus();
    return;
  }

  const id = Date.now().toString() + Math.random().toString(36).slice(2);

  const novaTarefa = {
    id,
    title: texto.trim(),
    completed: false,
    created_at: new Date().toISOString()
  };

  tarefas.push(novaTarefa);
  salvarNoLocalStorage('tasks', tarefas);
  renderizarLista();

  campo.value = '';
  campo.focus();
}

function alternarConclusao(id) {
  const tarefa = tarefas.find(t => t.id === id);
  if (!tarefa) return;

  tarefa.completed = !tarefa.completed;

  salvarNoLocalStorage('tasks', tarefas);
  renderizarLista();
}

// --- Inicialização ---

document.addEventListener('DOMContentLoaded', () => {
  localStorageDisponivel = isLocalStorageAvailable();

  if (!localStorageDisponivel) {
    document.getElementById('storage-warning').hidden = false;
  }

  // Tracking executado antes de qualquer renderização
  inicializarTracking();

  if (localStorageDisponivel) {
    tarefas = lerDoLocalStorage('tasks') || [];
  }

  renderizarLista();

  const campo = document.getElementById('task-input');
  campo.focus();

  campo.addEventListener('keydown', e => {
    if (e.key === 'Enter') adicionarTarefa();
  });

  document.getElementById('add-button').addEventListener('click', adicionarTarefa);
});

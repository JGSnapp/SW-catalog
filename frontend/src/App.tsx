import { useEffect, useMemo, useState, type FormEvent } from 'react';

// ---------- types ----------

type ProjectStatus = 'planning' | 'in_progress' | 'review' | 'completed' | 'archived';
type SupplierStatus = 'new' | 'verified' | 'preferred' | 'rejected' | 'contacted';

type ProjectItem = {
  id: string;
  name: string;
  specification: string;
  quantity: number;
  unit: string;
  target_price: string;
  notes: string;
  image_url: string;
  monitoring_enabled: boolean;
  ai_notes: string;
  created_at: string;
  updated_at: string;
};

type ProjectRecord = {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  target_volume: string;
  budget: string;
  currency: string;
  category: string;
  cover_image_url: string;
  items: ProjectItem[];
  created_at: string;
  updated_at: string;
};

type SupplierRecord = {
  id: string;
  project_id: string;
  item_id: string;
  name: string;
  offer_title: string;
  price: number | null;
  price_text: string;
  currency: string;
  lead_time: string;
  country: string;
  category: string;
  description: string;
  terms: string;
  restrictions: string;
  url: string;
  source_url: string;
  contact: string;
  image_url: string;
  status: SupplierStatus;
  is_existing: boolean;
  monitoring_enabled: boolean;
  discovered_at: string;
  updated_at: string;
  last_checked_at: string | null;
  ai_notes: string;
};

type SourceSite = {
  id: string;
  label: string;
  url: string;
  enabled: boolean;
  category: string;
  notes: string;
};

type AppConfig = {
  company_profile: string;
  global_prompt: string;
  default_currency: string;
  monitored_categories: string;
  preferred_regions: string;
  excluded_regions: string;
  max_lead_time: string;
  discovery_iterations: number;
  monitor_iterations: number;
  monitor_interval_hours: number;
  sites: SourceSite[];
};

type RunRecord = {
  id: string;
  kind: 'item_discovery' | 'item_monitor' | 'upload_parse' | 'image_search';
  project_id: string | null;
  item_id: string | null;
  label: string;
  started_at: string;
  finished_at: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed';
  summary: string;
  error: string | null;
};

type UploadRecord = {
  id: string;
  name: string;
  kind: 'text' | 'table' | 'file';
  size: number;
  received_at: string;
  parsed_at: string | null;
  status: 'received' | 'parsing' | 'parsed' | 'failed';
  summary: string;
  error: string | null;
  created_project_ids: string[];
};

type SupplierChange = {
  id: string;
  supplier_id: string;
  project_id: string;
  item_id: string;
  supplier_name: string;
  item_name: string;
  change_type: 'price_up' | 'price_down' | 'stock' | 'lead_time' | 'terms' | 'added' | 'removed' | 'info';
  old_value: string;
  new_value: string;
  summary: string;
  detected_at: string;
};

type AppState = {
  config: AppConfig;
  projects: ProjectRecord[];
  suppliers: SupplierRecord[];
  runs: RunRecord[];
  uploads: UploadRecord[];
  changes: SupplierChange[];
  stats: Record<string, number | string>;
};

type View = 'dashboard' | 'projects' | 'project' | 'suppliers' | 'reports' | 'settings';

type UserPublic = {
  id: string;
  email: string;
  name: string;
  created_at: string;
};

type AuthResponse = {
  user: UserPublic;
  token: string;
};

// ---------- constants ----------

const API_BASE = (process.env.REACT_APP_API_BASE || '/api').replace(/\/$/, '');
const AUTH_TOKEN_KEY = 'sw_catalog_token';

const STATUS_LABELS: Record<ProjectStatus, string> = {
  planning: 'В планах',
  in_progress: 'В работе',
  review: 'На проверке',
  completed: 'Готово',
  archived: 'Архив',
};

const SUPPLIER_STATUS_LABELS: Record<SupplierStatus, string> = {
  new: 'Новый',
  verified: 'Проверен',
  preferred: 'Предпочтительный',
  rejected: 'Отклонён',
  contacted: 'Запросили',
};

const NAV_ITEMS: { id: View; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Главная', icon: '◧' },
  { id: 'projects', label: 'Проекты', icon: '▦' },
  { id: 'suppliers', label: 'Поставщики', icon: '◎' },
  { id: 'reports', label: 'Отчёты', icon: '◈' },
  { id: 'settings', label: 'Настройки', icon: '◐' },
];

const APP_CURRENCY = 'RUB';
const APP_CURRENCY_SYMBOL = '₽';

// ---------- helpers ----------

const formatMoney = (value: number, _currency = APP_CURRENCY) => {
  const formatted = value.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
  return `${formatted} ${APP_CURRENCY_SYMBOL}`;
};

const formatDate = (value: string | null | undefined) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(date);
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = window.localStorage.getItem(AUTH_TOKEN_KEY);
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `Request failed: ${response.status}`;
    try {
      const payload = JSON.parse(text);
      message = payload.detail || message;
    } catch {
      // Keep the raw response text when the backend does not return JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) return null as T;
  return response.json() as Promise<T>;
}

const emptyConfig: AppConfig = {
  company_profile: '',
  global_prompt: '',
  default_currency: APP_CURRENCY,
  monitored_categories: '',
  preferred_regions: '',
  excluded_regions: '',
  max_lead_time: '',
  discovery_iterations: 10,
  monitor_iterations: 6,
  monitor_interval_hours: 24,
  sites: [],
};

const emptyProjectDraft = {
  name: '',
  description: '',
  status: 'planning' as ProjectStatus,
  target_volume: '',
  budget: '',
  currency: APP_CURRENCY,
  category: '',
  items: [{ name: '', specification: '', quantity: 1, unit: 'шт', target_price: '', notes: '' }],
};

// ---------- App ----------

export default function App() {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [state, setState] = useState<AppState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [view, setView] = useState<View>('dashboard');
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);

  const loadState = async () => {
    const payload = await request<AppState>('/state');
    setState(payload);
  };

  useEffect(() => {
    let active = true;
    (async () => {
      const token = window.localStorage.getItem(AUTH_TOKEN_KEY);
      if (!token) {
        if (active) {
          setAuthChecked(true);
          setLoading(false);
        }
        return;
      }
      try {
        const me = await request<UserPublic>('/auth/me');
        if (active) setUser(me);
      } catch {
        window.localStorage.removeItem(AUTH_TOKEN_KEY);
      } finally {
        if (active) setAuthChecked(true);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!authChecked) return;
    if (!user) {
      setState(null);
      setLoading(false);
      return;
    }
    let active = true;
    (async () => {
      try {
        setLoading(true);
        await loadState();
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Ошибка загрузки данных');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [authChecked, user]);

  // Poll while any run is in flight.
  useEffect(() => {
    if (!state) return;
    const hasActiveRun = state.runs.some((run) => run.status === 'queued' || run.status === 'running');
    if (!hasActiveRun) return;
    const interval = window.setInterval(() => {
      loadState().catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(interval);
  }, [state]);

  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 3500);
    return () => window.clearTimeout(t);
  }, [notice]);

  const openProject = (projectId: string) => {
    setSelectedProjectId(projectId);
    setSelectedItemId(null);
    setView('project');
  };

  const selectedProject = useMemo(
    () => state?.projects.find((p) => p.id === selectedProjectId) || null,
    [state?.projects, selectedProjectId]
  );

  const handleAuthenticated = (payload: AuthResponse) => {
    window.localStorage.setItem(AUTH_TOKEN_KEY, payload.token);
    setUser(payload.user);
    setError(null);
    setNotice(null);
  };

  const logout = async () => {
    try {
      await request('/auth/logout', { method: 'POST' });
    } catch {
      // Local logout must work even if the token has already expired server-side.
    }
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    setUser(null);
    setState(null);
    setView('dashboard');
  };

  if (!authChecked || loading) {
    return (
      <div className="app-shell">
        <div className="centered">Загрузка SW-catalog...</div>
      </div>
    );
  }

  if (!user) {
    return <AuthLanding onAuthenticated={handleAuthenticated} />;
  }

  return (
    <div className="app-shell">
      <Sidebar view={view} onNavigate={(next) => { setView(next); }} />
      <main className="app-main">
        <TopBar user={user} onLogout={logout} />
        {error ? <div className="banner error-banner">{error}</div> : null}
        {notice ? <div className="banner success-banner">{notice}</div> : null}

        {view === 'dashboard' && state ? (
          <DashboardView
            state={state}
            onError={setError}
            onNotice={setNotice}
            onReload={loadState}
            onOpenProject={openProject}
          />
        ) : null}

        {view === 'projects' && state ? (
          <ProjectsView
            state={state}
            onOpenProject={openProject}
            onError={setError}
            onNotice={setNotice}
            onReload={loadState}
          />
        ) : null}

        {view === 'project' && state ? (
          selectedProject ? (
            <ProjectDetailView
              state={state}
              project={selectedProject}
              selectedItemId={selectedItemId}
              onSelectItem={setSelectedItemId}
              onError={setError}
              onNotice={setNotice}
              onReload={loadState}
              onBack={() => setView('projects')}
            />
          ) : (
            <div className="empty-state">
              <h3>Проект не выбран</h3>
              <p>Откройте проект из списка.</p>
              <button className="primary-button" onClick={() => setView('projects')}>К проектам</button>
            </div>
          )
        ) : null}

        {view === 'suppliers' && state ? (
          <SuppliersView state={state} onError={setError} onNotice={setNotice} onReload={loadState} />
        ) : null}

        {view === 'reports' && state ? <ReportsView state={state} /> : null}

        {view === 'settings' && state ? (
          <SettingsView state={state} onError={setError} onNotice={setNotice} onReload={loadState} />
        ) : null}
      </main>
    </div>
  );
}

// ---------- Landing / Auth ----------

function AuthLanding({ onAuthenticated }: { onAuthenticated: (payload: AuthResponse) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = await request<AuthResponse>(mode === 'login' ? '/auth/login' : '/auth/register', {
        method: 'POST',
        body: JSON.stringify({ email, password, name }),
      });
      onAuthenticated(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Не удалось войти');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="landing-page">
      <section className="landing-hero">
        <div className="landing-copy">
          <img className="landing-logo" src="/logo.png" alt="SW-catalog" />
          <p className="section-kicker">Procurement OS</p>
          <h1>SW-catalog</h1>
          <p>
            Рабочее пространство для закупок fashion-команд: проекты, BOM, поставщики, мониторинг цен и
            агентский поиск альтернатив в одном кабинете.
          </p>
          <div className="landing-metrics">
            <span>Проекты</span>
            <span>Поставщики</span>
            <span>Мониторинг</span>
          </div>
        </div>

        <form className="auth-panel" onSubmit={submit}>
          <div className="auth-tabs" role="tablist" aria-label="Авторизация">
            <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>
              Вход
            </button>
            <button type="button" className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>
              Регистрация
            </button>
          </div>
          <h2>{mode === 'login' ? 'Войти в аккаунт' : 'Создать аккаунт'}</h2>
          {mode === 'register' ? (
            <label className="field">
              <span>Имя</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Анна" />
            </label>
          ) : null}
          <label className="field">
            <span>Email</span>
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" required />
          </label>
          <label className="field">
            <span>Пароль</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Минимум 6 символов"
              required
              minLength={6}
            />
          </label>
          {error ? <div className="auth-error">{error}</div> : null}
          <button className="primary-button" type="submit" disabled={busy || !email.trim() || password.length < 6}>
            {busy ? 'Подключаю...' : mode === 'login' ? 'Войти' : 'Зарегистрироваться'}
          </button>
        </form>
      </section>
    </main>
  );
}

// ---------- Sidebar / TopBar ----------

function Sidebar({ view, onNavigate }: { view: View; onNavigate: (next: View) => void }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <img className="brand-logo" src="/logo.png" alt="SW-catalog" />
        <div>
          <div className="brand-name">SW-catalog</div>
          <div className="brand-sub">Procurement OS</div>
        </div>
      </div>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`sidebar-link ${view === item.id || (item.id === 'projects' && view === 'project') ? 'sidebar-link-active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="sidebar-icon">{item.icon}</span>
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-foot">
        <div className="sidebar-brand-card">
          <div className="brand-mark mini">FA</div>
          <div>
            <div className="brand-name small">Fashion Atelier</div>
            <div className="brand-sub">SS26 · capsule</div>
          </div>
        </div>
        <div className="sidebar-status">Подписка активна</div>
      </div>
    </aside>
  );
}

function TopBar({ user, onLogout }: { user: UserPublic; onLogout: () => void }) {
  const initials = (user.name || user.email).slice(0, 2).toUpperCase();
  return (
    <header className="top-bar">
      <div className="top-bar-left">
        <input className="top-search" placeholder="Поиск проектов, позиций, поставщиков..." />
      </div>
      <div className="top-bar-right">
        <button className="icon-button" title="Обновить">↻</button>
        <div className="user-chip">
          <div className="user-avatar">{initials}</div>
          <div>
            <div className="user-name">{user.name || user.email}</div>
            <div className="user-role">{user.email}</div>
          </div>
        </div>
        <button className="ghost-button small" type="button" onClick={onLogout}>Выйти</button>
      </div>
    </header>
  );
}

// ---------- Dashboard view ----------

function DashboardView(props: {
  state: AppState;
  onError: (value: string | null) => void;
  onNotice: (value: string | null) => void;
  onReload: () => Promise<void>;
  onOpenProject: (id: string) => void;
}) {
  const { state, onError, onNotice, onReload, onOpenProject } = props;
  const stats = state.stats;
  const spent = Number(stats.spent_estimate || 0);
  const savings = Number(stats.savings_estimate || 0);
  const savingsPct = Number(stats.savings_pct || 0);
  const activeProjects = Number(stats.projects_active || 0);
  const suppliersMonitored = Number(stats.suppliers_monitored || 0);
  const suppliersTotal = Number(stats.suppliers_total || 0);

  return (
    <div className="page">
      <UploadCard onError={onError} onNotice={onNotice} onReload={onReload} />

      <section className="stats-grid">
        <StatCard
          label="Оценка закупок"
          value={formatMoney(spent)}
          hint={savings ? `${formatMoney(savings)} (${savingsPct}%) экономии vs текущих условий` : 'Загрузите данные, чтобы посчитать экономию'}
          accent="warm"
        />
        <StatCard
          label="Поставщиков"
          value={`${suppliersTotal}`}
          hint={`${suppliersMonitored} под мониторингом`}
          accent="cool"
        />
        <StatCard
          label="Активных проектов"
          value={`${activeProjects}`}
          hint={`${stats.projects_total || 0} всего`}
          accent="violet"
        />
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Каталог</p>
            <h2>Проекты и изделия</h2>
          </div>
          <CreateProjectButton onCreated={onReload} onError={onError} onNotice={onNotice} />
        </div>
        <ProjectsTable projects={state.projects} suppliers={state.suppliers} onOpen={onOpenProject} />
      </section>

      {state.changes.length ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">Мониторинг</p>
              <h2>Изменения по отслеживаемым поставщикам</h2>
            </div>
          </div>
          <ChangesTable changes={state.changes.slice(0, 8)} />
        </section>
      ) : null}
    </div>
  );
}

function StatCard({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: 'warm' | 'cool' | 'violet' }) {
  return (
    <div className={`stat-card stat-card-${accent || 'warm'}`}>
      <div className="stat-card-label">{label}</div>
      <div className="stat-card-value">{value}</div>
      {hint ? <div className="stat-card-hint">{hint}</div> : null}
    </div>
  );
}

// ---------- Upload card ----------

function UploadCard(props: { onError: (v: string | null) => void; onNotice: (v: string | null) => void; onReload: () => Promise<void> }) {
  const [text, setText] = useState('');
  const [name, setName] = useState('Импорт закупочной выгрузки');
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const uploadKind = /\.(csv|tsv|xlsx?|xml|json)$/i.test(name) ? 'table' : 'text';

  const submit = async () => {
    if (!text.trim()) {
      props.onError('Вставьте текст или таблицу для парсинга.');
      return;
    }
    setBusy(true);
    props.onError(null);
    try {
      await request('/uploads', {
        method: 'POST',
        body: JSON.stringify({ name: name || 'Импорт закупочной выгрузки', kind: uploadKind, content: text }),
      });
      setText('');
      setName('Импорт закупочной выгрузки');
      props.onNotice('Файл принят. ИИ-агент сейчас разнесёт его в проекты и позиции.');
      await props.onReload();
    } catch (err) {
      props.onError(err instanceof Error ? err.message : 'Ошибка загрузки');
    } finally {
      setBusy(false);
    }
  };

  const consumeFile = async (file: File) => {
    const content = await file.text();
    setName(file.name);
    setText(content);
  };

  const handleFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) await consumeFile(file);
  };

  const handleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) await consumeFile(file);
  };

  return (
    <section className="upload-card">
      <div className="upload-card-head">
        <div>
          <p className="section-kicker">Загрузка данных</p>
          <h2>Загрузите данные по закупкам</h2>
          <p className="upload-hint">
            Вставьте таблицу BOM, прайс, текст с описанием или сделайте выгрузку из вашей системы — ИИ-агент
            автоматически разнесёт это по проектам, позициям и поставщикам.
          </p>
        </div>
      </div>

      <div
        className={`upload-dropzone ${dragOver ? 'upload-dropzone-active' : ''}`}
        onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <div className="upload-format-row">
          <span className="upload-format upload-format-bom">BOM</span>
          <span className="upload-format upload-format-xls">XLS</span>
          <span className="upload-format upload-format-csv">CSV</span>
          <span className="upload-format upload-format-pdf">PDF</span>
          <span className="upload-format upload-format-img">PNG</span>
          <span className="upload-format upload-format-json">JSON</span>
        </div>
        <div className="upload-dropzone-text">
          <strong>Перетащите файл сюда или выберите документ</strong>
          <span>Поддерживаются TXT, CSV, TSV, JSON, MD. Большие файлы парсятся фоновым ИИ-агентом.</span>
        </div>
        <label className="file-input">
          <input type="file" accept=".csv,.tsv,.txt,.md,.json,.xml" onChange={handleFile} />
          <span>Выбрать файл</span>
        </label>
      </div>

      <div className="upload-card-body">
        <textarea
          className="upload-textarea"
          rows={5}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`Жакет oversize SS26\n- основная ткань: шерсть 350 г/м², 320 м, цель 1 800 ₽/м, поставщик TextilePro Italy 2 200 ₽/м\n- подкладка: вискоза 120 г/м², 280 м\n- пуговицы: рог, 12 шт, поставщик Fornituris 40 ₽/шт`}
        />
      </div>

      <div className="upload-card-actions">
        <button className="primary-button" disabled={busy || !text.trim()} onClick={submit}>
          {busy ? 'Парсю...' : 'Запустить ИИ-парсер'}
        </button>
      </div>
    </section>
  );
}

// ---------- Projects table ----------

function ProjectsTable({ projects, suppliers, onOpen }: { projects: ProjectRecord[]; suppliers: SupplierRecord[]; onOpen: (id: string) => void }) {
  if (!projects.length) {
    return (
      <div className="empty-state">
        <h3>Проектов пока нет</h3>
        <p>Загрузите файл выше или создайте проект вручную.</p>
      </div>
    );
  }
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Проект</th>
            <th>Статус</th>
            <th>Компоненты</th>
            <th>Поставщики</th>
            <th>Бюджет</th>
          </tr>
        </thead>
        <tbody>
          {projects.map((project) => {
            const projectSuppliers = suppliers.filter((s) => s.project_id === project.id);
            return (
              <tr key={project.id} onClick={() => onOpen(project.id)}>
                <td>
                  <div className="project-row">
                    <div className="project-thumb">
                      {project.cover_image_url ? (
                        <img src={project.cover_image_url} alt="" />
                      ) : (
                        <span>{(project.name[0] || '?').toUpperCase()}</span>
                      )}
                    </div>
                    <div>
                      <strong>{project.name}</strong>
                      <p>{project.category || project.description.slice(0, 60) || '—'}</p>
                    </div>
                  </div>
                </td>
                <td><StatusPill status={project.status} /></td>
                <td>{project.items.length}</td>
                <td>{projectSuppliers.length}</td>
                <td>{project.budget || '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusPill({ status }: { status: ProjectStatus }) {
  return <span className={`status-pill status-${status}`}>{STATUS_LABELS[status]}</span>;
}

// ---------- Create project ----------

function CreateProjectButton(props: { onCreated: () => Promise<void>; onError: (v: string | null) => void; onNotice: (v: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(emptyProjectDraft);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft.name.trim()) return;
    setBusy(true);
    try {
      await request('/projects', { method: 'POST', body: JSON.stringify(draft) });
      props.onNotice('Проект создан.');
      setDraft(emptyProjectDraft);
      setOpen(false);
      await props.onCreated();
    } catch (err) {
      props.onError(err instanceof Error ? err.message : 'Не удалось создать проект');
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="primary-button" type="button" onClick={() => setOpen(true)}>
        + Добавить проект
      </button>
    );
  }

  return (
    <div className="modal-backdrop" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h3>Новый проект</h3>
        <form className="stack-form" onSubmit={submit}>
          <label className="field">
            <span>Название</span>
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Жакет oversize SS26" />
          </label>
          <label className="field">
            <span>Описание</span>
            <textarea rows={3} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Какая коллекция, тираж, дедлайн" />
          </label>
          <div className="field-row">
            <label className="field">
              <span>Статус</span>
              <select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as ProjectStatus })}>
                {(Object.entries(STATUS_LABELS) as Array<[ProjectStatus, string]>).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Бюджет</span>
              <input value={draft.budget} onChange={(e) => setDraft({ ...draft, budget: e.target.value })} placeholder="800 000 ₽" />
            </label>
          </div>
          <div className="field-row">
            <label className="field">
              <span>Тираж</span>
              <input value={draft.target_volume} onChange={(e) => setDraft({ ...draft, target_volume: e.target.value })} placeholder="240 шт" />
            </label>
            <label className="field">
              <span>Категория</span>
              <input value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} placeholder="Outerwear" />
            </label>
          </div>
          <div className="modal-items">
            <strong>Состав (компоненты)</strong>
            {draft.items.map((item, index) => (
              <div key={index} className="component-row">
                <input
                  value={item.name}
                  onChange={(e) => {
                    const items = [...draft.items];
                    items[index] = { ...item, name: e.target.value };
                    setDraft({ ...draft, items });
                  }}
                  placeholder="Название позиции"
                />
                <input
                  value={item.specification}
                  onChange={(e) => {
                    const items = [...draft.items];
                    items[index] = { ...item, specification: e.target.value };
                    setDraft({ ...draft, items });
                  }}
                  placeholder="Спецификация"
                />
                <input
                  type="number"
                  value={item.quantity}
                  onChange={(e) => {
                    const items = [...draft.items];
                    items[index] = { ...item, quantity: Number(e.target.value) };
                    setDraft({ ...draft, items });
                  }}
                />
              </div>
            ))}
            <button
              type="button"
              className="secondary-button"
              onClick={() =>
                setDraft({
                  ...draft,
                  items: [...draft.items, { name: '', specification: '', quantity: 1, unit: 'шт', target_price: '', notes: '' }],
                })
              }
            >
              + позиция
            </button>
          </div>
          <div className="modal-actions">
            <button type="button" className="ghost-button" onClick={() => setOpen(false)}>Отмена</button>
            <button type="submit" className="primary-button" disabled={busy || !draft.name.trim()}>
              {busy ? 'Сохраняю...' : 'Создать проект'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ---------- Projects view ----------

function ProjectsView(props: {
  state: AppState;
  onOpenProject: (id: string) => void;
  onError: (v: string | null) => void;
  onNotice: (v: string | null) => void;
  onReload: () => Promise<void>;
}) {
  return (
    <div className="page">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Каталог</p>
            <h2>Все проекты</h2>
          </div>
          <CreateProjectButton onCreated={props.onReload} onError={props.onError} onNotice={props.onNotice} />
        </div>
        <ProjectsTable projects={props.state.projects} suppliers={props.state.suppliers} onOpen={props.onOpenProject} />
      </section>
    </div>
  );
}

// ---------- Project detail ----------

function ProjectDetailView(props: {
  state: AppState;
  project: ProjectRecord;
  selectedItemId: string | null;
  onSelectItem: (id: string | null) => void;
  onError: (v: string | null) => void;
  onNotice: (v: string | null) => void;
  onReload: () => Promise<void>;
  onBack: () => void;
}) {
  const { project, state, selectedItemId, onSelectItem, onError, onNotice, onReload, onBack } = props;
  const selectedItem = useMemo(
    () => project.items.find((item) => item.id === selectedItemId) || project.items[0] || null,
    [project.items, selectedItemId]
  );

  useEffect(() => {
    if (!selectedItemId && project.items[0]) onSelectItem(project.items[0].id);
  }, [project.id, project.items, selectedItemId, onSelectItem]);

  const projectSuppliers = state.suppliers.filter((s) => s.project_id === project.id);
  const itemSuppliers = selectedItem ? projectSuppliers.filter((s) => s.item_id === selectedItem.id) : [];

  const itemSavingsForProject = useMemo(() => {
    let best = 0;
    let baseline = 0;
    project.items.forEach((item) => {
      const sup = projectSuppliers.filter((s) => s.item_id === item.id && s.price != null);
      if (!sup.length) return;
      const minPrice = Math.min(...sup.map((s) => s.price as number));
      const maxPrice = Math.max(...sup.map((s) => s.price as number));
      const qty = item.quantity || 0;
      best += minPrice * qty;
      baseline += maxPrice * qty;
    });
    return { best, baseline, saved: Math.max(0, baseline - best) };
  }, [project.items, projectSuppliers]);

  return (
    <div className="page">
      <div className="breadcrumb">
        <button className="link-button" onClick={onBack}>← Проекты</button>
        <span>/</span>
        <span>{project.name}</span>
      </div>

      <section className="project-header">
        <div>
          <p className="section-kicker">{project.category || 'Проект'}</p>
          <h1>{project.name}</h1>
          <p className="project-desc">{project.description || 'Описание не заполнено.'}</p>
        </div>
        <StatusPill status={project.status} />
      </section>

      <div className="project-stats">
        <Mini label="Компоненты" value={`${project.items.length}`} />
        <Mini label="Поставщики" value={`${projectSuppliers.length}`} />
        <Mini label="На мониторинге" value={`${projectSuppliers.filter((s) => s.monitoring_enabled).length}`} />
        <Mini label="Оценка закупки" value={formatMoney(itemSavingsForProject.best, project.currency)} accent="warm" />
        <Mini label="Сэкономлено" value={formatMoney(itemSavingsForProject.saved, project.currency)} accent="cool" />
        <Mini label="Бюджет" value={project.budget || '—'} />
      </div>

      <section className="project-grid">
        <ItemsColumn
          project={project}
          suppliers={projectSuppliers}
          selectedItemId={selectedItem?.id || null}
          onSelect={(id) => onSelectItem(id)}
          onError={onError}
          onNotice={onNotice}
          onReload={onReload}
        />
        <ItemDetailColumn
          project={project}
          item={selectedItem}
          itemSuppliers={itemSuppliers}
          onError={onError}
          onNotice={onNotice}
          onReload={onReload}
        />
        <SuppliersColumn
          project={project}
          item={selectedItem}
          suppliers={itemSuppliers}
          onError={onError}
          onNotice={onNotice}
          onReload={onReload}
        />
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Мониторинг</p>
            <h2>Изменения по отслеживаемым поставщикам</h2>
          </div>
        </div>
        <ChangesTable
          changes={state.changes.filter((change) => change.project_id === project.id).slice(0, 10)}
        />
      </section>
    </div>
  );
}

function Mini({ label, value, accent }: { label: string; value: string; accent?: 'warm' | 'cool' }) {
  return (
    <div className={`mini-stat mini-${accent || 'neutral'}`}>
      <div className="mini-label">{label}</div>
      <div className="mini-value">{value}</div>
    </div>
  );
}

function ItemsColumn(props: {
  project: ProjectRecord;
  suppliers: SupplierRecord[];
  selectedItemId: string | null;
  onSelect: (id: string) => void;
  onError: (v: string | null) => void;
  onNotice: (v: string | null) => void;
  onReload: () => Promise<void>;
}) {
  const { project, suppliers, selectedItemId, onSelect, onError, onNotice, onReload } = props;
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: '', specification: '', quantity: 1, unit: 'шт', target_price: '' });

  const addItem = async () => {
    if (!draft.name.trim()) return;
    try {
      await request(`/projects/${project.id}/items`, {
        method: 'POST',
        body: JSON.stringify({ ...draft, notes: '', image_url: '', monitoring_enabled: true }),
      });
      setDraft({ name: '', specification: '', quantity: 1, unit: 'шт', target_price: '' });
      setAdding(false);
      onNotice('Позиция добавлена.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось добавить позицию');
    }
  };

  return (
    <div className="column">
      <div className="column-head">
        <h3>Компоненты товара</h3>
        <button className="secondary-button small" onClick={() => setAdding(!adding)}>{adding ? '×' : '+ позиция'}</button>
      </div>
      {adding ? (
        <div className="inline-form">
          <input placeholder="Название" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <input placeholder="Спецификация" value={draft.specification} onChange={(e) => setDraft({ ...draft, specification: e.target.value })} />
          <div className="inline-row">
            <input type="number" value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: Number(e.target.value) })} />
            <input value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value })} />
            <input placeholder="Цель, ₽" value={draft.target_price} onChange={(e) => setDraft({ ...draft, target_price: e.target.value })} />
          </div>
          <button className="primary-button small" onClick={addItem}>Добавить</button>
        </div>
      ) : null}
      <div className="item-list">
        {project.items.map((item) => {
          const itemSuppliers = suppliers.filter((s) => s.item_id === item.id);
          const minPrice = itemSuppliers
            .map((s) => s.price)
            .filter((p): p is number => p != null)
            .reduce((acc, value) => Math.min(acc, value), Infinity);
          return (
            <button
              key={item.id}
              type="button"
              className={`item-row ${selectedItemId === item.id ? 'item-row-active' : ''}`}
              onClick={() => onSelect(item.id)}
            >
              <div className="item-thumb">
                {item.image_url ? <img src={item.image_url} alt="" /> : <span>{item.name[0] || '?'}</span>}
              </div>
              <div className="item-body">
                <div className="item-name">{item.name}</div>
                <div className="item-spec">{item.specification || 'Спецификация не указана'}</div>
                <div className="item-meta">
                  <span>{itemSuppliers.length} поставщ.</span>
                  <span>{item.quantity} {item.unit}</span>
                </div>
              </div>
              <div className="item-price">
                {Number.isFinite(minPrice) ? formatMoney(minPrice as number, project.currency) : item.target_price || '—'}
              </div>
            </button>
          );
        })}
        {!project.items.length ? <div className="empty-state small"><p>Нет позиций. Добавьте первую.</p></div> : null}
      </div>
    </div>
  );
}

function ItemDetailColumn(props: {
  project: ProjectRecord;
  item: ProjectItem | null;
  itemSuppliers: SupplierRecord[];
  onError: (v: string | null) => void;
  onNotice: (v: string | null) => void;
  onReload: () => Promise<void>;
}) {
  const { project, item, itemSuppliers, onError, onNotice, onReload } = props;
  const [busy, setBusy] = useState(false);

  if (!item) {
    return (
      <div className="column">
        <div className="column-head"><h3>Деталь позиции</h3></div>
        <div className="empty-state small"><p>Выберите позицию слева.</p></div>
      </div>
    );
  }

  const runDiscovery = async () => {
    setBusy(true);
    try {
      await request(`/projects/${project.id}/items/${item.id}/discover`, { method: 'POST' });
      onNotice('ИИ-агент ищет новых поставщиков для этой позиции.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось запустить агента');
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm('Удалить позицию и связанных поставщиков?')) return;
    try {
      await request(`/projects/${project.id}/items/${item.id}`, { method: 'DELETE' });
      onNotice('Позиция удалена.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось удалить');
    }
  };

  const minPrice = itemSuppliers
    .map((s) => s.price)
    .filter((p): p is number => p != null)
    .reduce((acc, value) => Math.min(acc, value), Infinity);
  const maxPrice = itemSuppliers
    .map((s) => s.price)
    .filter((p): p is number => p != null)
    .reduce((acc, value) => Math.max(acc, value), 0);

  return (
    <div className="column">
      <div className="column-head">
        <h3>{item.name}</h3>
        <button className="ghost-button small" onClick={remove}>Удалить</button>
      </div>
      {item.image_url ? <img className="item-hero-image" src={item.image_url} alt={item.name} /> : null}
      <dl className="detail-list">
        <div><dt>Спецификация</dt><dd>{item.specification || '—'}</dd></div>
        <div><dt>Количество</dt><dd>{item.quantity} {item.unit}</dd></div>
        <div><dt>Целевая цена</dt><dd>{item.target_price || '—'}</dd></div>
        <div><dt>Заметки</dt><dd>{item.notes || '—'}</dd></div>
        <div><dt>Диапазон цен</dt><dd>{Number.isFinite(minPrice) ? `${formatMoney(minPrice as number, project.currency)} … ${formatMoney(maxPrice, project.currency)}` : '—'}</dd></div>
      </dl>
      <div className="item-detail-actions">
        <button className="primary-button" onClick={runDiscovery} disabled={busy}>
          {busy ? 'Запускаю...' : 'Найти поставщиков'}
        </button>
      </div>
      {item.ai_notes ? (
        <div className="ai-notes">
          <strong>Заметки ИИ-агента</strong>
          <pre>{item.ai_notes}</pre>
        </div>
      ) : null}
    </div>
  );
}

function SuppliersColumn(props: {
  project: ProjectRecord;
  item: ProjectItem | null;
  suppliers: SupplierRecord[];
  onError: (v: string | null) => void;
  onNotice: (v: string | null) => void;
  onReload: () => Promise<void>;
}) {
  const { project, item, suppliers, onError, onNotice, onReload } = props;
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: '', price_text: '', lead_time: '', country: '', url: '' });

  if (!item) {
    return (
      <div className="column">
        <div className="column-head"><h3>Поставщики</h3></div>
        <div className="empty-state small"><p>Выберите позицию слева.</p></div>
      </div>
    );
  }

  const toggleMonitor = async (supplier: SupplierRecord) => {
    try {
      await request(`/suppliers/${supplier.id}/monitor`, {
        method: 'PUT',
        body: JSON.stringify({ monitoring_enabled: !supplier.monitoring_enabled }),
      });
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Ошибка');
    }
  };

  const addSupplier = async () => {
    if (!draft.name.trim()) return;
    try {
      await request(`/projects/${project.id}/items/${item.id}/suppliers`, {
        method: 'POST',
        body: JSON.stringify({
          ...draft,
          currency: APP_CURRENCY,
          offer_title: '',
          price: null,
          description: '',
          terms: '',
          restrictions: '',
          category: item.specification,
          source_url: draft.url,
          contact: '',
          image_url: '',
          status: 'verified',
          is_existing: true,
          monitoring_enabled: true,
        }),
      });
      setDraft({ name: '', price_text: '', lead_time: '', country: '', url: '' });
      setAdding(false);
      onNotice('Поставщик добавлен.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось добавить');
    }
  };

  const sorted = [...suppliers].sort((a, b) => {
    const pa = a.price ?? Infinity;
    const pb = b.price ?? Infinity;
    return pa - pb;
  });
  const minPrice = sorted.find((s) => s.price != null)?.price ?? null;

  return (
    <div className="column">
      <div className="column-head">
        <h3>Поставщики для: {item.name}</h3>
        <button className="secondary-button small" onClick={() => setAdding(!adding)}>{adding ? '×' : '+ поставщик'}</button>
      </div>
      {adding ? (
        <div className="inline-form">
          <input placeholder="Название поставщика" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          <div className="inline-row">
            <input placeholder="Цена, ₽" value={draft.price_text} onChange={(e) => setDraft({ ...draft, price_text: e.target.value })} />
          </div>
          <div className="inline-row">
            <input placeholder="Срок поставки" value={draft.lead_time} onChange={(e) => setDraft({ ...draft, lead_time: e.target.value })} />
            <input placeholder="Страна" value={draft.country} onChange={(e) => setDraft({ ...draft, country: e.target.value })} />
          </div>
          <input placeholder="Ссылка" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
          <button className="primary-button small" onClick={addSupplier}>Добавить</button>
        </div>
      ) : null}
      <div className="supplier-list">
        {sorted.map((supplier) => (
          <article key={supplier.id} className={`supplier-card ${supplier.price != null && supplier.price === minPrice ? 'supplier-best' : ''}`}>
            <div className="supplier-card-head">
              <div>
                <strong>{supplier.name}</strong>
                <div className="supplier-meta">
                  {supplier.country || '—'} · {supplier.lead_time || 'срок не указан'}
                </div>
              </div>
              <label className="toggle" title="Отслеживать в открытых источниках">
                <input
                  type="checkbox"
                  checked={supplier.monitoring_enabled}
                  onChange={() => toggleMonitor(supplier)}
                />
                <span />
              </label>
            </div>
            <div className="supplier-price-row">
              <strong>{supplier.price != null ? formatMoney(supplier.price, supplier.currency) : supplier.price_text || '—'}</strong>
              <span className={`status-pill status-${supplier.status}`}>{SUPPLIER_STATUS_LABELS[supplier.status]}</span>
            </div>
            {supplier.description ? <p className="supplier-desc">{supplier.description}</p> : null}
            {supplier.ai_notes ? <p className="supplier-ai">{supplier.ai_notes}</p> : null}
            <div className="supplier-actions">
              {supplier.url ? (
                <a href={supplier.url} target="_blank" rel="noreferrer">Открыть</a>
              ) : null}
              {supplier.monitoring_enabled ? <span className="dot dot-live">Мониторинг</span> : null}
              {supplier.is_existing ? <span className="dot dot-existing">Текущий</span> : <span className="dot dot-discovered">ИИ</span>}
            </div>
          </article>
        ))}
        {!sorted.length ? (
          <div className="empty-state small">
            <p>Поставщиков нет. Запустите ИИ-агента или добавьте вручную.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ---------- Suppliers view ----------

function SuppliersView(props: { state: AppState; onError: (v: string | null) => void; onNotice: (v: string | null) => void; onReload: () => Promise<void> }) {
  const { state, onError, onNotice, onReload } = props;
  const [filter, setFilter] = useState('');

  const filtered = state.suppliers
    .filter((s) => !filter.trim() || `${s.name} ${s.country} ${s.category} ${s.description}`.toLowerCase().includes(filter.toLowerCase()))
    .sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));

  const toggle = async (supplier: SupplierRecord) => {
    try {
      await request(`/suppliers/${supplier.id}/monitor`, {
        method: 'PUT',
        body: JSON.stringify({ monitoring_enabled: !supplier.monitoring_enabled }),
      });
      onNotice(`Мониторинг ${!supplier.monitoring_enabled ? 'включен' : 'выключен'} для ${supplier.name}`);
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Ошибка');
    }
  };

  const projectFor = (projectId: string) => state.projects.find((p) => p.id === projectId);
  const itemFor = (projectId: string, itemId: string) =>
    state.projects.find((p) => p.id === projectId)?.items.find((i) => i.id === itemId);

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Каталог</p>
            <h2>Поставщики</h2>
          </div>
          <input className="top-search" placeholder="Фильтр..." value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 280 }} />
        </div>
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Поставщик</th>
                <th>Проект / позиция</th>
                <th>Цена</th>
                <th>Срок</th>
                <th>Страна</th>
                <th>Мониторинг</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((supplier) => {
                const project = projectFor(supplier.project_id);
                const item = itemFor(supplier.project_id, supplier.item_id);
                return (
                  <tr key={supplier.id}>
                    <td>
                      <strong>{supplier.name}</strong>
                      <div className="muted">{supplier.offer_title || supplier.category}</div>
                    </td>
                    <td>
                      <div>{project?.name || '—'}</div>
                      <div className="muted">{item?.name || '—'}</div>
                    </td>
                    <td>{supplier.price != null ? formatMoney(supplier.price, supplier.currency) : supplier.price_text || '—'}</td>
                    <td>{supplier.lead_time || '—'}</td>
                    <td>{supplier.country || '—'}</td>
                    <td>
                      <label className="toggle">
                        <input type="checkbox" checked={supplier.monitoring_enabled} onChange={() => toggle(supplier)} />
                        <span />
                      </label>
                    </td>
                  </tr>
                );
              })}
              {!filtered.length ? (
                <tr><td colSpan={6}><div className="empty-state small"><p>Поставщиков пока нет.</p></div></td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

// ---------- Reports ----------

function ReportsView({ state }: { state: AppState }) {
  return (
    <div className="page">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">История</p>
            <h2>Запуски ИИ-агента</h2>
          </div>
        </div>
        <div className="run-list">
          {state.runs.map((run) => (
            <div key={run.id} className="run-row">
              <div>
                <strong>{run.label}</strong>
                <p>{run.summary || run.error || 'Без резюме.'}</p>
              </div>
              <div className="run-meta">
                <span className={`status-pill status-${run.status}`}>{run.status}</span>
                <span>{formatDate(run.started_at)}</span>
              </div>
            </div>
          ))}
          {!state.runs.length ? <div className="empty-state"><p>Запусков пока нет.</p></div> : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Загрузки</p>
            <h2>Импорт данных</h2>
          </div>
        </div>
        <div className="run-list">
          {state.uploads.map((upload) => (
            <div key={upload.id} className="run-row">
              <div>
                <strong>{upload.name}</strong>
                <p>{upload.summary || upload.error || `Статус: ${upload.status}`}</p>
              </div>
              <div className="run-meta">
                <span className={`status-pill status-${upload.status}`}>{upload.status}</span>
                <span>{formatDate(upload.received_at)}</span>
              </div>
            </div>
          ))}
          {!state.uploads.length ? <div className="empty-state"><p>Загрузок пока нет.</p></div> : null}
        </div>
      </section>
    </div>
  );
}

// ---------- Settings ----------

function SettingsView(props: { state: AppState; onError: (v: string | null) => void; onNotice: (v: string | null) => void; onReload: () => Promise<void> }) {
  const { state, onError, onNotice, onReload } = props;
  const [draft, setDraft] = useState<AppConfig>({ ...emptyConfig, ...state.config, default_currency: APP_CURRENCY });
  const [saving, setSaving] = useState(false);
  const [siteDraft, setSiteDraft] = useState({ label: '', url: '', category: '', notes: '', enabled: true });

  useEffect(() => {
    setDraft({ ...emptyConfig, ...state.config, default_currency: APP_CURRENCY });
  }, [state.config]);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    try {
      await request('/config', { method: 'PUT', body: JSON.stringify({ ...draft, default_currency: APP_CURRENCY }) });
      onNotice('Настройки сохранены.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось сохранить');
    } finally {
      setSaving(false);
    }
  };

  const addSite = async () => {
    if (!siteDraft.label || !siteDraft.url) return;
    try {
      await request('/sites', { method: 'POST', body: JSON.stringify(siteDraft) });
      setSiteDraft({ label: '', url: '', category: '', notes: '', enabled: true });
      onNotice('Источник добавлен.');
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Не удалось добавить');
    }
  };

  const deleteSite = async (siteId: string) => {
    try {
      await request(`/sites/${siteId}`, { method: 'DELETE' });
      await onReload();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Ошибка');
    }
  };

  return (
    <div className="page">
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Профиль бренда</p>
            <h2>Настройки поиска</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={save}>
          <label className="field"><span>Профиль бренда / закупщика</span>
            <textarea rows={4} value={draft.company_profile} onChange={(e) => setDraft({ ...draft, company_profile: e.target.value })} />
          </label>
          <label className="field"><span>Глобальный промпт для ИИ</span>
            <textarea rows={3} value={draft.global_prompt} onChange={(e) => setDraft({ ...draft, global_prompt: e.target.value })} />
          </label>
          <div className="field-row">
            <label className="field"><span>Категории мониторинга</span>
              <input value={draft.monitored_categories} onChange={(e) => setDraft({ ...draft, monitored_categories: e.target.value })} />
            </label>
            <label className="field"><span>Валюта по умолчанию</span>
              <input value={APP_CURRENCY} readOnly />
            </label>
          </div>
          <div className="field-row">
            <label className="field"><span>Предпочтительные регионы</span>
              <input value={draft.preferred_regions} onChange={(e) => setDraft({ ...draft, preferred_regions: e.target.value })} />
            </label>
            <label className="field"><span>Исключённые регионы</span>
              <input value={draft.excluded_regions} onChange={(e) => setDraft({ ...draft, excluded_regions: e.target.value })} />
            </label>
          </div>
          <div className="field-row">
            <label className="field"><span>Максимальный срок поставки</span>
              <input value={draft.max_lead_time} onChange={(e) => setDraft({ ...draft, max_lead_time: e.target.value })} />
            </label>
            <label className="field"><span>Итераций discovery</span>
              <input type="number" min={1} value={draft.discovery_iterations} onChange={(e) => setDraft({ ...draft, discovery_iterations: Number(e.target.value) })} />
            </label>
          </div>
          <div className="field-row">
            <label className="field"><span>Итераций мониторинга</span>
              <input type="number" min={1} value={draft.monitor_iterations} onChange={(e) => setDraft({ ...draft, monitor_iterations: Number(e.target.value) })} />
            </label>
            <label className="field"><span>Период мониторинга, ч</span>
              <input type="number" min={1} value={draft.monitor_interval_hours} onChange={(e) => setDraft({ ...draft, monitor_interval_hours: Number(e.target.value) })} />
            </label>
          </div>
          <button className="primary-button" disabled={saving} type="submit">{saving ? 'Сохраняю...' : 'Сохранить'}</button>
        </form>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Источники</p>
            <h2>Постоянные площадки</h2>
          </div>
        </div>
        <div className="stack-form compact-form">
          <div className="field-row">
            <label className="field"><span>Название</span><input value={siteDraft.label} onChange={(e) => setSiteDraft({ ...siteDraft, label: e.target.value })} /></label>
            <label className="field"><span>URL</span><input value={siteDraft.url} onChange={(e) => setSiteDraft({ ...siteDraft, url: e.target.value })} /></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Категория</span><input value={siteDraft.category} onChange={(e) => setSiteDraft({ ...siteDraft, category: e.target.value })} /></label>
            <label className="field"><span>Заметка</span><input value={siteDraft.notes} onChange={(e) => setSiteDraft({ ...siteDraft, notes: e.target.value })} /></label>
          </div>
          <button className="secondary-button" onClick={addSite}>Добавить источник</button>
        </div>
        <div className="site-list">
          {state.config.sites.map((site) => (
            <div key={site.id} className="site-row">
              <div>
                <strong>{site.label}</strong>
                <div className="muted">{site.url}</div>
                {site.category ? <div className="muted">{site.category}</div> : null}
              </div>
              <button className="ghost-button small" onClick={() => deleteSite(site.id)}>Удалить</button>
            </div>
          ))}
          {!state.config.sites.length ? <div className="empty-state small"><p>Источники не настроены.</p></div> : null}
        </div>
      </section>
    </div>
  );
}

// ---------- Changes table ----------

function ChangesTable({ changes }: { changes: SupplierChange[] }) {
  if (!changes.length) {
    return <div className="empty-state small"><p>Изменений ещё нет — включите мониторинг поставщиков, и ИИ-агент будет сюда писать.</p></div>;
  }
  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Поставщик</th>
            <th>Позиция</th>
            <th>Тип</th>
            <th>Было</th>
            <th>Стало</th>
            <th>Когда</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={change.id}>
              <td>{change.supplier_name}</td>
              <td>{change.item_name}</td>
              <td><span className={`status-pill status-${change.change_type}`}>{change.change_type}</span></td>
              <td>{change.old_value || '—'}</td>
              <td>{change.new_value || '—'}</td>
              <td>{formatDate(change.detected_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

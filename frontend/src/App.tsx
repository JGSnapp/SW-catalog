import { useEffect, useMemo, useState, type FormEvent } from 'react';

type SupplierStatus = 'new' | 'reviewed' | 'suitable' | 'not_suitable' | 'applied';

type SiteConfig = {
  id: string;
  label: string;
  url: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_run_at: string | null;
  next_run_at: string | null;
};

type AppConfig = {
  company_profile: string;
  global_prompt: string;
  target_institutions: string;
  search_directions: string;
  min_amount: string;
  max_amount: string;
  funding_types: string;
  regions: string;
  deadline_window: string;
  eligibility_requirements: string;
  excluded_restrictions: string;
  keywords: string;
  interval_hours: number;
  iterations_per_site: number;
  source_discovery_enabled: boolean;
  source_discovery_interval_hours: number;
  source_discovery_iterations: number;
  source_discovery_last_run_at: string | null;
  source_discovery_next_run_at: string | null;
  sites: SiteConfig[];
};

type SupplierOffer = {
  id: string;
  title: string;
  institution: string;
  amount: string;
  funding_type: string;
  category: string;
  conditions: string;
  restrictions: string;
  deadline: string;
  application_url: string;
  status: SupplierStatus;
  site: string;
  site_id: string;
  description: string;
  fit_reason: string;
  how_to_apply: string;
  source: string;
  site_url: string;
  discovered_at: string;
  updated_at: string;
  last_run_id: string;
  telegram_notified_at: string | null;
};

type ProductComponent = {
  id: string;
  name: string;
  specification: string;
  quantity: number;
  target_price: string;
  notes: string;
};

type ProductRecord = {
  id: string;
  name: string;
  description: string;
  target_volume: string;
  components: ProductComponent[];
  created_at: string;
  updated_at: string;
};

type SourceCandidate = {
  id: string;
  label: string;
  url: string;
  reason: string;
  evidence: string;
  status: 'new' | 'added' | 'dismissed';
  discovered_at: string;
  updated_at: string;
  last_run_id: string;
  telegram_notified_at: string | null;
};

type RunRecord = {
  id: string;
  site_id: string;
  site_url: string;
  started_at: string;
  finished_at: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed';
  summary: string;
  error: string | null;
};

type RunEventRecord = {
  id: string;
  run_id: string;
  site_id: string;
  site_url: string;
  event_type: string;
  message: string;
  created_at: string;
  metadata: Record<string, string>;
};

type SiteTextResponse = {
  content: string;
  updated_at: string | null;
};

type AppState = {
  config: AppConfig;
  grants: SupplierOffer[];
  products: ProductRecord[];
  runs: RunRecord[];
  source_candidates: SourceCandidate[];
};

type DashboardTab = 'products' | 'suppliers' | 'monitoring' | 'runs';

const API_BASE = (process.env.REACT_APP_API_BASE || '/api').replace(/\/$/, '');

const emptyConfig: AppConfig = {
  company_profile: '',
  global_prompt: '',
  target_institutions: '',
  search_directions: 'электроника, корпус, крепеж, упаковка, производство',
  min_amount: '',
  max_amount: '',
  funding_types: 'производители, дистрибьюторы, маркетплейсы',
  regions: '',
  deadline_window: '',
  eligibility_requirements: '',
  excluded_restrictions: '',
  keywords: '',
  interval_hours: 24,
  iterations_per_site: 12,
  source_discovery_enabled: false,
  source_discovery_interval_hours: 168,
  source_discovery_iterations: 10,
  source_discovery_last_run_at: null,
  source_discovery_next_run_at: null,
  sites: [],
};

const statusLabels: Record<SupplierStatus, string> = {
  new: 'Новый',
  reviewed: 'Проверен',
  suitable: 'Подходит',
  not_suitable: 'Не подходит',
  applied: 'Запрошена цена',
};

const statusOptions = Object.entries(statusLabels) as Array<[SupplierStatus, string]>;

const makeId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const newComponent = (name = ''): ProductComponent => ({
  id: makeId(),
  name,
  specification: '',
  quantity: 1,
  target_price: '',
  notes: '',
});

const emptyProductDraft = {
  name: '',
  description: '',
  target_volume: '',
  components: [newComponent()],
};

const formatDate = (value: string | null) => {
  if (!value) return 'Не указано';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
};

const normalize = (value: string | null | undefined) => (value || '').toLowerCase();

const parsePrice = (value: string) => {
  const normalized = value.replace(/\s/g, '').replace(',', '.');
  const matches = normalized.match(/\d+(?:\.\d+)?/g);
  if (!matches?.length) return 0;
  return Math.min(...matches.map(Number).filter((item) => item > 0));
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) throw new Error((await response.text()) || 'Request failed');
  return response.json() as Promise<T>;
}

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [configDraft, setConfigDraft] = useState<AppConfig>(emptyConfig);
  const [productDraft, setProductDraft] = useState<Omit<ProductRecord, 'id' | 'created_at' | 'updated_at'>>(emptyProductDraft);
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);
  const [selectedSupplierId, setSelectedSupplierId] = useState<string | null>(null);
  const [selectedSiteId, setSelectedSiteId] = useState<string | null>(null);
  const [selectedSiteDraft, setSelectedSiteDraft] = useState({ label: '', url: '', enabled: true });
  const [newSite, setNewSite] = useState({ label: '', url: '' });
  const [siteNotes, setSiteNotes] = useState<SiteTextResponse>({ content: '', updated_at: null });
  const [siteStatus, setSiteStatus] = useState<SiteTextResponse>({ content: '', updated_at: null });
  const [supplierQuery, setSupplierQuery] = useState('');
  const [supplierStatusFilter, setSupplierStatusFilter] = useState<'all' | SupplierStatus>('all');
  const [activeTab, setActiveTab] = useState<DashboardTab>('products');
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<RunEventRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [runningSiteId, setRunningSiteId] = useState<string | null>(null);
  const [runningDiscovery, setRunningDiscovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const selectedProduct = useMemo(
    () => state?.products.find((product) => product.id === selectedProductId) || state?.products[0] || null,
    [selectedProductId, state?.products]
  );
  const selectedSupplier = useMemo(
    () => state?.grants.find((supplier) => supplier.id === selectedSupplierId) || state?.grants[0] || null,
    [selectedSupplierId, state?.grants]
  );
  const selectedSite = useMemo(
    () => state?.config.sites.find((site) => site.id === selectedSiteId) || null,
    [selectedSiteId, state?.config.sites]
  );
  const activeRun = useMemo(
    () => state?.runs.find((run) => run.status === 'queued' || run.status === 'running') || null,
    [state?.runs]
  );
  const monitoredRun = useMemo(
    () => state?.runs.find((run) => run.id === liveRunId) || activeRun || null,
    [activeRun, liveRunId, state?.runs]
  );

  const filteredSuppliers = useMemo(() => {
    const query = normalize(supplierQuery);
    return [...(state?.grants || [])]
      .filter((supplier) => {
        const haystack = [
          supplier.title,
          supplier.institution,
          supplier.amount,
          supplier.funding_type,
          supplier.category,
          supplier.conditions,
          supplier.deadline,
          supplier.description,
          supplier.site,
        ]
          .map(normalize)
          .join(' ');
        const matchesQuery = !query || haystack.includes(query);
        const matchesStatus = supplierStatusFilter === 'all' || supplier.status === supplierStatusFilter;
        return matchesQuery && matchesStatus;
      })
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [state?.grants, supplierQuery, supplierStatusFilter]);

  const supplierMatches = useMemo(() => {
    const product = selectedProduct;
    const suppliers = state?.grants || [];
    if (!product) return [];
    return product.components.map((component) => {
      const terms = normalize(`${component.name} ${component.specification}`).split(/\s+/).filter((term) => term.length > 2);
      const matches = suppliers
        .filter((supplier) => {
          const haystack = normalize(`${supplier.title} ${supplier.category} ${supplier.description} ${supplier.fit_reason}`);
          return terms.some((term) => haystack.includes(term));
        })
        .sort((a, b) => parsePrice(a.amount) - parsePrice(b.amount));
      const best = matches.find((supplier) => parsePrice(supplier.amount) > 0) || matches[0] || null;
      const price = best ? parsePrice(best.amount) : 0;
      return {
        component,
        best,
        matches,
        subtotal: price * Number(component.quantity || 1),
      };
    });
  }, [selectedProduct, state?.grants]);

  const totalEstimate = supplierMatches.reduce((sum, item) => sum + item.subtotal, 0);
  const pendingCandidates = (state?.source_candidates || []).filter((candidate) => candidate.status === 'new');
  const latestLiveEvent = liveEvents[liveEvents.length - 1] || null;

  const loadState = async () => {
    const payload = await request<AppState>('/state');
    setState(payload);
    setConfigDraft({ ...emptyConfig, ...payload.config });
    setSelectedProductId((current) => current || payload.products[0]?.id || null);
    setSelectedSupplierId((current) => current || payload.grants[0]?.id || null);
    setSelectedSiteId((current) => current || payload.config.sites[0]?.id || null);
  };

  const loadSiteText = async (siteId: string) => {
    const [notes, status] = await Promise.all([
      request<SiteTextResponse>(`/sites/${siteId}/notes`),
      request<SiteTextResponse>(`/sites/${siteId}/status`),
    ]);
    setSiteNotes(notes);
    setSiteStatus(status);
  };

  const loadRunEvents = async (runId: string) => {
    setLiveEvents(await request<RunEventRecord[]>(`/runs/${runId}/events`));
  };

  const refreshAll = async (siteId?: string | null) => {
    await loadState();
    const targetSiteId = siteId || selectedSiteId;
    if (targetSiteId) await loadSiteText(targetSiteId);
    if (liveRunId) await loadRunEvents(liveRunId);
  };

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        await loadState();
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Failed to load state');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedSite) return;
    setSelectedSiteDraft({ label: selectedSite.label, url: selectedSite.url, enabled: selectedSite.enabled });
    loadSiteText(selectedSite.id).catch((err) => setError(err instanceof Error ? err.message : 'Failed to load site'));
  }, [selectedSite]);

  useEffect(() => {
    if (!activeRun) return;
    const timer = window.setInterval(() => {
      loadState().catch(() => undefined);
      if (selectedSiteId) loadSiteText(selectedSiteId).catch(() => undefined);
      if (liveRunId) loadRunEvents(liveRunId).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [activeRun, liveRunId, selectedSiteId]);

  useEffect(() => {
    if (activeRun?.id) setLiveRunId(activeRun.id);
    else if (!liveRunId && state?.runs[0]?.id) setLiveRunId(state.runs[0].id);
  }, [activeRun?.id, liveRunId, state?.runs]);

  useEffect(() => {
    if (liveRunId) loadRunEvents(liveRunId).catch(() => undefined);
  }, [liveRunId]);

  const saveProduct = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const product = await request<ProductRecord>('/products', { method: 'POST', body: JSON.stringify(productDraft) });
      setProductDraft(emptyProductDraft);
      setSelectedProductId(product.id);
      setNotice('Карточка изделия сохранена.');
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save product');
    } finally {
      setSaving(false);
    }
  };

  const updateProduct = async (product: ProductRecord) => {
    setSaving(true);
    setError(null);
    try {
      await request<ProductRecord>(`/products/${product.id}`, { method: 'PUT', body: JSON.stringify(product) });
      setNotice('Состав изделия обновлен.');
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update product');
    } finally {
      setSaving(false);
    }
  };

  const deleteProduct = async (productId: string) => {
    setSaving(true);
    setError(null);
    try {
      await request<{ ok: boolean }>(`/products/${productId}`, { method: 'DELETE' });
      setSelectedProductId(null);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete product');
    } finally {
      setSaving(false);
    }
  };

  const generateComponents = () => {
    const text = normalize(`${productDraft.name} ${productDraft.description}`);
    const names = ['Корпус', 'Плата управления', 'Крепеж', 'Упаковка'];
    if (text.includes('датчик') || text.includes('сенсор')) names.splice(1, 0, 'Сенсорный модуль', 'Кабель');
    if (text.includes('робот') || text.includes('мотор')) names.splice(1, 0, 'Электродвигатель', 'Редуктор');
    if (text.includes('пласт') || text.includes('лить')) names.splice(1, 0, 'Пластиковая заготовка');
    setProductDraft((current) => ({ ...current, components: Array.from(new Set(names)).map((name) => newComponent(name)) }));
  };

  const saveConfig = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await request<AppConfig>('/config', { method: 'PUT', body: JSON.stringify(configDraft) });
      setNotice('Настройки мониторинга сохранены.');
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const addSite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const site = await request<SiteConfig>('/sites', { method: 'POST', body: JSON.stringify({ ...newSite, enabled: true }) });
      setNewSite({ label: '', url: '' });
      setSelectedSiteId(site.id);
      await refreshAll(site.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add site');
    } finally {
      setSaving(false);
    }
  };

  const saveSite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedSite) return;
    setSaving(true);
    setError(null);
    try {
      await request<SiteConfig>(`/sites/${selectedSite.id}`, { method: 'PUT', body: JSON.stringify(selectedSiteDraft) });
      setNotice('Площадка обновлена.');
      await refreshAll(selectedSite.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update site');
    } finally {
      setSaving(false);
    }
  };

  const deleteSite = async (siteId: string) => {
    setSaving(true);
    setError(null);
    try {
      await request<{ ok: boolean }>(`/sites/${siteId}`, { method: 'DELETE' });
      setSelectedSiteId(null);
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete site');
    } finally {
      setSaving(false);
    }
  };

  const runSite = async (siteId: string) => {
    setRunningSiteId(siteId);
    setError(null);
    try {
      const result = await request<{ run_id: string }>(`/sites/${siteId}/run`, { method: 'POST' });
      setLiveRunId(result.run_id);
      setNotice('Проверка площадки запущена.');
      await refreshAll(siteId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start run');
    } finally {
      setRunningSiteId(null);
    }
  };

  const runDiscovery = async () => {
    setRunningDiscovery(true);
    setError(null);
    try {
      const result = await request<{ run_id: string }>('/source-discovery/run', { method: 'POST' });
      setLiveRunId(result.run_id);
      setNotice('Поиск новых площадок запущен.');
      await refreshAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start source discovery');
    } finally {
      setRunningDiscovery(false);
    }
  };

  const addCandidate = async (candidateId: string) => {
    setSaving(true);
    setError(null);
    try {
      const site = await request<SiteConfig>(`/source-candidates/${candidateId}/add`, { method: 'POST' });
      setSelectedSiteId(site.id);
      await refreshAll(site.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add candidate');
    } finally {
      setSaving(false);
    }
  };

  const updateSupplierStatus = async (supplierId: string, status: SupplierStatus) => {
    try {
      const updated = await request<SupplierOffer>(`/grants/${supplierId}/status`, {
        method: 'PUT',
        body: JSON.stringify({ status }),
      });
      setState((current) =>
        current ? { ...current, grants: current.grants.map((item) => (item.id === supplierId ? updated : item)) } : current
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update supplier');
    }
  };

  if (loading) return <div className="shell centered">Загрузка приложения...</div>;

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Supplier Scout</p>
          <h1>Закупки изделия и мониторинг поставщиков</h1>
          <p className="hero-copy">
            Карточки изделий, состав, найденные поставщики, сравнение цен и регулярная проверка сайтов в одном рабочем
            интерфейсе.
          </p>
        </div>
        <div className="hero-stats">
          <div className="stat-card">
            <span className="stat-value">{state?.products.length || 0}</span>
            <span className="stat-label">Изделий</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{state?.grants.length || 0}</span>
            <span className="stat-label">Поставщиков</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{state?.config.sites.length || 0}</span>
            <span className="stat-label">Площадок</span>
          </div>
        </div>
      </header>

      {error ? <div className="banner error-banner">{error}</div> : null}
      {notice ? <div className="banner success-banner">{notice}</div> : null}

      <nav className="tab-bar" aria-label="Разделы">
        {[
          ['products', 'Изделия'],
          ['suppliers', 'Поставщики'],
          ['monitoring', 'Мониторинг'],
          ['runs', 'Запуски'],
        ].map(([tab, label]) => (
          <button
            key={tab}
            type="button"
            className={`tab-button ${activeTab === tab ? 'tab-button-active' : ''}`}
            onClick={() => setActiveTab(tab as DashboardTab)}
          >
            {label}
          </button>
        ))}
      </nav>

      <section className="live-strip">
        <div className="live-strip-head">
          <strong>Live мониторинг</strong>
          {monitoredRun ? <span className={`pill pill-${monitoredRun.status}`}>{monitoredRun.status}</span> : null}
        </div>
        <div className="live-strip-body">
          <p>{latestLiveEvent?.message || monitoredRun?.summary || 'Нет активной проверки.'}</p>
          {monitoredRun ? <p>{formatDate(latestLiveEvent?.created_at || monitoredRun.started_at)}</p> : null}
        </div>
      </section>

      <main className="tab-layout">
        {activeTab === 'products' ? (
          <div className="dashboard">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">Product Card</p>
                  <h2>Карточка изделия</h2>
                </div>
              </div>
              <form className="stack-form" onSubmit={saveProduct}>
                <label className="field">
                  <span>Название изделия</span>
                  <input
                    value={productDraft.name}
                    onChange={(event) => setProductDraft((current) => ({ ...current, name: event.target.value }))}
                    placeholder="Например: контроллер доступа"
                  />
                </label>
                <label className="field">
                  <span>Описание и требования</span>
                  <textarea
                    rows={4}
                    value={productDraft.description}
                    onChange={(event) => setProductDraft((current) => ({ ...current, description: event.target.value }))}
                    placeholder="Материалы, назначение, параметры, ограничения по поставке"
                  />
                </label>
                <label className="field">
                  <span>Плановый объем</span>
                  <input
                    value={productDraft.target_volume}
                    onChange={(event) => setProductDraft((current) => ({ ...current, target_volume: event.target.value }))}
                    placeholder="100 шт/месяц, пилотная партия 20 шт"
                  />
                </label>
                <div className="component-list">
                  {productDraft.components.map((component, index) => (
                    <div className="component-row" key={component.id}>
                      <input
                        value={component.name}
                        onChange={(event) =>
                          setProductDraft((current) => ({
                            ...current,
                            components: current.components.map((item) =>
                              item.id === component.id ? { ...item, name: event.target.value } : item
                            ),
                          }))
                        }
                        placeholder="Позиция"
                      />
                      <input
                        value={component.specification}
                        onChange={(event) =>
                          setProductDraft((current) => ({
                            ...current,
                            components: current.components.map((item) =>
                              item.id === component.id ? { ...item, specification: event.target.value } : item
                            ),
                          }))
                        }
                        placeholder="Спецификация"
                      />
                      <input
                        type="number"
                        min={0}
                        step="0.01"
                        value={component.quantity}
                        onChange={(event) =>
                          setProductDraft((current) => ({
                            ...current,
                            components: current.components.map((item) =>
                              item.id === component.id ? { ...item, quantity: Number(event.target.value) } : item
                            ),
                          }))
                        }
                        aria-label={`Количество ${index + 1}`}
                      />
                    </div>
                  ))}
                </div>
                <div className="site-card-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => setProductDraft((current) => ({ ...current, components: [...current.components, newComponent()] }))}
                  >
                    Добавить позицию
                  </button>
                  <button className="secondary-button" type="button" onClick={generateComponents}>
                    ИИ: предложить состав
                  </button>
                  <button className="primary-button" type="submit" disabled={saving || !productDraft.name}>
                    Сохранить изделие
                  </button>
                </div>
              </form>
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">Savings Mix</p>
                  <h2>Комбинация поставщиков</h2>
                </div>
              </div>
              {selectedProduct ? (
                <div className="product-summary">
                  <h3>{selectedProduct.name}</h3>
                  <p>{selectedProduct.description || 'Описание не заполнено.'}</p>
                  <div className="total-card">
                    <span>Оценка по лучшим ценам</span>
                    <strong>{totalEstimate ? `${totalEstimate.toLocaleString('ru-RU')} ₽` : 'Недостаточно цен'}</strong>
                  </div>
                  <div className="match-list">
                    {supplierMatches.map(({ component, best, matches, subtotal }) => (
                      <article className="match-card" key={component.id}>
                        <div>
                          <h4>{component.name}</h4>
                          <p>{component.specification || 'Спецификация не указана'} · {component.quantity} шт.</p>
                        </div>
                        {best ? (
                          <div>
                            <strong>{best.institution || best.site || best.title}</strong>
                            <p>{best.amount || 'Цена не указана'} · вариантов: {matches.length}</p>
                            <p>{subtotal ? `Подытог: ${subtotal.toLocaleString('ru-RU')} ₽` : 'Нужна ручная проверка цены'}</p>
                          </div>
                        ) : (
                          <p>Подходящих поставщиков пока нет. Запустите мониторинг площадок.</p>
                        )}
                      </article>
                    ))}
                  </div>
                  <button className="ghost-button" type="button" onClick={() => deleteProduct(selectedProduct.id)} disabled={saving}>
                    Удалить изделие
                  </button>
                </div>
              ) : (
                <div className="empty-state">
                  <h3>Изделий пока нет</h3>
                  <p>Создайте карточку изделия и распишите состав, чтобы система сопоставила позиции с поставщиками.</p>
                </div>
              )}
            </section>

            <section className="panel panel-wide">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">Products</p>
                  <h2>Сохраненные изделия</h2>
                </div>
              </div>
              <div className="candidate-grid">
                {(state?.products || []).map((product) => (
                  <article
                    key={product.id}
                    className={`candidate-card ${selectedProduct?.id === product.id ? 'site-card-active' : ''}`}
                    onClick={() => setSelectedProductId(product.id)}
                  >
                    <h3>{product.name}</h3>
                    <p>{product.description || 'Нет описания.'}</p>
                    <div className="site-card-meta">
                      <span>{product.components.length} позиций</span>
                      <span>{product.target_volume || 'Объем не указан'}</span>
                    </div>
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        updateProduct({
                          ...product,
                          components: [...product.components, newComponent('Новая позиция')],
                        });
                      }}
                    >
                      Добавить позицию
                    </button>
                  </article>
                ))}
              </div>
            </section>
          </div>
        ) : null}

        {activeTab === 'suppliers' ? (
          <section className="panel">
            <div className="panel-heading">
              <div>
                <p className="section-kicker">Supplier Cards</p>
                <h2>Карточки поставщиков</h2>
              </div>
            </div>
            <div className="grant-toolbar">
              <label className="field">
                <span>Поиск</span>
                <input value={supplierQuery} onChange={(event) => setSupplierQuery(event.target.value)} placeholder="Поставщик, компонент, цена" />
              </label>
              <label className="field">
                <span>Статус</span>
                <select value={supplierStatusFilter} onChange={(event) => setSupplierStatusFilter(event.target.value as 'all' | SupplierStatus)}>
                  <option value="all">Все статусы</option>
                  {statusOptions.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
            </div>
            {filteredSuppliers.length ? (
              <div className="supplier-card-grid">
                {filteredSuppliers.map((supplier) => (
                  <article
                    key={supplier.id}
                    className={`supplier-card ${selectedSupplier?.id === supplier.id ? 'site-card-active' : ''}`}
                    onClick={() => setSelectedSupplierId(supplier.id)}
                  >
                    <div className="candidate-card-head">
                      <div>
                        <h3>{supplier.institution || supplier.title}</h3>
                        <p>{supplier.title}</p>
                      </div>
                      <span className={`pill pill-grant-${supplier.status}`}>{statusLabels[supplier.status]}</span>
                    </div>
                    <dl className="grant-facts">
                      <div><dt>Цена</dt><dd>{supplier.amount || 'Unknown'}</dd></div>
                      <div><dt>Компонент</dt><dd>{supplier.category || 'Unknown'}</dd></div>
                      <div><dt>Тип</dt><dd>{supplier.funding_type || 'Unknown'}</dd></div>
                      <div><dt>Срок</dt><dd>{supplier.deadline || 'Unknown'}</dd></div>
                    </dl>
                    <p>{supplier.description || supplier.fit_reason || 'Описание не заполнено.'}</p>
                    <div className="site-card-actions">
                      <select
                        value={supplier.status}
                        onClick={(event) => event.stopPropagation()}
                        onChange={(event) => updateSupplierStatus(supplier.id, event.target.value as SupplierStatus)}
                      >
                        {statusOptions.map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                      <a href={supplier.application_url || supplier.source} target="_blank" rel="noreferrer">Открыть</a>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <h3>Поставщиков пока нет</h3>
                <p>Добавьте площадки и запустите проверку. Найденные предложения появятся карточками.</p>
              </div>
            )}
          </section>
        ) : null}

        {activeTab === 'monitoring' ? (
          <div className="dashboard">
            <section className="panel panel-wide">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">Search Setup</p>
                  <h2>Настройки поиска поставщиков</h2>
                </div>
              </div>
              <form className="stack-form" onSubmit={saveConfig}>
                <label className="field">
                  <span>Профиль изделия и закупки</span>
                  <textarea rows={5} value={configDraft.company_profile} onChange={(event) => setConfigDraft((current) => ({ ...current, company_profile: event.target.value }))} />
                </label>
                <div className="field-row">
                  <label className="field">
                    <span>Предпочтительные площадки</span>
                    <input value={configDraft.target_institutions} onChange={(event) => setConfigDraft((current) => ({ ...current, target_institutions: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span>Компоненты и группы</span>
                    <input value={configDraft.search_directions} onChange={(event) => setConfigDraft((current) => ({ ...current, search_directions: event.target.value }))} />
                  </label>
                </div>
                <div className="field-row">
                  <label className="field">
                    <span>Минимальная цена/партия</span>
                    <input value={configDraft.min_amount} onChange={(event) => setConfigDraft((current) => ({ ...current, min_amount: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span>Максимальный бюджет</span>
                    <input value={configDraft.max_amount} onChange={(event) => setConfigDraft((current) => ({ ...current, max_amount: event.target.value }))} />
                  </label>
                </div>
                <div className="field-row">
                  <label className="field">
                    <span>Типы поставщиков</span>
                    <input value={configDraft.funding_types} onChange={(event) => setConfigDraft((current) => ({ ...current, funding_types: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span>Регион и доставка</span>
                    <input value={configDraft.regions} onChange={(event) => setConfigDraft((current) => ({ ...current, regions: event.target.value }))} />
                  </label>
                </div>
                <div className="field-row">
                  <label className="field">
                    <span>Срок поставки</span>
                    <input value={configDraft.deadline_window} onChange={(event) => setConfigDraft((current) => ({ ...current, deadline_window: event.target.value }))} />
                  </label>
                  <label className="field">
                    <span>Ключевые слова и артикулы</span>
                    <input value={configDraft.keywords} onChange={(event) => setConfigDraft((current) => ({ ...current, keywords: event.target.value }))} />
                  </label>
                </div>
                <label className="field">
                  <span>Обязательные условия</span>
                  <textarea rows={3} value={configDraft.eligibility_requirements} onChange={(event) => setConfigDraft((current) => ({ ...current, eligibility_requirements: event.target.value }))} />
                </label>
                <label className="field">
                  <span>Исключить</span>
                  <textarea rows={3} value={configDraft.excluded_restrictions} onChange={(event) => setConfigDraft((current) => ({ ...current, excluded_restrictions: event.target.value }))} />
                </label>
                <div className="field-row">
                  <label className="field">
                    <span>Периодичность проверки, часов</span>
                    <input type="number" min={1} value={configDraft.interval_hours} onChange={(event) => setConfigDraft((current) => ({ ...current, interval_hours: Number(event.target.value) }))} />
                  </label>
                  <label className="field">
                    <span>Итераций на площадку</span>
                    <input type="number" min={1} value={configDraft.iterations_per_site} onChange={(event) => setConfigDraft((current) => ({ ...current, iterations_per_site: Number(event.target.value) }))} />
                  </label>
                </div>
                <label className="checkbox-field">
                  <input type="checkbox" checked={configDraft.source_discovery_enabled} onChange={(event) => setConfigDraft((current) => ({ ...current, source_discovery_enabled: event.target.checked }))} />
                  <span>Искать новые площадки поставщиков по расписанию</span>
                </label>
                <button className="primary-button" type="submit" disabled={saving}>Сохранить настройки</button>
              </form>
            </section>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">Sites</p>
                  <h2>Площадки</h2>
                </div>
                <button className="secondary-button" type="button" onClick={runDiscovery} disabled={runningDiscovery}>
                  {runningDiscovery ? 'Запускается...' : 'Найти площадки'}
                </button>
              </div>
              <form className="stack-form compact-form" onSubmit={addSite}>
                <label className="field"><span>Название</span><input value={newSite.label} onChange={(event) => setNewSite((current) => ({ ...current, label: event.target.value }))} /></label>
                <label className="field"><span>URL</span><input value={newSite.url} onChange={(event) => setNewSite((current) => ({ ...current, url: event.target.value }))} /></label>
                <button className="secondary-button" type="submit" disabled={saving || !newSite.label || !newSite.url}>Добавить площадку</button>
              </form>
              <div className="site-list">
                {(state?.config.sites || []).map((site) => {
                  const siteRun = state?.runs.find((run) => run.site_id === site.id && (run.status === 'queued' || run.status === 'running'));
                  return (
                    <article key={site.id} className={`site-card ${selectedSiteId === site.id ? 'site-card-active' : ''}`} onClick={() => setSelectedSiteId(site.id)}>
                      <div className="site-card-top">
                        <div><h3>{site.label}</h3><p>{site.url}</p></div>
                        <span className={`pill ${site.enabled ? 'pill-live' : 'pill-muted'}`}>{site.enabled ? 'Включена' : 'Пауза'}</span>
                      </div>
                      <div className="site-card-meta"><span>Последний: {formatDate(site.last_run_at)}</span><span>Следующий: {formatDate(site.next_run_at)}</span></div>
                      <div className="site-card-actions">
                        <button className="primary-button" type="button" disabled={Boolean(siteRun) || runningSiteId === site.id} onClick={(event) => { event.stopPropagation(); runSite(site.id); }}>
                          {siteRun ? 'Выполняется...' : 'Проверить'}
                        </button>
                        <button className="ghost-button" type="button" onClick={(event) => { event.stopPropagation(); deleteSite(site.id); }}>Удалить</button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>

            <section className="panel">
              <div className="panel-heading"><div><p className="section-kicker">Selected Site</p><h2>Статус площадки</h2></div></div>
              {selectedSite ? (
                <>
                  <form className="stack-form" onSubmit={saveSite}>
                    <label className="field"><span>Название</span><input value={selectedSiteDraft.label} onChange={(event) => setSelectedSiteDraft((current) => ({ ...current, label: event.target.value }))} /></label>
                    <label className="field"><span>URL</span><input value={selectedSiteDraft.url} onChange={(event) => setSelectedSiteDraft((current) => ({ ...current, url: event.target.value }))} /></label>
                    <label className="checkbox-field"><input type="checkbox" checked={selectedSiteDraft.enabled} onChange={(event) => setSelectedSiteDraft((current) => ({ ...current, enabled: event.target.checked }))} /><span>Проверять по расписанию</span></label>
                    <button className="secondary-button" type="submit" disabled={saving}>Сохранить площадку</button>
                  </form>
                  <div className="detail-grid single-column">
                    <div className="detail-card"><div className="detail-card-top"><h3>Заметки</h3><span>{formatDate(siteNotes.updated_at)}</span></div><pre>{siteNotes.content || 'Заметок пока нет.'}</pre></div>
                    <div className="detail-card"><div className="detail-card-top"><h3>Отчет</h3><span>{formatDate(siteStatus.updated_at)}</span></div><pre>{siteStatus.content || 'Отчета пока нет.'}</pre></div>
                  </div>
                </>
              ) : <div className="empty-state"><h3>Площадка не выбрана</h3><p>Выберите сайт, чтобы увидеть заметки и отчет.</p></div>}
            </section>

            {pendingCandidates.length ? (
              <section className="panel panel-wide">
                <div className="panel-heading"><div><p className="section-kicker">Suggestions</p><h2>Новые площадки</h2></div></div>
                <div className="candidate-grid">
                  {pendingCandidates.map((candidate) => (
                    <article className="candidate-card" key={candidate.id}>
                      <h3>{candidate.label}</h3>
                      <a href={candidate.url} target="_blank" rel="noreferrer">{candidate.url}</a>
                      <p>{candidate.reason || candidate.evidence || 'Нет пояснения.'}</p>
                      <button className="primary-button" type="button" onClick={() => addCandidate(candidate.id)} disabled={saving}>Добавить</button>
                    </article>
                  ))}
                </div>
              </section>
            ) : null}
          </div>
        ) : null}

        {activeTab === 'runs' ? (
          <section className="panel">
            <div className="panel-heading"><div><p className="section-kicker">Run Log</p><h2>История запусков</h2></div></div>
            <div className="run-live-header">
              <span>События</span>
              <select value={liveRunId || ''} onChange={(event) => setLiveRunId(event.target.value || null)} disabled={!state?.runs.length}>
                {!state?.runs.length ? <option value="">Нет запусков</option> : null}
                {state?.runs.map((run) => <option key={run.id} value={run.id}>{formatDate(run.started_at)} · {run.site_url}</option>)}
              </select>
            </div>
            {liveEvents.length ? (
              <div className="run-event-list">
                {liveEvents.map((event) => (
                  <div className="run-event-row" key={event.id}>
                    <div><strong>{event.message}</strong><p>{event.metadata?.query || event.metadata?.url || event.site_url}</p></div>
                    <span>{formatDate(event.created_at)}</span>
                  </div>
                ))}
              </div>
            ) : <div className="empty-state"><h3>Событий пока нет</h3><p>Они появятся во время проверки площадок.</p></div>}
            <div className="run-list">
              {(state?.runs || []).map((run) => (
                <div className="run-row" key={run.id}>
                  <div><strong>{run.site_url}</strong><p>{run.summary || run.error || 'Нет резюме.'}</p></div>
                  <div className="run-meta"><span className={`pill pill-${run.status}`}>{run.status}</span><span>{formatDate(run.started_at)}</span></div>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}

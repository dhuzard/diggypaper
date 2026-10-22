/**
 * Client API (frontend → backend FastAPI).
 *
 * Les appels sont faits côté client (fetch, same-origin). En prod, nginx proxy
 * /api/v1 vers le backend ; en dev, le rewrite next.config.mjs fait le même.
 * L'auth est un cookie JWT httpOnly posé par le backend (credentials: include).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

// --- Types partagés ---------------------------------------------------------

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: "ADMIN" | "RESEARCHER";
  language_preference: string;
  data_consent_given: boolean;
  can_create_projects: boolean;
  created_at: string;
}

/** Métrique de classement d'un item (benchmark) : rappel par défaut. */
export type RankMetric = "recall" | "precision" | "f1" | "accuracy";

export interface SchemaField {
  key: string;
  label: string;
  type: "boolean" | "choice" | "text";
  options?: string[];
  /** Critère de classement des modèles / prompts du benchmark (un seul item) et sa métrique. */
  primary?: boolean;
  metric?: RankMetric;
}

export interface Project {
  id: string;
  title: string;
  description: string | null;
  status: "DRAFT" | "PROCESSING" | "FROZEN" | "COMPLETED";
  created_by: string | null;
  default_language: string;
  text_sections?: string[];
  question?: string;
  read_windows?: number;
  fields?: SchemaField[];
  custom_schema?: boolean;
  question_set?: boolean;
  created_at: string | null;
  article_count?: number;
  fulltext_count?: number;
  role_in_project?: "LEAD" | "ANNOTATOR" | null;
}

export interface Article {
  id: string;
  source_view_token?: string;
  pmid: string | null;
  pmcid: string | null;
  doi: string | null;
  title: string | null;
  abstract: string | null;
  methods_text: string | null;
  text?: string;
  has_fulltext?: boolean;
  fulltext_source?: string | null;
  fulltext_note?: string | null;
  doi_url?: string | null;
  fulltext_url?: string | null;
  journal?: string | null;
  pub_date?: string | null;
  authors?: string[];
  first_author?: string | null;
  last_author?: string | null;
  pub_types?: string[];
  answers?: { key: string; label: string; value: unknown; confidence: number | null; source?: "human" | "model" }[];
  prediction_status?: "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | null;
  confidence?: number | null;
  annotated?: boolean;
  review?: Review | null;
  original_language: string | null;
  created_at: string | null;
}

/** Dernière relecture humaine d'un article. */
export interface Review {
  by: string | null;
  at: string | null;
  status: "VALIDATED" | "CORRECTED" | "MANUAL" | null;
}

export interface ArticleReview extends Review {
  decision_json: Record<string, unknown> | null;
  notes: string | null;
}

export interface ArticlePrediction {
  id: string;
  article_id: string;
  prompt_template_id: string | null;
  model_used: string;
  decision_json: Record<string, unknown> | null;
  confidence_score: number | null;
  evidence_quotes: string[] | null;
  quote_checks: QuoteChecks | null;
  /** Declared by the bound model call; null when no call is bound (legacy). */
  quotation_producer?: QuotationProducer | null;
  evidence_graph?: EvidenceGraph | null;
  status: "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW";
  created_at: string | null;
}

export interface EvidenceGraph {
  envelope: {
    schema_version: string;
    state: string;
    scope: {
      project_ref: string;
      article_ref: string;
      record_kind: string;
      record_ref: string;
      revision_ref: string;
    };
    field_definition_ref: string | null;
    answers: Array<{ answer_ref: string; field_key: string; value: unknown }>;
    evidence: Array<{
      evidence_ref: string;
      quote: string;
      origin: string;
      source_binding_ref: string | null;
      quote_match_ref: string | null;
    }>;
    links: Array<{ answer_ref: string; evidence_ref: string }>;
  };
  field_definition: {
    schema_version: string;
    fields: Array<{ key: string; label: string }>;
  } | null;
}

export type SupportStatus =
  | "supports" | "contradicts" | "insufficient" | "unclear" | "not_assessed";

export interface SupportAssessment {
  id: string;
  prediction_id: string;
  graph_revision_ref: string;
  answer_ref: string;
  evidence_ref: string;
  actor_ref: string;
  status: SupportStatus;
  rationale: string;
  protocol_version: string;
  created_at: string;
}

export interface SupportAssessmentHistory {
  prediction_id: string;
  graph_revision_ref: string | null;
  current: boolean;
  state: "assessed" | "not_assessed";
  assessments: SupportAssessment[];
}

/**
 * "not_produced": a scorer call that cannot emit quotations (nothing to check).
 * "may_produce": a chat call that may have emitted quotations.
 */
export type QuotationProducer = "may_produce" | "not_produced";

export interface QuoteChecks {
  schema_version: "quote-checks-v1";
  state: "missing_quote_field" | "invalid_quote_list" | "empty_quote_list" | "checked" | "incomplete";
  source_snapshot_ref: string;
  input_ref: string;
  list_complete: boolean;
  overflow_count: number;
  items: Array<{
    quote_index: number;
    match_ref: string;
    result: {
      quote: unknown;
      status: "exact" | "normalized" | "not_found" | "ambiguous" | "invalid" | "source_unavailable" | "not_checked" | "incomplete";
      counts_complete: boolean;
      input_occurrence_count: number;
      distinct_source_location_count: number;
    };
  }>;
}

/** Décision : un champ par SchemaField du projet + clés standard. */
export type AnnotationPayload = Record<string, unknown>;

export interface ImportJob {
  id: string;
  task: string;
  kind?: "import" | "fulltext";
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  query: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface GoldArticle {
  id: string;
  project_id?: string | null;
  article_id: string | null;
  article_title: string | null;
  ground_truth_json: Record<string, unknown>;
  search_id?: string | null;
  /** Relecture dont la référence est la copie (null : saisie manuelle ancienne). */
  source?: {
    annotation_id: string;
    by: string | null;
    at: string | null;
    status: "VALIDATED" | "CORRECTED" | "MANUAL" | null;
    blind: boolean;
  } | null;
  added_by?: string | null;
  /** La relecture a changé depuis la copie : « mettre à jour ». */
  stale?: boolean;
}

// --- Recherches -------------------------------------------------------------

export type SearchKind = "standard" | "tuning" | "blind";

/** Une recherche : une question (ses items) posée à un lot d'articles. */
export interface SearchInfo {
  id: string;
  name: string;
  kind: SearchKind;
  is_default: boolean;
  question: string;
  fields: SchemaField[];
  text_sections: string[] | null;
  parent: { id: string; name: string } | null;
  parent_spec: Selection | null;
  created_at: string | null;
  created_by: string | null;
  lot_size: number;
  predicted: number;
  reviewed: number;
  last_job: { status: InferenceJob["status"]; model: string } | null;
}

/** Quels articles : critères cumulés sur les résultats d'une recherche (aucun = tout le corpus). */
export interface Selection {
  from_search_id?: string;
  status?: PredictionFilter;
  answers?: Record<string, string | boolean>;
  reviewed?: boolean;
  has_fulltext?: boolean;
  confidence_min?: number;
  confidence_max?: number;
  ids?: string[];
  sample_n?: number;
  seed?: number;
}

export interface PromptTemplate {
  id: string;
  project_id: string | null;
  version_number: string;
  title: string | null;
  system_prompt: string | null;
  user_prompt_schema: string | null;
  prompt_type: string | null;
  status: "DRAFT" | "VALIDATED" | "ARCHIVED";
}

export interface ModelInfo {
  id: string;
  name: string;
  backend_type: string | null;
  model_identifier: string;
  ram_usage_mb: number | null;
  is_active: boolean | null;
  /** Modèle défini par l'utilisateur courant. */
  mine?: boolean;
  /** Modèle de la plateforme (installé par un administrateur). */
  platform?: boolean;
}

/** Matrice de confusion et métriques de la pertinence ou d'un item booléen. */
export interface Prf {
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  precision: number;
  recall: number;
  f1: number;
  accuracy: number;
  specificity: number;
}

/** Métriques d'un item : booléen (confusion + prf), choix (exactitude), texte (f1 de mots). */
export interface ItemMetrics extends Partial<Prf> {
  type: SchemaField["type"];
  n: number;
}

/** Réponses du modèle comparées au gold (benchmark : details.scored ; analyse : validation). */
export interface Scored {
  n: number;
  relevance: Prf;
  items: Record<string, ItemMetrics>;
  accuracy: number | null;
}

/** Critère de classement : item (ou « relevance ») + métrique + valeur. */
export interface PrimaryCriterion {
  item: string;
  metric: RankMetric;
  value: number | null;
}

export type Validation = Scored & { primary?: PrimaryCriterion };

/** Une formulation à comparer dans un benchmark. */
export interface BenchmarkVariant {
  name: string;
  system_prompt?: string;
  user_prompt_schema?: string;
  prompt_template_id?: string;
}

export interface BenchmarkResult {
  model_name: string;
  variant?: string | null;
  n_scored?: number | null;
  execution_time_seconds: number | null;
  accuracy: number | null;
  precision_score: number | null;
  recall_score: number | null;
  f1_score: number | null;
  details?: {
    articles?: number;
    /** Accord (0..1) avec la majorité des variantes. */
    agreement?: number | null;
    scored?: Scored;
    primary?: PrimaryCriterion;
  } | null;
}

export interface BenchmarkRun {
  id: string;
  project_id: string | null;
  search_id?: string | null;
  prompt_template_id: string | null;
  models_tested: string[] | null;
  variants?: BenchmarkVariant[] | null;
  options?: { use_gold?: boolean; gold_examples?: number } | null;
  progress?: { done: number; total: number; model?: string; variant?: string } | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  created_at: string | null;
  results?: BenchmarkResult[];
}

/** Réponse d'un (modèle, variante) pour un article. */
export interface BenchmarkOutput {
  model: string;
  variant: string | null;
  decision: Record<string, unknown> | null;
  error: string | null;
}

export interface BenchmarkArticleRow {
  article_id: string;
  title: string | null;
  gold: Record<string, unknown> | null;
  disagree: boolean;
  outputs: BenchmarkOutput[];
}

export interface InferenceJob {
  id: string;
  project_id: string | null;
  prompt_template_id: string | null;
  model_used: string;
  prefilter_model?: string | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

// --- Client générique -------------------------------------------------------

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* pas de body JSON */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

/** Chaîne de requête `?a=1&b=2` sans les valeurs vides (chaîne vide si aucune). */
function qs(params: Record<string, string | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// --- Auth -------------------------------------------------------------------

export const getPublicConfig = (): Promise<{ privacy_url: string | null }> =>
  request("/auth/config");

export async function login(
  email: string,
  password: string,
  consent?: boolean
): Promise<User> {
  const body: Record<string, unknown> = { email, password };
  if (consent !== undefined) body.consent = consent;
  const data = await request<{ user: User }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data.user;
}

export async function register(
  token: string,
  password: string,
  fullName?: string
): Promise<User> {
  return request<User>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ token, password, full_name: fullName ?? null }),
  });
}

export async function me(): Promise<User> {
  return request<User>("/auth/me");
}

/** Déconnexion côté client (le cookie est httpOnly → on ne peut que demander
 *  au serveur de l'effacer ; ici on force un rechargement vers /login). */
export async function logout(): Promise<void> {
  try {
    await request("/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}

export async function updateProfile(patch: {
  full_name?: string;
  language_preference?: string;
}): Promise<User> {
  return request<User>("/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
}

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  await request("/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });
}

export async function exportMyData(): Promise<unknown> {
  return request("/users/me/export-data");
}

export async function anonymizeMe(): Promise<void> {
  await request("/users/me/anonymize", { method: "DELETE" });
}

// --- Projets ----------------------------------------------------------------

export async function listProjects(): Promise<Project[]> {
  const data = await request<{ items: Project[] }>("/projects");
  return data.items;
}

export async function createProject(payload: {
  title: string;
  description?: string;
  default_language?: string;
}): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Le projet ; question, items et parties de texte sont ceux de la recherche `searchId` (déf. initiale). */
export async function getProject(projectId: string, searchId?: string): Promise<Project> {
  return request<Project>(`/projects/${projectId}${qs({ search: searchId })}`);
}

export async function deleteProject(projectId: string): Promise<void> {
  await request<void>(`/projects/${projectId}`, { method: "DELETE" });
}

// --- Import (MODULE 2) ------------------------------------------------------

export async function queryVolume(
  projectId: string,
  query: string
): Promise<{ count: number }> {
  return request<{ count: number }>(`/projects/${projectId}/query-volume`, {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

export async function triggerImport(
  projectId: string,
  query: string
): Promise<{ job_id: string; status: string }> {
  return request<{ job_id: string; status: string }>(
    `/projects/${projectId}/import`,
    { method: "POST", body: JSON.stringify({ query }) }
  );
}

export async function completeFulltext(projectId: string): Promise<void> {
  await request(`/projects/${projectId}/fulltext`, { method: "POST" });
}

export async function listJobs(projectId: string): Promise<ImportJob[]> {
  const data = await request<{ jobs: ImportJob[] }>(`/projects/${projectId}/jobs`);
  return data.jobs;
}

export async function updateProject(
  projectId: string,
  patch: {
    text_sections?: string[];
    question?: string;
    fields?: SchemaField[];
    read_windows?: number;
  },
  searchId?: string
): Promise<Project> {
  return request<Project>(`/projects/${projectId}${qs({ search: searchId })}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function generatePrompt(projectId: string, searchId?: string): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/generate${qs({ search: searchId })}`, {
    method: "POST",
  });
}

// --- Articles (MODULE 2/5) --------------------------------------------------

export type PredictionFilter = "AUTO_ACCEPTED" | "AUTO_REJECTED" | "NEEDS_HUMAN_REVIEW" | "NONE" | "TO_REVIEW";

export async function listArticles(
  projectId: string,
  page = 1,
  perPage = 50,
  status?: PredictionFilter,
  sort: "added" | "date_desc" | "date_asc" | "score_desc" = "added",
  answers: Record<string, string | boolean> = {},
  searchId?: string
): Promise<{
  items: Article[];
  total: number;
  page: number;
  per_page: number;
  stats: Record<string, number>;
}> {
  const active = Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== "" && v != null));
  const q =
    (status ? `&status=${status}` : "") +
    (searchId ? `&search=${searchId}` : "") +
    `&sort=${sort}` +
    (Object.keys(active).length ? `&answers=${encodeURIComponent(JSON.stringify(active))}` : "");
  return request(`/projects/${projectId}/articles?page=${page}&per_page=${perPage}${q}`);
}

export async function getArticle(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<Article> {
  return request<Article>(`/projects/${projectId}/articles/${articleId}${qs({ search: searchId })}`);
}

export async function getPrediction(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<ArticlePrediction | null> {
  return request<ArticlePrediction | null>(
    `/projects/${projectId}/articles/${articleId}/prediction${qs({ search: searchId })}`
  );
}

export async function getSupportAssessments(
  projectId: string, articleId: string, predictionId: string, searchId?: string
): Promise<SupportAssessmentHistory> {
  return request<SupportAssessmentHistory>(
    `/projects/${projectId}/articles/${articleId}/predictions/${predictionId}/support-assessments${qs({ search: searchId })}`
  );
}

export async function createSupportAssessment(
  projectId: string, articleId: string, predictionId: string,
  input: Pick<SupportAssessment, "graph_revision_ref" | "answer_ref" | "evidence_ref" | "status" | "rationale">,
  searchId?: string
): Promise<SupportAssessment> {
  return request<SupportAssessment>(
    `/projects/${projectId}/articles/${articleId}/predictions/${predictionId}/support-assessments${qs({ search: searchId })}`,
    { method: "POST", body: JSON.stringify(input) }
  );
}

export async function getReview(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<ArticleReview | null> {
  return request<ArticleReview | null>(
    `/projects/${projectId}/articles/${articleId}/annotation${qs({ search: searchId })}`
  );
}

export async function getArticleWithPrediction(
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<{ article: Article; prediction: ArticlePrediction | null; review: ArticleReview | null }> {
  const article = await getArticle(projectId, articleId, searchId);
  const prediction = await getPrediction(projectId, articleId, searchId).catch(() => null);
  const review = await getReview(projectId, articleId, searchId).catch(() => null);
  return { article, prediction, review };
}

/** Dépôt manuel du PDF d'un article : le texte est extrait et remplace le texte intégral. */
export async function uploadArticlePdf(
  projectId: string,
  articleId: string,
  file: File,
  force = false,
  searchId?: string
): Promise<Article & { pdf_check?: "match" | "unverified" | "forced" }> {
  return request(`/projects/${projectId}/articles/${articleId}/fulltext${qs({ force, search: searchId })}`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
}

/** Dépôt en lot : rattache ce PDF à l'article du projet dont il porte le titre / le DOI. */
export async function matchArticlePdf(
  projectId: string,
  file: File
): Promise<{ article_id: string; title: string | null; score: number }> {
  return request(`/projects/${projectId}/fulltext/match`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
}

/** Relit la notice PubMed de l'article puis relance la recherche de son texte intégral. */
export const searchArticleFulltext = (
  projectId: string,
  articleId: string,
  searchId?: string
): Promise<{ found: boolean; source: string | null; article: Article }> =>
  request(`/projects/${projectId}/articles/${articleId}/fulltext/search${qs({ search: searchId })}`, {
    method: "POST",
  });

export async function submitAnnotation(
  projectId: string,
  articleId: string,
  annotation: AnnotationPayload,
  searchId?: string,
  blind = false,
  sourceViewToken?: string
): Promise<Review> {
  return request<Review>(`/projects/${projectId}/articles/${articleId}/annotation${qs({ search: searchId, blind })}`, {
    method: "POST",
    body: JSON.stringify({ ...annotation, ...(sourceViewToken ? { source_view_token: sourceViewToken } : {}) }),
  });
}

// --- Gold (MODULE 3) --------------------------------------------------------

export async function listGold(projectId: string, searchId?: string): Promise<GoldArticle[]> {
  const data = await request<{ items: GoldArticle[] }>(`/projects/${projectId}/gold${qs({ search: searchId })}`);
  return data.items;
}

/** Ajoute au gold la dernière relecture des articles donnés (défaut : tous les articles relus). */
export const goldFromReviews = (
  projectId: string,
  opts: { articleIds?: string[]; refresh?: boolean } = {},
  searchId?: string
): Promise<{ added: number; updated: number; skipped: number }> =>
  request(`/projects/${projectId}/gold/from-reviews${qs({ search: searchId })}`, {
    method: "POST",
    body: JSON.stringify({
      ...(opts.articleIds ? { article_ids: opts.articleIds } : {}),
      ...(opts.refresh ? { refresh: true } : {}),
    }),
  });

export async function deleteGold(projectId: string, goldId: string): Promise<void> {
  await request(`/projects/${projectId}/gold/${goldId}`, { method: "DELETE" });
}

// --- Prompts (MODULE 3) -----------------------------------------------------

export async function listPrompts(projectId: string): Promise<PromptTemplate[]> {
  const data = await request<{ items: PromptTemplate[] }>(
    `/projects/${projectId}/prompts`
  );
  return data.items;
}

export async function getPrompt(
  projectId: string,
  promptId: string
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/${promptId}`);
}

export async function createPrompt(
  projectId: string,
  payload: {
    version_number: string;
    title?: string;
    system_prompt?: string;
    user_prompt_schema?: string;
    prompt_type?: string;
  }
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updatePrompt(
  projectId: string,
  promptId: string,
  payload: Partial<{
    version_number: string;
    title: string;
    system_prompt: string;
    user_prompt_schema: string;
    status: string;
  }>
): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/projects/${projectId}/prompts/${promptId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// --- Modèles (MODULE 3) -----------------------------------------------------

/** Modèles que l'utilisateur peut utiliser (dans ce projet si `projectId`). */
export async function listModels(projectId?: string): Promise<ModelInfo[]> {
  const q = projectId ? `?project_id=${projectId}` : "";
  const data = await request<{ items: ModelInfo[] }>(`/models${q}`);
  return data.items;
}

export interface ModelShare {
  id: string;
  type: "user" | "project";
  label: string;
  email: string | null;
  project_id: string | null;
}

export const listModelShares = async (modelId: string): Promise<ModelShare[]> =>
  (await request<{ items: ModelShare[] }>(`/models/${modelId}/shares`)).items;

export const addModelShare = (modelId: string, target: { email?: string; project_id?: string }): Promise<void> =>
  request(`/models/${modelId}/shares`, { method: "POST", body: JSON.stringify(target) });

export const removeModelShare = (modelId: string, shareId: string): Promise<void> =>
  request(`/models/${modelId}/shares/${shareId}`, { method: "DELETE" });

export async function createModel(payload: {
  name: string;
  model_identifier: string;
  backend_type?: string;
  ram_usage_mb?: number;
}): Promise<ModelInfo> {
  return request<ModelInfo>("/models", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateModel(
  modelId: string,
  payload: Partial<{
    name: string;
    model_identifier: string;
    ram_usage_mb: number;
    is_active: boolean;
  }>
): Promise<ModelInfo> {
  return request<ModelInfo>(`/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteModel(modelId: string): Promise<void> {
  await request(`/models/${modelId}`, { method: "DELETE" });
}

export interface OllamaModel {
  name: string;
  size: number | null;
  registered: boolean;
}
export interface PullState {
  state: "running" | "done" | "error";
  status: string;
  completed: number;
  total: number;
  error?: string;
}

export interface HfModel {
  id: string;
  downloads: number;
  likes: number;
  decision?: boolean; // modèle de décision (Kev…) : ne s'installe pas via Ollama
}
export interface HfFile {
  file: string;
  quant?: string | null;
  size?: number | null;
  recommended?: boolean;
}

export async function searchHf(q: string, kind: "chat" | "encoder"): Promise<HfModel[]> {
  return (
    await request<{ items: HfModel[] }>(
      `/models/search?q=${encodeURIComponent(q)}&kind=${kind}`
    )
  ).items;
}

export async function hfFiles(
  repo: string,
  kind: "chat" | "encoder"
): Promise<{ items: HfFile[]; tokenizer?: boolean }> {
  return request(`/models/hf/files?repo=${encodeURIComponent(repo)}&kind=${kind}`);
}

export async function installHf(payload: {
  repo: string;
  kind: "chat" | "encoder";
  quant?: string;
  file?: string;
}): Promise<void> {
  await request("/models/hf/install", { method: "POST", body: JSON.stringify(payload) });
}

export async function listOllama(): Promise<{
  items: OllamaModel[];
  pulls: Record<string, PullState>;
}> {
  return request("/models/ollama");
}

export async function pullOllama(name: string): Promise<void> {
  await request("/models/ollama/pull", { method: "POST", body: JSON.stringify({ name }) });
}

export interface ExternalModelInput {
  name: string;
  base_url: string;
  model: string;
  api_key?: string;
}

export async function testExternalModel(p: ExternalModelInput): Promise<{ ok: boolean; error?: string }> {
  return request("/models/external/test", { method: "POST", body: JSON.stringify(p) });
}

export async function addExternalModel(p: ExternalModelInput): Promise<void> {
  await request("/models/external", { method: "POST", body: JSON.stringify(p) });
}

export async function registerOllama(name: string): Promise<void> {
  await request("/models/ollama/register", { method: "POST", body: JSON.stringify({ name }) });
}

// --- Benchmarks (MODULE 3) --------------------------------------------------

export interface BenchmarkCreate {
  models: string[];
  search_id?: string;
  variants?: BenchmarkVariant[];
  prompt_template_id?: string;
  use_gold?: boolean;
  gold_examples?: number;
}

export async function triggerBenchmark(
  projectId: string,
  payload: BenchmarkCreate
): Promise<{ run_id: string; status: string; calls?: number }> {
  return request<{ run_id: string; status: string; calls?: number }>(
    `/projects/${projectId}/benchmarks`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export async function listBenchmarks(projectId: string): Promise<BenchmarkRun[]> {
  const data = await request<{ items: BenchmarkRun[] }>(
    `/projects/${projectId}/benchmarks`
  );
  return data.items;
}

export async function getBenchmark(
  projectId: string,
  runId: string
): Promise<BenchmarkRun> {
  return request<BenchmarkRun>(`/projects/${projectId}/benchmarks/${runId}`);
}

/** Grille article × (modèle, variante) d'un benchmark, désaccords d'abord. */
export const listBenchmarkArticles = (
  projectId: string,
  runId: string,
  opts: { disagree?: boolean; page?: number; perPage?: number } = {}
): Promise<{ total: number; page: number; per_page: number; items: BenchmarkArticleRow[] }> =>
  request(
    `/projects/${projectId}/benchmarks/${runId}/articles${qs({
      disagree: opts.disagree,
      page: String(opts.page ?? 1),
      per_page: String(opts.perPage ?? 25),
    })}`
  );

// --- Inférence (MODULE 4) ---------------------------------------------------

export async function triggerInference(
  projectId: string,
  payload: {
    model: string;
    prefilter_model?: string;
    prompt_template_id?: string;
    resume?: boolean;
    search_id?: string;
    gold_examples?: number;
    validate_with_gold?: boolean;
  }
): Promise<{ job_id: string; status: string }> {
  return request<{ job_id: string; status: string }>(
    `/projects/${projectId}/inference`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export const cancelInference = (projectId: string): Promise<unknown> =>
  request(`/projects/${projectId}/inference/cancel`, { method: "POST" });

export const cancelBenchmark = (projectId: string, runId: string): Promise<unknown> =>
  request(`/projects/${projectId}/benchmarks/${runId}/cancel`, { method: "POST" });

export async function getInference(projectId: string, searchId?: string): Promise<InferenceJob | null> {
  const r = await request<{ job: InferenceJob | null }>(`/projects/${projectId}/inference${qs({ search: searchId })}`);
  return r.job;
}

// --- Settings (MODULE 7, admin) --------------------------------------------

export type SettingsScope = "GLOBAL" | "PROJECT" | "USER";

export async function getSettings(params?: {
  scope?: SettingsScope;
  project_id?: string;
  user_id?: string;
}): Promise<Record<string, Record<string, string | null>>> {
  const qs = new URLSearchParams();
  if (params?.scope) qs.set("scope", params.scope);
  if (params?.project_id) qs.set("project_id", params.project_id);
  if (params?.user_id) qs.set("user_id", params.user_id);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return request<Record<string, Record<string, string | null>>>(
    `/admin/settings${suffix}`
  );
}

export async function updateSettings(payload: {
  scope: SettingsScope;
  project_id?: string;
  user_id?: string;
  values: Record<string, string>;
}): Promise<Record<string, Record<string, string | null>>> {
  return request<Record<string, Record<string, string | null>>>("/admin/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function testSmtp(to?: string): Promise<{ sent: boolean; error?: string }> {
  return request<{ sent: boolean; error?: string }>("/admin/settings/smtp/test", {
    method: "POST",
    body: JSON.stringify({ to: to ?? null }),
  });
}

// --- Utilisateur (RGPD) -----------------------------------------------------

export async function anonymize(): Promise<void> {
  await request("/users/me/anonymize", { method: "DELETE" });
}

export async function exportData(): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>("/users/me/export-data");
}


/** Famille d'un modèle du registre : scoreur (décision / encodeur) ou génératif. */
export interface Capability {
  configured: boolean;
  reachable: boolean;
}
export type ModelCapabilities = Record<"ollama" | "laya" | "kev", Capability>;
export const getModelCapabilities = (): Promise<ModelCapabilities> => request("/models/capabilities");

export function isScorer(m: Pick<ModelInfo, "backend_type">): boolean {
  return m.backend_type === "SYSTEMONE" || m.backend_type === "ONNX_LOCAL";
}

// --- Équipe + export ---------------------------------------------------------

export interface Member {
  user_id: string;
  email: string;
  full_name: string | null;
  role: "LEAD" | "ANNOTATOR";
}

export const listMembers = async (projectId: string): Promise<Member[]> =>
  (await request<{ items: Member[] }>(`/projects/${projectId}/members`)).items;

export const inviteMember = (
  projectId: string,
  email: string,
  role: Member["role"],
  language?: string
): Promise<{ status: "added" | "invited"; email_sent: boolean; link: string | null }> =>
  request(`/projects/${projectId}/members/invite`, {
    method: "POST",
    body: JSON.stringify({ email, role, language }),
  });

export const removeMember = (projectId: string, userId: string): Promise<void> =>
  request(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });

/** Lien de téléchargement du classeur Excel (le cookie d'authentification suffit). */
export const exportUrl = (projectId: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/export.xlsx${qs({ search: searchId })}`;

/** Références RIS (Zotero, EndNote, Mendeley…) ou BibTeX, avec les mêmes filtres que la liste. */
export function referencesUrl(
  projectId: string,
  fmt: "ris" | "bib",
  status?: string,
  answers: Record<string, string> = {},
  searchId?: string
): string {
  const active = Object.fromEntries(Object.entries(answers).filter(([, v]) => v !== ""));
  const q = new URLSearchParams();
  if (searchId) q.set("search", searchId);
  if (status) q.set("status", status);
  if (Object.keys(active).length) q.set("answers", JSON.stringify(active));
  const qs = q.toString();
  return `${API_BASE}/projects/${projectId}/export.${fmt}${qs ? `?${qs}` : ""}`;
}

export interface CrateExport {
  id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  result: { done?: number; total?: number; size?: number; articles?: number; pdf?: number } | null;
  error: string | null;
  created_at: string | null;
  download: boolean;
}

export const prismaPdfUrl = (projectId: string, lang: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/prisma.pdf${qs({ lang, search: searchId })}`;

/** Diagramme PRISMA 2020 (SVG) ; ``lang`` = langue des libellés. */
export const prismaUrl = (projectId: string, lang: string, searchId?: string): string =>
  `${API_BASE}/projects/${projectId}/prisma.svg${qs({ lang, search: searchId })}`;

/** RO-Crate (.zip) : tâche de fond (e-mail à la fin) puis téléchargement. */
export const startCrateExport = (projectId: string, searchId?: string): Promise<CrateExport> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`, { method: "POST" });
export const getCrateExport = (projectId: string, searchId?: string): Promise<CrateExport | null> =>
  request(`/projects/${projectId}/export/crate${qs({ search: searchId })}`);
export const crateDownloadUrl = (projectId: string, jobId: string): string =>
  `${API_BASE}/projects/${projectId}/export/crate/${jobId}/download`;

// --- Administration des comptes ---------------------------------------------

export interface AdminUser {
  id: string;
  email: string;
  full_name: string | null;
  role: "ADMIN" | "RESEARCHER";
  language_preference: string | null;
  is_active: boolean;
  can_create_projects: boolean;
  anonymized: boolean;
  created_at: string | null;
  projects: number;
  created_projects: number;
  models: number;
  annotations: number;
}

export interface AdminInvitation {
  id: string;
  email: string;
  language: string;
  project: string | null;
  role: string | null;
  invited_by: string | null;
  created_at: string | null;
}

export interface AdminProject {
  id: string;
  title: string;
  created_by: string | null;
  created_at: string | null;
  members: number;
  articles: number;
}

export const listAdminUsers = async (): Promise<AdminUser[]> =>
  (await request<{ items: AdminUser[] }>("/admin/users")).items;

export const patchAdminUser = (
  id: string,
  patch: Partial<Pick<AdminUser, "full_name" | "role" | "language_preference" | "is_active" | "can_create_projects">>
): Promise<void> => request(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteAdminUser = (
  id: string,
  deleteTheirProjects: boolean
): Promise<{ deleted_projects: number; orphaned_projects: string[] }> =>
  request(`/admin/users/${id}?delete_their_projects=${deleteTheirProjects}`, { method: "DELETE" });

export const inviteCreator = (
  email: string,
  language: string
): Promise<{ email_sent: boolean; link: string | null }> =>
  request("/auth/invite", {
    method: "POST",
    body: JSON.stringify({ email, language, can_create_projects: true }),
  });

export const purgeOrphanUsers = (): Promise<{ deleted: number }> =>
  request("/admin/users/purge-orphans", { method: "POST" });

export const listAdminInvitations = async (): Promise<AdminInvitation[]> =>
  (await request<{ items: AdminInvitation[] }>("/admin/invitations")).items;

export const resendInvitation = (id: string): Promise<{ email_sent: boolean; link: string | null }> =>
  request(`/admin/invitations/${id}/resend`, { method: "POST" });

export const revokeInvitation = (id: string): Promise<void> =>
  request(`/admin/invitations/${id}`, { method: "DELETE" });

export const listAdminProjects = async (): Promise<AdminProject[]> =>
  (await request<{ items: AdminProject[] }>("/admin/projects")).items;

// --- Recherches (une question posée à un lot d'articles) ---------------------

export const listSearches = async (projectId: string): Promise<SearchInfo[]> =>
  (await request<{ items: SearchInfo[] }>(`/projects/${projectId}/searches`)).items;

/** Nombre d'articles qui répondent à la sélection (avant de créer la recherche). */
export const previewSelection = async (projectId: string, selection: Selection): Promise<number> =>
  (
    await request<{ count: number }>(`/projects/${projectId}/searches/preview`, {
      method: "POST",
      body: JSON.stringify(selection),
    })
  ).count;

export interface SearchCreate {
  name: string;
  question?: string;
  fields?: SchemaField[];
  text_sections?: string[];
  read_windows?: number;
  kind?: SearchKind;
  selection: Selection;
}

export const createSearch = (projectId: string, payload: SearchCreate): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches`, { method: "POST", body: JSON.stringify(payload) });

export const renameSearch = (projectId: string, searchId: string, name: string): Promise<SearchInfo> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "PATCH", body: JSON.stringify({ name }) });

export const deleteSearch = (projectId: string, searchId: string): Promise<void> =>
  request(`/projects/${projectId}/searches/${searchId}`, { method: "DELETE" });

// --- NTS publication traceability (issue #11) ---------------------------------

export const NTS_MODEL_NAME = "nts-deterministic-extractor-v2";

export interface NtsDataset {
  id: string;
  kind: "nts" | "ra" | "statistics" | "other";
  file_name: string;
  source_url: string;
  retrieved_at: string | null;
  http_status: number | null;
  bytes: number;
  sha256: string;
  row_count: number;
}

export interface NtsJob {
  id: string;
  kind: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface NtsStatus {
  is_nts_project: boolean;
  model_name: string;
  extracted_articles: number;
  /** Latest run of the extractor (an ordinary inference job), null before the first one. */
  extraction_job: Omit<NtsJob, "kind"> | null;
  /** Findings proposed by the extractor (reviewed or not). */
  proposed_findings: number;
  datasets: NtsDataset[];
  has_links: boolean;
  review_pending: number;
  jobs: NtsJob[];
}

export interface NtsAdjudication {
  decision: "confirmed" | "rejected" | "unresolved";
  rationale: string;
  adjudicator_ref: string;
  created_at: string | null;
  count: number;
}

export interface NtsDatasetCell {
  file: string;
  row: number;
  columns: string[];
}

export interface NtsPlannedCell {
  species: string;
  authorized_number: number | "not reported";
  raw_value: string | null;
  count_category: string;
  meaning: string;
  dataset_cell?: NtsDatasetCell;
}

export interface NtsLink {
  id: string;
  article_id: string;
  prediction_id: string;
  finding_id: string | null;
  quote_index: number;
  identifier_system: string;
  identifier_verbatim: string;
  identifier_normalized: string | null;
  country: string | null;
  version_cited: string | null;
  date_cited: string | null;
  alures_state: string;
  alures_reasons: string[];
  alures_versions: string[] | null;
  alures_current_version: string | null;
  alures_row_numbers: number[][] | null;
  alures_candidates: string[] | null;
  dataset_id: string | null;
  link_state: string;
  planned: NtsPlannedCell[] | null;
  scope_note: string | null;
  computed_at: string | null;
  adjudication: NtsAdjudication | null;
}

export interface NtsObservation {
  id: string;
  article_id: string;
  prediction_id: string;
  finding_id: string | null;
  observation_id: string;
  source_kind: "article" | "alures" | "rar";
  count_category: string;
  species: string;
  strain: string;
  sex: string;
  number: number | null;
  unit_counted: string;
  cohort_label: string;
  count_scope: string;
  group_count: number | null;
  derived: boolean;
  derivation: string | null;
  method_section: string | null;
  rule_id: string | null;
  possible_overlap: string[];
  notes: string | null;
  dataset_cell: NtsDatasetCell | null;
}

export interface NtsSummaryRow {
  article_id: string;
  doi: string | null;
  pmid: string | null;
  title: string | null;
  nts_identifiers: {
    normalized: string | null;
    version_cited: string | null;
    link_state: string;
    alures_state: string;
    adjudication: NtsAdjudication | null;
  }[];
  national_authorizations: string[];
  planned: { species: string; number: number | null; cell: NtsDatasetCell | null }[];
  reported: { number: number | null; species: string; scope: string; derived: boolean; cohort: string }[];
  comparability: string;
  flags: string[];
  match_status: string;
}

export interface NtsReviewItem {
  finding_id: string;
  article_id: string;
  prediction_id: string;
  revision: number;
  kind: "nts-identifier" | "national-authorization" | "animal-counts" | null;
  statement: string | null;
  /** Full content of the revision awaiting review (statement, context, typed numbers). */
  content: FindingContent;
  /** Who wrote this revision, as seen by the viewer (no identity is ever exposed). */
  proposed_by: "automation" | "you" | "member";
  /** False when the viewer wrote this revision: the API refuses a self-review. */
  can_review: boolean;
  quote: string | null;
  quote_match: string | null;
  answer_ref: string;
  evidence_ref: string;
  proposed_at: string | null;
}

const ntsBase = (projectId: string) => `/projects/${projectId}/nts`;

export const getNtsStatus = (projectId: string): Promise<NtsStatus> => request(ntsBase(projectId));
export const ntsSetup = (projectId: string): Promise<{ model_name: string }> =>
  request(`${ntsBase(projectId)}/setup`, { method: "POST" });
export const ntsPropose = (
  projectId: string
): Promise<{ predictions: number; created: number; skipped: number }> =>
  request(`${ntsBase(projectId)}/propose`, { method: "POST" });
export const ntsAluresSync = (projectId: string, offline = false): Promise<{ job_id: string; status: string }> =>
  request(`${ntsBase(projectId)}/alures/sync`, { method: "POST", body: JSON.stringify({ offline }) });
export const ntsJoin = (projectId: string): Promise<{ job_id: string; status: string }> =>
  request(`${ntsBase(projectId)}/join`, { method: "POST" });
export const listNtsLinks = async (projectId: string): Promise<NtsLink[]> =>
  (await request<{ items: NtsLink[] }>(`${ntsBase(projectId)}/links`)).items;
export const listNtsObservations = async (projectId: string): Promise<NtsObservation[]> =>
  (await request<{ items: NtsObservation[] }>(`${ntsBase(projectId)}/observations`)).items;
export const getNtsSummary = async (projectId: string): Promise<NtsSummaryRow[]> =>
  (await request<{ items: NtsSummaryRow[] }>(`${ntsBase(projectId)}/summary`)).items;
export const getNtsReviewQueue = async (projectId: string): Promise<NtsReviewItem[]> =>
  (await request<{ items: NtsReviewItem[] }>(`${ntsBase(projectId)}/review-queue`)).items;
export const ntsAdjudicate = (
  projectId: string,
  linkId: string,
  input: { decision: NtsAdjudication["decision"]; rationale: string }
): Promise<NtsLink> =>
  request(`${ntsBase(projectId)}/links/${linkId}/adjudications`, {
    method: "POST",
    body: JSON.stringify(input),
  });

// --- Finding curation (reviews on proposed findings) --------------------------

export interface FindingReviewInput {
  decision: "approve" | "reject";
  rationale: string;
  judgments: { evidence_ref: string; status: SupportStatus; rationale: string }[];
}

/** A finding revision's content (contract source-evidence-packet/0.1.0, finding_schema.py). */
export interface FindingContent {
  statement: string;
  origin: "source-explicit" | "ai-paraphrase" | "reviewer-interpretation";
  context: {
    population: string;
    exposure: string;
    comparator: string;
    outcome: string;
    time: string;
    experimental_unit: string;
  };
  numeric_results: {
    name: string;
    value: number;
    unit: string;
    n: { value: number; definition: "article-level" | "experimental-unit" | "group" | "unknown" };
  }[];
}

/** Proposes a new revision of a finding (same evidence links); earlier ones stay in history. */
export const reviseFinding = (
  projectId: string,
  articleId: string,
  findingId: string,
  input: {
    content: FindingContent;
    evidence: { prediction_id: string; answer_ref: string; evidence_ref: string }[];
  }
): Promise<unknown> =>
  request(`/projects/${projectId}/articles/${articleId}/findings/${findingId}/revisions`, {
    method: "POST",
    body: JSON.stringify(input),
  });

export interface FindingRevisionView {
  revision: number;
  is_latest: boolean;
  state: "proposed" | "approved" | "rejected" | "superseded";
  content: { statement: string } & Record<string, unknown>;
  proposer_ref: string;
  created_at: string | null;
  evidence: {
    prediction_id: string;
    answer_ref: string;
    evidence_ref: string;
    quote: string | null;
    match: { status: string } | null;
  }[];
  reviews: { id: string; reviewer_ref: string; decision: "approve" | "reject"; created_at: string | null }[];
}

export interface FindingView {
  finding_id: string;
  article_id: string;
  revisions: FindingRevisionView[];
}

export const listFindings = async (projectId: string, articleId: string): Promise<FindingView[]> =>
  (await request<{ findings: FindingView[] }>(`/projects/${projectId}/articles/${articleId}/findings`)).findings;

export const reviewFinding = (
  projectId: string,
  articleId: string,
  findingId: string,
  revision: number,
  input: FindingReviewInput
): Promise<unknown> =>
  request(`/projects/${projectId}/articles/${articleId}/findings/${findingId}/revisions/${revision}/reviews`, {
    method: "POST",
    body: JSON.stringify(input),
  });

/** URL of the rights-aware packet export (zip); the browser downloads it with the session cookie. */
export const findingsExportUrl = (projectId: string, accessLevel: "restricted" | "public" = "restricted"): string =>
  `${API_BASE}/projects/${projectId}/findings/export${qs({ access_level: accessLevel })}`;
